from __future__ import annotations

from pymongo import MongoClient

from src.python.web_crawler.config import CrawlerConfig


def get_client(config: CrawlerConfig) -> MongoClient:
    return MongoClient(config.mongo_host)


def get_database(config: CrawlerConfig):
    client = get_client(config)
    return client[config.db_name]