from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class FaServerApiError(RuntimeError):
    status_code: int | None
    payload: dict[str, Any]

    def __str__(self) -> str:
        message = self.payload.get("error") or self.payload.get("message")
        if message:
            return str(message)
        if self.status_code is not None:
            return f"FA Server request failed with HTTP {self.status_code}"
        return "FA Server request failed"


class FaServerClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout_seconds: float = 30,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def health_check(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def create_sync_task(
        self,
        folder_name: str,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        folder = quote(folder_name, safe="/")
        return self._request(
            "POST",
            f"/api/v1/sync/tasks/{folder}",
            body=options,
        )

    def update_sync_task(
        self,
        folder_name: str,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        folder = quote(folder_name, safe="/")
        return self._request(
            "POST",
            f"/api/v1/sync/tasks/{folder}/updates",
            body=options,
        )

    def get_sync_job(
        self,
        job_id: str,
        *,
        folder_name: str,
        local_root: str | None = None,
        database_filename: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/v1/sync/jobs/{quote(job_id, safe='')}",
            query=_query_options(folder_name, local_root, database_filename),
        )

    def create_super_resolution_task(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/super-resolution/tasks",
            body=payload,
        )

    def get_super_resolution_job(
        self,
        job_id: str,
        *,
        folder_name: str,
        local_root: str | None = None,
        database_filename: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/v1/super-resolution/tasks/{quote(job_id, safe='')}",
            query=_query_options(folder_name, local_root, database_filename),
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"

        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key

        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return _decode_json_response(response.read())
        except HTTPError as exc:
            payload = _decode_error_payload(exc.read(), exc.reason)
            raise FaServerApiError(exc.code, payload) from exc
        except URLError as exc:
            raise FaServerApiError(
                None,
                {"error": f"cannot connect to FA Server: {exc.reason}"},
            ) from exc


def _query_options(
    folder_name: str,
    local_root: str | None,
    database_filename: str | None,
) -> dict[str, str]:
    query = {"folder_name": folder_name}
    if local_root:
        query["local_root"] = local_root
    if database_filename:
        query["database_filename"] = database_filename
    return query


def _decode_json_response(data: bytes) -> dict[str, Any]:
    if not data:
        return {}
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise FaServerApiError(
            None,
            {"error": "FA Server returned a non-object JSON response"},
        )
    return payload


def _decode_error_payload(data: bytes, reason: str) -> dict[str, Any]:
    try:
        payload = _decode_json_response(data)
    except (json.JSONDecodeError, UnicodeDecodeError, FaServerApiError):
        return {"error": str(reason)}
    return payload
