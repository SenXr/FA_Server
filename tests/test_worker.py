from __future__ import annotations

import unittest
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import bootstrap
from fa_server.worker import BackgroundTaskManager, log_background_task_failure


class BackgroundTaskManagerTests(unittest.TestCase):
    def test_failed_future_logs_task_context_and_traceback(self):
        future: Future[None] = Future()
        future.set_exception(RuntimeError("sync failed"))

        with self.assertLogs("fa_server.worker", level="ERROR") as captured:
            log_background_task_failure(
                future,
                task_type="sync",
                job_id="job-123",
                folder_name="raw_test",
            )

        output = "\n".join(captured.output)
        self.assertIn("job-123", output)
        self.assertIn("raw_test", output)
        self.assertIn("RuntimeError: sync failed", output)

    def test_submit_sync_registers_failure_callback(self):
        sync_service = MagicMock()
        sync_service.run_job.side_effect = RuntimeError("sync failed")
        manager = BackgroundTaskManager(sync_service, MagicMock(), max_workers=1)

        try:
            with patch(
                "fa_server.worker.log_background_task_failure"
            ) as log_failure:
                future = manager.submit_sync(
                    "job-456",
                    SimpleNamespace(folder_name="raw_test"),
                )
                with self.assertRaisesRegex(RuntimeError, "sync failed"):
                    future.result(timeout=2)
                manager.executor.shutdown(wait=True)

            log_failure.assert_called_once()
            self.assertEqual("sync", log_failure.call_args.kwargs["task_type"])
            self.assertEqual("job-456", log_failure.call_args.kwargs["job_id"])
            self.assertEqual("raw_test", log_failure.call_args.kwargs["folder_name"])
        finally:
            manager.executor.shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
