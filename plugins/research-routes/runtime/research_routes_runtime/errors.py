"""Errors raised by the Research Routes Pack.

The Pack deliberately owns its semantic errors.  It does not import or
extend All in Luna Core errors, which keeps the Core host- and
research-philosophy-neutral.
"""

from __future__ import annotations


class ResearchPackError(ValueError):
    """Base error for malformed or semantically unsafe research input."""


class PackValidationError(ResearchPackError):
    """Raised when a packet cannot be compiled into a valid Research Pack."""

    def __init__(self, message: str, *, errors: tuple[str, ...] = ()) -> None:
        self.errors = errors or (message,)
        super().__init__(message)


class BoundaryViolation(PackValidationError):
    """Raised when research state attempts to authorize product state."""


class CrossContextReferenceError(PackValidationError):
    """Raised when a record references a different research context."""


class AuthorizationRequired(PackValidationError):
    """Raised when a transition needs an explicit human decision."""
