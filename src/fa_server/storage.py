from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ImageTask:
    id: int
    folder_name: str
    raw_path: str
    bmp_path: str | None
    final_bmp_path: str | None
    sr_input_path: str
    sr_status: str


class DuplicateFolderTask(RuntimeError):
    def __init__(self, *, folder_name: str, job_id: str):
        self.folder_name = folder_name
        self.job_id = job_id
        super().__init__(
            f"sync task already exists for folder '{folder_name}': {job_id}"
        )


class ActiveJobExists(RuntimeError):
    def __init__(self, *, job_type: str, folder_name: str, job_id: str):
        self.job_type = job_type
        self.folder_name = folder_name
        self.job_id = job_id
        super().__init__(
            f"active {job_type} job already exists for folder '{folder_name}': {job_id}"
        )


class TaskRepository:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS sync_jobs (
                    job_id TEXT PRIMARY KEY,
                    folder_name TEXT NOT NULL,
                    remote_url TEXT NOT NULL,
                    local_dir TEXT NOT NULL,
                    job_kind TEXT NOT NULL DEFAULT 'initial',
                    transcode_rename_enabled INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    idle_timeout_seconds INTEGER NOT NULL,
                    poll_interval_seconds INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    last_new_file_at TEXT,
                    synced_file_count INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT
                );

                CREATE TABLE IF NOT EXISTS sr_jobs (
                    job_id TEXT PRIMARY KEY,
                    folder_name TEXT NOT NULL,
                    output_dir TEXT NOT NULL,
                    model_path TEXT NOT NULL,
                    batch_size INTEGER NOT NULL,
                    process_partial_batch INTEGER NOT NULL,
                    idle_timeout_seconds INTEGER NOT NULL DEFAULT 600,
                    poll_interval_seconds INTEGER NOT NULL DEFAULT 10,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    processed_file_count INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT
                );

                CREATE TABLE IF NOT EXISTS image_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    folder_name TEXT NOT NULL,
                    raw_path TEXT NOT NULL UNIQUE,
                    source_mtime_ns INTEGER,
                    source_size INTEGER,
                    bmp_path TEXT,
                    final_bmp_path TEXT,
                    sr_input_path TEXT NOT NULL,
                    sr_output_path TEXT,
                    transcode_status TEXT NOT NULL,
                    rename_status TEXT NOT NULL,
                    sr_status TEXT NOT NULL,
                    sync_job_id TEXT,
                    sr_job_id TEXT,
                    synced_at TEXT NOT NULL,
                    processed_at TEXT,
                    updated_at TEXT NOT NULL,
                    error_message TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_image_tasks_folder_sr
                    ON image_tasks(folder_name, sr_status, id);
                """
            )
            self._ensure_column(
                conn,
                "sync_jobs",
                "job_kind",
                "TEXT NOT NULL DEFAULT 'initial'",
            )
            self._ensure_column(conn, "image_tasks", "source_mtime_ns", "INTEGER")
            self._ensure_column(conn, "image_tasks", "source_size", "INTEGER")
            self._ensure_column(
                conn,
                "sr_jobs",
                "idle_timeout_seconds",
                "INTEGER NOT NULL DEFAULT 600",
            )
            self._ensure_column(
                conn,
                "sr_jobs",
                "poll_interval_seconds",
                "INTEGER NOT NULL DEFAULT 10",
            )
            self._ensure_column(conn, "sr_jobs", "model_path", "TEXT")
            conn.commit()
        finally:
            conn.close()

    def create_sync_job(
        self,
        *,
        job_id: str,
        folder_name: str,
        remote_url: str,
        local_dir: Path,
        transcode_rename_enabled: bool,
        poll_interval_seconds: int,
        job_kind: str = "initial",
        allow_existing_folder: bool = False,
    ) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                """
                SELECT job_id
                FROM sync_jobs
                WHERE folder_name = ? AND status IN ('queued', 'running')
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (folder_name,),
            ).fetchone()
            if active is not None:
                raise ActiveJobExists(
                    job_type="sync",
                    folder_name=folder_name,
                    job_id=active["job_id"],
                )

            if not allow_existing_folder:
                existing = conn.execute(
                    """
                    SELECT job_id
                    FROM sync_jobs
                    WHERE folder_name = ? AND job_kind = 'initial'
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    (folder_name,),
                ).fetchone()
                if existing is not None:
                    raise DuplicateFolderTask(
                        folder_name=folder_name,
                        job_id=existing["job_id"],
                    )

            conn.execute(
                """
                INSERT INTO sync_jobs (
                    job_id, folder_name, remote_url, local_dir, job_kind,
                    transcode_rename_enabled, status, idle_timeout_seconds,
                    poll_interval_seconds, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?)
                """,
                (
                    job_id,
                    folder_name,
                    remote_url,
                    str(local_dir),
                    job_kind,
                    int(transcode_rename_enabled),
                    poll_interval_seconds,
                    now,
                ),
            )

    def update_sync_job(self, job_id: str, **fields: object) -> None:
        self._update("sync_jobs", "job_id", job_id, fields)

    def get_sync_job(self, job_id: str) -> dict | None:
        row = self._get_by_id("sync_jobs", "job_id", job_id)
        if row is not None:
            row.pop("idle_timeout_seconds", None)
        return row

    def has_active_sync_job(self, folder_name: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM sync_jobs
                WHERE folder_name = ? AND status IN ('queued', 'running')
                LIMIT 1
                """,
                (folder_name,),
            ).fetchone()
        return row is not None

    def create_sr_job(
        self,
        *,
        job_id: str,
        folder_name: str,
        output_dir: Path,
        model_path: Path,
        batch_size: int,
        process_partial_batch: bool,
        poll_interval_seconds: int = 10,
    ) -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                """
                SELECT job_id
                FROM sr_jobs
                WHERE folder_name = ? AND status IN ('queued', 'running')
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (folder_name,),
            ).fetchone()
            if active is not None:
                raise ActiveJobExists(
                    job_type="super-resolution",
                    folder_name=folder_name,
                    job_id=active["job_id"],
                )
            conn.execute(
                """
                INSERT INTO sr_jobs (
                    job_id, folder_name, output_dir, model_path, batch_size,
                    process_partial_batch, idle_timeout_seconds,
                    poll_interval_seconds, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, 'queued', ?)
                """,
                (
                    job_id,
                    folder_name,
                    str(output_dir),
                    str(model_path),
                    batch_size,
                    int(process_partial_batch),
                    poll_interval_seconds,
                    utc_now(),
                ),
            )

    def update_sr_job(self, job_id: str, **fields: object) -> None:
        self._update("sr_jobs", "job_id", job_id, fields)

    def get_sr_job(self, job_id: str) -> dict | None:
        row = self._get_by_id("sr_jobs", "job_id", job_id)
        if row is not None:
            row.pop("idle_timeout_seconds", None)
        return row

    def upsert_raw_file(
        self,
        *,
        folder_name: str,
        raw_path: Path,
        sync_job_id: str,
        transcode_rename_enabled: bool,
    ) -> tuple[bool, int]:
        now = utc_now()
        if transcode_rename_enabled:
            transcode_status = "pending"
            rename_status = "pending"
            sr_status = "pending_conversion"
        else:
            transcode_status = "skipped"
            rename_status = "skipped"
            sr_status = "pending"

        raw_path_str = str(raw_path.resolve())
        stat = raw_path.stat()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO image_tasks (
                    folder_name, raw_path, source_mtime_ns, source_size,
                    sr_input_path, transcode_status,
                    rename_status, sr_status, sync_job_id, synced_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    folder_name,
                    raw_path_str,
                    stat.st_mtime_ns,
                    stat.st_size,
                    raw_path_str,
                    transcode_status,
                    rename_status,
                    sr_status,
                    sync_job_id,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                """
                SELECT id, source_mtime_ns, source_size
                FROM image_tasks
                WHERE raw_path = ?
                """,
                (raw_path_str,),
            ).fetchone()
            image_task_id = int(row["id"])
            if cursor.rowcount == 1:
                return True, image_task_id

            if (
                row["source_mtime_ns"] == stat.st_mtime_ns
                and row["source_size"] == stat.st_size
            ):
                return False, image_task_id

            conn.execute(
                """
                UPDATE image_tasks
                SET source_mtime_ns = ?, source_size = ?, bmp_path = NULL,
                    final_bmp_path = NULL, sr_output_path = NULL,
                    sr_input_path = ?, transcode_status = ?,
                    rename_status = ?, sr_status = ?, sync_job_id = ?,
                    synced_at = ?, processed_at = NULL, updated_at = ?,
                    error_message = NULL
                WHERE id = ?
                """,
                (
                    stat.st_mtime_ns,
                    stat.st_size,
                    raw_path_str,
                    transcode_status,
                    rename_status,
                    sr_status,
                    sync_job_id,
                    now,
                    now,
                    image_task_id,
                ),
            )
            return True, image_task_id

    def mark_conversion_done(
        self,
        image_task_id: int,
        *,
        bmp_path: Path,
        final_bmp_path: Path,
    ) -> None:
        final_path = str(final_bmp_path.resolve())
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE image_tasks
                SET bmp_path = ?, final_bmp_path = ?, sr_input_path = ?,
                    transcode_status = 'done', rename_status = 'done',
                    sr_status = 'pending', updated_at = ?, error_message = NULL
                WHERE id = ?
                """,
                (
                    str(bmp_path.resolve()),
                    final_path,
                    final_path,
                    utc_now(),
                    image_task_id,
                ),
            )

    def get_completed_final_bmp_path(self, image_task_id: int) -> Path | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT final_bmp_path
                FROM image_tasks
                WHERE id = ?
                  AND transcode_status = 'done'
                  AND rename_status = 'done'
                """,
                (image_task_id,),
            ).fetchone()
        if row is None or not row["final_bmp_path"]:
            return None
        return Path(row["final_bmp_path"])

    def mark_conversion_failed(self, image_task_id: int, error_message: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE image_tasks
                SET transcode_status = 'failed', rename_status = 'failed',
                    sr_status = 'blocked', updated_at = ?, error_message = ?
                WHERE id = ?
                """,
                (utc_now(), error_message, image_task_id),
            )

    def list_pending_sr(self, folder_name: str, limit: int) -> list[ImageTask]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, folder_name, raw_path, bmp_path, final_bmp_path,
                       sr_input_path, sr_status
                FROM image_tasks
                WHERE folder_name = ? AND sr_status = 'pending'
                ORDER BY id ASC
                LIMIT ?
                """,
                (folder_name, limit),
            ).fetchall()
        return [
            ImageTask(
                id=int(row["id"]),
                folder_name=row["folder_name"],
                raw_path=row["raw_path"],
                bmp_path=row["bmp_path"],
                final_bmp_path=row["final_bmp_path"],
                sr_input_path=row["sr_input_path"],
                sr_status=row["sr_status"],
            )
            for row in rows
        ]

    def mark_sr_processing(self, task_ids: Iterable[int], sr_job_id: str) -> None:
        ids = list(task_ids)
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as conn:
            conn.execute(
                f"""
                UPDATE image_tasks
                SET sr_status = 'processing', sr_job_id = ?, updated_at = ?
                WHERE id IN ({placeholders})
                """,
                (sr_job_id, utc_now(), *ids),
            )

    def mark_sr_done(
        self,
        task_id: int,
        output_path: Path | None = None,
    ) -> None:
        resolved_output_path = (
            str(output_path.resolve()) if output_path is not None else None
        )
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE image_tasks
                SET sr_status = 'done', sr_output_path = ?, processed_at = ?,
                    updated_at = ?, error_message = NULL
                WHERE id = ?
                """,
                (resolved_output_path, utc_now(), utc_now(), task_id),
            )

    def mark_sr_failed(self, task_id: int, error_message: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE image_tasks
                SET sr_status = 'failed', processed_at = ?, updated_at = ?,
                    error_message = ?
                WHERE id = ?
                """,
                (utc_now(), utc_now(), error_message, task_id),
            )

    def count_images_by_sr_status(self, folder_name: str) -> dict[str, int]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT sr_status, COUNT(*) AS count
                FROM image_tasks
                WHERE folder_name = ?
                GROUP BY sr_status
                """,
                (folder_name,),
            ).fetchall()
        return {row["sr_status"]: int(row["count"]) for row in rows}

    def _get_by_id(self, table: str, id_column: str, value: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                f"SELECT * FROM {table} WHERE {id_column} = ?",
                (value,),
            ).fetchone()
        return dict(row) if row else None

    def _update(self, table: str, id_column: str, value: str, fields: dict) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{key} = ?" for key in fields)
        params = [*fields.values(), value]
        with self.connect() as conn:
            conn.execute(
                f"UPDATE {table} SET {assignments} WHERE {id_column} = ?",
                params,
            )

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        column_name: str,
        definition: str,
    ) -> None:
        columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name not in columns:
            conn.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
            )
