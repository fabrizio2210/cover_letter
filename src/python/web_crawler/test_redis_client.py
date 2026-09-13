from __future__ import annotations

import unittest
from unittest import mock

from src.python.web_crawler import redis_client


class ParseSentinelsTests(unittest.TestCase):
    def test_none_and_empty_are_none(self):
        self.assertIsNone(redis_client.parse_sentinels(None))
        self.assertIsNone(redis_client.parse_sentinels(""))
        self.assertIsNone(redis_client.parse_sentinels("   "))

    def test_parses_comma_separated(self):
        self.assertEqual(
            redis_client.parse_sentinels("s1:26379, s2:26379 ,s3:26379"),
            [("s1", 26379), ("s2", 26379), ("s3", 26379)],
        )

    def test_skips_invalid_entries(self):
        self.assertEqual(
            redis_client.parse_sentinels("s1:26379, bad, :26379, s2:abc"),
            [("s1", 26379)],
        )


class BuildRedisClientTests(unittest.TestCase):
    def test_fallback_uses_plain_redis_client(self):
        with mock.patch.object(redis_client.redis, "Redis") as redis_cls:
            redis_client.build_redis_client(host="h", port=6380, decode_responses=True)

        redis_cls.assert_called_once_with(
            host="h",
            port=6380,
            socket_connect_timeout=5,
            socket_timeout=60,
            socket_keepalive=True,
            health_check_interval=30,
            decode_responses=True,
        )

    def test_sentinel_path_uses_master_for(self):
        fake_master = object()
        fake_sentinel = mock.Mock()
        fake_sentinel.master_for.return_value = fake_master

        with mock.patch.object(
            redis_client.redis.sentinel, "Sentinel", return_value=fake_sentinel
        ) as sentinel_cls:
            client = redis_client.build_redis_client(
                host="h",
                port=6380,
                sentinels=[("s1", 26379), ("s2", 26379)],
                master_name="mymaster",
                decode_responses=True,
            )

        self.assertIs(client, fake_master)
        sentinel_cls.assert_called_once_with(
            [("s1", 26379), ("s2", 26379)],
            socket_connect_timeout=5,
            socket_timeout=60,
        )
        fake_sentinel.master_for.assert_called_once_with(
            "mymaster",
            decode_responses=True,
            socket_keepalive=True,
            health_check_interval=30,
        )


if __name__ == "__main__":
    unittest.main()
