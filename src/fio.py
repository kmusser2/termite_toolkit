#!/usr/bin/env python3
# TYPE: TOOLKIT
"""fio.py - quick file I/O helpers for the sandbox workdir.
# keywords: file|files|read|write|list|cat|head|tail|append|remove|delete|move|copy|backup|info|find|io|filesystem|directory|ls
# examples: ./fio.py list | ./fio.py cat path/to/file | ./fio.py head main.cpp 10

Usage:
  fio.py list [pattern]           list files in workdir (optionally matching pattern)
  fio.py cat <path>               print file contents
  fio.py head <path> [n]          print first n lines (default 20)
  fio.py tail <path> [n]          print last n lines (default 20)
  fio.py write <path> <text>      write text (overwrites)
  fio.py append <path> <text>     append text
  fio.py rm <path>                delete file
  fio.py mv <src> <dst>           move/rename
  fio.py cp <src> <dst>           copy
  fio.py backup <path>            make timestamped copy
  fio.py info <path>              size, mtime, line count
  fio.py find <needle> [path]     grep for needle in file(s)
  fio.py strip-ansi <path>        print file with ANSI escapes removed
Relative paths resolve to the workspace root (the directory containing src/ and tmp/).
  fio.py --selftest               run self tests
"""
import os
import shutil
import sys
import time
import stat

if __package__:
    from . import ansi
else:
    import ansi

# Workspace root (contains src/ and tmp/); relative paths resolve here.
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

__all__ = ["resolve", "atomic_write", "selftest", "main"]


def atomic_write(path: str, text: str) -> None:
    """Replace path with text atomically (temp file + os.replace), preserving
    the replaced file's mode; raises OSError on failure.

    The one home of the sandbox's atomic-replace dance: plain 'w' overwrites of
    existing files are denied (Errno 13), so every in-place rewrite goes
    through a fresh temp file swapped in with os.replace.
    """
    tmp = path + '.new'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(text)
    # Preserve the original file's mode (e.g. executable bit on scripts).
    try:
        os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode))
    except OSError:
        pass
    os.replace(tmp, path)


def resolve(path: str) -> str:
    """Resolve a path relative to the workspace root to an absolute path."""
    p = path if os.path.isabs(path) else os.path.join(BASE, path)
    return os.path.normpath(p)


def need_args(args: list[str], n: int) -> None:
    """Exit with an error message unless args has at least n elements."""
    if len(args) < n:
        print(f"error: expected at least {n} argument(s)")
        sys.exit(1)


def cmd_list(args: list[str]) -> None:
    """List files in the workspace, optionally filtered to paths containing pattern."""
    pattern = args[0] if args else None
    entries = []
    for root, dirs, files in os.walk(BASE):
        # skip nothing; show relative paths
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, BASE)
            size = os.path.getsize(full)
            entries.append((rel, size))
    entries.sort()
    if pattern:
        entries = [(r, s) for r, s in entries if pattern in r]
    if not entries:
        print("(no files)")
        return
    for rel, size in entries:
        print(f"{size:>10,}  {rel}")


def cmd_cat(args: list[str]) -> None:
    """Print a file's raw bytes to stdout."""
    need_args(args, 1)
    p = resolve(args[0])
    if not os.path.exists(p):
        print(f"error: {args[0]} does not exist"); sys.exit(1)
    with open(p, 'rb') as f:
        sys.stdout.buffer.write(f.read())


def cmd_head(args: list[str]) -> None:
    """Print the first n lines of a file (default 20)."""
    need_args(args, 1)
    n = int(args[1]) if len(args) > 1 else 20
    p = resolve(args[0])
    with open(p, 'r', errors='replace') as f:
        for i, line in enumerate(f):
            if i >= n:
                break
            print(line, end='')


def cmd_tail(args: list[str]) -> None:
    """Print the last n lines of a file (default 20)."""
    need_args(args, 1)
    n = int(args[1]) if len(args) > 1 else 20
    p = resolve(args[0])
    with open(p, 'r', errors='replace') as f:
        lines = f.readlines()
    for line in lines[-n:]:
        print(line, end='')


def cmd_write(args: list[str]) -> None:
    """Write text to a file atomically (tmp + os.replace), overwriting if present."""
    need_args(args, 2)
    p = resolve(args[0])
    text = ' '.join(args[1:])
    body = text + ('\n' if text and not text.endswith('\n') else '')
    atomic_write(p, body)
    print(f"wrote {len(text)} chars to {args[0]}")


def cmd_append(args: list[str]) -> None:
    """Append text to a file, adding a trailing newline if missing."""
    need_args(args, 2)
    p = resolve(args[0])
    text = ' '.join(args[1:])
    with open(p, 'a') as f:
        f.write(text + ('\n' if text and not text.endswith('\n') else ''))
    print(f"appended {len(text)} chars to {args[0]}")


def cmd_rm(args: list[str]) -> None:
    """Delete a file."""
    need_args(args, 1)
    p = resolve(args[0])
    os.remove(p)
    print(f"removed {args[0]}")


def cmd_mv(args: list[str]) -> None:
    """Move or rename a file or directory."""
    need_args(args, 2)
    shutil.move(resolve(args[0]), resolve(args[1]))
    print(f"moved {args[0]} -> {args[1]}")


def cmd_cp(args: list[str]) -> None:
    """Copy a file to a new location, preserving metadata."""
    need_args(args, 2)
    shutil.copy2(resolve(args[0]), resolve(args[1]))
    print(f"copied {args[0]} -> {args[1]}")


def cmd_backup(args: list[str]) -> None:
    """Make a timestamped backup copy of a file."""
    need_args(args, 1)
    p = resolve(args[0])
    ts = time.strftime('%Y%m%d-%H%M%S')
    dst = f"{p}.{ts}.bak"
    shutil.copy2(p, dst)
    print(f"backup: {os.path.basename(dst)}")


def cmd_info(args: list[str]) -> None:
    """Print size, line count, and mtime for a file."""
    need_args(args, 1)
    p = resolve(args[0])
    st = os.stat(p)
    nlines = 0
    with open(p, 'rb') as f:
        nlines = sum(1 for _ in f)
    print(f"path:    {args[0]}")
    print(f"size:    {st.st_size:,} bytes")
    print(f"lines:   {nlines:,}")
    print(f"mtime:   {time.ctime(st.st_mtime)}")


def cmd_find(args: list[str]) -> None:
    """Grep for a needle across files, printing path:line matches."""
    need_args(args, 1)
    needle = args[0]
    p = resolve(args[1]) if len(args) > 1 else None
    if p is not None and os.path.isfile(p):
        paths = [p]
    else:
        paths = [os.path.join(r, f) for r, _, fs in os.walk(p or BASE) for f in fs]
    hits = 0
    for path in paths:
        try:
            with open(path, 'rb') as f:
                chunk = f.read(8192)
            if b'\0' in chunk:
                continue  # binary file, skip
            with open(path, 'r', errors='replace') as f:
                for i, line in enumerate(f, 1):
                    if needle in line:
                        rel = os.path.relpath(path, BASE)
                        print(f"{rel}:{i}: {line.rstrip()}")
                        hits += 1
        except OSError:
            continue
    print(f"({hits} match{'es' if hits != 1 else ''})")


def cmd_strip_ansi(args: list[str]) -> None:
    """Print a file with ANSI escape sequences removed."""
    need_args(args, 1)
    p = resolve(args[0])
    with open(p, 'r', errors='replace') as f:
        for line in f:
            print(ansi.strip_str(line), end='')


CMDS = {
    'list': cmd_list, 'ls': cmd_list,
    'cat': cmd_cat, 'head': cmd_head, 'tail': cmd_tail,
    'write': cmd_write, 'append': cmd_append,
    'rm': cmd_rm, 'mv': cmd_mv, 'cp': cmd_cp, 'backup': cmd_backup,
    'info': cmd_info, 'find': cmd_find, 'strip-ansi': cmd_strip_ansi,
}


def selftest() -> int:
    """Verify write (new + overwrite), append, backup, rm. Uses tmp/ only."""
    ok = True
    def check(name, cond):
        nonlocal ok
        print(("PASS" if cond else "FAIL") + "  " + name)
        if not cond:
            ok = False

    import tempfile
    d = tempfile.mkdtemp(dir=os.path.join(BASE, 'tmp'))
    p = os.path.join(d, 't.txt')
    cmd_write([p, 'hello'])
    check('write new file', open(p).read() == 'hello\n')
    cmd_write([p, 'world'])
    check('write overwrites atomically', open(p).read() == 'world\n')
    check('no .new left behind', not os.path.exists(p + '.new'))
    cmd_append([p, 'again'])
    check('append', open(p).read() == 'world\nagain\n')
    os.chmod(p, 0o755)
    atomic_write(p, 'mode')
    import stat as _stat
    check('atomic_write preserves mode', _stat.S_IMODE(os.stat(p).st_mode) == 0o755)
    cmd_backup([p])
    baks = [f for f in os.listdir(d) if f.endswith('.bak')]
    check('backup created', len(baks) == 1)
    cmd_rm([p])
    check('rm', not os.path.exists(p))
    check('resolve relative to workdir',
          resolve('tmp') == os.path.normpath(os.path.join(BASE, 'tmp')))
    shutil.rmtree(d, ignore_errors=True)
    print('\n%s' % ('ALL TESTS PASSED' if ok else 'SOME TESTS FAILED'))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point (see the module docstring for usage)."""
    args = sys.argv[1:] if argv is None else list(argv)
    if not args or args[0] in ('-h', '--help', 'help'):
        print(__doc__)
        sys.exit(0 if args else 1)
    if args[0] == '--selftest':
        sys.exit(selftest())
    cmd = args[0]
    if cmd not in CMDS:
        print(f"unknown command: {cmd}")
        sys.exit(1)
    CMDS[cmd](args[1:])


if __name__ == '__main__':
    sys.exit(main())
