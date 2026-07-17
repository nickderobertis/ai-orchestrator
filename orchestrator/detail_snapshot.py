"""Typed, validated records persisted by the graph monitor for later detail lookup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .github import Check, PRStatus
from .journal import DetailValue

SNAPSHOT_VERSION = 1


def _optional_strings(value: dict[str, Any], names: tuple[str, ...]) -> bool:
    return all(name not in value or isinstance(value[name], str) for name in names)


@dataclass(frozen=True)
class CommitDetail:
    sha: str
    subject: str = ""
    branch: str = ""
    base: str = ""
    identity: str = ""
    detail: str = ""

    @classmethod
    def from_value(cls, value: object) -> CommitDetail | None:
        if not isinstance(value, dict) or not isinstance(value.get("sha"), str):
            return None
        if not _optional_strings(value, ("subject", "branch", "base", "identity", "detail")):
            return None
        return cls(
            sha=value["sha"],
            subject=value.get("subject", ""),
            branch=value.get("branch", ""),
            base=value.get("base", ""),
            identity=value.get("identity", ""),
            detail=value.get("detail", ""),
        )

    def to_record(self) -> dict[str, DetailValue]:
        return {
            "sha": self.sha,
            "subject": self.subject,
            "branch": self.branch,
            "base": self.base,
            "identity": self.identity,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class PrDetail:
    number: int
    url: str = ""
    identity: str = ""
    state: str = "unknown"
    merged: bool = False
    merge_state_status: str = "unknown"
    draft: bool = False
    checks: tuple[Check, ...] = ()
    revision: int = 0

    @classmethod
    def from_value(cls, value: object) -> PrDetail | None:
        if not isinstance(value, dict):
            return None
        number = value.get("number")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            return None
        if not _optional_strings(value, ("url", "identity", "state", "merge_state_status")):
            return None
        merged = value.get("merged", False)
        draft = value.get("draft", False)
        raw_checks = value.get("checks", [])
        revision = value.get("revision", 0)
        if (
            not isinstance(merged, bool)
            or not isinstance(draft, bool)
            or not isinstance(raw_checks, list)
            or not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision < 0
        ):
            return None
        checks = tuple(
            Check(name=item["name"], state=item["state"], required=item["required"])
            for item in raw_checks
            if isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and isinstance(item.get("state"), str)
            and isinstance(item.get("required"), bool)
        )
        return cls(
            number=number,
            url=value.get("url", ""),
            identity=value.get("identity", ""),
            state=value.get("state", "unknown"),
            merged=merged,
            merge_state_status=value.get("merge_state_status", "unknown"),
            draft=draft,
            checks=checks,
            revision=revision,
        )

    @classmethod
    def from_status(
        cls, status: PRStatus, *, url: str, identity: str, revision: int = 0
    ) -> PrDetail:
        return cls(
            number=status.number,
            url=url,
            identity=identity,
            state=status.state,
            merged=status.merged,
            merge_state_status=status.merge_state_status,
            draft=status.draft,
            checks=status.checks,
            revision=revision,
        )

    def status(self) -> PRStatus:
        return PRStatus(
            number=self.number,
            state=self.state,
            merged=self.merged,
            merge_state_status=self.merge_state_status,
            checks=self.checks,
            draft=self.draft,
        )

    def to_record(self) -> dict[str, DetailValue]:
        return {
            "number": self.number,
            "url": self.url,
            "identity": self.identity,
            "state": self.state,
            "merged": self.merged,
            "merge_state_status": self.merge_state_status,
            "draft": self.draft,
            "checks": [
                {"name": check.name, "state": check.state, "required": check.required}
                for check in self.checks
            ],
            "revision": self.revision,
        }
