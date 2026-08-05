from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import subprocess

import bootstrap
from fa_server.config import AppConfig, DEFAULT_LOCAL_ROOT, DEFAULT_REMOTE_BASE
from fa_server.services.sync_service import (
    SyncService,
    TaskStallTimeout,
    format_rsync_destination,
    is_msys_rsync,
    required_sync_file_count,
)
from fa_server.services.raw2bmp_service import RawFileManifest, sync_manifest_complete
from fa_server.storage import TaskRepository


class SyncServiceTests(unittest.TestCase):
    def test_default_config_points_to_local_input_data(self):
        config = AppConfig.from_env()

        self.assertEqual(DEFAULT_REMOTE_BASE, config.remote_base)
        self.assertEqual(DEFAULT_LOCAL_ROOT, config.local_root)
        self.assertEqual("rsync", config.rsync_command)
        self.assertEqual((".raw", ".bmp"), config.raw_extensions)
        self.assertFalse(hasattr(config, "idle_timeout_seconds"))
        self.assertEqual(3600, config.task_stall_timeout_seconds)
        self.assertTrue(config.purge_enabled)
        self.assertEqual(10, config.purge_max_folders)
        self.assertEqual(86400, config.purge_interval_seconds)

    def test_build_request_accepts_rsync_command(self):
        service = SyncService(AppConfig())

        request = service.build_request(
            {
                "folder_name": "bmp_test",
                "rsync": "C:/msys64/usr/bin/rsync.exe",
            }
        )

        self.assertEqual("bmp_test", request.folder_name)
        self.assertEqual("C:/msys64/usr/bin/rsync.exe", request.rsync_command)
        self.assertFalse(hasattr(request, "idle_timeout_seconds"))

    def test_msys_destination_uses_drive_mount_path(self):
        destination = format_rsync_destination(
            Path("D:/Agent/ChatAgent/PWQ/FA_Server/data/rsync_data/bmp_test"),
            "C:/msys64/usr/bin/rsync.exe",
        )

        self.assertTrue(is_msys_rsync("C:/msys64/usr/bin/rsync.exe"))
        self.assertTrue(destination.startswith("/d/"))
        self.assertNotIn("D:", destination)

    @patch("fa_server.services.sync_service.subprocess.Popen")
    @patch("fa_server.services.sync_service.resolve_rsync")
    def test_run_rsync_once_uses_configured_rsync(self, resolve_rsync, popen):
        resolve_rsync.return_value = "rsync"
        process = MagicMock()
        process.poll.return_value = 0
        process.wait.return_value = 0
        popen.return_value = process
        service = SyncService(AppConfig())
        request = service.build_request(
            {
                "folder_name": "bmp_test",
                "remote_base": "rsync://host/data",
                "local_root": "D:/tmp/fa",
                "rsync": "rsync",
            }
        )

        service._run_rsync_once(request, Path("D:/tmp/fa/bmp_test"))

        popen.assert_called_once()
        command = popen.call_args.args[0]
        self.assertEqual("rsync", command[0])
        self.assertEqual("-a", command[1])
        self.assertNotIn("-avP", command)
        self.assertEqual("rsync://host/data/bmp_test/", command[2])
        self.assertIs(subprocess.DEVNULL, popen.call_args.kwargs["stdout"])
        self.assertIsNot(subprocess.PIPE, popen.call_args.kwargs["stderr"])

    @patch("fa_server.services.sync_service.time.sleep")
    @patch("fa_server.services.sync_service.subprocess.Popen")
    @patch("fa_server.services.sync_service.resolve_rsync")
    def test_run_rsync_once_scans_while_process_is_active(
        self,
        resolve_rsync,
        popen,
        sleep,
    ):
        resolve_rsync.return_value = "rsync"
        process = MagicMock()
        process.poll.side_effect = [None, None, 0]
        process.wait.return_value = 0
        popen.return_value = process
        callback = MagicMock()
        service = SyncService(AppConfig())
        request = service.build_request(
            {
                "folder_name": "production_folder",
                "remote_base": "rsync://host/data",
                "local_root": "D:/tmp/fa",
                "rsync": "rsync",
            }
        )

        service._run_rsync_once(
            request,
            Path("D:/tmp/fa/production_folder"),
            progress_callback=callback,
        )

        callback.assert_called_once()
        sleep.assert_called_once()

    @patch("fa_server.services.sync_service.subprocess.Popen")
    @patch("fa_server.services.sync_service.resolve_rsync")
    def test_stall_timeout_stops_active_rsync_process(
        self,
        resolve_rsync,
        popen,
    ):
        resolve_rsync.return_value = "rsync"
        process = MagicMock()
        process.poll.side_effect = [None, None]
        process.wait.return_value = -1
        popen.return_value = process
        service = SyncService(AppConfig())
        request = service.build_request(
            {
                "folder_name": "production_folder",
                "remote_base": "rsync://host/data",
                "local_root": "D:/tmp/fa",
                "rsync": "rsync",
            }
        )

        with self.assertRaises(TaskStallTimeout):
            service._run_rsync_once(
                request,
                Path("D:/tmp/fa/production_folder"),
                progress_callback=MagicMock(
                    side_effect=TaskStallTimeout("stalled")
                ),
            )

        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with()

    @patch("fa_server.services.sync_service.subprocess.run")
    @patch("fa_server.services.sync_service.resolve_rsync")
    def test_prefetch_xml_configuration_requests_manifest_before_bulk_sync(
        self,
        resolve_rsync,
        run,
    ):
        resolve_rsync.return_value = "rsync"
        service = SyncService(AppConfig())
        request = service.build_request(
            {
                "folder_name": "production_folder",
                "remote_base": "rsync://host/data",
                "rsync": "rsync",
                "rsync_timeout_seconds": 3600,
            }
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            local_dir = Path(temp_dir) / "production_folder"
            local_dir.mkdir()

            def create_manifest(*args, **kwargs):
                del args, kwargs
                (local_dir / "raw_file_manifest.xml").write_text(
                    "<manifest />",
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="",
                    stderr="",
                )

            run.side_effect = create_manifest
            service._prefetch_xml_configuration("job-1", request, local_dir)

        command = run.call_args.args[0]
        self.assertEqual("rsync", command[0])
        self.assertEqual("-a", command[1])
        self.assertEqual(
            "rsync://host/data/production_folder/raw_file_manifest.xml",
            command[2],
        )
        self.assertEqual(60.0, run.call_args.kwargs["timeout"])

    @patch("fa_server.services.sync_service.subprocess.Popen")
    @patch("fa_server.services.sync_service.resolve_rsync")
    def test_missing_remote_folder_is_not_a_sync_error(
        self, resolve_rsync, popen
    ):
        resolve_rsync.return_value = "rsync"

        def start_process(command, **kwargs):
            del command
            kwargs["stderr"].write(
                b'change_dir "/missing" failed: No such file or directory'
            )
            process = MagicMock()
            process.poll.return_value = 23
            process.wait.return_value = 23
            return process

        popen.side_effect = start_process
        service = SyncService(AppConfig())
        request = service.build_request(
            {
                "folder_name": "missing",
                "remote_base": "rsync://host/data",
                "local_root": "D:/tmp/fa",
            }
        )

        service._run_rsync_once(request, Path("D:/tmp/fa/missing"))

    def test_sync_manifest_complete_checks_final_t_bmp_names(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = RawFileManifest(
                path=root / "raw_file_manifest.xml",
                expected_files=("test_001.raw", "test_002.raw"),
            )
            (root / "test_001T.bmp").write_bytes(b"bmp")

            self.assertFalse(sync_manifest_complete(root, manifest))

            (root / "test_002T.bmp").write_bytes(b"bmp")
            self.assertTrue(sync_manifest_complete(root, manifest))

    def test_sync_manifest_complete_checks_source_names_when_conversion_disabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = RawFileManifest(
                path=root / "raw_file_manifest.xml",
                expected_files=("test_001.raw", "test_002.raw"),
            )
            (root / "test_001.raw").write_bytes(b"raw")

            self.assertFalse(
                sync_manifest_complete(
                    root,
                    manifest,
                    transcode_rename_enabled=False,
                )
            )

            (root / "test_002.raw").write_bytes(b"raw")
            self.assertTrue(
                sync_manifest_complete(
                    root,
                    manifest,
                    transcode_rename_enabled=False,
                )
            )

    def test_required_sync_file_count_reads_manifest(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "raw_file_manifest.xml").write_text(
                '<manifest><file name="test_001.raw" />'
                '<file name="test_002.raw" /></manifest>',
                encoding="utf-8",
            )

            self.assertEqual(2, required_sync_file_count(root))

    def test_resynced_raw_is_removed_using_recorded_final_bmp_path(self):
        class FakeRaw2BmpSession:
            def __init__(self, final_path: Path):
                self.final_path = final_path
                self.call_count = 0

            def transcode_and_rename_raw(self, raw_path: Path) -> Path:
                del raw_path
                self.call_count += 1
                self.final_path.write_bytes(b"bmp")
                return self.final_path

        with tempfile.TemporaryDirectory() as temp_dir:
            local_root = Path(temp_dir)
            local_dir = local_root / "production_folder"
            local_dir.mkdir()
            (local_dir / "raw_file_manifest.xml").write_text(
                '<manifest><file name="source.raw" /></manifest>',
                encoding="utf-8",
            )
            raw_path = local_dir / "source.raw"
            raw_path.write_bytes(b"raw")
            original_mtime_ns = raw_path.stat().st_mtime_ns
            final_path = local_dir / "coordinate_123_456.bmp"

            service = SyncService(AppConfig(local_root=local_root))
            request = service.build_request(
                {
                    "folder_name": "production_folder",
                    "local_root": str(local_root),
                    "raw_extensions": [".raw"],
                    "enable_transcode_rename": True,
                }
            )
            repository = TaskRepository(local_dir / "tasks.sqlite3")
            session = FakeRaw2BmpSession(final_path)

            service._register_new_files(
                "job-1",
                request,
                local_dir,
                repository,
                session,
            )
            self.assertFalse(raw_path.exists())
            self.assertTrue(final_path.exists())

            raw_path.write_bytes(b"raw")
            os.utime(raw_path, ns=(original_mtime_ns, original_mtime_ns))
            service._register_new_files(
                "job-2",
                request,
                local_dir,
                repository,
                session,
            )

            self.assertEqual(1, session.call_count)
            self.assertFalse(raw_path.exists())

    def test_run_job_processes_completed_files_while_rsync_is_running(self):
        class FakeRaw2BmpSession:
            def transcode_and_rename_raw(self, raw_path: Path) -> Path:
                final_path = raw_path.with_name(f"{raw_path.stem}T.bmp")
                final_path.write_bytes(b"bmp")
                return final_path

            def finish(self) -> None:
                return None

        class FakeRaw2BmpService:
            def create_task_session(self, folder: Path) -> FakeRaw2BmpSession:
                del folder
                return FakeRaw2BmpSession()

        class ProgressAwareSyncService(SyncService):
            def _prefetch_xml_configuration(self, job_id, request, local_dir):
                del job_id, request, local_dir

            def _run_rsync_once(
                self,
                request,
                local_dir,
                progress_callback=None,
            ):
                del request
                (local_dir / "raw_file_manifest.xml").write_text(
                    '<manifest><file name="source.raw" /></manifest>',
                    encoding="utf-8",
                )
                (local_dir / "source.raw").write_bytes(b"raw")
                if progress_callback is None:
                    raise AssertionError(
                        "sync processing callback was not registered"
                    )

                progress_callback()
                if not (local_dir / "sourceT.bmp").is_file():
                    raise AssertionError(
                        "RAW conversion did not run before rsync completed"
                    )
                if not (local_dir / "source.raw").is_file():
                    raise AssertionError(
                        "RAW source was removed while rsync was still active"
                    )

        with tempfile.TemporaryDirectory() as temp_dir:
            local_root = Path(temp_dir)
            service = ProgressAwareSyncService(
                AppConfig(local_root=local_root),
                FakeRaw2BmpService(),
            )
            request = service.build_request(
                {
                    "folder_name": "production_folder",
                    "local_root": str(local_root),
                    "enable_transcode_rename": True,
                }
            )
            job_id, _ = service.create_job(request)

            service.run_job(job_id, request)

            repository = TaskRepository(
                local_root / "production_folder" / "tasks.sqlite3"
            )
            job = repository.get_sync_job(job_id)
            assert job is not None
            self.assertEqual("completed", job["status"])
            self.assertEqual(1, job["synced_file_count"])
            self.assertFalse(
                (local_root / "production_folder" / "source.raw").exists()
            )

    def test_run_job_prefetches_manifest_before_bulk_rsync_processing(self):
        class FakeRaw2BmpSession:
            def transcode_and_rename_raw(self, raw_path: Path) -> Path:
                final_path = raw_path.with_name(f"{raw_path.stem}T.bmp")
                final_path.write_bytes(b"bmp")
                return final_path

            def finish(self) -> None:
                return None

        class FakeRaw2BmpService:
            def create_task_session(self, folder: Path) -> FakeRaw2BmpSession:
                del folder
                return FakeRaw2BmpSession()

        class ManifestFirstSyncService(SyncService):
            def _prefetch_xml_configuration(self, job_id, request, local_dir):
                del job_id, request
                (local_dir / "raw_file_manifest.xml").write_text(
                    '<manifest><file name="source.raw" /></manifest>',
                    encoding="utf-8",
                )

            def _run_rsync_once(
                self,
                request,
                local_dir,
                progress_callback=None,
            ):
                del request
                if not (local_dir / "raw_file_manifest.xml").is_file():
                    raise AssertionError(
                        "bulk rsync started before manifest was prefetched"
                    )
                (local_dir / "source.raw").write_bytes(b"raw")
                assert progress_callback is not None
                progress_callback()
                if not (local_dir / "sourceT.bmp").is_file():
                    raise AssertionError(
                        "RAW conversion did not start during bulk rsync"
                    )

        with tempfile.TemporaryDirectory() as temp_dir:
            local_root = Path(temp_dir)
            service = ManifestFirstSyncService(
                AppConfig(local_root=local_root),
                FakeRaw2BmpService(),
            )
            request = service.build_request(
                {
                    "folder_name": "production_folder",
                    "local_root": str(local_root),
                    "enable_transcode_rename": True,
                }
            )
            job_id, _ = service.create_job(request)

            service.run_job(job_id, request)

            repository = TaskRepository(
                local_root / "production_folder" / "tasks.sqlite3"
            )
            self.assertEqual(
                {"pending": 1},
                repository.count_images_by_sr_status("production_folder"),
            )

    def test_run_job_waits_for_manifest_without_idle_timeout(self):
        class FakeRaw2BmpSession:
            def transcode_and_rename_raw(self, raw_path: Path) -> Path:
                raise AssertionError(f"conversion should be disabled: {raw_path}")

            def finish(self) -> None:
                return None

        class FakeRaw2BmpService:
            def create_task_session(self, folder: Path) -> FakeRaw2BmpSession:
                del folder
                return FakeRaw2BmpSession()

        class DelayedManifestSyncService(SyncService):
            def __init__(self, config, raw2bmp_service):
                super().__init__(config, raw2bmp_service)
                self.sync_calls = 0

            def _prefetch_xml_configuration(self, job_id, request, local_dir):
                del job_id, request, local_dir

            def _run_rsync_once(
                self,
                request,
                local_dir,
                progress_callback=None,
            ):
                del request
                self.sync_calls += 1
                if self.sync_calls == 1:
                    return
                (local_dir / "raw_file_manifest.xml").write_text(
                    '<manifest><file name="source.raw" /></manifest>',
                    encoding="utf-8",
                )
                (local_dir / "source.raw").write_bytes(b"raw")
                assert progress_callback is not None
                progress_callback()

        with tempfile.TemporaryDirectory() as temp_dir:
            local_root = Path(temp_dir)
            service = DelayedManifestSyncService(
                AppConfig(local_root=local_root),
                FakeRaw2BmpService(),
            )
            request = service.build_request(
                {
                    "folder_name": "production_folder",
                    "local_root": str(local_root),
                    "enable_transcode_rename": False,
                    "poll_interval_seconds": 1,
                }
            )
            job_id, _ = service.create_job(request)

            with patch("fa_server.services.sync_service.time.sleep") as sleep:
                service.run_job(job_id, request)

            repository = TaskRepository(
                local_root / "production_folder" / "tasks.sqlite3"
            )
            self.assertEqual(2, service.sync_calls)
            sleep.assert_called_once_with(1)
            self.assertEqual("completed", repository.get_sync_job(job_id)["status"])

    def test_run_job_ends_as_timed_out_after_internal_stall_timeout(self):
        class FakeRaw2BmpSession:
            def finish(self) -> None:
                return None

        class FakeRaw2BmpService:
            def create_task_session(self, folder: Path) -> FakeRaw2BmpSession:
                del folder
                return FakeRaw2BmpSession()

        class EmptySyncService(SyncService):
            def _prefetch_xml_configuration(self, job_id, request, local_dir):
                del job_id, request, local_dir

            def _run_rsync_once(
                self,
                request,
                local_dir,
                progress_callback=None,
            ):
                del request, local_dir, progress_callback

        with tempfile.TemporaryDirectory() as temp_dir:
            local_root = Path(temp_dir)
            service = EmptySyncService(
                AppConfig(
                    local_root=local_root,
                    task_stall_timeout_seconds=10,
                ),
                FakeRaw2BmpService(),
            )
            request = service.build_request(
                {
                    "folder_name": "production_folder",
                    "local_root": str(local_root),
                    "enable_transcode_rename": False,
                }
            )
            job_id, _ = service.create_job(request)

            with patch(
                "fa_server.services.sync_service.time.monotonic",
                side_effect=[0.0, 10.0],
            ):
                service.run_job(job_id, request)

            repository = TaskRepository(
                local_root / "production_folder" / "tasks.sqlite3"
            )
            job = repository.get_sync_job(job_id)
            assert job is not None
            self.assertEqual("timed_out", job["status"])
            self.assertIsNotNone(job["finished_at"])
            self.assertIn("XML target", job["error_message"])

    def test_manifest_completion_takes_precedence_over_stall_timeout(self):
        class FakeRaw2BmpSession:
            def finish(self) -> None:
                return None

        class FakeRaw2BmpService:
            def create_task_session(self, folder: Path) -> FakeRaw2BmpSession:
                del folder
                return FakeRaw2BmpSession()

        class EmptySyncService(SyncService):
            def _prefetch_xml_configuration(self, job_id, request, local_dir):
                del job_id, request, local_dir

            def _run_rsync_once(
                self,
                request,
                local_dir,
                progress_callback=None,
            ):
                del request, local_dir, progress_callback

        with tempfile.TemporaryDirectory() as temp_dir:
            local_root = Path(temp_dir)
            local_dir = local_root / "production_folder"
            local_dir.mkdir()
            raw_path = local_dir / "source.raw"
            raw_path.write_bytes(b"raw")
            (local_dir / "raw_file_manifest.xml").write_text(
                '<manifest><file name="source.raw" /></manifest>',
                encoding="utf-8",
            )
            service = EmptySyncService(
                AppConfig(
                    local_root=local_root,
                    task_stall_timeout_seconds=10,
                ),
                FakeRaw2BmpService(),
            )
            request = service.build_request(
                {
                    "folder_name": "production_folder",
                    "local_root": str(local_root),
                    "enable_transcode_rename": False,
                }
            )
            job_id, _ = service.create_job(request)
            repository = TaskRepository(local_dir / "tasks.sqlite3")
            repository.upsert_raw_file(
                folder_name="production_folder",
                raw_path=raw_path,
                sync_job_id=job_id,
                transcode_rename_enabled=False,
            )

            with patch(
                "fa_server.services.sync_service.time.monotonic",
                side_effect=[0.0, 10.0, 10.0],
            ):
                service.run_job(job_id, request)

            self.assertEqual("completed", repository.get_sync_job(job_id)["status"])

    def test_active_sync_scan_does_not_reprocess_seen_raw_file(self):
        class FakeRaw2BmpSession:
            def __init__(self, final_path: Path):
                self.final_path = final_path
                self.call_count = 0

            def transcode_and_rename_raw(self, raw_path: Path) -> Path:
                del raw_path
                self.call_count += 1
                self.final_path.write_bytes(b"bmp")
                return self.final_path

        with tempfile.TemporaryDirectory() as temp_dir:
            local_root = Path(temp_dir)
            local_dir = local_root / "production_folder"
            local_dir.mkdir()
            (local_dir / "raw_file_manifest.xml").write_text(
                '<manifest><file name="source.raw" /></manifest>',
                encoding="utf-8",
            )
            raw_path = local_dir / "source.raw"
            raw_path.write_bytes(b"raw")
            final_path = local_dir / "sourceT.bmp"
            repository = TaskRepository(local_dir / "tasks.sqlite3")
            service = SyncService(AppConfig(local_root=local_root))
            request = service.build_request(
                {
                    "folder_name": "production_folder",
                    "local_root": str(local_root),
                    "enable_transcode_rename": True,
                }
            )
            session = FakeRaw2BmpSession(final_path)
            seen_signatures: set[tuple[str, int, int]] = set()

            first_count = service._register_new_files(
                "job-1",
                request,
                local_dir,
                repository,
                session,
                cleanup_sources=False,
                seen_file_signatures=seen_signatures,
            )
            second_count = service._register_new_files(
                "job-1",
                request,
                local_dir,
                repository,
                session,
                cleanup_sources=False,
                seen_file_signatures=seen_signatures,
            )

            self.assertEqual(1, first_count)
            self.assertEqual(0, second_count)
            self.assertEqual(1, session.call_count)
            self.assertTrue(raw_path.exists())


if __name__ == "__main__":
    unittest.main()
