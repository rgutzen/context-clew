"""l1 -- the always-on compiler, and the two properties that make it safe.

Every test pins a property the boundaries compiler learned the hard way on
2026-09-04, plus the three things specific to this compiler: collaboration.md
is in the always-on set IN FULL (user decision 2026-09-04), opencode is wired
via the `instructions` field so private content never enters the public dotfiles
repo, and hermes is skipped by construction (its sanitizer blocks the block,
verified empirically 2026-09-04).
"""
import json
import pathlib

import pytest

from clew import l1
from clew.tree import ALWAYS_LOADED


@pytest.fixture
def agents(tmp_path):
    (tmp_path / "user").mkdir()
    (tmp_path / "conventions").mkdir()
    (tmp_path / "CONTEXT.md").write_text("# Router\n")
    (tmp_path / "user" / "user-identity.md").write_text("# Who the user is\nA conversation partner.\n")
    (tmp_path / "conventions" / "collaboration.md").write_text(
        "# Collaboration\n\nAlways-on. Every interaction.\n")
    return tmp_path


# ── idempotency is the acceptance test ────────────────────────────────────────

def test_compile_then_check_is_stable(tmp_path, monkeypatch, agents):
    entry = tmp_path / "CLAUDE.md"
    monkeypatch.setattr(l1, "CLAUDE_ENTRY", entry)
    first = next(t for t in l1.targets(agents) if t[0] == "claude")[2]
    entry.write_text(first)
    again = next(t for t in l1.targets(agents) if t[0] == "claude")[2]
    assert again == first


def test_marker_rename_does_not_orphan_old_block(tmp_path, monkeypatch, agents):
    """A renamed marker must still find and replace the old block."""
    entry = tmp_path / "CLAUDE.md"
    monkeypatch.setattr(l1, "CLAUDE_ENTRY", entry)
    old = "<!-- BEGIN GENERATED always-on — OLD -->\njunk\n<!-- END GENERATED always-on -->\n"
    entry.write_text(old + "user prose\n")
    desired = next(t for t in l1.targets(agents) if t[0] == "claude")[2]
    assert "OLD" not in desired
    assert "junk" not in desired
    assert "user prose" in desired


def test_second_block_is_removed_not_duplicated(tmp_path, monkeypatch, agents):
    """Two stray blocks must collapse to one, not three."""
    entry = tmp_path / "CLAUDE.md"
    monkeypatch.setattr(l1, "CLAUDE_ENTRY", entry)
    block = ("<!-- BEGIN GENERATED always-on — X -->\na\n"
             "<!-- END GENERATED always-on -->\n")
    entry.write_text(block + block)
    desired = next(t for t in l1.targets(agents) if t[0] == "claude")[2]
    assert desired.count("BEGIN GENERATED always-on") == 1
    assert desired.count("END GENERATED always-on") == 1


# ── the payload ───────────────────────────────────────────────────────────────

def test_collaboration_is_in_the_always_on_set(tmp_path, agents):
    assert "conventions/collaboration.md" in ALWAYS_LOADED


def test_claude_block_excludes_already_imported_context(tmp_path, monkeypatch, agents):
    monkeypatch.setattr(l1, "CLAUDE_ENTRY", tmp_path / "CLAUDE.md")
    desired = next(t for t in l1.targets(agents) if t[0] == "claude")[2]
    block = desired.split("<!-- BEGIN GENERATED always-on", 1)[1]
    assert "/CONTEXT.md" not in block          # not re-imported: CLAUDE.md already has it
    assert "user/user-identity.md" in block
    assert "conventions/collaboration.md" in block


def test_always_loaded_names_are_the_source(tmp_path, agents):
    """l1 compiles exactly the ALWAYS_LOADED set, no more."""
    for name in ALWAYS_LOADED:
        assert (agents / name).is_file()


# ── opencode: instructions field, private paths, no inline content ────────────

def test_opencode_uses_instructions_field(tmp_path, monkeypatch, agents):
    cfg = tmp_path / "opencode.json"
    monkeypatch.setattr(l1, "OPENCODE_CONFIG", cfg)
    desired = next(t for t in l1.targets(agents) if t[0] == "opencode")[2]
    parsed = json.loads(desired)
    assert parsed["instructions"] == l1._opencode_instructions(agents)
    # content itself is NOT inlined into the (public) config
    assert "Always-on. Every interaction." not in desired


def test_opencode_instructions_include_router(tmp_path, agents):
    """opencode expands no `@` import, so CONTEXT.md must be in the list too."""
    paths = l1._opencode_instructions(agents)
    assert any(p.endswith("CONTEXT.md") for p in paths)
    assert any(p.endswith("user/user-identity.md") for p in paths)
    assert any(p.endswith("conventions/collaboration.md") for p in paths)


def test_opencode_json_rewrites_only_instructions(tmp_path, agents):
    """Other keys in opencode.json survive byte-identical."""
    before = '{\n  "plugin": ["a"],\n  "model": "anthropic/x"\n}\n'
    after = json.loads(l1._opencode_json(before, agents))
    assert after["plugin"] == ["a"]
    assert after["model"] == "anthropic/x"
    assert "instructions" in after


# ── pi: SYSTEM.md carries the payload, AGENTS.md carries none ────────────────

@pytest.fixture
def pi_paths(tmp_path, monkeypatch):
    """Both Pi surfaces redirected into tmp: the compiler must never touch the
    real `~/.pi`."""
    monkeypatch.setattr(l1, "PI_AGENTS", tmp_path / "PI_AGENTS.md")
    monkeypatch.setattr(l1, "PI_SYSTEM", tmp_path / "PI_SYSTEM.md")
    return tmp_path


def test_pi_system_inlines_the_always_on_files(pi_paths, agents):
    """SYSTEM.md replaces the vendor prompt and is private, so the always-on
    pair goes in VERBATIM — this is the whole point of the surface."""
    desired = next(t for t in l1.targets(agents) if t[0] == "pi-system")[2]
    assert "A conversation partner." in desired      # user-identity.md, in full
    assert "Always-on. Every interaction." in desired   # collaboration.md, in full


def test_pi_system_points_at_router_and_project_files(pi_paths, agents):
    desired = next(t for t in l1.targets(agents) if t[0] == "pi-system")[2]
    # The router is named by the vault's own path, not by a hardcoded one: the
    # prompt must be true for a vault that is not this machine's.
    assert f"{l1._import_path(agents)}/CONTEXT.md" in desired
    assert "# Router" not in desired                # pointer, not inlined
    for name in ("AGENTS.md", "CLAUDE.md", "CONTEXT.md"):
        assert name in desired


def test_pi_system_carries_no_skill_descriptions(pi_paths, agents):
    """Skills are not installed in any harness; the prompt routes, it does not
    list. A skill index here would re-introduce the O(installed) cost."""
    desired = next(t for t in l1.targets(agents) if t[0] == "pi-system")[2]
    assert "<available_skills>" not in desired
    assert "skills/CONTEXT.md" in desired


def test_pi_agents_block_is_stripped_not_duplicated(pi_paths, agents):
    """The pointer moved to SYSTEM.md. Any block left in the public AGENTS.md is
    removed — paying for it twice per session is the bug this closes — while the
    human prose around it survives."""
    entry = pi_paths / "PI_AGENTS.md"
    entry.write_text(l1.BEGIN + "\nold pointer\n" + l1.END + "\n# Human header\n")
    desired = next(t for t in l1.targets(agents) if t[0] == "pi-agents")[2]
    assert "old pointer" not in desired
    assert "BEGIN GENERATED always-on" not in desired
    assert "# Human header" in desired


def test_pi_agents_target_absent_when_no_block(pi_paths, agents):
    """Nothing to strip means no target at all: the compiler does not rewrite a
    file it has no content for."""
    (pi_paths / "PI_AGENTS.md").write_text("# Human header only\n")
    assert not [t for t in l1.targets(agents) if t[0] == "pi-agents"]


def test_pi_system_compile_then_check_is_stable(pi_paths, agents):
    entry = pi_paths / "PI_SYSTEM.md"
    first = next(t for t in l1.targets(agents) if t[0] == "pi-system")[2]
    entry.write_text(first)
    again = next(t for t in l1.targets(agents) if t[0] == "pi-system")[2]
    assert again == first


# ── hermes is skipped by construction ─────────────────────────────────────────

def test_hermes_has_no_target(pi_paths, agents):
    labels = {t[0] for t in l1.targets(agents)}
    assert labels <= {"claude", "opencode", "pi-agents", "pi-system"}
    assert "hermes" not in labels


def test_pi_system_routes_delegation_without_a_roster(pi_paths, agents):
    """The delegation pointer must name the command, never the agents: an
    inlined roster is the 4.2KB the pi-subagent extension was removed for."""
    desired = next(t for t in l1.targets(agents) if t[0] == "pi-system")[2]
    assert "pi-agent --list" in desired
    assert "explore" not in desired
    assert "subagent" not in desired
