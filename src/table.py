#!/usr/bin/env python3
# TYPE: TOOLKIT
"""table.py - ANSI-safe Unicode table renderer for terminal output."""
# keywords: table|render|unicode|ansi|box|grid|column|display|terminal
# examples: import $name; $name.render_table(["a", "b"], [["1", "2"]])|./table.py --selftest
import shutil
import sys
import unicodedata
from typing import Sequence

import ansi

__all__ = ["display_width", "render_table", "selftest", "main"]

# Status words get colored when `color=True`: PASS green, FAIL red,
# BLOCKED yellow, STALE gray.
STATUS_COLORS = {
    'PASS': '32',
    'FAIL': '31',
    'BLOCKED': '33',
    'STALE': '90',
}


def display_width(value: object) -> int:  # ANSI sequences never count toward width; wcwidth avoided (stdlib-only)
    """Visible width of a value in terminal cells; ANSI escapes and combining chars count 0, wide/fullwidth chars count 2."""
    # Most toolkit output is ASCII/emoji/box-drawing; wcwidth is avoided to
    # keep this helper stdlib-only. ANSI sequences never count toward width.
    text = ansi.strip_str(str(value))
    return sum(0 if unicodedata.combining(ch) else (2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1) for ch in text)


def _pad(value, width):
    value = str(value)
    return value + ' ' * max(0, width - display_width(value))


def _colorize_status(value):
    """Wrap a cell in ANSI color if its plain text is exactly a status word."""
    plain = ansi.strip_str(str(value)).strip()
    code = STATUS_COLORS.get(plain)
    return value if code is None else f'\033[{code}m{value}\033[0m'


def render_table(headers: Sequence[str], rows: Sequence[Sequence[object]], *, title: str | None = None, border: str = '│', color: bool = True, max_width: int | None = None) -> str:
    """Return a uniformly boxed table. Headers and rows are sequences of values.

    When `color` is True, cells whose plain text is exactly a known status
    (PASS/FAIL/BLOCKED/STALE) are wrapped in the matching ANSI color.
    """
    headers = [str(x) for x in headers]
    rows = [[str(x) for x in row] for row in rows]
    n = len(headers)
    if any(len(row) != n for row in rows):
        raise ValueError('every row must have the same number of columns as headers')
    widths = [display_width(headers[i]) for i in range(n)]
    for row in rows:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], display_width(value))
    if max_width is None:
        max_width = shutil.get_terminal_size((100, 24)).columns
    # If needed, shrink the last column (without corrupting ANSI text).
    fixed = sum(widths) + 3*n + 1
    if fixed > max_width and n:
        widths[-1] = max(8, widths[-1] - (fixed - max_width))
    def cell(value, i):
        value = str(value)
        if display_width(value) > widths[i]:
            plain = ANSI.sub('', value)
            value = plain[:max(0, widths[i]-1)] + '…'
        if color:
            value = _colorize_status(value)
        return _pad(value, widths[i])
    top = '┌' + '┬'.join('─'*(w+2) for w in widths) + '┐'
    mid = '├' + '┼'.join('─'*(w+2) for w in widths) + '┤'
    bot = '└' + '┴'.join('─'*(w+2) for w in widths) + '┘'
    lines = []
    if title:
        lines.append(str(title))
    lines += [top, '│ ' + ' │ '.join(cell(headers[i], i) for i in range(n)) + ' │', mid]
    lines += ['│ ' + ' │ '.join(cell(row[i], i) for i in range(n)) + ' │' for row in rows]
    lines.append(bot)
    return '\n'.join(lines)


def selftest() -> int:
    """Verify table rendering: layout widths, status colorization, no-color path, and column-count mismatch error."""
    red = '\033[31mFAIL\033[0m'
    out = render_table(['Check', 'Status'], [['syntax', 'PASS'], ['pty', red]], max_width=80)
    lines = out.splitlines()
    assert len(lines) == 6
    assert all(display_width(line) == display_width(lines[0]) for line in lines)
    # status colorization: PASS -> green, FAIL -> red
    assert '\033[32mPASS\033[0m' in out
    assert '\033[31mFAIL\033[0m' in out
    # --no-color path: no ANSI escapes at all
    plain = render_table(['Check', 'Status'], [['syntax', 'PASS'], ['pty', 'FAIL']], color=False, max_width=80)
    assert '\033[' not in plain
    try:
        render_table(['a', 'b'], [['x']]); assert False
    except ValueError:
        pass
    print('table.py --selftest PASS')
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: ./table.py [--selftest] (otherwise print a demo table)."""
    args = sys.argv[1:] if argv is None else list(argv)
    if args and args[0] in ('-h', '--help', 'help'):
        print(__doc__)
        return 0
    if args and args[0] == '--selftest':
        return selftest()
    print(render_table(['Column 1', 'Column 2'], [['hello', 'world']]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
