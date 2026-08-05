"""Public Research Routes Pack API.

The package is a co-installable Pack runtime.  It contains no imports from
All in Luna Core; callers can persist its JSON snapshots and lifecycle
records through generic Core artifact and decision primitives.
"""

from .compiler import (
    PACK_ID,
    SCHEMA_PATH,
    SCHEMA_VERSION,
    ResearchPackCompiler,
    ResearchRoutesCompiler,
    compile_pack,
    load_schema,
    validate_pack,
)
from .errors import (
    AuthorizationRequired,
    BoundaryViolation,
    CrossContextReferenceError,
    PackValidationError,
    ResearchPackError,
)
from .model import *
from .model import __all__ as _MODEL_EXPORTS
from .runtime import ResearchPackRuntime, ResearchRoutesRuntime, ResearchRuntime

ResearchRoutesPack = ResearchPack

__all__ = [
    "AuthorizationRequired",
    "BoundaryViolation",
    "CrossContextReferenceError",
    "PACK_ID",
    "PackValidationError",
    "ResearchPackCompiler",
    "ResearchPackError",
    "ResearchPackRuntime",
    "ResearchRoutesPack",
    "ResearchRoutesCompiler",
    "ResearchRoutesRuntime",
    "ResearchRuntime",
    "SCHEMA_PATH",
    "SCHEMA_VERSION",
    "compile_pack",
    "load_schema",
    "validate_pack",
] + list(_MODEL_EXPORTS)
