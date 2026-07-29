# FA Server MCP Workflow Reference

## Tool Inputs

### `create_sync_task` and `update_sync_task`

Required:

- `folder_name`: relative task folder name.

Optional:

- `remote_base`
- `local_root`
- `rsync`
- `enable_transcode_rename`
- `idle_timeout_seconds`
- `poll_interval_seconds`
- `raw_extensions`
- `rsync_timeout_seconds`
- `database_filename`

`raw_extensions` controls local discovery and task registration. It does not
currently prevent rsync from transferring other remote files.

### `get_sync_job`

Required:

- `job_id`
- `folder_name`

Optional:

- `local_root`
- `database_filename`

Important response fields:

- `status`
- `required_file_count`
- `synced_file_count`
- `image_counts`
- `error_message`

### `create_super_resolution_task`

Required:

- `folder_name`

Optional:

- `local_root`
- `model_path`: absolute path on the FA Server host.
- `batch_size`
- `process_partial_batch`
- `idle_timeout_seconds`
- `poll_interval_seconds`
- `output_dirname`
- `database_filename`

### `get_super_resolution_job`

Required:

- `job_id`
- `folder_name`

Optional:

- `local_root`
- `database_filename`

Important response fields:

- `status`
- `processed_file_count`
- `image_counts`
- `output_dir`
- `model_path`
- `error_message`

## State Interpretation

Sync terminal states:

- `completed`: expected data is available or the fallback completion rule was met.
- `partially_completed`: the task ended but manifest and synchronized counts differ.
- `failed`: execution failed; inspect `error_message`.

Super-resolution terminal states:

- `completed`: the task has stopped normally.
- `failed`: inference or task execution failed.

Image counts may contain:

- `pending_conversion`
- `blocked`
- `pending`
- `processing`
- `done`
- `failed`

Always report nonzero `blocked`, `processing`, or `failed` counts when explaining
why a task is not complete.

## Error Handling

| HTTP status | Meaning | Action |
| --- | --- | --- |
| 400 | Invalid path, JSON, or task parameter | Correct the request; do not retry unchanged |
| 401 | Missing or invalid API Key | Ask the operator to verify MCP environment configuration |
| 404 | Job or task database not found | Verify `job_id`, `folder_name`, `local_root`, and database filename |
| 409 | Duplicate or active task exists | Report and reuse `existing_job_id`; do not create a parallel task |
| 500 | Unhandled server error | Report error details and inspect FA Server logs |

## Recommended Sequence

```text
health_check
  -> create_sync_task or update_sync_task
  -> get_sync_job
  -> completed?
       yes -> create_super_resolution_task
       no  -> report partial/failed state
  -> get_super_resolution_job
  -> report final status and image counts
```
