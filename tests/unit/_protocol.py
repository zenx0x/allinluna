"""Small test-only adapter for the vNext protocol.

The product API is still being introduced by the other lanes.  This module
keeps the expected test protocol in one place without implementing runtime
behaviour.  Module or symbol absence is an actionable contract failure: the
runtime is part of this repository and the full CI suite must never turn a
missing implementation into a planned skip.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = ROOT / "plugins" / "allinluna" / "runtime"
if RUNTIME_ROOT.is_dir() and str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

PACKAGE_CANDIDATES = (
    "allinluna_runtime",
    "plugins.allinluna.runtime.allinluna_runtime",
)


def require_package() -> ModuleType:
    """Import only the vNext package, failing if it is not present."""

    errors: list[str] = []
    for name in PACKAGE_CANDIDATES:
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name != name:
                raise
            errors.append(f"{name}: {exc}")
    raise ModuleNotFoundError(
        "vNext runtime is not available; legacy runtime is intentionally excluded "
        f"from these contract tests ({'; '.join(errors)})"
    )


def require_module(suffix: str) -> ModuleType:
    """Import a required vNext submodule; absence fails the contract."""

    package = require_package()
    package_name = package.__name__
    name = f"{package_name}.{suffix}"
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name == name:
            raise ModuleNotFoundError(
                f"required vNext module {name!r} is not available"
            ) from exc
        raise


def require_symbol(module: ModuleType, dotted_name: str) -> Any:
    """Resolve a required public symbol and fail loudly if it is absent."""

    current: Any = module
    for part in dotted_name.split("."):
        if not hasattr(current, part):
            pytest.fail(f"vNext contract missing {module.__name__}.{dotted_name}")
        current = getattr(current, part)
    return current


def construct(factory: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Construct a product object; constructor incompatibility is not a skip."""

    try:
        return factory(*args, **kwargs)
    except TypeError as exc:
        pytest.fail(f"vNext contract constructor {factory!r} rejected its declared API: {exc}")


def invoke(obj: Any, method: str, *args: Any, **kwargs: Any) -> Any:
    """Call a required protocol method; absent methods are contract failures."""

    if not hasattr(obj, method):
        pytest.fail(f"vNext contract object {type(obj).__name__} has no required method {method!r}")
    return getattr(obj, method)(*args, **kwargs)


def assert_no_raw_logs(view: Any) -> None:
    """Assert the typed view does not expose raw tool/transcript fields."""

    if hasattr(view, "model_dump"):
        view = view.model_dump()
    if hasattr(view, "__dict__") and not isinstance(view, dict):
        view = vars(view)
    assert isinstance(view, dict), "typed context view must be mapping-like for inspection"
    serialized = repr(view).lower()
    assert "raw_tool_log" not in serialized
    assert "raw_tool_logs" not in serialized
    assert "transcript" not in serialized
