from __future__ import annotations

import argparse
import json

from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.db import get_database
from src.python.web_crawler.workflow1 import run_workflow1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the web crawler workflows")
    parser.add_argument("--identity-id", required=True, help="MongoDB ObjectId of the identity to crawl for")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = CrawlerConfig.from_env()
    database = get_database(config)
    result = run_workflow1(database, config, args.identity_id)
    print(json.dumps(result.__dict__, indent=2))


if __name__ == "__main__":
    main()