from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from businessflow.api.finance_source_limits import (
    finance_source_request_body_limit,
    multipart_upload_request_body_limit,
)
from businessflow.api.forecasting_limits import MAX_FORECAST_DATASET_REQUEST_BODY_BYTES

MAX_UNIVERSAL_SYNC_REQUEST_BODY_BYTES = 4 * 1024


class UploadRequestBodyTooLarge(HTTPException):
    def __init__(self, message: str) -> None:
        super().__init__(status_code=413, detail=message)


@dataclass(frozen=True, slots=True)
class _UploadBodyLimit:
    max_bytes: int
    message: str


def _path_candidates(scope: Scope) -> set[str]:
    path = scope.get("path", "")
    paths = {path}
    root_path = scope.get("root_path", "").rstrip("/")
    if root_path and path.startswith(root_path):
        paths.add(path[len(root_path) :] or "/")
    return paths


def _upload_body_limit(scope: Scope, max_file_bytes: int) -> _UploadBodyLimit | None:
    if scope.get("method") != "POST":
        return None

    for candidate in _path_candidates(scope):
        normalized = candidate[:-1] if candidate.endswith("/") else candidate
        if not normalized.startswith("/") or normalized.startswith("//"):
            continue
        parts = normalized[1:].split("/")
        tenant_prefix = parts[:3] == ["api", "v1", "tenants"]
        if not tenant_prefix or len(parts) < 4 or not parts[3]:
            continue

        if len(parts) == 5 and parts[4] == "imports":
            return _UploadBodyLimit(
                multipart_upload_request_body_limit(max_file_bytes),
                "The import upload request exceeds the configured size limit.",
            )
        if (
            len(parts) == 9
            and parts[4:7] == ["company-data", "universal", "sources"]
            and parts[7]
            and parts[8] == "ingestions"
        ):
            return _UploadBodyLimit(
                multipart_upload_request_body_limit(max_file_bytes),
                "The Universal Data upload request exceeds the configured size limit.",
            )
        if (
            len(parts) == 9
            and parts[4:7] == ["company-data", "universal", "sources"]
            and parts[7]
            and parts[8] == "sync"
        ):
            return _UploadBodyLimit(
                MAX_UNIVERSAL_SYNC_REQUEST_BODY_BYTES,
                "The Universal Data sync request exceeds the configured size limit.",
            )
        if (
            len(parts) == 9
            and parts[4:7] == ["company-data", "universal", "sources"]
            and parts[7]
            and parts[8] == "fixture-sync"
        ):
            return _UploadBodyLimit(
                MAX_UNIVERSAL_SYNC_REQUEST_BODY_BYTES,
                "The Universal Data fixture-sync request exceeds the configured size limit.",
            )
        if (
            len(parts) == 8
            and parts[4:6] == ["company-data", "customer-commitments"]
            and parts[6]
            and parts[7] == "evidence"
        ):
            evidence_file_limit = min(max_file_bytes, 10 * 1024 * 1024)
            return _UploadBodyLimit(
                multipart_upload_request_body_limit(evidence_file_limit),
                "The customer commitment evidence request exceeds the 10 MiB file limit.",
            )
        if len(parts) == 6 and parts[4:] == ["position-reviews", "financial-imports"]:
            return _UploadBodyLimit(
                multipart_upload_request_body_limit(max_file_bytes),
                "The M10 financial import request exceeds the configured size limit.",
            )
        if len(parts) == 6 and parts[4:] == ["finance-sources", "intakes"]:
            return _UploadBodyLimit(
                finance_source_request_body_limit(max_file_bytes),
                "The finance source intake request exceeds the configured size limit.",
            )
        if len(parts) == 6 and parts[4:] == ["core-financials", "imports"]:
            return _UploadBodyLimit(
                finance_source_request_body_limit(max_file_bytes),
                "The Core Financials import request exceeds the configured size limit.",
            )
        if len(parts) == 6 and parts[4:] == ["forecasts", "datasets"]:
            return _UploadBodyLimit(
                MAX_FORECAST_DATASET_REQUEST_BODY_BYTES,
                "The forecast dataset request exceeds the 32 MiB limit.",
            )
    return None


def _declared_content_length(scope: Scope, max_body_bytes: int) -> int | None:
    values = [
        value for name, value in scope.get("headers", []) if name.lower() == b"content-length"
    ]
    if len(values) != 1 or not values[0] or not values[0].isdigit():
        return None

    normalized = values[0].lstrip(b"0") or b"0"
    maximum = str(max_body_bytes).encode("ascii")
    if len(normalized) > len(maximum) or (len(normalized) == len(maximum) and normalized > maximum):
        return max_body_bytes + 1
    return int(normalized)


async def _send_too_large(
    scope: Scope,
    receive: Receive,
    send: Send,
    message: str,
) -> None:
    state = scope.setdefault("state", {})
    request_id = state.get("request_id")
    if not isinstance(request_id, UUID):
        request_id = uuid4()
        state["request_id"] = request_id
    request_id_text = str(request_id)
    response = JSONResponse(
        status_code=413,
        content={
            "error": {
                "code": "request_body_too_large",
                "message": message,
                "request_id": request_id_text,
            }
        },
        headers={"X-Request-ID": request_id_text},
    )
    await response(scope, receive, send)


class UploadRequestBodyLimitMiddleware:
    """Bound selected multipart and JSON request bodies while streaming them through ASGI."""

    def __init__(self, app: ASGIApp, *, max_file_bytes: int) -> None:
        self.app = app
        self.max_file_bytes = max_file_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        body_limit = _upload_body_limit(scope, self.max_file_bytes)
        if body_limit is None:
            await self.app(scope, receive, send)
            return

        content_length = _declared_content_length(scope, body_limit.max_bytes)
        if content_length is not None and content_length > body_limit.max_bytes:
            await _send_too_large(scope, receive, send, body_limit.message)
            return

        received_bytes = 0
        response_started = False

        async def receive_with_limit() -> Message:
            nonlocal received_bytes
            # ASGI receive yields the complete next chunk before it can be counted. Earlier
            # chunks may already have reached Starlette's parser; never forward the chunk that
            # would take the cumulative body beyond the cap.
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > body_limit.max_bytes:
                    raise UploadRequestBodyTooLarge(body_limit.message)
            return message

        async def track_response_start(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive_with_limit, track_response_start)
        except UploadRequestBodyTooLarge:
            if response_started:
                raise
            await _send_too_large(scope, receive, send, body_limit.message)
