from __future__ import annotations

import json
import re
from hmac import compare_digest
from pathlib import Path

from flask import Flask, Response, jsonify, request
from werkzeug.exceptions import BadRequest

from fa_server.config import AppConfig
from fa_server.openapi import OPENAPI_SPEC
from fa_server.utils.paths import (
    InvalidFolderName,
    folder_database_path,
    normalize_path_text,
    path_from_user_input,
    validate_folder_name,
)
from fa_server.swagger_ui import SWAGGER_UI_HTML
from fa_server.services.super_resolution_service import SuperResolutionService
from fa_server.services.sync_service import SyncService, required_sync_file_count
from fa_server.storage import ActiveJobExists, DuplicateFolderTask, TaskRepository
from fa_server.worker import BackgroundTaskManager


def create_app(config: AppConfig | None = None) -> Flask:
    config = config or AppConfig.from_env()
    app = Flask(__name__)
    app.config.update(
        FA_HOST=config.host,
        FA_PORT=config.port,
    )

    sync_service = SyncService(config)
    sr_service = SuperResolutionService(config)
    task_manager = BackgroundTaskManager(sync_service, sr_service)

    @app.before_request
    def authenticate_api_request():
        if not config.auth_enabled or not request.path.startswith("/api/v1/"):
            return None

        api_key = request.headers.get("X-API-Key", "")
        bearer = request.headers.get("Authorization", "")
        if bearer.lower().startswith("bearer "):
            api_key = bearer[7:].strip()

        if api_key and compare_digest(api_key, config.api_key):
            return None

        return unauthorized_response()

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.get("/openapi.json")
    def openapi():
        return jsonify(OPENAPI_SPEC)

    @app.get("/docs")
    def docs():
        return SWAGGER_UI_HTML

    @app.get("/favicon.ico")
    def favicon():
        return app.send_static_file("swagger-ui/favicon-32x32.png")

    @app.post("/api/v1/sync/tasks/<path:folder_name>")
    def create_sync_task_for_folder(folder_name: str):
        try:
            payload = json_payload()
            payload["folder_name"] = folder_name
            sync_request = sync_service.build_request(payload)
            job_id, db_path = sync_service.create_job(sync_request)
            task_manager.submit_sync(job_id, sync_request)
            return (
                jsonify(
                    {
                        "job_id": job_id,
                        "folder_name": sync_request.folder_name,
                        "database_path": str(db_path),
                        "status_url": (
                            f"/api/v1/sync/jobs/{job_id}"
                            f"?folder_name={sync_request.folder_name}"
                        ),
                    }
                ),
                202,
            )
        except (InvalidFolderName, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        except (ActiveJobExists, DuplicateFolderTask) as exc:
            return (
                jsonify(
                    {
                        "error": str(exc),
                        "existing_job_id": exc.job_id,
                    }
                ),
                409,
            )

    @app.post("/api/v1/sync/tasks/<path:folder_name>/updates")
    def create_sync_update_task(folder_name: str):
        try:
            payload = json_payload()
            payload["folder_name"] = folder_name
            sync_request = sync_service.build_request(payload)
            job_id, db_path = sync_service.create_update_job(sync_request)
            task_manager.submit_sync(job_id, sync_request)
            return (
                jsonify(
                    {
                        "job_id": job_id,
                        "folder_name": sync_request.folder_name,
                        "database_path": str(db_path),
                        "status_url": (
                            f"/api/v1/sync/jobs/{job_id}"
                            f"?folder_name={sync_request.folder_name}"
                        ),
                    }
                ),
                202,
            )
        except (InvalidFolderName, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        except ActiveJobExists as exc:
            return (
                jsonify(
                    {
                        "error": str(exc),
                        "existing_job_id": exc.job_id,
                    }
                ),
                409,
            )

    @app.get("/api/v1/sync/jobs/<job_id>")
    @app.get("/api/v1/sync/tasks/<job_id>")
    def get_sync_task(job_id: str):
        try:
            repository = _repository_from_query(config)
            row = repository.get_sync_job(job_id)
        except (InvalidFolderName, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        except ActiveJobExists as exc:
            return (
                jsonify(
                    {
                        "error": str(exc),
                        "existing_job_id": exc.job_id,
                    }
                ),
                409,
            )
        if row is None:
            return jsonify({"error": "sync task not found"}), 404
        row = enrich_sync_job_response(row, repository)
        return jsonify(row)

    @app.post("/api/v1/super-resolution/tasks")
    def create_super_resolution_task():
        try:
            payload = json_payload()
            sr_request = sr_service.build_request(payload)
            job_id, db_path = sr_service.create_job(sr_request)
            task_manager.submit_super_resolution(job_id, sr_request)
            return (
                jsonify(
                    {
                        "job_id": job_id,
                        "folder_name": sr_request.folder_name,
                        "database_path": str(db_path),
                        "status_url": (
                            f"/api/v1/super-resolution/tasks/{job_id}"
                            f"?folder_name={sr_request.folder_name}"
                        ),
                    }
                ),
                202,
            )
        except (InvalidFolderName, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/v1/super-resolution/tasks/<job_id>")
    def get_super_resolution_task(job_id: str):
        try:
            repository = _repository_from_query(config)
            row = repository.get_sr_job(job_id)
        except (InvalidFolderName, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        if row is None:
            return jsonify({"error": "super-resolution task not found"}), 404
        row["image_counts"] = repository.count_images_by_sr_status(row["folder_name"])
        return jsonify(row)

    return app


def unauthorized_response() -> Response:
    response = jsonify({"error": "valid API key required"})
    response.status_code = 401
    return response


def json_payload() -> dict:
    try:
        payload = request.get_json(silent=False)
    except BadRequest as exc:
        payload = parse_lenient_json_payload()
        if payload is None:
            raise ValueError(
                "invalid JSON body; escape Windows backslashes as \\\\ or use / in paths"
            ) from exc

    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ValueError("JSON body must be an object")
    return payload


def parse_lenient_json_payload() -> dict | None:
    body = request.get_data(cache=True, as_text=True)
    normalized = normalize_json_path_backslashes(body)
    if normalized == body:
        return None

    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def normalize_json_path_backslashes(body: str) -> str:
    path_fields = ("local_root", "rsync")
    pattern = re.compile(r'("(?:' + "|".join(path_fields) + r')"\s*:\s*")([^"]*)(")')

    def replace_path(match: re.Match[str]) -> str:
        return f"{match.group(1)}{normalize_path_text(match.group(2))}{match.group(3)}"

    return pattern.sub(replace_path, body)


def _repository_from_query(config: AppConfig) -> TaskRepository:
    folder_name = validate_folder_name(request.args.get("folder_name", ""))
    local_root = path_from_user_input(request.args.get("local_root") or config.local_root)
    database_filename = request.args.get("database_filename") or config.database_filename
    return TaskRepository(folder_database_path(local_root, folder_name, database_filename))


def enrich_sync_job_response(row: dict, repository: TaskRepository) -> dict:
    row["image_counts"] = repository.count_images_by_sr_status(row["folder_name"])
    row["required_file_count"] = required_sync_file_count(Path(row["local_dir"]))
    return row
