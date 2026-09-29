"""tokens -- counting, and being honest about what the count is worth.

THE UNIT OF RECORD IS BYTES. A byte count is exact, reproducible on any
machine, and nobody has to trust our tokenizer to check it. Every table here
carries bytes first for that reason.

TOKENS ARE AN ESTIMATE, ALWAYS. Anthropic's tokenizer is not public; OpenAI's
`o200k_base` (via tiktoken) is the closest local stand-in and is what this
module uses. On English prose and JSON tool schemas it lands within a few
percent of Claude's own count -- close enough to rank sinks, not close enough
to quote as a bill. `calibrate()` measures the residual error against real
Claude Code usage instead of asserting a figure, so the caveat stays a number.

NO NETWORK. An audit must run offline, cost nothing, and never ship a captured
prompt to a third party to have it counted. If tiktoken is missing we fall back
to bytes/4 and SAY SO in `estimator()` -- a silent fallback would let a wrong
number pass as a measured one.
"""
from __future__ import annotations

import json
import pathlib

FLOOR = 1000        # below this a "session" is a stub, not a measurement
_ENC = None
_ENC_NAME = "bytes/4 (tiktoken unavailable — ESTIMATE ONLY)"

try:                                                       # pragma: no cover
    import tiktoken
    _ENC = tiktoken.get_encoding("o200k_base")
    _ENC_NAME = "tiktoken o200k_base (proxy for Claude's tokenizer)"
except Exception:                                          # pragma: no cover
    pass


def estimator() -> str:
    """What produced the numbers. Printed in every report header."""
    return _ENC_NAME


def count(text: str) -> int:
    if not text:
        return 0
    if _ENC is not None:
        return len(_ENC.encode(text, disallowed_special=()))
    return max(1, len(text.encode()) // 4)


def count_json(obj) -> int:
    """A tool schema costs what its serialisation costs, not what its prose
    costs -- braces and key names are tokens too."""
    return count(json.dumps(obj, ensure_ascii=False))


def calibrate(projects_dir: pathlib.Path, limit: int = 40) -> dict:
    """Real first-turn prompt totals from Claude Code transcripts.

    Claude Code records the API's own `usage` per assistant turn. The first
    assistant turn of a session was billed for the whole fixed prefix, so
    `input + cache_read + cache_creation` on that turn is a MEASURED total that
    owes nothing to our tokenizer. It is not decomposable -- the transcript
    keeps no system prompt -- which is exactly why the recorder exists; the two
    together give a decomposition we trust and a total we did not estimate.
    """
    totals, stubs = [], 0
    if not projects_dir.is_dir():
        return {"sessions": 0, "note": f"no transcripts at {projects_dir}"}
    files = sorted(projects_dir.glob("*/*.jsonl"),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    for path in files:
        try:
            with path.open() as fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if rec.get("type") != "assistant":
                        continue
                    usage = (rec.get("message") or {}).get("usage") or {}
                    if not usage:
                        continue
                    total = (usage.get("input_tokens", 0)
                             + usage.get("cache_read_input_tokens", 0)
                             + usage.get("cache_creation_input_tokens", 0))
                    # A real session cannot have a three-figure prefix: the
                    # vendor system prompt alone is thousands of tokens. Small
                    # totals are stub sessions -- including the ones THIS tool
                    # creates when it drives `claude -p` against the recorder.
                    # Counting our own probe as a session would drag the median
                    # toward the number we are trying to check against.
                    if total >= FLOOR:
                        totals.append({"session": path.stem, "first_turn_prompt": total})
                    elif total:
                        stubs += 1
                    break
        except OSError:
            continue
    if not totals:
        return {"sessions": 0, "note": "transcripts held no usage records"}
    values = sorted(t["first_turn_prompt"] for t in totals)
    mid = len(values) // 2
    return {
        "sessions": len(values),
        "min": values[0],
        "median": values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) // 2,
        "max": values[-1],
        "excluded_stub_sessions": stubs,
        "note": "measured by the API, not by our tokenizer",
    }
