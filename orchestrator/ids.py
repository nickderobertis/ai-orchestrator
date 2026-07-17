"""Strict typed identifiers for the things a journal detail points *at*.

A journal record says what happened; its ``detail`` says what it happened *to* —
a dispatched session, a commit, a PR, a node in a tracked graph. Those references
were previously bare strings whose meaning lived only in the key that carried them
(``{"pr": "https://..."}``), which is unreadable across records and unresolvable
after the fact: a reader holding one value cannot tell what namespace it belongs
to, and two subsystems naming the same PR differently could never be joined.

This module gives each reference a **self-describing wire form** — the namespace
travels *with* the value, so one string is enough to know both what it names and
where to resolve it:

* ``oh:<history-id>`` — one oneharness history session.
* ``git:<repo-identity>@<sha>`` — one commit in one repository.
* ``pr:<repo-identity>#<number>`` — one pull request.
* ``graph:<run>/<round>/<node>`` — one node of one round of one tracked run.

``<repo-identity>`` is the ``owner/name`` slug a repository already names itself
by in the ledger and in `MergeContext.repo_slug` (``local/<name>`` for a local
repo), not the full clone URL its registry `IdentityKey` uses. The slug is what
persisted results actually store, so it is what a snapshot lookup can match; a URL
would also drag ``://`` through a grammar whose separators are punctuation.

Parsing is **strict and total**: every component is validated against the shape it
claims, so `parse_detail_id` returning a value means the id can be resolved rather
than merely that it started with a known prefix. That strictness is what lets
`show_run` treat "parses as a typed id" as the branch condition and still hand
anything else to the legacy substring search unharmed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TypeAlias

# Each component is anchored to the vocabulary it comes from rather than a
# permissive catch-all, because these ids are the *keys* a snapshot is searched
# by: a pattern loose enough to admit `pr:x/y#007` would make two spellings of one
# PR that never compare equal.
_IDENTITY = r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*"
_TOKEN = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_NUMBER = r"[1-9][0-9]*"
# Git shas are lowercase hex. Abbreviations are accepted down to 7 — git's own
# minimum for an unambiguous short sha — so an id a human copied out of a log
# resolves by prefix rather than demanding all 40.
_SHA = r"[0-9a-f]{7,40}"

MIN_SHA_LENGTH = 7

_OH = re.compile(rf"\Aoh:({_TOKEN})\Z")
_GIT = re.compile(rf"\Agit:({_IDENTITY})@({_SHA})\Z")
_PR = re.compile(rf"\Apr:({_IDENTITY})#({_NUMBER})\Z")
_GRAPH = re.compile(rf"\Agraph:({_TOKEN})/({_NUMBER})/({_TOKEN})\Z")

# The prefixes this module owns. A query starting with one of these is a *claim*
# to be a typed id, which is what separates "malformed id" (an error worth
# reporting) from "not an id at all" (a legacy substring, left alone).
PREFIXES = ("oh:", "git:", "pr:", "graph:")


class DetailIdError(ValueError):
    """A string claims to be a typed detail id but does not satisfy its grammar."""


@dataclass(frozen=True)
class OneharnessId:
    """A dispatched session, as oneharness' history store names it."""

    history_id: str

    def __post_init__(self) -> None:
        _require(_TOKEN, self.history_id, "oneharness history id")

    def __str__(self) -> str:
        return f"oh:{self.history_id}"


@dataclass(frozen=True)
class GitId:
    """One commit, qualified by the repository it lives in.

    The sha is carried as written rather than expanded: this module never touches
    a repository, so it cannot resolve an abbreviation, and silently padding one
    would invent a commit that may not exist.
    """

    identity: str
    sha: str

    def __post_init__(self) -> None:
        _require(_IDENTITY, self.identity, "repo identity")
        _require(_SHA, self.sha, "git sha")

    @property
    def abbreviated(self) -> bool:
        return len(self.sha) < 40

    def matches(self, sha: str) -> bool:
        """Whether ``sha`` is this commit, allowing for an abbreviated id.

        Prefix matching runs in the one direction that is sound: a *stored* sha is
        always full, so this id is a prefix of it. Comparing the other way would let
        a stored abbreviation claim a longer id it may not be.
        """
        return sha.startswith(self.sha)

    def __str__(self) -> str:
        return f"git:{self.identity}@{self.sha}"


@dataclass(frozen=True)
class PrId:
    """One pull request, qualified by the repository it was opened against."""

    identity: str
    number: int

    def __post_init__(self) -> None:
        _require(_IDENTITY, self.identity, "repo identity")
        if isinstance(self.number, bool) or not isinstance(self.number, int) or self.number < 1:
            raise DetailIdError(f"pr number must be a positive integer: {self.number!r}")

    def __str__(self) -> str:
        return f"pr:{self.identity}#{self.number}"


@dataclass(frozen=True)
class GraphId:
    """One node of one round of one tracked run — where the ledger stores it."""

    run_id: str
    round: int
    node: str

    def __post_init__(self) -> None:
        _require(_TOKEN, self.run_id, "run id")
        _require(_TOKEN, self.node, "node id")
        if isinstance(self.round, bool) or not isinstance(self.round, int) or self.round < 1:
            raise DetailIdError(f"round must be a positive integer: {self.round!r}")

    @property
    def round_name(self) -> str:
        """The round's directory name, zero-padded as the ledger writes it."""
        return f"round-{self.round:02d}"

    def __str__(self) -> str:
        return f"graph:{self.run_id}/{self.round}/{self.node}"


DetailId: TypeAlias = OneharnessId | GitId | PrId | GraphId


def _require(pattern: str, value: object, label: str) -> None:
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise DetailIdError(f"{label} does not satisfy {pattern}: {value!r}")


def looks_like_detail_id(value: str) -> bool:
    """Whether ``value`` claims a typed-id namespace, well-formed or not."""
    return value.startswith(PREFIXES)


def parse_detail_id(value: str) -> DetailId | None:
    """Parse a typed detail id, or return None for a string that is not one.

    ``None`` means "not a typed id" — a legacy oneharness id or substring, which
    every caller must go on accepting. A string that *claims* a namespace but
    breaks its grammar raises instead: it is a typo in an id the user meant, and
    silently degrading it to a substring search would answer a question they did
    not ask (``pr:owner/repo#abc`` would quietly match on the literal text).
    """
    if not isinstance(value, str) or not looks_like_detail_id(value):
        return None
    if match := _OH.fullmatch(value):
        return OneharnessId(history_id=match.group(1))
    if match := _GIT.fullmatch(value):
        return GitId(identity=match.group(1), sha=match.group(2))
    if match := _PR.fullmatch(value):
        return PrId(identity=match.group(1), number=int(match.group(2)))
    if match := _GRAPH.fullmatch(value):
        return GraphId(run_id=match.group(1), round=int(match.group(2)), node=match.group(3))
    raise DetailIdError(
        f"{value!r} is not a valid typed detail id; expected one of "
        "oh:<history-id>, git:<owner/name>@<sha>, pr:<owner/name>#<number>, "
        "graph:<run>/<round>/<node>"
    )
