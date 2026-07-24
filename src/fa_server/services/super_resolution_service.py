from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from numbers import Integral
from pathlib import Path
from typing import Any

from fa_server.config import AppConfig
from ep5_enhancement import UNetGANConfig
from ep5_enhancement import batch_process as batch_process_ep5
from ep5_enhancement import load_model as load_ep5_model
from fa_server.utils.paths import (
    folder_database_path,
    folder_dir,
    path_from_user_input,
    validate_folder_name,
)
from fa_server.storage import ImageTask, TaskRepository, utc_now


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SR_MODEL_PATH = PROJECT_ROOT / "models" / "super_resolution" / "best_model.pth"


@dataclass(frozen=True)
class SuperResolutionTaskRequest:
    folder_name: str
    local_root: Path
    model_path: Path = DEFAULT_SR_MODEL_PATH
    batch_size: int = 3
    process_partial_batch: bool = True
    idle_timeout_seconds: int = 600
    poll_interval_seconds: int = 10
    output_dirname: str = "Super_Resolution"
    database_filename: str = "tasks.sqlite3"


class ModelRunner:
    def __init__(self):
        self._lock = threading.Lock()
        self._model_sessions: dict[
            Path,
            tuple[object, UNetGANConfig],
        ] = {}

    def ensure_loaded(
        self,
        model_path: Path = DEFAULT_SR_MODEL_PATH,
    ) -> tuple[object, UNetGANConfig]:
        resolved_model_path = model_path.expanduser().resolve()
        with self._lock:
            if resolved_model_path not in self._model_sessions:
                self._model_sessions[resolved_model_path] = load_ep5_model(
                    str(resolved_model_path),
                    config=UNetGANConfig(),
                )
            return self._model_sessions[resolved_model_path]

    def enhance(
        self,
        paths: list[str],
        output_dir: Path,
        model_path: Path = DEFAULT_SR_MODEL_PATH,
    ) -> Any:
        model, config = self.ensure_loaded(model_path)
        return batch_process_ep5(
            input_paths=paths,
            output_path=str(output_dir),
            model=model,
            config=config,
        )


class SuperResolutionService:
    def __init__(
        self,
        config: AppConfig,
        model_runner: ModelRunner | None = None,
        *,
        sleep_func=time.sleep,
        monotonic_func=time.monotonic,
    ):
        self.config = config
        self.model_runner = model_runner or ModelRunner()
        self.sleep_func = sleep_func
        self.monotonic_func = monotonic_func

    def build_request(self, payload: dict) -> SuperResolutionTaskRequest:
        folder_name = validate_folder_name(payload.get("folder_name", ""))
        model_path = path_from_user_input(
            payload.get("model_path") or DEFAULT_SR_MODEL_PATH
        ).expanduser()
        if not model_path.is_absolute():
            raise ValueError("model_path must be an absolute path")
        batch_size = int(
            payload.get("batch_size", self.config.super_resolution_batch_size)
        )
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than 0")
        return SuperResolutionTaskRequest(
            folder_name=folder_name,
            local_root=path_from_user_input(
                payload.get("local_root") or self.config.local_root
            ),
            model_path=model_path.resolve(),
            batch_size=batch_size,
            process_partial_batch=bool(payload.get("process_partial_batch", True)),
            idle_timeout_seconds=int(
                payload.get(
                    "idle_timeout_seconds",
                    self.config.super_resolution_idle_timeout_seconds,
                )
            ),
            poll_interval_seconds=int(
                payload.get(
                    "poll_interval_seconds",
                    self.config.super_resolution_poll_interval_seconds,
                )
            ),
            output_dirname=payload.get(
                "output_dirname", self.config.super_resolution_output_dirname
            ),
            database_filename=payload.get(
                "database_filename", self.config.database_filename
            ),
        )

    def create_job(self, request: SuperResolutionTaskRequest) -> tuple[str, Path]:
        job_id = uuid.uuid4().hex
        output_dir = folder_dir(request.local_root, request.folder_name) / request.output_dirname
        db_path = folder_database_path(
            request.local_root, request.folder_name, request.database_filename
        )
        repository = TaskRepository(db_path)
        repository.create_sr_job(
            job_id=job_id,
            folder_name=request.folder_name,
            output_dir=output_dir,
            model_path=request.model_path,
            batch_size=request.batch_size,
            process_partial_batch=request.process_partial_batch,
            idle_timeout_seconds=request.idle_timeout_seconds,
            poll_interval_seconds=request.poll_interval_seconds,
        )
        return job_id, db_path

    def run_job(self, job_id: str, request: SuperResolutionTaskRequest) -> None:
        repository = TaskRepository(
            folder_database_path(
                request.local_root, request.folder_name, request.database_filename
            )
        )
        output_dir = folder_dir(request.local_root, request.folder_name) / request.output_dirname
        output_dir.mkdir(parents=True, exist_ok=True)
        processed_count = 0
        last_activity_at = self.monotonic_func()

        repository.update_sr_job(job_id, status="running", started_at=utc_now())
        try:
            self.model_runner.ensure_loaded(request.model_path)
            while True:
                batch = self._next_batch(repository, request)
                if batch:
                    processed_count += self._process_batch(
                        job_id,
                        batch,
                        output_dir,
                        repository,
                        request.model_path,
                    )
                    last_activity_at = self.monotonic_func()
                    repository.update_sr_job(
                        job_id,
                        status="running",
                        processed_file_count=processed_count,
                    )
                    continue

                if super_resolution_task_table_complete(repository, request.folder_name):
                    repository.update_sr_job(
                        job_id,
                        status="completed",
                        finished_at=utc_now(),
                        processed_file_count=processed_count,
                    )
                    return

                if (
                    self.monotonic_func() - last_activity_at
                    >= request.idle_timeout_seconds
                ):
                    repository.update_sr_job(
                        job_id,
                        status="completed",
                        finished_at=utc_now(),
                        processed_file_count=processed_count,
                    )
                    return

                repository.update_sr_job(
                    job_id,
                    status="running",
                    processed_file_count=processed_count,
                )
                self.sleep_func(request.poll_interval_seconds)
        except Exception as exc:
            repository.update_sr_job(
                job_id,
                status="failed",
                finished_at=utc_now(),
                processed_file_count=processed_count,
                error_message=str(exc),
            )
            raise

    def _next_batch(
        self,
        repository: TaskRepository,
        request: SuperResolutionTaskRequest,
    ) -> list[ImageTask]:
        batch = repository.list_pending_sr(
            request.folder_name,
            limit=request.batch_size,
        )
        if len(batch) < request.batch_size and not request.process_partial_batch:
            return []
        return batch

    def _process_batch(
        self,
        job_id: str,
        batch: list[ImageTask],
        output_dir: Path,
        repository: TaskRepository,
        model_path: Path,
    ) -> int:
        repository.mark_sr_processing((task.id for task in batch), job_id)
        paths = [str(Path(task.sr_input_path).resolve()) for task in batch]
        try:
            result = self.model_runner.enhance(paths, output_dir, model_path)
            validate_batch_result(result, len(batch))
        except Exception as exc:
            for task in batch:
                repository.mark_sr_failed(task.id, str(exc))
            raise

        for task in batch:
            repository.mark_sr_done(task.id)
        return len(batch)


def validate_batch_result(
    result: Any,
    expected_count: int,
) -> None:
    if isinstance(result, Integral) and not isinstance(result, bool):
        processed_count = int(result)
        if processed_count != expected_count:
            raise RuntimeError(
                "super-resolution batch processed "
                f"{processed_count} of {expected_count} input images"
            )
        return

    if not isinstance(result, (list, tuple)):
        raise TypeError(
            "super-resolution batch must return an integer count "
            "or one result item per input image"
        )
    if len(result) != expected_count:
        raise RuntimeError(
            "super-resolution batch returned "
            f"{len(result)} result items for {expected_count} input images"
        )


def super_resolution_task_table_complete(
    repository: TaskRepository,
    folder_name: str,
) -> bool:
    counts = repository.count_images_by_sr_status(folder_name)
    if not counts:
        return False
    if repository.has_active_sync_job(folder_name):
        return False
    unfinished_statuses = {"pending", "processing", "pending_conversion"}
    return not any(counts.get(status, 0) for status in unfinished_statuses)
