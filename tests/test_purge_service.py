from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import bootstrap
from fa_server.config import PROJECT_ROOT
from fa_server.services.purge_service import PurgeService, purge_log_path
from fa_server.storage import TaskRepository


class PurgeServiceTests(unittest.TestCase):
    def test_purge_deletes_oldest_folders_when_limit_is_exceeded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index in range(12):
                folder = root / f"folder_{index:02d}"
                folder.mkdir()
                (folder / "data.txt").write_text(str(index), encoding="utf-8")
                os.utime(folder, (index + 1, index + 1))

            service = PurgeService(
                local_root=root,
                max_folders=10,
                database_filename="tasks.sqlite3",
                log_path=root / ".fa_server" / "purge.log",
            )

            result = service.purge_once()

            self.assertEqual(("folder_00", "folder_01"), result.deleted)
            self.assertFalse((root / "folder_00").exists())
            self.assertFalse((root / "folder_01").exists())
            self.assertEqual(
                10,
                len(
                    [
                        path
                        for path in root.iterdir()
                        if path.is_dir() and not path.name.startswith(".")
                    ]
                ),
            )

    def test_purge_skips_folder_with_active_job(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index in range(11):
                folder = root / f"folder_{index:02d}"
                folder.mkdir()
                (folder / "data.txt").write_text(str(index), encoding="utf-8")

            repository = TaskRepository(root / "folder_00" / "tasks.sqlite3")
            repository.create_sync_job(
                job_id="sync-1",
                folder_name="folder_00",
                remote_url="rsync://host/data/folder_00/",
                local_dir=root / "folder_00",
                transcode_rename_enabled=True,
                idle_timeout_seconds=600,
                poll_interval_seconds=30,
            )

            for index in range(11):
                folder = root / f"folder_{index:02d}"
                os.utime(folder, (index + 1, index + 1))

            service = PurgeService(
                local_root=root,
                max_folders=10,
                database_filename="tasks.sqlite3",
                log_path=root / ".fa_server" / "purge.log",
            )

            result = service.purge_once()

            self.assertEqual(("folder_00",), result.skipped_active)
            self.assertEqual(("folder_01",), result.deleted)
            self.assertTrue((root / "folder_00").exists())
            self.assertFalse((root / "folder_01").exists())

    def test_purge_writes_compact_log_line(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index in range(2):
                folder = root / f"folder_{index:02d}"
                folder.mkdir()
                os.utime(folder, (index + 1, index + 1))
            log_path = root / ".fa_server" / "purge.log"
            service = PurgeService(
                local_root=root,
                max_folders=1,
                database_filename="tasks.sqlite3",
                log_path=log_path,
            )

            service.purge_once()

            lines = log_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(1, len(lines))
            self.assertIn("scanned=2", lines[0])
            self.assertIn("max=1", lines[0])
            self.assertIn("action=purge", lines[0])
            self.assertIn("cleared_count=1", lines[0])
            self.assertIn("cleared_folders=folder_00", lines[0])
            self.assertIn("errors=-", lines[0])

    def test_default_purge_log_path_is_project_log_folder(self):
        self.assertEqual(PROJECT_ROOT / "log" / "purge.log", purge_log_path("log/purge.log"))


if __name__ == "__main__":
    unittest.main()
