from __future__ import annotations

import queue
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

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
    ) -> list[Path]:
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
                batch_queue = self._build_batch_queue(repository, request)
                processed_now = self._drain_batch_queue(
                    job_id,
                    batch_queue,
                    output_dir,
                    repository,
                    request.model_path,
                )
                if processed_now:
                    processed_count += processed_now
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

    def _build_batch_queue(
        self,
        repository: TaskRepository,
        request: SuperResolutionTaskRequest,
    ) -> queue.Queue[list[ImageTask]]:
        batch_queue: queue.Queue[list[ImageTask]] = queue.Queue()
        while True:
            batch = repository.list_pending_sr(
                request.folder_name,
                limit=request.batch_size,
            )
            if not batch:
                return batch_queue
            if len(batch) < request.batch_size and not request.process_partial_batch:
                return batch_queue
            batch_queue.put(batch)
            if len(batch) < request.batch_size:
                return batch_queue

            repository.mark_sr_processing((task.id for task in batch), "queued")

    def _drain_batch_queue(
        self,
        job_id: str,
        batch_queue: queue.Queue[list[ImageTask]],
        output_dir: Path,
        repository: TaskRepository,
        model_path: Path,
    ) -> int:
        processed_count = 0
        while not batch_queue.empty():
            processed_count += self._process_batch(
                job_id,
                batch_queue.get_nowait(),
                output_dir,
                repository,
                model_path,
            )
        return processed_count

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
            outputs = self.model_runner.enhance(paths, output_dir, model_path)
        except Exception as exc:
            for task in batch:
                repository.mark_sr_failed(task.id, str(exc))
            raise

        if len(outputs) != len(batch):
            message = "super_resolve returned a different number of outputs"
            for task in batch:
                repository.mark_sr_failed(task.id, message)
            raise RuntimeError(message)

        for task, output in zip(batch, outputs, strict=True):
            repository.mark_sr_done(task.id, output)
        return len(batch)


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
