# clew — the thread you carry into every session, and the way back out

Every agent session starts by paying a context tax: system prompts, always-on
rules, an entry file, whatever each harness decides to inject. Nobody reports the
number, and two measurements of the same setup disagree by 3× because one counts
files on disk and the other counts bytes on the wire.

This measures the wire, and compiles the always-on payload from one source into
every harness that must receive it.

*(A clew is a ball of thread — what you carry into the labyrinth and follow back
out. It is also where the word **clue** comes from.)*

## Commands

| | | |
|---|---|---|
| `clew spin` | *(alias `l1`)* | compile the always-on payload into each harness entry point, idempotently |
| `clew weigh` | *(alias `harness`)* | drive each harness against a stub endpoint and read the prefix it really sent |
| `clew follow` | *(alias `links`)* | do the pointers between documents resolve, and does anything point at each document? |
| `clew deliver` | *(alias `route`)* | is the knowledge tree actually delivered to the harness, or only promised? |
| `clew tree` | | what navigating the vault costs, by route, and where the budget is spent |
| `clew all` | | the four static checks, one report |

```bash
CLEW_VAULT=~/my-vault/agents bin/clew tree
CLEW_VAULT=~/my-vault/agents bin/clew spin --check   # exit 3 on drift
```

The vault is whatever `CLEW_VAULT` names, then `PAZRAS_AGENTS_DIR`, then
`./agents`. Assumed convention, not enforced: a knowledge tree with a `CONTEXT.md`
routing table per folder, an always-on set (`CONTEXT.md`,
`user/user-identity.md`, `conventions/collaboration.md`), and per-document
frontmatter. `tree` and `deliver` parse the routing tables themselves.

## What it believes

**Measured, not reconstructed.** `weigh` and `deliver` stand a stub inference
endpoint in front of the harness and read the request it actually sends. No token
reaches a provider, nothing is forwarded, and a harness that cannot be driven is
reported as `failed` — never filled in from a plausible estimate.

**Bytes are exact; tokens are an estimate.** The report prints which estimator
produced its numbers, and cross-checks against API-reported usage where it exists.

**Displacement, not truncation.** A file that is read on every turn carries a
hard cap; on overflow the least-referenced section moves verbatim into another
document and leaves one pointer behind. Nothing is silently dropped, and the cap
makes overflow a filing signal rather than a loss.

## Requirements and limits

- Python 3.11+ (stdlib only). No network access, no API key.
- `weigh` and `deliver` need the harnesses themselves installed (Claude Code,
  opencode, codex, pi, hermes) and report `failed` for any that cannot be driven.
  Expect `all` to take 1–4 minutes, dominated by start-up.
- `spin` writes into harness entry points under `$HOME` (`~/.claude/CLAUDE.md`,
  `~/.config/opencode/opencode.json`, `~/.pi/agent/AGENTS.md`,
  `~/.pi/agent/SYSTEM.md`, hermes' memory file); `--check` reports drift instead
  of writing.
- Linux/POSIX paths. Not tested elsewhere.

## Provenance

One artifact from a personal system, published one piece at a time. The vault
conventions it expects are the ones that system was built on; the writing about
them is a separate publication.

## Licence

MIT — see `LICENSE`.
