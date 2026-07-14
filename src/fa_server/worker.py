from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from fa_server.services.super_resolution_service import (
    SuperResolutionService,
    SuperResolutionTaskRequest,
)
from fa_server.services.sync_service import SyncService, SyncTaskRequest


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

    def submit_sync(self, job_id: str, request: SyncTaskRequest) -> None:
        self.executor.submit(self.sync_service.run_job, job_id, request)

    def submit_super_resolution(
        self,
        job_id: str,
        request: SuperResolutionTaskRequest,
    ) -> None:
        self.executor.submit(self.super_resolution_service.run_job, job_id, request)
