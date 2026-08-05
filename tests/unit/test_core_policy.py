from __future__ import annotations

from pathlib import Path
import sys


RUNTIME = Path(__file__).resolve().parents[2] / "plugins" / "allinluna" / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from allinluna_runtime.core.policy import contains, contains_all, matches, overlaps


def test_containment_is_directional_while_overlap_is_symmetric():
    assert contains("src/**", "src/a.py")
    assert contains("src", "src/a.py")
    assert not contains("src/a.py", "src/**")
    assert overlaps("src/**", "src/a.py")
    assert overlaps("src/a.py", "src/**")


def test_glob_containment_does_not_allow_a_wider_child():
    assert contains("src/**", "src/pkg/*.py")
    assert not contains("src/pkg/*.py", "src/**")
    assert contains_all(("src/**",), ("src/a.py", "src/pkg/*.py"))
    assert not contains_all(("src/pkg/**",), ("src/**",))


def test_matching_normalizes_windows_separators():
    assert matches(r"src\pkg\a.py", "src/**/*.py")
