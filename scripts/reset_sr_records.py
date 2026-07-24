from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


ACTIVE_STATUSES = ("queued", "running")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Reset super-resolution jobs and image states in one tasks.sqlite3."
        )
    )
    parser.add_argument("database", type=Path, help="Path to tasks.sqlite3")
    backup_group = parser.add_mutually_exclusive_group()
    backup_group.add_argument(
        "--backup",
        type=Path,
        help="Custom backup path. The default is a timestamped file beside the database.",
    )
    backup_group.add_argument(
        "--no-backup",
        action="store_true",
        help="Reset without creating a backup.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow reset when queued or running super-resolution jobs exist.",
    )
    return parser.parse_args()


def default_backup_path(database_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return database_path.with_name(f"{database_path.name}.{timestamp}.bak")


def ensure_required_tables(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name IN ('sr_jobs', 'image_tasks')
        """
    ).fetchall()
    existing = {row[0] for row in rows}
    missing = {"sr_jobs", "image_tasks"} - existing
    if missing:
        raise RuntimeError(
            f"Not a valid task database; missing tables: {', '.join(sorted(missing))}"
        )


def create_backup(
    source_connection: sqlite3.Connection,
    backup_path: Path,
) -> None:
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    if backup_path.exists():
        raise FileExistsError(f"Backup already exists: {backup_path}")
    with closing(sqlite3.connect(backup_path)) as backup_connection:
        source_connection.backup(backup_connection)


def reset_sr_records(
    database_path: Path,
    *,
    backup_path: Path | None,
    force: bool = False,
) -> tuple[int, int]:
    database_path = database_path.expanduser().resolve()
    if not database_path.is_file():
        raise FileNotFoundError(f"Database not found: {database_path}")

    with closing(sqlite3.connect(database_path, timeout=30)) as connection:
        connection.execute("PRAGMA busy_timeout=30000")
        ensure_required_tables(connection)

        placeholders = ", ".join("?" for _ in ACTIVE_STATUSES)
        active_count = int(
            connection.execute(
                f"""
                SELECT COUNT(*)
                FROM sr_jobs
                WHERE status IN ({placeholders})
                """,
                ACTIVE_STATUSES,
            ).fetchone()[0]
        )
        if active_count and not force:
            raise RuntimeError(
                f"Found {active_count} active super-resolution job(s). "
                "Stop the service first or use --force."
            )

        if backup_path is not None:
            create_backup(connection, backup_path.expanduser().resolve())

        job_count = int(
            connection.execute("SELECT COUNT(*) FROM sr_jobs").fetchone()[0]
        )
        image_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM image_tasks
                WHERE transcode_status IN ('done', 'skipped')
                  AND rename_status IN ('done', 'skipped')
                """
            ).fetchone()[0]
        )

        now = datetime.now(timezone.utc).isoformat()
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute("DELETE FROM sr_jobs")
            connection.execute(
                """
                UPDATE image_tasks
                SET sr_status = 'pending',
                    sr_job_id = NULL,
                    sr_output_path = NULL,
                    processed_at = NULL,
                    error_message = NULL,
                    updated_at = ?
                WHERE transcode_status IN ('done', 'skipped')
                  AND rename_status IN ('done', 'skipped')
                """,
                (now,),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    return job_count, image_count


def main() -> int:
    args = parse_args()
    database_path = args.database.expanduser().resolve()
    backup_path = None
    if not args.no_backup:
        backup_path = (
            args.backup.expanduser().resolve()
            if args.backup is not None
            else default_backup_path(database_path)
        )

    try:
        job_count, image_count = reset_sr_records(
            database_path,
            backup_path=backup_path,
            force=args.force,
        )
    except (FileNotFoundError, FileExistsError, RuntimeError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if backup_path is not None:
        print(f"Backup: {backup_path}")
    print(f"Deleted super-resolution jobs: {job_count}")
    print(f"Reset images to pending: {image_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
