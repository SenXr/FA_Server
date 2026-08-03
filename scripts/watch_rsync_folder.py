from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


FOLDER_NAME_MATCH = "raw_test"
RSYNC_COMMAND = "rsync"
RSYNC_REMOTE_BASE = "rsync://10.88.7.117/data"
LATEST_FOLDER_LIMIT = 10

FA_SERVER_URL = "http://127.0.0.1:5000"
API_KEY = "dev-api-key"

CHECK_INTERVAL_SECONDS = 60
SUPER_RESOLUTION_DELAY_SECONDS = 180
SUPER_RESOLUTION_BATCH_SIZE = 150
RSYNC_CHECK_TIMEOUT_SECONDS = 30
HTTP_TIMEOUT_SECONDS = 30


def log(message: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}", flush=True)


RSYNC_DIRECTORY_LINE = re.compile(
    r"^d\S*\s+\S+\s+"
    r"(?P<date>\d{4}[/-]\d{2}[/-]\d{2})\s+"
    r"(?P<time>\d{2}:\d{2}:\d{2})\s+"
    r"(?P<name>.+)$"
)


def parse_rsync_folders(output: str) -> list[tuple[datetime, str]]:
    folders: list[tuple[datetime, str]] = []
    for line in output.splitlines():
        match = RSYNC_DIRECTORY_LINE.match(line.strip())
        if not match:
            continue
        folder_name = match.group("name").rstrip("/")
        if folder_name in {"", "."}:
            continue
        timestamp_text = (
            f"{match.group('date').replace('-', '/')} {match.group('time')}"
        )
        folders.append(
            (
                datetime.strptime(timestamp_text, "%Y/%m/%d %H:%M:%S"),
                folder_name,
            )
        )
    return folders


def find_latest_matching_folder() -> str | None:
    remote_url = f"{RSYNC_REMOTE_BASE.rstrip('/')}/"
    try:
        result = subprocess.run(
            [RSYNC_COMMAND, "--list-only", remote_url],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            timeout=RSYNC_CHECK_TIMEOUT_SECONDS,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log(f"rsync check failed: {exc}")
        return None

    if result.returncode != 0:
        error = result.stderr.strip() or f"exit code {result.returncode}"
        log(f"rsync check failed: {error}")
        return None

    latest_folders = sorted(
        parse_rsync_folders(result.stdout),
        key=lambda item: item[0],
        reverse=True,
    )[:LATEST_FOLDER_LIMIT]
    name_match = FOLDER_NAME_MATCH.casefold()
    for _, folder_name in latest_folders:
        if name_match in folder_name.casefold():
            return folder_name
    return None


def post_json(path: str, payload: dict) -> dict:
    request = Request(
        f"{FA_SERVER_URL.rstrip('/')}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-API-Key": API_KEY,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"FA Server request failed: {exc.reason}") from exc


def main() -> None:
    log(
        f"waiting for a folder containing {FOLDER_NAME_MATCH!r} among the "
        f"latest {LATEST_FOLDER_LIMIT} rsync folders"
    )

    folder_name = find_latest_matching_folder()
    while folder_name is None:
        time.sleep(CHECK_INTERVAL_SECONDS)
        folder_name = find_latest_matching_folder()

    remote_url = f"{RSYNC_REMOTE_BASE.rstrip('/')}/{folder_name}/"
    log(f"rsync folder found: {remote_url}; creating sync task")
    sync_result = post_json(
        f"/api/v1/sync/tasks/{quote(folder_name, safe='')}",
        {
            "remote_base": RSYNC_REMOTE_BASE,
            "enable_transcode_rename": True,
            "raw_extensions": [".raw"],
        },
    )
    log(f"sync task accepted: job_id={sync_result.get('job_id')}")

    log(
        f"waiting {SUPER_RESOLUTION_DELAY_SECONDS} seconds before creating "
        "super-resolution task"
    )
    time.sleep(SUPER_RESOLUTION_DELAY_SECONDS)

    sr_result = post_json(
        "/api/v1/super-resolution/tasks",
        {
            "folder_name": folder_name,
            "batch_size": SUPER_RESOLUTION_BATCH_SIZE,
        },
    )
    log(f"super-resolution task accepted: job_id={sr_result.get('job_id')}")


if __name__ == "__main__":
    main()
