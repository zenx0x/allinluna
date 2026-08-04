"""Read-only legacy compatibility APIs."""

from .common import CompatibilityReport
from .legacy_plan import LegacyPlanImportAPI, LegacyPlanImportResult
from .legacy_run_state import LegacyRunStateImportAPI, LegacyRunStateImportResult
from .resources import LegacyResourceTranslator, ResourceTranslation

LegacyPlanImporter = LegacyPlanImportAPI
LegacyRunStateImporter = LegacyRunStateImportAPI

__all__ = [
    "CompatibilityReport", "LegacyPlanImportAPI", "LegacyPlanImportResult", "LegacyPlanImporter", "LegacyResourceTranslator", "LegacyRunStateImportAPI", "LegacyRunStateImportResult", "LegacyRunStateImporter", "ResourceTranslation",
]
