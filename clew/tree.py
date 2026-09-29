"""tree -- what it costs to *navigate* the tree, as opposed to be handed it.

THE HARNESS AUDIT AND THIS ONE MEASURE DIFFERENT MONEY. `harnesses.py` measures
the fixed prefix: bytes the harness injects whether or not they are wanted, paid
once per session. This module measures the variable part -- what an agent spends
reading down the routing tables to reach an answer. The first is a tax; the
second is a route, and a route can be shortened.

ROUTES ARE PARSED, NOT LISTED. The walk an agent makes is written down already,
in the markdown routing tables of `CONTEXT.md` and each `knowledge/**/CONTEXT.md`.
This module reads those tables and follows the paths in them. A hand-maintained
list of routes here would be a fourth copy of the same fact and would drift the
week someone edits a table -- the failure `CLAUDE.md` rule 2 names. The cost of
parsing is that an unparseable row is reported as such rather than silently
dropped: `unresolved` in the output means a routing table points somewhere that
does not exist, which is a defect in the table, not in the parser.

A READ IS A WHOLE FILE. Agents `Read` a leaf; they rarely grep one. So the model
here is: every file on the route is paid in full, once. That over-states an
agent that greps and under-states one that re-reads after compaction; it is the
honest middle, and it is the number the budgets in `README.md` are written
against.

WHAT THIS DELIBERATELY DOES NOT MODEL: tool-call overhead (the Read call and its
result envelope, ~50-100 tokens a hop) and re-reads after compaction. Both are
real; neither changes which file is the sink, which is the question this answers.
"""
from __future__ import annotations

import dataclasses
import pathlib
import re

from . import links, tokens

# `| question | `path` — comment |` rows. Both columns matter: the question is
# what an agent matches on, the backticked paths are where it goes.
ROW = re.compile(r"^\|(?P<q>[^|]+)\|(?P<t>[^|]+)\|", re.M)
# Only the routing table is a route. `CONTEXT.md` holds a second table -- write
# authority -- with the same pipe syntax, and reading its rows as routes turned
# "`journal/**`" into a question an agent might ask. Scope by heading.
SECTION = re.compile(r"^##\s+Routing table\s*$(?P<body>.*?)(?=^##\s|\Z)",
                     re.M | re.S)

# Budgets asserted in agents/README.md §The five layers. Restated here ONLY as
# the thing to compare against; if they disagree, README.md is the source and
# this is the bug.
BUDGETS = {"L0": 800, "L1": 1800, "L2": 300}

# The always-loaded set, as compiled into every harness by `clew spin`.
# A NAME THAT NO LONGER EXISTS MUST NOT PASS SILENTLY: `measure()` returns None
# for a missing file, so listing a deleted name here would score the layer as
# zero tokens and report the budget as holding. That is what happened when
# `USER.md`/`MEMORY.md` were moved on 2026-09-04 and the check stayed green
# against files that were gone -- `layers()` now asserts each one is present.
ALWAYS_LOADED = ("CONTEXT.md", "user/user-identity.md", "conventions/collaboration.md")

# Hard character caps on the always-loaded files (README.md §Displacement).
CAPS = {"user/user-identity.md": 1375}


@dataclasses.dataclass
class File:
    rel: str
    bytes: int
    tokens: int


@dataclasses.dataclass
class Route:
    question: str
    hops: list          # list[File]
    unresolved: list    # targets named by the table that do not exist
    external: list = dataclasses.field(default_factory=list)  # outside agents/

    @property
    def tokens(self) -> int:
        return sum(f.tokens for f in self.hops)


def measure(path: pathlib.Path, root: pathlib.Path) -> File | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, IsADirectoryError):
        return None
    return File(rel=path.relative_to(root).as_posix(),
                bytes=len(text.encode()), tokens=tokens.count(text))


def _targets(cell: str) -> list[str]:
    """The paths a routing-table row sends you to. Prose in the cell is
    ignored; only backticked tokens that look like paths count, because that is
    the convention the tables actually follow.

    The "does this token name a file" test is `links._is_candidate`, not a
    local re-derivation. `links.py` and this module once carried two different
    answers to that one question -- this module's looser `"." in name` rule vs
    `links.py`'s metacharacter/`@`/URL rejection and known-extension list --
    and they had already diverged in intent if not yet in outcome. Sharing the
    one predicate makes a backticked token mean the same thing in the pointer
    audit and the cost measurement, and stops the two from drifting apart.
    """
    out = []
    for tick in links.TICKED.findall(cell):
        tick = tick.strip()
        if links._is_candidate(tick):
            out.append(tick)
    return out


def _resolve(target: str, router: pathlib.Path, root: pathlib.Path) -> pathlib.Path | None:
    """Routing tables use paths relative to the file they are written in,
    sometimes with `../`, and sometimes -- like the rest of the tree's
    prose, per `links._roots()` -- relative to a nearer ancestor directory
    instead of the router's own. `knowledge/operations/context-budget.md`
    writing `operations/boundaries.md` means "relative to `knowledge/`", not
    to `operations/`'s own directory; a router one level down is written by
    the same convention and must be resolved the same way, or a real target
    silently drops out of the cost measurement instead of counting as a hop.

    Shares `links._roots()` rather than re-deriving the ancestor walk, so the
    two modules cannot drift back out of sync on what "relative" means here.
    Refuses to leave the tree, same as before.
    """
    for base in links._roots(router, root):
        cand = (base / target / "CONTEXT.md") if target.endswith("/") else base / target
        try:
            cand = cand.resolve()
            cand.relative_to(root.resolve())
        except (OSError, ValueError):
            continue
        if cand.is_file():
            return cand
    return None


def routes(agents: pathlib.Path) -> list[Route]:
    """One route per row of the top-level routing table, expanded one level:
    a row that lands on a `CONTEXT.md` router continues into that router's own
    rows, because that is what 'then descend' in the table means."""
    root = agents
    entry = root / "CONTEXT.md"
    if not entry.is_file():
        return []
    base_files = [measure(root / n, root) for n in ALWAYS_LOADED]
    base_files = [f for f in base_files if f]

    text = entry.read_text(encoding="utf-8", errors="replace")
    section = SECTION.search(text)
    if section is None:
        return []
    out = []
    for m in ROW.finditer(section.group("body")):
        question = m.group("q").strip()
        if not question or set(question) <= set("- ") or question.startswith("You need"):
            continue
        hops, unresolved, external = list(base_files), [], []
        for target in _targets(m.group("t")):
            resolved = _resolve(target, entry, root)
            if resolved is None:
                # A route out of the tree (`../CLAUDE.md`) is legitimate and
                # unmeasurable here; a dangling one is a defect. Say which.
                (external if target.startswith("..") else unresolved).append(target)
                continue
            f = measure(resolved, root)
            if f and f.rel not in {h.rel for h in hops}:
                hops.append(f)
                if resolved.name == "CONTEXT.md":
                    hops.extend(_descend(resolved, root, {h.rel for h in hops}))
        out.append(Route(question=question, hops=hops,
                         unresolved=unresolved, external=external))
    return out


def _descend(router: pathlib.Path, root: pathlib.Path, seen: set) -> list:
    """The single most expensive leaf a router can send you to. Not the sum:
    an agent reads one leaf per question, and summing every leaf under a router
    would answer 'what if I bulk-loaded the tree' -- the thing the loading
    discipline forbids."""
    best = None
    for target in _targets(router.read_text(encoding="utf-8", errors="replace")):
        resolved = _resolve(target, router, root)
        if resolved is None or resolved.name == "CONTEXT.md":
            continue
        f = measure(resolved, root)
        if f and f.rel not in seen and (best is None or f.tokens > best.tokens):
            best = f
    return [best] if best else []


def inventory(agents: pathlib.Path) -> list[File]:
    """Every markdown/yaml file in the tree, journal excluded -- the journal is
    never on a route by invariant (`CONTEXT.md` §Loading discipline)."""
    out = []
    for path in sorted(agents.rglob("*")):
        if not path.is_file() or path.suffix not in (".md", ".yaml", ".yml"):
            continue
        rel = path.relative_to(agents).as_posix()
        if rel.startswith(("journal/", ".obsidian/", "loredb/")):
            continue
        f = measure(path, agents)
        if f:
            out.append(f)
    return out


def layers(agents: pathlib.Path) -> dict:
    """The always-paid part, against the budgets README.md asserts."""
    def tok(*names):
        return sum(f.tokens for f in (measure(agents / n, agents) for n in names) if f)
    l1 = [n for n in ALWAYS_LOADED if n != "CONTEXT.md"]
    missing = [n for n in ALWAYS_LOADED if not (agents / n).is_file()]
    return {
        "L0": {"files": ["CONTEXT.md"], "tokens": tok("CONTEXT.md"),
               "budget": BUDGETS["L0"], "missing": [m for m in missing if m == "CONTEXT.md"]},
        "L1": {"files": l1, "tokens": tok(*l1), "budget": BUDGETS["L1"],
               "missing": [m for m in missing if m != "CONTEXT.md"]},
    }


def caps(agents: pathlib.Path) -> list[dict]:
    """The two hard character caps, checked. They are the mechanism that keeps
    the always-loaded layer constant (`README.md` §Displacement)."""
    out = []
    for name, cap in CAPS.items():
        path = agents / name
        if not path.is_file():
            # Absent is over-budget, not under it. A cap on a file nobody can
            # find is a check that always passes.
            out.append({"file": name, "chars": 0, "cap": cap, "over": True,
                        "note": "missing"})
            continue
        chars = len(path.read_text(encoding="utf-8", errors="replace"))
        out.append({"file": name, "chars": chars, "cap": cap, "over": chars > cap})
    return out
