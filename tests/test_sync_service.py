from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch
import subprocess

import bootstrap
from fa_server.config import AppConfig, DEFAULT_LOCAL_ROOT, DEFAULT_REMOTE_BASE
from fa_server.services.sync_service import (
    SyncService,
    format_rsync_destination,
    is_missing_remote_folder_error,
    is_msys_rsync,
    required_sync_file_count,
    sync_completion_status,
)
from fa_server.services.raw2bmp_service import RawFileManifest, sync_manifest_complete


class SyncServiceTests(unittest.TestCase):
    def test_default_config_points_to_local_input_data(self):
        config = AppConfig.from_env()

        self.assertEqual(DEFAULT_REMOTE_BASE, config.remote_base)
        self.assertEqual(DEFAULT_LOCAL_ROOT, config.local_root)
        self.assertEqual("rsync", config.rsync_command)
        self.assertEqual((".raw", ".bmp"), config.raw_extensions)
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

    def test_msys_destination_uses_drive_mount_path(self):
        destination = format_rsync_destination(
            Path("D:/Agent/ChatAgent/PWQ/FA_Server/data/rsync_data/bmp_test"),
            "C:/msys64/usr/bin/rsync.exe",
        )

        self.assertTrue(is_msys_rsync("C:/msys64/usr/bin/rsync.exe"))
        self.assertTrue(destination.startswith("/d/"))
        self.assertNotIn("D:", destination)

    @patch("fa_server.services.sync_service.subprocess.run")
    @patch("fa_server.services.sync_service.resolve_rsync")
    def test_run_rsync_once_uses_configured_rsync(self, resolve_rsync, subprocess_run):
        resolve_rsync.return_value = "rsync"
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

        subprocess_run.assert_called_once()
        command = subprocess_run.call_args.args[0]
        self.assertEqual("rsync", command[0])
        self.assertEqual("-av", command[1])
        self.assertNotIn("-avP", command)
        self.assertEqual("rsync://host/data/bmp_test/", command[2])
        self.assertTrue(subprocess_run.call_args.kwargs["capture_output"])

    @patch("fa_server.services.sync_service.subprocess.run")
    @patch("fa_server.services.sync_service.resolve_rsync")
    def test_missing_remote_folder_is_not_a_sync_error(
        self, resolve_rsync, subprocess_run
    ):
        resolve_rsync.return_value = "rsync"
        subprocess_run.return_value = subprocess.CompletedProcess(
            args=["rsync"],
            returncode=23,
            stdout="",
            stderr='change_dir "/missing" failed: No such file or directory',
        )
        service = SyncService(AppConfig())
        request = service.build_request(
            {
                "folder_name": "missing",
                "remote_base": "rsync://host/data",
                "local_root": "D:/tmp/fa",
            }
        )

        service._run_rsync_once(request, Path("D:/tmp/fa/missing"))

        self.assertTrue(is_missing_remote_folder_error(subprocess_run.return_value))

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

    def test_sync_completion_status_marks_incomplete_manifest_partial(self):
        manifest = RawFileManifest(
            path=Path("raw_file_manifest.xml"),
            expected_files=("test_001.raw", "test_002.raw"),
        )

        self.assertEqual("completed", sync_completion_status(manifest, 2))
        self.assertEqual("partially_completed", sync_completion_status(manifest, 1))
        self.assertEqual("completed", sync_completion_status(None, 1))


if __name__ == "__main__":
    unittest.main()
