from __future__ import annotations

FINANCE_SOURCE_MAX_METADATA_BYTES = 1_048_576
FINANCE_SOURCE_MULTIPART_ENVELOPE_BYTES = 64 * 1024


def finance_source_request_body_limit(max_file_bytes: int) -> int:
    """Allow the configured file, maximum metadata field, and bounded multipart framing."""
    return (
        max_file_bytes + FINANCE_SOURCE_MAX_METADATA_BYTES + FINANCE_SOURCE_MULTIPART_ENVELOPE_BYTES
    )
