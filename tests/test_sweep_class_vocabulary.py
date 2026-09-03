"""The worktree-root class vocabulary has one producer, and four readers of it.

A drift gate rather than a journey, and it lives here rather than beside the sweep's
journeys for that reason: what it reconciles is two statements of one vocabulary inside
`scripts/sweep.sh`, which is a property of the script and not a thing an operator can
observe by running it. `tests/e2e/test_sweep_e2e.py` drives the real recipe and asserts
what an operator reads; nothing there can see a class that has no arm anywhere, because
a class nothing produces a directory for produces no line to read.

llmlint: ignore-file[shell_test_tiers_stay_split] This module runs no shell and drives
no script: it reads `scripts/sweep.sh` as text and reconciles two statements of one
vocabulary inside it, which is the offline drift gate `AGENTS.md` places in the root
unit tier beside the pins and the prose. There is no shell cost here for a tier of its
own to select or skip, so the rule's subject is absent at this site. Giving the sweep's
journeys an Nx project of their own — which is where the shell cost actually is — was
ruled out of this change and is a follow-up, for the reason stated at the same rule in
`tests/e2e/test_sweep_e2e.py`.
"""

from __future__ import annotations

import re

from orchestrator.root import REPO_ROOT

#: The composing sweep, whose worktree-root reading this reconciles.
WRAPPER = REPO_ROOT / "scripts" / "sweep.sh"

#: How `scripts/sweep.sh` is read for the worktree-root class vocabulary. `worktree_class`
#: is the producer and therefore the one declaration; the arms of the three dispatches
#: beside it and the listing order each have to carry all of it, and bash has no
#: exhaustiveness to say when one does not.
CLASS_PRODUCER = re.compile(r"^worktree_class\(\) \{\n(.*?)^\}", re.S | re.M)
#: `-printf` is `find`'s and prints a label of its own, so the lookbehind is what keeps
#: the producer's own classes apart from it.
CLASS_ANSWERED = re.compile(r"(?<!-)printf '([a-z]+)\\t")
CLASS_OWNER_ARMS = re.compile(
    r'^worktree_owner\(\) \{\n  case "\$1" in\n(.*?)\n  esac', re.S | re.M
)
#: Non-greedy to the first `esac`, so the two `case "$class" in` blocks are two blocks
#: rather than one span running from the first arm of one to the last of the other —
#: which is what a greedy read of them does, and it reports every arm as present in both.
CLASS_DISPATCH_ARMS = re.compile(r'case "\$class" in\n(.*?)\n *esac', re.S)
CLASS_ORDER = re.compile(r"for class in ([a-z ]+); do")
CLASS_ARM = re.compile(r"^ *([a-z]+)\)", re.M)


def test_every_class_the_worktree_reading_answers_with_is_carried_by_the_whole_report() -> None:
    """A class the producer can print and a dispatch has no arm for is silent drift.

    The reading answers one of a fixed set of classes, and four places downstream have
    to know that set: the owner printed under the listing, the sentence a directory
    gets, the label its class is folded into, and the order the listing is built in.
    Adding one touches all four by hand, and nothing in bash says when one is missed. A
    class with no arm of its own
    would take no arm at all, so a directory would be reported with an empty sentence
    or folded under an empty label, and its operator would be told nothing.

    Read out of the script rather than driven, deliberately: driving it can only assert
    the classes a journey thought to build a directory for, which is the same list
    going stale in a second place.
    """
    script = WRAPPER.read_text()
    produced = CLASS_PRODUCER.search(script)
    assert produced, "worktree_class is not in scripts/sweep.sh under the name this reads"
    answered = set(CLASS_ANSWERED.findall(produced.group(1)))
    assert answered, produced.group(1)

    owner = CLASS_OWNER_ARMS.search(script)
    assert owner, "worktree_owner is not in scripts/sweep.sh in the shape this reads"
    dispatches = CLASS_DISPATCH_ARMS.findall(script)
    sentences = [block for block in dispatches if "sentence=" in block]
    labels = [block for block in dispatches if "label=" in block]
    assert len(sentences) == 1 and len(labels) == 1, dispatches
    order = CLASS_ORDER.search(script)
    assert order, "the listing order is not in scripts/sweep.sh in the shape this reads"

    carried = {
        "the owner dispatch": set(CLASS_ARM.findall(owner.group(1))),
        "the per-directory sentence": set(CLASS_ARM.findall(sentences[0])),
        "the fold label": set(CLASS_ARM.findall(labels[0])),
        "the listing order": set(order.group(1).split()),
    }
    for site, classes in carried.items():
        assert classes == answered, (
            f"{site} carries {sorted(classes)} while worktree_class answers with "
            f"{sorted(answered)}; a class in one and not the other is a directory "
            f"reported under no prose of its own, or an operator told nothing about it"
        )
