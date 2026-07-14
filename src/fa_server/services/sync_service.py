from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from fa_server.config import AppConfig
from fa_server.utils.paths import (
    folder_database_path,
    folder_dir,
    normalize_path_text,
    path_from_user_input,
    validate_folder_name,
)
from fa_server.services.raw2bmp_service import (
    Raw2BmpService,
    Raw2BmpTaskSession,
    RawFileManifest,
    read_raw_manifest,
    sync_manifest_complete,
)
from fa_server.storage import TaskRepository, utc_now


@dataclass(frozen=True)
class SyncTaskRequest:
    folder_name: str
    remote_base: str
    local_root: Path
    rsync_command: str = "rsync"
    enable_transcode_rename: bool = True
    idle_timeout_seconds: int = 600
    poll_interval_seconds: int = 30
    raw_extensions: tuple[str, ...] = (".raw",)
    rsync_timeout_seconds: int = 3600
    database_filename: str = "tasks.sqlite3"


class SyncService:
    def __init__(
        self,
        config: AppConfig,
        raw2bmp_service: Raw2BmpService | None = None,
    ):
        self.config = config
        self.raw2bmp_service = raw2bmp_service or Raw2BmpService()

    def build_request(self, payload: dict) -> SyncTaskRequest:
        folder_name = validate_folder_name(payload.get("folder_name", ""))
        raw_extensions = payload.get("raw_extensions")
        if raw_extensions is None:
            extensions = self.config.raw_extensions
        else:
            extensions = tuple(str(ext).lower() for ext in raw_extensions if str(ext).strip())

        return SyncTaskRequest(
            folder_name=folder_name,
            remote_base=payload.get("remote_base") or self.config.remote_base,
            local_root=path_from_user_input(
                payload.get("local_root") or self.config.local_root
            ),
            rsync_command=normalize_path_text(
                payload.get("rsync") or self.config.rsync_command
            ),
            enable_transcode_rename=bool(payload.get("enable_transcode_rename", True)),
            idle_timeout_seconds=int(
                payload.get("idle_timeout_seconds", self.config.idle_timeout_seconds)
            ),
            poll_interval_seconds=int(
                payload.get("poll_interval_seconds", self.config.poll_interval_seconds)
            ),
            raw_extensions=extensions,
            rsync_timeout_seconds=int(
                payload.get("rsync_timeout_seconds", self.config.rsync_timeout_seconds)
            ),
            database_filename=payload.get(
                "database_filename", self.config.database_filename
            ),
        )

    def create_job(self, request: SyncTaskRequest) -> tuple[str, Path]:
        return self._create_job(
            request,
            job_kind="initial",
            allow_existing_folder=False,
        )

    def create_update_job(self, request: SyncTaskRequest) -> tuple[str, Path]:
        return self._create_job(
            request,
            job_kind="update",
            allow_existing_folder=True,
        )

    def _create_job(
        self,
        request: SyncTaskRequest,
        *,
        job_kind: str,
        allow_existing_folder: bool,
    ) -> tuple[str, Path]:
        job_id = uuid.uuid4().hex
        local_dir = folder_dir(request.local_root, request.folder_name)
        db_path = folder_database_path(
            request.local_root, request.folder_name, request.database_filename
        )
        repository = TaskRepository(db_path)
        repository.create_sync_job(
            job_id=job_id,
            folder_name=request.folder_name,
            remote_url=self._remote_url(request),
            local_dir=local_dir,
            transcode_rename_enabled=request.enable_transcode_rename,
            idle_timeout_seconds=request.idle_timeout_seconds,
            poll_interval_seconds=request.poll_interval_seconds,
            job_kind=job_kind,
            allow_existing_folder=allow_existing_folder,
        )
        return job_id, db_path

    def run_job(self, job_id: str, request: SyncTaskRequest) -> None:
        local_dir = folder_dir(request.local_root, request.folder_name)
        local_dir.mkdir(parents=True, exist_ok=True)
        repository = TaskRepository(
            folder_database_path(
                request.local_root, request.folder_name, request.database_filename
            )
        )
        repository.update_sync_job(
            job_id,
            status="running",
            started_at=utc_now(),
            last_new_file_at=utc_now(),
        )

        last_new_file_at = time.monotonic()
        total_new_files = 0
        raw2bmp_session = self.raw2bmp_service.create_task_session(local_dir)

        try:
            while True:
                self._run_rsync_once(request, local_dir)
                cleanup_redundant_files(local_dir)
                new_count = self._register_new_files(
                    job_id,
                    request,
                    local_dir,
                    repository,
                    raw2bmp_session,
                )
                if new_count:
                    total_new_files += new_count
                    last_new_file_at = time.monotonic()
                    repository.update_sync_job(
                        job_id,
                        last_new_file_at=utc_now(),
                        synced_file_count=total_new_files,
                    )

                manifest = read_raw_manifest(local_dir)
                if manifest and sync_manifest_complete(local_dir, manifest):
                    repository.update_sync_job(
                        job_id,
                        status="completed",
                        finished_at=utc_now(),
                        synced_file_count=total_new_files,
                    )
                    return

                if time.monotonic() - last_new_file_at >= request.idle_timeout_seconds:
                    manifest = read_raw_manifest(local_dir)
                    repository.update_sync_job(
                        job_id,
                        status=sync_completion_status(manifest, total_new_files),
                        finished_at=utc_now(),
                        synced_file_count=total_new_files,
                    )
                    return

                time.sleep(request.poll_interval_seconds)
        except Exception as exc:
            repository.update_sync_job(
                job_id,
                status="failed",
                finished_at=utc_now(),
                error_message=str(exc),
                synced_file_count=total_new_files,
            )
            raise
        finally:
            raw2bmp_session.finish()

    def _register_new_files(
        self,
        job_id: str,
        request: SyncTaskRequest,
        local_dir: Path,
        repository: TaskRepository,
        raw2bmp_session: Raw2BmpTaskSession,
    ) -> int:
        new_count = 0
        extensions = (
            conversion_source_extensions(request.raw_extensions)
            if request.enable_transcode_rename
            else request.raw_extensions
        )
        for raw_path in discover_data_files(local_dir, extensions):
            inserted, image_task_id = repository.upsert_raw_file(
                folder_name=request.folder_name,
                raw_path=raw_path,
                sync_job_id=job_id,
                transcode_rename_enabled=request.enable_transcode_rename,
            )
            if not inserted:
                continue

            new_count += 1
            if request.enable_transcode_rename:
                try:
                    final_bmp_path = raw2bmp_session.transcode_and_rename_raw(raw_path)
                    cleanup_source_artifacts(raw_path, final_bmp_path)
                    repository.mark_conversion_done(
                        image_task_id,
                        bmp_path=final_bmp_path,
                        final_bmp_path=final_bmp_path,
                    )
                except Exception as exc:
                    repository.mark_conversion_failed(image_task_id, str(exc))
        return new_count

    def _run_rsync_once(self, request: SyncTaskRequest, local_dir: Path) -> None:
        rsync_command = resolve_rsync(request.rsync_command)
        if rsync_command is None:
            raise FileNotFoundError(f"rsync executable not found: {request.rsync_command}")

        command = [
            rsync_command,
            "-av",
            self._remote_url(request),
            format_rsync_destination(local_dir, rsync_command),
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=request.rsync_timeout_seconds,
        )
        if completed.returncode == 0:
            return
        if is_missing_remote_folder_error(completed):
            return
        completed.check_returncode()

    def _remote_url(self, request: SyncTaskRequest) -> str:
        return f"{request.remote_base.rstrip('/')}/{request.folder_name}/"


def required_sync_file_count(root: Path) -> int | None:
    manifest = read_raw_manifest(root)
    return manifest.expected_count if manifest else None


def sync_completion_status(
    manifest: RawFileManifest | None,
    synced_file_count: int,
) -> str:
    if manifest and manifest.expected_count != synced_file_count:
        return "partially_completed"
    return "completed"


def discover_data_files(root: Path, raw_extensions: tuple[str, ...]) -> list[Path]:
    if not root.exists():
        return []

    normalized = tuple(ext.lower() for ext in raw_extensions)
    raw_stems = {
        path.with_suffix("").as_posix()
        for path in root.rglob("*.raw")
        if path.is_file()
    }
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if "Super_Resolution" in path.parts:
            continue
        if is_manifest_file_or_artifact(path):
            continue
        if is_transient_file(path):
            continue
        if is_generated_bmp(path):
            continue
        if is_redundant_bmp_for_raw(path, raw_stems):
            continue
        if normalized:
            if path.suffix.lower() in normalized:
                files.append(path)
        elif path.suffix.lower() not in {".xml", ".bmp"}:
            files.append(path)
    return sorted(files)


def conversion_source_extensions(raw_extensions: tuple[str, ...]) -> tuple[str, ...]:
    raw_only = tuple(ext for ext in raw_extensions if ext.lower() == ".raw")
    return raw_only or (".raw",)


def is_missing_remote_folder_error(completed: subprocess.CompletedProcess[str]) -> bool:
    output = f"{completed.stdout}\n{completed.stderr}".lower()
    missing_markers = (
        "no such file or directory",
        "failed to change directory",
        "change_dir",
        "unknown module",
        "does not exist",
    )
    return any(marker in output for marker in missing_markers)


def is_generated_bmp(path: Path) -> bool:
    if path.suffix.lower() != ".bmp":
        return False
    return (
        path.stem.endswith("T")
        or path.stem.endswith("_converted")
        or path.stem.endswith("_convertedT")
    )


def is_redundant_bmp_for_raw(path: Path, raw_stems: set[str]) -> bool:
    if path.suffix.lower() != ".bmp":
        return False
    return path.with_suffix("").as_posix() in raw_stems


def is_transient_file(path: Path) -> bool:
    return path.suffix.lower() in {".tmp", ".part", ".partial"}


def is_manifest_file_or_artifact(path: Path) -> bool:
    lower_name = path.name.lower()
    lower_stem = path.stem.lower()
    if lower_name.endswith(".xml"):
        return True
    return (
        path.suffix.lower() == ".bmp"
        and ("manifest" in lower_stem or "mainifest" in lower_stem)
    )


def cleanup_redundant_files(root: Path) -> None:
    if not root.exists():
        return

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if "Super_Resolution" in path.parts:
            continue
        if is_transient_file(path):
            path.unlink(missing_ok=True)
            continue
        if is_manifest_file_or_artifact(path) and path.suffix.lower() == ".bmp":
            path.unlink(missing_ok=True)

    raw_stems = {
        path.with_suffix("").as_posix()
        for path in root.rglob("*.raw")
        if path.is_file()
    }
    for raw_path in root.rglob("*.raw"):
        if not raw_path.is_file():
            continue
        if "Super_Resolution" in raw_path.parts:
            continue
        final_path = raw_path.with_name(f"{raw_path.stem}T.bmp")
        if final_path.exists():
            raw_path.unlink(missing_ok=True)

    for path in root.rglob("*.bmp"):
        if not path.is_file():
            continue
        if "Super_Resolution" in path.parts:
            continue
        if is_generated_bmp(path) and path.stem.endswith(("_converted", "_convertedT")):
            path.unlink(missing_ok=True)
            continue
        if is_redundant_bmp_for_raw(path, raw_stems):
            path.unlink(missing_ok=True)


def cleanup_source_artifacts(source_path: Path, final_bmp_path: Path) -> None:
    candidates = {
        source_path,
        source_path.with_suffix(".bmp"),
        source_path.with_name(f"{source_path.stem}_converted.bmp"),
        source_path.with_name(f"{source_path.stem}_convertedT.bmp"),
    }
    for path in candidates:
        if path == final_bmp_path:
            continue
        if path.exists() and path.is_file():
            path.unlink(missing_ok=True)


def resolve_rsync(command: str) -> str | None:
    command = normalize_path_text(command)
    if os.sep in command or "/" in command or "\\" in command:
        path = Path(command)
        return str(path) if path.exists() else None
    return shutil.which(command)


def format_rsync_destination(path: Path, rsync_command: str) -> str:
    if is_msys_rsync(rsync_command):
        return msys_path(path)
    return str(path)


def is_msys_rsync(rsync_command: str) -> bool:
    normalized = rsync_command.replace("\\", "/").lower()
    return "msys64/usr/bin/rsync" in normalized or normalized.endswith("/rsync.exe")


def msys_path(path: Path) -> str:
    value = str(path.resolve()).replace("\\", "/")
    if len(value) >= 2 and value[1] == ":":
        drive = value[0].lower()
        return f"/{drive}{value[2:]}"
    return value
