# FA Server MCP 与 Skill 使用说明

FA Server MCP 将现有 HTTP API 包装为标准 MCP 工具。MCP 不直接访问
SQLite，也不复制同步或图像处理逻辑，所有业务状态仍以 FA Server 为准。

配套 Skill 位于：

```text
skills/fa-server-operations/
```

## 1. MCP 工具

| 工具 | 作用 |
| --- | --- |
| `health_check` | 检查 FA Server 是否可访问 |
| `create_sync_task` | 创建文件夹初始同步任务 |
| `update_sync_task` | 更新已有文件夹 |
| `get_sync_job` | 查询同步任务及图片状态 |
| `create_super_resolution_task` | 创建超分辨任务 |
| `get_super_resolution_job` | 查询超分辨任务及图片状态 |

## 2. 运行配置

MCP 使用以下环境变量：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `FA_MCP_BASE_URL` | `http://127.0.0.1:5000` | FA Server HTTP 地址 |
| `FA_MCP_API_KEY` | `FA_API_KEY` 或 `dev-api-key` | 调用接口使用的 API Key |
| `FA_MCP_HTTP_TIMEOUT_SECONDS` | `30` | 单次 HTTP 请求超时 |

先启动 FA Server：

```powershell
.\.venv\Scripts\python.exe run.py
```

MCP stdio 入口为：

```powershell
.\.venv\Scripts\python.exe run_mcp.py
```

直接运行后进程等待标准输入属于正常现象，通常应由 MCP 客户端启动。

## 3. Codex MCP 配置

在 Codex MCP 配置中增加以下内容，并将路径和 API Key 替换为实际值：

```toml
[mcp_servers.fa-server]
command = "D:\\Agent\\ChatAgent\\PWQ\\FA_Server\\.venv\\Scripts\\python.exe"
args = ["D:\\Agent\\ChatAgent\\PWQ\\FA_Server\\run_mcp.py"]

[mcp_servers.fa-server.env]
FA_MCP_BASE_URL = "http://127.0.0.1:5000"
FA_MCP_API_KEY = "replace-with-production-api-key"
FA_MCP_HTTP_TIMEOUT_SECONDS = "30"
```

FA Server 位于其他服务器时，将 `FA_MCP_BASE_URL` 改为对应局域网地址。
MCP 所在机器必须能够访问该 HTTP 地址。

## 4. Skill 安装

项目内 Skill 源目录为：

```text
skills/fa-server-operations
```

需要全局使用时，将整个目录复制到 Codex Skills 目录：

```powershell
Copy-Item -Recurse `
  .\skills\fa-server-operations `
  $env:USERPROFILE\.codex\skills\fa-server-operations
```

重启或重新加载 Codex 后，可通过 `$fa-server-operations` 显式调用，也可在
提出 FA Server 同步、超分辨和状态查询请求时由 Codex 自动选择。

## 5. 推荐调用流程

```text
health_check
  -> create_sync_task 或 update_sync_task
  -> get_sync_job
  -> 同步 completed
  -> create_super_resolution_task
  -> get_super_resolution_job
  -> 汇报最终状态与 image_counts
```

遇到以下状态时不应继续自动提交后续任务：

- 同步任务仍为 `queued` 或 `running`。
- 任一任务为 `timed_out`，且 XML 目标仍未满足。
- 任一任务为 `failed`。
- 图片状态存在需要人工排查的 `blocked` 或 `failed`。
- 接口返回 HTTP 409，表示同一文件夹已有重复或活跃任务。

## 6. 安全边界

- MCP 配置中不要提交生产 API Key 到 Git。
- 生产 API Key 应通过运行环境或密钥管理系统注入。
- MCP 只调用 HTTP API，不提供直接删除目录、修改 SQLite 或重置任务的工具。
- `model_path` 和 `local_root` 是 FA Server 主机上的路径，不是 MCP 客户端路径。
- 超分辨结果按输入图片记账，不应推测生产输出文件名。

## 7. 验证

运行 MCP 测试：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_mcp_server
```

运行项目全量测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```
