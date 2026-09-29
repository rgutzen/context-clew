"""harnesses -- drive each harness once, against the recorder, and bring back
the request it sent.

ONE ADAPTER PER HARNESS, because they do not agree on anything: which env var
redirects inference, whether a config file can be overridden, whether the CLI
has a non-interactive mode at all. What they do share is the shape of the
answer -- a `Result` -- so the report does not branch on harness.

A HARNESS THAT CANNOT BE DRIVEN REPORTS `failed` AND WHY. It never reports a
number derived some other way. Mixing a measured figure with a reconstructed
one in the same table is how the plugin-cache error happened
(`knowledge/tools/claude-code.md`); `status` keeps the two apart in the type
system, not in a footnote.

THE REDIRECTS, and the traps behind each:

  claude    `--settings` with an env block. `~/.claude/settings.json` carries
            an `env` block, and settings env BEATS the parent process env --
            exporting ANTHROPIC_BASE_URL in the child is not enough, which is
            why the redirect goes through `--settings`.

  opencode  XDG_CONFIG_HOME pointed at a COPY of `~/.config/opencode`, so the
            plugin list and AGENTS.md still load, then the copied config's
            `provider.<p>.options.baseURL` is rewritten to the recorder. That
            override was inert while `@loreai/opencode` was installed (it
            rewrote every provider's baseURL to the lore gateway); with the
            plugin removed 2026-09-11 it is the seam again.

  codex     `-c model_providers.…` overrides on the command line; no file is
            written, so a failed audit leaves no config behind.

  pi        `PI_CODING_AGENT_DIR` points at a COPY of `~/.pi/agent` with
            models.json's provider baseUrl rewritten to the recorder.

  hermes    NOT intercepted. `hermes prompt-size --json` already reports the
            fixed prefix offline and by block. Re-deriving it through a capture
            would be a second, worse implementation of a number the harness
            computes exactly -- and hermes reports BYTES, which is our unit of
            record anyway.

CWD IS A PARAMETER, NOT A DETAIL. Measured 2026-09-03: the same Claude Code
session costs ~12k tokens in an empty directory and far more inside a repo with
per-project memory and instructions -- per-project injection is a per-project
cost. An audit that does not say which directory it ran in is not reproducible,
so `Result` carries
it.
"""
from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import pty
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time

from . import blocks
from .recorder import Recorder

ALL = ("claude", "opencode", "codex", "pi", "hermes")


@dataclasses.dataclass
class Result:
    harness: str
    status: str                      # measured | native | failed
    method: str
    cwd: str
    blocks: list = dataclasses.field(default_factory=list)
    capture: dict | None = None
    note: str = ""
    stderr: str = ""

    @property
    def total_bytes(self) -> int:
        return sum(b.bytes for b in self.blocks)

    @property
    def total_tokens(self) -> int:
        return sum(b.tokens for b in self.blocks)


def select_primary(captures: list) -> dict | None:
    """Which captured request is *the agent's* turn.

    THE RULE, stated so it can be argued with: a request that carries tool
    schemas is an agent turn; one without them is a utility call (title
    generation, a summariser, a guard). Among agent turns, take the largest --
    the fixed prefix is at its fullest on the first real turn. With no tooled
    request at all, fall back to the largest of what arrived and let the report
    say the selection was a fallback.
    """
    if not captures:
        return None
    tooled = [c for c in captures if (c.get("body") or {}).get("tools")]
    pool = tooled or captures
    best = dict(max(pool, key=lambda c: c["bytes"]))
    best["_selected_from"] = len(captures)
    best["_selection"] = "tooled request" if tooled else "fallback: no tooled request arrived"
    return best


def _run(cmd, cwd, env, rec, first=180.0, settle=15.0):
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    arrived = rec.wait_settle(first, settle)
    proc.terminate()
    try:
        _out, err = proc.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        _out, err = proc.communicate()
    return arrived, err


# -- pty interaction (interactive TUI capture) ------------------------------
_ANSI = re.compile(
    r"\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[a-zA-Z]|\x1b[()][0-9A-B]|\x1b[=>]")


class _Pty:
    """A child on a pty with a draining reader and an ANSI-stripped screen.

    Claude Code's TUI renders *without inter-word spaces* -- words are laid out
    with cursor-positioning escapes rather than literal spaces -- so `flat()`
    strips ANSI *and* collapses all whitespace, and every marker we match on is
    written space-free too (`Yes,Itrustthisfolder`, `automodeon(...)`).
    """

    def __init__(self, argv, cwd, env):
        self.buf = []
        self._master, slave = pty.openpty()
        self.proc = subprocess.Popen(
            argv, cwd=cwd, env=env, stdin=slave, stdout=slave, stderr=slave,
            preexec_fn=os.setsid)
        os.close(slave)
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        while True:
            try:
                chunk = os.read(self._master, 65536)
            except OSError:
                return
            if not chunk:
                return
            self.buf.append(chunk.decode("utf-8", "replace"))

    def flat(self) -> str:
        """The whole screen, ANSI gone and every space gone."""
        return re.sub(r"\s+", "", _ANSI.sub("", "".join(self.buf)))

    def send(self, data: str | bytes):
        os.write(self._master, data.encode() if isinstance(data, str) else data)

    def wait_for(self, needles, timeout: float, want: str = "") -> str | None:
        """Wait until any space-free needle is in the screen, then send `want`.
        Returns the needle that fired, or None on timeout."""
        end = time.time() + timeout
        while time.time() < end:
            flat = self.flat()
            for needle in needles:
                if needle in flat:
                    if want:
                        time.sleep(1.0)
                        self.send(want)
                    return needle
            time.sleep(0.4)
        return None

    def kill(self):
        try:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
        except Exception:
            pass


def run_claude_tui(rec: Recorder, cwd: str) -> Result:
    """Drive the interactive TUI -- the session the user actually pays for.

    The headless `-p` path is ~3.5x cheaper than the interactive TUI (measured
    2026-09-03), so an audit that stops at `-p` understates the real bill. This
    adapter answers the trust dialog and the custom-API-key dialog, then sends a
    bare prompt and captures the first full agent turn.
    """
    settings = {"env": {"ANTHROPIC_BASE_URL": rec.url, "ANTHROPIC_API_KEY": "stub",
                        "_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL": "1",
                        "DISABLE_AUTO_COMPACT": "1"}}
    env = dict(os.environ, ANTHROPIC_BASE_URL=rec.url, ANTHROPIC_API_KEY="stub",
               ANTHROPIC_AUTH_TOKEN="stub", TERM="xterm-256color",
               COLUMNS="120", LINES="45")
    for k in ("CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_CHILD_SESSION", "CLAUDECODE"):
        env.pop(k, None)
    tty = _Pty(["claude", "--settings", json.dumps(settings)], cwd, env)
    tty.wait_for(["Yes,Itrustthisfolder", "trustthisfolder"], 40,
                 want="\x1b[B\r")                 # Down to 'Yes' + Enter
    tty.wait_for(["detectedacustomapikey", "customAPIkey"], 10,
                 want="\x1b[A\r")                 # Up to 'Yes' + Enter
    tty.wait_for(["shift+tabtocycle", "automode"], 60)  # main prompt is up
    tty.send("hi\r")
    arrived = rec.wait_settle(60, 8.0)
    tty.kill()
    if not arrived:
        return Result("claude", "failed", "claude TUI via pty", cwd,
                      note="no request reached the recorder",
                      stderr=tty.flat()[-500:])
    cap = _primary_or_fail(rec.captures, "claude TUI")
    return Result("claude", "measured", "claude TUI (pty), trust dialog answered",
                  cwd, blocks=blocks.decompose(cap), capture=cap,
                  note=f"{cap['_selection']}; {cap['_selected_from']} request(s) captured",
                  stderr=tty.flat()[-400:])


# -- adapters ---------------------------------------------------------------
def run_claude(rec: Recorder, cwd: str) -> Result:
    settings = {"env": {"ANTHROPIC_BASE_URL": rec.url,
                        "ANTHROPIC_API_KEY": "stub-audit-key",
                        "_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL": "1",
                        "DISABLE_AUTO_COMPACT": "1"}}
    env = dict(os.environ, ANTHROPIC_BASE_URL=rec.url,
               ANTHROPIC_API_KEY="stub-audit-key", ANTHROPIC_AUTH_TOKEN="stub-audit-key",
               CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    arrived, err = _run(["claude", "-p", "hi", "--settings", json.dumps(settings)],
                        cwd, env, rec)
    return _finish("claude", "claude -p, ANTHROPIC_BASE_URL via --settings",
                   cwd, rec, arrived, err)


def run_opencode(rec: Recorder, cwd: str) -> Result:
    src = pathlib.Path.home() / ".config/opencode"
    if not src.is_dir():
        return Result("opencode", "failed", "-", cwd,
                      note="no ~/.config/opencode to copy")
    home = pathlib.Path(tempfile.mkdtemp(prefix="pazctx-oc-"))
    dst = home / "opencode"
    dst.mkdir(parents=True)
    for name in ("opencode.json", "AGENTS.md", "oh-my-opencode-slim.json"):
        s = src / name
        if s.exists():
            shutil.copy(s.resolve(), dst / name)
    cfg = json.loads((dst / "opencode.json").read_text())
    provider = next((p for p in (cfg.get("provider") or {}) if "ollama" in p), None)
    if provider is None:
        return Result("opencode", "failed", "-", cwd,
                      note="no local provider in opencode.json to redirect")
    model = next(iter(cfg["provider"][provider]["models"]))
    cfg["model"] = f"{provider}/{model}"
    # The redirect: opencode itself honours this, now that the plugin that
    # used to overwrite every provider's baseURL is gone (2026-09-11).
    cfg["provider"][provider].setdefault("options", {})["baseURL"] = rec.url + "/v1"
    (dst / "opencode.json").write_text(json.dumps(cfg, indent=2))
    env = dict(os.environ, XDG_CONFIG_HOME=str(home))
    arrived, err = _run(["opencode", "run", "hi"], cwd, env, rec, first=240.0, settle=20.0)
    shutil.rmtree(home, ignore_errors=True)
    return _finish("opencode",
                   "opencode run, XDG_CONFIG_HOME copy + provider baseURL override",
                   cwd, rec, arrived, err)


def run_codex(rec: Recorder, cwd: str) -> Result:
    base = rec.url + "/v1"
    cmd = ["codex", "exec", "hi",
           "-c", 'model_providers.pazctx={name="pazctx",'
                 f'base_url="{base}",wire_api="responses",env_key="PAZCTX_KEY"}}',
           "-c", 'model_provider="pazctx"',
           "-c", 'model="stub-model"',
           "--skip-git-repo-check"]
    env = dict(os.environ, PAZCTX_KEY="stub-audit-key")
    arrived, err = _run(cmd, cwd, env, rec)
    return _finish("codex", "codex exec, -c model_providers override", cwd, rec, arrived, err)


def run_pi(rec: Recorder, cwd: str) -> Result:
    """Drive pi once against the recorder.

    Pi reads `models.json` for the ollama provider's baseUrl and POSTs
    `{baseUrl}/chat/completions`, which the recorder answers. The config dir is
    redirected with `PI_CODING_AGENT_DIR` so the real `~/.pi/agent` is never
    touched; the mutable dotfiles are copied (not symlinked) because pi may
    rewrite them at startup, while the heavy read-only trees (npm/ ≈ 289MB,
    extensions/, agents/) are symlinked so the packages settings.json names
    still resolve."""
    src = pathlib.Path.home() / ".pi" / "agent"
    if not src.is_dir():
        return Result("pi", "failed", "-", cwd, note="no ~/.pi/agent to copy")
    home = pathlib.Path(tempfile.mkdtemp(prefix="pazctx-pi-"))
    for name in ("agents", "extensions", "skills", "themes", "npm"):
        s = src / name
        if s.exists():
            os.symlink(s.resolve(), home / name)
    for name in ("AGENTS.md", "SYSTEM.md", "auth.json", "claude-bridge.json",
                 "models-store.json", "settings.json"):
        s = src / name
        if s.exists():
            shutil.copy(s.resolve(), home / name)
    models_path = home / "models.json"
    try:
        models = json.loads((src / "models.json").read_text())
    except (OSError, ValueError):
        shutil.rmtree(home, ignore_errors=True)
        return Result("pi", "failed", "-", cwd, note="models.json unreadable")
    redirected = 0
    for prov in models.get("providers", {}).values():
        if prov.get("api") in ("openai-completions", "openai-responses"):
            prov["baseUrl"] = rec.url + "/v1"
            redirected += 1
    if not redirected:
        shutil.rmtree(home, ignore_errors=True)
        return Result("pi", "failed", "-", cwd,
                      note="no openai-completions/responses provider to redirect")
    models_path.write_text(json.dumps(models))
    env = dict(os.environ, PI_CODING_AGENT_DIR=str(home), PI_OFFLINE="1")
    arrived, err = _run(["pi", "-p", "hi", "--no-approve", "--no-session"],
                        cwd, env, rec, first=90.0, settle=10.0)
    shutil.rmtree(home, ignore_errors=True)
    return _finish("pi", "pi -p, PI_CODING_AGENT_DIR copy", cwd, rec, arrived, err)


def run_hermes(rec: Recorder | None, cwd: str) -> Result:
    """Native, not intercepted -- see the module note. The `rec` argument is
    accepted for a uniform adapter signature and unused."""
    try:
        proc = subprocess.run(["hermes", "prompt-size", "--json"], cwd=cwd,
                              capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Result("hermes", "failed", "hermes prompt-size --json", cwd, note=str(exc))
    try:
        data = json.loads(proc.stdout[proc.stdout.index("{"):])
    except ValueError:
        return Result("hermes", "failed", "hermes prompt-size --json", cwd,
                      note="could not parse JSON", stderr=proc.stderr[-800:])
    return Result("hermes", "native", "hermes prompt-size --json (bytes are exact)",
                  cwd, blocks=_hermes_blocks(data), capture={"body": data},
                  note="reported by the harness itself; tokens estimated from bytes")


def _hermes_blocks(data: dict) -> list:
    """Map hermes' own breakdown onto our owners.

    Hermes reports BYTES ONLY, so tokens here are bytes/4 and the Result says
    so. Do not quietly upgrade that to a tiktoken count of a reconstructed
    string: we never see hermes' assembled prompt, only its measurements, and
    counting a string we rebuilt would be an estimate of an estimate.
    """
    def blk(label, owner, nbytes, detail=""):
        nbytes = int(nbytes or 0)
        return blocks.Block(label=label, owner=owner, where="prompt-size",
                            bytes=nbytes, tokens=nbytes // 4, detail=detail)

    out = []
    total = _num(data.get("system_prompt"), "bytes")
    parts = {}
    for key, owner, label in (("skills_index", "harness", "skill index"),
                              ("memory", "vault", "memory block"),
                              ("user_profile", "vault", "user profile")):
        val = _num(data.get(key), "bytes")
        if val:
            parts[key] = val
            out.append(blk(label, owner, val))
    if total:
        rest = total - sum(parts.values())
        if rest > 0:
            out.append(blk("vendor system prompt", "harness", rest))
    for entry in data.get("toolsets_breakdown") or []:
        out.append(blk("tool schema", "tool", entry.get("json_bytes"),
                       detail=str(entry.get("toolset", "?"))))
    if not out:
        out.append(blk("whole prompt", "unattributed", total))
    return out


def _num(d, *keys):
    """Pull a numeric field, tolerating a schema that has moved. Returns 0
    rather than raising: a hermes release renaming a key should cost one wrong
    row, not the whole audit."""
    for k in keys:
        v = (d or {}).get(k)
        if isinstance(v, (int, float)):
            return v
    return 0


def _primary_or_fail(captures, result_meta) -> dict:
    """`select_primary`, but the caller has already established a request
    arrived, so None is a logic error, not a runtime case."""
    cap = select_primary(captures)
    if cap is None:
        raise AssertionError(f"{result_meta}: select_primary returned None "
                             "after arrival was confirmed")
    return cap


def _finish(name, method, cwd, rec, arrived, err) -> Result:
    if not arrived:
        return Result(name, "failed", method, cwd,
                      note="no request reached the recorder", stderr=(err or "")[-900:])
    cap = _primary_or_fail(rec.captures, name)
    return Result(name, "measured", method, cwd,
                  blocks=blocks.decompose(cap), capture=cap,
                  note=f"{cap['_selection']}; {cap['_selected_from']} request(s) captured",
                  stderr=(err or "")[-400:])


ADAPTERS = {"claude": run_claude, "opencode": run_opencode,
            "codex": run_codex, "pi": run_pi, "hermes": run_hermes}


def audit(names, cwd: str, interactive: bool = False) -> list[Result]:
    """Drive the harnesses once. `interactive` switches claude from the cheap
    headless `-p` path to the real TUI (see `run_claude_tui`) -- the number the
    user actually pays. Only claude has a TUI worth capturing; the others have
    no interactive form that changes the prefix."""
    out = []
    for name in names:
        if name == "hermes":
            out.append(run_hermes(None, cwd))
            continue
        if interactive and name == "claude":
            with Recorder() as rec:
                out.append(run_claude_tui(rec, cwd))
            continue
        with Recorder() as rec:
            out.append(ADAPTERS[name](rec, cwd))
    return out
