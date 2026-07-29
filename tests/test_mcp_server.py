from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import bootstrap
from fa_server_mcp.client import FaServerApiError, FaServerClient
from fa_server_mcp.server import (
    FaServerMcpServer,
    run_stdio,
    tool_definitions,
)


class FakeClient:
    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def health_check(self):
        self.calls.append(("health_check", None))
        return {"status": "ok"}

    def create_sync_task(self, folder_name, options):
        self.calls.append(("create_sync_task", (folder_name, options)))
        return {"job_id": "sync-job", "folder_name": folder_name}

    def update_sync_task(self, folder_name, options):
        self.calls.append(("update_sync_task", (folder_name, options)))
        return {"job_id": "update-job", "folder_name": folder_name}

    def get_sync_job(self, job_id, **options):
        self.calls.append(("get_sync_job", (job_id, options)))
        return {"job_id": job_id, "status": "completed"}

    def create_super_resolution_task(self, payload):
        self.calls.append(("create_super_resolution_task", payload))
        return {"job_id": "sr-job", "folder_name": payload["folder_name"]}

    def get_super_resolution_job(self, job_id, **options):
        self.calls.append(("get_super_resolution_job", (job_id, options)))
        return {"job_id": job_id, "status": "running"}


class FailingClient(FakeClient):
    def health_check(self):
        raise FaServerApiError(401, {"error": "valid API key required"})


class FaServerMcpServerTests(unittest.TestCase):
    def test_lists_expected_tools(self):
        names = {tool["name"] for tool in tool_definitions()}

        self.assertEqual(
            {
                "health_check",
                "create_sync_task",
                "update_sync_task",
                "get_sync_job",
                "create_super_resolution_task",
                "get_super_resolution_job",
            },
            names,
        )

    def test_create_sync_task_passes_folder_separately_from_payload(self):
        client = FakeClient()
        server = FaServerMcpServer(client)

        response = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_sync_task",
                    "arguments": {
                        "folder_name": "raw_test",
                        "enable_transcode_rename": True,
                    },
                },
            }
        )

        self.assertEqual(
            [
                (
                    "create_sync_task",
                    ("raw_test", {"enable_transcode_rename": True}),
                )
            ],
            client.calls,
        )
        self.assertEqual("sync-job", response["result"]["structuredContent"]["job_id"])

    def test_http_api_error_is_returned_as_tool_error(self):
        server = FaServerMcpServer(FailingClient())

        response = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "health_check",
                    "arguments": {},
                },
            }
        )

        result = response["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(401, result["structuredContent"]["status_code"])
        self.assertEqual(
            "valid API key required",
            result["structuredContent"]["error"],
        )

    def test_stdio_initializes_and_lists_tools(self):
        messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {},
            },
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            },
        ]
        input_stream = io.StringIO(
            "".join(json.dumps(message) + "\n" for message in messages)
        )
        output_stream = io.StringIO()

        run_stdio(
            FaServerMcpServer(FakeClient()),
            input_stream=input_stream,
            output_stream=output_stream,
        )

        responses = [
            json.loads(line)
            for line in output_stream.getvalue().splitlines()
        ]
        self.assertEqual(2, len(responses))
        self.assertEqual("fa-server", responses[0]["result"]["serverInfo"]["name"])
        self.assertEqual(6, len(responses[1]["result"]["tools"]))

    def test_http_client_builds_authenticated_update_request(self):
        response = MagicMock()
        response.read.return_value = b'{"job_id":"update-job"}'
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        client = FaServerClient(
            "http://127.0.0.1:5000/",
            "secret-key",
            timeout_seconds=12,
        )

        with patch(
            "fa_server_mcp.client.urlopen",
            return_value=response,
        ) as urlopen:
            payload = client.update_sync_task(
                "raw folder",
                {"poll_interval_seconds": 10},
            )

        request = urlopen.call_args.args[0]
        self.assertEqual("update-job", payload["job_id"])
        self.assertEqual(
            "http://127.0.0.1:5000/api/v1/sync/tasks/raw%20folder/updates",
            request.full_url,
        )
        self.assertEqual("POST", request.get_method())
        self.assertEqual("secret-key", request.get_header("X-api-key"))
        self.assertEqual(12, urlopen.call_args.kwargs["timeout"])

    def test_run_mcp_entrypoint_supports_stdio_handshake(self):
        project_root = Path(__file__).resolve().parents[1]
        message = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "test-client",
                        "version": "1.0.0",
                    },
                },
            }
        )

        completed = subprocess.run(
            [sys.executable, str(project_root / "run_mcp.py")],
            input=f"{message}\n",
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )

        response = json.loads(completed.stdout)
        self.assertEqual("fa-server", response["result"]["serverInfo"]["name"])
        self.assertEqual(
            "2024-11-05",
            response["result"]["protocolVersion"],
        )


if __name__ == "__main__":
    unittest.main()
