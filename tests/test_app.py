from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bootstrap
from fa_server.app import create_app, json_payload
from fa_server.config import AppConfig
from fa_server.services.super_resolution_service import SuperResolutionService
from fa_server.services.sync_service import SyncService
from fa_server.storage import TaskRepository
from fa_server.utils.paths import normalize_path_text, path_from_user_input


def auth_headers(api_key: str = "dev-api-key") -> dict[str, str]:
    return {"X-API-Key": api_key}


class AppTests(unittest.TestCase):
    def test_path_normalization_accepts_windows_and_linux_paths(self):
        self.assertEqual("D:/Agent/data", normalize_path_text(r"D:\Agent\data"))
        self.assertEqual("/home/anchor/data", normalize_path_text("/home/anchor/data"))
        self.assertEqual(Path("D:/Agent/data"), path_from_user_input(r"D:\Agent\data"))

    def test_sync_request_normalizes_local_root_and_rsync_path(self):
        service = SyncService(AppConfig())

        sync_request = service.build_request(
            {
                "folder_name": "raw_test",
                "local_root": r"D:\Agent\ChatAgent\PWQ\FA_Server\data\rsync_data",
                "rsync": r"C:\msys64\usr\bin\rsync.exe",
            }
        )

        self.assertEqual(
            Path("D:/Agent/ChatAgent/PWQ/FA_Server/data/rsync_data"),
            sync_request.local_root,
        )
        self.assertEqual("C:/msys64/usr/bin/rsync.exe", sync_request.rsync_command)

    def test_super_resolution_request_normalizes_local_root(self):
        service = SuperResolutionService(AppConfig())

        sr_request = service.build_request(
            {
                "folder_name": "raw_test",
                "local_root": r"D:\Agent\ChatAgent\PWQ\FA_Server\data\rsync_data",
            }
        )

        self.assertEqual(
            Path("D:/Agent/ChatAgent/PWQ/FA_Server/data/rsync_data"),
            sr_request.local_root,
        )

    def test_sync_create_accepts_folder_name_in_route(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = create_app(
                AppConfig(local_root=Path(temp_dir), rsync_command="missing")
            )
            client = app.test_client()

            with patch("fa_server.worker.BackgroundTaskManager.submit_sync"):
                response = client.post(
                    "/api/v1/sync/tasks/raw_test",
                    headers=auth_headers(),
                    json={"poll_interval_seconds": 1},
                )

            self.assertEqual(202, response.status_code)
            self.assertEqual("raw_test", response.json["folder_name"])

    def test_sync_status_includes_required_file_count(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            local_root = Path(temp_dir)
            folder = local_root / "raw_test"
            folder.mkdir()
            (folder / "raw_file_manifest.xml").write_text(
                '<manifest><file name="test_001.raw" />'
                '<file name="test_002.raw" /></manifest>',
                encoding="utf-8",
            )
            repository = TaskRepository(folder / "tasks.sqlite3")
            repository.create_sync_job(
                job_id="sync-job",
                folder_name="raw_test",
                remote_url="rsync://host/data/raw_test/",
                local_dir=folder,
                transcode_rename_enabled=True,
                poll_interval_seconds=30,
            )
            repository.update_sync_job(
                "sync-job",
                status="running",
                synced_file_count=1,
            )
            app = create_app(AppConfig(local_root=local_root))
            client = app.test_client()

            response = client.get(
                "/api/v1/sync/jobs/sync-job?folder_name=raw_test",
                headers=auth_headers(),
            )

            self.assertEqual(200, response.status_code)
            self.assertEqual(2, response.json["required_file_count"])
            self.assertEqual(1, response.json["synced_file_count"])
            self.assertEqual("running", response.json["status"])
            self.assertNotIn("idle_timeout_seconds", response.json)

    def test_api_requires_basic_auth(self):
        app = create_app(AppConfig())
        client = app.test_client()

        response = client.post("/api/v1/super-resolution/tasks", json={})

        self.assertEqual(401, response.status_code)
        self.assertEqual("valid API key required", response.json["error"])

    def test_api_accepts_bearer_api_key(self):
        app = create_app(AppConfig())
        client = app.test_client()

        response = client.post(
            "/api/v1/super-resolution/tasks",
            headers={"Authorization": "Bearer dev-api-key"},
            json={},
        )

        self.assertEqual(400, response.status_code)
        self.assertEqual("folder_name is required", response.json["error"])

    def test_parallel_super_resolution_task_returns_conflict(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = create_app(AppConfig(local_root=Path(temp_dir)))
            client = app.test_client()

            with patch(
                "fa_server.worker.BackgroundTaskManager.submit_super_resolution"
            ):
                first = client.post(
                    "/api/v1/super-resolution/tasks",
                    headers=auth_headers(),
                    json={"folder_name": "raw_test"},
                )
                second = client.post(
                    "/api/v1/super-resolution/tasks",
                    headers=auth_headers(),
                    json={"folder_name": "raw_test"},
                )

            self.assertEqual(202, first.status_code)
            self.assertEqual(409, second.status_code)
            self.assertEqual(
                first.json["job_id"],
                second.json["existing_job_id"],
            )

    def test_legacy_sync_create_without_route_folder_is_removed(self):
        app = create_app(AppConfig())
        client = app.test_client()

        response = client.post(
            "/api/v1/sync/tasks",
            headers=auth_headers(),
            json={"folder_name": "raw_test"},
        )

        self.assertEqual(404, response.status_code)

    def test_local_root_with_single_windows_backslashes_is_accepted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = create_app(AppConfig(local_root=Path(temp_dir)))
            local_root = str(Path(temp_dir) / "data")
            model_path = str(Path(temp_dir) / "models" / "best_model.pth")

            with app.test_request_context(
                "/api/v1/super-resolution/tasks",
                method="POST",
                data=(
                    '{"folder_name":"raw_test",'
                    f'"local_root":"{local_root}",'
                    f'"model_path":"{model_path}",'
                    '"batch_size":3}'
                ),
                content_type="application/json",
            ):
                payload = json_payload()

            self.assertEqual("raw_test", payload["folder_name"])
            self.assertEqual(normalize_path_text(local_root), payload["local_root"])
            self.assertEqual(normalize_path_text(model_path), payload["model_path"])

    def test_invalid_json_reports_json_error(self):
        app = create_app(AppConfig())
        client = app.test_client()

        response = client.post(
            "/api/v1/super-resolution/tasks",
            headers=auth_headers(),
            data='{"folder_name":"raw_test","batch_size":}',
            content_type="application/json",
        )

        self.assertEqual(400, response.status_code)
        self.assertIn("invalid JSON body", response.json["error"])

    def test_docs_page_uses_local_swagger_ui_assets(self):
        app = create_app(AppConfig())
        client = app.test_client()

        response = client.get("/docs")
        body = response.get_data(as_text=True)

        self.assertEqual(200, response.status_code)
        self.assertIn("SwaggerUIBundle", body)
        self.assertIn("/static/swagger-ui/swagger-ui.css", body)
        self.assertIn("/static/swagger-ui/swagger-ui-bundle.js", body)
        self.assertNotIn("https://", body)
        self.assertNotIn("unpkg.com", body)

        static_paths = (
            "/static/swagger-ui/swagger-ui.css",
            "/static/swagger-ui/swagger-ui-bundle.js",
            "/favicon.ico",
        )
        for path in static_paths:
            static_response = client.get(path)
            try:
                self.assertEqual(200, static_response.status_code)
            finally:
                static_response.close()

    def test_openapi_uses_manifest_completion_without_idle_timeout(self):
        app = create_app(AppConfig())
        client = app.test_client()

        response = client.get("/openapi.json")

        self.assertEqual(200, response.status_code)
        statuses = response.json["components"]["schemas"][
            "SuperResolutionJobStatus"
        ]["properties"]["status"]["enum"]
        self.assertNotIn("partially_completed", statuses)
        self.assertNotIn(
            "idle_timeout_seconds",
            response.json["components"]["schemas"][
                "SuperResolutionTaskCreate"
            ]["properties"],
        )
        self.assertNotIn(
            "idle_timeout_seconds",
            response.json["components"]["schemas"][
                "SyncTaskCreateForFolder"
            ]["properties"],
        )


if __name__ == "__main__":
    unittest.main()
