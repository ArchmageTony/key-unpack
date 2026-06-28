from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    SEVENZIP_MISSING = "sevenzip_missing"
    PASSWORD_NOT_FOUND = "password_not_found"
    BAD_PASSWORD = "bad_password"
    ARCHIVE_CORRUPT = "archive_corrupt"
    UNSUPPORTED_FORMAT = "unsupported_format"
    VOLUME_MISSING = "volume_missing"
    PERMISSION_DENIED = "permission_denied"
    FILE_CONFLICT = "file_conflict"
    STEGO_NOT_FOUND = "stego_not_found"
    CANCELED = "canceled"
    UNKNOWN_ERROR = "unknown_error"


class KeyUnpackError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
