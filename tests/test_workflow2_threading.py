"""
Tests for workflow2 parallel ATS detection with thread pool.

Tests cover:
- Thread-local session isolation
- Error handling and resilience
- Result accumulation correctness
- Parallel execution verification
- Compatibility with expected output format
"""

from __future__ import annotations

import logging
import unittest
from concurrent.futures import Future
from typing import Any
from unittest.mock import MagicMock, Mock, patch

from src.python.web_crawler.executor import (
    ATSWorkerResult,
    ATSWorkerTask,
    ThreadSafeSessionPool,
    _detect_ats_worker,
)
from src.python.web_crawler.config import CrawlerConfig
from bson import ObjectId

logger = logging.getLogger(__name__)


class TestThreadSafeSessionPool(unittest.TestCase):
    """Tests for ThreadSafeSessionPool thread-local session management."""

    def test_session_creation(self):
        """Test that a session is created on first access."""
        pool = ThreadSafeSessionPool(user_agent="test-agent")
        session = pool.get_session()
        
        self.assertIsNotNone(session)
        self.assertEqual(session.headers.get("User-Agent"), "test-agent")

    def test_session_reuse_same_thread(self):
        """Test that the same session is reused within the same thread."""
        pool = ThreadSafeSessionPool(user_agent="test-agent")
        session1 = pool.get_session()
        session2 = pool.get_session()
        
        self.assertIs(session1, session2, "Session should be reused in the same thread")

    def test_session_isolation_different_threads(self):
        """Test that different threads get different sessions."""
        import threading
        
        pool = ThreadSafeSessionPool(user_agent="test-agent")
        sessions = []
        
        def get_session_in_thread():
            sessions.append(pool.get_session())
        
        thread1 = threading.Thread(target=get_session_in_thread)
        thread2 = threading.Thread(target=get_session_in_thread)
        
        thread1.start()
        thread2.start()
        thread1.join()
        thread2.join()
        
        self.assertEqual(len(sessions), 2)
        self.assertIsNot(sessions[0], sessions[1], "Different threads should have different sessions")

    def test_session_pool_close_all(self):
        """Test that close_all completes without error."""
        pool = ThreadSafeSessionPool(user_agent="test-agent")
        _ = pool.get_session()
        
        # Should not raise an exception
        pool.close_all()


class TestATSWorkerTask(unittest.TestCase):
    """Tests for ATSWorkerTask data structure."""

    def test_task_creation(self):
        """Test creating an ATSWorkerTask."""
        task = ATSWorkerTask(
            company_id="test-id",
            company_object_id=ObjectId(),
            company_name="Test Company",
            candidate_urls=["http://example.com"],
            company_index=1,
            total_companies=10,
        )
        
        self.assertEqual(task.company_id, "test-id")
        self.assertEqual(task.company_name, "Test Company")
        self.assertEqual(len(task.candidate_urls), 1)


class TestATSWorkerResult(unittest.TestCase):
    """Tests for ATSWorkerResult data structure."""

    def test_success_result(self):
        """Test creating a successful result."""
        result = ATSWorkerResult(
            company_id="test-id",
            company_object_id=ObjectId(),
            company_name="Test Company",
            company_index=1,
            success=True,
            provider="greenhouse",
            slug="test-company",
        )
        
        self.assertTrue(result.success)
        self.assertEqual(result.provider, "greenhouse")
        self.assertEqual(result.slug, "test-company")
        self.assertIsNone(result.error_type)

    def test_failure_result(self):
        """Test creating a failure result."""
        result = ATSWorkerResult(
            company_id="test-id",
            company_object_id=ObjectId(),
            company_name="Test Company",
            company_index=1,
            success=False,
            error_type="no_ats_provider",
            error_message="No provider detected",
        )
        
        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "no_ats_provider")
        self.assertEqual(result.error_message, "No provider detected")


class TestDetectATSWorker(unittest.TestCase):
    """Tests for _detect_ats_worker function."""

    def setUp(self):
        """Set up test fixtures."""
        self.config = MagicMock(spec=CrawlerConfig)
        self.config.user_agent = "test-agent"
        self.pool = ThreadSafeSessionPool(user_agent="test-agent")
        self.task = ATSWorkerTask(
            company_id="test-id",
            company_object_id=ObjectId(),
            company_name="Test Company",
            candidate_urls=["http://example.com"],
            company_index=1,
            total_companies=10,
        )

    def test_worker_handles_no_ats_provider(self):
        """Test worker handles case when no ATS provider is detected."""
        with patch("src.python.web_crawler.executor.detect_ats_provider") as mock_detect:
            mock_detect.return_value = None
            
            result = _detect_ats_worker(self.task, self.config, self.pool)
            
            self.assertFalse(result.success)
            self.assertEqual(result.error_type, "no_ats_provider")

    def test_worker_handles_successful_detection_with_slug(self):
        """Test worker handles successful ATS detection with direct slug resolution."""
        from src.python.web_crawler.sources.ats_detector import ATSDetectionResult
        
        with patch("src.python.web_crawler.executor.detect_ats_provider") as mock_detect, \
             patch("src.python.web_crawler.executor.resolve_direct_slug") as mock_slug:
            
            mock_detect.return_value = ATSDetectionResult(
                provider="greenhouse",
                board_url="http://greenhouse.io",
                checked_url="http://example.com",
            )
            mock_slug.return_value = "test-company"
            
            result = _detect_ats_worker(self.task, self.config, self.pool)
            
            self.assertTrue(result.success)
            self.assertEqual(result.provider, "greenhouse")
            self.assertEqual(result.slug, "test-company")

    def test_worker_handles_ats_detection_without_slug(self):
        """Test worker handles ATS detection but slug resolution fails."""
        from src.python.web_crawler.sources.ats_detector import ATSDetectionResult
        
        with patch("src.python.web_crawler.executor.detect_ats_provider") as mock_detect, \
             patch("src.python.web_crawler.executor.resolve_direct_slug") as mock_slug:
            
            mock_detect.return_value = ATSDetectionResult(
                provider="greenhouse",
                board_url="http://greenhouse.io",
                checked_url="http://example.com",
            )
            mock_slug.return_value = None
            
            result = _detect_ats_worker(self.task, self.config, self.pool)
            
            self.assertFalse(result.success)
            self.assertEqual(result.provider, "greenhouse")
            self.assertEqual(result.error_type, "slug_not_resolved_direct")

    def test_worker_handles_ats_request_failure(self):
        """Test worker handles ATSRequestFailure exceptions."""
        from src.python.web_crawler.sources.ats_detector import ATSRequestFailure
        
        with patch("src.python.web_crawler.executor.detect_ats_provider") as mock_detect:
            mock_detect.side_effect = ATSRequestFailure(
                failure_type="dns_resolution",
                url="http://example.com",
                message="DNS resolution failed",
            )
            
            result = _detect_ats_worker(self.task, self.config, self.pool)
            
            self.assertFalse(result.success)
            self.assertIn("dns_resolution", result.error_type)

    def test_worker_handles_unexpected_exception(self):
        """Test worker handles unexpected exceptions gracefully."""
        with patch("src.python.web_crawler.executor.detect_ats_provider") as mock_detect:
            mock_detect.side_effect = ValueError("Unexpected error")
            
            result = _detect_ats_worker(self.task, self.config, self.pool)
            
            self.assertFalse(result.success)
            self.assertEqual(result.error_type, "unexpected_error")


class TestWorkflow2Integration(unittest.TestCase):
    """Integration tests for workflow2 with parallel execution.
    
    Note: These are minimal integration tests. For full integration testing,
    use tests/e2e/test_workflow2_integration.sh with real MongoDB and network.
    """

    def test_workflow2_result_structure(self):
        """Test that workflow2 returns correct result structure."""
        from src.python.web_crawler.models import Workflow2Result
        
        result = Workflow2Result()
        result.company_ids.append("test-id-1")
        result.company_ids.append("test-id-2")
        result.enriched_count = 1
        result.skipped_count = 1
        result.failed_count = 0
        result.ats_providers["greenhouse"] = 1
        
        self.assertEqual(len(result.company_ids), 2)
        self.assertEqual(result.enriched_count, 1)
        self.assertEqual(result.skipped_count, 1)
        self.assertEqual(result.ats_providers["greenhouse"], 1)

    def test_worker_pool_submitting_multiple_tasks(self):
        """Test that executor properly submits and processes multiple tasks."""
        from concurrent.futures import ThreadPoolExecutor
        
        config = MagicMock(spec=CrawlerConfig)
        config.user_agent = "test-agent"
        pool = ThreadSafeSessionPool(user_agent="test-agent")
        
        tasks = [
            ATSWorkerTask(
                company_id=f"id-{i}",
                company_object_id=ObjectId(),
                company_name=f"Company {i}",
                candidate_urls=[f"http://example{i}.com"],
                company_index=i + 1,
                total_companies=3,
            )
            for i in range(3)
        ]
        
        # Mock the detect_ats_worker to return fast results
        def mock_worker(task, config, pool):
            return ATSWorkerResult(
                company_id=task.company_id,
                company_object_id=task.company_object_id,
                company_name=task.company_name,
                company_index=task.company_index,
                success=False,
                error_type="no_ats_provider",
                error_message="Test result",
            )
        
        with patch("src.python.web_crawler.executor._detect_ats_worker", side_effect=mock_worker):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(_detect_ats_worker, task, config, pool)
                    for task in tasks
                ]
                
                results = [f.result() for f in futures]
                
                self.assertEqual(len(results), 3)
                self.assertTrue(all(not r.success for r in results))


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    unittest.main()
