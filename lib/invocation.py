"""invocation -- one parser for the command line, and the flags a pass carries.

WHY. Five subcommands hand-rolled the same parse, slicing values at hard-coded
offsets (`a[7:]` for `--kind=`, `a[10:]` for `--context=`), and all of them
IGNORED anything they did not recognise. On 2026-09-03 that cost a commit: a
mistyped `--dry-dun` for `--dry-run` was dropped silently and a REAL pass ran,
writing a partial report over the previous night's and committing it. A flag nobody declared must cost an error message, not a
mutation -- which is how `capture` has always behaved, and it is the part of
this CLI that runs unattended.

A SPEC IS A DECLARATION. Each subcommand states the flags and options it
accepts and the usage line it advertises, in one place. `tests/test_invocation`
compares the two, so a flag can no longer be documented and unimplemented --
`report --last` was in the usage text for a month with nothing behind it.

WHAT IS NOT HERE. `capture.py` keeps its own twelve-line parse: it runs on
every SessionEnd and PostToolUse and may import nothing beyond `os` and `sys`.
The rule is not that imports are slow, it is that the hook path must not
acquire imports that can go missing or grow -- and a module shared with the
rest of the CLI is exactly the thing that later grows one. `skills index` keeps
its argparse, which already fails closed on an unknown flag.
"""
from __future__ import annotations

import dataclasses
import pathlib


class UsageError(ValueError):
    """A flag nobody declared, or a value that will not parse. Never ignored."""


@dataclasses.dataclass(frozen=True)
class Spec:
    """What one subcommand accepts. `usage` is checked against it by a test."""
    name: str
    usage: str = ""
    flags: tuple = ()              # bare switches, e.g. "--dry-run"
    options: tuple = ()            # `--key=value`, e.g. "--stages"
    aliases: dict = dataclasses.field(default_factory=dict)   # "-n" -> "--dry-run"
    positional: str | None = None  # what a bare argument means; None = none taken


@dataclasses.dataclass(frozen=True)
class Invocation:
    """One parsed command line, plus where this machine keeps its data."""
    spec: Spec
    base: pathlib.Path             # PAZRAS_DATA_DIR
    agents: pathlib.Path           # PAZRAS_AGENTS_DIR
    flags: frozenset
    options: dict
    rest: tuple = ()
    help: bool = False

    def has(self, name: str) -> bool:
        return name in self.flags

    def get(self, name: str, default: str = "") -> str:
        return self.options.get(name, default)

    def integer(self, name: str, default: int) -> int:
        """`--top=N`. A bad value is a usage error, not a traceback."""
        raw = self.options.get(name)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            raise UsageError(f"{name} wants a number, got {raw!r}")


def parse(argv, spec: Spec, base, agents) -> Invocation:
    """Parse `argv` against `spec`. Raises UsageError; never guesses."""
    flags, options, rest, want_help = set(), {}, [], False
    for arg in argv:
        if arg in ("-h", "--help"):
            want_help = True
            continue
        name, _, value = arg.partition("=")
        name = spec.aliases.get(name, name)
        if name in spec.options:
            if not _:
                raise UsageError(f"{name} takes a value: {name}=VALUE")
            options[name] = value
        elif name in spec.flags and not _:
            flags.add(name)
        elif arg.startswith("-"):
            known = ", ".join(sorted(spec.flags) + [f"{o}=" for o in sorted(spec.options)])
            raise UsageError(f"unknown flag for `{spec.name}`: {arg}"
                             + (f" (accepts: {known})" if known else ""))
        elif spec.positional:
            rest.append(arg)
        else:
            raise UsageError(f"`{spec.name}` takes no arguments, got {arg!r}")
    return Invocation(spec=spec, base=pathlib.Path(base), agents=pathlib.Path(agents),
                      flags=frozenset(flags), options=options, rest=tuple(rest),
                      help=want_help)
