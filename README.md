# FA Server

FA Server is a Flask service for folder-based image processing. It runs two
workflows:

1. Incremental file synchronization over `rsync`, with optional RAW-to-BMP
   conversion and XML-based renaming.
2. Batched super-resolution inference over images recorded in SQLite.

Each folder is an independent task scope with its own database:

```text
<local_root>/<folder_name>/tasks.sqlite3
```

## Processing Flow

The sync service fetches `raw_file_manifest.xml` first when it is available,
then starts the folder sync. Files that have finished copying are registered and
processed while `rsync` is still running. With `enable_transcode_rename=true`,
only `.raw` files are sent to the RAW processor.

The super-resolution service reads pending input records from the same SQLite
database and sends them to the model in batches. Models are cached in the server
process, and inference is serialized to avoid concurrent access to the same
accelerator. Output files are written under:

```text
<local_root>/<folder_name>/Super_Resolution/
```

Both workflows use the XML manifest as the completion target. A task becomes
`completed` only when that target is met. If no progress is made before the
internal stall timeout, the task ends as `timed_out` instead of waiting forever.

## Project Layout

```text
run.py                         HTTP service entry point
run_mcp.py                     stdio MCP entry point
src/fa_server/                 API, task services, storage, and workers
src/fa_server_mcp/             MCP server and HTTP client
src/raw2bmp/                   RAW conversion integration
src/ep5_enhancement/           super-resolution integration
models/super_resolution/       model files
scripts/                       maintenance and automation scripts
docs/api.md                    HTTP API reference
docs/mcp_and_skill.md          MCP and Codex Skill setup
tests/                         test suite
```

Runtime data is stored in `data/rsync_data/` by default. General service logs
are written to `logs/fa_server.log`; processor and purge logs are kept in
`log/`.

## Requirements

- Python 3.10 or newer
- A local `rsync` executable
- Access to the configured rsync server
- Production RAW and super-resolution dependencies when deploying the real
  processing implementations

Create the virtual environment and install dependencies.

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

Linux:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

## Run the Service

```powershell
.\.venv\Scripts\python run.py
```

To accept connections from other machines on the LAN:

```powershell
$env:FA_HOST = "0.0.0.0"
.\.venv\Scripts\python run.py
```

Available endpoints after startup:

```text
Health check  http://127.0.0.1:5000/health
Swagger UI   http://127.0.0.1:5000/docs
OpenAPI      http://127.0.0.1:5000/openapi.json
```

Swagger UI is served from local static files and does not require internet
access.

## Configuration

Configuration is read from environment variables at startup.

| Variable | Default | Purpose |
| --- | --- | --- |
| `FA_HOST` | `127.0.0.1` | HTTP bind address |
| `FA_PORT` | `5000` | HTTP port |
| `FA_API_KEY` | `dev-api-key` | API key for `/api/v1/*` |
| `FA_AUTH_ENABLED` | `true` | Enable API key authentication |
| `FA_REMOTE_BASE` | `rsync://admin@172.24.22.29:8873/data` | rsync data root |
| `FA_LOCAL_ROOT` | `<project>/data/rsync_data` | local data root |
| `FA_RSYNC` | `rsync` | rsync command or absolute executable path |
| `FA_POLL_INTERVAL_SECONDS` | `30` | sync polling interval |
| `FA_RSYNC_TIMEOUT_SECONDS` | `3600` | timeout for one rsync execution |
| `FA_TASK_STALL_TIMEOUT_SECONDS` | `3600` | maximum time without task progress |
| `FA_RAW_EXTENSIONS` | `.raw,.bmp` | extensions registered for image processing |
| `FA_DATABASE_FILENAME` | `tasks.sqlite3` | per-folder task database name |
| `FA_SR_BATCH_SIZE` | `3` | default inference batch size |
| `FA_SR_POLL_INTERVAL_SECONDS` | `10` | super-resolution polling interval |
| `FA_SR_OUTPUT_DIRNAME` | `Super_Resolution` | inference output directory name |
| `FA_PURGE_ENABLED` | `true` | enable folder retention checks |
| `FA_PURGE_MAX_FOLDERS` | `10` | number of task folders to retain |
| `FA_PURGE_INTERVAL_SECONDS` | `86400` | retention check interval |

`FA_TASK_STALL_TIMEOUT_SECONDS` is an internal service setting, not an API
request field. Sync progress means a new file was discovered; super-resolution
progress means a batch completed successfully.

## API

The main routes are:

```text
POST /api/v1/sync/tasks/{folder_name}
POST /api/v1/sync/tasks/{folder_name}/updates
GET  /api/v1/sync/jobs/{job_id}

POST /api/v1/super-resolution/tasks
GET  /api/v1/super-resolution/tasks/{job_id}
```

An initial sync task is unique per folder. Use the update route to check an
existing folder for new files. The service also prevents parallel active tasks
for the same folder where processing would conflict.

Send the API key with either header:

```text
X-API-Key: <api-key>
Authorization: Bearer <api-key>
```

See [docs/api.md](docs/api.md) for request fields, responses, and examples.

## Task Data and Status

`sync_jobs`, `sr_jobs`, and `image_tasks` are stored in the folder's
`tasks.sqlite3`. Typical task states are:

| Status | Meaning |
| --- | --- |
| `queued` | Waiting for a worker |
| `running` | Actively processing or waiting for manifest data |
| `completed` | XML completion target reached |
| `timed_out` | No progress before the internal timeout |
| `failed` | Processing stopped because of an error |

A `timed_out` task is not successful. Check `error_message`, the XML manifest,
and `image_counts` before starting another task.

## Logs

The main log rotates at 10 MB and keeps five backups:

```text
logs/fa_server.log
```

Follow it from PowerShell:

```powershell
Get-Content .\logs\fa_server.log -Wait
```

Or from Linux:

```bash
tail -f logs/fa_server.log
```

## Production Integrations

Production deployments must provide implementations compatible with these
boundaries:

- `src/raw2bmp/`: XML loading, RAW decoding, BMP output, and final naming.
- `src/ep5_enhancement/`: model loading and batched inference.
- `models/super_resolution/`: the model files used by the inference adapter.

Image completion is recorded against the input path. The server does not assume
that an output file name can be derived from the input name.

## Maintenance

Reset super-resolution records after stopping the service:

```powershell
.\.venv\Scripts\python scripts\reset_sr_records.py D:\data\folder\tasks.sqlite3
```

The script creates a backup by default and refuses to modify a database with an
active task. Run it with `--help` for override options.

Run the test suite:

```powershell
.\.venv\Scripts\python -m unittest discover -s tests
.\.venv\Scripts\python -m compileall src tests
```

MCP setup and usage are documented in
[docs/mcp_and_skill.md](docs/mcp_and_skill.md).
