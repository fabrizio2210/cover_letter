from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.models import DiscoveredCompany
from src.python.web_crawler.sources.base import SourceAdapter

logger = logging.getLogger(__name__)


_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
_COMPANY_SALARIES_PATH_RE = re.compile(
    r"^/(?:[a-z]{2}-[a-z]{2}/)?companies/([^/?#]+)/salaries/?(?:[?#].*)?$",
    re.IGNORECASE,
)
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_CURRENCY_SUFFIX_RE = re.compile(r"\s+[£$€]\s?\d[\d.,KkMm]*.*$")
_JOB_DETAIL_PATH_RE = re.compile(r"^/jobs\?(?:.*&)?jobId=\d+(?:[&#].*)?$", re.IGNORECASE)
_NOISE_LABELS = {
    "see all companies",
    "see our leaderboard",
    "levels fyi logo",
    "levels.fyi jobs",
    "salaries",
    "jobs",
    "services",
    "community",
    "download app",
    "home page",
    "job board",
    "compensation",
}


@dataclass(slots=True)
class LevelsFyiJobCard:
    """A single discovered job from the Levels.fyi search or taxonomy pages."""

    job_title: str
    company_name: str
    source_url: str
    external_job_id: str
    domain: str = ""
    description: str = ""
    location: str = ""
    compensation: str = ""
    role: str = ""


class LevelsFyiAdapter(SourceAdapter):
    source_name = "levelsfyi"
    base_url = "https://www.levels.fyi"

    @staticmethod
    def _delay_seconds(config: CrawlerConfig, attempt: int) -> float:
        base_seconds = max(config.base_delay_ms, 0) / 1000.0
        max_seconds = max(config.max_delay_ms, 0) / 1000.0
        return min(base_seconds * attempt, max_seconds if max_seconds > 0 else base_seconds * attempt)

    @staticmethod
    def _role_slug(role: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "-", role.casefold()).strip("-")
        return normalized

    @staticmethod
    def _search_url(role: str) -> str:
        return f"https://www.levels.fyi/jobs?searchText={quote_plus(role)}"

    @classmethod
    def _title_jobs_url(cls, role_slug: str) -> str:
        return f"{cls.base_url}/jobs/title/{role_slug}"

    @staticmethod
    def _extract_company_slug(href: str) -> str:
        if not href:
            return ""
        parsed = urlparse(href)
        match = _COMPANY_SALARIES_PATH_RE.match(parsed.path)
        if not match:
            return ""
        return match.group(1).strip()

    @staticmethod
    def _fallback_company_name(slug: str) -> str:
        if not slug:
            return ""
        tokens = [token for token in slug.replace("_", "-").split("-") if token]
        return " ".join(token.upper() if len(token) <= 3 else token.capitalize() for token in tokens)

    @classmethod
    def _clean_company_label(cls, label: str, slug: str) -> str:
        cleaned = re.sub(r"\b(?:icon|logo)\b", " ", label, flags=re.IGNORECASE)
        cleaned = _CURRENCY_SUFFIX_RE.sub("", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -:\n\t")

        if not cleaned:
            return cls._fallback_company_name(slug)

        lowered = cleaned.casefold()
        if lowered in _NOISE_LABELS or len(cleaned) > 80:
            return cls._fallback_company_name(slug)

        slug_label = slug.replace("-", " ").casefold()
        if slug_label and slug_label in lowered:
            start = lowered.find(slug_label)
            cleaned = cleaned[start : start + len(slug_label)]
            cleaned = re.sub(r"\s+", " ", cleaned).strip()

        parts = cleaned.split()
        if len(parts) >= 2:
            midpoint = len(parts) // 2
            if parts[:midpoint] == parts[midpoint:]:
                cleaned = " ".join(parts[:midpoint])

        return cleaned or cls._fallback_company_name(slug)

    @staticmethod
    def _is_job_detail_href(href: str) -> bool:
        if not href:
            return False
        parsed = urlparse(href)
        return bool(_JOB_DETAIL_PATH_RE.match(parsed.path + (f"?{parsed.query}" if parsed.query else "")))

    @staticmethod
    def _extract_job_id_from_href(href: str) -> str:
        """Parse the ``jobId`` query parameter from a Levels.fyi job-detail URL."""
        if not href:
            return ""
        try:
            parsed = urlparse(href)
            params = parse_qs(parsed.query)
            job_id_values = params.get("jobId") or params.get("jobid") or []
            return job_id_values[0].strip() if job_id_values else ""
        except Exception:
            return ""

    @staticmethod
    def _extract_domain_from_logo_url(logo_url: str) -> str:
        if not logo_url:
            return ""

        parsed = urlparse(logo_url)
        if parsed.netloc.casefold() != "img.logo.dev":
            return ""

        domain = parsed.path.strip("/").split("/", 1)[0].casefold()
        if not domain or "." not in domain:
            return ""
        return domain

    def _extract_companies_from_job_cards(self, soup: BeautifulSoup, page_url: str, role: str) -> list[DiscoveredCompany]:
        if "/jobs" not in urlparse(page_url).path:
            return []

        discovered: list[DiscoveredCompany] = []
        seen_names: set[str] = set()

        for card in soup.find_all(attrs={"role": "button"}):
            job_links = []
            for anchor in card.find_all("a", href=True):
                href = str(anchor.get("href") or "").strip()
                if self._is_job_detail_href(href):
                    job_links.append(urljoin(self.base_url, href))

            if not job_links:
                continue

            # Derive company name from the /companies/{slug}/salaries anchor inside the card,
            # falling back to the h2/h3 heading (which may be the job title on search pages).
            company_name = ""
            for anchor in card.find_all("a", href=True):
                href = str(anchor.get("href") or "").strip()
                slug = self._extract_company_slug(href)
                if slug:
                    label = anchor.get_text(" ", strip=True)
                    company_name = self._clean_company_label(label, slug)
                    break

            if not company_name:
                heading = card.find(["h2", "h3"])
                if heading is None:
                    continue
                label = heading.get_text(" ", strip=True)
                company_name = self._clean_company_label(label, "")

            lowered = company_name.casefold()
            if not company_name or lowered in _NOISE_LABELS or lowered in seen_names:
                continue

            logo = card.find("img")
            logo_url = str(logo.get("src") or "").strip() if logo else ""
            domain = self._extract_domain_from_logo_url(logo_url)

            seen_names.add(lowered)
            discovered.append(
                DiscoveredCompany(
                    name=company_name,
                    source=self.source_name,
                    role=role,
                    source_url=job_links[0],
                    domain=domain,
                )
            )

        return discovered

    def _extract_job_cards_from_html(self, html: str, page_url: str, role: str) -> list[LevelsFyiJobCard]:
        """Parse job cards from a Levels.fyi search or taxonomy page.

        Each card must have at least one job-detail link (``/jobs?jobId=...``).
        The job title is taken from the h2/h3 heading.
        The company name is taken from the ``/companies/{slug}/salaries`` anchor inside the card.
        """
        if "/jobs" not in urlparse(page_url).path:
            return []

        soup = BeautifulSoup(html, "html.parser")
        cards: list[LevelsFyiJobCard] = []
        seen_job_ids: set[str] = set()

        for card in soup.find_all(attrs={"role": "button"}):
            job_href = ""
            job_id = ""
            for anchor in card.find_all("a", href=True):
                href = str(anchor.get("href") or "").strip()
                if self._is_job_detail_href(href):
                    jid = self._extract_job_id_from_href(href)
                    if jid and jid not in seen_job_ids:
                        job_href = href
                        job_id = jid
                        break

            if not job_id:
                continue

            # Job title: prefer h2/h3 heading text
            heading = card.find(["h2", "h3"])
            job_title = heading.get_text(" ", strip=True) if heading else ""
            if not job_title:
                continue

            # Company name: from /companies/{slug}/salaries anchor
            company_name = ""
            domain = ""
            for anchor in card.find_all("a", href=True):
                href_c = str(anchor.get("href") or "").strip()
                slug = self._extract_company_slug(href_c)
                if slug:
                    label = anchor.get_text(" ", strip=True)
                    company_name = self._clean_company_label(label, slug)
                    break

            # Logo domain as fallback company domain
            logo = card.find("img")
            logo_url = str(logo.get("src") or "").strip() if logo else ""
            domain = self._extract_domain_from_logo_url(logo_url)

            source_url = urljoin(self.base_url, job_href)
            seen_job_ids.add(job_id)
            cards.append(
                LevelsFyiJobCard(
                    job_title=job_title,
                    company_name=company_name,
                    source_url=source_url,
                    external_job_id=job_id,
                    domain=domain,
                    role=role,
                )
            )

        return cards

    def _fetch_job_detail(self, session: requests.Session, url: str, config: CrawlerConfig) -> dict:
        """Fetch a Levels.fyi job detail page and extract description, location, and compensation.

        Returns a dict with keys ``description``, ``location``, ``compensation``.
        All values default to empty strings on failure or CSR-only content.
        """
        result = {"description": "", "location": "", "compensation": ""}
        response = self._request_with_retries(session, url, config)
        if response is None:
            return result
        try:
            response.raise_for_status()
        except requests.HTTPError:
            return result

        soup = BeautifulSoup(response.text, "html.parser")

        # Try JSON-LD first (JobPosting schema)
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, dict) and data.get("@type") == "JobPosting":
                    result["description"] = data.get("description", "")
                    job_loc = data.get("jobLocation", {})
                    if isinstance(job_loc, dict):
                        addr = job_loc.get("address", {})
                        if isinstance(addr, dict):
                            parts = [
                                addr.get("addressLocality", ""),
                                addr.get("addressRegion", ""),
                                addr.get("addressCountry", ""),
                            ]
                            result["location"] = ", ".join(p for p in parts if p)
                    salary = data.get("baseSalary", {})
                    if isinstance(salary, dict):
                        val = salary.get("value", {})
                        if isinstance(val, dict):
                            min_val = val.get("minValue", "")
                            max_val = val.get("maxValue", "")
                            currency = salary.get("currency", "")
                            if min_val or max_val:
                                result["compensation"] = f"{currency} {min_val}–{max_val}".strip()
                    if result["description"]:
                        return result
            except Exception:
                continue

        # Fallback: heuristic DOM scraping
        # Description: look for common content containers
        for selector in ["[data-testid='job-description']", "article", "main"]:
            el = soup.select_one(selector)
            if el:
                text = el.get_text(" ", strip=True)
                if len(text) > 100:
                    result["description"] = text[:8000]
                    break

        # Location: look for location hints
        for el in soup.find_all(string=re.compile(r"(?:remote|hybrid|on.?site|\b[A-Z][a-z]+,\s*[A-Z]{2}\b)", re.IGNORECASE)):
            parent = el.parent
            if parent:
                loc_text = parent.get_text(" ", strip=True)
                if loc_text and len(loc_text) < 80:
                    result["location"] = loc_text
                    break

        return result

    def _request_with_retries(self, session: requests.Session, url: str, config: CrawlerConfig) -> requests.Response | None:
        last_error: Exception | None = None
        for attempt in range(1, max(config.max_retries, 1) + 1):
            try:
                response = session.get(url, timeout=config.http_timeout_seconds)
            except requests.RequestException as exc:
                last_error = exc
                logger.debug("levelsfyi request attempt %d failed for %s: %s", attempt, url, exc)
                if attempt < max(config.max_retries, 1):
                    time.sleep(self._delay_seconds(config, attempt))
                continue

            if response.status_code in _TRANSIENT_STATUS_CODES and attempt < max(config.max_retries, 1):
                logger.debug("levelsfyi transient status %d for %s on attempt %d", response.status_code, url, attempt)
                time.sleep(self._delay_seconds(config, attempt))
                continue
            return response

        if last_error is not None:
            logger.debug("levelsfyi request exhausted retries for %s: %s", url, last_error)
        return None

    def _extract_companies_from_html(self, html: str, page_url: str, role: str) -> list[DiscoveredCompany]:
        soup = BeautifulSoup(html, "html.parser")
        discovered: list[DiscoveredCompany] = []
        seen_slugs: set[str] = set()

        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href") or "").strip()
            slug = self._extract_company_slug(href)
            if not slug or slug in seen_slugs:
                continue

            label = anchor.get_text(" ", strip=True)
            name = self._clean_company_label(label, slug)
            if not name:
                continue

            seen_slugs.add(slug)
            discovered.append(
                DiscoveredCompany(
                    name=name,
                    source=self.source_name,
                    role=role,
                    source_url=urljoin(page_url, href),
                )
            )

        if discovered:
            return discovered

        return self._extract_companies_from_job_cards(soup, page_url, role)

    def _extract_companies_from_markdown(self, markdown: str, role: str) -> list[DiscoveredCompany]:
        discovered: list[DiscoveredCompany] = []
        seen_slugs: set[str] = set()

        for match in _MARKDOWN_LINK_RE.finditer(markdown):
            label = match.group(1).strip()
            href = match.group(2).strip()
            slug = self._extract_company_slug(href)
            if not slug or slug in seen_slugs:
                continue

            name = self._clean_company_label(label, slug)
            if not name:
                continue

            seen_slugs.add(slug)
            discovered.append(
                DiscoveredCompany(
                    name=name,
                    source=self.source_name,
                    role=role,
                    source_url=urljoin(self.base_url, href),
                )
            )

        return discovered

    def discover_companies(self, roles: list[str], config: CrawlerConfig) -> list[DiscoveredCompany]:
        session = requests.Session()
        session.headers.update({"User-Agent": config.user_agent})

        discovered: list[DiscoveredCompany] = []
        seen_keys: set[tuple[str, str]] = set()
        max_per_role = max(1, config.levelsfyi_max_companies_per_role)

        for role in roles:
            role_slug = self._role_slug(role)
            if not role.strip() or not role_slug:
                continue

            role_companies: list[DiscoveredCompany] = []
            page_urls = [
                self._search_url(role),
                self._title_jobs_url(role_slug),
                f"{self.base_url}/t/{role_slug}",
            ]

            for page_url in page_urls:
                response = self._request_with_retries(session, page_url, config)
                if response is None:
                    continue
                try:
                    response.raise_for_status()
                except requests.HTTPError:
                    logger.debug("levelsfyi page returned %d for %s", response.status_code, page_url)
                    continue

                role_companies.extend(self._extract_companies_from_html(response.text, page_url, role))
                if len(role_companies) >= max_per_role:
                    break

            if len(role_companies) < max_per_role:
                markdown_url = f"{self.base_url}/t/{role_slug}.md"
                response = self._request_with_retries(session, markdown_url, config)
                if response is not None:
                    try:
                        response.raise_for_status()
                    except requests.HTTPError:
                        logger.debug("levelsfyi markdown route returned %d for %s", response.status_code, markdown_url)
                    else:
                        role_companies.extend(self._extract_companies_from_markdown(response.text, role))

            for company in role_companies:
                key = (role.casefold(), company.name.casefold())
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                discovered.append(company)
                if len([item for item in discovered if item.role == role]) >= max_per_role:
                    break

        logger.debug("Levels.fyi total companies found: %d", len(discovered))
        return discovered

    def discover_jobs(self, roles: list[str], config: CrawlerConfig) -> list[LevelsFyiJobCard]:
        """Discover job listings from Levels.fyi search pages and fetch their detail pages.

        Fetches description, location, and compensation from each job's detail page.
        Deduplicates by ``external_job_id`` (the ``jobId`` query parameter).
        """
        session = requests.Session()
        session.headers.update({"User-Agent": config.user_agent})

        all_cards: list[LevelsFyiJobCard] = []
        seen_job_ids: set[str] = set()
        max_per_role = max(1, config.levelsfyi_max_companies_per_role)

        for role in roles:
            role_slug = self._role_slug(role)
            if not role.strip() or not role_slug:
                continue

            role_cards: list[LevelsFyiJobCard] = []
            page_urls = [
                self._search_url(role),
                self._title_jobs_url(role_slug),
            ]

            for page_url in page_urls:
                response = self._request_with_retries(session, page_url, config)
                if response is None:
                    continue
                try:
                    response.raise_for_status()
                except requests.HTTPError:
                    logger.debug("levelsfyi page returned %d for %s", response.status_code, page_url)
                    continue

                cards = self._extract_job_cards_from_html(response.text, page_url, role)
                for card in cards:
                    if card.external_job_id not in seen_job_ids and len(role_cards) < max_per_role:
                        role_cards.append(card)
                        seen_job_ids.add(card.external_job_id)

                if len(role_cards) >= max_per_role:
                    break

            # Fetch detail pages for this role's cards
            for card in role_cards:
                try:
                    details = self._fetch_job_detail(session, card.source_url, config)
                    card.description = details["description"]
                    card.location = details["location"]
                    card.compensation = details["compensation"]
                except Exception as exc:
                    logger.debug("levelsfyi detail fetch failed for %s: %s", card.source_url, exc)

            all_cards.extend(role_cards)

        logger.debug("Levels.fyi total jobs discovered: %d", len(all_cards))
        return all_cards