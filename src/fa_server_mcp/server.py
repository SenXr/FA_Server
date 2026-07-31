from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any, TextIO

from .client import FaServerApiError, FaServerClient

JSONRPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "fa-server"
SERVER_VERSION = "1.0.0"


@dataclass(frozen=True)
class McpConfig:
    base_url: str
    api_key: str
    timeout_seconds: float

    @classmethod
    def from_env(cls) -> "McpConfig":
        return cls(
            base_url=os.getenv(
                "FA_MCP_BASE_URL",
                "http://127.0.0.1:5000",
            ),
            api_key=os.getenv(
                "FA_MCP_API_KEY",
                os.getenv("FA_API_KEY", "dev-api-key"),
            ),
            timeout_seconds=float(os.getenv("FA_MCP_HTTP_TIMEOUT_SECONDS", "30")),
        )


class FaServerMcpServer:
    def __init__(self, client: FaServerClient):
        self.client = client

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if "method" not in message:
            return _error_response(message.get("id"), -32600, "Invalid Request")

        request_id = message.get("id")
        method = message["method"]
        params = message.get("params") or {}

        if request_id is None:
            return None

        try:
            if method == "initialize":
                return _result_response(
                    request_id,
                    {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {
                            "tools": {"listChanged": False},
                        },
                        "serverInfo": {
                            "name": SERVER_NAME,
                            "version": SERVER_VERSION,
                        },
                    },
                )
            if method == "ping":
                return _result_response(request_id, {})
            if method == "tools/list":
                return _result_response(request_id, {"tools": tool_definitions()})
            if method == "tools/call":
                return _result_response(request_id, self._call_tool(params))
        except ValueError as exc:
            return _error_response(request_id, -32602, str(exc))
        except Exception as exc:
            return _error_response(request_id, -32603, str(exc))

        return _error_response(request_id, -32601, f"Method not found: {method}")

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or not name:
            raise ValueError("tools/call requires a tool name")
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be an object")

        try:
            payload = self._execute_tool(name, arguments)
        except FaServerApiError as exc:
            error_payload = {
                "status_code": exc.status_code,
                **exc.payload,
            }
            return _tool_result(error_payload, is_error=True)
        return _tool_result(payload)

    def _execute_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if name == "health_check":
            return self.client.health_check()

        if name == "create_sync_task":
            folder_name = _required_string(arguments, "folder_name")
            return self.client.create_sync_task(
                folder_name,
                _without_keys(arguments, "folder_name"),
            )

        if name == "update_sync_task":
            folder_name = _required_string(arguments, "folder_name")
            return self.client.update_sync_task(
                folder_name,
                _without_keys(arguments, "folder_name"),
            )

        if name == "get_sync_job":
            return self.client.get_sync_job(
                _required_string(arguments, "job_id"),
                folder_name=_required_string(arguments, "folder_name"),
                local_root=_optional_string(arguments, "local_root"),
                database_filename=_optional_string(
                    arguments,
                    "database_filename",
                ),
            )

        if name == "create_super_resolution_task":
            _required_string(arguments, "folder_name")
            return self.client.create_super_resolution_task(arguments)

        if name == "get_super_resolution_job":
            return self.client.get_super_resolution_job(
                _required_string(arguments, "job_id"),
                folder_name=_required_string(arguments, "folder_name"),
                local_root=_optional_string(arguments, "local_root"),
                database_filename=_optional_string(
                    arguments,
                    "database_filename",
                ),
            )

        raise ValueError(f"unknown tool: {name}")


def tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": "health_check",
            "description": "Check whether the FA Server HTTP service is reachable.",
            "inputSchema": _object_schema({}),
        },
        {
            "name": "create_sync_task",
            "description": (
                "Create the one-time initial rsync task for a folder. "
                "Use update_sync_task when the folder already has an initial task."
            ),
            "inputSchema": _sync_task_schema(),
        },
        {
            "name": "update_sync_task",
            "description": (
                "Check and synchronize new or changed files for an existing folder."
            ),
            "inputSchema": _sync_task_schema(),
        },
        {
            "name": "get_sync_job",
            "description": "Get sync task status and per-image status counts.",
            "inputSchema": _job_query_schema(),
        },
        {
            "name": "create_super_resolution_task",
            "description": (
                "Create an incremental super-resolution task for prepared images "
                "in a folder task database."
            ),
            "inputSchema": _super_resolution_schema(),
        },
        {
            "name": "get_super_resolution_job",
            "description": (
                "Get super-resolution task status and per-image status counts."
            ),
            "inputSchema": _job_query_schema(),
        },
    ]


def _sync_task_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "folder_name": {
                "type": "string",
                "description": "Relative task folder name.",
            },
            "remote_base": {"type": "string"},
            "local_root": {"type": "string"},
            "rsync": {"type": "string"},
            "enable_transcode_rename": {"type": "boolean"},
            "poll_interval_seconds": {"type": "integer", "minimum": 0},
            "raw_extensions": {
                "type": "array",
                "items": {"type": "string"},
            },
            "rsync_timeout_seconds": {"type": "integer", "minimum": 1},
            "database_filename": {"type": "string"},
        },
        required=["folder_name"],
    )


def _super_resolution_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "folder_name": {
                "type": "string",
                "description": "Folder whose image_tasks table is processed.",
            },
            "local_root": {"type": "string"},
            "model_path": {
                "type": "string",
                "description": "Absolute model path on the FA Server host.",
            },
            "batch_size": {"type": "integer", "minimum": 1},
            "process_partial_batch": {"type": "boolean"},
            "poll_interval_seconds": {"type": "integer", "minimum": 0},
            "output_dirname": {"type": "string"},
            "database_filename": {"type": "string"},
        },
        required=["folder_name"],
    )


def _job_query_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "job_id": {"type": "string"},
            "folder_name": {"type": "string"},
            "local_root": {"type": "string"},
            "database_filename": {"type": "string"},
        },
        required=["job_id", "folder_name"],
    )


def _object_schema(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def _required_string(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value


def _optional_string(arguments: dict[str, Any], key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _without_keys(arguments: dict[str, Any], *keys: str) -> dict[str, Any]:
    excluded = set(keys)
    return {key: value for key, value in arguments.items() if key not in excluded}


def _tool_result(
    payload: dict[str, Any],
    *,
    is_error: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, indent=2),
            }
        ],
        "structuredContent": payload,
    }
    if is_error:
        result["isError"] = True
    return result


def _result_response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "result": result,
    }


def _error_response(
    request_id: Any,
    code: int,
    message: str,
) -> dict[str, Any]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": {
            "code": code,
            "message": message,
        },
    }


def run_stdio(
    server: FaServerMcpServer,
    *,
    input_stream: TextIO = sys.stdin,
    output_stream: TextIO = sys.stdout,
) -> None:
    for line in input_stream:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError("JSON-RPC message must be an object")
            response = server.handle_message(message)
        except (json.JSONDecodeError, ValueError) as exc:
            response = _error_response(None, -32700, str(exc))

        if response is not None:
            output_stream.write(
                json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            output_stream.flush()


def main() -> int:
    config = McpConfig.from_env()
    client = FaServerClient(
        config.base_url,
        config.api_key,
        timeout_seconds=config.timeout_seconds,
    )
    run_stdio(FaServerMcpServer(client))
    return 0
