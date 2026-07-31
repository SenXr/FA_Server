from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


FOLDER_NAME = "raw_test"
RSYNC_COMMAND = "rsync"
RSYNC_REMOTE_BASE = "rsync://10.88.7.117/data"

FA_SERVER_URL = "http://127.0.0.1:5000"
API_KEY = "dev-api-key"

CHECK_INTERVAL_SECONDS = 60
SUPER_RESOLUTION_DELAY_SECONDS = 180
SUPER_RESOLUTION_BATCH_SIZE = 150
RSYNC_CHECK_TIMEOUT_SECONDS = 30
HTTP_TIMEOUT_SECONDS = 30


def log(message: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}", flush=True)


def rsync_folder_exists() -> bool:
    remote_url = f"{RSYNC_REMOTE_BASE.rstrip('/')}/{FOLDER_NAME}/"
    try:
        result = subprocess.run(
            [RSYNC_COMMAND, "--list-only", remote_url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=RSYNC_CHECK_TIMEOUT_SECONDS,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log(f"rsync check failed: {exc}")
        return False
    return result.returncode == 0


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
    remote_url = f"{RSYNC_REMOTE_BASE.rstrip('/')}/{FOLDER_NAME}/"
    log(f"waiting for rsync folder: {remote_url}")

    while not rsync_folder_exists():
        time.sleep(CHECK_INTERVAL_SECONDS)

    log("rsync folder found; creating sync task")
    sync_result = post_json(
        f"/api/v1/sync/tasks/{quote(FOLDER_NAME, safe='/')}",
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
            "folder_name": FOLDER_NAME,
            "batch_size": SUPER_RESOLUTION_BATCH_SIZE,
        },
    )
    log(f"super-resolution task accepted: job_id={sr_result.get('job_id')}")


if __name__ == "__main__":
    main()
