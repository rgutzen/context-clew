"""links -- do the pointers in the tree still point at something?

WHY THIS EXISTS. The tree is held together by paths written in prose: routing
tables, `see:` fields, "further reading" lines. Nothing checks them. On
2026-09-04 a folder reorganisation left `agents/CONTEXT.md` routing to four
files that no longer existed, and the only way to notice was to follow each one
by hand. A pointer that does not resolve is the exact failure the routing tables
exist to prevent -- an agent is told where to look, looks, finds nothing, and
proceeds without the context it was promised. That failure is silent: no error,
no missing-file message, just an agent working from less than it was given.

TWO HALVES, AND THE SECOND ONE IS THE INTERESTING ONE.
  broken  -- a router points at a file that is not there.
  orphan  -- a file is there and nothing points at it.
The first is loud once you look; the second is invisible forever. An orphan is
a leaf that was written, is good, and will never be read, because no route
reaches it. Both are drift; only the first announces itself.

WHY IT MAY REWRITE HUMAN-OWNED FILES, WHICH NOTHING ELSE MAY DO. `user/**`,
`conventions/**` and `boundaries/**` are human-owned: agents propose, never
edit. This tool is the single declared exception, granted 2026-09-04, and the
reason is that it does not decide anything. A pointer repair is not a judgement
about content -- the human wrote the target, moved it, and the path is now a
typo for the file they themselves created. Refusing to fix it would leave the
human hand-patching paths after every move, which is how the tree drifted in
the first place.

The exception is bounded by one rule, and the rule is the whole safety
argument:

    A broken path is repaired ONLY when exactly one file in the tree
    carries that basename. Zero candidates, or two, and it is reported
    and left alone.

And by one more, which bounds WHERE a repair may write:

    Only the characters between the delimiters that marked the text as a
    path -- an inline code span, or a markdown link target -- are ever
    replaced. Free prose is never edited, at any offset.

The second rule is why the edit is splice-by-span rather than
`line.replace(old, new)`: a line reading "renamed `USER.md`, so USER.md is
gone" holds the same token twice, once as a pointer and once as prose, and a
string replace would rewrite both. The delimiters are what made it a pointer;
outside them the tool has no opinion.

That makes a repair a lookup, not a guess. It fixes the common case -- a file
that MOVED and kept its name -- and it deliberately fails on the case that
needs a person: a file that was RENAMED, or deleted outright, where the right
new text is a decision about meaning, not a path lookup. `MEMORY.md` is the
worked example: it was deleted, its content dispersed, and every pointer to it
must be rewritten by someone who knows where that content went. This tool
reports those and stops, which is the correct answer.

WHAT IS NOT A PATH. Backticks in this tree hold flags (`--check`), values
(`deny`, `authority: human`), globs (`knowledge/**`) and placeholders
(`<name>`). Treating one of those as a broken path and "repairing" it would be
worse than the drift. The discriminator is lexical and deliberately narrow --
see `PATHISH` -- and it fails closed: a token it cannot confidently call a path
is not one, and is never touched. Under-reporting is a nuisance; a false repair
edits a human's file for no reason.

JOURNAL IS EXCLUDED, ON PURPOSE. `journal/**` is append-only and records what
was true on a date. A stale path in a journal entry is not drift, it is the
record being accurate about a world that has since changed. Repairing one would
falsify it.
"""
from __future__ import annotations

import dataclasses
import pathlib
import re

# ── what counts as a path ────────────────────────────────────────────────────

# Inline code spans and markdown link targets are the two ways a path is written
# in this tree. Both are captured; neither is trusted until PATHISH agrees.
TICKED = re.compile(r"`([^`\n]+)`")
MDLINK = re.compile(r"\[[^\]\n]*\]\(([^)\s]+)\)")
# `agents/boundaries/*.yaml` carries `see:` targets that are pointers by
# declaration -- no backticks, because YAML is not prose. They went unchecked
# until 2026-09-04, when three of them survived a folder move and were quoted,
# stale, in the guard's own denial message.
YAML_SEE = re.compile(r"^\s*see:\s*(\S+)\s*$", re.M)

# A token is path-shaped when it either contains a directory separator or ends
# in an extension this tree actually uses. Everything else is prose in
# backticks. Anchors and trailing punctuation are stripped before the test.
EXTENSIONS = (".md", ".yaml", ".yml", ".json", ".py", ".sh", ".txt", ".bash")
BRANCH_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
PATHISH = re.compile(
    r"^(?:~/|\.\.?/)?"                    # optional ~/ or ./ or ../ prefix
    r"[A-Za-z0-9_.@+-]+"                  # first segment
    r"(?:/[A-Za-z0-9_.@+-]+)*"            # further segments
    r"/?$"                                # may name a directory
)

# Tokens that are path-shaped by accident. A bare word ending in nothing, a
# flag, a glob, a placeholder, a URL, or anything holding shell/regex syntax.
def _is_candidate(raw: str) -> bool:
    if not raw or raw[0] == "-":
        return False
    if any(c in raw for c in "*<>|$ \t'\"()[]{}!?=,;:"):
        return False
    if "://" in raw:
        return False
    # `owner/repo@sha` is a git ref, not a file, and it is exactly path-shaped.
    # No file in this tree carries `@` in its name, so the rule is total.
    if "@" in raw:
        return False
    if not PATHISH.match(raw):
        return False
    # Needs a separator or a known extension; `deny` and `local` have neither.
    return "/" in raw or raw.endswith(EXTENSIONS)


def _is_branch(raw: str, source: pathlib.Path) -> pathlib.Path | None:
    """A bare word naming a sibling directory is a route, not prose.

    The generated `skills/CONTEXT.md` names CATEGORIES (`development`, `os`)
    rather than paths, to stay inside its 2,400-char budget. Without this the
    chain from the entry point to any SKILL.md is broken and every skill in the
    library reports as orphaned -- 29 false positives that would train a reader
    to ignore the orphan list. Category routers name their skills the same way,
    so `SKILL.md` counts as an entry point too.
    """
    # NOT `str.isidentifier()`: every hyphenated skill name (`app-builder`,
    # `code-review`) fails it, which left 24 of 29 skills reporting as orphans
    # while `tdd` and `spike` resolved. Directory names are not Python names.
    if "/" in raw or raw.endswith(EXTENSIONS) or not BRANCH_NAME.match(raw):
        return None
    for entry in ("CONTEXT.md", "SKILL.md"):
        candidate = source.parent / raw / entry
        if candidate.exists():
            return candidate
    return None


def _clean(raw: str) -> str:
    """Strip an anchor and trailing sentence punctuation. `a.md#top,` -> `a.md`."""
    raw = raw.split("#", 1)[0]
    return raw.rstrip(".,;:)")


# ── resolution ───────────────────────────────────────────────────────────────

def _roots(source: pathlib.Path, agents: pathlib.Path) -> list[pathlib.Path]:
    """Where a relative path in `source` may legitimately be anchored.

    Order matters: nearest first, so `CONTEXT.md` in a branch means that
    branch's own router before the tree's.

    EVERY ANCESTOR UP TO `agents/` COUNTS. `knowledge/operations/context-budget.md`
    writes `operations/boundaries.md`, meaning "relative to `knowledge/`". That
    is how a person reads it and it is not a defect, so it is not reported as
    one. Anchoring only at the file's own directory and at the tree root would
    flag a third of the tree's working pointers.

    The repo root and `$HOME` come last: `see: CLAUDE.md` and
    `see: DATA-ARCHITECTURE.md` name files one level above `agents/`, and
    a doc may be written from the home directory's point of view.
    Both are real targets.
    """
    roots = [source.parent]
    for parent in source.parents:
        if parent == agents:
            break
        if agents in parent.parents or parent == agents:
            roots.append(parent)
    roots += [agents, agents.parent, pathlib.Path.home()]
    # dict.fromkeys: de-duplicate, keep order.
    return list(dict.fromkeys(roots))


def in_scope(raw: str, agents: pathlib.Path) -> bool:
    """Is this pointer the tree's responsibility?

    `~/.hermes/.anthropic_oauth.json` and `~/05_ARCHIVES-EXTERNAL` are named by
    boundary rules and operations leaves. They describe the machine, may be
    absent on purpose (an unmounted archive disk, a credential this machine
    does not hold), and repairing them means nothing. Only absolute paths that
    point INTO `agents/` are pointers this tool owns.
    """
    cleaned = _clean(raw)
    if not cleaned.startswith(("~/", "/")):
        return True
    try:
        pathlib.Path(cleaned).expanduser().relative_to(agents)
        return True
    except ValueError:
        return False


def resolve(raw: str, source: pathlib.Path, agents: pathlib.Path) -> pathlib.Path | None:
    """The file this path names, or None. Directories count as resolved."""
    cleaned = _clean(raw)
    if not cleaned:
        return None
    if cleaned.startswith(("~/", "/")):
        p = pathlib.Path(cleaned).expanduser()
        return p.resolve() if p.exists() else None
    for root in _roots(source, agents):
        p = root / cleaned
        if p.exists():
            # NORMALISE, ALWAYS. `../../knowledge/x.md` and `knowledge/x.md`
            # name one file but are two different Path objects, and `..` is
            # never collapsed by pathlib. Returning the unnormalised form made
            # the orphan walk treat every spelling as a new file, so its
            # visited-set never converged and the walk did not terminate.
            return p.resolve()
    return None


# ── findings ─────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class Broken:
    source: pathlib.Path        # the file holding the bad pointer
    line: int                   # 1-indexed
    raw: str                    # exactly as written, anchor and all
    col: int = 0                # start offset of the path INSIDE its delimiter
    repair: str | None = None   # the unique replacement, or None if ambiguous
    reason: str = ""            # why there is no repair
    strict: bool = False        # source describes this repo's own mechanisms

    @property
    def fixable(self) -> bool:
        return self.repair is not None

    @property
    def gates(self) -> bool:
        """Does `--check` fail on this one?

        Two ways to qualify, and they are different kinds of thing. A FIXABLE
        pointer gates because the tool could have repaired it and a person
        declined. A STRICT one gates because the file holding it claims to
        describe this repository, so a markdown name it carries that the tree
        does not contain is drift whether or not a repair can be looked up --
        which is exactly what `MEMORY.md` was, and why it survived a week of
        green checks.
        """
        return self.fixable or self.strict

    @property
    def is_pointer(self) -> bool:
        """Does this text claim to be a path in this tree?

        A token with a separator does: `knowledge/meta/nope.md` can only be a
        path, so failing to resolve is drift. A bare `config.yaml` or
        `bootstrap.sh` usually does not -- it is prose naming a file that lives
        somewhere else entirely, and reporting 111 of those buries the six that
        matter. Bare names still surface, counted and collapsed, because
        The same bare name appearing seventeen times IS drift and must stay
        visible.
        """
        return "/" in _clean(self.raw)


# -- the files where a bare name is NOT prose --------------------------------
#
# WHY A SECOND, STRICTER RULE. `is_pointer` deliberately lets a bare name pass:
# 126 of them appear across the tree, most naming files in other repos, and
# failing on all of them would leave `--check` permanently red, which is the
# same as having no check. But three times in two weeks the prose describing
# this system drifted from the system, and every time the stale text was A BARE
# NAME in one of the same handful of files: `MEMORY.md` (deleted 2026-09-04,
# still described as live for a week), `loredb` (store retired, boundary rule
# kept), and stage 9 (removed, still in the stage table). Each was found by
# hand, late.
#
# These files differ from the rest of the tree in a way that justifies a
# different rule: THEY DESCRIBE THIS REPOSITORY'S OWN MECHANISMS. A `.md` name
# in `knowledge/meta/memory-mechanisms.md` is a claim about a file that is
# supposed to be here, not a reference to somewhere else -- so failing to
# resolve is drift, in exactly the way the same token in `knowledge/tools/pi.md`
# is not.
#
# Scoped to `.md` on purpose. `.json`, `.sh` and `.py` names in these files
# legitimately point outside the repo (`settings.json`, `opencode.json`, unit
# files); markdown is this tree's own population, so a markdown file this tree
# names and does not contain is a file that moved or died.
# Scoped to PATHS, not bare names, and that distinction is the whole design.
# A bare `.md` name in these files is usually historical narrative -- `MEMORY.md`
# is discussed at length precisely BECAUSE it was deleted -- and gating on it
# would make `--check` permanently red for correct prose. Measured: a bare-name
# rule flags 13 places in these files, of which 9 are records of a removal and 3
# are prose about a kind of file (`SKILL.md`, 29 namesakes). A PATH-shaped
# pointer is different in kind. `stages/consolidate.py` is a claim that a reader
# can follow to the file implementing the mechanism, and if it resolves nowhere
# then the claim cannot be checked by the person it was written for.
#
# That is the exit criterion for these files, stated as a rule: a mechanism
# claim must name the file that implements it, and the name must resolve.
STRICT_SOURCES = (
    "README.md",
    "knowledge/meta/context-budget.md",
    "knowledge/meta/memory-architecture.md",
    "knowledge/meta/memory-mechanisms.md",
    "knowledge/meta/consolidation-cycle.md",
    "knowledge/operations/healthcheck.md",
)
STRICT_SUFFIX = ".md"      # only markdown names are examined at all

# Files a generator CREATES rather than that exist on disk. Absent is their
# normal state before the first run or after a cleanup, so failing on them would
# make `--check` red for a reason that is not drift. `BRIEF.md` is
# `memory/brief.py`'s stable report path (its NAME constant) -- mirrored here by
# name because the two packages share no parent and cannot import each other;
# tests/test_links.py asserts the mirror still matches the source, the same way
# A downstream copy of this list is checked against this module's elsewhere.
GENERATED_OUTPUTS = ("BRIEF.md",)

# Prose that RECORDS a removal is not a stale claim; it is the tree being
# accurate about its own history -- the same principle that excludes journal/**
# from repair entirely. It still matters under the path rule: a router that
# says "the conventions lived at `knowledge/conventions/` until 2026-08-31" is
# correct, and the prefix is kept elsewhere in the codebase as evidence of that
# move (see scripts/core/declared_paths.py, which excuses the same prefix).
#
# Deliberately narrow, and it fails closed in the safe direction: a stale claim
# sitting in a line that happens to contain one of these words goes unreported,
# while a live one almost never does. These are the words this tree actually
# uses to record a deletion.
RECORDED_REMOVAL = re.compile(
    r"\b(?:deleted|removed|retired|retirement|tombstone|superseded|deprecated|"
    r"renamed|replaced|no longer|used to|since-deleted|since removed|gone)\b",
    re.I)


def _records_a_removal(line: str) -> bool:
    """Does this line describe a file that is GONE, rather than one that is
    supposed to be here?"""
    return bool(RECORDED_REMOVAL.search(line))


SKIP_DIRS = {"journal", ".obsidian", "loredb", "node_modules", ".git"}
GENERATED = "GENERATED FILE"


def _walk(agents: pathlib.Path):
    for pattern in ("*.md", "*.yaml"):
        for p in sorted(agents.rglob(pattern)):
            if SKIP_DIRS & set(p.relative_to(agents).parts):
                continue
            yield p


def _pointers(line: str, source: pathlib.Path):
    """Every span in this line that claims to be a path, with its offset."""
    if source.suffix == ".yaml":
        return list(YAML_SEE.finditer(line))
    return list(TICKED.finditer(line)) + list(MDLINK.finditer(line))


def _basename_index(agents: pathlib.Path) -> dict[str, list[pathlib.Path]]:
    """basename -> every file carrying it. The repair lookup, and the reason a
    repair is only offered when the list has exactly one entry.

    The repo root's own top-level files are included: `ROADMAP.md`,
    `CLAUDE.md` and `DATA-ARCHITECTURE.md` are legitimate targets one level
    above the tree, and a leaf that moves changes its distance to them.
    """
    index: dict[str, list[pathlib.Path]] = {}
    for p in agents.rglob("*"):
        if not p.is_file():
            continue
        if SKIP_DIRS & set(p.relative_to(agents).parts):
            continue
        index.setdefault(p.name, []).append(p)
    for p in agents.parent.glob("*.md"):
        index.setdefault(p.name, []).append(p)
    return index


def _relative(target: pathlib.Path, source: pathlib.Path, agents: pathlib.Path) -> str:
    """How `source` should spell `target`: relative to the file doing the
    pointing.

    A repair must be unambiguous FROM THE FILE A READER IS IN. Tree-relative
    text (`conventions/code-style.md`) is only unambiguous to someone who knows
    the path is anchored at `agents/`; inside `skills/development/CONTEXT.md`
    it reads like a sibling directory that does not exist. `../../conventions/
    code-style.md` cannot be misread, and it is already the dominant spelling
    under `skills/**`. Siblings collapse to a bare name on their own.
    """
    import os.path
    return os.path.relpath(target, source.parent)


def scan(agents: pathlib.Path, renames: dict[str, str] | None = None) -> list[Broken]:
    """Every pointer in the tree that does not resolve, with a repair when the
    repair is a lookup rather than a decision.

    `renames` supplies the one thing a lookup cannot: what a file was called
    before. A rename is a fact only the person who performed it holds, so it is
    declared on the command line (`--rename USER.md=user/user-identity.md`),
    applied to every pointer at once, and recorded in the shell history that
    ran it. That is a migration tool, not a standing configuration -- keeping a
    rename table in a file would make it a second, drifting record of the
    tree's own history.
    """
    renames = renames or {}
    index = _basename_index(agents)
    found: list[Broken] = []
    for source in _walk(agents):
        strict_source = source.relative_to(agents).as_posix() in STRICT_SOURCES
        for lineno, line in enumerate(source.read_text().splitlines(), 1):
            strict_line = strict_source and not _records_a_removal(line)
            for match in _pointers(line, source):
                raw = match.group(1)
                if not _is_candidate(raw):
                    continue
                if resolve(raw, source, agents) is not None:
                    continue
                if not in_scope(raw, agents):
                    continue
                written = pathlib.PurePosixPath(_clean(raw))
                # Strictness is decided HERE, where every exemption is known:
                # only in a file that describes the repository, only for a
                # POINTER rather than prose, only when the line is not recording
                # a removal, and never for a name a generator creates.
                strict = (strict_line
                          and "/" in _clean(raw)
                          and written.name not in GENERATED_OUTPUTS)
                declared = renames.get(_clean(raw)) or renames.get(written.name)
                if declared is not None:
                    target = agents / declared
                    found.append(Broken(source, lineno, raw, match.start(1),
                                        repair=_relative(target, source, agents),
                                        strict=strict))
                    continue
                hits = index.get(written.name, [])
                # `knowledge/conventions/CONTEXT.md` has twelve namesakes, but
                # only one sits in a directory called `conventions`. The folder
                # the author wrote is evidence, and using it turns the tree's
                # most-repeated filename from unfixable into a lookup.
                if len(hits) > 1 and written.parent.name:
                    narrowed = [h for h in hits if h.parent.name == written.parent.name]
                    if len(narrowed) == 1:
                        hits = narrowed
                if len(hits) == 1:
                    repair = _relative(hits[0], source, agents)
                    if GENERATED in source.read_text()[:400]:
                        found.append(Broken(
                            source, lineno, raw, match.start(1),
                            reason=f"generated file; fix the generator, then "
                                   f"rebuild (would become {repair})",
                            strict=strict))
                    else:
                        found.append(Broken(source, lineno, raw, match.start(1),
                                            repair=repair, strict=strict))
                else:
                    why = ("no file of that name in the tree -- renamed or "
                           "deleted; the replacement is a decision"
                           if not hits else
                           f"{len(hits)} files share that name: " +
                           ", ".join(str(h.relative_to(agents)) for h in hits))
                    if strict:
                        why = ("this file describes the repository's own "
                               "mechanisms, so this path is a claim a reader is "
                               "meant to follow to the code that implements the "
                               "mechanism -- and it resolves to nothing. POINTER "
                               "PROBLEM: an incomplete path fragment costs the "
                               "reader the lookup, e.g. `stages/consolidate.py` "
                               "for the memory subsystem's stages/"
                               "consolidate.py`. Write the path from the "
                               "repository root, or name the file that "
                               "implements the mechanism.")
                    found.append(Broken(source, lineno, raw, match.start(1),
                                        reason=why, strict=strict))
    return found


def apply(broken: list[Broken]) -> int:
    """Rewrite the repairable pointers in place. Returns how many were changed.

    SPLICE BY SPAN, NEVER BY STRING REPLACE. Each repair rewrites exactly the
    characters the delimiter enclosed -- `[col, col+len(raw))` on one line --
    so prose on the same line that happens to contain the same text is left
    alone. See the second rule in this module's docstring.

    Right-to-left within a line, because an earlier splice shifts every later
    offset on that line.
    """
    by_file: dict[pathlib.Path, list[Broken]] = {}
    for b in broken:
        if b.fixable:
            by_file.setdefault(b.source, []).append(b)

    changed = 0
    for source, items in by_file.items():
        # NEVER REPAIR A GENERATED FILE. The fix would be correct and would
        # survive until the next rebuild, at which point the drift returns from
        # whatever produced it -- so the tool would report clean while the
        # defect lived on in the generator. Reported instead, with the rebuild.
        if GENERATED in source.read_text()[:400]:
            continue
        lines = source.read_text().splitlines(keepends=True)
        for b in sorted(items, key=lambda b: (-b.line, -b.col)):
            line = lines[b.line - 1]
            if line[b.col:b.col + len(b.raw)] != b.raw:
                continue          # file moved under us; refuse rather than guess
            anchor = b.raw[len(_clean(b.raw)):]      # keep `#section` if present
            lines[b.line - 1] = (line[:b.col] + b.repair + anchor
                                 + line[b.col + len(b.raw):])
            changed += 1
        source.write_text("".join(lines))
    return changed


# ── orphans ──────────────────────────────────────────────────────────────────

# Files that are reachable by construction rather than by pointer: an entry
# point nothing above it can link to, and templates named by convention.
ROOTS = ("CONTEXT.md", "README.md")
USER_INVOKED = re.compile(r"^invocation:\s*user\s*$", re.M)
EXEMPT_NAMES = {"_leaf-template.md", "_skill-template.md", "_category.md",
                "_scope-bindings.md"}


def orphans(agents: pathlib.Path) -> list[pathlib.Path]:
    """Markdown under `agents/` that no router reaches from the entry point.

    Reachability is transitive: CONTEXT.md points at a branch router, that
    router points at leaves. A file reachable only from a file that is itself
    an orphan is still an orphan, which is the point -- a whole detached
    subtree reports as detached.
    """
    reachable: set[pathlib.Path] = set()
    frontier = [agents / name for name in ROOTS if (agents / name).exists()]
    reachable.update(frontier)

    while frontier:
        source = frontier.pop()
        if not source.is_file() or source.suffix != ".md":
            continue
        text = source.read_text()
        for match in list(TICKED.finditer(text)) + list(MDLINK.finditer(text)):
            raw = match.group(1)
            branch = _is_branch(raw, source)
            if branch is not None:
                if branch not in reachable:
                    reachable.add(branch)
                    frontier.append(branch)
                continue
            if not _is_candidate(raw):
                continue
            target = resolve(raw, source, agents)
            if target is None:
                continue
            # A directory counts as reaching its own router.
            if target.is_dir():
                target = target / "CONTEXT.md"
                if not target.exists():
                    continue
            try:
                target.relative_to(agents)
            except ValueError:
                continue                       # outside the tree; not ours
            if target not in reachable:
                reachable.add(target)
                frontier.append(target)

    return [p for p in _walk(agents)
            if p.suffix == ".md"
            and p not in reachable
            and p.name not in EXEMPT_NAMES
            and not _user_invoked(p)]


def _user_invoked(path: pathlib.Path) -> bool:
    """A skill declaring `invocation: user` is reached by slash command, not by
    pointer.

    The generated category routers exclude these ON PURPOSE -- they are for the
    human, indexed in `skills/USER-INVOKED.md`, and a model must not route to
    them. Counting them as orphans produced nine false positives and an
    instruction to "rebuild the index", which would have changed nothing.
    """
    return path.name == "SKILL.md" and bool(USER_INVOKED.search(path.read_text()))


# ── adopting orphans ─────────────────────────────────────────────────────────

TABLE_ROW = re.compile(r"^\|.*\|\s*$")
TITLE = re.compile(r"^title:\s*(.+)$", re.M)
HEADING = re.compile(r"^#\s+(.+)$", re.M)


@dataclasses.dataclass
class Adoption:
    orphan: pathlib.Path
    router: pathlib.Path | None
    row: str = ""
    reason: str = ""       # why it could not be adopted

    @property
    def ok(self) -> bool:
        return self.router is not None and not self.reason


def _describe(leaf: pathlib.Path) -> str:
    """What to put in the router's second column. The leaf's own `title:` is
    the author's one-line summary; its H1 is the fallback. Never invented."""
    text = leaf.read_text()
    m = TITLE.search(text)
    if m:
        return m.group(1).strip().strip('"')
    m = HEADING.search(text)
    if m:
        return m.group(1).strip()
    return leaf.stem.replace("-", " ")


def adopt(orphan: pathlib.Path, agents: pathlib.Path) -> Adoption:
    """Give one orphan a pointer from its own directory's router.

    THIS IS THE ONLY ORPHAN REPAIR THAT IS NOT A JUDGEMENT. Where a leaf should
    be *mentioned* -- which router, under which question, in what words -- is a
    filing decision. But a leaf sitting in a folder whose router does not list
    it is simply an omission from that folder's own index, and the row that
    fixes it can be built from the leaf itself: its filename and its declared
    title. Anything else this tool leaves alone.
    """
    if orphan.name == "CONTEXT.md":
        return Adoption(orphan, None,
                        reason="a router itself -- the folder above must point at it")
    # A skill is indexed by its CATEGORY router, one level up -- `SKILL.md`
    # lives alone in its own directory and is named from outside it.
    router = (orphan.parent.parent if orphan.name == "SKILL.md"
              else orphan.parent) / "CONTEXT.md"
    if not router.exists():
        return Adoption(orphan, None,
                        reason=f"no router in {orphan.parent.relative_to(agents)}/")
    head = router.read_text()[:400]
    if GENERATED in head:
        return Adoption(orphan, None,
                        reason="router is generated; rebuild it instead "
                               "(`clew-follow`, in the tree that generates it)")

    lines = router.read_text().splitlines()
    last = max((i for i, l in enumerate(lines) if TABLE_ROW.match(l)), default=None)
    if last is None:
        return Adoption(orphan, None, reason="router has no table to extend")
    columns = lines[last].count("|") - 1
    cells = [f"`{orphan.name}`", _describe(orphan)] + ["—"] * (columns - 2)
    return Adoption(orphan, router, row="| " + " | ".join(cells[:columns]) + " |")


def adopt_all(orphans: list[pathlib.Path], agents: pathlib.Path,
              write: bool) -> list[Adoption]:
    """Plan a pointer for every orphan; write the ones that need no decision."""
    plans = [adopt(o, agents) for o in orphans]
    if not write:
        return plans
    by_router: dict[pathlib.Path, list[Adoption]] = {}
    for a in plans:
        if a.ok:
            by_router.setdefault(a.router, []).append(a)
    for router, items in by_router.items():
        lines = router.read_text().splitlines()
        last = max(i for i, l in enumerate(lines) if TABLE_ROW.match(l))
        for offset, a in enumerate(items):
            lines.insert(last + 1 + offset, a.row)
        router.write_text("\n".join(lines) + "\n")
    return plans
