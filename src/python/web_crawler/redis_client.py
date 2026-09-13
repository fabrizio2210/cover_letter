from __future__ import annotations

import redis

from src.python.web_crawler.config import CrawlerConfig

REDIS_CONNECT_TIMEOUT = 5
REDIS_SOCKET_TIMEOUT = 60
REDIS_HEALTH_CHECK_INTERVAL = 30


def parse_sentinels(raw: str | None) -> list[tuple[str, int]] | None:
    if not raw:
        return None
    parsed: list[tuple[str, int]] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        host, sep, port = entry.rpartition(":")
        if not host or not sep:
            continue
        try:
            parsed.append((host, int(port or 26379)))
        except ValueError:
            continue
    return parsed or None


def build_redis_client(
    *,
    host: str = "localhost",
    port: int = 6379,
    sentinels: list[tuple[str, int]] | None = None,
    master_name: str | None = None,
    decode_responses: bool = False,
    socket_connect_timeout: int = REDIS_CONNECT_TIMEOUT,
    socket_timeout: int = REDIS_SOCKET_TIMEOUT,
    socket_keepalive: bool = True,
    health_check_interval: int = REDIS_HEALTH_CHECK_INTERVAL,
) -> redis.Redis:
    if sentinels and master_name:
        sentinel = redis.sentinel.Sentinel(
            sentinels,
            socket_connect_timeout=socket_connect_timeout,
            socket_timeout=socket_timeout,
        )
        return sentinel.master_for(
            master_name,
            decode_responses=decode_responses,
            socket_keepalive=socket_keepalive,
            health_check_interval=health_check_interval,
        )
    return redis.Redis(
        host=host,
        port=port,
        socket_connect_timeout=socket_connect_timeout,
        socket_timeout=socket_timeout,
        socket_keepalive=socket_keepalive,
        health_check_interval=health_check_interval,
        decode_responses=decode_responses,
    )


def connect_redis(config: CrawlerConfig, *, decode_responses: bool = False) -> redis.Redis:
    return build_redis_client(
        host=config.redis_host,
        port=config.redis_port,
        sentinels=parse_sentinels(config.redis_sentinels),
        master_name=config.redis_master_name,
        decode_responses=decode_responses,
    )
