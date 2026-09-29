"""render -- the tables. Numbers a reader can check, in a shape they can act on.

EVERY TABLE LEADS WITH BYTES. Tokens are an estimate (`tokens.py`); bytes are
not. A reader who distrusts our tokenizer can still audit the ranking.

RANK BY WHAT YOU WOULD CHANGE. Rows are owners and files, never abstract
categories, because the point of the audit is the next action: disable this
plugin, drop this tool, split this leaf.
"""
from __future__ import annotations


def _bar(part, whole, width=18):
    if not whole:
        return " " * width
    filled = max(1, round(width * part / whole)) if part else 0
    return "█" * filled + "·" * (width - filled)


def rule(title, width=78):
    return f"\n{title}\n" + "─" * width


def harness_table(results) -> str:
    lines = [rule("FIXED PREFIX PER SESSION — what the harness sends before you type")]
    lines.append(f"  {'harness':10} {'status':9} {'bytes':>9} {'~tokens':>9}  method")
    for r in sorted(results, key=lambda r: -r.total_tokens):
        lines.append(f"  {r.harness:10} {r.status:9} {r.total_bytes:9,} "
                     f"{r.total_tokens:9,}  {r.method[:38]}")
        if r.status == "failed":
            lines.append(f"  {'':10} └─ {r.note}")
            if r.stderr.strip():
                first = r.stderr.strip().splitlines()[0][:70]
                lines.append(f"  {'':10}    {first}")
    return "\n".join(lines)


def owner_table(result, width=78) -> str:
    from .blocks import roll_up
    roll = roll_up(result.blocks)
    total = sum(v["tokens"] for v in roll.values()) or 1
    lines = [rule(f"{result.harness}  ·  where the prefix goes  (cwd={result.cwd})", width)]
    lines.append(f"  {'owner':14} {'blocks':>6} {'bytes':>9} {'~tok':>8} {'share':>6}")
    for owner, v in sorted(roll.items(), key=lambda kv: -kv[1]["tokens"]):
        share = 100 * v["tokens"] / total
        lines.append(f"  {owner:14} {v['n']:6} {v['bytes']:9,} {v['tokens']:8,} "
                     f"{share:5.1f}%  {_bar(v['tokens'], total)}")
    if "unattributed" in roll and roll["unattributed"]["tokens"] > 0.15 * total:
        lines.append("  ! over 15% unattributed — blocks.SECTIONS is behind this "
                     "harness version")
    return "\n".join(lines)


def sink_table(result, top=12) -> str:
    lines = [rule(f"{result.harness}  ·  largest single sinks")]
    lines.append(f"  {'~tok':>7} {'bytes':>8}  {'owner':12} what")
    for b in sorted(result.blocks, key=lambda b: -b.tokens)[:top]:
        what = f"{b.label}" + (f" · {b.detail}" if b.detail else "")
        lines.append(f"  {b.tokens:7,} {b.bytes:8,}  {b.owner:12} {what[:44]}")
    return "\n".join(lines)


def tree_table(layers, caps, routes, inventory, top=10) -> str:
    lines = [rule("PAZRAS TREE — the always-paid layers")]
    for name, v in layers.items():
        flag = "OVER" if v["tokens"] > v["budget"] else "ok"
        lines.append(f"  {name}  {'+'.join(v['files']):24} {v['tokens']:6,} tok  "
                     f"budget {v['budget']:>5}  {flag}")
    always = sum(v["tokens"] for v in layers.values())
    lines.append(f"  {'':6}{'always loaded, total':24} {always:6,} tok")
    for c in caps:
        lines.append(f"  cap {c['file']:20} {c['chars']:6,} chars / {c['cap']:,}"
                     f"  {'OVER' if c['over'] else 'ok'}")

    lines.append(rule("PAZRAS TREE — cost of answering one question, worst case"))
    lines.append("  (entry + L1 files + router chain + the most expensive leaf "
                 "it offers)")
    lines.append(f"  {'~tok':>6}  question")
    for r in sorted(routes, key=lambda r: -r.tokens):
        extra = [h.rel for h in r.hops[3:]]
        lines.append(f"  {r.tokens:6,}  {r.question[:44]:44} {' → '.join(extra)[:50]}")
        if r.unresolved:
            lines.append(f"  {'':6}  ! routing table points at missing: {r.unresolved}")
        if r.external:
            lines.append(f"  {'':6}  · leaves the tree (not measured): {r.external}")

    lines.append(rule("PAZRAS TREE — biggest files (a route that lands here pays this)"))
    lines.append(f"  {'~tok':>6} {'bytes':>8}  file")
    for f in sorted(inventory, key=lambda f: -f.tokens)[:top]:
        lines.append(f"  {f.tokens:6,} {f.bytes:8,}  {f.rel}")
    total = sum(f.tokens for f in inventory)
    lines.append(f"  {total:6,} tok in {len(inventory)} files "
                 f"— never load this; it is the point of the routing tables")
    return "\n".join(lines)


def route_table(rows) -> str:
    lines = [rule("ROUTING PROBE — is the tree actually delivered to the model?")]
    lines.append("  expanded = bytes are in the prompt · pointer = only the path, "
                 "the model must read it")
    for harness, verdict, findings, note in rows:
        lines.append(f"\n  {harness}  →  {verdict.upper()}   {note}")
        for f in findings:
            mark = "ok  " if f.ok else "MISS"
            lines.append(f"    {mark} {f.file:24} {f.status:9} "
                         f"(expected {f.expected})")
            if not f.ok:
                lines.append(f"         canary: {f.canary!r}")
    return "\n".join(lines)


def calibration(cal) -> str:
    if not cal.get("sessions"):
        return f"\n  calibration: {cal.get('note', 'unavailable')}"
    return (f"\n  Cross-check, Claude Code transcripts ({cal['sessions']} sessions, "
            f"API-reported):\n"
            f"    first-turn prompt  min {cal['min']:,} · median {cal['median']:,} "
            f"· max {cal['max']:,} tokens\n"
            f"    These are real sessions in real projects; the audit above is one "
            f"stated cwd.")


def links_table(broken, adoptions, fixed, agents) -> str:
    """Pointer integrity. Repairable and unrepairable are separated because they
    need different people: the first is a lookup this tool already did, the
    second is a decision only the author can make."""
    lines = [rule("POINTER INTEGRITY — does the tree still reach its own files")]
    if fixed:
        lines.append(f"  repaired {fixed} pointer(s) in place")

    fixable = [b for b in broken if b.fixable]
    # STRICT is its own bucket, and it outranks the others: a stale markdown
    # name in a file that DESCRIBES this repository is a claim about the system
    # that is no longer true, which is a different kind of wrong from a pointer
    # that moved. Pulled out before the fixable/stuck split so it cannot be
    # filed under the bare-name counts, which is where it used to disappear.
    strict = [b for b in broken if b.strict and not b.fixable]
    rest = [b for b in broken if not (b.strict and not b.fixable)]
    stuck = [b for b in rest if not b.fixable]

    if strict:
        lines.append(f"\n  STALE MECHANISM CLAIM — this file describes the "
                     f"repository and points at a path that resolves to "
                     f"nothing ({len(strict)})")
        for b in strict:
            lines.append(f"    {str(b.source.relative_to(agents)):42}:{b.line:<4} {b.raw}")
            lines.append(f"    {'':42} └─ {b.reason}")

    if fixable:
        lines.append(f"\n  REPAIRABLE — one file of that name exists ({len(fixable)});"
                     " run with --fix")
        for b in fixable:
            lines.append(f"    {str(b.source.relative_to(agents)):42}:{b.line:<4} "
                         f"{b.raw}  →  {b.repair}")
    paths = [b for b in stuck if b.is_pointer]
    bare = [b for b in stuck if not b.is_pointer]
    if paths:
        lines.append(f"\n  NEEDS A DECISION — written as a path, resolves to nothing"
                     f" ({len(paths)})")
        for b in paths:
            lines.append(f"    {str(b.source.relative_to(agents)):42}:{b.line:<4} {b.raw}")
            lines.append(f"    {'':42} └─ {b.reason}")
    if bare:
        import collections
        counts = collections.Counter(b.raw for b in bare)
        lines.append(f"\n  BARE NAMES — no namesake in the tree ({len(bare)} mentions,"
                     f" {len(counts)} names). Prose unless you recognise one:")
        lines.append("    " + "  ".join(
            f"{name}×{n}" if n > 1 else name for name, n in counts.most_common()))
    adoptable = [a for a in adoptions if a.ok]
    stuck_orphans = [a for a in adoptions if not a.ok]
    if adoptable:
        lines.append(f"\n  ORPHANED, ADOPTABLE — their own folder's router omits them"
                     f" ({len(adoptable)}); run with --fix")
        for a in adoptable:
            lines.append(f"    {str(a.orphan.relative_to(agents)):50} → row in "
                         f"{a.router.relative_to(agents)}")
    if stuck_orphans:
        lines.append(f"\n  ORPHANED, NEEDS A DECISION ({len(stuck_orphans)})")
        for a in stuck_orphans:
            lines.append(f"    {str(a.orphan.relative_to(agents)):50} {a.reason}")
    if not broken and not adoptions:
        lines.append("\n  every pointer resolves, and every file is reachable")
    return "\n".join(lines)
