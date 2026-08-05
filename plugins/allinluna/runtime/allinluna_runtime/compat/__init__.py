"""Explicit, read-only legacy import boundary.

Compatibility translators are never imported by the normal goal path.  The
public names are ordinary classes; the former lazy proxy/import-cycle
workaround has been removed.
"""

from .common import CompatibilityReport
from .legacy_plan import LegacyPlanImportAPI, LegacyPlanImportResult, LegacyPlanImporter
from .legacy_run_state import LegacyRunStateImportAPI, LegacyRunStateImportResult, LegacyRunStateImporter
from .resources import LegacyResourceTranslator, ResourceTranslation

__all__ = [
    "CompatibilityReport", "LegacyPlanImportAPI", "LegacyPlanImportResult",
    "LegacyPlanImporter", "LegacyResourceTranslator", "LegacyRunStateImportAPI",
    "LegacyRunStateImportResult", "LegacyRunStateImporter", "ResourceTranslation",
]
