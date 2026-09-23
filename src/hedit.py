#!/usr/bin/env python3
# TYPE: TOOLKIT
"""hedit.py - Hash-anchored line editor for AI agents.
# keywords: edit|editor|hash|anchor|hashline|stale|atomic|patch|diff|line|safe|reconcile|file|source|code|modify|change|rewrite|apply
# examples: import hedit; hedit.read_file("main.cpp") | import hedit; hedit.replace("main.cpp", "12#a1b2", ["return 0;"], preview=True) | import hedit; hedit.batch("main.cpp", [{"op": "replace", "anchor": "12#a1b2", "lines": ["x = 1"]}], preview=True)

Implements the "hashline" editing idea (Can Boluk / hledit / Dirac):
instead of tracking line numbers, each line is tagged with a short content
hash. Edits reference those anchors; if the file changed since the read,
the hash won't match and the edit is rejected BEFORE anything is written
(stale-safe). No fuzzy matching, no reproducing old code.

Credits & attribution:
- Concept: "hashline" hash-anchored editing by Can Boluk
  (https://blog.can.ac/2026/02/12/the-harness-problem/), refined by hledit
  (https://github.com/dabito/hledit, MIT), Dirac
  (https://github.com/dirac-run/dirac, Apache-2.0), and oh-my-pi
  (https://github.com/can1357/oh-my-pi, MIT).
- This file is an ORIGINAL, independent, stdlib-only implementation written
  from those public descriptions; no source code was copied from them.

License: MIT.

CLI:
  ./hedit.py read <file> [--offset N] [--limit M] [--grep PAT] [--context N]
                   [--plain] [--json]
  ./hedit.py replace <file> <anchor> [<end-anchor>] [--new-text TEXT | --file F | -] [--preview]
  ./hedit.py insert <file> <anchor> [--before|--after] [--new-text TEXT | --file F | -] [--preview]
  ./hedit.py delete <file> <anchor> [<end-anchor>] [--preview]
  ./hedit.py batch <file> [--preview]   # JSON edits on stdin: op anchor end_anchor
                   # lines new_text before; --preview prints diff, no write
  ./hedit.py --selftest

Read output format:  LN#HASH|text
Anchors:             LN#HASH   (e.g. 12#NKA)
Stale edits return JSON: {"ok":false,"error":"stale","message":"anchor 12#NKA: stale"}

Python API:
  import hedit
  hedit.read_file("foo.py")                      -> [(ln, anchor, text), ...]
  hedit.replace("foo.py", "12#NKA", ["new line"]) -> {"ok": True, ...}
  hedit.replace("foo.py", "12#NKA", ["new"], preview=True)  # -> {"ok": True, "preview": True, "diff": "..."} (no write)
  hedit.hash_line("def f():", 12)                -> "NKA"
  hedit.batch("foo.py", [{"op": "insert", "anchor": "3#NKA", "lines": ["x"], "before": True}])
"""

import argparse
import difflib
import json
import os
import stat
import sys
from typing import Any, Sequence, TypedDict

HASH_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 32 chars, drops 0/O/1/I
HASH_LEN = 3
MAX_FILE_SIZE = 2 * 1024 * 1024  # refuse to read >2 MiB (like hledit)

# Keys a batch edit may carry; any other key is a validation error, never
# silently ignored. "before" (insert only) mirrors insert(before=...).
_EDIT_KEYS = frozenset({"op", "anchor", "end_anchor", "lines", "new_text", "before"})


class Edit(TypedDict, total=False):
    """One batch edit: op (replace|insert|delete), anchor LN#HASH,
    optional end_anchor and lines/new_text, and before (insert only)."""
    op: str
    anchor: str
    end_anchor: str
    lines: list[str]
    new_text: str
    before: bool



# --------------------------------------------------------------------------
# Hashing
# --------------------------------------------------------------------------

def fnv1a_32(data: bytes) -> int:
    """FNV-1a 32-bit hash of data (deterministic; used for line anchors)."""
    h = 0x811C9DC5
    for b in data:
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def _base32(v, length=HASH_LEN):
    out = []
    for _ in range(length):
        out.append(HASH_ALPHABET[v & 0x1F])
        v >>= 5
    return "".join(reversed(out))


def hash_line(line: str, ln: int) -> str:
    """3-char content hash for a line.

    Trailing whitespace is normalized. Blank / punctuation-only lines mix in
    the line number so identical structural lines get distinct anchors.
    """
    text = line.rstrip("\n").rstrip()
    if not text or all(c in " \t()[]{}.,;:'\"!?-_=+*/\\|&^%$#@~`<>" for c in text):
        text = "%d:%s" % (ln, text)
    return _base32(fnv1a_32(text.encode("utf-8")))


def format_line(ln: int, text: str) -> str:
    """Format a line as LN#HASH|text, hashing the line content at line number ln."""
    return "%d#%s|%s" % (ln, hash_line(text, ln), text.rstrip("\n"))


def parse_anchor(anchor: str) -> tuple[int, str] | None:
    """Parse 'LN#HASH' -> (ln, hash). Returns None if malformed."""
    if not anchor or "#" not in anchor:
        return None
    ln_s, h = anchor.split("#", 1)
    if not ln_s.isdigit() or not (2 <= len(h) <= 3):
        return None
    return int(ln_s), h


# --------------------------------------------------------------------------
# File I/O (atomic writes: .new + os.replace, per sandbox lesson)
# --------------------------------------------------------------------------

def read_lines(path: str) -> tuple[list[str], str]:
    """Read a file into lines (size-capped) and detect its line ending; returns (lines, ending)."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    if len(content) > MAX_FILE_SIZE:
        raise ValueError("file too large (>%d bytes)" % MAX_FILE_SIZE)
    # Keep line endings simple: split on \n, strip trailing \r per line
    lines = content.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # drop trailing empty element from final newline
    return [ln.rstrip("\r") for ln in lines], _detect_ending(content)


def _detect_ending(content):
    return "\r\n" if "\r\n" in content else "\n"

def _unified_diff(path, old_lines, new_lines):
    """Human-visible unified diff between two line lists (stdlib difflib)."""
    a = [l + "\n" for l in old_lines]
    b = [l + "\n" for l in new_lines]
    return "".join(difflib.unified_diff(a, b, fromfile=path, tofile=path))


def write_lines_atomic(path: str, lines: Sequence[str], ending: str = "\n") -> None:
    """Write lines to path atomically (.hedit-new + os.replace), preserving the file mode."""
    text = ending.join(lines)
    if lines:
        text += ending
    tmp = path + ".hedit-new"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    # Preserve the original file's mode (e.g. executable bit on scripts).
    try:
        os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode))
    except OSError:
        pass
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# Read
# --------------------------------------------------------------------------

def read_file(path: str, offset: int | None = None, limit: int | None = None, grep: str | None = None, context: int | None = None, plain: bool = False) -> dict[str, Any]:
    """Read a file with LN#HASH anchors, supporting offset/limit/grep/context windows and plain output."""
    lines, _ = read_lines(path)
    out = []
    total = len(lines)
    start, end = 0, total

    if grep is not None:
        hits = [i for i, ln in enumerate(lines) if grep in ln]
        if context is None:
            context = 0
        if hits:
            start = max(0, hits[0] - context)
            end = min(total, hits[-1] + context + 1)
        else:
            start, end = 0, 0
    else:
        if offset is not None:
            start = max(0, offset - 1)
        if limit is not None:
            end = min(total, start + limit)

    truncated = end < total
    for i in range(start, end):
        ln = i + 1
        text = lines[i]
        out.append((ln, hash_line(text, ln), text))

    if plain:
        payload = [t for (_, _, t) in out]
    else:
        payload = ["%d#%s|%s" % (ln, h, t) for (ln, h, t) in out]
    return {"ok": True, "lines": out, "truncated": truncated,
            "next_offset": end + 1 if truncated else None, "payload": payload,
            "total_lines": total}


# --------------------------------------------------------------------------
# Edit primitives (stale-safe)
# --------------------------------------------------------------------------

def _validate_anchor(lines, anchor):
    """Return (index) if anchor's line+hash matches current content, else None."""
    parsed = parse_anchor(anchor)
    if parsed is None:
        raise ValueError("invalid anchor %r (want LN#HASH)" % anchor)
    ln, h = parsed
    if ln < 1 or ln > len(lines):
        return None
    current = hash_line(lines[ln - 1], ln)
    if current != h:
        return None
    return ln - 1


def replace(path: str, anchor: str, new_lines: str | Sequence[str], end_anchor: str | None = None, preview: bool = False) -> dict[str, Any]:
    """Replace the line(s) at anchor (through end_anchor, inclusive)."""
    lines, ending = read_lines(path)
    idx = _validate_anchor(lines, anchor)
    if idx is None:
        return _stale(anchor)
    end_idx = idx
    if end_anchor is not None:
        e = _validate_anchor(lines, end_anchor)
        if e is None:
            return _stale(end_anchor)
        if e < idx:
            raise ValueError("end_anchor precedes start anchor")
        end_idx = e
    if isinstance(new_lines, str):
        new_lines = [new_lines]
    new_lines = [nl.rstrip("\r\n") for nl in new_lines]
    old = list(lines)
    lines[idx:end_idx + 1] = new_lines
    if preview:
        return {"ok": True, "preview": True,
                "diff": _unified_diff(path, old, lines),
                "first_changed_line": idx + 1,
                "last_changed_line": idx + len(new_lines)}
    write_lines_atomic(path, lines, ending)
    return {"ok": True, "first_changed_line": idx + 1,
            "last_changed_line": idx + len(new_lines)}


def insert(path: str, anchor: str, new_lines: str | Sequence[str], before: bool = False, preview: bool = False) -> dict[str, Any]:
    """Insert new_lines before or after the anchor line; preview returns a diff without writing."""
    lines, ending = read_lines(path)
    idx = _validate_anchor(lines, anchor)
    if idx is None:
        return _stale(anchor)
    if isinstance(new_lines, str):
        new_lines = [new_lines]
    new_lines = [nl.rstrip("\r\n") for nl in new_lines]
    at = idx if before else idx + 1
    old = list(lines)
    lines[at:at] = new_lines
    if preview:
        return {"ok": True, "preview": True,
                "diff": _unified_diff(path, old, lines),
                "first_changed_line": at + 1,
                "last_changed_line": at + len(new_lines)}
    write_lines_atomic(path, lines, ending)
    return {"ok": True, "first_changed_line": at + 1,
            "last_changed_line": at + len(new_lines)}


def delete(path: str, anchor: str, end_anchor: str | None = None, preview: bool = False) -> dict[str, Any]:
    """Delete the line(s) at anchor (through end_anchor, inclusive)."""
    return replace(path, anchor, [], end_anchor=end_anchor, preview=preview)


def _stale(anchor):
    return {"ok": False, "error": "stale",
            "message": "anchor %s: stale" % anchor}


def batch(path: str, edits: Sequence[Edit], preview: bool = False) -> dict[str, Any]:
    """Apply multiple edits atomically. All anchors and edit keys are
    validated first; an unknown key is a validation error, not ignored."""
    lines, ending = read_lines(path)
    prepared = []
    for e in edits:
        unknown = set(e) - _EDIT_KEYS
        if unknown:
            return {"ok": False, "error": "validation",
                    "message": "unknown key(s) %s (want %s)"
                               % (", ".join(sorted(repr(k) for k in unknown)),
                                  "|".join(sorted(_EDIT_KEYS)))}
        op = e.get("op")
        if op not in ("replace", "insert", "delete"):
            return {"ok": False, "error": "validation",
                    "message": "unknown op %r (want replace|insert|delete)" % (op,)}
        before = e.get("before", False)
        if before and op != "insert":
            return {"ok": False, "error": "validation",
                    "message": "'before' is only valid for insert edits"}
        anchor = e.get("anchor")
        if not anchor:
            return {"ok": False, "error": "validation",
                    "message": "edit missing 'anchor'"}
        idx = _validate_anchor(lines, anchor)
        if idx is None:
            return _stale(anchor)
        end_idx = idx
        if e.get("end_anchor"):
            end_idx = _validate_anchor(lines, e["end_anchor"])
            if end_idx is None:
                return _stale(e["end_anchor"])
            if end_idx < idx:
                raise ValueError("end_anchor precedes start anchor")
        if op in ("replace", "insert"):
            raw = e.get("lines", e.get("new_text"))
            if raw is None:
                return {"ok": False, "error": "validation",
                        "message": "%s edit missing 'lines' (or 'new_text')" % op}
            if isinstance(raw, str):
                raw = raw.split("\n")
            new_lines = [nl.rstrip("\r\n") for nl in raw]
        else:
            new_lines = []
        prepared.append((op, idx, end_idx, new_lines, before))

    # Apply in reverse order so earlier indices stay valid.
    old = list(lines)
    changed = []
    for op, idx, end_idx, new_lines, before in sorted(prepared, key=lambda x: x[1], reverse=True):
        if op == "replace":
            lines[idx:end_idx + 1] = new_lines
            changed.append((idx + 1, idx + len(new_lines)))
        elif op == "insert":
            at = idx if before else idx + 1  # 'before' inserts ahead of the anchor
            lines[at:at] = new_lines
            changed.append((at + 1, at + len(new_lines)))
        elif op == "delete":
            lines[idx:end_idx + 1] = []
            changed.append((idx + 1, idx + 1))
        else:
            raise ValueError("unknown op %r" % op)

    if changed:
        first = min(c[0] for c in changed)
        last = max(c[1] for c in changed)
    else:
        first = last = 0
    if preview:
        return {"ok": True, "preview": True,
                "diff": _unified_diff(path, old, lines),
                "first_changed_line": first, "last_changed_line": last,
                "edits_applied": len(prepared)}
    write_lines_atomic(path, lines, ending)
    return {"ok": True, "first_changed_line": first, "last_changed_line": last,
            "edits_applied": len(prepared)}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _read_args(parser, args):
    path = args.file
    try:
        res = read_file(path, offset=args.offset, limit=args.limit,
                        grep=args.grep, context=args.context, plain=args.plain)
    except Exception as e:
        print(json.dumps({"ok": False, "error": "io", "message": str(e)}))
        return 1
    if args.json:
        print(json.dumps({"ok": True, "file": path,
                          "lines": [{"line": ln, "anchor": "%d#%s" % (ln, h), "text": t}
                                    for (ln, h, t) in res["lines"]],
                          "truncated": res["truncated"],
                          "next_offset": res["next_offset"],
                          "total_lines": res["total_lines"]}))
    else:
        for ln in res["payload"]:
            print(ln)
        if res["truncated"]:
            print("# ... truncated (next offset %d)" % res["next_offset"],
                  file=sys.stderr)
    return 0


def _content_from(args):
    new_text = getattr(args, "new_text", None)
    if new_text is not None:
        return new_text.split("\n")
    file_input = getattr(args, "file_input", None)
    if file_input:
        with open(file_input, "r", encoding="utf-8") as f:
            return f.read().split("\n")
    # stdin
    return sys.stdin.read().split("\n")


def _write_cmd(parser, args, op):
    if not args.anchor:
        parser.error("%s requires an <anchor>" % op)
    preview = getattr(args, "preview", False)
    as_json = getattr(args, "json", False)
    try:
        if op == "delete":
            res = delete(args.file, args.anchor, args.end_anchor, preview=preview)
        else:
            new_lines = _content_from(args)
            if op == "replace":
                res = replace(args.file, args.anchor, new_lines, args.end_anchor,
                              preview=preview)
            elif op == "insert":
                res = insert(args.file, args.anchor, new_lines,
                             before=getattr(args, "before", False), preview=preview)
            else:
                parser.error("unknown op")
    except Exception as e:
        print(json.dumps({"ok": False, "error": "io", "message": str(e)}))
        return 2 if isinstance(e, (ValueError, TypeError)) else 1
    if preview:
        if as_json:
            print(json.dumps(res))
        else:
            print(res.get("diff", "") or "(no changes)")
        return 0 if res.get("ok") else 1
    print(json.dumps(res))
    return 0 if res.get("ok") else 1


def _batch_cmd(parser, args):
    try:
        data = json.load(sys.stdin)
        edits = data.get("edits", data) if isinstance(data, dict) else data
        res = batch(args.file, edits, preview=getattr(args, "preview", False))
    except Exception as e:
        print(json.dumps({"ok": False, "error": "io", "message": str(e)}))
        return 1
    if getattr(args, "preview", False):
        if getattr(args, "json", False):
            print(json.dumps(res))
        else:
            print(res.get("diff", "") or "(no changes)")
        return 0 if res.get("ok") else 1
    print(json.dumps(res))
    return 0 if res.get("ok") else 1


def _selftest():
    ok = True
    def check(name, cond):
        nonlocal ok
        print(("PASS" if cond else "FAIL") + "  " + name)
        if not cond:
            ok = False

    # Hash determinism + distinctness
    check("hash deterministic", hash_line("def f():", 1) == hash_line("def f():", 1))
    check("hash distinct lines", hash_line("a = 1", 1) != hash_line("a = 2", 1))
    check("hash len/alphabet", len(hash_line("x", 1)) == 3 and
          all(c in HASH_ALPHABET for c in hash_line("x", 1)))
    check("blank lines mix ln", hash_line("", 5) != hash_line("", 6))
    check("trailing ws normalized", hash_line("foo  ", 1) == hash_line("foo", 1))

    # Anchor parse
    check("parse anchor", parse_anchor("12#NKA") == (12, "NKA"))
    check("parse bad anchor", parse_anchor("abc") is None)

    # Stale-safe replace on a temp file
    import tempfile
    d = tempfile.mkdtemp()
    p = os.path.join(d, "t.txt")

    def _ow(path_, text):
        """Overwrite via .new + os.replace (sandbox denies 'w' on existing)."""
        tmp = path_ + ".t"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path_)

    _ow(p, "one\ntwo\nthree\n")
    res = read_file(p)
    a2 = "%d#%s" % (res["lines"][1][0], res["lines"][1][1])  # line 2 anchor
    r = replace(p, a2, ["TWO"])
    check("replace ok", r.get("ok") is True)
    with open(p) as f:
        check("replace content", f.read() == "one\nTWO\nthree\n")

    # String-arg regression (2026-08-21): plain str new_lines must be ONE
    # line; historically it was iterated char-by-char (silent corruption).
    _ow(p, "one\ntwo\nthree\n")
    res = read_file(p)
    a2 = "%d#%s" % (res["lines"][1][0], res["lines"][1][1])
    replace(p, a2, "TWO-STRING")
    with open(p) as f:
        check("replace str arg = one line", f.read() == "one\nTWO-STRING\nthree\n")
    res = read_file(p)
    a1 = "%d#%s" % (res["lines"][0][0], res["lines"][0][1])
    insert(p, a1, "ZERO-STRING")  # after anchor 1
    with open(p) as f:
        check("insert str arg = one line", f.read() == "one\nZERO-STRING\nTWO-STRING\nthree\n")

    # Stale detection: change file, then old anchor must be rejected
    _ow(p, "one\nCHANGED\nthree\n")
    r = replace(p, a2, ["nope"])
    check("stale rejected", r.get("ok") is False and r.get("error") == "stale")

    # Insert before/after
    _ow(p, "one\ntwo\n")
    res = read_file(p)
    a1 = "%d#%s" % (res["lines"][0][0], res["lines"][0][1])
    insert(p, a1, ["zero"], before=True)
    with open(p) as f:
        check("insert before", f.read() == "zero\none\ntwo\n")

    # Batch atomicity: one stale edit -> nothing applied
    _ow(p, "a\nb\nc\n")
    res = read_file(p)
    a1 = "%d#%s" % (res["lines"][0][0], res["lines"][0][1])
    a3 = "%d#%s" % (res["lines"][2][0], res["lines"][2][1])
    # Corrupt line 3
    _ow(p, "a\nb\nCORRUPT\n")
    r = batch(p, [{"op": "replace", "anchor": a1, "lines": ["A"]},
                  {"op": "replace", "anchor": a3, "lines": ["C"]}])
    check("batch stale aborts all", r.get("error") == "stale")
    with open(p) as f:
        check("batch left file untouched", f.read() == "a\nb\nCORRUPT\n")

    # Atomic write leaves no temp file
    check("no leftover temp", not os.path.exists(p + ".hedit-new"))

    # CLI delete path (regression: delete used to crash on missing new_text)
    _ow(p, "x\ny\nz\n")
    res = read_file(p)
    ay = "%d#%s" % (res["lines"][1][0], res["lines"][1][1])
    fake = argparse.Namespace(file=p, anchor=ay, end_anchor=None,
                              new_text=None, file_input=None)
    check("delete CLI path", _write_cmd(argparse.ArgumentParser(), fake, "delete") == 0)
    with open(p) as f:
        check("delete CLI removed line", f.read() == "x\nz\n")

    # Batch: new_text string alias works
    _ow(p, "a\nb\nc\n")
    res = read_file(p)
    a1 = "%d#%s" % (res["lines"][0][0], res["lines"][0][1])
    r = batch(p, [{"op": "replace", "anchor": a1, "new_text": "A1\nA2"}])
    check("batch new_text alias", r.get("ok") is True)
    with open(p) as f:
        check("batch new_text content", f.read() == "A1\nA2\nb\nc\n")

    # Batch: missing lines on replace -> validation error, NOT silent delete
    _ow(p, "a\nb\nc\n")
    res = read_file(p)
    a1 = "%d#%s" % (res["lines"][0][0], res["lines"][0][1])
    r = batch(p, [{"op": "replace", "anchor": a1}])
    check("batch missing lines rejected",
          r.get("ok") is False and r.get("error") == "validation")
    with open(p) as f:
        check("batch no silent delete", f.read() == "a\nb\nc\n")

    # Batch: unknown op rejected
    r = batch(p, [{"op": "frobnicate", "anchor": a1, "lines": ["Q"]}])
    check("batch unknown op rejected",
          r.get("ok") is False and r.get("error") == "validation")

    # Batch: unknown key rejected (never silently ignored)
    r = batch(p, [{"op": "replace", "anchor": a1, "lines": ["Q"], "befor": True}])
    check("batch unknown key rejected",
          r.get("ok") is False and r.get("error") == "validation")
    with open(p) as f:
        check("batch unknown key wrote nothing", f.read() == "a\nb\nc\n")

    # Batch: insert-before honored
    _ow(p, "a\nb\nc\n")
    res = read_file(p)
    a3 = "%d#%s" % (res["lines"][2][0], res["lines"][2][1])
    r = batch(p, [{"op": "insert", "anchor": a3, "lines": ["before-c"], "before": True}])
    check("batch insert-before ok", r.get("ok") is True)
    with open(p) as f:
        check("batch insert-before content", f.read() == "a\nb\nbefore-c\nc\n")

    # Batch: insert still defaults to after (unchanged behavior)
    _ow(p, "a\nb\nc\n")
    res = read_file(p)
    a1 = "%d#%s" % (res["lines"][0][0], res["lines"][0][1])
    r = batch(p, [{"op": "insert", "anchor": a1, "lines": ["after-a"]}])
    check("batch insert-after default ok", r.get("ok") is True)
    with open(p) as f:
        check("batch insert-after default content", f.read() == "a\nafter-a\nb\nc\n")

    # Batch: 'before' on a non-insert op rejected
    _ow(p, "a\nb\nc\n")
    res = read_file(p)
    a1 = "%d#%s" % (res["lines"][0][0], res["lines"][0][1])
    r = batch(p, [{"op": "delete", "anchor": a1, "before": True}])
    check("batch before on non-insert rejected",
          r.get("ok") is False and r.get("error") == "validation")
    with open(p) as f:
        check("batch before-invalid wrote nothing", f.read() == "a\nb\nc\n")

    # --- patch-preview ---
    _ow(p, "one\ntwo\nthree\n")
    res = read_file(p)
    a2 = "%d#%s" % (res["lines"][1][0], res["lines"][1][1])
    r = replace(p, a2, ["TWO", "2.5"], preview=True)
    check("preview replace ok", r.get("ok") is True and r.get("preview") is True)
    check("preview replace diff -two", "-two\n" in r.get("diff", ""))
    check("preview replace diff +TWO", "+TWO\n" in r.get("diff", ""))
    with open(p) as f:
        check("preview replace wrote nothing", f.read() == "one\ntwo\nthree\n")

    _ow(p, "one\ntwo\n")
    res = read_file(p)
    a1 = "%d#%s" % (res["lines"][0][0], res["lines"][0][1])
    r = insert(p, a1, ["zero"], before=True, preview=True)
    check("preview insert ok", r.get("ok") is True)
    check("preview insert diff +zero", "+zero\n" in r.get("diff", ""))
    with open(p) as f:
        check("preview insert wrote nothing", f.read() == "one\ntwo\n")

    _ow(p, "one\ntwo\nthree\n")
    res = read_file(p)
    a2 = "%d#%s" % (res["lines"][1][0], res["lines"][1][1])
    r = delete(p, a2, preview=True)
    check("preview delete ok", r.get("ok") is True)
    check("preview delete diff -two", "-two\n" in r.get("diff", ""))
    with open(p) as f:
        check("preview delete wrote nothing", f.read() == "one\ntwo\nthree\n")

    _ow(p, "a\nb\nc\n")
    res = read_file(p)
    a1 = "%d#%s" % (res["lines"][0][0], res["lines"][0][1])
    a3 = "%d#%s" % (res["lines"][2][0], res["lines"][2][1])
    r = batch(p, [{"op": "replace", "anchor": a1, "lines": ["A"]},
                  {"op": "insert", "anchor": a3, "lines": ["d"]}], preview=True)
    check("preview batch ok", r.get("ok") is True and r.get("edits_applied") == 2)
    check("preview batch diff +A", "+A\n" in r.get("diff", ""))
    check("preview batch diff +d", "+d\n" in r.get("diff", ""))
    with open(p) as f:
        check("preview batch wrote nothing", f.read() == "a\nb\nc\n")

    _ow(p, "a\nb\nc\n")
    res = read_file(p)
    a1 = "%d#%s" % (res["lines"][0][0], res["lines"][0][1])
    _ow(p, "X\nb\nc\n")
    r = batch(p, [{"op": "replace", "anchor": a1, "lines": ["A"]}], preview=True)
    check("preview stale rejected", r.get("ok") is False and r.get("error") == "stale")
    with open(p) as f:
        check("preview stale wrote nothing", f.read() == "X\nb\nc\n")

    # CLI preview path (regression guard for _write_cmd wiring)
    _ow(p, "one\ntwo\n")
    res = read_file(p)
    a2 = "%d#%s" % (res["lines"][1][0], res["lines"][1][1])
    fake = argparse.Namespace(file=p, anchor=a2, end_anchor=None, new_text="TWO",
                              file_input=None, before=False, preview=True, json=False)
    check("CLI preview rc", _write_cmd(argparse.ArgumentParser(), fake, "replace") == 0)
    with open(p) as f:
        check("CLI preview wrote nothing", f.read() == "one\ntwo\n")

    print("\n%s" % ("ALL TESTS PASSED" if ok else "SOME TESTS FAILED"))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hedit.py",
        description="Hash-anchored line editor for AI agents (stale-safe edits).",
        epilog=("Examples:\n"
                "  ./hedit.py read foo.py\n"
                "  ./hedit.py read foo.py --grep def --context 2 --json\n"
                "  printf 'new line\\n' | ./hedit.py replace foo.py 12#NKA -\n"
                "  ./hedit.py replace foo.py 12#NKA 18#VRC --new-text 'a\\nb\\n'\n"
                "  ./hedit.py insert foo.py 1#QVE --before --file header.txt\n"
                "  ./hedit.py delete foo.py 12#NKA\n"
                "  echo '{\"edits\":[...]}' | ./hedit.py batch foo.py\n"
                "  echo '{\"edits\":[...]}' | ./hedit.py batch foo.py --preview   # show diff, no write\n"
                "  ./hedit.py --selftest"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd")

    p_read = sub.add_parser("read", help="read a file with LN#HASH anchors")
    p_read.add_argument("file")
    p_read.add_argument("--offset", type=int)
    p_read.add_argument("--limit", type=int)
    p_read.add_argument("--grep")
    p_read.add_argument("--context", type=int)
    p_read.add_argument("--plain", action="store_true", help="no anchors")
    p_read.add_argument("--json", action="store_true")

    p_repl = sub.add_parser("replace", help="replace line(s) at anchor")
    p_repl.add_argument("file")
    p_repl.add_argument("anchor")
    p_repl.add_argument("end_anchor", nargs="?")
    p_repl.add_argument("--new-text")
    p_repl.add_argument("--file", dest="file_input")
    p_repl.add_argument("--preview", "--dry-run", action="store_true",
                        help="print diff without writing")
    p_repl.add_argument("--json", action="store_true", help="JSON output (with --preview)")
    p_repl.add_argument("content", nargs="?", help="'-' to read from stdin")

    p_ins = sub.add_parser("insert", help="insert lines before/after anchor")
    p_ins.add_argument("file")
    p_ins.add_argument("anchor")
    p_ins.add_argument("--before", action="store_true")
    p_ins.add_argument("--after", action="store_true")
    p_ins.add_argument("--new-text")
    p_ins.add_argument("--file", dest="file_input")
    p_ins.add_argument("--preview", "--dry-run", action="store_true",
                       help="print diff without writing")
    p_ins.add_argument("--json", action="store_true", help="JSON output (with --preview)")
    p_ins.add_argument("content", nargs="?")

    p_del = sub.add_parser("delete", help="delete line(s) at anchor")
    p_del.add_argument("file")
    p_del.add_argument("anchor")
    p_del.add_argument("end_anchor", nargs="?")
    p_del.add_argument("--preview", "--dry-run", action="store_true",
                       help="print diff without writing")
    p_del.add_argument("--json", action="store_true", help="JSON output (with --preview)")

    p_batch = sub.add_parser("batch", help="apply JSON edits from stdin atomically")
    p_batch.add_argument("file")
    p_batch.add_argument("--preview", "--dry-run", action="store_true",
                         help="print diff without writing")
    p_batch.add_argument("--json", action="store_true", help="JSON output (with --preview)")

    parser.add_argument("--selftest", action="store_true", help="run self tests")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()
    if not args.cmd:
        parser.print_help()
        return 2

    if args.cmd == "read":
        return _read_args(parser, args)
    if args.cmd == "replace":
        return _write_cmd(parser, args, "replace")
    if args.cmd == "insert":
        return _write_cmd(parser, args, "insert")
    if args.cmd == "delete":
        return _write_cmd(parser, args, "delete")
    if args.cmd == "batch":
        return _batch_cmd(parser, args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
