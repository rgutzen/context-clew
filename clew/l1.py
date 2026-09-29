"""l1 -- compile the always-on payload into each harness's entry point.

WHY. Layer 1 is a promise until this runs: `CONTEXT.md` says `user/user-identity.md`
and the always-on core of `conventions/collaboration.md` are "always loaded", but no
harness carries them. Claude Code expands `@` imports from `CLAUDE.md`; opencode
expands no import syntax in `AGENTS.md`, so it names its instruction files in
`opencode.json`; hermes cannot carry them at all (see HERMES IS SKIPPED). This
module is the compiler that makes the promise a mechanism.

ONE SOURCE, SEVERAL SURFACES, ONE SPLICE WITH boundaries-compile. The always-loaded
set lives in `tree.ALWAYS_LOADED`; this module reads those files and emits each
harness's surface. It writes into a file it does not own (a generated block in a
markdown file, a JSON field), so idempotency (`compile && compile --check` exits 0)
and drift detection (`--check`) come from `generated_block.splice` (2026-09-05) --
the two compilers called the same algorithm by copy-paste until it drifted (see
that module's docstring); this file supplies only its own markers.

THE DOTFILES TRAP. Each entry point is a symlink into the stow canonical source
(`~/02_AREAS/B_Tech/dotfiles/`). Writing through the symlink's other end is the
failure that has bitten this repo before, so every target is resolved to its
canonical path first and written there. This compiler writes; it never commits --
the user commits.

PI IS THE EXCEPTION THAT PROVES THE BOUNDARY. Pi's `~/.pi/agent/SYSTEM.md` is
private (not stowed) and REPLACES the vendor system prompt rather than appending
to it. So Pi is the one harness where the payload can be inlined in full AND the
vendor's own prose dropped: `_pi_system_block` emits the whole prompt -- framing,
the always-on pair verbatim, a pointer to the router, a pointer to the project's
own context files -- and nothing else. Its public `AGENTS.md` therefore carries
no block at all; the `pi-agents` target exists only to strip the one it had.

THE PUBLIC-REPO BOUNDARY. `opencode.json` lives in the public dotfiles repo, but
the payload is whatever the vault's always-on files say. A full-text splice into
`AGENTS.md` would publish the user's identity and behavioural core. opencode's
`instructions` field names the private paths instead; opencode loads them in full
(verified empirically against opencode 1.18.23, 2026-09-04) and a path is not a
secret -- `AGENTS.md` already names the vault's `CONTEXT.md`.

HERMES IS SKIPPED, NOT FORGOTTEN. hermes' `MemoryStore.load_from_disk()` runs
`scan_for_threats(scope="strict")` over `MEMORY.md` and replaces any matching
entry with a `[BLOCKED]` placeholder. A delimited block spliced there matches
`html_comment_injection` (the marker itself), plus `ssh_access` and `hermes_env`
(the boundary rules) -- and never reaches the prompt. Making hermes carry L1 is
a fix in hermes' sanitizer/assembly path, not in this compiler. Reported, not
silently ignored.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from generated_block import splice as _splice_block
from .tree import ALWAYS_LOADED

BEGIN = "<!-- BEGIN GENERATED always-on -->"
END = "<!-- END GENERATED always-on -->"
# Match any generation's opening marker, not just this one's, and strip every
# block, not the first -- the two bugs boundaries-compile fixed on 2026-09-04.
BLOCK_ANY = re.compile(
    r"<!-- BEGIN GENERATED always-on.*?<!-- END GENERATED always-on -->\n?", re.S)

CLAUDE_ENTRY = Path.home() / ".claude" / "CLAUDE.md"
OPENCODE_CONFIG = Path.home() / ".config" / "opencode" / "opencode.json"
# Pi loads `AGENTS.md` verbatim — it expands no `@`-import. So the pointer is a
# directive (hint, then route), not an import line, exactly like opencode's own
# hand-written AGENTS.md. The content stays in the private repo; only the path
# is published.
PI_AGENTS = Path.home() / ".pi" / "agent" / "AGENTS.md"
# Pi is the one harness whose system prompt is ours to write: `SYSTEM.md`
# REPLACES the vendor prompt (verified 2026-09-06 against pi 0.85.0) rather than
# appending to it, and it lives outside the public dotfiles repo -- so the
# private always-on payload can be inlined here in full, which `AGENTS.md`
# (public) cannot do. This file is generated end to end; the only hand-written
# prose in it is the framing the vendor prompt used to supply.
PI_SYSTEM = Path.home() / ".pi" / "agent" / "SYSTEM.md"

# What the Pi system prompt carries, and nothing else (user decision 2026-09-06):
# the always-loaded pair IN FULL, a pointer to the router, a pointer to the
# project's own context files. CONTEXT.md stays a pointer -- inlining the router
# would pay 2.1KB every session for a table most turns never consult.
PI_INLINE = ("user/user-identity.md", "conventions/collaboration.md")

# The one file Claude Code already imports via its own human-authored line in
# CLAUDE.md. Re-importing it here would inject CONTEXT.md twice and double its
# cost.
CLAUDE_ALREADY_HAS = ("CONTEXT.md",)


def _import_path(agents: Path) -> str:
    """The agents dir as Claude Code `@`-import path: `~/…` under home, absolute
    otherwise. Claude expands `~` the same way the existing line uses it."""
    home = Path.home()
    try:
        return "~/" + agents.resolve().relative_to(home.resolve()).as_posix()
    except ValueError:
        return str(agents.resolve())


def _claude_block(agents: Path) -> str:
    base = _import_path(agents)
    lines = [f"@{base}/{name}" for name in ALWAYS_LOADED if name not in CLAUDE_ALREADY_HAS]
    return f"{BEGIN}\n" + "\n".join(lines) + f"\n{END}"


def _opencode_instructions(agents: Path) -> list[str]:
    """The `instructions` field opencode loads in full. Absolute private paths:
    a path is not a secret, and the content stays in the private repo.

    Every always-loaded file is listed, CONTEXT.md included -- opencode expands
    no `@` import, so the `@<vault>/CONTEXT.md` line in AGENTS.md is
    inert prose and does not deliver the router. (Claude Code is different: its
    `@` import IS expanded, which is why CLAUDE_ALREADY_HAS exists for it only.)"""
    return [str((agents / name).resolve()) for name in ALWAYS_LOADED]


def _pi_system_block(agents: Path) -> str:
    """Pi's whole system prompt: framing, the always-on files verbatim, two
    pointers.

    NO SKILL DESCRIPTIONS, BY CONSTRUCTION. `CONTEXT.md` states skills are not
    installed in any harness; discovery routes through `skills/CONTEXT.md` on
    demand. Pi's own `<available_skills>` index is injected by its skill
    *discovery*, not by this prompt -- emptying `~/.agents/skills` is what stops
    it, and this block is the replacement route."""
    base = _import_path(agents)
    parts = [
        BEGIN,
        "You are a coding agent running in pi on the user's machine. Work to the\n"
        "conventions below; they are not optional and they outrank any default\n"
        "style you would otherwise fall back on.",
        "# Knowledge and skills\n\n"
        f"The canonical, private, cross-harness knowledge base lives at\n"
        f"`{base}/`. Its entry point is a routing table -- read it\n"
        f"before doing work, and follow the one row that answers:\n\n"
        f"`{base}/CONTEXT.md`\n\n"
        f"Skills are not installed in this harness. Route to one via\n"
        f"`{base}/skills/CONTEXT.md`; never load the library wholesale.",
        # O(1) IN THE ROSTER'S SIZE. Naming the agents here would re-create the
        # 4.2KB listing the pi-subagent extension was removed for (2026-09-06);
        # the roster is behind a command, so the twentieth specialist costs the
        # parent prompt nothing.
        "# Delegation\n\n"
        "Offload bulk recon or mechanical work to a smaller local model with\n"
        "`pi-agent <name> \"<task>\"` (roster: `pi-agent --list`). Worth it only\n"
        "when the child discards far more than it returns — you already have\n"
        "`bash` for a single command.",
        "# Project context\n\n"
        "Read the project's own instructions before editing it: `AGENTS.md`,\n"
        "`CLAUDE.md` and `CONTEXT.md` in the working directory and its ancestors.",
    ]
    for name in PI_INLINE:
        text = (agents / name).read_text(encoding="utf-8").strip()
        parts.append(text)
    return "\n\n".join(parts) + f"\n{END}"


def _opencode_json(current: str, agents: Path) -> str:
    """Rewrite only the `instructions` key, leaving everything else byte-identical.

    opencode.json round-trips exactly through `json.dumps(indent=2)` (verified
    2026-09-04), so a parse-and-dump is safe -- but only the `instructions` field
    is touched, and an empty result means the field is removed, not left as `[]`."""
    cfg = json.loads(current)
    wanted = _opencode_instructions(agents)
    if wanted:
        cfg["instructions"] = wanted
    else:
        cfg.pop("instructions", None)
    return json.dumps(cfg, indent=2) + "\n"


def _splice(existing: str, block: str) -> str:
    """This file's markers, generated_block's algorithm -- see that module for
    why it is shared with boundaries-compile rather than copied from it."""
    return _splice_block(existing, block, BLOCK_ANY)


def targets(agents: Path):
    """(label, path, desired_full_text, spliced?) for each harness surface."""
    for label, entry, block in (
        ("claude", CLAUDE_ENTRY, _claude_block(agents)),
    ):
        path = entry.resolve() if entry.is_symlink() else entry
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        yield label, path, _splice(current, block), True

    cfg_path = OPENCODE_CONFIG.resolve() if OPENCODE_CONFIG.is_symlink() else OPENCODE_CONFIG
    current = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else "{}\n"
    yield "opencode", cfg_path, _opencode_json(current, agents), True

    # Pi's public `AGENTS.md` carried the same pointer until 2026-09-06. It is
    # now emitted once, into the private SYSTEM.md below; any block left in
    # AGENTS.md is stripped (empty block -> splice removes it) so the pointer is
    # not paid twice per session.
    pi_path = PI_AGENTS.resolve() if PI_AGENTS.is_symlink() else PI_AGENTS
    if pi_path.exists():
        current = pi_path.read_text(encoding="utf-8")
        if BLOCK_ANY.search(current):
            yield "pi-agents", pi_path, BLOCK_ANY.sub("", current).lstrip("\n"), True

    sys_path = PI_SYSTEM.resolve() if PI_SYSTEM.is_symlink() else PI_SYSTEM
    current = sys_path.read_text(encoding="utf-8") if sys_path.exists() else ""
    yield "pi-system", sys_path, _splice(current, _pi_system_block(agents)), True

