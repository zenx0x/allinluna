"""Canonical reference parsing and construction."""

from __future__ import annotations

import re
from dataclasses import dataclass

_REF = re.compile(r"^(?P<kind>[a-z][a-z0-9-]*)://(?P<identity>[^\s@]+)(?:@(?P<version>[1-9][0-9]*))?$")


@dataclass(frozen=True, slots=True)
class Ref:
    kind: str
    identity: str
    version: int | None = None

    @classmethod
    def parse(cls, value: str, *, expected_kind: str | None = None) -> "Ref":
        match = _REF.fullmatch(str(value))
        if match is None:
            raise ValueError(f"invalid reference: {value!r}")
        kind = match.group("kind")
        if expected_kind and kind != expected_kind:
            raise ValueError(f"expected {expected_kind} reference, found {kind}")
        version = int(match.group("version")) if match.group("version") else None
        return cls(kind, match.group("identity"), version)

    def __str__(self) -> str:
        suffix = f"@{self.version}" if self.version is not None else ""
        return f"{self.kind}://{self.identity}{suffix}"


def make_ref(kind: str, identity: str, version: int | None = None) -> str:
    return str(Ref(str(kind), str(identity), version))


__all__ = ["Ref", "make_ref"]
