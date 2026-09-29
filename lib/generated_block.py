"""generated_block.py -- one splice algorithm, shared by every compiler that
rewrites a delimited block inside a file it does not otherwise own.

EXTRACTED 2026-09-05 from the boundary compiler's `splice()` and from
`l1.py`'s `_splice()`. Both were the same
function, copy-pasted -- l1.py's own docstring said so ("Straight from
boundaries-compile") -- and had already drifted: boundaries-compile joined a
replaced block with a single "\\n", l1.py with "\\n\\n". Nothing compared
their outputs, so the drift was silent. One implementation now; the marker-
rename and duplicate-block fixes both found on 2026-09-04 apply to every
caller at once, and so will the next one.

THE JOIN, RESOLVED. The two callers disagreed only on the branch where a
stale block is stripped and replaced. Every caller already agrees, in the
fresh-append branch below, that a generated block gets exactly one blank line
before it. l1.py's "\\n\\n" is the replace-branch that keeps that promise;
boundaries-compile's "\\n" broke it by butting the new block directly against
whatever text preceded the old one. This module keeps l1.py's version -- not
an arbitrary pick between two copies, but the one the surrounding logic in
BOTH copies already implied.

NOT MERGED HERE: the memory subsystem's reindex stage has a
`replace_block`. It answers a different question -- substitute content
between an EXISTING, named `BEGIN:tag (generated)` / `END:tag` pair inside a
file that carries several such blocks, and report a problem (never write) if
the marker is missing -- where this module strips every stale block from a
file that carries at most one, generated or not, and appends fresh when none
is found. Forcing the two together would make reindex.py silently create a
block that should have been a reported error, or make this module refuse to
create a block that legitimately does not exist yet. Same marker vocabulary,
different contract; kept separate on purpose.
"""
from __future__ import annotations

import re


def splice(existing: str, block: str, block_any: re.Pattern) -> str:
    """Remove EVERY block matching `block_any`, then append `block` once.

    `block_any` must match broadly enough to find a block under an OLD marker
    text, not just the current one -- a marker rename must not orphan the old
    block -- and the removal must strip every match, not just the first, or a
    second stray block sits in the file forever. Both bugs were found
    together on 2026-09-04, in the boundaries-compile copy of this function.

    `block` arrives fully formed (markers included); this function owns only
    placement, never content.
    """
    stripped = block_any.sub("", existing)
    if stripped != existing:
        return (stripped.rstrip("\n") + "\n\n" + block + "\n") if stripped.strip() \
            else (block + "\n")
    sep = "" if existing.endswith("\n\n") or not existing \
        else ("\n" if existing.endswith("\n") else "\n\n")
    return existing + sep + block + "\n"
