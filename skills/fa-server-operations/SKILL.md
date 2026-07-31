---
name: fa-server-operations
description: Operate and monitor FA Server folder workflows through the configured fa-server MCP tools. Use when a user asks to check FA Server health, create or update an rsync folder task, inspect sync progress, start super-resolution processing, inspect image status counts, or diagnose an API task failure.
---

# FA Server Operations

Use the `fa-server` MCP tools as the authoritative interface. Do not edit
`tasks.sqlite3` or invoke internal Python services directly unless the user
explicitly requests local maintenance.

## Run A Folder Workflow

1. Call `health_check`.
2. Determine whether the user needs an initial sync or an update:
   - Call `create_sync_task` only for a folder that has never had an initial task.
   - Call `update_sync_task` for an existing folder.
3. Preserve `job_id`, `folder_name`, `local_root`, and `database_filename` from
   the request or response. They are required to query the correct database.
4. Call `get_sync_job` when the user asks for status or when the next processing
   stage depends on sync completion.
5. Interpret the sync terminal state:
   - `completed`: continue when super-resolution was requested.
   - `failed`: stop and report `error_message`.
6. Call `create_super_resolution_task` only after the relevant inputs are ready.
   Pass `model_path` when the user specifies a production model.
7. Call `get_super_resolution_job` to report progress and image status counts.
8. Report the job IDs and terminal outcomes. Keep paths exactly as returned.

## Apply Operational Guardrails

- Do not invent `folder_name`, server paths, model paths, or API keys.
- Do not submit duplicate initial sync tasks to work around HTTP 409.
- When a tool returns 409, report `existing_job_id` and inspect that job when
  enough query context is available.
- Treat `queued` and `running` as non-terminal states.
- Do not infer super-resolution output names from input names. The service
  records completion against input images.
- Ask before repeatedly polling a task unless the user requested monitoring.
- Surface HTTP status, `error`, `error_message`, and `image_counts` in failure
  reports; do not hide failed or blocked image counts.

## Use The Right Tool

| User intent | Tool |
| --- | --- |
| Check connectivity | `health_check` |
| First synchronization of a folder | `create_sync_task` |
| Check an existing folder for changes | `update_sync_task` |
| Inspect synchronization status | `get_sync_job` |
| Start image enhancement | `create_super_resolution_task` |
| Inspect enhancement status | `get_super_resolution_job` |

Read [references/api-workflows.md](references/api-workflows.md) when constructing
non-default requests, interpreting status fields, or diagnosing errors.
