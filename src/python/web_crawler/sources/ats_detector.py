from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from socket import timeout as SocketTimeout
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from urllib3.exceptions import NameResolutionError

from src.python.web_crawler.config import CrawlerConfig

logger = logging.getLogger(__name__)

ATS_PROVIDERS = ("greenhouse", "lever", "ashby")
_PROVIDER_PRECEDENCE = {"greenhouse": 0, "lever": 1, "ashby": 2}
_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
_HOST_UNAVAILABLE_STATUS_CODES = {500, 502, 503, 504}


@dataclass(slots=True)
class ATSDetectionResult:
    provider: str
    board_url: str | None = None
    checked_url: str | None = None


@dataclass(slots=True)
class ATSRequestFailure(Exception):
    failure_type: str
    url: str
    message: str

    def __str__(self) -> str:
        return self.message


def _delay_seconds(config: CrawlerConfig, attempt: int) -> float:
    base_seconds = max(config.base_delay_ms, 0) / 1000.0
    max_seconds = max(config.max_delay_ms, 0) / 1000.0
    return min(base_seconds * attempt, max_seconds if max_seconds > 0 else base_seconds * attempt)


def _provider_from_url(url: str | None) -> str | None:
    if not url:
        return None

    parsed = urlparse(url)
    host = parsed.netloc.casefold()
    if host.startswith("www."):
        host = host[4:]

    if "greenhouse" in host:
        return "greenhouse"
    if host == "jobs.lever.co" or host.endswith(".lever.co"):
        return "lever"
    if host == "jobs.ashbyhq.com" or host.endswith(".ashbyhq.com"):
        return "ashby"
    return None


def _is_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, (requests.Timeout, TimeoutError, SocketTimeout)):
        return True

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (requests.Timeout, TimeoutError, SocketTimeout)):
            return True
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)

    return "timed out" in str(exc).casefold()


def _is_dns_resolution_error(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, NameResolutionError):
            return True
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)

    message = str(exc).casefold()
    return "failed to resolve" in message or "name or service not known" in message


def _is_host_unreachable_error(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (requests.ConnectionError, requests.exceptions.SSLError)):
            return True
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)

    message = str(exc).casefold()
    return "tlsv1 alert" in message or "ssl" in message


def _is_root_url(url: str) -> bool:
    path = urlparse(url).path or "/"
    return path == "/"


def extract_ats_signatures_from_html(html: str) -> set[str]:
    lowered = html.casefold()
    providers: set[str] = set()

    greenhouse_markers = ("boards.greenhouse.io", "grnh.io", "job-boards.greenhouse.io")
    lever_markers = ("jobs.lever.co", ".lever-job", "lever-job")
    ashby_markers = ("jobs.ashbyhq.com", "window.ashby", "ashbyhq.com")

    if any(marker in lowered for marker in greenhouse_markers):
        providers.add("greenhouse")
    if any(marker in lowered for marker in lever_markers):
        providers.add("lever")
    if any(marker in lowered for marker in ashby_markers):
        providers.add("ashby")

    return providers


def fetch_url(session: requests.Session, url: str, config: CrawlerConfig) -> requests.Response | None:
    last_error: Exception | None = None
    last_status_code: int | None = None
    for attempt in range(1, max(config.max_retries, 1) + 1):
        try:
            response = session.get(url, timeout=config.http_timeout_seconds, allow_redirects=True)
        except requests.RequestException as exc:
            last_error = exc
            logger.debug("request attempt %d failed for %s: %s", attempt, url, exc)

            # DNS and timeout failures are terminal for enrichment_ats_enrichment; do not retry.
            if _is_dns_resolution_error(exc):
                raise ATSRequestFailure("dns_resolution", url, str(exc)) from exc
            if _is_timeout_error(exc):
                raise ATSRequestFailure("timeout", url, str(exc)) from exc

            if attempt < max(config.max_retries, 1):
                time.sleep(_delay_seconds(config, attempt))
            continue

        if response.status_code in _TRANSIENT_STATUS_CODES and attempt < max(config.max_retries, 1):
            last_status_code = response.status_code
            logger.debug("transient status %d for %s on attempt %d", response.status_code, url, attempt)
            time.sleep(_delay_seconds(config, attempt))
            continue

        if response.status_code >= 400:
            last_status_code = response.status_code
            logger.debug("request returned non-success status %d for %s", response.status_code, url)
            if response.status_code in _HOST_UNAVAILABLE_STATUS_CODES:
                raise ATSRequestFailure("host_unavailable", url, f"status {response.status_code}")
            return None
        return response

    if last_error:
        logger.debug("request exhausted retries for %s: %s", url, last_error)
        if _is_host_unreachable_error(last_error):
            raise ATSRequestFailure("host_unreachable", url, str(last_error)) from last_error
    return None


def detect_ats_provider(candidate_urls: list[str], config: CrawlerConfig, session: requests.Session | None = None) -> ATSDetectionResult | None:
    if not candidate_urls:
        return None

    owned_session = session is None
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": config.user_agent})

    detected_links: list[tuple[str, str]] = []
    detected_signatures: set[str] = set()
    skipped_hosts: set[str] = set()

    try:
        for candidate_url in candidate_urls:
            host = urlparse(candidate_url).netloc.casefold()
            if host in skipped_hosts:
                logger.debug("skipping ATS detection for %s after host-level failure on %s", candidate_url, host)
                continue

            direct_provider = _provider_from_url(candidate_url)
            if direct_provider:
                return ATSDetectionResult(provider=direct_provider, board_url=candidate_url, checked_url=candidate_url)

            try:
                response = fetch_url(session, candidate_url, config)
            except ATSRequestFailure as exc:
                if exc.failure_type in {"dns_resolution", "timeout", "host_unreachable"}:
                    skipped_hosts.add(host)
                    logger.debug("skipping remaining ATS candidates for host %s after %s failure", host, exc.failure_type)
                    continue
                if exc.failure_type == "host_unavailable" and _is_root_url(candidate_url):
                    skipped_hosts.add(host)
                    logger.debug("skipping remaining ATS candidates for host %s after root returned unavailable status", host)
                    continue
                raise

            if response is None:
                continue

            response_provider = _provider_from_url(response.url)
            if response_provider:
                return ATSDetectionResult(provider=response_provider, board_url=response.url, checked_url=candidate_url)

            soup = BeautifulSoup(response.text, "html.parser")
            for anchor in soup.find_all("a", href=True):
                href = urljoin(response.url, anchor["href"])
                provider = _provider_from_url(href)
                if provider:
                    detected_links.append((provider, href))

            detected_signatures.update(extract_ats_signatures_from_html(response.text))

        if detected_links:
            detected_links.sort(key=lambda item: _PROVIDER_PRECEDENCE.get(item[0], len(_PROVIDER_PRECEDENCE)))
            provider, board_url = detected_links[0]
            if len({item[0] for item in detected_links}) > 1:
                logger.debug("ambiguous ATS links detected for %s: %s", candidate_urls[0], sorted({item[0] for item in detected_links}))
            return ATSDetectionResult(provider=provider, board_url=board_url, checked_url=candidate_urls[0])

        if detected_signatures:
            provider = sorted(detected_signatures, key=lambda item: _PROVIDER_PRECEDENCE.get(item, len(_PROVIDER_PRECEDENCE)))[0]
            if len(detected_signatures) > 1:
                logger.debug("ambiguous ATS signatures detected for %s: %s", candidate_urls[0], sorted(detected_signatures))
            return ATSDetectionResult(provider=provider, checked_url=candidate_urls[0])

        return None
    finally:
        if owned_session:
            session.close()