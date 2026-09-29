"""route -- does this harness actually put the PAZRAS entry point in front of
the model, or does it only promise to?

THE QUESTION IS NOT "IS IT CONFIGURED". Configuration is what we already
believe; a CONTEXT.md asserting "All three harnesses resolve it; verified by
running them" is a claim, and a symlink existing is not that verification. The only
evidence that settles it is the request body: either the bytes are there or
they are not. This module reads the capture `harnesses.py` brought back and
classifies each canonical file by how it arrived.

THREE OUTCOMES, and they are not degrees of the same thing:

  expanded   the file's own words are in the prompt. The model has it whether
             or not it decides to look. This is a mechanism.
  pointer    only the path is present. The model must choose to read it, and
             may not. This is a request -- level 4 in memory-mechanisms.md --
             and it is what `~/.hermes/memories/USER.md` is by construction.
  absent     neither. The harness is not wired to the tree at all.

CANARIES ARE DERIVED FROM THE LIVE FILE, NEVER HARD-CODED. A literal string
pasted in here would go stale the first time the user rewrites a sentence, and
a stale canary reports `absent` for a file that is present -- the worst failure
this test can have, because it looks like a real finding. `canary()` picks the
longest prose line in the file at run time, so the probe re-derives itself from
whatever the file says today. The chosen line is printed with the verdict so a
miss can be checked by hand rather than believed.

WHAT THIS CANNOT TELL YOU: whether the model *obeyed* what it was given.
Delivery is mechanical and checkable; compliance is behavioural and would need
a live model, a bill, and a nondeterministic answer. This test is deliberately
the mechanical half -- it fails loudly when the wiring breaks, which is the
failure that goes unnoticed.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
import re

CANONICAL = ("CONTEXT.md", "user/user-identity.md", "conventions/collaboration.md",
             "boundaries/read-write.md")

# What each file is *supposed* to be. `CONTEXT.md` and the always-loaded pair
# are compiled into each harness's entry point by `clew spin`, so
# `pointer` for any of them is a compile that did not run or did not land.
# The boundary prose is reached on demand and is expected to stay a pointer.
EXPECTED = {"CONTEXT.md": "expanded", "user/user-identity.md": "expanded",
            "conventions/collaboration.md": "expanded",
            "boundaries/read-write.md": "pointer"}

_TABLE = re.compile(r"^\s*[|<#>-]")


def canary(path: pathlib.Path) -> str:
    """The longest plain prose line in the file: distinctive, and it moves with
    the file. Table rows, headings, HTML comments and blockquotes are skipped --
    they repeat across files and would match the wrong one."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    prose = [l.strip() for l in lines
             if len(l.strip()) > 40 and not _TABLE.match(l)]
    return max(prose, key=len) if prose else ""


@dataclasses.dataclass
class Finding:
    file: str
    status: str          # expanded | pointer | absent
    expected: str
    canary: str
    where: str = ""

    @property
    def ok(self) -> bool:
        order = {"absent": 0, "pointer": 1, "expanded": 2}
        return order[self.status] >= order[self.expected]


def probe(capture: dict | None, agents: pathlib.Path) -> list[Finding]:
    blob = json.dumps(capture.get("body") if capture else {}, ensure_ascii=False)
    out = []
    for name in CANONICAL:
        path = agents / name
        mark = canary(path)
        if mark and mark in blob:
            status, where = "expanded", "prompt body"
        elif f"agents/{name}" in blob or name in blob:
            status, where = "pointer", "named only"
        else:
            status, where = "absent", ""
        out.append(Finding(file=name, status=status, expected=EXPECTED[name],
                           canary=mark[:70], where=where))
    return out


def verdict(findings: list[Finding]) -> str:
    if not findings:
        return "unknown"
    if all(f.ok for f in findings):
        return "pass"
    if any(f.status == "absent" and f.expected == "expanded" for f in findings):
        return "fail"
    return "warn"
