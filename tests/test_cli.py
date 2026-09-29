"""The CLI, run the way a stranger runs it: as a script, from another directory.

This test exists because of a real bug. The CLI used to live at the repository
root, so `HERE/lib` was the right place to look for the vendored modules. Moving
it to `bin/` kept the code reading correctly and made the *script* unrunnable —
`ModuleNotFoundError: No module named 'lib'` — while all 66 unit tests stayed
green, because the test suite imports the package and never executes the entry
point. A package that passes its tests and cannot be started is the failure this
file is here to prevent.

The vault is a scratch tree, never `$HOME`: `spin` writes into harness entry
points, and a test that can write to the developer's own config is a test that
will eventually do it.
"""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "clew"

VAULT = {
    "CONTEXT.md": "# Vault\n\n## Routing table\n\n| Looking for | Read |\n|---|---|\n"
                  "| how to behave | `conventions/collaboration.md` |\n",
    "user/user-identity.md": "# Who the user is\n\nA conversation partner.\n",
    "conventions/collaboration.md": "# Collaboration\n\nAlways-on.\n",
    "knowledge/meta/leaf.md": "# A leaf\n\nSee `knowledge/meta/leaf.md`.\n",
}


def _vault(tmp_path: pathlib.Path) -> pathlib.Path:
    agents = tmp_path / "vault" / "agents"
    for rel, text in VAULT.items():
        p = agents / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return agents


def _run(agents: pathlib.Path, *argv: str, cwd: pathlib.Path | None = None):
    env = {"PATH": "/usr/bin:/bin", "CLEW_VAULT": str(agents), "HOME": str(agents.parent)}
    return subprocess.run([sys.executable, str(CLI), *argv], cwd=str(cwd or agents.parent),
                          env=env, capture_output=True, text=True)


def test_the_script_runs_from_a_directory_that_is_not_the_repository(tmp_path):
    agents = _vault(tmp_path)
    out = _run(agents, "tree", "--json", cwd=tmp_path)
    assert out.returncode == 0, out.stderr
    payload = json.loads(out.stdout)
    assert payload["layers"]["L0"]["files"] == ["CONTEXT.md"]


def test_it_runs_with_no_environment_beyond_the_vault(tmp_path):
    """No PYTHONPATH, no cwd inside the repository: the path handling must be the
    script's own business."""
    agents = _vault(tmp_path)
    out = _run(agents, "follow", cwd=tmp_path)
    assert out.returncode == 0, out.stderr
    assert "POINTER INTEGRITY" in out.stdout


def test_an_unknown_command_fails_loudly(tmp_path):
    agents = _vault(tmp_path)
    out = _run(agents, "scry", cwd=tmp_path)
    assert out.returncode == 1
    assert "unknown command" in out.stderr


def test_a_missing_vault_is_an_error_not_an_empty_report(tmp_path):
    out = _run(tmp_path / "nowhere", "tree", "--json", cwd=tmp_path)
    assert out.returncode != 0
    assert "nowhere" in out.stderr or "vault" in out.stderr.lower()
