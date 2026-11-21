from __future__ import annotations


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class CommitAndRaise(Exception):
    """Commit security/audit state before returning a safe client error."""

    def __init__(self, error: ApiError) -> None:
        super().__init__(error.message)
        self.error = error
