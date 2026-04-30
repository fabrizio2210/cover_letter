from __future__ import annotations

from pymongo import MongoClient

from src.python.web_crawler.config import CrawlerConfig


def get_client(config: CrawlerConfig) -> MongoClient:
    return MongoClient(config.mongo_host)


def get_database(config: CrawlerConfig):
    client = get_client(config)
    return client[config.db_name]


def get_user_database(config: CrawlerConfig, user_id: str):
    if not user_id:
        raise ValueError("user_id is required to access user-scoped database")
    # user_id is the JWT sub claim: a SHA-256-derived hex string set at login time.
    # Use it directly as the DB suffix — no additional hashing needed.
    client = get_client(config)
    return client[f"cover_letter_{user_id}"]