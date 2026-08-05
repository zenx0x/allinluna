"""Canonical public surface for the All in Luna vNext T1 core runtime."""

from __future__ import annotations

from .contracts import (
    ContractDelta,
    ContractRepository,
    ContractRevision,
    ContractRevisionError,
    ContractValidationError,
    StoreTransactionRules,
    validate_contract,
)
from .domain import (
    Contract,
    ContractRef,
    DomainAPI,
    ExportPort,
    ImportPort,
    ResourceEnvelope,
    Run,
    RunIntent,
    Task,
    TaskDependency,
    TaskGraph,
    ValidationError,
    WorkGraph,
)
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
    SCHEMA_VERSION,
    apply_migrations,
    validate_schema,
)
from .resource_observation import ResourceObservation
from .evidence import (
    CheckReceipt,
    CheckRunner,
    CheckRunnerProtocol,
    EVIDENCE_PROFILES,
    EvidenceCollectionError,
    EvidenceCollector,
    EvidenceProfile,
)
from .verification import VerificationSpec, VerificationSpecError, VerifierSpec
from .store import (
    LeaseConflictError,
    ReceiptIngestionAPI,
    Store,
    StoreError,
    TaskStoreAPI,
)


# This is intentionally a narrow, explicit convenience surface.  Domain and
# adapter modules remain importable at their owning paths; package import must
# not hide ownership with wildcard re-exports or generated aliases.
__all__ = [
    "CheckReceipt", "CheckRunner", "CheckRunnerProtocol", "Contract",
    "ContractDelta", "ContractRef", "ContractRepository", "ContractRevision",
    "ContractRevisionError", "ContractValidationError", "DomainAPI",
    "EVIDENCE_PROFILES", "EvidenceCollectionError", "EvidenceCollector",
    "EvidenceProfile", "ExportPort", "FollowResult", "ImportPort",
    "JournalCursor", "LegacyReadOnlyMigration", "LeaseConflictError",
    "Migration", "MigrationAPI", "MigrationRunner", "MigrationVersionError",
    "ReceiptIngestionAPI", "ResourceEnvelope", "ResourceObservation", "Run",
    "RunIntent", "SCHEMA_VERSION", "SignalJournal", "SignalJournalAPI",
    "SignalJournalError", "Store", "StoreError",
    "StoreTransactionRules", "Task", "TaskDependency", "TaskGraph",
    "TaskStoreAPI", "ValidationError", "WorkGraph", "apply_migrations",
    "VerificationSpec", "VerificationSpecError", "VerifierSpec",
    "validate_contract", "validate_schema",
]
