from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.formparsers import MultiPartParser

from businessflow.api.finance_source_limits import (
    finance_source_request_body_limit,
    multipart_upload_request_body_limit,
)
from businessflow.api.finance_source_upload_limit import MAX_UNIVERSAL_SYNC_REQUEST_BODY_BYTES
from businessflow.api.forecasting_limits import MAX_FORECAST_DATASET_REQUEST_BODY_BYTES
from businessflow.main import create_app
from tests.helpers import auth_headers, create_tenant, register_user


def _raw_asgi_post(
    app,
    path: str,
    headers: list[tuple[bytes, bytes]],
    body: bytes,
    chunk_size: int,
) -> tuple[int, dict[str, object], int]:
    async def invoke() -> tuple[int, dict[str, object], int]:
        chunks = [body[index : index + chunk_size] for index in range(0, len(body), chunk_size)]
        received_bytes = 0
        next_chunk = 0
        sent: list[dict[str, object]] = []

        async def receive() -> dict[str, object]:
            nonlocal next_chunk, received_bytes
            if next_chunk >= len(chunks):
                return {"type": "http.disconnect"}
            chunk = chunks[next_chunk]
            next_chunk += 1
            received_bytes += len(chunk)
            return {
                "type": "http.request",
                "body": chunk,
                "more_body": next_chunk < len(chunks),
            }

        async def send(message: dict[str, object]) -> None:
            sent.append(message.copy())

        raw_path = path.encode("ascii")
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": raw_path,
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "state": {},
        }
        await app(scope, receive, send)
        status = next(
            int(message["status"]) for message in sent if message["type"] == "http.response.start"
        )
        payload = b"".join(
            message.get("body", b"") for message in sent if message["type"] == "http.response.body"
        )
        return status, json.loads(payload), received_bytes

    return asyncio.run(invoke())


def _record_multipart_parser_input(monkeypatch) -> list[int]:
    received_chunk_sizes: list[int] = []
    original_parse = MultiPartParser.parse

    async def recording_parse(parser: MultiPartParser):
        upstream = parser.stream

        async def record_chunks() -> AsyncIterator[bytes]:
            async for chunk in upstream:
                received_chunk_sizes.append(len(chunk))
                yield chunk

        parser.stream = record_chunks()
        return await original_parse(parser)

    monkeypatch.setattr(MultiPartParser, "parse", recording_parse)
    return received_chunk_sizes


def _limited_app(settings):
    return create_app(
        settings.model_copy(
            update={
                "import_max_file_bytes": 1024,
                "finance_source_intake_enabled": True,
                "core_financials_enabled": True,
            }
        )
    )


def _multipart_body(boundary: bytes, target_size: int, *, include_metadata: bool) -> bytes:
    metadata = (
        b"--"
        + boundary
        + b"\r\n"
        + b'Content-Disposition: form-data; name="metadata_json"\r\n\r\n{}\r\n'
        if include_metadata
        else b""
    )
    prefix = (
        metadata
        + b"--"
        + boundary
        + b"\r\n"
        + b'Content-Disposition: form-data; name="file"; filename="bounded.csv"\r\n'
        + b"Content-Type: text/csv\r\n\r\n"
    )
    suffix = b"\r\n--" + boundary + b"--\r\n"
    payload_size = target_size - len(prefix) - len(suffix)
    assert payload_size > 0
    return prefix + b"x" * payload_size + suffix


def _route_limits(max_file_bytes: int, tenant_id: str):
    return (
        (
            f"/api/v1/tenants/{tenant_id}/imports",
            multipart_upload_request_body_limit(max_file_bytes),
            False,
        ),
        (
            f"/api/v1/tenants/{tenant_id}/position-reviews/financial-imports",
            multipart_upload_request_body_limit(max_file_bytes),
            False,
        ),
        (
            f"/api/v1/tenants/{tenant_id}/company-data/universal/sources/{tenant_id}/ingestions",
            multipart_upload_request_body_limit(max_file_bytes),
            False,
        ),
        (
            f"/api/v1/tenants/{tenant_id}/finance-sources/intakes",
            finance_source_request_body_limit(max_file_bytes),
            True,
        ),
        (
            f"/api/v1/tenants/{tenant_id}/core-financials/imports",
            finance_source_request_body_limit(max_file_bytes),
            True,
        ),
    )


def test_forecast_dataset_json_body_limit_rejects_declared_oversize_before_parsing(
    settings,
) -> None:
    app = _limited_app(settings)
    tenant_id = str(uuid4())
    path = f"/api/v1/tenants/{tenant_id}/forecasts/datasets"
    status, payload, received_bytes = _raw_asgi_post(
        app,
        path,
        [
            (b"content-type", b"application/json"),
            (
                b"content-length",
                str(MAX_FORECAST_DATASET_REQUEST_BODY_BYTES + 1).encode("ascii"),
            ),
        ],
        b"{}",
        2,
    )
    assert status == 413
    assert payload["error"]["code"] == "request_body_too_large"
    assert received_bytes == 0


def test_universal_sync_json_limit_rejects_declared_oversize_before_auth_or_parsing(
    settings,
) -> None:
    app = _limited_app(settings)
    tenant_id = str(uuid4())
    source_id = str(uuid4())
    path = f"/api/v1/tenants/{tenant_id}/company-data/universal/sources/{source_id}/sync"
    body = b'{"relative_path":"incoming/file.csv"}'
    body += b" " * (MAX_UNIVERSAL_SYNC_REQUEST_BODY_BYTES + 1 - len(body))
    status, payload, received_bytes = _raw_asgi_post(
        app,
        path,
        [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
        body,
        1024,
    )
    assert len(body) == MAX_UNIVERSAL_SYNC_REQUEST_BODY_BYTES + 1
    assert status == 413
    assert payload["error"]["code"] == "request_body_too_large"
    assert received_bytes == 0


def test_universal_sync_json_limit_rejects_chunked_body_as_it_crosses_cap(settings) -> None:
    app = _limited_app(settings)
    tenant_id = str(uuid4())
    source_id = str(uuid4())
    path = f"/api/v1/tenants/{tenant_id}/company-data/universal/sources/{source_id}/sync"
    body = b'{"relative_path":"incoming/file.csv"}'
    body += b" " * (MAX_UNIVERSAL_SYNC_REQUEST_BODY_BYTES + 1 - len(body))
    status, payload, received_bytes = _raw_asgi_post(
        app,
        path,
        [(b"content-type", b"application/json")],
        body,
        1024,
    )
    assert status == 413
    assert payload["error"]["code"] == "request_body_too_large"
    assert MAX_UNIVERSAL_SYNC_REQUEST_BODY_BYTES < received_bytes <= len(body)


def test_universal_fixture_sync_json_limit_rejects_oversize_before_auth_or_parsing(
    settings,
) -> None:
    app = _limited_app(settings)
    tenant_id = str(uuid4())
    source_id = str(uuid4())
    path = f"/api/v1/tenants/{tenant_id}/company-data/universal/sources/{source_id}/fixture-sync"
    body = b'{"fixture_key":"synthetic_customer_export_v1"}'
    body += b" " * (MAX_UNIVERSAL_SYNC_REQUEST_BODY_BYTES + 1 - len(body))
    status, payload, received_bytes = _raw_asgi_post(
        app,
        path,
        [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
        body,
        1024,
    )
    assert status == 413
    assert payload["error"]["code"] == "request_body_too_large"
    assert received_bytes == 0


def test_authenticated_import_rejects_over_file_cap_below_route_body_cap(settings) -> None:
    max_file_bytes = 1024
    app = _limited_app(settings)
    boundary = b"bfs-file-cap"
    file_content = b"x" * (max_file_bytes + 1)
    body = (
        b"--"
        + boundary
        + b"\r\n"
        + b'Content-Disposition: form-data; name="file"; filename="too-large.csv"\r\n'
        + b"Content-Type: text/csv\r\n\r\n"
        + file_content
        + b"\r\n--"
        + boundary
        + b"--\r\n"
    )
    route_limit = multipart_upload_request_body_limit(max_file_bytes)
    assert len(file_content) > max_file_bytes
    assert len(body) < route_limit

    with TestClient(app, raise_server_exceptions=False) as client:
        tokens = register_user(client, "file-limit@example.com")
        tenant = create_tenant(client, tokens)
        response = client.post(
            f"/api/v1/tenants/{tenant['id']}/imports",
            content=body,
            headers={
                **auth_headers(tokens),
                "Content-Type": f"multipart/form-data; boundary={boundary.decode('ascii')}",
                "Content-Length": str(len(body)),
                "Idempotency-Key": "file-cap-check-0001",
            },
        )

    assert response.status_code == 413, response.text
    assert response.json()["error"]["code"] == "file_too_large"
    assert len(body) < route_limit, "the route-wide request cap was not reached"


def test_declared_oversized_m4_m10_universal_and_m14_bodies_stop_before_multipart_parser(
    settings, monkeypatch
) -> None:
    app = _limited_app(settings)
    tenant_id = str(uuid4())
    observed_chunks = _record_multipart_parser_input(monkeypatch)

    with TestClient(app, raise_server_exceptions=False) as client:
        for path, limit, include_metadata in _route_limits(1024, tenant_id):
            body = _multipart_body(
                b"bfs-declared-limit", limit + 1, include_metadata=include_metadata
            )
            response = client.post(
                path,
                content=body,
                headers={
                    "Content-Type": "multipart/form-data; boundary=bfs-declared-limit",
                    "Content-Length": str(len(body)),
                },
            )
            assert response.status_code == 413, response.text
            assert response.json()["error"]["code"] == "request_body_too_large"
            assert response.headers["X-Request-ID"] == response.json()["error"]["request_id"]
            assert observed_chunks == [], "declared oversize must be rejected before parser input"

        unrelated_json = client.post(
            "/api/v1/auth/login",
            content=b"{}" + b" " * (finance_source_request_body_limit(1024) + 1),
            headers={"Content-Type": "application/json"},
        )
        assert unrelated_json.status_code == 422
        assert unrelated_json.json()["error"]["code"] == "invalid_request"


@pytest.mark.parametrize(
    "route_index",
    [0, 1, 2, 3, 4],
    ids=["m4", "m10", "universal", "m14", "core-financials"],
)
def test_exact_limit_is_accepted_and_one_byte_over_is_rejected(
    settings, monkeypatch, route_index: int
) -> None:
    app = _limited_app(settings)
    tenant_id = str(uuid4())
    path, limit, include_metadata = _route_limits(1024, tenant_id)[route_index]
    boundary = f"bfs-boundary-{route_index}".encode("ascii")
    content_type = f"multipart/form-data; boundary={boundary.decode('ascii')}"
    observed_chunks = _record_multipart_parser_input(monkeypatch)
    exact_body = _multipart_body(boundary, limit, include_metadata=include_metadata)
    over_body = _multipart_body(boundary, limit + 1, include_metadata=include_metadata)

    with TestClient(app, raise_server_exceptions=False) as client:
        exact = client.post(
            path,
            content=exact_body,
            headers={"Content-Type": content_type, "Content-Length": str(limit)},
        )
        assert exact.status_code != 413, exact.text
        assert sum(observed_chunks) == limit

        observed_chunks.clear()
        over = client.post(
            path,
            content=over_body,
            headers={"Content-Type": content_type, "Content-Length": str(limit + 1)},
        )
        assert over.status_code == 413, over.text
        assert observed_chunks == [], "declared one-byte oversize must not reach the parser"


def test_streamed_m4_m10_universal_and_m14_bodies_count_unreliable_lengths_and_track_parser_chunks(
    settings, monkeypatch
) -> None:
    app = _limited_app(settings)
    tenant_id = str(uuid4())
    chunk_size = 16 * 1024
    observed_chunks = _record_multipart_parser_input(monkeypatch)
    length_variants = (
        [],
        [(b"content-length", b"1")],
        [(b"content-length", b"not-a-length")],
        [(b"content-length", b"1"), (b"content-length", b"2")],
        [(b"content-length", b"0" * 5000 + b"1")],
    )

    for route_index, (path, body_limit, include_metadata) in enumerate(
        _route_limits(1024, tenant_id)
    ):
        boundary = f"bfs-stream-limit-{route_index}".encode("ascii")
        prefix = (
            b"--"
            + boundary
            + b"\r\n"
            + (
                b'Content-Disposition: form-data; name="metadata_json"\r\n\r\n{}\r\n--'
                + boundary
                + b"\r\n"
                if include_metadata
                else b""
            )
            + b'Content-Disposition: form-data; name="file"; filename="large.csv"\r\n'
            + b"Content-Type: text/csv\r\n\r\n"
        )
        body = prefix + b"x" * (body_limit + 4 * chunk_size) + b"\r\n--" + boundary + b"--\r\n"
        headers_without_length = [
            (b"host", b"testserver"),
            (b"content-type", b"multipart/form-data; boundary=" + boundary),
        ]

        for length_headers in length_variants:
            observed_chunks.clear()
            status, payload, received_bytes = _raw_asgi_post(
                app,
                path,
                headers_without_length + length_headers,
                body,
                chunk_size,
            )
            parser_bytes = sum(observed_chunks)
            assert status == 413
            assert payload["error"]["code"] == "request_body_too_large"
            assert body_limit < received_bytes < len(body)
            assert received_bytes <= body_limit + chunk_size
            assert 0 < parser_bytes <= body_limit, "prior safe chunks may reach the parser"
            assert received_bytes - parser_bytes == chunk_size, (
                "the chunk crossing the limit must be consumed from ASGI but not forwarded"
            )
