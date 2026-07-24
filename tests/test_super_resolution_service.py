from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bootstrap
from fa_server.config import AppConfig
from fa_server.services.super_resolution_service import (
    SuperResolutionService,
    SuperResolutionTaskRequest,
    super_resolution_task_table_complete,
)
from fa_server.storage import TaskRepository


class RecordingModelRunner:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.loaded = False
        self.model_paths: list[Path] = []

    def ensure_loaded(self, model_path: Path) -> object:
        self.loaded = True
        self.model_paths.append(model_path)
        return object()

    def enhance(
        self,
        paths: list[str],
        output_dir: Path,
        model_path: Path,
    ) -> list[Path]:
        self.model_paths.append(model_path)
        self.calls.append(paths)
        output_dir.mkdir(parents=True, exist_ok=True)
        outputs = []
        for path in paths:
            output = output_dir / Path(path).name
            output.write_bytes(b"sr")
            outputs.append(output)
        return outputs


class FailingModelRunner:
    def ensure_loaded(self, model_path: Path) -> object:
        del model_path
        raise RuntimeError("model dependencies are missing")

    def enhance(
        self,
        paths: list[str],
        output_dir: Path,
        model_path: Path,
    ) -> list[Path]:
        del paths, output_dir, model_path
        raise AssertionError("enhance should not run when model loading fails")


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleep_calls: list[int] = []
        self.on_sleep = None

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: int) -> None:
        self.sleep_calls.append(seconds)
        self.now += seconds
        if self.on_sleep is not None:
            self.on_sleep()


class SuperResolutionServiceTests(unittest.TestCase):
    def test_build_request_accepts_absolute_model_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            model_path = root / "models" / "production_model.pth"
            service = SuperResolutionService(AppConfig(local_root=root))

            request = service.build_request(
                {
                    "folder_name": "folder",
                    "local_root": str(root),
                    "model_path": str(model_path),
                }
            )

            self.assertEqual(model_path, request.model_path)

    def test_build_request_rejects_relative_model_path(self):
        service = SuperResolutionService(AppConfig())

        with self.assertRaisesRegex(ValueError, "absolute path"):
            service.build_request(
                {
                    "folder_name": "folder",
                    "model_path": "models/production_model.pth",
                }
            )

    def test_super_resolution_task_table_complete_checks_sqlite_statuses(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository = TaskRepository(root / "folder" / "tasks.sqlite3")
            self.assertFalse(
                super_resolution_task_table_complete(repository, "folder")
            )
            image_path = root / "folder" / "image.bmp"
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image_path.write_bytes(b"bmp")
            inserted, image_id = repository.upsert_raw_file(
                folder_name="folder",
                raw_path=image_path,
                sync_job_id="sync",
                transcode_rename_enabled=False,
            )
            self.assertTrue(inserted)
            self.assertFalse(
                super_resolution_task_table_complete(repository, "folder")
            )
            repository.mark_sr_done(image_id, root / "folder" / "Super_Resolution" / "image_sr.bmp")
            self.assertTrue(
                super_resolution_task_table_complete(repository, "folder")
            )

    def test_super_resolution_task_table_waits_for_active_sync_job(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository = TaskRepository(root / "folder" / "tasks.sqlite3")
            repository.create_sync_job(
                job_id="sync",
                folder_name="folder",
                remote_url="rsync://host/data/folder/",
                local_dir=root / "folder",
                transcode_rename_enabled=False,
                idle_timeout_seconds=600,
                poll_interval_seconds=30,
            )
            image_path = root / "folder" / "image.bmp"
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image_path.write_bytes(b"bmp")
            _, image_id = repository.upsert_raw_file(
                folder_name="folder",
                raw_path=image_path,
                sync_job_id="sync",
                transcode_rename_enabled=False,
            )
            repository.mark_sr_done(image_id, root / "folder" / "Super_Resolution" / "image_sr.bmp")

            self.assertFalse(
                super_resolution_task_table_complete(repository, "folder")
            )
            repository.update_sync_job("sync", status="completed")
            self.assertTrue(
                super_resolution_task_table_complete(repository, "folder")
            )

    def test_model_runner_loads_model_session_once(self):
        class FakeLoadedModel:
            def __init__(self):
                self.calls = 0

            def infer_batch(self, paths: list[str], output_dir: Path) -> list[Path]:
                self.calls += 1
                output_dir.mkdir(parents=True, exist_ok=True)
                outputs = []
                for path in paths:
                    output = output_dir / f"{Path(path).stem}_sr{Path(path).suffix}"
                    output.write_bytes(b"sr")
                    outputs.append(output)
                return outputs

        model = FakeLoadedModel()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_a = root / "a.bmp"
            image_b = root / "b.bmp"
            image_a.write_bytes(b"a")
            image_b.write_bytes(b"b")
            runner = SuperResolutionService(AppConfig(local_root=root)).model_runner
            model_path = root / "selected_model.pth"

            with (
                patch(
                    "fa_server.services.super_resolution_service.load_ep5_model",
                    return_value=(model, object()),
                ) as load_model,
                patch(
                    "fa_server.services.super_resolution_service.batch_process_ep5",
                    side_effect=lambda input_paths, output_path, model, config: model.infer_batch(
                        input_paths,
                        Path(output_path),
                    ),
                ),
            ):
                first = runner.enhance(
                    [str(image_a)],
                    root / "Super_Resolution",
                    model_path,
                )
                second = runner.enhance(
                    [str(image_b)],
                    root / "Super_Resolution",
                    model_path,
                )

        self.assertEqual(1, load_model.call_count)
        self.assertEqual(str(model_path.resolve()), load_model.call_args.args[0])
        self.assertEqual(2, model.calls)
        self.assertEqual("a_sr.bmp", first[0].name)
        self.assertEqual("b_sr.bmp", second[0].name)

    def test_processes_exact_batches_before_waiting_for_more(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            folder = root / "folder"
            db_path = folder / "tasks.sqlite3"
            repository = TaskRepository(db_path)
            for index in range(65):
                raw_path = folder / f"{index:03d}.raw"
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                raw_path.write_bytes(b"raw")
                repository.upsert_raw_file(
                    folder_name="folder",
                    raw_path=raw_path,
                    sync_job_id="sync",
                    transcode_rename_enabled=False,
                )

            runner = RecordingModelRunner()
            clock = FakeClock()
            service = SuperResolutionService(
                AppConfig(local_root=root),
                runner,
                sleep_func=clock.sleep,
                monotonic_func=clock.monotonic,
            )
            request = SuperResolutionTaskRequest(
                folder_name="folder",
                local_root=root,
                batch_size=60,
                process_partial_batch=False,
                idle_timeout_seconds=0,
            )
            job_id, _ = service.create_job(request)
            service.run_job(job_id, request)

            self.assertEqual(1, len(runner.calls))
            self.assertEqual(60, len(runner.calls[0]))
            self.assertEqual(
                {"done": 60, "pending": 5},
                repository.count_images_by_sr_status("folder"),
            )
            self.assertEqual(
                "completed",
                repository.get_sr_job(job_id)["status"],
            )
            self.assertEqual(
                str(request.model_path),
                repository.get_sr_job(job_id)["model_path"],
            )
            self.assertTrue(runner.loaded)

    def test_model_load_failure_marks_job_failed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository = TaskRepository(root / "folder" / "tasks.sqlite3")
            service = SuperResolutionService(
                AppConfig(local_root=root),
                FailingModelRunner(),
            )
            request = SuperResolutionTaskRequest(
                folder_name="folder",
                local_root=root,
                idle_timeout_seconds=0,
            )
            job_id, _ = service.create_job(request)

            with self.assertRaisesRegex(RuntimeError, "model dependencies are missing"):
                service.run_job(job_id, request)

            job = repository.get_sr_job(job_id)
            assert job is not None
            self.assertEqual("failed", job["status"])
            self.assertEqual("model dependencies are missing", job["error_message"])
            self.assertEqual(0, job["processed_file_count"])

    def test_can_process_partial_batch_when_enabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            folder = root / "folder"
            db_path = folder / "tasks.sqlite3"
            repository = TaskRepository(db_path)
            for index in range(5):
                raw_path = folder / f"{index:03d}.raw"
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                raw_path.write_bytes(b"raw")
                repository.upsert_raw_file(
                    folder_name="folder",
                    raw_path=raw_path,
                    sync_job_id="sync",
                    transcode_rename_enabled=False,
                )

            runner = RecordingModelRunner()
            clock = FakeClock()
            service = SuperResolutionService(
                AppConfig(local_root=root),
                runner,
                sleep_func=clock.sleep,
                monotonic_func=clock.monotonic,
            )
            request = SuperResolutionTaskRequest(
                folder_name="folder",
                local_root=root,
                batch_size=60,
                process_partial_batch=True,
                idle_timeout_seconds=0,
            )
            job_id, _ = service.create_job(request)
            service.run_job(job_id, request)

            self.assertEqual(1, len(runner.calls))
            self.assertEqual(5, len(runner.calls[0]))
            self.assertEqual({"done": 5}, repository.count_images_by_sr_status("folder"))
            self.assertEqual("completed", repository.get_sr_job(job_id)["status"])
            self.assertTrue(runner.loaded)

    def test_waits_for_incremental_images_before_timeout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            folder = root / "folder"
            db_path = folder / "tasks.sqlite3"
            repository = TaskRepository(db_path)
            runner = RecordingModelRunner()
            clock = FakeClock()

            inserted = False

            def insert_after_first_sleep():
                nonlocal inserted
                if inserted:
                    return
                inserted = True
                for index in range(3):
                    raw_path = folder / f"{index:03d}.raw"
                    raw_path.parent.mkdir(parents=True, exist_ok=True)
                    raw_path.write_bytes(b"raw")
                    repository.upsert_raw_file(
                        folder_name="folder",
                        raw_path=raw_path,
                        sync_job_id="sync",
                        transcode_rename_enabled=False,
                    )

            clock.on_sleep = insert_after_first_sleep
            service = SuperResolutionService(
                AppConfig(local_root=root),
                runner,
                sleep_func=clock.sleep,
                monotonic_func=clock.monotonic,
            )
            request = SuperResolutionTaskRequest(
                folder_name="folder",
                local_root=root,
                batch_size=3,
                process_partial_batch=True,
                idle_timeout_seconds=10,
                poll_interval_seconds=10,
            )
            job_id, _ = service.create_job(request)
            service.run_job(job_id, request)

            self.assertEqual(1, len(runner.calls))
            self.assertEqual(3, len(runner.calls[0]))
            self.assertEqual({"done": 3}, repository.count_images_by_sr_status("folder"))
            self.assertEqual("completed", repository.get_sr_job(job_id)["status"])
            self.assertEqual([10], clock.sleep_calls)


if __name__ == "__main__":
    unittest.main()
