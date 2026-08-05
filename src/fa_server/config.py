from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from fa_server.utils.paths import path_from_user_input

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REMOTE_BASE = "rsync://admin@172.24.22.29:8873/data"
DEFAULT_LOCAL_ROOT = PROJECT_ROOT / "data" / "rsync_data"


@dataclass(frozen=True)
class AppConfig:
    host: str = "127.0.0.1"
    port: int = 5000
    remote_base: str = DEFAULT_REMOTE_BASE
    local_root: Path = DEFAULT_LOCAL_ROOT
    rsync_command: str = "rsync"
    poll_interval_seconds: int = 30
    task_stall_timeout_seconds: int = 3600
    raw_extensions: tuple[str, ...] = (".raw", ".bmp")
    rsync_timeout_seconds: int = 3600
    database_filename: str = "tasks.sqlite3"
    super_resolution_output_dirname: str = "Super_Resolution"
    super_resolution_batch_size: int = 3
    super_resolution_poll_interval_seconds: int = 10
    auth_enabled: bool = True
    api_key: str = "dev-api-key"
    purge_enabled: bool = True
    purge_max_folders: int = 10
    purge_interval_seconds: int = 86400
    purge_log_filename: str = "log/purge.log"

    @classmethod
    def from_env(cls) -> "AppConfig":
        raw_extensions = os.getenv("FA_RAW_EXTENSIONS", ".raw,.bmp")
        return cls(
            host=os.getenv("FA_HOST", "127.0.0.1"),
            port=int(os.getenv("FA_PORT", "5000")),
            remote_base=os.getenv("FA_REMOTE_BASE", DEFAULT_REMOTE_BASE),
            local_root=path_from_user_input(
                os.getenv("FA_LOCAL_ROOT", str(DEFAULT_LOCAL_ROOT))
            ),
            rsync_command=os.getenv("FA_RSYNC", "rsync"),
            poll_interval_seconds=int(os.getenv("FA_POLL_INTERVAL_SECONDS", "30")),
            task_stall_timeout_seconds=int(
                os.getenv("FA_TASK_STALL_TIMEOUT_SECONDS", "3600")
            ),
            raw_extensions=tuple(
                ext.strip().lower() for ext in raw_extensions.split(",") if ext.strip()
            ),
            rsync_timeout_seconds=int(os.getenv("FA_RSYNC_TIMEOUT_SECONDS", "3600")),
            database_filename=os.getenv("FA_DATABASE_FILENAME", "tasks.sqlite3"),
            super_resolution_output_dirname=os.getenv(
                "FA_SR_OUTPUT_DIRNAME", "Super_Resolution"
            ),
            super_resolution_batch_size=int(os.getenv("FA_SR_BATCH_SIZE", "3")),
            super_resolution_poll_interval_seconds=int(
                os.getenv("FA_SR_POLL_INTERVAL_SECONDS", "10")
            ),
            auth_enabled=os.getenv("FA_AUTH_ENABLED", "true").lower()
            not in {"0", "false", "no", "off"},
            api_key=os.getenv("FA_API_KEY", "dev-api-key"),
            purge_enabled=os.getenv("FA_PURGE_ENABLED", "true").lower()
            not in {"0", "false", "no", "off"},
            purge_max_folders=int(os.getenv("FA_PURGE_MAX_FOLDERS", "10")),
            purge_interval_seconds=int(os.getenv("FA_PURGE_INTERVAL_SECONDS", "86400")),
            purge_log_filename=os.getenv("FA_PURGE_LOG_FILENAME", "log/purge.log"),
        )
