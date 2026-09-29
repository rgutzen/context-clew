"""blocks -- Pi attribution, and the collision guard that makes it safe.

Pi sends its prompt as a single `system`-role OpenAI chat message and stamps its
own markers. Two of them collide with other harnesses (`<available_skills>` is
also opencode's skill-listing tag; `<project_context>` and `## Available
Subagents` are Pi's alone), so Pi attribution is scoped behind `_is_pi_system`
rather than added to the shared SECTIONS table. These tests pin that split: a
Pi-shaped message decomposes into Pi owners, an opencode-shaped message never
touches the Pi table, and the `vault` special-case gets the right fallback
label.
"""
from clew import blocks

PI_SYSTEM = """You are an expert coding assistant operating inside pi.
Available tools:
- read: Read file contents
- edit: Make precise file edits

Guidelines:
- Use bash for file operations

<project_context>
Project-specific instructions and guidelines.
</project_context>

The following skills provide specialized instructions for specific tasks.
Use the read tool to load a skill's file when the task matches its description.

<available_skills>
  <skill>
    <name>spike</name>
    <description>Throwaway experiments.</description>
  </skill>
</available_skills>
Current working directory: /tmp/foo

## Available Subagents

The following subagents are available via the `subagent` tool:

- **bash** (user): Mechanical command-execution worker.
"""


def _openai_body(system, tools=()):
    return {"messages": [{"role": "system", "content": system},
                         {"role": "user", "content": "hi"}],
            "tools": list(tools)}


def _owners(blocks_list):
    return {b.label: b.owner for b in blocks_list}


def test_pi_system_decomposes_into_pi_owners():
    out = blocks.openai(_openai_body(PI_SYSTEM))
    owners = _owners(out)
    assert owners["project context (AGENTS.md)"] == "vault"
    assert owners["skill preamble"] == "harness"
    assert owners["skills index"] == "vault"
    assert owners["cwd line"] == "session"
    assert owners["subagent listing"] == "plugin:pi-subagent"
    # the vendor intro before <project_context> stays "harness"
    assert any(b.label == "vendor system prompt" and b.owner == "harness"
               for b in out)


def test_pi_vendor_prompt_is_harness_not_unattributed():
    out = blocks.openai(_openai_body(PI_SYSTEM))
    vendor = [b for b in out if b.label == "vendor system prompt"]
    assert vendor and vendor[0].owner == "harness"


def test_opencode_shape_never_hits_pi_table():
    """opencode also wraps skills in `<available_skills>` but has no
    `<project_context>` / `## Available Subagents` -- so it must NOT be
    re-labelled with Pi owners."""
    opencode_system = ("<available_skills>\n"
                       "  <skill><name>spike</name></skill>\n"
                       "</available_skills>\n")
    out = blocks.openai(_openai_body(opencode_system))
    # falls through to the shared SECTIONS table, which has no <available_skills>
    # marker for the openai dialect -> whole message is the vendor default
    assert all(b.owner != "plugin:pi-subagent" for b in out)
    assert all(b.owner != "vault" for b in out)


def test_pi_attribution_assigns_every_byte():
    """Every byte of the system message lands in exactly one block."""
    out = blocks.openai(_openai_body(PI_SYSTEM))
    system_blocks = [b for b in out if b.where.startswith("messages[0]")]
    total = sum(b.bytes for b in system_blocks)
    assert total == len(PI_SYSTEM.encode())


def test_pi_tool_schemas_still_counted():
    tool = {"type": "function", "function": {"name": "read", "description": "d"}}
    out = blocks.openai(_openai_body(PI_SYSTEM, tools=[tool]))
    assert any(b.label == "tool schema" and b.detail == "read" for b in out)


def test_system_md_payload_is_billed_to_the_vault_not_the_vendor():
    """`SYSTEM.md` REPLACES Pi's vendor prompt. Without the BEGIN-marker entry
    in PI_SECTIONS the always-on payload is billed as `harness / vendor system
    prompt`, i.e. the audit reports the vendor growing while it was deleted."""
    system = ("<!-- BEGIN GENERATED always-on -->\n"
              "always-on payload\n"
              "<!-- END GENERATED always-on -->\n" + PI_SYSTEM)
    out = blocks.openai(_openai_body(system))
    payload = [b for b in out if b.label == "always-on payload (SYSTEM.md)"]
    assert len(payload) == 1
    assert payload[0].owner == "vault"
    assert payload[0].bytes > 0
    assert all(b.label != "vendor system prompt" for b in out)


def test_pi_detected_without_the_subagent_plugin():
    """`## Available Subagents` comes from an OPTIONAL extension. Keying the Pi
    detector on it re-billed the whole prompt to the vendor the day the plugin
    was removed (2026-09-06). Either Pi-only marker must be enough."""
    without_plugin = PI_SYSTEM.split("## Available Subagents")[0]
    out = blocks.openai(_openai_body(without_plugin))
    assert any(b.label == "project context (AGENTS.md)" for b in out)
    assert any(b.label == "cwd line" for b in out)


def test_system_md_alone_identifies_pi():
    """SYSTEM.md replaces the vendor prompt, so a Pi message may have no
    `<project_context>` at all -- the l1 marker carries the identification."""
    out = blocks.openai(_openai_body(
        "<!-- BEGIN GENERATED always-on — x -->\npayload\n"
        "<!-- END GENERATED always-on -->\nCurrent working directory: /tmp/foo\n"))
    assert any(b.label == "always-on payload (SYSTEM.md)" for b in out)
    assert any(b.label == "cwd line" for b in out)
