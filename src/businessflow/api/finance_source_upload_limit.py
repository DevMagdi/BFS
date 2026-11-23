from __future__ import annotations

from uuid import UUID, uuid4

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from businessflow.api.finance_source_limits import finance_source_request_body_limit


class FinanceSourceRequestBodyTooLarge(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=413, detail="The finance source intake request is too large.")


def _is_finance_source_intake(scope: Scope) -> bool:
    if scope.get("method") != "POST":
        return False

    path = scope.get("path", "")
    paths = {path}
    root_path = scope.get("root_path", "").rstrip("/")
    if root_path and path.startswith(root_path):
        paths.add(path[len(root_path) :] or "/")

    for candidate in paths:
        normalized = candidate[:-1] if candidate.endswith("/") else candidate
        if not normalized.startswith("/") or normalized.startswith("//"):
            continue
        parts = normalized[1:].split("/")
        if (
            len(parts) == 6
            and parts[:3] == ["api", "v1", "tenants"]
            and parts[3]
            and parts[4:] == ["finance-sources", "intakes"]
        ):
            return True
    return False


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


async def _send_too_large(scope: Scope, receive: Receive, send: Send) -> None:
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
                "message": "The finance source intake request exceeds the configured size limit.",
                "request_id": request_id_text,
            }
        },
        headers={"X-Request-ID": request_id_text},
    )
    await response(scope, receive, send)


class FinanceSourceUploadSizeLimitMiddleware:
    """Bound only the finance-source multipart request at ASGI body ingress."""

    def __init__(self, app: ASGIApp, *, max_file_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = finance_source_request_body_limit(max_file_bytes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _is_finance_source_intake(scope):
            await self.app(scope, receive, send)
            return

        content_length = _declared_content_length(scope, self.max_body_bytes)
        if content_length is not None and content_length > self.max_body_bytes:
            await _send_too_large(scope, receive, send)
            return

        received_bytes = 0
        response_started = False

        async def receive_with_limit() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_body_bytes:
                    raise FinanceSourceRequestBodyTooLarge
            return message

        async def track_response_start(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive_with_limit, track_response_start)
        except FinanceSourceRequestBodyTooLarge:
            if response_started:
                raise
            await _send_too_large(scope, receive, send)
