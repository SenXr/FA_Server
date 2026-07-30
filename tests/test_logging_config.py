from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

import bootstrap
from fa_server.config import PROJECT_ROOT
from fa_server.logging_config import DEFAULT_LOG_PATH, configure_logging


class LoggingConfigTests(unittest.TestCase):
    def test_default_log_path_is_project_logs_folder(self):
        self.assertEqual(PROJECT_ROOT / "logs" / "fa_server.log", DEFAULT_LOG_PATH)

    def test_exception_traceback_is_written_to_log_file(self):
        root_logger = logging.getLogger()
        previous_handlers = root_logger.handlers[:]
        previous_level = root_logger.level

        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "logs" / "fa_server.log"
            try:
                configure_logging(log_path=log_path)
                try:
                    raise RuntimeError("background task exploded")
                except RuntimeError:
                    logging.getLogger("fa_server.test").exception(
                        "background task failed"
                    )

                for handler in root_logger.handlers:
                    handler.flush()

                content = log_path.read_text(encoding="utf-8")
                self.assertIn("background task failed", content)
                self.assertIn("RuntimeError: background task exploded", content)
                self.assertIn("Traceback", content)
            finally:
                for handler in root_logger.handlers:
                    if handler not in previous_handlers:
                        handler.close()
                root_logger.handlers = previous_handlers
                root_logger.setLevel(previous_level)


if __name__ == "__main__":
    unittest.main()
