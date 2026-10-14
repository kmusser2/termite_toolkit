#!/usr/bin/env python3
# TYPE: TOOLKIT
# keywords: |clangd|c++|cpp|lsp|language-server|diagnostics|errors|warnings|hover|definition|references|go-to-definition|find-references|compile_commands|compile-db|clang|semantic|navigation|index|code-intelligence|static-analysis|supervisor|session|cache|json|selftest|usages|find-usages|who-calls|callers|call-sites|call-hierarchy|used-by|rename|refactor|symbol|type-of|signature|impact|unused|dead-code|where-defined|
# examples: |import clangd; clangd.diagnostics("/path/to/proj", "src/a.cpp")|import clangd; clangd.hover("/path/to/proj", "src/a.cpp", 42, 7)|./clangd.py --root DIR check src/a.cpp|
"""clangd.py - C++ semantics: find usages, definitions, types, diagnostics.

clangd is the C++ language server.  It reads a project's compile_commands.json
(the build description CMake/ninja/make can emit) and parses each source file
with the real compiler flags, so a query answers with genuine name lookup,
types and overload resolution instead of a text guess.  These functions drive
one long-lived clangd per project and hand its results back as plain Python
data; they never run a build and never write into the project.

Getting started:
Every call takes the project `root` first, then a file `path` (absolute, or
relative to `root`).  clangd needs the project's compile database, so call
compile_db_health(root) once first: the other verbs raise ClangdError when it
is missing or stale.

    import clangd
    root = "/path/to/project"
    clangd.compile_db_health(root)          # is the build database usable?
    clangd.check(root, "src/a.cpp")         # CheckSummary: counts + `clean`
    clangd.diagnostics(root, "src/a.cpp")   # list[Diagnostic] (empty = clean)
    clangd.hover(root, "src/a.cpp", 42, 7)  # symbol at line 42, column 7
    clangd.definition(root, "src/a.cpp", 42, 7)
    clangd.references(root, "src/a.cpp", 42, 7)
    clangd.status(); clangd.close(root)

Line and column are 1-based everywhere in this API (clangd/LSP is 0-based; the
conversion is internal).  A query reflects the file's text on disk at call
time, not an editor buffer.

Functions:
Each also accepts a final optional `timeout` (seconds, default 90) and raises
ClangdError if clangd does not answer in time.

  available() -> bool
      True when a clangd binary is on PATH; false means none of this can work.
  compile_db_health(root, db=None) -> DbHealth
      Whether `root` has a usable compile_commands.json.  `db` names a
      specific database file instead of searching the usual places
      (build/compile_commands.json, then compile_commands.json).
  diagnostics(root, path, min_severity="hint") -> list[Diagnostic]
      clangd's errors/warnings for `path`, at `min_severity` or above.
  check(root, path) -> CheckSummary
      The same, reduced to per-severity counts plus `clean` (True when there
      are no errors -- warnings and below do not count).
  hover(root, path, line, col) -> Hover | None
      Type, signature and doc comment of the symbol at that position.
  definition(root, path, line, col) -> list[Location]
      Where that symbol is defined (a list: overloads, redeclarations).
  references(root, path, line, col, include_decl=False) -> list[Location]
      Every use of the symbol project-wide; include_decl also lists the
      declaration site.
  status() -> SupervisorStatus
      What clangd work is currently warm.
  close(root=None) -> int
      Stop the clangd for `root` (every one when None); returns how many.

Return shapes (all line/col values are 1-based):
A function's own top-level shape is also shown with its signature; nested
shapes are spelled out here.

  Diagnostic       = {file, line, col, end_line, end_col, severity, code,
                      message}; severity is one of error/warning/info/hint.
  Location         = {file, line, col, end_line, end_col}
  Hover            = {signature, doc}
  CheckSummary     = {file, errors, warnings, infos, hints, total, clean}
  DbHealth         = {root, db_path, present, fresh, entries, missing, hint}
  SupervisorStatus = {running, supervisor_pid, clangd_version, socket, scratch,
                      idle_timeout, max_instances, instances}
  InstanceInfo     = {root, clangd_pid, clangd_version, idle_seconds, open_files,
                      rss_mib}

Errors:
ClangdError means the question could not be put to clangd: no clangd on PATH,
a clangd older than the protocol this speaks (>= 12), a handshake or protocol
mismatch, no usable compile database (see compile_db_health), an unreadable
file, or clangd silent past `timeout`.  It is durable: a clangd that crashes
or goes silent is thrown away and the call retried once on a fresh one, and if
the supervisor itself dies it is restarted and the call retried -- so a single
process death is invisible.  Trust a ClangdError only when it survives that
retry; the message names the clangd, its version and its last words.

Staying fast, and keeping memory in check:
The first call starts a detached "supervisor" process (one per sandbox uid)
that owns one clangd per project and keeps it warm, so later calls reuse the
parser and the index.  clangd's index, preamble caches, HOME and TMPDIR live
under $TERMITE_CLANGD_DIR or $TMPDIR/termite-clangd/uid-<uid>/<hash of root>/;
nothing is written into the project.  A warm clangd costs hundreds of MiB, so
instances are capped, evicted by least-recent use, and closed after an idle
timeout; status() shows what is warm and close(root) frees one immediately.

CLI (global flags --root/--json/--all come *before* the verb):
  ./clangd.py --root DIR db-health
  ./clangd.py --root DIR check FILE [FILE...]        (exit 1 if any has errors)
  ./clangd.py [--json] --root DIR diagnostics FILE [FILE...] [--min-severity S]
  ./clangd.py --root DIR hover|definition|references FILE:LINE:COL
  ./clangd.py status
  ./clangd.py --root DIR close       (every root: ./clangd.py --all close)
  ./clangd.py --selftest

Environment knobs (all optional):
  TERMITE_CLANGD_DIR       override the scratch root (default: $TMPDIR)
  TERMITE_CLANGD_IDLE      seconds a clangd may sit unused (600)
  TERMITE_CLANGD_MAX       max simultaneous warm clangd instances (3)
  TERMITE_CLANGD_OPEN_TUS  max files kept open in one clangd (2)
  TERMITE_CLANGD_SETTLE    quiet seconds before diagnostics are read (0.4)
  TERMITE_CLANGD_TIMEOUT   per-request timeout in seconds (90)
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import pathlib
import re
import queue
import select
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections import OrderedDict, deque
from enum import StrEnum
from typing import TypedDict
from urllib.parse import quote, unquote, urlparse

__all__ = ["ClangdError", "Diagnostic", "Hover", "Location", "CheckSummary",
           "DbHealth", "InstanceInfo", "SupervisorStatus", "Severity",
           "available", "check", "close", "compile_db_health", "definition",
           "diagnostics", "hover", "references", "status", "main"]

# Defaults; every one can be overridden through the environment (see above).
DEFAULT_IDLE = 600.0
DEFAULT_MAX = 3
DEFAULT_OPEN_TUS = 2
DEFAULT_SETTLE = 0.4
DEFAULT_TIMEOUT = 90.0
_MIN_CLANGD_MAJOR = 12   # oldest clangd whose LSP this tool understands

class Severity(StrEnum):
    """A diagnostic severity; the value is the public/wire form."""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    HINT = "hint"


# rank 1 is the most severe (LSP's numbering); the dict keys are the set members
_SEVERITY_RANK = {Severity.ERROR: 1, Severity.WARNING: 2, Severity.INFO: 3, Severity.HINT: 4}
_SEVERITY_NAME = {1: Severity.ERROR, 2: Severity.WARNING, 3: Severity.INFO, 4: Severity.HINT}


class ClangdError(RuntimeError):
    """Raised when clangd cannot answer: no clangd, an unsupported clangd
    version, a missing or stale database, a socket it does not own, clangd
    crashing, or a request that timed out.

    An instance may carry `retryable = True`: that marks a failure the
    supervisor can plausibly fix by throwing the clangd away and starting a
    fresh one (a crash, a broken pipe, a request that went unanswered).  The
    caller only ever sees the final, already-retried error."""


class _SupervisorGoneError(ClangdError):
    """The supervisor socket died mid-request; the client restarts it and retries."""


def _unavailable(message: str) -> ClangdError:
    """A ClangdError the supervisor should retry on a fresh clangd."""
    error = ClangdError(message)
    error.retryable = True
    return error


class Diagnostic(TypedDict):
    """One compiler diagnostic, positions 1-based."""
    file: str
    line: int
    col: int
    end_line: int
    end_col: int
    severity: Severity
    code: str            # clangd diagnostic code, "" when absent
    message: str         # first line only


class Location(TypedDict):
    """A source range, positions 1-based."""
    file: str
    line: int
    col: int
    end_line: int
    end_col: int


class Hover(TypedDict):
    """What clangd knows about the symbol at a position."""
    signature: str       # the declaration as written, e.g. "int add(int a, int b)"
    doc: str             # documentation comment, "" when none


class CheckSummary(TypedDict):
    """Severity counts for one file, plus a convenience flag."""
    file: str
    errors: int
    warnings: int
    infos: int
    hints: int
    total: int
    clean: bool


class DbHealth(TypedDict):
    """Health of a project's compile_commands.json."""
    root: str
    db_path: str | None
    present: bool
    fresh: bool          # every directory / -I path in it exists on disk
    entries: int
    missing: list[str]   # sample of paths that no longer exist
    hint: str            # what to do about it


class InstanceInfo(TypedDict):
    """One live clangd instance owned by the supervisor."""
    root: str
    clangd_pid: int
    clangd_version: str
    idle_seconds: float
    open_files: int
    rss_mib: float


class SupervisorStatus(TypedDict):
    """The supervisor process and the clangd instances it owns."""
    running: bool
    supervisor_pid: int
    clangd_version: str
    socket: str
    scratch: str
    idle_timeout: float
    max_instances: int
    instances: list[InstanceInfo]


# ---------------------------------------------------------------------------
#  Scratch layout: $TMPDIR/termite-clangd/uid-<uid>/<sha1(root)>/{compdb,tmp,home}
#  Everything clangd can write lives here, so the project is never touched.
# ---------------------------------------------------------------------------
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _scratch_root() -> str:
    explicit = os.environ.get("TERMITE_CLANGD_DIR")
    if explicit:
        return explicit
    return os.path.join(os.environ.get("TMPDIR", "/tmp"), "termite-clangd")


def _uid_dir() -> str:
    """This uid's private tree inside the session-shared scratch root.

    The root is shared by every uid using the session scratch, so it stays
    01777: each uid creates its own uid-<uid> tree inside (0700, one owner) and
    the sticky bit keeps one uid from deleting another's. Everything below the
    uid dir -- caches, socket, pid, log -- is that uid's alone."""
    root = _scratch_root()
    try:
        os.mkdir(root, 0o1777)
    except FileExistsError:
        pass
    try:
        os.chmod(root, 0o1777)          # defeats umask; heals a root this uid made earlier
    except OSError:
        pass                            # another uid's root; _mkdir_private explains if it blocks
    return os.path.join(root, "uid-%d" % os.getuid())


def _supervisor_paths() -> dict:
    d = _uid_dir()
    return {"dir": d,
            "sock": os.path.join(d, "supervisor.sock"),
            "pid": os.path.join(d, "supervisor.pid"),
            "log": os.path.join(d, "supervisor.log")}


def _project_paths(root: str) -> dict:
    key = hashlib.sha1(os.path.realpath(root).encode()).hexdigest()[:12]
    d = os.path.join(_uid_dir(), key)
    return {"dir": d, "compdb": os.path.join(d, "compdb"),
            "tmp": os.path.join(d, "tmp"), "home": os.path.join(d, "home")}


def _mkdir_private(path: str) -> None:
    """Create path and any missing parents with mode 0700."""
    if os.path.isdir(path):
        return
    parts, cur = [], os.path.abspath(path)
    while not os.path.isdir(cur):
        parts.append(cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    for p in reversed(parts):
        try:
            os.mkdir(p, 0o700)
        except FileExistsError:
            pass
        except PermissionError as exc:
            raise ClangdError("cannot create %s: %s -- a scratch root made by "
                              "another uid may be blocking it" % (p, exc))


def _to_uri(path: str) -> str:
    return pathlib.Path(os.path.realpath(path)).as_uri()


def _from_uri(uri: str) -> str:
    return unquote(urlparse(uri).path) if uri else ""


def _language_id(path: str) -> str:
    return "c" if path.endswith((".c", ".h")) else "cpp"


def _request_timeout() -> float:
    return _env_float("TERMITE_CLANGD_TIMEOUT", DEFAULT_TIMEOUT)


def _parse_clangd_version(text: str) -> "dict[str, object]":
    """Pull (major, minor, patch, raw) out of `clangd --version` output.

    Tolerates the several shapes distros use ("clangd version 18.1.3",
    "Debian clangd version 16.0.6 ...", a bare "18.1.3").  `major` is None when
    nothing version-like is found -- the caller then proceeds rather than
    guessing."""
    raw = (text or "").strip().splitlines()[0].strip() if (text or "").strip() else ""
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    if not match:
        return {"raw": raw, "major": None, "minor": None, "patch": None,
                "binary": None}
    return {"raw": raw, "major": int(match.group(1)), "minor": int(match.group(2)),
            "patch": int(match.group(3)), "binary": None}


def _clangd_version() -> "dict[str, object]":
    """Version of the clangd on PATH.  Never raises: an unreadable or
    unparseable version comes back with major=None so the caller can carry on.

    ponytail: run per call (each _Lsp start and one CLI report) instead of a
    module cache; memoize in an instance if profiling ever shows clangd
    --version on a hot path."""
    binary = shutil.which("clangd")
    if binary is None:
        return {"raw": "", "major": None, "minor": None, "patch": None, "binary": None}
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True,
                             timeout=10.0)
        info = _parse_clangd_version((out.stdout or "") + (out.stderr or ""))
    except Exception:                                         # noqa: BLE001 - never fatal
        info = {"raw": "", "major": None, "minor": None, "patch": None, "binary": None}
    info = dict(info)
    info["binary"] = binary
    return info


def _require_supported_clangd() -> "dict[str, object]":
    """The cached version, or a clear ClangdError when clangd is absent or too old."""
    info = _clangd_version()
    if info["binary"] is None:
        raise ClangdError("no clangd found on PATH: install clangd (the C++ language "
                          "server) to answer C++ questions about a project")
    major = info["major"]
    if major is not None and major < _MIN_CLANGD_MAJOR:
        raise ClangdError("%s is too old: this tool speaks the language-server protocol "
                          "of clangd %d and later, so its answers would be unreliable.  "
                          "Point PATH at a newer clangd."
                          % (info["raw"] or "the clangd on PATH", _MIN_CLANGD_MAJOR))
    return info


# ---------------------------------------------------------------------------
#  Compile database discovery and health
# ---------------------------------------------------------------------------
def _find_db(root: str, db: str | None = None) -> str | None:
    if db:
        return db if os.path.exists(db) else None
    for rel in ("build/compile_commands.json", "compile_commands.json"):
        p = os.path.join(root, rel)
        if os.path.exists(p):
            return p
    for pat in ("out/*/compile_commands.json", "cmake-build-*/compile_commands.json",
                "build/*/compile_commands.json"):
        hits = sorted(glob.glob(os.path.join(root, pat)))
        if hits:
            return hits[0]
    return None


def _entry_include_dirs(entry: dict) -> list[str]:
    """Absolute -I/-isystem directories named by one compile database entry."""
    words = entry.get("arguments")
    if not words:
        words = (entry.get("command") or "").split()
    out = []
    for i, w in enumerate(words):
        if w in ("-I", "-isystem") and i + 1 < len(words):
            out.append(words[i + 1])
        elif w.startswith("-I") and len(w) > 2:
            out.append(w[2:])
        elif w.startswith("-isystem") and len(w) > 8:
            out.append(w[8:])
    base = entry.get("directory") or ""
    return [d if os.path.isabs(d) else os.path.join(base, d) for d in out]


def compile_db_health(root: str, db: str | None = None) -> DbHealth:
    """Check that the C++ project at `root` has a `compile_commands.json` -- the build description clangd needs -- that still points at real paths.

    `present` is False when no database is found under `root` (usually `build/compile_commands.json` or `compile_commands.json`); `fresh` is False when the directories and -I include paths it names no longer exist -- the symptom of a project that was moved or a build tree that was deleted. clangd answers such a database with guessed flags and a flood of phantom 'file not found' errors, so the other verbs refuse to query instead of returning nonsense. `missing` lists the paths that were not found and `hint` says how to regenerate the database. Call this first when another verb raises ClangdError, and pass `db` to name a specific database file.
    """
    root = os.path.realpath(root)
    found = _find_db(root, db)
    if not found:
        return DbHealth(root=root, db_path=None, present=False, fresh=False, entries=0, missing=[],
                        hint="no compile_commands.json under %s: configure with "
                             "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON (CMake) or add compile_flags.txt" % root)
    try:
        data = json.load(open(found))
        if not isinstance(data, list):
            raise ValueError("top level is not a list")
    except Exception as exc:                                  # noqa: BLE001 - report, never raise
        return DbHealth(root=root, db_path=found, present=True, fresh=False, entries=0, missing=[],
                        hint="%s is unreadable: %s" % (found, exc))
    missing: list[str] = []
    for entry in data[:400]:
        if not isinstance(entry, dict):
            continue
        directory = entry.get("directory")
        if directory and not os.path.isdir(directory) and directory not in missing:
            missing.append(directory)
        for inc in _entry_include_dirs(entry):
            if not os.path.isdir(inc) and inc not in missing:
                missing.append(inc)
        if len(missing) >= 20:
            break
    if not data:
        hint = "%s has no entries" % found
    elif missing:
        hint = "%s looks stale (%d path(s) it names do not exist, e.g. %s): regenerate it, " \
               "e.g. ./build.sh cleanall icf test" % (found, len(missing), missing[0])
    else:
        hint = "ok"
    return DbHealth(root=root, db_path=found, present=True, fresh=bool(data) and not missing,
                    entries=len(data), missing=missing, hint=hint)


def _require_db(root: str) -> str:
    health = compile_db_health(root)
    if not health["present"]:
        raise ClangdError(health["hint"])
    if not health["fresh"]:
        raise ClangdError(health["hint"])
    return health["db_path"] or ""


# ---------------------------------------------------------------------------
#  The clangd child: one per project root, inside the supervisor
# ---------------------------------------------------------------------------
def _pos(line: int, col: int) -> dict:
    if line < 1 or col < 1:
        raise ClangdError("line and column are 1-based, got %d:%d" % (line, col))
    return {"line": line - 1, "character": col - 1}


def _rng(rng: dict) -> dict:
    start, end = rng.get("start") or {}, rng.get("end") or {}
    return {"line": int(start.get("line", 0)) + 1, "col": int(start.get("character", 0)) + 1,
            "end_line": int(end.get("line", 0)) + 1, "end_col": int(end.get("character", 0)) + 1}


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _markup_text(contents) -> str:
    if isinstance(contents, str):
        return contents
    if isinstance(contents, dict):
        return contents.get("value") or ""
    if isinstance(contents, list):
        return "\n".join(_markup_text(c) for c in contents)
    return ""


# ---------------------------------------------------------------------------
#  The command channel: one owner thread per worker, no locks anywhere.
# ---------------------------------------------------------------------------
#  Every piece of state belongs to exactly one owner thread; other threads
#  hand over a callable through a queue and get the result back as a value on
#  their own one-slot queue (C1, C4).  ponytail: one command runs at a time per
#  worker; widen to a pipeline if parallelism ever matters.
def _noop() -> None:
    """A command that does nothing (used to wake a worker at shutdown)."""


def _pop_message(buf: bytes) -> "tuple[dict | None, bytes]":
    """The first complete LSP frame in buf, or (None, buf) when incomplete."""
    for sep in (b"\r\n\r\n", b"\n\n"):
        head, found, rest = buf.partition(sep)
        if found:
            break
    else:
        return None, buf
    length = 0
    for line in head.replace(b"\r\n", b"\n").split(b"\n"):
        key, _, value = line.decode(errors="replace").partition(":")
        if key.lower() == "content-length":
            length = int(value.strip())
    if len(rest) < length:
        return None, buf
    return json.loads(rest[:length]), rest[length:]


class _Worker:
    """Base for single-owner-thread objects; subclasses get _call() and may
    override _tick() for periodic work while idle."""

    _tick_seconds = 5.0

    def __init__(self) -> None:
        self._commands: "queue.Queue[tuple]" = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve_loop, daemon=True)
        self._thread.start()

    def _call(self, fn, timeout: float | None = None):
        """Run fn on the owner thread and return its result; raise what it raised."""
        reply: "queue.Queue[tuple]" = queue.Queue(maxsize=1)
        self._commands.put((fn, reply))
        try:
            kind, value = reply.get(timeout=timeout or (_request_timeout() + 30.0))
        except queue.Empty:
            raise ClangdError("%s is not answering" % type(self).__name__)
        if kind == "err":
            raise value
        return value

    def _tick(self) -> None:
        """Periodic work while idle."""

    def _serve_loop(self) -> None:
        while True:
            try:
                fn, reply = self._commands.get(timeout=self._tick_seconds)
            except queue.Empty:
                self._tick()
                if self._stop.is_set():
                    return
                continue
            try:
                outcome = ("ok", fn())
            except Exception as exc:                          # noqa: BLE001 - handed to the caller
                outcome = ("err", exc)
            if reply is not None:
                reply.put(outcome)
            if self._stop.is_set():
                return


class _Lsp(_Worker):
    """One clangd child for one project root.

    Threading: one owner thread runs every protocol exchange and owns all
    state (`_diags`, `_open`, `_text`, `_id`, `_stderr_tail`); other threads
    reach it only via _call() -- see _Worker.  ponytail: one request in flight
    per clangd, executed end to end on the owner; pipeline several if query
    latency ever matters.

    All of clangd's notifications are absorbed here and only ever read back
    through an explicit call, which is what keeps a partially applied edit from
    being pushed at the caller.  A bounded LRU of open files keeps memory in
    check while still avoiding a re-parse on every consecutive query."""

    _tick_seconds = 1.0

    def __init__(self, root: str, db_path: str):
        self.root = root
        self.db_path = db_path
        self.version = _require_supported_clangd()   # raises if clangd is absent or too old
        paths = _project_paths(root)
        for key in ("dir", "compdb", "tmp", "home"):
            _mkdir_private(paths[key])
        shutil.copyfile(db_path, os.path.join(paths["compdb"], "compile_commands.json"))
        env = dict(os.environ)
        env["TMPDIR"] = paths["tmp"]           # keeps the ~150 MB of preamble .pch files here
        env["HOME"] = paths["home"]            # 'nobody' has HOME=/nonexistent in the sandbox
        env.pop("CLANGD_FLAGS", None)
        self._diags: dict = {}                 # uri -> (timestamp, diagnostics)
        self._open: "OrderedDict[str, int]" = OrderedDict()
        self._text: dict = {}                  # uri -> text last pushed to clangd
        self._id = 0
        self._buf = b""                        # raw bytes read from clangd's stdout
        self._stderr_tail: "deque[str]" = deque(maxlen=40)
        try:
            self.proc = subprocess.Popen(
                ["clangd", "--compile-commands-dir=" + paths["compdb"], "--log=error",
                 "--background-index"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=paths["compdb"], env=env, start_new_session=True)
        except OSError as exc:
            raise ClangdError("could not start clangd %s: %s"
                              % (self.version.get("binary") or "clangd", exc))
        self._fd = self.proc.stdout.fileno()
        _Worker.__init__(self)
        threading.Thread(target=self._read_stderr, daemon=True).start()
        try:
            self._call(self._handshake, timeout=60.0)
        except ClangdError:
            self.close()
            raise

    # -- protocol (owner-thread only below this line) -----------------------
    def _handshake(self) -> None:
        init = self._request("initialize",
                            {"processId": os.getpid(), "rootUri": "file://" + self.root,
                             "capabilities": {"textDocument": {"publishDiagnostics": {}}},
                             "initializationOptions": {}}, timeout=60.0)
        result = init.get("result")
        if not isinstance(result, dict) or "capabilities" not in result:
            raise self._error("returned an initialize response this tool does not understand "
                              "(no 'capabilities'): the clangd on PATH may be a different "
                              "implementation, build or version than expected")
        self._notify("initialized", {})
        _log("clangd %s (pid %d) is serving %s"
             % (self.version.get("raw") or "version unknown", self.proc.pid, self.root))

    def _read_message(self, timeout: float) -> "dict | None":
        """Read one LSP message within timeout seconds (None on timeout)."""
        deadline = time.time() + timeout
        while True:
            msg, self._buf = _pop_message(self._buf)
            if msg is not None:
                return msg
            wait = deadline - time.time()
            if wait <= 0:
                return None
            ready, _, _ = select.select([self._fd], [], [], wait)
            if not ready:
                return None
            chunk = os.read(self._fd, 65536)
            if not chunk:
                raise EOFError("clangd closed its output")
            self._buf += chunk

    def _absorb(self, msg: dict) -> None:
        """Fold one notification into owner state; anything else is dropped."""
        if msg.get("method") == "textDocument/publishDiagnostics":
            params = msg["params"]
            self._diags[params["uri"]] = (time.time(), params.get("diagnostics") or [])

    def _read_stderr(self) -> None:
        """Drain clangd's stderr so it can never block, handing each line to
        the owner thread, which keeps the tail for error messages (bounded:
        only the last few lines are ever reported)."""
        try:
            for line in self.proc.stderr:
                text = line.decode(errors="replace").rstrip()
                if text:
                    self._commands.put((lambda t=text: self._note_stderr(t), None))
        except Exception:                                     # noqa: BLE001 - pipe closed
            pass

    def _note_stderr(self, text: str) -> None:
        self._stderr_tail.append(text)

    def dead(self) -> bool:
        """True once the clangd process has exited (crash or clean shutdown)."""
        return self.proc.poll() is not None

    def _failure(self, what: str) -> str:
        """A one-line description of this clangd, its version and its last words."""
        tail = [line for line in self._stderr_tail if line]
        said = ("  clangd said: " + " | ".join(tail[-4:])) if tail else ""
        code = self.proc.poll()
        where = "%s (pid %d%s)" % (self.version.get("raw") or "clangd",
                                   self.proc.pid,
                                   ", exit %s" % code if code is not None else "")
        return "%s %s.%s" % (where, what, said)

    def _error(self, what: str, retryable: bool = False) -> ClangdError:
        """Build a ClangdError about this clangd, flagged when a fresh process
        could plausibly succeed."""
        error = ClangdError(self._failure(what))
        error.retryable = retryable
        return error

    def _send(self, payload: dict) -> None:
        blob = json.dumps(payload).encode()
        try:
            self.proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(blob) + blob)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise self._error("could not be written to (%s)" % exc, retryable=True)

    def _notify(self, method: str, params) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params, timeout: float | None = None) -> dict:
        """Send one request and return clangd's reply, or raise.

        A crash, a closed pipe or silence past the deadline all raise a
        *retryable* ClangdError; a JSON-RPC error object from clangd raises a
        plain ClangdError carrying clangd's own message (a protocol or version
        mismatch shows up here)."""
        timeout = timeout or _request_timeout()
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise self._error("did not answer %s within %.0fs" % (method, timeout),
                                  retryable=True)
            try:
                msg = self._read_message(remaining)
            except EOFError:
                raise self._error("exited while answering %s" % method, retryable=True)
            if msg is None:
                raise self._error("did not answer %s within %.0fs" % (method, timeout),
                                  retryable=True)
            if msg.get("id") == rid:
                if "error" in msg:
                    err = msg["error"]
                    detail = err.get("message") if isinstance(err, dict) else err
                    raise ClangdError("%s rejected %s: %s"
                                      % (self.version.get("raw") or "clangd", method, detail))
                return msg
            if "id" in msg:
                continue                           # stray reply to a timed-out request
            self._absorb(msg)

    # -- files -------------------------------------------------------------
    def _open_path(self, path: str) -> "tuple[str, float]":
        """Make clangd's view of `path` match the file on disk.

        Returns the file's URI and the time from which a *new* diagnostics
        snapshot is expected -- 0.0 when the text was unchanged, in which case
        the snapshot already held is still current.  clangd silently ignores a
        didChange that carries identical text (it does not re-publish), so the
        caller must not wait for a fresh report in that case.  The open set is
        an LRU capped at TERMITE_CLANGD_OPEN_TUS files."""
        uri = _to_uri(path)
        try:
            text = open(path, "r", errors="replace").read()
        except OSError as exc:
            raise ClangdError("cannot read %s: %s" % (path, exc))
        if uri in self._open and self._text.get(uri) == text:
            self._open.move_to_end(uri)
            return uri, 0.0                                   # clangd already holds this text
        pushed = time.time()
        self._diags.pop(uri, None)                            # drop any pre-change snapshot
        if uri in self._open:
            self._open.move_to_end(uri)
            self._open[uri] += 1
            self._notify("textDocument/didChange",
                         {"textDocument": {"uri": uri, "version": self._open[uri]},
                          "contentChanges": [{"text": text}]})
        else:
            self._open[uri] = 1
            self._notify("textDocument/didOpen",
                         {"textDocument": {"uri": uri, "languageId": _language_id(path),
                                           "version": 1, "text": text}})
        self._text[uri] = text
        limit = max(1, _env_int("TERMITE_CLANGD_OPEN_TUS", DEFAULT_OPEN_TUS))
        while len(self._open) > limit:
            oldest = next(iter(self._open))
            del self._open[oldest]
            self._text.pop(oldest, None)
            self._notify("textDocument/didClose", {"textDocument": {"uri": oldest}})
            self._diags.pop(oldest, None)
        return uri, pushed

    # -- queries (public: any thread; each runs on the owner) --------------
    def diagnostics(self, path: str, min_severity: Severity | str = Severity.HINT) -> list:
        return self._call(lambda: self._diagnostics_impl(path, Severity(min_severity)))

    def hover(self, path: str, line: int, col: int):
        return self._call(lambda: self._hover_impl(path, line, col))

    def locations(self, method: str, path: str, line: int, col: int, extra: dict | None = None) -> list:
        return self._call(lambda: self._locations_impl(method, path, line, col, extra))

    def status(self) -> dict:
        """Live facts about this clangd; answers within seconds even when busy."""
        return self._call(self._status_impl, timeout=5.0)

    def _diagnostics_impl(self, path: str, min_severity: Severity) -> list:
        settle = _env_float("TERMITE_CLANGD_SETTLE", DEFAULT_SETTLE)
        uri, pushed = self._open_path(path)
        deadline = time.time() + _request_timeout()
        seen_ts = None          # timestamp of the newest snapshot clangd published
        quiet_since = None      # when that snapshot last changed
        while True:
            hit = self._diags.get(uri)
            current = hit is not None and (pushed == 0.0 or hit[0] >= pushed)
            if current and pushed == 0.0:
                break                                          # unchanged text: snapshot is current
            if current:
                if hit[0] != seen_ts:                          # a fresh snapshot arrived
                    seen_ts = hit[0]
                    quiet_since = time.time()
                elif time.time() - quiet_since >= settle:      # nothing new for `settle`
                    break
            now = time.time()
            if now >= deadline:
                raise self._error("did not report diagnostics for %s within %.0fs"
                                  % (path, _request_timeout()), retryable=True)
            if self.proc.poll() is not None:
                raise self._error("exited before reporting diagnostics for %s" % path,
                                  retryable=True)
            try:
                msg = self._read_message(min(0.05, deadline - now))
            except EOFError:
                raise self._error("exited before reporting diagnostics for %s" % path,
                                  retryable=True)
            if msg is not None and "id" not in msg:
                self._absorb(msg)                              # notifications -> _diags
        raw = (self._diags.get(uri) or (0, []))[1]
        allowed = _SEVERITY_RANK[min_severity]
        out = []
        for item in raw:
            severity = _SEVERITY_NAME.get(item.get("severity"), Severity.HINT)
            if _SEVERITY_RANK[severity] > allowed:
                continue
            where = _rng(item.get("range") or {})
            out.append(Diagnostic(file=path, line=where["line"], col=where["col"],
                                  end_line=where["end_line"], end_col=where["end_col"],
                                  severity=severity,
                                  code=str(item.get("code") or ""),
                                  message=(item.get("message") or "").splitlines()[0] if item.get("message") else ""))
        return out

    def _hover_impl(self, path: str, line: int, col: int):
        uri, _ = self._open_path(path)
        result = self._request("textDocument/hover",
                               {"textDocument": {"uri": uri}, "position": _pos(line, col)}).get("result")
        if not result:
            return None
        text = _markup_text(result.get("contents")).strip()
        if not text:
            return None
        signature, _, rest = text.partition("\n")
        return Hover(signature=signature.strip(), doc=rest.strip().strip("`").strip())

    def _locations_impl(self, method: str, path: str, line: int, col: int, extra: dict | None = None) -> list:
        uri, _ = self._open_path(path)
        params = {"textDocument": {"uri": uri}, "position": _pos(line, col)}
        if extra:
            params.update(extra)
        found = []
        for item in _as_list(self._request(method, params).get("result")):
            if not isinstance(item, dict):
                continue
            target = item.get("uri") or item.get("targetUri") or ""
            where = _rng(item.get("range") or item.get("targetSelectionRange")
                         or item.get("targetRange") or {})
            found.append(Location(file=_from_uri(target), **where))
        return found

    def _status_impl(self) -> dict:
        return {"clangd_pid": self.proc.pid,
                "clangd_version": self.version.get("raw") or "",
                "open_files": len(self._open),
                "rss_mib": round(_rss_mib(self.proc.pid), 1)}

    def close(self) -> None:
        """Kill this clangd and stop its owner thread within a bound."""
        self._stop.set()
        try:
            self.proc.kill()
        except Exception:                                     # noqa: BLE001
            pass
        self._commands.put((_noop, None))            # wake the owner if it is idle
        self._thread.join(timeout=5.0)


def _rss_mib(pid: int) -> float:
    try:
        with open("/proc/%d/status" % pid) as handle:
            for line in handle:
                if line.startswith("VmRSS"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return 0.0


# ---------------------------------------------------------------------------
#  The supervisor: owns every clangd child, enforces idle timeout + LRU cap
# ---------------------------------------------------------------------------
class _Supervisor(_Worker):
    """Registry of root -> _Lsp, with an idle timeout and an LRU cap on how many
    clangd instances may be warm at once (each one costs hundreds of MiB).

    Threading: one owner thread owns the registry and runs every request;
    serve_conn threads only do socket I/O and hand commands over (see
    _Worker).  The monitor reaper is this worker's idle _tick.
    ponytail: one worker serializes requests across roots; per-root workers
    if parallel roots ever matter."""

    _tick_seconds = 15.0

    def __init__(self) -> None:
        self.instances: dict = {}
        self.last_used: dict = {}
        self.started = time.time()
        self.last_request = time.time()
        self.idle_timeout = _env_float("TERMITE_CLANGD_IDLE", DEFAULT_IDLE)
        self.max_instances = max(1, _env_int("TERMITE_CLANGD_MAX", DEFAULT_MAX))
        _Worker.__init__(self)

    def _tick(self) -> None:
        """Reclaim memory: drop clangd instances that have gone idle, and exit
        once nothing is warm and no one has asked for anything for a while."""
        now = time.time()
        for root in list(self.instances):
            instance = self.instances[root]
            if instance.dead():
                self.instances.pop(root, None)
                self.last_used.pop(root, None)
                _log("clangd pid=%d for %s exited; dropped" % (instance.proc.pid, root))
                continue
            if now - self.last_used.get(root, now) > self.idle_timeout:
                self.instances.pop(root)
                self.last_used.pop(root, None)
                instance.close()
                _log("idle timeout: closed clangd pid=%d for %s" % (instance.proc.pid, root))
        if not self.instances and now - max(self.last_request, self.started) > 120:
            _log("supervisor idle with nothing warm; exiting")
            # ponytail: hard exit is the daemon's defined end (C2); drain
            # in-flight connections gracefully if that ever stops being enough
            os._exit(0)

    def _instance(self, root: str) -> _Lsp:
        """The warm clangd for `root`, starting one on first use and silently
        replacing one that has crashed.  Raises ClangdError (no usable database,
        unsupported clangd, failed handshake) rather than returning a bad one."""
        root = os.path.realpath(root)
        self.last_used[root] = time.time()
        existing = self.instances.get(root)
        if existing is not None and not existing.dead():
            return existing
        if existing is not None:                          # it crashed: replace it
            self.instances.pop(root, None)
            _log("clangd pid=%d for %s had already exited; replacing it"
                 % (existing.proc.pid, root))
            existing.close()
        db_path = _require_db(root)
        while len(self.instances) >= self.max_instances:
            self._evict_lru(keep=root)
        started = time.time()
        instance = _Lsp(root, db_path)                    # may raise ClangdError
        self.instances[root] = instance
        self.last_used[root] = time.time()
        _log("started clangd pid=%d for %s in %.1fs" % (instance.proc.pid, root, time.time() - started))
        return instance

    def _forget(self, root: str, instance: _Lsp) -> None:
        """Drop `instance` from the registry (only if it is still the current
        one) and kill it, so the next request starts a fresh clangd."""
        if self.instances.get(root) is instance:
            self.instances.pop(root, None)
            self.last_used.pop(root, None)
        instance.close()

    def _serve(self, root: str, action) -> dict:
        """Run `action(instance)` for `root` and return a response body.

        A clangd that crashes or goes silent is thrown away and the action is
        retried once on a fresh instance, so a single crash is invisible to the
        caller.  An action that fails for the caller's own reason (unreadable
        file, 1-based position out of range) is reported without touching a
        healthy clangd."""
        error: ClangdError | None = None
        for attempt in (1, 2):
            try:
                instance = self._instance(root)
            except ClangdError as exc:
                return {"ok": False, "error": str(exc)}
            try:
                return {"ok": True, **action(instance)}
            except ClangdError as exc:
                error = exc
                broken = bool(getattr(exc, "retryable", False)) or instance.dead()
                if not broken:                                # the caller's fault: keep clangd
                    return {"ok": False, "error": str(exc)}
                _log("clangd pid=%d for %s failed (%s)%s"
                     % (instance.proc.pid, root, exc,
                        "; retrying on a fresh clangd" if attempt == 1 else ""))
                self._forget(root, instance)
                if attempt == 2:
                    break
        return {"ok": False, "error": str(error) if error else "clangd failed"}

    def _evict_lru(self, keep: str | None = None) -> str | None:
        victims = [r for r in self.instances if r != keep]
        if not victims:
            return None
        victim = min(victims, key=lambda r: self.last_used.get(r, 0.0))
        instance = self.instances.pop(victim)
        self.last_used.pop(victim, None)
        instance.close()
        _log("evicted clangd pid=%d for %s (index cache kept on disk)" % (instance.proc.pid, victim))
        return victim

    def _close(self, root: str | None) -> int:
        targets = [os.path.realpath(root)] if root else list(self.instances)
        closed = 0
        for target in targets:
            instance = self.instances.pop(target, None)
            self.last_used.pop(target, None)
            if instance is not None:
                instance.close()
                closed += 1
        return closed

    def handle(self, request: dict) -> dict:
        method = request.get("method")
        root = request.get("root")
        self.last_request = time.time()
        if method == "ping":
            return {"ok": True, "supervisor_pid": os.getpid(), "started": self.started}
        if method == "status":
            instances: list[InstanceInfo] = []
            for r in list(self.instances):
                try:
                    info = self.instances[r].status()
                except ClangdError:                           # busy: report what we know
                    info = {"clangd_pid": 0, "clangd_version": "",
                            "open_files": 0, "rss_mib": 0.0}
                instances.append(InstanceInfo(
                    root=r, idle_seconds=round(time.time() - self.last_used.get(r, time.time()), 1),
                    **info))
            return {"ok": True, "supervisor_pid": os.getpid(),
                    "clangd_version": _clangd_version().get("raw", ""),
                    "instances": instances}
        if method == "close":
            return {"ok": True, "closed": self._close(root)}
        if method == "shutdown":
            self._close(None)
            threading.Timer(0.2, lambda: os._exit(0)).start()
            return {"ok": True}
        if not root:
            return {"ok": False, "error": "request needs a 'root'"}
        path = request.get("path")
        if method in ("diagnostics", "hover", "definition", "references") and not path:
            return {"ok": False, "error": "request needs a 'path'"}
        try:
            if method == "diagnostics":
                return self._serve(root, lambda i: {
                    "diagnostics": i.diagnostics(path, Severity(request.get("min_severity", "hint")))})
            if method == "hover":
                line, col = int(request["line"]), int(request["col"])
                return self._serve(root, lambda i: {"hover": i.hover(path, line, col)})
            if method == "definition":
                line, col = int(request["line"]), int(request["col"])
                return self._serve(root, lambda i: {"locations": i.locations(
                    "textDocument/definition", path, line, col)})
            if method == "references":
                line, col = int(request["line"]), int(request["col"])
                return self._serve(root, lambda i: {"locations": i.locations(
                    "textDocument/references", path, line, col,
                    {"context": {"includeDeclaration": bool(request.get("include_decl"))}})})
        except (KeyError, ValueError) as exc:
            return {"ok": False, "error": "bad request for %s: %s" % (method, exc)}
        return {"ok": False, "error": "unknown method %r" % (method,)}

    def serve_conn(self, conn: socket.socket) -> None:
        """One client connection: read JSON request lines, answer each with the
        supervisor owner thread's response (socket I/O stays on this thread)."""
        try:
            stream = conn.makefile("rwb")
            for line in stream:
                try:
                    response = self._call(lambda: self.handle(json.loads(line)),
                                          timeout=_request_timeout() + 120.0)
                except Exception as exc:                      # noqa: BLE001 - never kill the daemon
                    response = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
                stream.write((json.dumps(response) + "\n").encode())
                stream.flush()
        except Exception:                                     # noqa: BLE001 - client hung up
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass


def _log(message: str) -> None:
    paths = _supervisor_paths()
    try:
        _mkdir_private(paths["dir"])
        with open(paths["log"], "a") as handle:
            handle.write("%s pid=%d %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), os.getpid(), message))
    except OSError:
        pass


def _daemon_main() -> int:
    """Entry point for the detached supervisor process (internal: `--daemon`)."""
    paths = _supervisor_paths()
    _mkdir_private(paths["dir"])
    if os.path.exists(paths["sock"]):
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(2.0)
            probe.connect(paths["sock"])
            probe.sendall(b'{"method":"ping"}\n')
            if probe.recv(4096):
                return 0                                      # another supervisor already serves us
        except OSError:
            pass
        finally:
            probe.close()
        try:
            os.unlink(paths["sock"])                          # stale socket from a dead supervisor
        except OSError:
            return 0
    if shutil.which("clangd") is None:
        _log("refusing to start: no clangd on PATH")
        return 1
    supervisor = _Supervisor()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(paths["sock"])
    except OSError as exc:
        _log("cannot bind %s: %s" % (paths["sock"], exc))
        return 1
    os.chmod(paths["sock"], 0o600)                            # only this uid may talk to us
    server.listen(16)
    with open(paths["pid"], "w") as handle:
        handle.write("%d\n" % os.getpid())
    _log("supervisor up, idle_timeout=%.0fs max=%d" % (supervisor.idle_timeout, supervisor.max_instances))
    while True:
        try:
            conn, _ = server.accept()
        except OSError:
            time.sleep(0.2)
            continue
        threading.Thread(target=supervisor.serve_conn, args=(conn,), daemon=True).start()


# ---------------------------------------------------------------------------
#  Client: thin, stateless; every call goes through the supervisor
# ---------------------------------------------------------------------------
def _connect(timeout: float) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(_supervisor_paths()["sock"])
    return sock


def _permission_help() -> str:
    paths = _supervisor_paths()
    return ("cannot use the clangd supervisor socket %s: it belongs to another sandbox uid "
            "(running as uid %d). Caches are keyed by uid, so remove %s and retry."
            % (paths["sock"], os.getuid(), paths["dir"]))


def _spawn_supervisor() -> None:
    paths = _supervisor_paths()
    _mkdir_private(paths["dir"])
    with open(paths["log"], "ab") as log:
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "--daemon"],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         cwd=paths["dir"], start_new_session=True, close_fds=True)


def _wait_for_supervisor(paths: dict, timeout: float) -> None:
    """Block until the supervisor socket accepts a connection, or time out."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.1)
        try:
            _connect(2.0).close()
            return
        except PermissionError:
            raise ClangdError(_permission_help())
        except OSError:
            continue
    raise ClangdError("clangd supervisor did not start within %.0fs; see %s"
                      % (timeout, paths["log"]))


def _ensure_supervisor(timeout: float = 60.0) -> None:
    """Connect to the supervisor, starting it on first use (lazy + detached).

    Fails fast, and clearly, when there is no usable clangd to serve -- there is
    no point starting a supervisor that would refuse every request."""
    _require_supported_clangd()
    paths = _supervisor_paths()
    _mkdir_private(paths["dir"])
    try:
        _connect(2.0).close()
        return
    except PermissionError:
        raise ClangdError(_permission_help())
    except OSError:
        pass
    _spawn_supervisor()
    _wait_for_supervisor(paths, timeout)


def _restart_supervisor(timeout: float = 60.0) -> None:
    """Start a fresh supervisor after one died.  The stale socket is removed
    first so the new daemon does not defer to a dead peer."""
    paths = _supervisor_paths()
    _mkdir_private(paths["dir"])
    try:
        os.unlink(paths["sock"])
    except OSError:
        pass
    _spawn_supervisor()
    _wait_for_supervisor(paths, timeout)


def _rpc_once(request: dict, timeout: float) -> dict:
    """One round trip to the supervisor.  Raises _SupervisorGoneError when the
    connection died (so _rpc can restart it), or ClangdError for a real error."""
    try:
        sock = _connect(timeout)
    except PermissionError:
        raise ClangdError(_permission_help())
    except OSError as exc:
        raise _SupervisorGoneError("could not reach the clangd supervisor: %s" % exc)
    try:
        sock.sendall((json.dumps(request) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as exc:
        raise _SupervisorGoneError("the clangd supervisor connection broke: %s" % exc)
    except socket.timeout:
        raise ClangdError("timed out after %.0fs waiting for clangd" % timeout)
    finally:
        sock.close()
    if not buf.strip():
        raise _SupervisorGoneError("the clangd supervisor closed the connection without answering")
    try:
        response = json.loads(buf)
    except ValueError as exc:
        raise _SupervisorGoneError("the clangd supervisor sent an unreadable reply: %s" % exc)
    if not response.get("ok"):
        raise ClangdError(response.get("error") or "the clangd supervisor reported an error")
    return response


def _rpc(request: dict, timeout: float | None = None) -> dict:
    """Send one request to the supervisor, restarting it once if it has died
    (the worker may have been recycled, or the supervisor crashed)."""
    timeout = timeout or _request_timeout()
    _ensure_supervisor()
    try:
        return _rpc_once(request, timeout)
    except _SupervisorGoneError:
        _restart_supervisor()
        return _rpc_once(request, timeout)


def _shutdown_supervisor() -> None:
    paths = _supervisor_paths()
    if not os.path.exists(paths["sock"]):
        return
    try:
        _rpc({"method": "shutdown"}, timeout=15.0)
    except Exception:                                         # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
#  Public API (root first: one clangd instance per (uid, root))
# ---------------------------------------------------------------------------
def available() -> bool:
    """True when a clangd language-server binary is installed on PATH; every C++ query below needs one.

    clangd is the C++ language server that answers these calls, so without it there is nothing to ask. Use this to tell whether a C++ question about a project can be answered at all on this machine.
    """
    return shutil.which("clangd") is not None


def diagnostics(root: str, path: str, min_severity: Severity | str = Severity.HINT,
                timeout: float | None = None) -> list[Diagnostic]:
    """Ask clangd for the compiler errors and warnings in `path`, reflecting the file's text on disk right now.

    `root` is the project directory (the one holding the compile database); `path` is the source file to check. clangd re-parses the file and the answer is awaited until the stream goes quiet (TERMITE_CLANGD_SETTLE seconds), so the result is a settled parse, not a half-finished one. Only severities at or above `min_severity` ('hint' < 'info' < 'warning' < 'error') are returned, each as a Diagnostic with 1-based line/col. A clean file returns an empty list; raises ClangdError when the project has no usable compile database.
    """
    response = _rpc({"method": "diagnostics", "root": os.path.realpath(root),
                     "path": os.path.realpath(path),
                     "min_severity": Severity(min_severity)}, timeout)
    items = response["diagnostics"]
    for item in items:
        item["severity"] = Severity(item["severity"])
    return items


def check(root: str, path: str, timeout: float | None = None) -> CheckSummary:
    """Ask clangd for `path`'s diagnostics and reduce them to per-severity counts plus `clean` (True when there are no *errors*).

    Warnings, infos and hints are counted but do not make a file unclean, which is exactly what makes this the one-call verb for a build-test-fix loop: edit until `clean` is True. It is a thin wrapper over `diagnostics`, so the same rules about the project and the settle window apply.
    """
    items = diagnostics(root, path, timeout=timeout)
    counts: dict[Severity, int] = {s: 0 for s in Severity}
    for item in items:
        counts[item["severity"]] = counts.get(item["severity"], 0) + 1
    return CheckSummary(file=os.path.realpath(path),
                        errors=counts[Severity.ERROR], warnings=counts[Severity.WARNING],
                        infos=counts[Severity.INFO], hints=counts[Severity.HINT],
                        total=len(items), clean=counts[Severity.ERROR] == 0)


def hover(root: str, path: str, line: int, col: int,
          timeout: float | None = None) -> Hover | None:
    """Ask clangd what the symbol at `path`:`line`:`col` is -- its type, signature and doc comment -- or None.

    `line` and `col` are 1-based (clangd itself is 0-based, so they are converted). Returns None when clangd has nothing to say at that spot, for example on whitespace or an unresolved name. Use it to confirm the type of an expression before relying on it.
    """
    response = _rpc({"method": "hover", "root": os.path.realpath(root),
                     "path": os.path.realpath(path), "line": line, "col": col}, timeout)
    return response["hover"]


def definition(root: str, path: str, line: int, col: int,
               timeout: float | None = None) -> list[Location]:
    """Ask clangd where the symbol at `path`:`line`:`col` (1-based) is defined, as file:line:col locations.

    A list because one position can resolve to several definitions (an overload set, a redeclaration); it is empty when clangd cannot resolve the symbol. Use it instead of grepping for the declaration; it follows #include and the compile database.
    """
    response = _rpc({"method": "definition", "root": os.path.realpath(root),
                     "path": os.path.realpath(path), "line": line, "col": col}, timeout)
    return response["locations"]


def references(root: str, path: str, line: int, col: int, include_decl: bool = False,
               timeout: float | None = None) -> list[Location]:
    """Ask clangd for every use of the symbol at `path`:`line`:`col` (1-based), across the whole project.

    Set `include_decl` to also list the declaration site. Results are project-wide because they come from clangd's index, so this is the safe way to find callers before changing a signature. A symbol with no other uses returns an empty list.
    """
    response = _rpc({"method": "references", "root": os.path.realpath(root),
                     "path": os.path.realpath(path), "line": line, "col": col,
                     "include_decl": include_decl}, timeout)
    return response["locations"]


def status(timeout: float | None = None) -> SupervisorStatus:
    """Report what clangd work is currently warm: the supervisor process and each project's clangd (pid, idle seconds, open files, resident MiB).

    Handy for deciding whether to `close` a project: each warm clangd costs hundreds of MiB. `running` is False when no supervisor has been started yet.
    """
    paths = _supervisor_paths()
    result = SupervisorStatus(running=False, supervisor_pid=0,
                              clangd_version=_clangd_version().get("raw", ""),
                              socket=paths["sock"], scratch=_uid_dir(),
                              idle_timeout=_env_float("TERMITE_CLANGD_IDLE", DEFAULT_IDLE),
                              max_instances=_env_int("TERMITE_CLANGD_MAX", DEFAULT_MAX),
                              instances=[])
    if not os.path.exists(paths["sock"]):
        return result
    try:
        response = _rpc({"method": "status"}, timeout)
    except ClangdError:
        return result
    result["running"] = True
    result["supervisor_pid"] = response.get("supervisor_pid", 0)
    result["clangd_version"] = response.get("clangd_version", result["clangd_version"])
    result["instances"] = response.get("instances", [])
    return result


def close(root: str | None = None, timeout: float | None = None) -> int:
    """Stop the clangd serving `root`, or every project's clangd when `root` is None, returning how many were stopped.

    The on-disk index cache is kept, so a later call for the same root reloads it instead of re-indexing from scratch; only the memory is freed.
    """
    paths = _supervisor_paths()
    if not os.path.exists(paths["sock"]):
        return 0
    try:
        response = _rpc({"method": "close", "root": os.path.realpath(root) if root else None}, timeout)
    except ClangdError:
        return 0
    return int(response.get("closed", 0))


# ---------------------------------------------------------------------------
#  CLI
# ---------------------------------------------------------------------------
def _parse_pos(text: str) -> tuple[str, int, int]:
    """Split "FILE:LINE:COL" into (file, line, col)."""
    parts = text.rsplit(":", 2)
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        raise ClangdError("expected FILE:LINE:COL, got %r" % (text,))
    return parts[0], int(parts[1]), int(parts[2])


def _format_diagnostic(item: Diagnostic) -> str:
    code = " [%s]" % item["code"] if item["code"] else ""
    return "%s:%d:%d: %s: %s%s" % (item["file"], item["line"], item["col"],
                                   item["severity"], item["message"], code)


def _format_health(health: DbHealth) -> str:
    if not health["present"]:
        return "no compile database: %s" % health["hint"]
    state = "ok" if health["fresh"] else "STALE"
    lines = ["%s (%s): %d entries" % (health["db_path"], state, health["entries"])]
    if health["missing"]:
        lines.append("missing paths (first %d):" % len(health["missing"]))
        lines += ["  " + path for path in health["missing"]]
    lines.append("hint: %s" % health["hint"])
    return "\n".join(lines)


def _resolve(root: str, path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(root, path)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code: 0 green, 1 not clean,
    2 usage/clangd missing, 3 clangd could not answer."""
    parser = argparse.ArgumentParser(
        prog="clangd.py",
        description="Ask a real clangd about a C++ project (diagnostics, hover, definitions, references).")
    parser.add_argument("--root", default=os.getcwd(), help="C++ project root (default: current directory)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument("--all", action="store_true", help="with 'close': every root")
    parser.add_argument("--selftest", action="store_true", help="run the built-in self test and exit")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("db-health", help="report on the project's compile_commands.json")
    diag = sub.add_parser("diagnostics", help="print compiler diagnostics for FILE...")
    diag.add_argument("files", nargs="*")
    diag.add_argument("--min-severity", default="hint", choices=[s.value for s in Severity])
    chk = sub.add_parser("check", help="count diagnostics per file; exit 1 if any file has errors")
    chk.add_argument("files", nargs="*")
    for name in ("hover", "definition", "references"):
        pos = sub.add_parser(name, help="%s at FILE:LINE:COL" % name)
        pos.add_argument("position")
    sub.add_parser("status", help="show the supervisor and its clangd instances")
    sub.add_parser("close", help="stop clangd for --root, or every root with --all")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()
    #  Only the semantic queries need clangd.  db-health, status and close are
    #  useful on their own (e.g. on a machine with no clangd), so they run and
    #  report their own outcome rather than being blocked here.
    query = args.command in ("diagnostics", "check", "hover", "definition", "references")
    if query and not available():
        try:
            _require_supported_clangd()   # authoritative "no clangd" message
        except ClangdError as exc:
            print("clangd.py: %s" % exc, file=sys.stderr)
        return 2
    root = os.path.realpath(args.root)
    try:
        if args.command == "db-health":
            health = compile_db_health(root)
            print(json.dumps(health, indent=2) if args.json else _format_health(health))
            return 0 if health["present"] and health["fresh"] else 1
        if args.command in ("diagnostics", "check"):
            if not args.files:
                parser.error("%s needs at least one FILE" % args.command)
            bad = 0
            for name in args.files:
                path = _resolve(root, name)
                if args.command == "diagnostics":
                    items = diagnostics(root, path, args.min_severity)
                    if args.json:
                        print(json.dumps(items, indent=2))
                    else:
                        for item in items:
                            print(_format_diagnostic(item))
                else:
                    summary = check(root, path)
                    if not summary["clean"]:
                        bad += 1
                    print(json.dumps(summary) if args.json else
                          "%s: %d error(s), %d warning(s), %d info, %d hint%s" % (
                              path, summary["errors"], summary["warnings"], summary["infos"],
                              summary["hints"], "" if summary["clean"] else "  <-- errors"))
            return 1 if bad else 0
        if args.command in ("hover", "definition", "references"):
            path, line, col = _parse_pos(args.position)
            path = _resolve(root, path)
            if args.command == "hover":
                result = hover(root, path, line, col)
                if args.json:
                    print(json.dumps(result, indent=2))
                elif result:
                    print(result["signature"])
                    if result["doc"]:
                        print(result["doc"])
                else:
                    print("(clangd has nothing at %s:%d:%d)" % (path, line, col))
            else:
                found = (definition if args.command == "definition" else references)(root, path, line, col)
                if args.json:
                    print(json.dumps(found, indent=2))
                else:
                    for where in found:
                        print("%s:%d:%d" % (where["file"], where["line"], where["col"]))
                    if not found:
                        print("(none)")
            return 0
        if args.command == "status":
            state = status()
            if args.json:
                print(json.dumps(state, indent=2))
            else:
                print("supervisor: %s (pid %s)" % ("running" if state["running"] else "not running",
                                                   state["supervisor_pid"] or "-"))
                print("socket:     %s" % state["socket"])
                print("scratch:    %s" % state["scratch"])
                print("policy:     idle %.0fs, max %d instance(s)" % (state["idle_timeout"],
                                                                      state["max_instances"]))
                if not state["instances"]:
                    print("instances:  none warm")
                for inst in state["instances"]:
                    print("instances:  pid %d  %s  idle %.0fs  %d file(s) open  %.0f MiB"
                          % (inst["clangd_pid"], inst["root"], inst["idle_seconds"],
                             inst["open_files"], inst["rss_mib"]))
            return 0
        if args.command == "close":
            print("closed %d clangd instance(s)" % close(None if args.all else root))
            return 0
        parser.print_help()
        return 2
    except ClangdError as exc:
        print("clangd.py: %s" % exc, file=sys.stderr)
        return 3


# ---------------------------------------------------------------------------
#  Self test
# ---------------------------------------------------------------------------
def _selftest_live(directory: str) -> list[str]:
    """Drive a real clangd against a throwaway project in scratch space."""
    failures: list[str] = []
    os.makedirs(directory, exist_ok=True)
    cxx = shutil.which("c++") or shutil.which("clang++") or shutil.which("g++") or "/usr/bin/c++"
    good = os.path.join(directory, "good.cpp")
    bad = os.path.join(directory, "bad.cpp")
    with open(good, "w") as handle:
        handle.write("int add(int a, int b) { return a + b; }\nint main() { return add(1, 2); }\n")
    with open(bad, "w") as handle:
        handle.write("int f() { return \"oops\"; }\n")
    entries = [{"directory": directory, "command": "%s -std=c++23 -c %s" % (cxx, name), "file": name}
               for name in (good, bad)]
    with open(os.path.join(directory, "compile_commands.json"), "w") as handle:
        json.dump(entries, handle)
    try:
        health = compile_db_health(directory)
        if not (health["present"] and health["fresh"]):
            failures.append("live: compile_db_health should be present+fresh, got %s" % health["hint"])
        found = diagnostics(directory, bad)
        if len(found) != 1 or found[0]["severity"] != Severity.ERROR:
            failures.append("live: expected exactly 1 error in bad.cpp, got %r" % (found,))
        elif found[0]["line"] != 1:
            failures.append("live: error should be on line 1, got %d" % found[0]["line"])
        clean = check(directory, good)
        if not clean["clean"] or clean["errors"]:
            failures.append("live: good.cpp should be clean, got %r" % (clean,))
        use_col = open(good).read().splitlines()[1].index("add(") + 1
        where = definition(directory, good, 2, use_col)
        if not where or where[0]["line"] != 1:
            failures.append("live: definition of add should be line 1, got %r" % (where,))
        result = hover(directory, good, 2, use_col)
        if not result or "add" not in result["signature"]:
            failures.append("live: hover should name add, got %r" % (result,))
        # a crashed clangd must be noticed and replaced transparently
        warm = [i for i in status()["instances"] if i["root"] == os.path.realpath(directory)]
        if not warm:
            failures.append("live: expected a warm clangd for the test project")
        else:
            try:
                os.kill(warm[0]["clangd_pid"], signal.SIGKILL)
            except OSError:
                pass
            again = diagnostics(directory, bad)
            if len(again) != 1 or again[0]["severity"] != Severity.ERROR:
                failures.append("live: did not recover from a clangd crash, got %r" % (again,))
    finally:
        close(directory)
        shutil.rmtree(directory, ignore_errors=True)
    return failures


def _selftest() -> int:
    """Run the built-in self test. Returns 0 when everything passed."""
    checks = 0
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal checks
        checks += 1
        if condition:
            print("  PASS  %s" % name)
        else:
            print("  FAIL  %s%s" % (name, (": " + detail) if detail else ""))
            failures.append(name)

    print("clangd.py selftest")
    check("parse FILE:LINE:COL", _parse_pos("a/b.cpp:12:7") == ("a/b.cpp", 12, 7))
    try:
        _parse_pos("a/b.cpp")
        check("reject FILE without LINE:COL", False, "no ClangdError raised")
    except ClangdError:
        check("reject FILE without LINE:COL", True)
    check("1-based line/col -> LSP 0-based", _pos(1, 1) == {"line": 0, "character": 0})
    check("uri round trip", _from_uri(_to_uri("/tmp/icf")) == "/tmp/icf")
    check("severity names", _SEVERITY_NAME[1] is Severity.ERROR and _SEVERITY_RANK[Severity.HINT] == 4)
    check("scratch is keyed by uid", ("uid-%d" % os.getuid()) in _uid_dir())
    check("scratch root shared across uids (01777)",
          os.stat(_scratch_root()).st_mode & 0o7777 == 0o1777)
    check("per-root cache is distinct", _project_paths("/a/b")["dir"] != _project_paths("/a/c")["dir"])
    check("parse clangd version", _parse_clangd_version("clangd version 18.1.3")["major"] == 18)
    check("parse distro clangd version",
          _parse_clangd_version("Debian clangd version 16.0.6 (x)\n")["major"] == 16)
    check("unparseable version is tolerated", _parse_clangd_version("no digits here")["major"] is None)
    check("retryable errors are marked", getattr(_unavailable("x"), "retryable", False) is True)
    check("plain errors are not retryable",
          getattr(ClangdError("x"), "retryable", False) is False)

    scratch = os.path.join(os.environ.get("TMPDIR", "/tmp"), "clangd-selftest-%d" % os.getpid())
    os.makedirs(scratch, exist_ok=True)
    try:
        empty = compile_db_health(scratch)
        check("missing database is reported", not empty["present"] and not empty["fresh"], empty["hint"])
        with open(os.path.join(scratch, "compile_commands.json"), "w") as handle:
            json.dump([{"directory": "/no/such/place", "command": "c++ -c x.cpp", "file": "x.cpp"}], handle)
        stale = compile_db_health(scratch)
        check("stale database is detected", stale["present"] and not stale["fresh"], stale["hint"])
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    #  The tool must survive a machine with no clangd: availability reads False
    #  and a query refuses with a clear ClangdError instead of crashing.  Force
    #  that case here so the guarantee holds wherever the suite runs.
    no_clangd = os.path.join(os.environ.get("TMPDIR", "/tmp"), "clangd-absent-%d" % os.getpid())
    os.makedirs(no_clangd, exist_ok=True)
    saved_path = os.environ.get("PATH")
    os.environ["PATH"] = no_clangd                # an empty PATH entry: nothing to find
    try:
        check("no clangd: available() is False", available() is False)
        try:
            _require_supported_clangd()
            check("no clangd: query refuses cleanly", False, "no ClangdError raised")
        except ClangdError as exc:
            check("no clangd: query refuses cleanly", "no clangd" in str(exc), str(exc))
    finally:
        if saved_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = saved_path
        shutil.rmtree(no_clangd, ignore_errors=True)

    if available():
        failures += _selftest_live(os.path.join(os.environ.get("TMPDIR", "/tmp"),
                                                "clangd-selftest-live-%d" % os.getpid()))
        for name in ("live clangd round trip",):
            check(name, name not in failures)
        _shutdown_supervisor()
    else:
        print("  SKIP  live clangd round trip (no clangd on PATH)")

    print("%d check(s), %d failed" % (checks, len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    if "--daemon" in sys.argv[1:]:
        sys.exit(_daemon_main())
    sys.exit(main(sys.argv[1:]))
