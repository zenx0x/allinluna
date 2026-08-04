"""Workspace adapter API for real Git and filesystem evidence."""

from .base import (
    Evidence,
    InvalidCommitError,
    NonGitWorkspaceError,
    OwnershipError,
    PathContainmentError,
    WorkspaceAdapter,
    WorkspaceAdapterError,
    WorkspaceCommandError,
    WorkspaceEvidence,
    WorkspaceEvidenceAPI,
    WorkspaceIdentity,
    WorkspaceNotFoundError,
    canonical_json,
    json_safe,
    stable_digest,
)
from .filesystem import FileSystemAdapter, FilesystemWorkspaceAdapter
from .git import GitWorktreeAdapter, GitWorkspaceAdapter


WorkspaceAdapterAPI = WorkspaceAdapter

__all__ = [
    "Evidence",
    "FileSystemAdapter",
    "FilesystemWorkspaceAdapter",
    "GitWorktreeAdapter",
    "GitWorkspaceAdapter",
    "InvalidCommitError",
    "NonGitWorkspaceError",
    "OwnershipError",
    "PathContainmentError",
    "WorkspaceAdapter",
    "WorkspaceAdapterAPI",
    "WorkspaceAdapterError",
    "WorkspaceCommandError",
    "WorkspaceEvidence",
    "WorkspaceEvidenceAPI",
    "WorkspaceIdentity",
    "WorkspaceNotFoundError",
    "canonical_json",
    "json_safe",
    "stable_digest",
]
