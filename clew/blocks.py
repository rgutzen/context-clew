"""blocks -- cut a captured request into named pieces and say who owns each.

A total is not a finding. "Claude Code costs 45k tokens a session" tells you
nothing you can act on; "1.4k of it is a skill description from a plugin you do
not use" is a decision. So every byte of a capture is assigned to exactly one
block, and every block to exactly one OWNER -- the thing you would change to
make it smaller.

OWNERS, and what each one means you would do:
  harness        the vendor's own system prompt        nothing; it is the tool
  tool           one tool schema                       trim the toolset
  plugin:<x>     a plugin's skills or instructions     disable the plugin
  mcp:<x>        an MCP server's instructions          drop the server
  vault:<path>   a file WE wrote, expanded into the prompt   edit that file
  session        the user's turn, dates, ids           nothing
  unattributed   we could not tell                     read it and extend the table

`unattributed` is deliberate. A parser that guesses an owner produces a table
that always sums to 100% and is quietly wrong at the edges; one that admits the
gap tells you when a harness changed shape under it. The audit prints the
unattributed share, and a large one means this module is out of date, not that
the harness got cheaper.

ATTRIBUTION IS BY MARKER, NOT BY GUESS. `SECTIONS` lists the literal strings
each harness stamps around its injections. They are the harness's own words, so
they change when it changes -- which is the point; a marker that stops matching
shows up as unattributed bytes rather than as a wrong label.
"""
from __future__ import annotations

import dataclasses
import json
import re

from . import tokens

# Literal markers Claude Code stamps around each injected section, in the order
# they appear. Each entry: (marker, owner, label). The marker starts the block;
# the next marker ends it.
SECTIONS = (
    ("The following deferred tools are now available", "harness", "deferred-tool listing"),
    ("The following MCP servers are configured but failed to connect", "harness", "MCP failure notice"),
    ("Available agent types for the Agent tool", "harness", "agent-type listing"),
    ("# MCP Server Instructions", "mcp", "MCP server instructions"),
    ("The following skills are available for use with the Skill tool", "harness", "skill listing"),
    ("<total_tokens>", "session", "token budget notice"),
    ("# claudeMd", "vault", "injected memory files"),
    ("# userEmail", "session", "user identity"),
    ("# currentDate", "session", "date"),
)

# Pi stamps its own markers into its single `system` message. `<available_skills>`
# is NOT Pi-specific -- opencode's skill listing wraps the same tag -- so this
# table is only consulted after `_is_pi_system` has confirmed the message is
# Pi-shaped (`<project_context>` and `## Available Subagents` are Pi's alone).
# A shared table would cross-label one harness as the other.
PI_SECTIONS = (
    # Pi's `SYSTEM.md` REPLACES the vendor prompt, so without this marker the
    # always-on payload l1 compiles is billed to `harness / vendor system
    # prompt` -- the audit would report the vendor as growing while it was in
    # fact deleted. The marker is l1's own BEGIN line.
    ("<!-- BEGIN GENERATED always-on", "vault", "always-on payload (SYSTEM.md)"),
    ("<project_context>", "vault", "project context (AGENTS.md)"),
    ("The following skills provide specialized instructions", "harness", "skill preamble"),
    ("<available_skills>", "vault", "skills index"),
    ("Current working directory:", "session", "cwd line"),
    ("## Available Subagents", "plugin:pi-subagent", "subagent listing"),
)

# Inside `# claudeMd`, each expanded file announces itself. This is what lets an
# audit say "CONTEXT.md costs N" rather than "your memory files cost N".
FILE_HEADER = re.compile(r"^Contents of (\S+?)(?: \([^)]*\))?:\s*$", re.M)


@dataclasses.dataclass
class Block:
    label: str
    owner: str
    where: str          # which part of the request body it came from
    bytes: int
    tokens: int
    detail: str = ""    # a per-item name, when the block is one of many

    @property
    def key(self) -> str:
        return f"{self.owner}/{self.label}" + (f"/{self.detail}" if self.detail else "")


def _block(label, owner, where, text, detail=""):
    return Block(label=label, owner=owner, where=where,
                 bytes=len(text.encode()), tokens=tokens.count(text), detail=detail)


def _split_sections(text: str, where: str, default=("unattributed", "prompt text"),
                    table: tuple = SECTIONS) -> list[Block]:
    """Cut one prompt string at its markers. Text before the first marker is
    kept as its own block rather than folded into the first section -- folding
    is how a parser starts inventing.

    `default` owns whatever no marker claims. In the Anthropic `system` array
    that is the vendor's own prompt and we can say so; inside `messages` it is
    genuinely unknown and must stay `unattributed`. `table` is the marker set
    (Claude's shared SECTIONS, or Pi's PI_SECTIONS)."""
    hits = sorted(((text.index(m), m, owner, label)
                   for m, owner, label in table if m in text),
                  key=lambda h: h[0])
    if not hits:
        return [_block(default[1], default[0], where, text)]
    out = []
    if hits[0][0] > 0:
        head = text[:hits[0][0]]
        if head.strip():
            out.append(_block(default[1], default[0], where, head))
    for i, (start, _marker, owner, label) in enumerate(hits):
        end = hits[i + 1][0] if i + 1 < len(hits) else len(text)
        chunk = text[start:end]
        if owner == "vault":
            out.extend(_split_memory_files(chunk, where, fallback=label))
        else:
            out.append(_block(label, owner, where, chunk))
    return out


def _split_memory_files(chunk: str, where: str, fallback: str = "injected memory files"
                        ) -> list[Block]:
    """`# claudeMd` holds one or more expanded files, each with a path header.
    Attribute each to its own path: that is the actionable unit. Pi's
    `<project_context>` block has no such header, so its chunk falls back to the
    label its PI_SECTIONS entry names (e.g. "project context (AGENTS.md)")."""
    marks = list(FILE_HEADER.finditer(chunk))
    if not marks:
        return [_block(fallback, "vault", where, chunk)]
    out = []
    if marks[0].start() > 0:
        out.append(_block("memory-file preamble", "harness", where, chunk[:marks[0].start()]))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(chunk)
        out.append(_block("expanded file", "vault", where,
                          chunk[m.start():end], detail=m.group(1)))
    return out


# -- dialects --------------------------------------------------------------
def _is_pi_system(text: str) -> bool:
    """Is this system message Pi-shaped?

    `<available_skills>` alone is ambiguous -- opencode wraps its skill listing
    in the same tag -- so the test is a marker opencode never sends. Two qualify,
    and EITHER suffices: Pi's own `<project_context>`, or l1's always-on BEGIN
    line, which only ever reaches a model through `~/.pi/agent/SYSTEM.md`.

    The original test also required `## Available Subagents`. Removing the
    pi-subagent extension (2026-09-06) deleted that marker and silently re-billed
    the entire Pi prompt to `harness / vendor system prompt` -- a detector keyed
    on an optional plugin is a detector that breaks when the plugin goes."""
    return ("<project_context>" in text) or ("<!-- BEGIN GENERATED always-on" in text)


def anthropic(body: dict) -> list[Block]:
    out: list[Block] = []
    system = body.get("system")
    if isinstance(system, str):
        out.extend(_split_sections(system, "system"))
    elif isinstance(system, list):
        for i, part in enumerate(system):
            text = part.get("text", "")
            if text.startswith("x-anthropic-billing-header"):
                out.append(_block("billing header", "harness", f"system[{i}]", text))
            else:
                out.extend(_split_sections(text, f"system[{i}]",
                                           ("harness", "vendor system prompt")))
    for tool in body.get("tools") or []:
        out.append(Block(label="tool schema", owner="tool", where="tools",
                         bytes=len(json.dumps(tool)), tokens=tokens.count_json(tool),
                         detail=tool.get("name", "?")))
    for i, msg in enumerate(body.get("messages") or []):
        content = msg.get("content")
        parts = [content] if isinstance(content, str) else [
            p.get("text", "") for p in (content or []) if isinstance(p, dict)]
        for j, text in enumerate(parts):
            if not text:
                continue
            where = f"messages[{i}][{j}] {msg.get('role')}"
            if len(text) < 400 and "<system-reminder>" not in text:
                out.append(_block("user turn", "session", where, text))
            else:
                out.extend(_split_sections(text, where))
    return out


def openai(body: dict) -> list[Block]:
    out: list[Block] = []
    for i, msg in enumerate(body.get("messages") or []):
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if not content:
            continue
        where = f"messages[{i}] {msg.get('role')}"
        if msg.get("role") == "system":
            if _is_pi_system(content):
                out.extend(_split_sections(content, where,
                                           ("harness", "vendor system prompt"),
                                           table=PI_SECTIONS))
            else:
                out.extend(_split_sections(content, where,
                                           ("harness", "vendor system prompt")))
        else:
            out.append(_block("user turn", "session", where, content))
    for tool in body.get("tools") or []:
        name = ((tool.get("function") or {}).get("name")
                or tool.get("name") or "?")
        out.append(Block(label="tool schema", owner="tool", where="tools",
                         bytes=len(json.dumps(tool)), tokens=tokens.count_json(tool),
                         detail=name))
    return out


def responses(body: dict) -> list[Block]:
    """OpenAI's Responses API: the system prompt is `instructions`, the turns
    are `input`, and a tool is flat rather than wrapped in `function`."""
    out: list[Block] = []
    if body.get("instructions"):
        out.extend(_split_sections(body["instructions"], "instructions",
                                   ("harness", "vendor system prompt")))
    items = body.get("input")
    if isinstance(items, str):
        out.append(_block("user turn", "session", "input", items))
        items = []
    for i, item in enumerate(items or []):
        content = item.get("content")
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        content = content or ""
        if not content:
            continue
        where = f"input[{i}] {item.get('role', item.get('type', '?'))}"
        default = ("harness", "vendor system prompt") if item.get("role") in (
            "system", "developer") else ("unattributed", "prompt text")
        if len(content) < 400 and default[0] == "unattributed":
            out.append(_block("user turn", "session", where, content))
        else:
            out.extend(_split_sections(content, where, default))
    for tool in body.get("tools") or []:
        name = tool.get("name") or (tool.get("function") or {}).get("name") or "?"
        out.append(Block(label="tool schema", owner="tool", where="tools",
                         bytes=len(json.dumps(tool)), tokens=tokens.count_json(tool),
                         detail=name))
    return out


def decompose(capture: dict) -> list[Block]:
    body = capture.get("body") or {}
    path = capture.get("path", "")
    fn = (openai if "chat/completions" in path
          else responses if "/responses" in path else anthropic)
    blocks = fn(body)
    envelope = len(json.dumps(body).encode()) - sum(b.bytes for b in blocks)
    if envelope > 0:
        blocks.append(Block(label="JSON envelope", owner="harness",
                            where="body", bytes=envelope,
                            tokens=tokens.count("x" * 0) + envelope // 4))
    return blocks


def roll_up(blocks: list[Block]) -> dict:
    """Owner -> {bytes, tokens, n}. The ranking the report is built on."""
    out: dict = {}
    for b in blocks:
        slot = out.setdefault(b.owner, {"bytes": 0, "tokens": 0, "n": 0})
        slot["bytes"] += b.bytes
        slot["tokens"] += b.tokens
        slot["n"] += 1
    return out
