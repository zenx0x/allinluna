"""Canonical public surface for the All in Luna vNext T1 core runtime."""

from __future__ import annotations

from . import contracts as _contracts
from . import domain as _domain
from . import journal as _journal
from . import migrations as _migrations
from . import store as _store
from .contracts import (
    ContractDelta,
    ContractRepository,
    ContractRevision,
    ContractRevisionError,
    ContractValidationError,
    StoreTransactionRules,
    validate_contract,
)
from .domain import *  # noqa: F403,F401 - domain is the canonical typed model surface.
from .journal import (
    FollowResult,
    JournalCursor,
    SignalJournal,
    SignalJournalAPI,
    SignalJournalError,
)
from .migrations import (
    LegacyReadOnlyMigration,
    Migration,
    MigrationAPI,
    MigrationRunner,
    MigrationVersionError,
    apply_migrations,
    validate_schema,
)
from .evidence import (
    CheckReceipt,
    CheckRunner,
    CheckRunnerProtocol,
    EVIDENCE_PROFILES,
    EvidenceCollectionError,
    EvidenceCollector,
    EvidenceProfile,
)
from .store import (
    LeaseConflictError,
    ReceiptIngestionAPI,
    StatusProjection,
    Store,
    StoreError,
    TaskStoreAPI,
)


DomainAPI = _domain.DomainAPI
TaskStoreAPI = _store.TaskStoreAPI
SignalJournalAPI = _journal.SignalJournalAPI
MigrationAPI = _migrations.MigrationAPI
ReceiptIngestionAPI = _store.ReceiptIngestionAPI
StoreTransactionRules = _contracts.StoreTransactionRules

# Make the package-level ``__all__`` truthful for every owned module while
# retaining the domain module's canonical names where sibling modules use the
# same protocol label (for example ``Signal`` and ``Contract``).
for _module in (_contracts, _journal, _migrations, _store):
    for _name in getattr(_module, "__all__", ()):
        if _name not in globals() and hasattr(_module, _name):
            globals()[_name] = getattr(_module, _name)

# The package-level schema version describes the SQLite database, whose
# authoritative value is owned by migrations.py.
SCHEMA_VERSION = _migrations.SCHEMA_VERSION


__all__ = sorted(
    {
        name
        for name in (
            set(_domain.__all__)
            | set(_contracts.__all__)
            | set(_journal.__all__)
            | set(_migrations.__all__)
            | set(_store.__all__)
            | {
                "DomainAPI",
                "TaskStoreAPI",
                "SignalJournalAPI",
                "MigrationAPI",
                "ReceiptIngestionAPI",
                "StoreTransactionRules",
                "ContractRepository",
                "LegacyReadOnlyMigration",
                "CheckReceipt",
                "CheckRunner",
                "CheckRunnerProtocol",
                "EVIDENCE_PROFILES",
                "EvidenceCollectionError",
                "EvidenceCollector",
                "EvidenceProfile",
            }
        )
        if name in globals()
    }
)
