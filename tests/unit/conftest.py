from __future__ import annotations

import pytest

from ._fixtures import FakeCodexHost, FakeSubagentHost, cleanup_git_fixture, make_git_fixture
from ._protocol import require_module, require_package


@pytest.fixture
def vnext_package():
    return require_package()


@pytest.fixture
def vnext_module():
    return require_module


@pytest.fixture
def fake_codex_host():
    return FakeCodexHost()


@pytest.fixture
def fake_subagent_host():
    return FakeSubagentHost()


@pytest.fixture
def git_fixture(tmp_path):
    fixture = make_git_fixture(tmp_path)
    try:
        yield fixture
    finally:
        cleanup_git_fixture(fixture)
