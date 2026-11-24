from __future__ import annotations

MULTIPART_ENVELOPE_BYTES = 64 * 1024
FINANCE_SOURCE_MAX_METADATA_BYTES = 1_048_576
FINANCE_SOURCE_MULTIPART_ENVELOPE_BYTES = MULTIPART_ENVELOPE_BYTES


def multipart_upload_request_body_limit(max_file_bytes: int) -> int:
    """Bound a file-only multipart request while allowing limited framing overhead."""
    return max_file_bytes + MULTIPART_ENVELOPE_BYTES


def finance_source_request_body_limit(max_file_bytes: int) -> int:
    """Allow the configured file, maximum metadata field, and bounded multipart framing."""
    return (
        max_file_bytes + FINANCE_SOURCE_MAX_METADATA_BYTES + FINANCE_SOURCE_MULTIPART_ENVELOPE_BYTES
    )
