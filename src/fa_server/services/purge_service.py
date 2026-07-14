from __future__ import annotations

import shutil
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from fa_server.config import AppConfig, PROJECT_ROOT


ACTIVE_STATUSES = ("queued", "running")


@dataclass(frozen=True)
class PurgeResult:
    scanned: int
    max_folders: int
    deleted: tuple[str, ...] = field(default_factory=tuple)
    skipped_active: tuple[str, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)


class PurgeService:
    def __init__(
        self,
        *,
        local_root: Path,
        max_folders: int,
        database_filename: str,
        log_path: Path,
    ):
        self.local_root = local_root
        self.max_folders = max_folders
        self.database_filename = database_filename
        self.log_path = log_path

    def purge_once(self) -> PurgeResult:
        self.local_root.mkdir(parents=True, exist_ok=True)
        folders = self._list_task_folders()
        deleted: list[str] = []
        skipped_active: list[str] = []
        errors: list[str] = []

        if self.max_folders < 0:
            errors.append("max_folders must be greater than or equal to 0")
            result = PurgeResult(
                scanned=len(folders),
                max_folders=self.max_folders,
                errors=tuple(errors),
            )
            self._write_log(result)
            return result

        for folder in folders:
            if len(folders) - len(deleted) <= self.max_folders:
                break
            if self._has_active_job(folder):
                skipped_active.append(folder.name)
                continue
            try:
                self._delete_folder(folder)
                deleted.append(folder.name)
            except OSError as exc:
                errors.append(f"{folder.name}:{exc}")

        result = PurgeResult(
            scanned=len(folders),
            max_folders=self.max_folders,
            deleted=tuple(deleted),
            skipped_active=tuple(skipped_active),
            errors=tuple(errors),
        )
        self._write_log(result)
        return result

    def _list_task_folders(self) -> list[Path]:
        if not self.local_root.exists():
            return []
        folders = [
            path
            for path in self.local_root.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        ]
        return sorted(folders, key=lambda path: (path.stat().st_mtime, path.name))

    def _has_active_job(self, folder: Path) -> bool:
        db_path = folder / self.database_filename
        if not db_path.exists():
            return False

        try:
            with closing(
                sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
            ) as conn:
                for table in ("sync_jobs", "sr_jobs"):
                    try:
                        row = conn.execute(
                            f"""
                            SELECT 1
                            FROM {table}
                            WHERE status IN (?, ?)
                            LIMIT 1
                            """,
                            ACTIVE_STATUSES,
                        ).fetchone()
                    except sqlite3.OperationalError as exc:
                        if "no such table" in str(exc).lower():
                            continue
                        return True
                    if row is not None:
                        return True
        except sqlite3.Error:
            return True
        return False

    def _delete_folder(self, folder: Path) -> None:
        root = self.local_root.resolve()
        target = folder.resolve()
        if target == root or not target.is_relative_to(root):
            raise OSError(f"refusing to delete outside local_root: {target}")
        shutil.rmtree(target)

    def _write_log(self, result: PurgeResult) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).isoformat()
        cleared = ",".join(result.deleted) if result.deleted else "-"
        skipped = ",".join(result.skipped_active) if result.skipped_active else "-"
        errors = "|".join(result.errors) if result.errors else "-"
        line = (
            f"{timestamp} action=purge scanned={result.scanned} "
            f"max={result.max_folders} cleared_count={len(result.deleted)} "
            f"cleared_folders={cleared} skipped_active={skipped} errors={errors}\n"
        )
        with self.log_path.open("a", encoding="utf-8") as file:
            file.write(line)


class PurgeScheduler:
    def __init__(self, service: PurgeService, interval_seconds: int):
        self.service = service
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            name="fa-server-purge",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        self.service.purge_once()
        while not self._stop.wait(self.interval_seconds):
            self.service.purge_once()


def start_purge_service(config: AppConfig) -> PurgeScheduler | None:
    if not config.purge_enabled:
        return None
    service = PurgeService(
        local_root=config.local_root,
        max_folders=config.purge_max_folders,
        database_filename=config.database_filename,
        log_path=purge_log_path(config.purge_log_filename),
    )
    scheduler = PurgeScheduler(service, config.purge_interval_seconds)
    scheduler.start()
    return scheduler


def purge_log_path(value: str) -> Path:
    path = Path(str(value).replace("\\", "/"))
    return path if path.is_absolute() else PROJECT_ROOT / path
