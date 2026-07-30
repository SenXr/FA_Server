# FA Server 接口文档

本文档描述 FA Server 当前提供的 HTTP 接口。接口以 OpenAPI 为准，服务启动后也可以访问 Swagger 页面进行测试。

```text
Swagger:  /docs
OpenAPI:  /openapi.json
```

## 基础信息

默认服务地址：

```text
http://127.0.0.1:5000
```

局域网访问时，服务端需要设置：

```text
FA_HOST=0.0.0.0
```

所有 `/api/v1/*` 接口默认需要 API Key。可任选一种认证方式：

```http
X-API-Key: <api-key>
```

```http
Authorization: Bearer <api-key>
```

默认开发 API Key：

```text
dev-api-key
```

生产环境请通过 `FA_API_KEY` 设置。

## 通用约定

### folder_name

`folder_name` 是任务文件夹名称，也是同步任务和超分辨任务的主业务标识。

示例：

```text
raw_test
```

对应本地目录：

```text
<local_root>/raw_test
```

### local_root

`local_root` 是本地数据根目录。Windows 和 Linux 路径均支持。

推荐在 JSON 中使用 `/`：

```json
{
  "local_root": "D:/Agent/ChatAgent/PWQ/FA_Server/data/rsync_data"
}
```

如果使用 Windows 反斜杠，需要写成 JSON 转义形式：

```json
{
  "local_root": "D:\\Agent\\ChatAgent\\PWQ\\FA_Server\\data\\rsync_data"
}
```

### 任务状态

同步任务状态：

```text
queued
running
completed
partially_completed
failed
```

`partially_completed` 表示任务已结束，但根据 `raw_file_manifest.xml` 解析出的需同步数量大于实际已同步数量。常见场景是：源端文件缺失或长时间未产生新文件，任务触发空闲超时后结束。

超分辨任务状态：

```text
queued
running
completed
partially_completed
failed
```

超分辨任务只有在全部图片状态均为 `done` 且同步任务已停止时才会返回
`completed`。达到空闲超时后仍存在 `pending_conversion`、`blocked`、
`pending`、`processing` 或 `failed` 图片时，任务结束为
`partially_completed`，具体数量通过 `image_counts` 返回。

### SQLite 任务表

每个任务目录会维护独立 SQLite 文件：

```text
<local_root>/<folder_name>/tasks.sqlite3
```

同步任务会写入 `sync_jobs` 和 `image_tasks`，超分辨任务会写入 `sr_jobs` 并更新 `image_tasks` 的超分辨状态。

## 健康检查

```http
GET /health
```

不需要认证。

响应示例：

```json
{
  "status": "ok"
}
```

## 创建同步任务

```http
POST /api/v1/sync/tasks/{folder_name}
```

用途：从远端 rsync 目录同步指定文件夹，并按配置执行 RAW 转 BMP 与重命名。

同一个 `folder_name` 的初始同步任务只允许创建一次。重复创建会返回 `409`。

单次 rsync 长时间运行时，服务会以不超过 5 秒的间隔扫描已经完成落盘的
文件，并与网络传输并行执行 RAW 转换。RAW 文件仍按单文件顺序处理，以限制
大图解码时的内存峰值。任务运行期间保留 RAW 文件，避免下一轮 rsync 重复
传输；任务正常结束后再删除已成功处理的 RAW 和中间文件。

路径参数：

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `folder_name` | string | 是 | 任务文件夹名称 |

请求体：

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `remote_base` | string | `FA_REMOTE_BASE` | rsync 远端根路径 |
| `local_root` | string | `FA_LOCAL_ROOT` | 本地数据根目录 |
| `rsync` | string | `FA_RSYNC` | rsync 命令或绝对路径 |
| `enable_transcode_rename` | boolean | `true` | 是否启用 RAW 转 BMP 与重命名 |
| `idle_timeout_seconds` | integer | `600` | 无新增文件后的结束超时时间 |
| `poll_interval_seconds` | integer | `30` | 同步轮询间隔 |
| `raw_extensions` | string[] | `[".raw"]` | 待发现的数据扩展名 |
| `rsync_timeout_seconds` | integer | `3600` | 单次 rsync 命令超时 |
| `database_filename` | string | `tasks.sqlite3` | SQLite 文件名 |

请求示例：

```json
{
  "remote_base": "rsync://admin@172.24.22.29:8873/data",
  "local_root": "D:/Agent/ChatAgent/PWQ/FA_Server/data/rsync_data",
  "rsync": "rsync",
  "enable_transcode_rename": true,
  "idle_timeout_seconds": 600,
  "poll_interval_seconds": 30
}
```

响应示例：

```json
{
  "job_id": "3efb01d94fd04590bb0558f6f914a2d1",
  "folder_name": "raw_test",
  "database_path": "D:\\Agent\\ChatAgent\\PWQ\\FA_Server\\data\\rsync_data\\raw_test\\tasks.sqlite3",
  "status_url": "/api/v1/sync/jobs/3efb01d94fd04590bb0558f6f914a2d1?folder_name=raw_test"
}
```

CMD 示例：

```bat
curl -X POST "http://127.0.0.1:5000/api/v1/sync/tasks/raw_test" ^
  -H "X-API-Key: dev-api-key" ^
  -H "Content-Type: application/json" ^
  -d "{\"remote_base\":\"rsync://admin@172.24.22.29:8873/data\",\"local_root\":\"D:/Agent/ChatAgent/PWQ/FA_Server/data/rsync_data\",\"rsync\":\"rsync\",\"enable_transcode_rename\":true}"
```

## 更新同步任务

```http
POST /api/v1/sync/tasks/{folder_name}/updates
```

用途：对已有目标文件夹再次执行同步，用于检查并更新新增或变化文件。

该接口允许 `folder_name` 已存在，但同一文件夹同一时间只能有一个活跃同步任务。

请求体字段与创建同步任务一致。

响应示例：

```json
{
  "job_id": "b0f9c8c9b21d4c10a01c67f6382bd2aa",
  "folder_name": "raw_test",
  "database_path": "D:\\Agent\\ChatAgent\\PWQ\\FA_Server\\data\\rsync_data\\raw_test\\tasks.sqlite3",
  "status_url": "/api/v1/sync/jobs/b0f9c8c9b21d4c10a01c67f6382bd2aa?folder_name=raw_test"
}
```

## 查询同步任务

```http
GET /api/v1/sync/jobs/{job_id}
```

查询参数：

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `folder_name` | string | 是 | 任务文件夹名称 |
| `local_root` | string | 否 | 本地数据根目录，不传则使用服务默认配置 |
| `database_filename` | string | 否 | SQLite 文件名 |

响应示例：

```json
{
  "job_id": "3efb01d94fd04590bb0558f6f914a2d1",
  "folder_name": "raw_test",
  "job_kind": "initial",
  "status": "partially_completed",
  "remote_url": "rsync://admin@172.24.22.29:8873/data/raw_test/",
  "local_dir": "D:\\Agent\\ChatAgent\\PWQ\\FA_Server\\data\\rsync_data\\raw_test",
  "required_file_count": 100,
  "synced_file_count": 99,
  "image_counts": {
    "pending": 99
  },
  "transcode_rename_enabled": 1,
  "idle_timeout_seconds": 600,
  "poll_interval_seconds": 30,
  "created_at": "2026-07-13T07:23:12.624171+00:00",
  "started_at": "2026-07-13T07:23:12.635281+00:00",
  "finished_at": "2026-07-13T07:42:13.368745+00:00",
  "last_new_file_at": "2026-07-13T07:32:13.368745+00:00",
  "error_message": null
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `required_file_count` | 从 `raw_file_manifest.xml` 解析出的需同步文件数。没有 manifest 时为 `null` |
| `synced_file_count` | 当前任务发现并写入任务表的数据文件数 |
| `image_counts` | 按超分辨状态统计的图片任务数量 |
| `status=completed` | manifest 已满足，或无 manifest 时空闲超时结束 |
| `status=partially_completed` | 有 manifest，且空闲超时结束时已同步数小于需同步数 |

CMD 示例：

```bat
curl -X GET "http://127.0.0.1:5000/api/v1/sync/jobs/3efb01d94fd04590bb0558f6f914a2d1?folder_name=raw_test" ^
  -H "X-API-Key: dev-api-key"
```

## 创建超分辨任务

```http
POST /api/v1/super-resolution/tasks
```

用途：读取指定任务目录中的 SQLite `image_tasks` 增量数据，以批次方式调用超分辨模型。

默认输出目录：

```text
<local_root>/<folder_name>/Super_Resolution
```

请求体：

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `folder_name` | string | 无 | 必填，任务文件夹名称 |
| `local_root` | string | `FA_LOCAL_ROOT` | 本地数据根目录 |
| `model_path` | string | 项目默认模型 | 超分辨模型的绝对路径，传给 `ep5_enhancement.load_model()` |
| `batch_size` | integer | `3` | 每批传入模型的图片数量 |
| `process_partial_batch` | boolean | `true` | 是否处理不足一个 batch 的尾批 |
| `idle_timeout_seconds` | integer | `600` | 无新增可处理图片后的结束超时时间 |
| `poll_interval_seconds` | integer | `10` | 扫描任务表间隔 |
| `output_dirname` | string | `Super_Resolution` | 输出子目录名称 |
| `database_filename` | string | `tasks.sqlite3` | SQLite 文件名 |

请求示例：

```json
{
  "folder_name": "raw_test",
  "local_root": "D:/Agent/ChatAgent/PWQ/FA_Server/data/rsync_data",
  "model_path": "D:/models/super_resolution/best_model.pth",
  "batch_size": 3,
  "process_partial_batch": true,
  "idle_timeout_seconds": 600,
  "poll_interval_seconds": 10,
  "output_dirname": "Super_Resolution"
}
```

响应示例：

```json
{
  "job_id": "640944458d584de5bde63dbaa1a5e83d",
  "folder_name": "raw_test",
  "database_path": "D:\\Agent\\ChatAgent\\PWQ\\FA_Server\\data\\rsync_data\\raw_test\\tasks.sqlite3",
  "status_url": "/api/v1/super-resolution/tasks/640944458d584de5bde63dbaa1a5e83d?folder_name=raw_test"
}
```

CMD 示例：

```bat
curl -X POST "http://127.0.0.1:5000/api/v1/super-resolution/tasks" ^
  -H "X-API-Key: dev-api-key" ^
  -H "Content-Type: application/json" ^
  -d "{\"folder_name\":\"raw_test\",\"local_root\":\"D:/Agent/ChatAgent/PWQ/FA_Server/data/rsync_data\",\"model_path\":\"D:/models/super_resolution/best_model.pth\",\"batch_size\":3,\"process_partial_batch\":true}"
```

## 查询超分辨任务

```http
GET /api/v1/super-resolution/tasks/{job_id}
```

查询参数：

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `folder_name` | string | 是 | 任务文件夹名称 |
| `local_root` | string | 否 | 本地数据根目录 |
| `database_filename` | string | 否 | SQLite 文件名 |

响应示例：

```json
{
  "job_id": "640944458d584de5bde63dbaa1a5e83d",
  "folder_name": "raw_test",
  "status": "completed",
  "batch_size": 3,
  "process_partial_batch": 1,
  "output_dir": "D:\\Agent\\ChatAgent\\PWQ\\FA_Server\\data\\rsync_data\\raw_test\\Super_Resolution",
  "processed_file_count": 99,
  "image_counts": {
    "done": 99
  },
  "idle_timeout_seconds": 600,
  "poll_interval_seconds": 10,
  "created_at": "2026-07-13T07:30:18.138889+00:00",
  "started_at": "2026-07-13T07:30:18.150016+00:00",
  "finished_at": "2026-07-13T07:34:40.011000+00:00",
  "error_message": null
}
```

`status=partially_completed` 表示任务已经停止等待，但仍有图片未成功完成。
调用方应检查 `image_counts`，处理其中的 `blocked`、`pending`、
`processing` 或 `failed` 数据后再决定是否重试。

CMD 示例：

```bat
curl -X GET "http://127.0.0.1:5000/api/v1/super-resolution/tasks/640944458d584de5bde63dbaa1a5e83d?folder_name=raw_test" ^
  -H "X-API-Key: dev-api-key"
```

## 常见错误

### 401

未提供有效 API Key。

```json
{
  "error": "valid API key required"
}
```

### 400

请求参数错误。例如 `folder_name` 缺失、JSON 格式错误、`batch_size <= 0`。

### 404

任务不存在，或查询时传入了错误的 `folder_name` / `local_root` / `database_filename`。

### 409

同一文件夹存在活跃任务，或初始同步任务重复创建。

响应示例：

```json
{
  "error": "active sync job already exists for folder 'raw_test': 3efb01d94fd04590bb0558f6f914a2d1",
  "existing_job_id": "3efb01d94fd04590bb0558f6f914a2d1"
}
```

## 调用顺序建议

1. 调用 `POST /api/v1/sync/tasks/{folder_name}` 创建同步任务。
2. 轮询 `GET /api/v1/sync/jobs/{job_id}`。
3. 同步任务为 `completed` 时，创建超分辨任务。
4. 同步任务为 `partially_completed` 时，先检查缺失文件是否符合预期，再决定是否创建超分辨任务。
5. 调用 `POST /api/v1/super-resolution/tasks` 创建超分辨任务。
6. 轮询 `GET /api/v1/super-resolution/tasks/{job_id}`。
