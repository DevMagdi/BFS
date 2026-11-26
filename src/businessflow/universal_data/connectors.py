from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID


class ConnectorError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ConnectorFile:
    file_name: str
    content: bytes
    content_type: str


class UniversalConnector(Protocol):
    name: str

    def read_file(self, relative_path: str) -> ConnectorFile: ...


class LocalDirectoryConnector:
    """Read-only CSV/XLSX adapter scoped to one Company directory under an approved root."""

    name = "local_directory"

    def __init__(self, root: str | Path, *, company_scope_id: UUID, max_file_bytes: int) -> None:
        if max_file_bytes < 1:
            raise ConnectorError(
                503, "connector_invalid_limit", "The local connector file limit must be positive."
            )
        try:
            self.root = Path(root).expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            raise ConnectorError(
                503, "connector_not_configured", "The local connector directory is unavailable."
            ) from None
        if not self.root.is_dir():
            raise ConnectorError(
                503, "connector_not_configured", "The local connector root must be a directory."
            )
        self.company_scope_id = str(company_scope_id)
        self.max_file_bytes = max_file_bytes
        if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
            raise ConnectorError(
                503,
                "connector_safe_path_unavailable",
                "The local connector requires operating-system support for safe directory "
                "traversal.",
            )

    def read_file(self, relative_path: str) -> ConnectorFile:
        if not relative_path or len(relative_path) > 512 or "\x00" in relative_path:
            raise ConnectorError(
                422, "invalid_connector_path", "Choose a valid relative file path."
            )
        if "\\" in relative_path:
            raise ConnectorError(
                422,
                "invalid_connector_path",
                "Use a relative path within the Company connector directory.",
            )
        parts = relative_path.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ConnectorError(
                422,
                "invalid_connector_path",
                "Use a relative path within the Company connector directory.",
            )

        suffix = Path(parts[-1]).suffix.casefold()
        content_types = {
            ".csv": "text/csv",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
        content_type = content_types.get(suffix)
        if content_type is None:
            raise ConnectorError(
                415, "unsupported_file", "The local connector accepts CSV and XLSX files only."
            )

        directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW
        no_follow = os.O_NOFOLLOW
        root_fd: int | None = None
        directory_fd: int | None = None
        file_fd: int | None = None
        try:
            root_fd = os.open(self.root, directory_flags)
            try:
                # The Company identifier comes from the authorized server-side context, never
                # from the requested path. A caller cannot select another Company's directory.
                directory_fd = os.open(
                    self.company_scope_id,
                    directory_flags,
                    dir_fd=root_fd,
                )
            except FileNotFoundError:
                raise ConnectorError(
                    503,
                    "connector_scope_not_configured",
                    "The operator has not provisioned a local connector directory for "
                    "this Company.",
                ) from None

            for part in parts[:-1]:
                next_fd = os.open(
                    part,
                    directory_flags | no_follow,
                    dir_fd=directory_fd,
                )
                os.close(directory_fd)
                directory_fd = next_fd
            file_fd = os.open(
                parts[-1],
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NONBLOCK", 0)
                | no_follow,
                dir_fd=directory_fd,
            )
            metadata = os.fstat(file_fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise ConnectorError(
                    422, "invalid_connector_file", "The selected path is not a regular file."
                )
            if metadata.st_size <= 0:
                raise ConnectorError(422, "malformed_file", "The selected file is empty.")
            if metadata.st_size > self.max_file_bytes:
                raise ConnectorError(
                    413, "file_too_large", "The selected file exceeds the configured size limit."
                )
            with os.fdopen(file_fd, "rb", closefd=True) as stream:
                file_fd = None
                content = stream.read(self.max_file_bytes + 1)
            if len(content) > self.max_file_bytes:
                raise ConnectorError(
                    413, "file_too_large", "The selected file exceeds the configured size limit."
                )
            return ConnectorFile(file_name=parts[-1], content=content, content_type=content_type)
        except ConnectorError:
            raise
        except FileNotFoundError:
            raise ConnectorError(
                404, "connector_file_not_found", "The selected connector file was not found."
            ) from None
        except (OSError, ValueError):
            raise ConnectorError(
                422,
                "invalid_connector_file",
                "The selected connector path could not be read safely.",
            ) from None
        finally:
            if file_fd is not None:
                os.close(file_fd)
            if directory_fd is not None:
                os.close(directory_fd)
            if root_fd is not None:
                os.close(root_fd)


class UniversalConnectorRegistry:
    def __init__(self) -> None:
        self._connectors: dict[str, UniversalConnector] = {}

    def register(self, connector: UniversalConnector) -> None:
        if connector.name in self._connectors:
            raise ValueError(f"connector '{connector.name}' is already registered")
        self._connectors[connector.name] = connector

    def get(self, name: str) -> UniversalConnector:
        try:
            return self._connectors[name]
        except KeyError:
            raise ConnectorError(
                503, "connector_unavailable", "The requested connector is not configured."
            ) from None

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._connectors))
