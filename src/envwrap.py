#!/usr/bin/env python3
# TYPE: TOOLKIT
# keywords: |env|environment|sandbox|home|pip|git|wrapper|shell|sh|helpers|permissions|cache|payload|
# examples: |import envwrap; envwrap.envrun(["git", "status"])||./envwrap.py pip list||./envwrap.py --selftest|
"""envwrap.py - Python entry carrying the sandbox environment shell helpers.

The shell helpers are package payload: they ship in this module's own
directory and are invoked through `sh`:

  envrun <cmd> [args...]   run a command with the sandbox's writable HOME,
                           pip cache and git config
  pip <args...>            pip via envrun, e.g. `pip install numpy`
  permissions-check.sh     verify sandbox path access after permissions are
                           granted

Sh-invocation: `sh <helper_dir()>/envrun <cmd> [args...]`, and the same
pattern for `pip` and `permissions-check.sh`.  `helper_dir()` is this
module's directory, so one invocation style works in the source tree and in
an installed package.

Python API:
  import envwrap
  envwrap.envrun(["git", "status"])  -> exit code of the command
  envwrap.pip(["install", "numpy"])  -> exit code of pip
  envwrap.permissions_check()        -> exit code of the probe

CLI:
  envwrap.py envrun <cmd> [args...]
  envwrap.py pip <args...]
  envwrap.py permissions-check
  envwrap.py --selftest
"""
import os
import subprocess
import sys
from collections.abc import Sequence

_HELPERS: tuple[str, ...] = ("envrun", "pip", "permissions-check.sh")


__all__ = ["envrun", "helper_dir", "permissions_check", "pip", "selftest", "main"]


def helper_dir() -> str:
    """Absolute path of the directory holding the shell helper payload."""
    return os.path.dirname(os.path.abspath(__file__))


def _helper(name: str) -> str:
    """Absolute path of one payload helper.

    Raises FileNotFoundError when the helper is missing from the payload.
    """
    path = os.path.join(helper_dir(), name)
    if not os.path.isfile(path):
        raise FileNotFoundError("shell helper payload missing: %s" % path)
    return path


def _run(name: str, args: Sequence[str]) -> int:
    """Run one payload helper through sh; return its exit code."""
    return subprocess.run(["sh", _helper(name), *args]).returncode


def envrun(command: Sequence[str]) -> int:
    """Run command (argv) with the sandbox environment; return its exit code.

    Raises ValueError when command is empty and FileNotFoundError when the
    payload is missing.
    """
    if not command:
        raise ValueError("command must not be empty")
    return _run("envrun", command)


def pip(args: Sequence[str]) -> int:
    """Run pip with the sandbox environment; return pip's exit code.

    Raises FileNotFoundError when the payload is missing.
    """
    return _run("pip", args)


def permissions_check() -> int:
    """Run the sandbox permission probe; return its exit code.

    Raises FileNotFoundError when the payload is missing.
    """
    return _run("permissions-check.sh", ())


def selftest() -> int:
    """Run the self checks; return 0 when all pass."""
    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name)
        ok = ok and cond

    for name in _HELPERS:
        check("%s present" % name, os.path.isfile(os.path.join(helper_dir(), name)))
        check("%s sh syntax" % name,
              subprocess.run(["sh", "-n", _helper(name)], capture_output=True).returncode == 0)
    check("envrun runs a command", envrun(["true"]) == 0)
    try:
        envrun([])
        check("empty command rejected", False)
    except ValueError:
        check("empty command rejected", True)
    try:
        _helper("no-such-helper")
        check("missing payload raises", False)
    except FileNotFoundError:
        check("missing payload raises", True)
    print("\n%s" % ("ALL TESTS PASSED" if ok else "SOME TESTS FAILED"))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry: dispatch to a payload helper; return its exit code (2 on usage errors)."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--selftest":
        return selftest()
    if args and args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if args and args[0] == "envrun" and len(args) > 1:
        return envrun(args[1:])
    if args and args[0] == "pip":
        return pip(args[1:])
    if args and args[0] == "permissions-check":
        return permissions_check()
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
