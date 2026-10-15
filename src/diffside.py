#!/usr/bin/env python3
# TYPE: TOOLKIT
"""diffside.py - side-by-side diff preview (SGR or HTML).
# keywords: diff|preview|side-by-side|unified|patch|compare|git|table|html|sgr|color
# examples: import $name; $name.from_unified(diff_text) | import $name; $name.files(old_lines, new_lines, old_name="a.py", new_name="b.py")

Renders a unified diff (hedit --preview, git diff, difflib) or two files
side-by-side as a fixed-width boxed table:

    ╭─ tmp/greet_demo.py ────────────────────────╮
    │   OLD                     │   NEW            │
    │  @@ -1,3 +1,3 @@                          │
    │   def greet(name):        │   def greet(name):│
    │ -     print("Hi, %s" % n…│ +     print(f"He…│
    │   main()                  │   main()          │
    ╰─────────────────────────────────────────────╯

Design notes:
  * a del block followed by an add block (a replace) renders on the SAME
    row: left = red old, right = green new (no staircase)
  * removed lines carry a red '- ' prefix, added lines a green '+ ' prefix,
    context lines carry two spaces -- columns stay aligned, and the diff
    stays readable without color (colorblind / NO_COLOR safe)
  * every row is padded to the SAME total width, so box borders line up
  * fromfile == tofile (the hedit --preview case) -> one box header with
    the file path, not duplicated OLD/NEW file names
  * a dimmed '@@ -a,b +c,d @@' badge per hunk shows the line ranges
  * --html emits a self-contained HTML page (CSS colors, no ANSI needed)

Usage:
  diffside.py OLD NEW [--width N] [--no-color] [--sep SEP] [--no-box] [--html]
  diffside.py --diff [FILE|-] [--width N] [--no-color] [--no-box] [--html]
  diffside.py --selftest

Python API:
  import $name
  $name.files(old_lines, new_lines, ...) -> [str]  # rendered rows
  $name.from_unified(diff_text, ...)     -> [str]  # rendered rows
  $name.html(diff_text, ...)             -> str    # full HTML page
"""
import argparse
import difflib
import html as _html
import re
import shutil
import sys
from typing import Sequence

import ansi

__all__ = ["files", "from_unified", "html", "html_files", "selftest", "main"]

RED = "\x1b[31m"
GREEN = "\x1b[32m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
CYAN = "\x1b[36m"
RESET = "\x1b[0m"

DEFAULT_SEP = "  |  "
DEFAULT_WIDTH = 60          # per-column cap when the terminal width is unknown
_MIN_COL = 6                # smallest a column may be (headers need ~5)
ELL = "\u2026"


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _vis(s):
    return len(ansi.strip_str(s))


def _truncate(s, w):
    s = s.rstrip()
    return s if len(s) <= w else s[: max(w - 1, 0)] + ELL


def _pad(s, w):
    s = s.rstrip()
    return s + " " * max(0, w - _vis(s))


def _cell(text, prefix, code, w, color):
    """One table cell: 2-char prefix + truncated text, colored, padded to w."""
    body = _truncate(text, max(1, w - 2))
    s = prefix + body
    if code and color:
        s = code + s + RESET
    return _pad(s, w)


def _default_width():
    """Autofit per-column width from the terminal; DEFAULT_WIDTH when unknown."""
    try:
        cols = shutil.get_terminal_size((DEFAULT_WIDTH, 24)).columns
    except Exception:
        cols = DEFAULT_WIDTH
    if not cols or cols <= 0:
        cols = DEFAULT_WIDTH
    return max(20, (cols - 8) // 2)


# --------------------------------------------------------------------------
# unified-diff parsing  (--- / +++ headers, @@ hunk ranges, +/- body lines)
# --------------------------------------------------------------------------

_HDR_RE = re.compile(r"^([+-]{3}) (.*)$")


def _split_hunks(diff_text):
    """-> (fromfile, tofile, [{"badge": "@@ ...", "lines": [(kind, text)...]}, ...])"""
    fromfile = tofile = None
    hunks, cur, in_hunk = [], None, False
    for line in diff_text.split("\n"):
        if line.startswith("@@"):
            if cur is not None:
                hunks.append(cur)
            cur = {"badge": line, "lines": []}
            in_hunk = True
            continue
        if not in_hunk:
            m = _HDR_RE.match(line)
            if m:
                p = m.group(2).strip()
                if m.group(1) == "---":
                    fromfile = p
                else:
                    tofile = p
            continue
        if line.startswith("\\"):
            continue  # "\ No newline at end of file"
        if line.startswith("+"):
            cur["lines"].append(("add", line[1:]))
        elif line.startswith("-"):
            cur["lines"].append(("del", line[1:]))
        elif line.startswith(" "):
            cur["lines"].append(("ctx", line[1:]))
        elif line == "":
            if cur is not None:
                hunks.append(cur)
                cur = None
                in_hunk = False
        else:
            cur["lines"].append(("ctx", line))
    if cur is not None:
        hunks.append(cur)
    return fromfile, tofile, hunks


def _pair(hunk_lines):
    """Pair a del block + its immediately following add block into replace rows.

    -> [("ctx", t) | ("del", t) | ("add", t) | ("repl", old, new), ...]
    """
    rows, i, n = [], 0, len(hunk_lines)
    while i < n:
        kind, text = hunk_lines[i]
        if kind == "ctx":
            rows.append(("ctx", text))
            i += 1
        elif kind == "del":
            dels, adds = [], []
            while i < n and hunk_lines[i][0] == "del":
                dels.append(hunk_lines[i][1]); i += 1
            while i < n and hunk_lines[i][0] == "add":
                adds.append(hunk_lines[i][1]); i += 1
            for k in range(max(len(dels), len(adds))):
                left = dels[k] if k < len(dels) else ""
                right = adds[k] if k < len(adds) else ""
                if left and not right:
                    rows.append(("del", left))
                elif right and not left:
                    rows.append(("add", right))
                else:
                    rows.append(("repl", left, right))
        else:
            while i < n and hunk_lines[i][0] == "add":
                rows.append(("add", hunk_lines[i][1])); i += 1
    return rows


# --------------------------------------------------------------------------
# logical table: (title, rows) where row specs are
#   ("row",  lp, lt, lc, rp, rt, rc)  l/r = prefix, text, color-code|None
#   ("hdr",  label_l, label_r)        OLD / NEW column headers
#   ("badge", text)                   dimmed @@ line, spans full width
#   ("full", text)                    bold line, spans full width
#   ("sep",)                          blank spacer row
# --------------------------------------------------------------------------

def _rows_for_diff(diff_text):
    fromfile, tofile, hunks = _split_hunks(diff_text)
    title = fromfile if (fromfile and fromfile == tofile) else ""
    rows = []
    if fromfile != tofile:
        if fromfile:
            rows.append(("full", "OLD  " + fromfile))
        if tofile:
            rows.append(("full", "NEW  " + tofile))
    rows.append(("hdr", "OLD", "NEW"))
    for hk, hunk in enumerate(hunks):
        if hk:
            rows.append(("sep",))
        rows.append(("badge", hunk["badge"]))
        for p in _pair(hunk["lines"]):
            k = p[0]
            if k == "ctx":
                rows.append(("row", "  ", p[1], None, "  ", p[1], None))
            elif k == "del":
                rows.append(("row", "- ", p[1], RED, "  ", "", None))
            elif k == "add":
                rows.append(("row", "  ", "", None, "+ ", p[1], GREEN))
            else:
                rows.append(("row", "- ", p[1], RED, "+ ", p[2], GREEN))
    # a trailing "\n" in the source shows up as one empty context row - drop it
    while rows and rows[-1][0] == "row" and rows[-1][2] == "" and rows[-1][5] == "":
        rows.pop()
    return title, rows


def _rows_for_files(old_lines, new_lines, old_name="", new_name=""):
    title = old_name if (old_name and old_name == new_name) else ""
    rows = []
    if old_name != new_name:
        if old_name:
            rows.append(("full", "OLD  " + old_name))
        if new_name:
            rows.append(("full", "NEW  " + new_name))
    rows.append(("hdr", "OLD", "NEW"))
    sm = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i1, i2):
                rows.append(("row", "  ", old_lines[k], None, "  ", new_lines[j1 + k - i1], None))
        elif tag == "delete":
            for k in range(i1, i2):
                rows.append(("row", "- ", old_lines[k], RED, "  ", "", None))
        elif tag == "insert":
            for k in range(j1, j2):
                rows.append(("row", "  ", "", None, "+ ", new_lines[k], GREEN))
        elif tag == "replace":
            for k in range(max(i2 - i1, j2 - j1)):
                left = old_lines[i1 + k] if k < i2 - i1 else ""
                right = new_lines[j1 + k] if k < j2 - j1 else ""
                rows.append(("row", "- " if left else "  ", left, RED if left else None,
                             "+ " if right else "  ", right, GREEN if right else None))
    return title, rows


def _column_widths(rows, width):
    ln = rn = _MIN_COL
    for row in rows:
        if row[0] != "row":
            continue
        ln = max(ln, 2 + _vis(row[2]))
        rn = max(rn, 2 + _vis(row[5]))
    return min(ln, width), min(rn, width)


# --------------------------------------------------------------------------
# SGR rendering
# --------------------------------------------------------------------------

def _box_geometry(rows, width, title):
    lw, rw = _column_widths(rows, width)
    t = title
    tlen = (_vis(t) + 2) if t else 0          # box title adds one space each side
    if t and 4 + tlen > 2 * width + 7:
        t = _truncate(t, 2 * width + 1)
        tlen = _vis(t) + 2
    total = max(lw + rw + 7, 4 + tlen)
    return lw, total - 7 - lw, total, t


def _box_top(title, total, color):
    d = DIM if color else ""
    e = RESET if color else ""
    if title:
        t = (BOLD + CYAN + " " + title + " " + RESET) if color else " " + title + " "
        inner = max(0, total - 4 - _vis(t))
    else:
        t = ""
        inner = total - 4
    return d + "╭─" + e + t + d + "─" * inner + "─╮" + e


def _box_bottom(total, color):
    d = DIM if color else ""
    e = RESET if color else ""
    return d + "╰─" + e + d + "─" * (total - 4) + "─╯" + e


def _box_row(lcell, rcell, lw, rw, color):
    d = DIM if color else ""
    e = RESET if color else ""
    return d + "│" + e + " " + lcell + " " + d + "│" + e + " " + rcell + " " + d + "│" + e


def _box_full(text, lw, rw, color):
    d = DIM if color else ""
    e = RESET if color else ""
    return d + "│" + e + " " + _pad(text, lw + rw + 3) + " " + d + "│" + e


def _render(rows, title, width, sep, color, box):
    lw_nat, rw_nat = _column_widths(rows, width)
    lw, rw_box, total, t = _box_geometry(rows, width, title)
    if box and total < 20:          # too narrow for a box -> plain side-by-side
        box = False
    rw = rw_box if box else rw_nat
    out = []
    if box:
        out.append(_box_top(t, total, color))
    elif t:
        out.append((BOLD if color else "") + t.strip() + (RESET if color else ""))
    for row in rows:
        k = row[0]
        if k == "row":
            left = _cell(row[2], row[1], row[3], lw, color)
            right = _cell(row[5], row[4], row[6], rw, color)
            out.append(_box_row(left, right, lw, rw, color) if box else left + sep + right)
        elif k == "hdr":
            left = _cell(row[1], "  ", BOLD, lw, color)
            right = _cell(row[2], "  ", BOLD, rw, color)
            out.append(_box_row(left, right, lw, rw, color) if box else left + sep + right)
        elif k in ("badge", "full"):
            line = ((DIM if k == "badge" else BOLD) if color else "") + \
                   row[1] + (RESET if color else "")
            if box:
                out.append(_box_full(line, lw, rw, color))
            else:
                out.append(_pad(line, lw + len(sep) + rw))
        else:  # "sep"
            out.append(_box_full("", lw, rw, color) if box else "")
    if box:
        out.append(_box_bottom(total, color))
    return out


# --------------------------------------------------------------------------
# HTML rendering
# --------------------------------------------------------------------------

def _html_page(rows, title, width, sep):
    lw, rw = _column_widths(rows, width)
    esc = _html.escape
    p = []
    p.append("<!DOCTYPE html><html><head><meta charset='utf-8'>")
    p.append("<style>body{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px;"
             "background:#fff;color:#222;margin:24px}"
             ".hdr{font-weight:bold;margin:3px 0}"
             ".badge{color:#888;margin:5px 0 0}"
             ".del{color:#b71c1c;background:#fdecea;border-radius:3px}"
             ".add{color:#1b5e20;background:#e8f5e9;border-radius:3px}"
             "table{border-collapse:collapse}td{vertical-align:top;white-space:pre;padding:0 8px 0 0}"
             "td.gap{color:#999}tr{line-height:1.45}</style></head><body>")
    if title:
        p.append("<div class='hdr'>%s</div>" % esc(title))
    p.append("<table>")
    for row in rows:
        k = row[0]
        if k == "hdr":
            p.append("<tr><td class='hdr'>%s</td><td class='gap'>%s</td><td class='hdr'>%s</td></tr>"
                     % (esc(row[1]), esc(sep), esc(row[2])))
        elif k == "badge":
            p.append("<tr><td colspan='3' class='badge'>%s</td></tr>" % esc(row[1]))
        elif k == "full":
            p.append("<tr><td colspan='3' class='hdr'>%s</td></tr>" % esc(row[1]))
        elif k == "sep":
            p.append("<tr><td colspan='3'>&nbsp;</td></tr>")
        else:
            left = _pad(_truncate(row[1] + row[2], lw), lw)
            right = _pad(_truncate(row[4] + row[5], rw), rw)
            cls_l = " class='del'" if row[3] else ""
            cls_r = " class='add'" if row[6] else ""
            p.append("<tr><td%s>%s</td><td class='gap'>%s</td><td%s>%s</td></tr>"
                     % (cls_l, esc(left), esc(sep), cls_r, esc(right)))
    p.append("</table></body></html>")
    return "\n".join(p)


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------

def files(old_lines: Sequence[str], new_lines: Sequence[str], width: int | None = None, sep: str = DEFAULT_SEP, color: bool = True, box: bool = True, old_name: str = "", new_name: str = "") -> str:
    """Render two files' lines side-by-side as a boxed table; returns the joined rows."""
    title, rows = _rows_for_files(old_lines, new_lines, old_name, new_name)
    return _render(rows, title, width if width is not None else _default_width(),
                   sep, color, box)


def from_unified(diff_text: str, width: int | None = None, sep: str = DEFAULT_SEP, color: bool = True, box: bool = True) -> str:
    """Render a unified diff string side-by-side; returns the joined rows."""
    title, rows = _rows_for_diff(diff_text)
    return _render(rows, title, width if width is not None else _default_width(),
                   sep, color, box)


def html(diff_text: str, width: int | None = None, sep: str = DEFAULT_SEP) -> str:
    """Render a unified diff string as a self-contained HTML page."""
    title, rows = _rows_for_diff(diff_text)
    return _html_page(rows, title, width if width is not None else _default_width(), sep)


def html_files(old_lines: Sequence[str], new_lines: Sequence[str], width: int | None = None, sep: str = DEFAULT_SEP, old_name: str = "", new_name: str = "") -> str:
    """Render two files' lines as a self-contained HTML page."""
    title, rows = _rows_for_files(old_lines, new_lines, old_name, new_name)
    return _html_page(rows, title, width if width is not None else _default_width(), sep)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _read_lines(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read().split("\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="diffside.py", description=__doc__.split("\n\n")[0])
    p.add_argument("paths", nargs="*", metavar="FILE", help="two files to compare")
    p.add_argument("--diff", metavar="FILE", nargs="?", const="-",
                   help="read a unified diff (FILE or - for stdin) instead of two files")
    p.add_argument("--width", type=int, default=None,
                   help="max column width (default: autofit to terminal)")
    p.add_argument("--no-color", action="store_true", help="plain output, no SGR")
    p.add_argument("--no-box", action="store_true", help="plain two-column rows, no box borders")
    p.add_argument("--html", action="store_true", help="emit a self-contained HTML page")
    p.add_argument("--sep", default=DEFAULT_SEP, help="column separator (default '%s')" % DEFAULT_SEP)
    p.add_argument("--selftest", action="store_true", help="run self tests and exit")
    args = p.parse_args(argv)

    if args.selftest:
        return _selftest()

    width = args.width if args.width is not None else _default_width()
    color = not args.no_color
    box = not args.no_box

    if args.diff is not None:
        if args.paths:
            p.error("cannot mix --diff with positional files")
        text = sys.stdin.read() if args.diff == "-" else open(args.diff, encoding="utf-8").read()
        if args.html:
            print(html(text, width, args.sep))
        else:
            print("\n".join(from_unified(text, width, args.sep, color, box)))
    else:
        if len(args.paths) != 2:
            p.error("expected two files (or --diff)")
        old, new = (_read_lines(x) for x in args.paths)
        while old and old[-1] == "":
            old.pop()
        while new and new[-1] == "":
            new.pop()
        if args.html:
            print(html_files(old, new, width, args.sep, args.paths[0], args.paths[1]))
        else:
            print("\n".join(files(old, new, width, args.sep, color, box,
                                  args.paths[0], args.paths[1])))
    return 0


def _selftest():
    ok = True

    def check(name, got, want):
        nonlocal ok
        if got != want:
            ok = False
            print("FAIL %s:\n  got  %r\n  want %r" % (name, got, want))
        else:
            print("ok   %s" % name)

    def widths(rows):
        return {_vis(r) for r in rows}

    # 1. files(): replace pairs on one row, prefixes, colors, uniform width
    old = ["def greet(name):", '    print("Hi, %s" % name)', "main()"]
    new = ["def greet(name):", '    print(f"Hello, {name}!")', "main()"]
    rows = files(old, new, width=60)
    check("files uniform width", len(widths(rows)), 1)
    check("files OLD header", any("OLD" in r for r in rows), True)
    check("files - prefix", any("- " in r and 'print("Hi' in r for r in rows), True)
    check("files + prefix", any("+ " in r and "print(f" in r for r in rows), True)
    check("files red", any(RED in r for r in rows), True)
    check("files green", any(GREEN in r for r in rows), True)

    # 2. no-color: no ESC, prefixes remain
    plain = files(old, new, width=60, color=False)
    check("no-color no ESC", all("\x1b" not in r for r in plain), True)
    check("no-color - prefix", any("- " in r and 'print("Hi' in r for r in plain), True)
    check("no-color uniform", len(widths(plain)), 1)

    # 3. from_unified, hedit case (same from/to): one box title, @@ badge, paired row
    diff = '''--- tmp/greet_demo.py
+++ tmp/greet_demo.py
@@ -1,16 +1,19 @@
 #!/usr/bin/env python3
-"""greet.py - a tiny greeting tool."""
+"""greet.py - a tiny greeting tool (shout/quiet options)."""
 
 import sys
 
-def greet(name, times=1):
+def greet(name, times=1, shout=False, quiet=False):
     """Print a greeting."""
     for _ in range(times):
-        print(f"Hello, {name}!")
+        msg = f"Hello, {name}!"
+        if quiet:
+            continue
+        print(msg.upper() if shout else msg)
 
 def main():
     name = sys.argv[1] if len(sys.argv) > 1 else "world"
-    greet(name)
+    greet(name, shout="--shout" in sys.argv, quiet="--quiet" in sys.argv)
 
 if __name__ == "__main__":
     main()
'''
    rows = from_unified(diff, width=60)
    check("hedit box title", "tmp/greet_demo.py" in rows[0] and ansi.strip_str(rows[0]).endswith("─╮"), True)
    check("hedit badge", any("@@ -1,16 +1,19 @@" in r for r in rows), True)
    check("hedit paired", any("- def greet(name, times=1)" in r and
                              "+ def greet(name, times=1, shout" in r for r in rows), True)
    check("hedit uniform", len(widths(rows)), 1)

    # 4. from_unified, two-file case: OLD/NEW file headers
    diff2 = """--- a/x.txt
+++ b/x.txt
@@ -1,3 +1,3 @@
 a
-b
+c
"""
    rows2 = from_unified(diff2, width=60)
    check("twofile OLD header", any("OLD  a/x.txt" in r for r in rows2), True)
    check("twofile NEW header", any("NEW  b/x.txt" in r for r in rows2), True)

    # 5. multi-hunk: two @@ badges, uniform width
    diff3 = """--- a/app.py
+++ b/app.py
@@ -1,3 +1,3 @@
 import os
-def old():
-    return 1
+def new():
+    return 2
 print("hi")
@@ -20,2 +21,3 @@
 x = 1
 y = 2
-print("old out")
+print("new out")
+print("done")
"""
    rows3 = from_unified(diff3, width=60)
    check("multihunk badges", sum("@@ -" in r for r in rows3), 2)
    check("multihunk uniform", len(widths(rows3)), 1)

    # 6. truncation with ellipsis
    long = ["x" * 120]
    rows_t = files(long, long, width=40, box=False)
    check("truncate ellipsis", any(ELL in r for r in rows_t), True)
    check("truncate fits cap", max(widths(rows_t)) <= 2 * 40 + len("  |  "), True)

    # 7. no-box: no box glyphs, still uniform
    rows_nb = files(old, new, width=60, box=False)
    check("no-box no glyphs", all("\u250c" not in r and "\u2510" not in r for r in rows_nb), True)
    check("no-box uniform", len(widths(rows_nb)), 1)

    # 8. html
    page = html(diff, width=60)
    check("html doctype", page.startswith("<!DOCTYPE html>"), True)
    check("html del class", "class='del'" in page, True)
    check("html add class", "class='add'" in page, True)

    print("SELFTEST %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
