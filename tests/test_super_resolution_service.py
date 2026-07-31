from __future__ import annotations

import concurrent.futures
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import bootstrap
from fa_server.config import AppConfig
from fa_server.services.super_resolution_service import (
    ModelRunner,
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


class IntegerResultModelRunner:
    def __init__(self):
        self.batch_sizes: list[int] = []

    def ensure_loaded(self, model_path: Path) -> object:
        del model_path
        return object()

    def enhance(
        self,
        paths: list[str],
        output_dir: Path,
        model_path: Path,
    ) -> int:
        del output_dir, model_path
        self.batch_sizes.append(len(paths))
        return len(paths)


class NoneItemResultModelRunner:
    def ensure_loaded(self, model_path: Path) -> object:
        del model_path
        return object()

    def enhance(
        self,
        paths: list[str],
        output_dir: Path,
        model_path: Path,
    ) -> list[None]:
        del output_dir, model_path
        return [None] * len(paths)


class FailOnSecondBatchRunner:
    def __init__(self):
        self.call_count = 0

    def ensure_loaded(self, model_path: Path) -> object:
        del model_path
        return object()

    def enhance(
        self,
        paths: list[str],
        output_dir: Path,
        model_path: Path,
    ) -> list[Path] | int:
        del model_path
        self.call_count += 1
        if self.call_count == 2:
            return len(paths) - 1

        output_dir.mkdir(parents=True, exist_ok=True)
        outputs = []
        for path in paths:
            source = Path(path)
            output = output_dir / f"{source.stem}_sr{source.suffix}"
            output.write_bytes(b"sr")
            outputs.append(output)
        return outputs


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


class StopWaiting(BaseException):
    pass


class SuperResolutionServiceTests(unittest.TestCase):
    @staticmethod
    def add_pending_images(
        repository: TaskRepository,
        folder: Path,
        count: int,
    ) -> None:
        for index in range(count):
            image_path = folder / f"{index:05d}.bmp"
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image_path.write_bytes(b"bmp")
            repository.upsert_raw_file(
                folder_name=folder.name,
                raw_path=image_path,
                sync_job_id="sync",
                transcode_rename_enabled=False,
            )

    @staticmethod
    def write_manifest(folder: Path, count: int) -> None:
        files = "".join(
            f'<file name="{index:05d}.raw" />'
            for index in range(count)
        )
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "raw_file_manifest.xml").write_text(
            f"<manifest>{files}</manifest>",
            encoding="utf-8",
        )

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
            self.assertFalse(hasattr(request, "idle_timeout_seconds"))

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
            folder = root / "folder"
            repository = TaskRepository(folder / "tasks.sqlite3")
            self.assertFalse(
                super_resolution_task_table_complete(repository, "folder", folder)
            )
            image_path = folder / "image.bmp"
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
                super_resolution_task_table_complete(repository, "folder", folder)
            )
            repository.mark_sr_done(image_id, root / "folder" / "Super_Resolution" / "image_sr.bmp")
            self.assertFalse(
                super_resolution_task_table_complete(repository, "folder", folder)
            )

            (folder / "raw_file_manifest.xml").write_text(
                '<manifest><file name="image.raw" /></manifest>',
                encoding="utf-8",
            )
            self.assertTrue(
                super_resolution_task_table_complete(repository, "folder", folder)
            )

    def test_super_resolution_task_table_waits_for_active_sync_job(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            folder = root / "folder"
            repository = TaskRepository(folder / "tasks.sqlite3")
            repository.create_sync_job(
                job_id="sync",
                folder_name="folder",
                remote_url="rsync://host/data/folder/",
                local_dir=folder,
                transcode_rename_enabled=False,
                poll_interval_seconds=30,
            )
            self.write_manifest(folder, 1)
            image_path = folder / "image.bmp"
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
                super_resolution_task_table_complete(repository, "folder", folder)
            )
            repository.update_sync_job("sync", status="completed")
            self.assertTrue(
                super_resolution_task_table_complete(repository, "folder", folder)
            )

    def test_blocked_images_do_not_make_task_table_complete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            folder = root / "folder"
            repository = TaskRepository(folder / "tasks.sqlite3")
            raw_path = folder / "image.raw"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(b"raw")
            _, image_id = repository.upsert_raw_file(
                folder_name="folder",
                raw_path=raw_path,
                sync_job_id="sync",
                transcode_rename_enabled=True,
            )
            repository.mark_conversion_failed(image_id, "conversion failed")
            self.write_manifest(folder, 1)

            self.assertFalse(
                super_resolution_task_table_complete(repository, "folder", folder)
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

    def test_model_runner_serializes_inference_to_bound_memory(self):
        runner = ModelRunner()
        active_calls = 0
        max_active_calls = 0
        state_lock = threading.Lock()
        start_barrier = threading.Barrier(2)

        def fake_batch_process(**kwargs):
            nonlocal active_calls, max_active_calls
            with state_lock:
                active_calls += 1
                max_active_calls = max(max_active_calls, active_calls)
            time.sleep(0.05)
            with state_lock:
                active_calls -= 1
            return [Path(path) for path in kwargs["input_paths"]]

        def run_inference(image_path: Path, model_path: Path) -> None:
            start_barrier.wait()
            runner.enhance(
                [str(image_path)],
                image_path.parent / "Super_Resolution",
                model_path,
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            model_path = root / "model.pth"
            first = root / "first.bmp"
            second = root / "second.bmp"
            model_path.write_bytes(b"model")
            first.write_bytes(b"bmp")
            second.write_bytes(b"bmp")

            with (
                patch(
                    "fa_server.services.super_resolution_service.load_ep5_model",
                    return_value=(object(), object()),
                ),
                patch(
                    "fa_server.services.super_resolution_service.batch_process_ep5",
                    side_effect=fake_batch_process,
                ),
                concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor,
            ):
                futures = [
                    executor.submit(
                        run_inference,
                        image_path,
                        model_path,
                    )
                    for image_path in (first, second)
                ]
                for future in futures:
                    future.result()

        self.assertEqual(1, max_active_calls)

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
            )
            self.write_manifest(folder, 65)

            def stop_waiting() -> None:
                raise StopWaiting()

            clock.on_sleep = stop_waiting
            request = SuperResolutionTaskRequest(
                folder_name="folder",
                local_root=root,
                batch_size=60,
                process_partial_batch=False,
            )
            job_id, _ = service.create_job(request)
            with self.assertRaises(StopWaiting):
                service.run_job(job_id, request)

            self.assertEqual(1, len(runner.calls))
            self.assertEqual(60, len(runner.calls[0]))
            self.assertEqual(
                {"done": 60, "pending": 5},
                repository.count_images_by_sr_status("folder"),
            )
            self.assertEqual(
                "running",
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
            )
            job_id, _ = service.create_job(request)

            with self.assertRaisesRegex(RuntimeError, "model dependencies are missing"):
                service.run_job(job_id, request)

            job = repository.get_sr_job(job_id)
            assert job is not None
            self.assertEqual("failed", job["status"])
            self.assertEqual("model dependencies are missing", job["error_message"])
            self.assertEqual(0, job["processed_file_count"])

    def test_integer_batch_result_marks_input_tasks_done(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            folder = root / "folder"
            repository = TaskRepository(folder / "tasks.sqlite3")
            self.add_pending_images(repository, folder, 301)
            self.write_manifest(folder, 301)
            runner = IntegerResultModelRunner()
            service = SuperResolutionService(
                AppConfig(local_root=root),
                runner,
            )
            request = SuperResolutionTaskRequest(
                folder_name="folder",
                local_root=root,
                batch_size=150,
                process_partial_batch=True,
            )
            job_id, _ = service.create_job(request)

            service.run_job(job_id, request)

            self.assertEqual([150, 150, 1], runner.batch_sizes)
            self.assertEqual(
                {"done": 301},
                repository.count_images_by_sr_status("folder"),
            )
            with repository.connect() as connection:
                missing_output_paths = connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM image_tasks
                    WHERE sr_status = 'done' AND sr_output_path IS NULL
                    """
                ).fetchone()[0]
            self.assertEqual(301, missing_output_paths)
            job = repository.get_sr_job(job_id)
            assert job is not None
            self.assertEqual("completed", job["status"])
            self.assertEqual(301, job["processed_file_count"])

    def test_none_result_items_mark_input_tasks_done(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            folder = root / "folder"
            repository = TaskRepository(folder / "tasks.sqlite3")
            self.add_pending_images(repository, folder, 3)
            self.write_manifest(folder, 3)
            service = SuperResolutionService(
                AppConfig(local_root=root),
                NoneItemResultModelRunner(),
            )
            request = SuperResolutionTaskRequest(
                folder_name="folder",
                local_root=root,
                batch_size=3,
                process_partial_batch=True,
            )
            job_id, _ = service.create_job(request)

            service.run_job(job_id, request)

            self.assertEqual(
                {"done": 3},
                repository.count_images_by_sr_status("folder"),
            )
            with repository.connect() as connection:
                output_path_count = connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM image_tasks
                    WHERE sr_output_path IS NOT NULL
                    """
                ).fetchone()[0]
            self.assertEqual(0, output_path_count)
            job = repository.get_sr_job(job_id)
            assert job is not None
            self.assertEqual("completed", job["status"])
            self.assertEqual(3, job["processed_file_count"])

    def test_second_batch_failure_does_not_leave_future_batches_processing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            folder = root / "folder"
            repository = TaskRepository(folder / "tasks.sqlite3")
            self.add_pending_images(repository, folder, 304)
            runner = FailOnSecondBatchRunner()
            service = SuperResolutionService(
                AppConfig(local_root=root),
                runner,
            )
            request = SuperResolutionTaskRequest(
                folder_name="folder",
                local_root=root,
                batch_size=150,
                process_partial_batch=True,
            )
            job_id, _ = service.create_job(request)

            with self.assertRaisesRegex(
                RuntimeError,
                "processed 149 of 150",
            ):
                service.run_job(job_id, request)

            self.assertEqual(
                {"done": 150, "failed": 150, "pending": 4},
                repository.count_images_by_sr_status("folder"),
            )
            job = repository.get_sr_job(job_id)
            assert job is not None
            self.assertEqual("failed", job["status"])
            self.assertEqual(150, job["processed_file_count"])

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
            self.write_manifest(folder, 5)
            service = SuperResolutionService(
                AppConfig(local_root=root),
                runner,
                sleep_func=clock.sleep,
            )
            request = SuperResolutionTaskRequest(
                folder_name="folder",
                local_root=root,
                batch_size=60,
                process_partial_batch=True,
            )
            job_id, _ = service.create_job(request)
            service.run_job(job_id, request)

            self.assertEqual(1, len(runner.calls))
            self.assertEqual(5, len(runner.calls[0]))
            self.assertEqual({"done": 5}, repository.count_images_by_sr_status("folder"))
            self.assertEqual("completed", repository.get_sr_job(job_id)["status"])
            self.assertTrue(runner.loaded)

    def test_waits_for_incremental_images_until_manifest_is_complete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            folder = root / "folder"
            db_path = folder / "tasks.sqlite3"
            repository = TaskRepository(db_path)
            runner = RecordingModelRunner()
            clock = FakeClock()
            self.write_manifest(folder, 3)

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
            )
            request = SuperResolutionTaskRequest(
                folder_name="folder",
                local_root=root,
                batch_size=3,
                process_partial_batch=True,
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
