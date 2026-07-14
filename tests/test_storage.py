from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import bootstrap
from fa_server.storage import ActiveJobExists, DuplicateFolderTask, TaskRepository


class TaskRepositoryTests(unittest.TestCase):
    def test_upsert_raw_file_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "folder" / "tasks.sqlite3"
            raw_path = Path(temp_dir) / "folder" / "image.raw"
            raw_path.parent.mkdir(parents=True)
            raw_path.write_bytes(b"raw")

            repository = TaskRepository(db_path)
            first_inserted, first_id = repository.upsert_raw_file(
                folder_name="folder",
                raw_path=raw_path,
                sync_job_id="job-1",
                transcode_rename_enabled=False,
            )
            second_inserted, second_id = repository.upsert_raw_file(
                folder_name="folder",
                raw_path=raw_path,
                sync_job_id="job-1",
                transcode_rename_enabled=False,
            )

            self.assertTrue(first_inserted)
            self.assertFalse(second_inserted)
            self.assertEqual(first_id, second_id)
            self.assertEqual({"pending": 1}, repository.count_images_by_sr_status("folder"))

    def test_conversion_done_makes_image_pending_for_sr(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "folder" / "tasks.sqlite3"
            raw_path = root / "folder" / "image.raw"
            bmp_path = root / "folder" / "image.bmp"
            final_path = root / "folder" / "image_x1_y2.bmp"
            raw_path.parent.mkdir(parents=True)
            raw_path.write_bytes(b"raw")
            bmp_path.write_bytes(b"bmp")

            repository = TaskRepository(db_path)
            inserted, image_id = repository.upsert_raw_file(
                folder_name="folder",
                raw_path=raw_path,
                sync_job_id="job-1",
                transcode_rename_enabled=True,
            )
            repository.mark_conversion_done(
                image_id,
                bmp_path=bmp_path,
                final_bmp_path=final_path,
            )

            self.assertTrue(inserted)
            pending = repository.list_pending_sr("folder", limit=60)
            self.assertEqual(1, len(pending))
            self.assertEqual(str(final_path.resolve()), pending[0].sr_input_path)

    def test_initial_sync_task_rejects_duplicate_folder_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "folder" / "tasks.sqlite3"
            repository = TaskRepository(db_path)
            repository.create_sync_job(
                job_id="job-1",
                folder_name="folder",
                remote_url="rsync://host/data/folder/",
                local_dir=db_path.parent,
                transcode_rename_enabled=True,
                idle_timeout_seconds=600,
                poll_interval_seconds=30,
            )
            repository.update_sync_job("job-1", status="completed")

            with self.assertRaises(DuplicateFolderTask):
                repository.create_sync_job(
                    job_id="job-2",
                    folder_name="folder",
                    remote_url="rsync://host/data/folder/",
                    local_dir=db_path.parent,
                    transcode_rename_enabled=True,
                    idle_timeout_seconds=600,
                    poll_interval_seconds=30,
                )

    def test_update_sync_task_allows_existing_folder_after_initial_completes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "folder" / "tasks.sqlite3"
            repository = TaskRepository(db_path)
            repository.create_sync_job(
                job_id="job-1",
                folder_name="folder",
                remote_url="rsync://host/data/folder/",
                local_dir=db_path.parent,
                transcode_rename_enabled=True,
                idle_timeout_seconds=600,
                poll_interval_seconds=30,
            )
            repository.update_sync_job("job-1", status="completed")

            repository.create_sync_job(
                job_id="job-2",
                folder_name="folder",
                remote_url="rsync://host/data/folder/",
                local_dir=db_path.parent,
                transcode_rename_enabled=True,
                idle_timeout_seconds=600,
                poll_interval_seconds=30,
                job_kind="update",
                allow_existing_folder=True,
            )

            self.assertEqual("update", repository.get_sync_job("job-2")["job_kind"])

    def test_active_sync_task_blocks_update_for_same_folder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "folder" / "tasks.sqlite3"
            repository = TaskRepository(db_path)
            repository.create_sync_job(
                job_id="job-1",
                folder_name="folder",
                remote_url="rsync://host/data/folder/",
                local_dir=db_path.parent,
                transcode_rename_enabled=True,
                idle_timeout_seconds=600,
                poll_interval_seconds=30,
            )

            with self.assertRaises(ActiveJobExists):
                repository.create_sync_job(
                    job_id="job-2",
                    folder_name="folder",
                    remote_url="rsync://host/data/folder/",
                    local_dir=db_path.parent,
                    transcode_rename_enabled=True,
                    idle_timeout_seconds=600,
                    poll_interval_seconds=30,
                    job_kind="update",
                    allow_existing_folder=True,
                )

    def test_changed_existing_file_is_requeued(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "folder" / "tasks.sqlite3"
            raw_path = Path(temp_dir) / "folder" / "image.raw"
            raw_path.parent.mkdir(parents=True)
            raw_path.write_bytes(b"raw")
            repository = TaskRepository(db_path)

            inserted, image_id = repository.upsert_raw_file(
                folder_name="folder",
                raw_path=raw_path,
                sync_job_id="job-1",
                transcode_rename_enabled=False,
            )
            raw_path.write_bytes(b"raw-updated")
            changed, changed_id = repository.upsert_raw_file(
                folder_name="folder",
                raw_path=raw_path,
                sync_job_id="job-2",
                transcode_rename_enabled=False,
            )

            self.assertTrue(inserted)
            self.assertTrue(changed)
            self.assertEqual(image_id, changed_id)


if __name__ == "__main__":
    unittest.main()
