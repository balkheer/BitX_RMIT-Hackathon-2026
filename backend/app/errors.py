from __future__ import annotations

from typing import Any


class AppError(Exception):
    """An error with a stable code and a message that is safe to show users.

    Messages must never contain patient values, tokens or internal details.
    """

    def __init__(self, status: int, code: str, message: str, extra: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra or {}


def unauthorized(message: str = "Authentication required.", code: str = "UNAUTHENTICATED") -> AppError:
    return AppError(401, code, message)


def forbidden(message: str = "You do not have permission to perform this action.", code: str = "FORBIDDEN") -> AppError:
    return AppError(403, code, message)


def not_found(message: str = "Not found.") -> AppError:
    return AppError(404, "NOT_FOUND", message)


def conflict(message: str, code: str = "CONFLICT") -> AppError:
    return AppError(409, code, message)


def unprocessable(message: str, code: str = "VALIDATION_FAILED", extra: dict[str, Any] | None = None) -> AppError:
    return AppError(422, code, message, extra)
