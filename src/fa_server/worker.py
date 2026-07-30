from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial

from fa_server.services.super_resolution_service import (
    SuperResolutionService,
    SuperResolutionTaskRequest,
)
from fa_server.services.sync_service import SyncService, SyncTaskRequest


logger = logging.getLogger(__name__)


def log_background_task_failure(
    future: Future[None],
    *,
    task_type: str,
    job_id: str,
    folder_name: str,
) -> None:
    try:
        future.result()
    except Exception:
        logger.exception(
            "Background %s task failed: job_id=%s folder_name=%s",
            task_type,
            job_id,
            folder_name,
        )


class BackgroundTaskManager:
    def __init__(
        self,
        sync_service: SyncService,
        super_resolution_service: SuperResolutionService,
        max_workers: int = 4,
    ):
        self.sync_service = sync_service
        self.super_resolution_service = super_resolution_service
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

    def submit_sync(
        self,
        job_id: str,
        request: SyncTaskRequest,
    ) -> Future[None]:
        future = self.executor.submit(self.sync_service.run_job, job_id, request)
        future.add_done_callback(
            partial(
                log_background_task_failure,
                task_type="sync",
                job_id=job_id,
                folder_name=request.folder_name,
            )
        )
        return future

    def submit_super_resolution(
        self,
        job_id: str,
        request: SuperResolutionTaskRequest,
    ) -> Future[None]:
        future = self.executor.submit(
            self.super_resolution_service.run_job,
            job_id,
            request,
        )
        future.add_done_callback(
            partial(
                log_background_task_failure,
                task_type="super-resolution",
                job_id=job_id,
                folder_name=request.folder_name,
            )
        )
        return future
