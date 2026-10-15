#!/usr/bin/env python3
# TYPE: TOOLKIT
"""fedit.py - tiny line-based text editor for the sandbox.
# keywords: edit|editor|line|insert|delete|replace|substitute|text|file
# examples: import $name; $name.run_fedit("notes.txt", ["p", "a 3 added line", "w", "q"])

Usage: fedit.py <file>

Commands (enter one per line):
  p [a [b]]        print lines a..b (default: all)
  i <n> <text>     insert text before line n
  a <n> <text>     append text after line n
  d <n> [m]        delete lines n..m (or just n)
  r <n> <text>     replace line n with text
  s/old/new/       substitute old with new in all lines (first occurrence per line)
  S/old/new/       substitute in all lines, all occurrences
  w                save to file
  W <path>         save as <path>
  q                quit (prompts to save if modified)
  --selftest       run self tests
  h                help
Numbers may be negative (count from end, -1 = last line).
"""
import os
import re
import sys
import subprocess
from typing import Sequence

BASE = os.path.dirname(os.path.abspath(__file__))


__all__ = ["resolve", "run_fedit", "selftest", "main"]


def resolve(path: str) -> str:
    """Resolve a path relative to this module's directory to an absolute path."""
    p = path if os.path.isabs(path) else os.path.join(BASE, path)
    return os.path.normpath(p)


def run_fedit(path: str, cmds: str | Sequence[str], timeout: float = 15) -> subprocess.CompletedProcess[str]:
    """Run the fedit CLI against <path>, feeding <cmds> (string or list of
    command lines) on stdin; returns the CompletedProcess."""
    import subprocess
    if isinstance(cmds, (list, tuple)):
        cmds = "\n".join(cmds) + "\n"
    return subprocess.run(
        [sys.executable, os.path.join(BASE, 'fedit.py'), path],
        input=cmds, capture_output=True, text=True, timeout=timeout)


def selftest() -> int:
    """Verify edit commands end-to-end on tmp/ files (scripted stdin)."""
    import shutil
    import subprocess
    import tempfile
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("PASS" if cond else "FAIL") + "  " + name)
        if not cond:
            ok = False

    d = tempfile.mkdtemp(dir=os.path.join(os.path.dirname(BASE), 'tmp'))
    try:
        p1 = os.path.join(d, 'a.txt')
        cmds = ('i 1 alpha\n'
                'i 2 beta\n'
                'a 2 middle\n'
                'r 3 omega\n'
                's/ha/x/\n'
                'w\nq\n')
        r = run_fedit(p1, cmds)
        content = open(p1).read() if os.path.exists(p1) else ''
        check('build from scratch (i/a/r/s/w)', r.returncode == 0 and content == 'alpx\nbeta\nomega\n')

        p2 = os.path.join(d, 'b.txt')
        with open(p2, 'w') as f:
            f.write('one\ntwo\nthree\nfour\n')
        r = run_fedit(p2, 'd 2\nr -1 last\nw\nq\n')
        content = open(p2).read()
        check('delete + negative-index replace', content == 'one\nthree\nlast\n')

        r = run_fedit(p2, 'S/e/x/\nw\nq\n')
        content = open(p2).read()
        check('S replaces all occurrences', content == 'onx\nthrxx\nlast\n')

        p3 = os.path.join(d, 'c.txt')
        r = run_fedit(p2, 'W %s\nq\n' % p3)
        check('W save-as', os.path.exists(p3) and open(p3).read() == open(p2).read())

        p4 = os.path.join(d, 'empty.txt')
        r = run_fedit(p4, 'p\nq\n')
        check('p on empty file ok', r.returncode == 0)

        r = run_fedit(p1, 'zzz\nq\n')
        check('unknown cmd tolerated', r.returncode == 0)

        rr = subprocess.run([sys.executable, os.path.join(BASE, 'fedit.py'), '-h'],
                            capture_output=True, text=True, timeout=5)
        check('-h exits 0 quickly (no hang)', rr.returncode == 0)

        # modified-file quit prompt: answer 'n' -> nothing saved
        p5 = os.path.join(d, 'd.txt')
        with open(p5, 'w') as f:
            f.write('orig\n')
        r = run_fedit(p5, 'i 1 new\nq\nn\n')
        check('quit without save discards', open(p5).read() == 'orig\n')
        r = run_fedit(p5, 'i 1 new\nq\ny\n')
        check('quit with save persists', open(p5).read() == 'new\norig\n')

        # out-of-range edits must not crash (regression: IndexError)
        with open(p5, 'w') as f:
            f.write('a\nb\n')
        r = run_fedit(p5, 'r 99 zzz\nq\nn\n')
        check('r out-of-range no crash', r.returncode == 0 and open(p5).read() == 'a\nb\n')
        r = run_fedit(p5, 'p 1 99\nq\n')
        check('p out-of-range no crash', r.returncode == 0)
        r = run_fedit(p5, 'd 99\nw\nq\n')
        check('d out-of-range no crash', r.returncode == 0 and open(p5).read() == 'a\nb\n')
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print('\n%s' % ('ALL TESTS PASSED' if ok else 'SOME TESTS FAILED'))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point (see the module docstring for usage)."""
    args = sys.argv[1:] if argv is None else list(argv)
    if not args:
        print(__doc__)
        sys.exit(1)
    if args[0] in ('-h', '--help', 'help'):
        print(__doc__)
        sys.exit(0)
    if args[0] == '--selftest':
        sys.exit(selftest())
    fname = resolve(args[0])
    lines = []
    if os.path.exists(fname):
        with open(fname, 'r', errors='replace') as f:
            lines = f.read().splitlines()
    modified = False

    def idx(n):
        return n - 1 if n >= 1 else len(lines) + n  # -1 -> last

    def print_range(a, b):
        start = max(0, idx(a))
        end = len(lines) if b is None else idx(b)
        end = min(max(end, start), len(lines))
        for i in range(start, end):
            print(f"{i+1:>5}: {lines[i]}")

    while True:
        try:
            cmd = input('> ').rstrip('\n')
        except (EOFError, KeyboardInterrupt):
            if modified:
                ans = input('modified! save before quit? [y/N] ').strip().lower()
                if ans in ('y', 'yes'):
                    with open(fname, 'w') as f:
                        f.write('\n'.join(lines) + ('\n' if lines else ''))
            print('bye')
            return
        if not cmd.strip():
            continue
        if cmd and cmd[0] in ('s', 'S') and len(cmd) > 1 and cmd[1] != ' ':
            op = cmd[0]
            rest = cmd[1:]
        else:
            parts = cmd.split(' ', 1)
            op = parts[0]
            rest = parts[1].strip() if len(parts) > 1 else ''

        if op == 'h':
            print(__doc__)
        elif op == 'p':
            nums = rest.split()
            a = int(nums[0]) if nums else 1
            b = int(nums[1]) if len(nums) > 1 else None
            print_range(a, b)
        elif op == 'i':
            sp = rest.split(' ', 1)
            if len(sp) < 2:
                print('usage: i <n> <text>'); continue
            n = int(sp[0]); text = sp[1]
            lines.insert(idx(n), text); modified = True
        elif op == 'a':
            sp = rest.split(' ', 1)
            if len(sp) < 2:
                print('usage: a <n> <text>'); continue
            n = int(sp[0]); text = sp[1]
            lines.insert(idx(n) + 1, text); modified = True
        elif op == 'd':
            nums = rest.split()
            if not nums:
                print('usage: d <n> [m]'); continue
            a = idx(int(nums[0]))
            b = idx(int(nums[1])) if len(nums) > 1 else a
            if a > b:
                a, b = b, a
            if a < 0 or a >= len(lines):
                print(f'error: line {nums[0]} out of range (1..{len(lines)})'); continue
            del lines[a:b+1]; modified = True
        elif op == 'r':
            sp = rest.split(' ', 1)
            if len(sp) < 2:
                print('usage: r <n> <text>'); continue
            n = idx(int(sp[0]))
            if n < 0 or n >= len(lines):
                print(f'error: line {sp[0]} out of range (1..{len(lines)})'); continue
            lines[n] = sp[1]; modified = True
        elif op == 's' or op == 'S':
            m = re.match(r'^(.)(.*)\1(.*)\1$', rest)
            if not m:
                print('usage: s/old/new/'); continue
            old, new = m.group(2), m.group(3)
            count = 0
            for i, line in enumerate(lines):
                if old in line:
                    if op == 's':
                        lines[i] = line.replace(old, new, 1)
                    else:
                        lines[i] = line.replace(old, new)
                    count += 1
            modified = True
            print(f'replaced in {count} line(s)')
        elif op == 'w':
            with open(fname, 'w') as f:
                f.write('\n'.join(lines) + ('\n' if lines else ''))
            modified = False
            print(f'saved {len(lines)} lines')
        elif op == 'W':
            if not rest:
                print('usage: W <path>'); continue
            with open(resolve(rest), 'w') as f:
                f.write('\n'.join(lines) + ('\n' if lines else ''))
            print(f'saved to {rest}')
        elif op == 'q':
            if modified:
                ans = input('modified! save before quit? [y/N] ').strip().lower()
                if ans in ('y', 'yes'):
                    with open(fname, 'w') as f:
                        f.write('\n'.join(lines) + ('\n' if lines else ''))
            print('bye')
            return
        else:
            print(f'unknown: {op} (h for help)')


if __name__ == '__main__':
    sys.exit(main())
