from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import logging

from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.db import get_database
from src.python.web_crawler.workflow1 import run_workflow1
from src.python.web_crawler.workflow2 import run_workflow2
from src.python.web_crawler.workflow3 import run_workflow3

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the web crawler workflows")
    parser.add_argument("--identity-id", required=True, help="MongoDB ObjectId of the identity to crawl for")
    parser.add_argument(
        "--force-serp-retry",
        action="store_true",
        help="Bypass prior SERP-attempt checks in workflow2 and retry slug search fallback",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = CrawlerConfig.from_env()
    if args.force_serp_retry:
        config.force_serp_retry_on_prior_attempt = True
    database = get_database(config)
    workflow1_result = run_workflow1(database, config, args.identity_id)
    workflow2_result = run_workflow2(database, config, workflow1_result.company_ids)
    workflow3_result = run_workflow3(database, config, workflow2_result.company_ids, args.identity_id)
    print(
        json.dumps(
            {
                "workflow1": asdict(workflow1_result),
                "workflow2": asdict(workflow2_result),
                "workflow3": asdict(workflow3_result),
            }
        )
    )


if __name__ == "__main__":
    main()