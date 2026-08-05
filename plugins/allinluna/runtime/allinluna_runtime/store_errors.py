"""Shared error types for the Store's bounded persistence domains."""

from __future__ import annotations


class StoreError(RuntimeError):
    """Base error for persistence failures."""


class LeaseConflictError(StoreError):
    """Raised when active write ownership overlaps an existing lease."""


class DuplicateIdentityError(StoreError):
    """Raised when two semantic identities collide with different payloads."""


class ResourceClaimError(StoreError):
    """Raised when a resource claim does not match its persisted run/lane scope."""
