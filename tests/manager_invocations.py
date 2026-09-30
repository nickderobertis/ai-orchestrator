"""Every invocation a manager session is sanctioned to run, and every one it is not.

One table, read by both of its readers and copied by neither: the deterministic gate
(`tests/test_agent_allowlist.py`) holds `config/manager-allowlist.toml` to it rule by
rule, and the live probe (`scripts/allowlist-probe.py`, `just probe-allowlist`) drives a
real Claude Code and a real Codex through every row against the rendered files. A row is
the command line as a manager writes it, with `{placeholders}` for what changes between
uses; the verdict each tool must reach; and the passage of `AGENTS.md` that sanctions or
forbids it, quoted so the gate can hold the quotation to the document.

A row is checked on both tools unless it names one, and then says why the other cannot
hold it. The reasons are few and each is the tool's own: Codex's execpolicy rules govern
shell commands only, match literal leading tokens only — no wildcard, no bound on a
command's length — and Codex reads a file inside its sandbox without asking any rule.
"""

from __future__ import annotations

import re
from typing import Literal, NamedTuple, NewType

#: A row's name, unique across the table: what the probe's `--row` takes and prints.
InvocationId = NewType("InvocationId", str)
Tool = Literal["claude-code", "codex"]
#: `allow`: a rule lets it through. `refuse`: `AGENTS.md` forbids it, and no rule may.
#: `unlisted`: sanctioned, but no rule can grant it without also granting something
#: forbidden, so it is left to each tool's own judgement — and no rule may cover it.
Verdict = Literal["allow", "refuse", "unlisted"]
#: How the row is carried out: a shell command, or the agent's own file-write tool
#: writing the path the command names.
Kind = Literal["shell", "write"]

BOTH: tuple[Tool, ...] = ("claude-code", "codex")
CLAUDE_ONLY: tuple[Tool, ...] = ("claude-code",)

#: Codex's rules file holds `prefix_rule`s, which match the leading literal tokens of a
#: command: an exact command cannot be bounded, and a wildcard inside a pattern cannot be
#: spelled, so `oneharness sync` reports either as unmapped rather than widen it.
CODEX_EXACT = (
    "an exact command: a Codex prefix rule cannot bound a command's length, and the "
    "prefix that would express it also admits a suffix this grant withholds"
)
CODEX_READ = (
    "Codex runs a read-only command inside its sandbox without consulting a rule, and "
    "the rule that would name it is a grant over every file on the host"
)
CODEX_CD = (
    "Codex holds each part of a compound command to a rule, and a `cd` rule cannot be "
    "bounded to this checkout without naming a host's path; Claude Code passes a `cd` "
    "into its working directory without one"
)
CODEX_WRITE_TOOL = "Codex's rules govern shell commands only; its own file edits are its sandbox's"


class Invocation(NamedTuple):
    """One sanctioned or forbidden manager invocation."""

    #: Unique, and used by the probe to make each row's command distinguishable.
    id: InvocationId
    #: The command line, `{placeholder}`s standing for run ids, paths and names.
    command: str
    verdict: Verdict
    #: `AGENTS.md`'s own words for why, whitespace-normalized; the gate holds the
    #: document to containing them.
    sanctioned_by: str
    #: The tools this row's verdict is held on.
    tools: tuple[Tool, ...] = BOTH
    #: Why a tool is left out of `tools`; empty exactly when both are held.
    why_one_tool: str = ""
    kind: Kind = "shell"


def _allow(
    id: str, command: str, sanctioned_by: str, *, codex: str = "", kind: Kind = "shell"
) -> Invocation:
    tools = CLAUDE_ONLY if codex else BOTH
    return Invocation(InvocationId(id), command, "allow", sanctioned_by, tools, codex, kind)


def _unlisted(id: str, command: str, sanctioned_by: str) -> Invocation:
    return Invocation(InvocationId(id), command, "unlisted", sanctioned_by)


def _refuse(id: str, command: str, sanctioned_by: str, *, kind: Kind = "shell") -> Invocation:
    tools = CLAUDE_ONLY if kind == "write" else BOTH
    return Invocation(
        InvocationId(id),
        command,
        "refuse",
        sanctioned_by,
        tools,
        CODEX_WRITE_TOOL if kind == "write" else "",
        kind,
    )


_VIEWS = (
    "`just runs`, `just status`, `just host`, `just results`, `just transcript`, and "
    "`just agents` are how a run is read"
)
_AFTER_LAUNCH = "After `just orchestrate`, use **only** `just channel-next`, `just channel-reply`"
_WATCH = "`just watch` is invoked directly, with no loop around it"
_UNFINISHED = "Before you end a turn, `just unfinished` answers what you still owe"
_UNPUBLISHED = (
    "For every identity from anywhere, or for the sessions your own runs opened, with "
    "what each costs in disk, read `just unpublished` instead"
)
_ENVELOPE = "write the envelope to `scratch/envelope.json` with your own file-write tool"
_PLAN = "`just plan <brief.md>` turns a manager-written brief into the project the planner authors"
_FINISH = "`just finish-plan <brief>` is its tail"
_CHECK_PLAN = "`just check-plan` reads a project against the bar each node will be judged against"
_REVIEW_PLAN = (
    "A plan you wrote or tweaked is unreviewed until `just review-plan <source:project>` "
    "has read it"
)
_COPY_PLAN = "A `just copy-plan` refused for a rate limit"
_PLANS_READ = "Read one with `just plans project show plans:<project>`"
_FIELDS = "Read the plan with `just plans sources fields <source>`, which writes nothing"
_LAUNCH = "Start with `just orchestrate <source:project>` from the repository root"
_DETACH = "`--detach` is for several runs supervised at once"
_ADOPT = (
    "A run whose driver is dead over an intact ledger is adopted with `just orchestrate "
    "--adopt <run-id>`"
)
_VERBS = "Every branch state has a verb, decided by what the branch *is*"
_RECOVERABLE = "`just recoverable` names which verb a branch needs"
_WORK_STATUS = (
    "`just work-status <change-url|session|branch|commit>` reports everything `onevcs` "
    "knows about one piece of work"
)
_IMPORT = (
    "`just import-branch <branch> --repo <checkout>` makes a branch finished in a session worktree"
)
_RECLAIM = "answered with `just reclaim-branch <branch> --repo <checkout>`"
_SYNC = "`just sync` fast-forwards a publication checkout"
_REPOS = (
    "confirm the identity and its checkout aliases with `just repos` and its resolved "
    "routing with `onevcs rules check <repo>`"
)
_REPOS_APPLY = "it is installed by `just repos-apply`, idempotently, so re-run it after an edit"
_SWEEP = "`just sweep` runs `onevcs sweep` then `oneagentgraph sweep`"
_ACKNOWLEDGE = (
    "answer such a hold with `onevcs release acknowledge <REFERENCE> --target <NAME> "
    "--version <VERSION>`"
)
_RELEASE_STATUS = "the adopted `onevcs` has the first `release status` that decides such a landing"
_POOL = "`onevcs pool status <repo>` reads them"
_SESSION_CLOSE = "`onevcs session close <session>` closes a session you opened"
_SUPERSESSIONS = "`uv run onepipeline supersessions <run-id> --record`, run once by hand"
_TICKETS = (
    "`python -m orchestrator.follow_up_tickets statuses` prints the vocabulary every agent reads"
)
_BOARD_ITEMS = (
    "**The board is searched, never listed: every query of it is `python -m "
    "orchestrator.follow_up_tickets board-items`.**"
)
_CHECK_DISPOSITIONS = (
    "`python -m orchestrator.follow_up_tickets check-dispositions` is what holds an account to that"
)
_CHECK_RESPONSES = (
    "`python -m orchestrator.follow_up_tickets check-responses` is what holds an account to that"
)
_FOLLOW_UP = (
    "Draft a follow-up with `just follow-up <run-id>` only when nobody needs it during the run"
)
_FOLLOW_UPS = (
    "Decide with the user whether to verify its drafts by hand with `just follow-ups <run-id>`"
)
_FEEDBACK = "anything more goes back with `just follow-ups <run-id> --feedback FILE`"
_COMMENTS = "People's board comments are answered by `just follow-ups-answer-comments` (`--dry-run`"
_WORKTREE_READ = (
    "When a dispatch looks silent, read its worktree — its commits, its tree, the "
    "`.logs/` its innermost stage writes"
)
_LOG_READ = "follow the innermost log and never read a live command through `/proc`"
_RECIPES = "Use the `just` recipes (`just --list` is the index) and never hand-roll equivalents"
_DEV_TIER = (
    "The development tier — this repository's own recipes, `uv sync` and `uv lock` over its "
    "lockfile, `llmlint`, the `onejudge` and `oneharness` reads, and `onemessagebus status` "
    "over a run's channel — is granted"
)
_CLONE = "and so is `git clone`: a clone adds a checkout and changes no remote or base"
_BARE = "Confirm `git config core.bare` is `false`"
_GATHERING = "The pre-launch `check-gathering` read"
_GIT_OUTPUT_UNGRANTED = "whose `--output` writes a file"
_GIT_C_UNGRANTED = "A `git -C <path>` read is not granted, because Claude Code's `*` spans words"
_CD = "an invocation written `cd <this checkout> && <verb>` passes on the verb's grant"
_STOP = (
    "`just stop` refuses another manager's run and is deliberately outside the manager's allowlist"
)
_SHUTDOWN = "`just shutdown` is outside it for the same reason and more so"
_APPROVE = "record their decision with `just approve-design <source>:<project>`"
_ACK_BRANCH = (
    "acknowledge work deliberately kept with `just unpublished --acknowledge <branch> "
    '--reason "<why>"`'
)
_ACK_RUN = 'or close it with `just unwatched --acknowledge <run> --reason "<why>"`'
_FORBIDDEN = "What stays off it is what this document forbids a manager"
_NO_VERIFY = "a hook bypassed with `git -c core.hooksPath=…` or `--no-verify`"
_BYPASS = "Nothing that changes what a remote or a base branch sees bypasses `onevcs`"
_KILL = "never derive a process list from `ps` and signal it"
_RUN_STATE = "never rebuild run state from `events.jsonl`, `ps`, or a clone's `git log`"

INVOCATIONS: tuple[Invocation, ...] = (
    _allow("bootstrap", "just bootstrap", _DEV_TIER, codex=CODEX_EXACT),
    _allow("check", "just check", _DEV_TIER, codex=CODEX_EXACT),
    _allow("test", "just test", _DEV_TIER, codex=CODEX_EXACT),
    _allow("test-e2e", "just test-e2e", _DEV_TIER, codex=CODEX_EXACT),
    _allow("lint", "just lint", _DEV_TIER, codex=CODEX_EXACT),
    _allow("format", "just format", _DEV_TIER, codex=CODEX_EXACT),
    _allow("format-check", "just format-check", _DEV_TIER, codex=CODEX_EXACT),
    _allow("typecheck", "just typecheck", _DEV_TIER, codex=CODEX_EXACT),
    _allow("upgrade", "just upgrade", _DEV_TIER, codex=CODEX_EXACT),
    _allow("validate-personas", "just validate-personas", _DEV_TIER, codex=CODEX_EXACT),
    _allow("new-persona", "just new-persona {label}", _DEV_TIER),
    _allow("session-setup", "just session-setup", _DEV_TIER, codex=CODEX_EXACT),
    _allow("sync-allowlist", "just sync-allowlist", _DEV_TIER, codex=CODEX_EXACT),
    _allow("setup-llmlint", "just setup-llmlint", _DEV_TIER, codex=CODEX_EXACT),
    _allow("lint-llm", "just lint-llm {label}", _DEV_TIER),
    _allow("lint-llm-validate", "just lint-llm-validate {label}", _DEV_TIER),
    _allow("lint-llm-diff", "just lint-llm-diff {base}", _DEV_TIER),
    _allow("llmlint", "llmlint history {label}", _DEV_TIER),
    _allow("setup-llmlint-script", "./scripts/setup-llmlint.sh", _DEV_TIER, codex=CODEX_EXACT),
    _allow("session-setup-script", "./scripts/session-setup.sh", _DEV_TIER, codex=CODEX_EXACT),
    _allow("uv-sync", "uv sync", _DEV_TIER, codex=CODEX_EXACT),
    _allow("uv-sync-frozen", "uv sync --frozen", _DEV_TIER, codex=CODEX_EXACT),
    _allow("uv-lock", "uv lock", _DEV_TIER, codex=CODEX_EXACT),
    _allow("uv-lock-check", "uv lock --check", _DEV_TIER, codex=CODEX_EXACT),
    _allow("onejudge-schema", "onejudge schema", _DEV_TIER, codex=CODEX_EXACT),
    _allow("onejudge-init", "onejudge init --force", _DEV_TIER, codex=CODEX_EXACT),
    _allow("oneharness-detect", "oneharness detect", _DEV_TIER, codex=CODEX_EXACT),
    _allow("oneharness-config", "oneharness config --format json", _DEV_TIER),
    _allow("onemessagebus-status", "onemessagebus status {run}", _DEV_TIER),
    _allow("git-status-here", "git status", _DEV_TIER, codex=CODEX_EXACT),
    _allow("git-bare", "git config core.bare", _BARE, codex=CODEX_EXACT),
    _allow("git-clone", "git clone {reference} {label}", _CLONE),
    _allow("telemetry", "just telemetry {run}", _RECIPES),
    _allow("goals", "just goals {run}", _RECIPES),
    _allow("history", "just history {run}", _RECIPES),
    _allow("history-show", "just history-show {run}", _RECIPES),
    _allow("replan", "just replan {brief}", _RECIPES),
    _allow("register-repo", "just register-repo {co}", _RECIPES),
    _allow("status-cut", "just status {run} --no-providers", _VIEWS),
    _allow("status-all", "just status", _VIEWS),
    _allow(
        "watch-log",
        "just watch {run} --until surface --until settled --until nothing-driving "
        "--timeout none --log {log}",
        _WATCH,
    ),
    _allow(
        "watch-rearm",
        "just watch {run} --until surface --until settled --until nothing-driving "
        "--timeout none --cursor {cursor} --log {log}",
        _WATCH,
    ),
    _allow("runs", "just runs", _VIEWS),
    _allow("monitor", "just monitor {run}", _AFTER_LAUNCH),
    _allow("unwatched", "just unwatched", _AFTER_LAUNCH, codex=CODEX_EXACT),
    _allow("unpublished-host", "just unpublished --host", _UNPUBLISHED, codex=CODEX_EXACT),
    _allow("unfinished", "just unfinished", _UNFINISHED, codex=CODEX_EXACT),
    _allow("unfinished-json", "just unfinished --json", _UNFINISHED, codex=CODEX_EXACT),
    _allow("unfinished-surface", "just unfinished --print-surface", _UNFINISHED, codex=CODEX_EXACT),
    _allow(
        "unpublished-surface", "just unpublished --print-surface", _UNPUBLISHED, codex=CODEX_EXACT
    ),
    _allow("host", "just host", _VIEWS),
    _allow("results", "just results {run}", _VIEWS),
    _allow("transcript", "just transcript {run} {node}", _VIEWS),
    _allow("agents", "just agents {run}", _VIEWS),
    _allow("work-status", "just work-status {branch}", _WORK_STATUS),
    _allow("recoverable", "just recoverable --repo {co}", _RECOVERABLE),
    _allow("channel-next", "just channel-next {run}", _AFTER_LAUNCH),
    _allow("channel-reply", "just channel-reply {run} {envelope}", _AFTER_LAUNCH),
    _allow(
        "channel-reply-correlation",
        "just channel-reply {run} {envelope} --correlation {correlation}",
        _AFTER_LAUNCH,
    ),
    _allow("envelope-write", "{envelope}", _ENVELOPE, codex=CODEX_WRITE_TOOL, kind="write"),
    _allow("plan", "just plan {brief}", _PLAN),
    _allow("finish-plan", "just finish-plan {brief}", _FINISH),
    _allow("check-plan", "just check-plan {project}", _CHECK_PLAN),
    _allow("review-plan", "just review-plan {project}", _REVIEW_PLAN),
    _allow("copy-plan", "just copy-plan {project}", _COPY_PLAN),
    _allow("plans-show", "just plans project show {project}", _PLANS_READ),
    _allow("plans-fields", "just plans sources fields plans", _FIELDS, codex=CODEX_EXACT),
    _allow(
        "plans-fields-authoring",
        "just plans sources fields authoring",
        _FIELDS,
        codex=CODEX_EXACT,
    ),
    _allow(
        "plans-fields-followups",
        "just plans sources fields followups",
        _FIELDS,
        codex=CODEX_EXACT,
    ),
    _allow("orchestrate", "just orchestrate {project}", _LAUNCH),
    _allow("orchestrate-detach", "just orchestrate {project} --detach", _DETACH),
    _allow("orchestrate-adopt", "just orchestrate --adopt {run}", _ADOPT),
    _allow("publish-branch", "just publish-branch {branch} --repo {co}", _VERBS),
    _allow("repo-recover", "just repo-recover {branch} --repo {co}", _VERBS),
    _allow("integrate", "just integrate --repo {co}", _VERBS),
    _allow("import-branch", "just import-branch {branch} --repo {co}", _IMPORT),
    _allow("reclaim-branch", "just reclaim-branch {branch} --repo {co}", _RECLAIM),
    _allow("sync", "just sync", _SYNC),
    _allow("repos", "just repos", _REPOS),
    _allow("repos-apply", "just repos-apply", _REPOS_APPLY),
    _allow("sweep", "just sweep", _SWEEP),
    _allow(
        "release-acknowledge",
        "onevcs release acknowledge {reference} --target {target} --version {version}",
        _ACKNOWLEDGE,
    ),
    _allow("release-status", "onevcs release status {reference}", _RELEASE_STATUS),
    _allow("session-close", "onevcs session close {session}", _SESSION_CLOSE),
    _allow("rules-check", "onevcs rules check {co}", _REPOS),
    _allow("pool-status", "onevcs pool status {co}", _POOL),
    _allow("follow-up", "just follow-up {run}", _FOLLOW_UP),
    _allow("follow-ups", "just follow-ups {run}", _FOLLOW_UPS),
    _allow("follow-ups-feedback", "just follow-ups {run} --feedback {feedback}", _FEEDBACK),
    _allow("follow-ups-comments", "just follow-ups-answer-comments", _COMMENTS),
    _allow("follow-ups-comments-dry-run", "just follow-ups-answer-comments --dry-run", _COMMENTS),
    _allow("supersessions", "uv run onepipeline supersessions {run} --record", _SUPERSESSIONS),
    _allow(
        "tickets-statuses",
        "uv run python -m orchestrator.follow_up_tickets statuses",
        _TICKETS,
        codex=CODEX_EXACT,
    ),
    _allow(
        "tickets-board-items",
        "uv run python -m orchestrator.follow_up_tickets board-items --board followups "
        "--search cursor",
        _BOARD_ITEMS,
    ),
    _allow(
        "tickets-dispositions",
        "uv run python -m orchestrator.follow_up_tickets check-dispositions {run}",
        _CHECK_DISPOSITIONS,
    ),
    _allow(
        "tickets-responses",
        "uv run python -m orchestrator.follow_up_tickets check-responses {run}",
        _CHECK_RESPONSES,
    ),
    _allow(
        "tickets-gathering",
        "uv run python -m orchestrator.follow_up_tickets check-gathering {run}",
        _GATHERING,
    ),
    _allow("log-cat", "cat {runs}/{run}/driver.log", _LOG_READ, codex=CODEX_READ),
    _allow("log-tail", "tail -n 50 .logs/{label}.log", _LOG_READ, codex=CODEX_READ),
    _allow("log-grep", "grep -n error {runs}/{run}/driver.log", _LOG_READ, codex=CODEX_READ),
    _allow(
        "worktree-log-tail",
        "tail -n 50 {workspace}/.logs/{label}.log",
        _WORKTREE_READ,
        codex=CODEX_READ,
    ),
    _allow(
        "worktree-log-grep",
        "grep -n error {workspace}/.logs/{label}.log",
        _WORKTREE_READ,
        codex=CODEX_READ,
    ),
    # Sanctioned reads of a worker's worktree or a registered checkout that no rule can
    # grant: the path sits between `git -C` and the verb, and a wildcard there spans words.
    _unlisted("git-log", "git -C {workspace} log --oneline HEAD ^{base}", _GIT_C_UNGRANTED),
    _unlisted(
        "git-diff", "git -C {workspace} diff --stat --merge-base {base} HEAD", _GIT_C_UNGRANTED
    ),
    _unlisted("git-log-output", "git log --oneline -1 --output={log}", _GIT_OUTPUT_UNGRANTED),
    _unlisted("git-show", "git -C {workspace} show --stat HEAD", _GIT_C_UNGRANTED),
    _unlisted("git-status", "git -C {workspace} status --short", _GIT_C_UNGRANTED),
    _unlisted("git-rev-parse", "git -C {workspace} rev-parse HEAD", _GIT_C_UNGRANTED),
    _unlisted("git-merge-base", "git -C {workspace} merge-base HEAD {base}", _GIT_C_UNGRANTED),
    # The same verbs behind the `cd <this checkout>` prefix most real invocations carry.
    _allow(
        "cd-and-status", "cd {checkout} && just status {run} --no-providers", _CD, codex=CODEX_CD
    ),
    _allow(
        "cd-semicolon-watch",
        "cd {checkout}; just watch {run} --timeout none --log {log}",
        _CD,
        codex=CODEX_CD,
    ),
    _allow("cd-and-channel-next", "cd {checkout} && just channel-next {run}", _CD, codex=CODEX_CD),
    _allow(
        "cd-and-import",
        "cd {checkout} && just import-branch {branch} --repo {co}",
        _CD,
        codex=CODEX_CD,
    ),
    _refuse("stop", "just stop {run}", _STOP),
    _refuse("shutdown", "just shutdown --mine", _SHUTDOWN),
    _refuse("approve-design", "just approve-design {project}", _APPROVE),
    _refuse(
        "unpublished-acknowledge",
        "just unpublished --acknowledge {branch} --reason kept",
        _ACK_BRANCH,
    ),
    _refuse(
        "unwatched-acknowledge",
        "just unwatched --acknowledge {run} --reason done",
        _ACK_RUN,
    ),
    _refuse("git-C-merge", "git -C {workspace} merge {branch}", _FORBIDDEN),
    _refuse("git-C-checkout", "git -C {workspace} checkout {branch}", _FORBIDDEN),
    _refuse("git-C-commit", "git -C {workspace} commit -m probe", _FORBIDDEN),
    # Each carries a read verb as a later word: the shape a `git -C * <verb>*` grant let
    # through, kept as the proof no such grant is back.
    _refuse("git-C-merge-log", "git -C {workspace} merge log-{branch}", _FORBIDDEN),
    _refuse("git-C-commit-status", 'git -C {workspace} commit -m "fix status"', _FORBIDDEN),
    _refuse("cd-git-merge", "cd {workspace} && git merge {branch}", _FORBIDDEN),
    _refuse("cd-git-checkout", "cd {workspace} && git checkout {branch}", _FORBIDDEN),
    _refuse("cd-git-commit", "cd {workspace} && git commit -m probe", _FORBIDDEN),
    _refuse("hooks-bypass", "git -c core.hooksPath=/dev/null commit -m probe", _NO_VERIFY),
    _refuse(
        "branch-protection",
        "gh api -X PUT repos/{owner}/{name}/branches/main/protection --input {file}",
        _BYPASS,
    ),
    _refuse("gh-api-write", "gh api -X POST repos/{owner}/{name}/issues -f title=probe", _BYPASS),
    _refuse("gh-run-cancel", "gh run cancel {ci_run}", _FORBIDDEN),
    _refuse("kill", "kill {pid}", _KILL),
    _refuse("launch-edit", "{runs}/{run}/launch.json", _RUN_STATE, kind="write"),
    _refuse("cursor-edit", "{runs}/{run}/channel/commands-cursor.json", _RUN_STATE, kind="write"),
    _refuse("launch-shell-edit", "echo {{}} > {runs}/{run}/launch.json", _RUN_STATE),
    _refuse(
        "cursor-shell-edit",
        "echo {{}} > {runs}/{run}/channel/commands-cursor.json",
        _RUN_STATE,
    ),
)

#: The placeholders a row may use. A row naming another is a typo the gate refuses.
PLACEHOLDERS = frozenset(
    {
        "run",
        "node",
        "log",
        "cursor",
        "co",
        "branch",
        "base",
        "envelope",
        "correlation",
        "brief",
        "project",
        "reference",
        "target",
        "version",
        "session",
        "feedback",
        "workspace",
        "runs",
        "label",
        "checkout",
        "owner",
        "name",
        "file",
        "ci_run",
        "pid",
    }
)
PLACEHOLDER = re.compile(r"(?<!\{)\{([a-z_]+)\}(?!\})")


def placeholders(invocation: Invocation) -> set[str]:
    """The placeholder names one row's command uses."""
    return set(PLACEHOLDER.findall(invocation.command))
