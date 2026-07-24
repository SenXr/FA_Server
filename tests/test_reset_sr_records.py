from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import bootstrap
from fa_server.storage import TaskRepository


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "reset_sr_records.py"


class ResetSuperResolutionRecordsTests(unittest.TestCase):
    def test_resets_sr_jobs_and_completed_images_with_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            folder = root / "folder"
            database_path = folder / "tasks.sqlite3"
            image_path = folder / "image.bmp"
            output_path = folder / "Super_Resolution" / "image.bmp"
            model_path = root / "model.pth"
            image_path.parent.mkdir(parents=True)
            image_path.write_bytes(b"bmp")
            output_path.parent.mkdir()
            output_path.write_bytes(b"sr")

            repository = TaskRepository(database_path)
            _, image_id = repository.upsert_raw_file(
                folder_name="folder",
                raw_path=image_path,
                sync_job_id="sync",
                transcode_rename_enabled=False,
            )
            repository.mark_sr_done(image_id, output_path)
            repository.create_sr_job(
                job_id="sr-job",
                folder_name="folder",
                output_dir=output_path.parent,
                model_path=model_path,
                batch_size=3,
                process_partial_batch=True,
            )
            repository.update_sr_job("sr-job", status="completed")

            completed = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), str(database_path)],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual(1, len(list(folder.glob("tasks.sqlite3.*.bak"))))
            with closing(sqlite3.connect(database_path)) as connection:
                self.assertEqual(
                    0,
                    connection.execute("SELECT COUNT(*) FROM sr_jobs").fetchone()[0],
                )
                row = connection.execute(
                    """
                    SELECT sr_status, sr_job_id, sr_output_path, processed_at
                    FROM image_tasks
                    WHERE id = ?
                    """,
                    (image_id,),
                ).fetchone()
            self.assertEqual(("pending", None, None, None), row)

    def test_refuses_to_reset_active_job_without_force(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            folder = root / "folder"
            database_path = folder / "tasks.sqlite3"
            repository = TaskRepository(database_path)
            repository.create_sr_job(
                job_id="active-job",
                folder_name="folder",
                output_dir=folder / "Super_Resolution",
                model_path=root / "model.pth",
                batch_size=3,
                process_partial_batch=True,
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    str(database_path),
                    "--no-backup",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(2, completed.returncode)
            self.assertIn("active super-resolution job", completed.stderr)
            self.assertIsNotNone(repository.get_sr_job("active-job"))


if __name__ == "__main__":
    unittest.main()
