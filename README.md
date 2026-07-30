# FA Server

FA Server 是一个基于 Flask 的文件夹级任务服务，用于在一台服务器上统一管理两类流程：

1. 基于 `rsync` 的增量数据同步任务。
2. 基于 SQLite 任务表的超分辨增强任务。

服务按 `folder_name` 作为任务目录维度。每个任务目录维护自己的 SQLite 数据库：

```text
<local_root>/<folder_name>/tasks.sqlite3
```

默认数据根目录为：

```text
<project>/data/rsync_data
```

## 功能概览

- 同步服务：调用本机 `rsync` 从远端拉取指定文件夹数据。
- 流水线处理：长时间 rsync 运行期间，持续发现已落盘文件并执行 RAW 处理。
- RAW 处理：同步后可默认触发 RAW 转 BMP 与重命名流程。
- 任务完成：优先根据 `raw_file_manifest.xml` 判断是否全部完成；若长时间无新增文件，则按超时兜底结束。
- 部分完成：若超时结束时已同步数小于 manifest 需同步数，同步任务状态为 `partially_completed`。
- 超分辨服务：读取任务目录 SQLite 中的待处理图片，以批次方式调用模型推理。
- 模型加载：超分辨模型在服务进程内只加载一次。
- 内存控制：rsync 输出不驻留内存，RAW 明细及时释放，模型推理串行执行。
- 完成状态：超分辨空闲超时时仍有未完成图片时返回 `partially_completed`。
- 清理服务：服务启动时检查一次数据目录，之后默认每天检查一次，保留最新的 10 个任务文件夹。
- 离线 Swagger：`/docs` 使用本地静态资源，不依赖公网 CDN。

## 项目结构

```text
run.py                         服务启动入口
run_mcp.py                     FA Server stdio MCP 启动入口
src/fa_server/                 Flask API、任务服务、SQLite 存储、后台执行器
src/fa_server_mcp/             MCP 协议服务与 FA Server HTTP 客户端
src/raw2bmp/                   RAW 转 BMP 与 XML 解析对接模块
src/ep5_enhancement/           超分辨算法对接模块
skills/fa-server-operations/   Codex 任务操作 Skill
models/super_resolution/       超分辨模型文件目录
data/rsync_data/               默认任务数据目录
log/                           RAW 处理、purge 等专项日志目录
logs/fa_server.log             接口服务统一主日志
docs/api.md                    HTTP 接口文档
tests/                         单元测试
```

## 环境准备

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

Linux：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

生产环境需要确保本机可直接调用 `rsync`。如果不是系统 PATH 中的 `rsync`，可通过 `FA_RSYNC` 指定绝对路径。

## 启动服务

本机测试：

```powershell
.\.venv\Scripts\python run.py
```

局域网访问时，需要监听所有网卡：

```powershell
$env:FA_HOST = "0.0.0.0"
.\.venv\Scripts\python run.py
```

启动后可访问：

```text
健康检查: http://127.0.0.1:5000/health
Swagger:  http://127.0.0.1:5000/docs
OpenAPI:  http://127.0.0.1:5000/openapi.json
```

如果从另一台服务器访问，请将 `127.0.0.1` 替换为服务所在机器的 IP。

## 关键配置

配置通过环境变量传入：

```text
FA_HOST=127.0.0.1
FA_PORT=5000
FA_REMOTE_BASE=rsync://admin@172.24.22.29:8873/data
FA_LOCAL_ROOT=<project>/data/rsync_data
FA_RSYNC=rsync
FA_API_KEY=dev-api-key
FA_DATABASE_FILENAME=tasks.sqlite3
FA_DEBUG=false
```

同步任务相关：

```text
FA_IDLE_TIMEOUT_SECONDS=600
FA_POLL_INTERVAL_SECONDS=30
FA_RSYNC_TIMEOUT_SECONDS=3600
FA_RAW_EXTENSIONS=.raw,.bmp
```

超分辨任务相关：

```text
FA_SR_BATCH_SIZE=3
FA_SR_POLL_INTERVAL_SECONDS=10
FA_SR_IDLE_TIMEOUT_SECONDS=600
FA_SR_OUTPUT_DIRNAME=Super_Resolution
```

清理服务相关：

```text
FA_PURGE_ENABLED=true
FA_PURGE_MAX_FOLDERS=10
FA_PURGE_INTERVAL_SECONDS=86400
FA_PURGE_LOG_FILENAME=log/purge.log
```

## 服务日志

执行 `python run.py` 后，接口访问记录、后台同步任务异常、超分辨任务异常和
RAW 单文件处理异常会统一写入：

```text
logs/fa_server.log
```

日志达到 10 MB 后自动滚动，默认保留 5 个历史文件。控制台仍会同步显示日志。
后台任务异常会记录 `job_id`、`folder_name` 和完整 traceback，便于从接口返回的
`error_message` 继续定位根因。

Windows PowerShell 实时查看：

```powershell
Get-Content .\logs\fa_server.log -Wait
```

Linux 实时查看：

```bash
tail -f logs/fa_server.log
```

## 数据目录约定

一个典型同步目录如下：

```text
data/rsync_data/raw_test/
  raw_file_manifest.xml
  test_001T.bmp
  test_002T.bmp
  tasks.sqlite3
  Super_Resolution/
    test_001T_sr.bmp
    test_002T_sr.bmp
```

开启 `enable_transcode_rename=true` 时，同步服务只对 `.raw` 文件做转码与重命名。最终保留的目标产物是类似 `test_001T.bmp` 的文件。

## API 文档

接口说明见：

[docs/api.md](docs/api.md)

MCP 与 Codex Skill 的配置和使用说明见：

[docs/mcp_and_skill.md](docs/mcp_and_skill.md)

Swagger 测试页面：

```text
http://127.0.0.1:5000/docs
```

所有 `/api/v1/*` 接口默认需要 API Key，可使用以下任一形式：

```text
X-API-Key: <api-key>
Authorization: Bearer <api-key>
```

## 生产对接点

以下模块是后续替换真实生产代码的主要边界：

- `src/raw2bmp/`：对接实际 XML 解析、RAW 解码、BMP 输出和命名逻辑。
- `src/ep5_enhancement/`：对接实际 Unet 超分辨模型加载与批量推理逻辑。
- `models/super_resolution/`：放置真实模型文件。

当前仓库内的算法实现主要用于本地联调和流程验证。

## 重置超分辨记录

停止接口服务后，执行：

```powershell
.\.venv\Scripts\python scripts\reset_sr_records.py D:\data\folder\tasks.sqlite3
```

脚本会自动备份数据库、删除 `sr_jobs`，并将已完成转码的图片恢复为超分辨 `pending` 状态。检测到 `queued` 或 `running` 任务时会拒绝操作；确认原进程已停止后可使用 `--force`。如明确不需要备份，可使用 `--no-backup`。

## 测试

```powershell
.\.venv\Scripts\python -m unittest discover -s tests
```

编译检查：

```powershell
.\.venv\Scripts\python -m compileall src tests
```

MCP 服务测试：

```powershell
.\.venv\Scripts\python -m unittest tests.test_mcp_server
```
