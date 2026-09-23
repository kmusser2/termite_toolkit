#!/usr/bin/env python3
# TYPE: TOOLKIT
# keywords: |symbol|outline|map|ast|function|class|method|signature|anchor|python|parse|structure|lines|scope|
# examples: |import symmap; symmap.parse("service.py")|import symmap; symmap.parse_source("def f(x): return x", "x.py")|import symmap; symmap.register_backend("go", backend_fn, [".go"])|
"""symmap.py - Symbol outline map for source files.

Python API (importable from any python_eval call):
  import symmap
  r = symmap.parse("service.py")   # ParseResult: every function/class/method
                                   # with qualified name, exact signature,
                                   # line range, and a hedit anchor for the
                                   # first line of each symbol.
  r = symmap.parse_source("def f(x): return x", "x.py")   # in-memory

Language backends plug in behind one schema; only the Python backend
(ast, stdlib-only) ships today.  Adding e.g. C++ later means registering
another backend (register_backend), never touching the consumer.

CLI:
  ./symmap.py FILE [--lang LANG] [--json] [--selftest]
"""
import argparse
import ast
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable, Sequence
from typing import Any, TypedDict

# --- sibling tools live in this directory (hedit supplies anchors) ----------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hedit  # noqa: E402


class Symbol(TypedDict):
    """One function/class/method entry in an outline."""
    name: str
    qualified_name: str
    kind: str                 # "class" | "function" | "method"
    is_async: bool
    start: int                # 1-based line of the def/class keyword
    end: int                  # 1-based last line of the body, inclusive
    signature: str            # exact first line, e.g. "async def fetch(self, url, timeout=5):"
    decorators: list[str]
    anchor: str               # "LN#HASH" of the start line (hedit-ready)


class ParseResult(TypedDict):
    """Outcome of parse() / parse_source()."""
    ok: bool
    path: str
    lang: str | None
    total_lines: int
    symbols: list[Symbol]
    error: str | None         # "syntax" | "unsupported_language" | "io" | "parse" | None
    message: str | None


# Backend contract: (source_text, path) -> symbols.  A future C++ backend
# (e.g. clangd documentSymbol via LSP) implements exactly this and registers.
Backend = Callable[[str, str], list[Symbol]]

_BACKENDS: dict[str, Backend] = {}
_EXT_LANG: dict[str, str] = {}


def register_backend(lang: str, backend: Backend, extensions: Sequence[str]) -> None:
    """Register a language backend (source,path)->symbols plus its file extensions."""
    _BACKENDS[lang] = backend
    for ext in extensions:
        _EXT_LANG[ext.lower()] = lang


def detect_lang(path: str) -> str | None:
    """Infer the language from the file extension (registered backends only)."""
    return _EXT_LANG.get(os.path.splitext(path)[1].lower())


# ---------------------------------------------------------------------------
# Python backend (stdlib ast — zero deps)
# ---------------------------------------------------------------------------

def _anchor(lines: Sequence[str], ln: int) -> str:
    text = lines[ln - 1] if 0 < ln <= len(lines) else ""
    return "%d#%s" % (ln, hedit.hash_line(text, ln))


def _mk(node: Any, qn: str, kind: str, sig: str, lines: Sequence[str], is_async: bool) -> Symbol:
    return {
        "name": node.name, "qualified_name": qn, "kind": kind, "is_async": is_async,
        "start": node.lineno, "end": node.end_lineno, "signature": sig,
        "decorators": [ast.unparse(d) for d in node.decorator_list],
        "anchor": _anchor(lines, node.lineno),
    }


def _py_backend(source: str, path: str) -> list[Symbol]:
    """Outline a Python file via ast: classes, methods, functions, nested included."""
    tree = ast.parse(source)
    lines = source.split("\n")
    out: list[Symbol] = []

    def walk(node: Any, qual: str, in_class: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                bases = [ast.unparse(b) for b in child.bases]
                sig = ("class %s(%s):" % (child.name, ", ".join(bases))
                       if bases else "class %s:" % child.name)
                qn = qual + child.name
                out.append(_mk(child, qn, "class", sig, lines, is_async=False))
                walk(child, qn + ".", True)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "method" if in_class else "function"
                qn = qual + child.name
                seg = ast.get_source_segment(source, child) or ""
                # first non-decorator line is the exact def/async def line
                first = next((ln for ln in seg.split("\n")
                              if ln.strip() and not ln.lstrip().startswith("@")), "")
                out.append(_mk(child, qn, kind, first.strip(), lines,
                               is_async=isinstance(child, ast.AsyncFunctionDef)))
                walk(child, qn + ".", False)
            else:
                walk(child, qual, in_class)

    walk(tree, "", False)
    out.sort(key=lambda s: s["start"])
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_source(source: str, path: str = "<string>", lang: str | None = None) -> ParseResult:
    """Outline an in-memory source string; path supplies the language via extension."""
    if lang is None:
        lang = detect_lang(path)
    total = source.count("\n") + 1
    if lang not in _BACKENDS:
        return {"ok": False, "path": path, "lang": lang, "total_lines": total,
                "symbols": [], "error": "unsupported_language",
                "message": "no backend for %r (have: %s)" % (
                    lang, ", ".join(sorted(_BACKENDS)) or "none")}
    try:
        symbols = _BACKENDS[lang](source, path)
    except SyntaxError as e:
        return {"ok": False, "path": path, "lang": lang, "total_lines": total,
                "symbols": [], "error": "syntax",
                "message": "line %s: %s" % (e.lineno or "?", e.msg)}
    except Exception as e:  # backend-specific failures stay local
        return {"ok": False, "path": path, "lang": lang, "total_lines": total,
                "symbols": [], "error": "parse", "message": str(e)}
    return {"ok": True, "path": path, "lang": lang, "total_lines": total,
            "symbols": symbols, "error": None, "message": None}


def parse(path: str, lang: str | None = None) -> ParseResult:
    """Outline a source file on disk; returns symbols plus hedit anchors."""
    if not os.path.isfile(path):
        return {"ok": False, "path": path, "lang": lang, "total_lines": 0,
                "symbols": [], "error": "io", "message": "no such file: %s" % path}
    try:
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
    except (OSError, UnicodeDecodeError) as e:
        return {"ok": False, "path": path, "lang": lang, "total_lines": 0,
                "symbols": [], "error": "io", "message": str(e)}
    return parse_source(source, path, lang)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_table(r: ParseResult) -> None:
    rows = [(s["start"], s["end"], s["kind"], s["qualified_name"],
             s["anchor"], s["signature"]) for s in r["symbols"]]
    if not rows:
        print("(no symbols)")
        return
    w_kind = max([len(x[2]) for x in rows]) + 1
    w_name = max([len(x[3]) for x in rows]) + 1
    w_anch = max([len(x[4]) for x in rows]) + 1
    for start, end, kind, name, anchor, sig in rows:
        print("L%3d-%-3d %-*s %-*s %-*s %s" % (
            start, end, w_kind, kind, w_name, name, w_anch, anchor, sig))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: ./symmap.py FILE [--lang L] [--json] [--selftest]."""
    p = argparse.ArgumentParser(description="Symbol outline for source files.")
    p.add_argument("path", nargs="?", help="source file to outline")
    p.add_argument("--lang", default=None, help="language override (default: detect from extension)")
    p.add_argument("--json", action="store_true", help="emit JSON (ParseResult)")
    p.add_argument("--selftest", action="store_true", help="run self tests")
    a = p.parse_args(argv)
    if a.selftest:
        return _selftest()
    if not a.path:
        p.error("path required")
    r = parse(a.path, a.lang)
    if not r["ok"]:
        print("symmap: %s: %s" % (r["error"], r["message"]), file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps(r, indent=2, default=str))
        return 0
    _print_table(r)
    return 0


# ---------------------------------------------------------------------------
# Self tests
# ---------------------------------------------------------------------------

def _selftest() -> int:
    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        ok = ok and cond
        print(("PASS" if cond else "FAIL") + "  " + name)

    src_lines = [
        "import os", "", "",
        "def _helper(x):", "    return x + 1", "", "",
        "@cache", "class Service:",
        "    def __init__(self, name, **kw):", "        self.name = name", "",
        "    async def fetch(self, url, timeout=5):",
        "        return await http_get(url, timeout)", "",
        "    def _private(self):",
        "        def inner(y):", "            return y * 2",
        "        return inner(3)", "", "",
        "def main():", '    s = Service("svc")', "    print(s.name)", "",
    ]
    src = "\n".join(src_lines)

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(src)
        tmp = f.name
    try:
        r = parse(tmp)
        check("parse ok", r["ok"])
        check("lang detected as python", r["lang"] == "python")
        check("total_lines == 25", r["total_lines"] == 25)
        names = {s["qualified_name"] for s in r["symbols"]}
        expect = {"_helper", "Service", "Service.__init__", "Service.fetch",
                  "Service._private", "Service._private.inner", "main"}
        check("all qualified names (nested + class-qualified)", names == expect)
        fetch = next(s for s in r["symbols"] if s["qualified_name"] == "Service.fetch")
        check("fetch is async method", fetch["is_async"] and fetch["kind"] == "method")
        check("exact signature w/ default", fetch["signature"] == "async def fetch(self, url, timeout=5):")
        check("line range 13-14", (fetch["start"], fetch["end"]) == (13, 14))
        check("anchor format LN#XXX", bool(re.match(r"^\d+#[A-Za-z0-9]{3}$", fetch["anchor"])))
        view = hedit.read_file(tmp, grep="async def fetch", context=0)
        check("anchor agrees with hedit", any(fetch["anchor"] in ln for ln in view["payload"]))
        svc = next(s for s in r["symbols"] if s["qualified_name"] == "Service")
        check("class signature + decorator", svc["signature"] == "class Service:" and svc["decorators"] == ["cache"])
        bad = parse_source("def broken(:\n", "x.py")
        check("syntax error reported", not bad["ok"] and bad["error"] == "syntax")
        nos = parse_source("int main() {}", "x.cpp")
        check("unsupported language reported", not nos["ok"] and nos["error"] == "unsupported_language")
        missing = parse("/no/such/file.py")
        check("missing file reported", not missing["ok"] and missing["error"] == "io")
    finally:
        os.unlink(tmp)
    print("\n%s" % ("ALL TESTS PASSED" if ok else "SOME TESTS FAILED"))
    return 0 if ok else 1


register_backend("python", _py_backend, [".py", ".pyw"])


if __name__ == "__main__":
    sys.exit(main())
