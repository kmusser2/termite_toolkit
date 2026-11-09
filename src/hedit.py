#!/usr/bin/env python3
# TYPE: TOOLKIT
"""hedit.py - Hash-anchored line editor for AI agents.
# keywords: edit|editor|hash|anchor|hashline|stale|atomic|patch|diff|line|safe|reconcile|file|source|code|modify|change|rewrite|apply|read|grep|pattern|regex|match|search|window|context|discover|navigate|undo|revert|rollback|verify|receipt|batch|order-free
# examples: import $name; print($name.read_file("main.cpp")) | import $name; print($name.read_file("main.cpp", pattern="^def ", context=2)) | import $name; print($name.window($name.match("main.cpp", pattern="return"), before=1, after=1)) | import $name; print($name.replace("main.cpp", $name.read_file("main.cpp").lines[0].anchor, ["return 0;"]).to_json()) | import $name; print($name.verify($name.replace("main.cpp", $name.read_file("main.cpp").lines[0].anchor, ["return 0;"]))) | import $name; print($name.undo($name.replace("main.cpp", $name.read_file("main.cpp").lines[0].anchor, ["return 0;"]).undo)) | import $name; print($name.batch("main.cpp", [{"op": "replace", "anchor": $name.read_file("main.cpp").lines[0].anchor, "lines": ["x = 1"]}]).windows)

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

READING (start here) -- read_file() returns a ReadResult that *renders as the
anchored lines*, so you can emit it directly; no formatting needed:
    res = $name.read_file("foo.py")                         # whole file
    res = $name.read_file("foo.py", offset=40, limit=20)    # a page
    res = $name.read_file("foo.py", pattern="int|double", context=2)  # regex + window
    res = $name.read_file("foo.py", grep="TODO", context=1)          # literal + window
    print(res)      # one "LN#HASH|text" per line, e.g. 12#NKA|return 0;
  pattern= is a regex (re.search per line); grep= is a literal substring; give
  one, not both. context= keeps N lines around each match (spans merged).
  offset/limit (a page) cannot be combined with a filter. A filtered read that
  matched nothing prints  "# no matches for pattern='...'".
  ReadResult is a frozen dataclass: .lines (tuple of Line), .hits (matched
  line numbers; () means none), .windows ((start,end) ranges),
  .payload (the printable lines), .query (the filter that ran, or
  None for a whole-file/page read), .truncated, .next_offset,
  .total_lines. Read .query to tell a filter that matched nothing
  (query set, hits == ()) from a page (query None).

DISCOVER, THEN EXPAND (one expression, no second round-trip):
    m   = $name.match("foo.py", pattern="^def ")   # -> Match(path, hits, query)
    res = $name.window(m, before=1, after=2)        # each hit +/- context, merged
  match() finds line numbers only; window() returns the surrounding lines.

ANCHORS -- an anchor is "LN#HASH" (e.g. 12#NKA). Each Line prints as
"LN#HASH|text" and has .anchor -> "LN#HASH" for the edit ops. A Line's digest
(the hash) alone is NOT an anchor and will be rejected.

EDITING (stale-safe; nothing is written when an anchor is bad or stale):
    $name.replace("foo.py", "12#NKA", ["new line"])           -> EditResult(ok=True, ...)
    $name.replace("foo.py", "12#NKA", ["new"], preview=True)  -> EditResult(ok=True, preview=True, diff="...") (no write)
    $name.insert("foo.py", "12#NKA", ["x"], before=True)      # default inserts after
    $name.delete("foo.py", "12#NKA")                          # or an [anchor, end_anchor] range
    $name.batch("foo.py", [{"op": "insert", "anchor": "3#NKA", "lines": ["x"], "before": True}])
    $name.hash_line("def f():", 12)                           -> "NKA"

  A batch is a SET of position-keyed edits, not a sequence: every edit must
  have its own slot, so overlapping ranges, two inserts at one position, and an
  insert inside a range another edit consumes are rejected ("validation",
  nothing written). The outcome never depends on the order the edits are listed
  in. Two blocks at one point are one insert with several lines.

VERIFY -- LOOK ONLY IF YOU WANT TO (the edited text is never re-emitted):
  An EditResult carries .windows: the post-edit line ranges the call changed --
  one per edit, a delete's being its empty seam (start, start-1).
    r = $name.batch("foo.py", edits)      # the text is emitted HERE, once
    print($name.verify(r))                # those lines, fresh anchors, +1 context
    print($name.verify(r, context=0))     # exactly the touched lines
  The anchors are freshly computed, so editing continues from verify() output
  with no re-read. verify() raises UsageError on a preview (it wrote nothing).

UNDO -- the second call is a pointer, not a re-sample:
  Every write returns .undo, the path of a receipt. undo(receipt) restores the
  previous bytes EXACTLY (CRLF endings and a missing final newline included)
  and refuses with "stale" if the file changed since, so a late undo can never
  clobber another writer. One receipt per call, so a batch undoes as one step;
  receipts live in $TMPDIR/hedit-undo/ and undo() deletes them on success.
    r = $name.replace("foo.py", "12#NKA", ["x = 1"])   # writes: text emitted once
    if not r.ok: ...
    $name.undo(r.undo)                                 # put it back, no text

ERRORS -- an edit returns EditResult(ok=False, error=TAG, message=...) and
writes nothing. TAG is "stale" (anchor well-formed but the line changed) or
"validation" (bad batch edit shape); malformed args raise UsageError and
filesystem problems raise OSError. EditResult.to_json() is the wire shape: the
CLI prints that JSON on stdout ("usage"/"io" for its own boundary errors) and
exits 2 for "usage", 1 otherwise.

CLI:
  ./hedit.py read <file> [--offset N] [--limit M] [--pattern RE | --grep PAT]
                   [--context N] [--plain] [--json]
  ./hedit.py replace <file> <anchor> [<end-anchor>] [--new-text TEXT | --file F | -] [--preview]
  ./hedit.py insert <file> <anchor> [--before|--after] [--new-text TEXT | --file F | -] [--preview]
  ./hedit.py delete <file> <anchor> [<end-anchor>] [--preview]
  ./hedit.py batch <file> [--preview]   # JSON edits on stdin: op anchor end_anchor
                   # lines new_text before; --preview prints diff, no write
  ./hedit.py undo <receipt>             # revert the write that receipt records
  ./hedit.py --selftest
"""

import argparse
import difflib
import hashlib
import itertools
import json
import os
import re
import stat
import sys
import tempfile
from dataclasses import asdict, dataclass
from typing import Iterable, NamedTuple, Sequence, TypedDict

if __package__:
    from . import fio
else:
    import fio

__all__ = ["Edit", "EditResult", "Line", "Match", "ReadResult",
           "fnv1a_32", "hash_line", "parse_anchor", "read_lines", "read_file",
           "match", "window", "replace", "insert", "delete", "batch",
           "verify", "undo", "write_lines_atomic", "main"]

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
# Records & errors
# --------------------------------------------------------------------------

class UsageError(ValueError):
    """Caller/argument error: bad regex, bad anchor, or incompatible options.

    Subclasses ValueError so existing "except ValueError" callers still catch
    it. The CLI reports it as {"ok": false, "error": "usage"} (exit 2), as
    opposed to "io" (filesystem/size/encoding, exit 1).
    """


class Line(NamedTuple):
    """One file line: number ln (1-based), content hash digest, raw text.

    Prints as the canonical anchored form "LN#HASH|text" (e.g. 12#NKA|return 0;)
    so emitting a Line can never lose its line number. .anchor gives the
    "LN#HASH" string the edit ops accept; digest alone is NOT an anchor.
    """

    ln: int
    digest: str
    text: str

    @property
    def anchor(self) -> str:
        """The 'LN#HASH' anchor accepted by replace/insert/delete/batch."""
        return "%d#%s" % (self.ln, self.digest)

    def __str__(self) -> str:
        return "%d#%s|%s" % (self.ln, self.digest, self.text)

    __repr__ = __str__


class Match(NamedTuple):
    """Filter result from match(): path, matched line numbers hits (1-based),
    and query (e.g. "pattern='^def '", used in "no matches" messages). Pass it
    to window() to expand the hits into surrounding lines."""

    path: str
    hits: list[int]
    query: str


@dataclass(frozen=True, repr=False)
class ReadResult:
    """read_file()/window() result: the anchored lines plus their metadata.

    Renders as the payload block -- one "LN#HASH|text" per line (bare text when
    plain=True) -- or "# no matches for <query>" when a pattern/grep filter
    matched nothing, so it can be handed back directly with no formatting.
    Fields: lines (tuple of Line), hits (matched 1-based line numbers; ()
    means none), windows ((start, end) ranges), payload (the printable lines),
    query (the filter that ran, e.g. "pattern='^def '", or None for a
    whole-file/page read), truncated, next_offset, total_lines. Read .query to
    tell "a filter matched nothing" (query set, hits == ()) apart from "this
    was a page" (query None).
    """
    ok: bool
    lines: tuple[Line, ...]
    hits: tuple[int, ...]
    windows: tuple[tuple[int, int], ...]
    payload: tuple[str, ...]
    truncated: bool
    next_offset: int | None
    total_lines: int
    query: str | None

    def __str__(self) -> str:
        if self.payload:
            return "\n".join(self.payload)
        if self.query is not None:
            return "# no matches for %s" % self.query
        return ""

    __repr__ = __str__


@dataclass(frozen=True)
class EditResult:
    """One edit's outcome (replace/insert/delete/batch, and undo()).

    On success: windows -- the post-edit 1-based line ranges this call changed,
    one per edit, merged where they touch (a delete's window is empty,
    (start, start - 1), i.e. its seam), first_changed_line/last_changed_line
    (the extent of those windows; batch also sets edits_applied); and, unless
    preview=True, undo -- the path of a receipt that undo() reverses. On
    failure: error ("stale" | "validation") and message, with nothing written.
    path names the file the result refers to; verify() reads it.
    to_json() gives the CLI's documented JSON shape.
    """
    ok: bool
    error: str | None = None
    message: str | None = None
    preview: bool = False
    diff: str | None = None
    first_changed_line: int | None = None
    last_changed_line: int | None = None
    edits_applied: int | None = None
    windows: tuple[tuple[int, int], ...] = ()
    undo: str | None = None
    path: str | None = None

    def to_json(self) -> dict[str, object]:
        """The wire shape (JSON boundary conversion): only the keys carried."""
        out: dict[str, object] = {"ok": self.ok}
        for key in ("error", "message", "diff",
                    "first_changed_line", "last_changed_line", "edits_applied"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        if self.preview:
            out["preview"] = True
        if self.windows:
            out["windows"] = [list(w) for w in self.windows]
        if self.undo is not None:
            out["undo"] = self.undo
        if self.path is not None:
            out["path"] = self.path
        return out


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
    a = [text + "\n" for text in old_lines]
    b = [text + "\n" for text in new_lines]
    return "".join(difflib.unified_diff(a, b, fromfile=path, tofile=path))


def write_lines_atomic(path: str, lines: Sequence[str], ending: str = "\n") -> None:
    """Write lines to path atomically, preserving the file mode.

    The replace mechanism is fio.atomic_write's (its contract); this adds the
    line-joining convention (trailing `ending` after a non-empty line list).
    """
    text = ending.join(lines)
    if lines:
        text += ending
    fio.atomic_write(path, text)


# --------------------------------------------------------------------------
# Undo receipts (one per write call)
# --------------------------------------------------------------------------

_UNDO_DIR = "hedit-undo"


def _undo_dir() -> str:
    """The directory receipts live in (created on demand, inside the temp dir)."""
    d = os.path.join(tempfile.gettempdir(), _UNDO_DIR)
    os.makedirs(d, exist_ok=True)
    return d


def _sha256(data: bytes) -> str:
    """Hex SHA-256 of data -- the fingerprint undo() compares before restoring."""
    return hashlib.sha256(data).hexdigest()


def _atomic_write_bytes(path: str, data: bytes) -> None:
    """Replace path with data atomically, preserving the file mode.

    Byte-level on purpose: fio.atomic_write is text-only, and undo must restore
    a file exactly -- a missing final newline or non-UTF-8 bytes would not
    survive a decode/encode round trip.
    """
    tmp = path + ".hedit-undo"
    with open(tmp, "wb") as f:
        f.write(data)
    try:
        os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode))
    except OSError:
        pass
    os.replace(tmp, path)


def _write_with_undo(path: str, lines: Sequence[str], ending: str) -> str:
    """Write lines atomically and leave an undo receipt; returns its path.

    The pre-image is stored byte-exact next to the receipt, and the receipt
    records the sha256 of what the write left, so undo() can refuse when the
    target changed in the meantime. If the write fails, the pre-image is
    removed and nothing is left behind.
    """
    with open(path, "rb") as f:
        before = f.read()
    stem = "%s-%s-" % (os.path.basename(path) or "file",
                       _sha256(os.path.abspath(path).encode("utf-8"))[:8])
    d = _undo_dir()
    fd, pre_image = tempfile.mkstemp(dir=d, prefix=stem, suffix=".before")
    with os.fdopen(fd, "wb") as f:
        f.write(before)
    receipt = ""
    try:
        write_lines_atomic(path, lines, ending)
        with open(path, "rb") as f:
            after = f.read()
        record = {"path": os.path.abspath(path), "before": pre_image,
                  "sha256_before": _sha256(before), "sha256_after": _sha256(after)}
        fd, receipt = tempfile.mkstemp(dir=d, prefix=stem, suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(record, f)
    except BaseException:
        for leftover in (pre_image, receipt):
            if leftover:
                try:
                    os.unlink(leftover)
                except OSError:
                    pass
        raise
    return receipt


def _discard(receipt: str) -> None:
    """Delete a receipt and the pre-image it points at (best effort)."""
    try:
        with open(receipt, "r", encoding="utf-8") as f:
            pre_image = json.load(f).get("before")
    except (OSError, ValueError):
        pre_image = None
    for p in (pre_image, receipt):
        if p:
            try:
                os.unlink(p)
            except OSError:
                pass


def undo(receipt: str) -> EditResult:
    """Revert the write that produced receipt; returns an EditResult.

    Restores the previous bytes exactly -- CRLF endings, a missing final
    newline, and non-UTF-8 content included -- and only if the target is still
    byte-identical to what that write left, so a late undo can never clobber a
    later change (error="stale"). An unknown or preview-only receipt is
    error="validation". One receipt per write call, so a batch undoes as one
    step. Nothing here re-emits or re-parses the edited text: the pre-image is
    a file, and the edit's own text was emitted once, when it was made.
    """
    if not receipt:
        return EditResult(ok=False, error="validation",
                          message="no receipt: previews and rejected edits write nothing")
    try:
        with open(receipt, "r", encoding="utf-8") as f:
            record = json.load(f)
        path = record["path"]
        pre_image = record["before"]
        expected = record["sha256_after"]
    except FileNotFoundError:
        return EditResult(ok=False, error="validation",
                          message="unknown receipt %r" % (receipt,))
    except (OSError, ValueError, KeyError) as e:
        return EditResult(ok=False, error="validation",
                          message="unreadable receipt %r: %s" % (receipt, e))
    try:
        with open(path, "rb") as f:
            current = f.read()
    except OSError as e:
        return EditResult(ok=False, path=path, error="stale",
                          message="%s: %s" % (path, e))
    if _sha256(current) != expected:
        return EditResult(ok=False, path=path, error="stale",
                          message="%s changed since that edit; refusing to undo" % path)
    try:
        with open(pre_image, "rb") as f:
            before = f.read()
    except OSError as e:
        return EditResult(ok=False, path=path, error="validation",
                          message="pre-image for %r is gone: %s" % (receipt, e))
    _atomic_write_bytes(path, before)
    _discard(receipt)
    return EditResult(ok=True, path=path)


# --------------------------------------------------------------------------
# Read
# --------------------------------------------------------------------------

def read_file(path: str, offset: int | None = None, limit: int | None = None,
              grep: str | None = None, context: int | None = None,
              plain: bool = False, pattern: str | None = None) -> ReadResult:
    """Read a file, returning a ReadResult that prints as "LN#HASH|text" lines.

    Pick one mode:
      * whole file      -- read_file(path)
      * a page          -- read_file(path, offset=40, limit=20)
      * filtered window -- read_file(path, pattern=RE, context=N)
                           read_file(path, grep=LITERAL, context=N)

    pattern= is a regex (re.search on each line); grep= is a literal substring.
    context= keeps N lines on each side of a match (spans that touch are merged).
    Give pattern or grep, never both. offset/limit cannot be combined with a
    filter, and context= is only meaningful together with one.

    Returns a ReadResult (a frozen dataclass that str()s as the payload):
    .lines (tuple of Line), .hits (matched 1-based line numbers), .windows
    ((start, end) ranges), .payload (the printable lines),
    .query (the filter that ran, or None for a page), .truncated,
    .next_offset, .total_lines. In filter mode hits == () means no
    match; in page mode hits is always () (read .query to distinguish).
    Raises UsageError on malformed or incompatible arguments.
    """
    if pattern is not None and grep is not None:
        raise UsageError("give pattern= (regex) or grep= (literal), not both")
    if pattern is not None or grep is not None:
        if offset is not None or limit is not None:
            raise UsageError("offset/limit cannot be combined with pattern/grep; "
                             "use context=N for a window around matches")
        ctx = context or 0
        return window(match(path, pattern=pattern, grep=grep),
                      before=ctx, after=ctx, plain=plain)
    if context is not None:
        raise UsageError("context= only applies with pattern= or grep=")
    lines, _ = read_lines(path)
    total = len(lines)
    start, end = 0, total
    if offset is not None:
        start = max(0, offset - 1)
    if limit is not None:
        end = min(total, start + limit)
    records = _records(lines, range(start, end))
    truncated = end < total
    return _result(records, hits=[], windows=[(start + 1, end)] if records else [],
                   total=total, truncated=truncated,
                   next_offset=end + 1 if truncated else None,
                   plain=plain, query=None)


def match(path: str, pattern: str | None = None,
          grep: str | None = None) -> Match:
    """Find matching line numbers in a file; returns Match(path, hits, query).

    Uses exactly one of pattern= (a regex, re.search per line) or grep= (a
    literal substring); giving neither or both raises UsageError, as does an
    invalid regex. hits are 1-based line numbers ([] means no match). The file
    body is not returned -- pass the Match to window() to get those lines with
    context. This is the discovery half of read_file(pattern=...).
    """
    if (pattern is None) == (grep is None):
        raise UsageError("match() needs exactly one of pattern= or grep=")
    lines, _ = read_lines(path)
    if pattern is not None:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            raise UsageError("invalid pattern %r: %s" % (pattern, e)) from e
        hits = [i + 1 for i, ln in enumerate(lines) if rx.search(ln)]
        query = "pattern=%r" % pattern
    else:
        hits = [i + 1 for i, ln in enumerate(lines) if grep in ln]
        query = "grep=%r" % grep
    return Match(path, hits, query)


def window(m: Match, before: int = 0, after: int = 0,
           plain: bool = False) -> ReadResult:
    """Expand a Match's hits to surrounding lines and return them.

    Each hit is widened to [hit - before, hit + after]; windows that overlap or
    touch are merged, so scattered hits do not drag in the lines between them.
    before and after need not be equal. Returns the same ReadResult as
    read_file(): .lines carry their true line numbers, so anchors stay
    valid across the gaps. Raises UsageError if before/after is negative.
    """
    if before < 0 or after < 0:
        raise UsageError("before/after must be >= 0")
    lines, _ = read_lines(m.path)
    total = len(lines)
    wins = _merge_windows(m.hits, before, after, total)
    records = _records(lines, [i for (s, e) in wins for i in range(s - 1, e)])
    return _result(records, hits=list(m.hits), windows=wins, total=total,
                   truncated=False, next_offset=None, plain=plain, query=m.query)


def _merge_spans(spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge spans that overlap or touch into disjoint [start, end] ranges,
    dropping empty or inverted ones (a delete's window is one)."""
    out: list[tuple[int, int]] = []
    for s, e in spans:
        if s > e:
            continue
        if out and s <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _merge_windows(hits: Sequence[int], before: int, after: int,
                   total: int) -> list[tuple[int, int]]:
    """Merge each 1-based hit +/- context into disjoint [start, end] windows."""
    return _merge_spans((max(1, h - before), min(total, h + after)) for h in hits)


def _records(lines: Sequence[str], indices: Iterable[int]) -> list[Line]:
    """Build Line records (1-based ln, content hash, text) for the given indices."""
    return [Line(i + 1, hash_line(lines[i], i + 1), lines[i]) for i in indices]


def _result(records: Sequence[Line], hits: Iterable[int],
            windows: Iterable[tuple[int, int]], total: int, truncated: bool,
            next_offset: int | None, plain: bool, query: str | None) -> ReadResult:
    """Assemble a ReadResult from records plus its metadata."""
    payload = tuple(r.text for r in records) if plain else \
        tuple("%d#%s|%s" % (r.ln, r.digest, r.text) for r in records)
    return ReadResult(ok=True, lines=tuple(records), hits=tuple(hits),
                      windows=tuple(windows), payload=payload,
                      truncated=truncated, next_offset=next_offset,
                      total_lines=total, query=query)


# --------------------------------------------------------------------------
# Edit primitives (stale-safe, order-free)
#
# Every edit path -- replace/insert/delete and batch -- goes through one
# implementation: each request is validated against the file as it was read,
# the validated edits are checked for slot conflicts, and the new file is built
# by a single ascending splice. So the outcome depends on the set of anchors
# alone: the order the edits are listed in cannot change a byte, and a pair of
# edits that would need an order chosen is rejected instead of guessed.
# --------------------------------------------------------------------------

class _Spec(NamedTuple):
    """One edit request (op, anchor, end_anchor, lines, before) -- the
    pre-validation form that _plan() turns into a _Planned."""
    op: str
    anchor: str
    end_anchor: str | None
    lines: tuple[str, ...]
    before: bool


class _Planned(NamedTuple):
    """One validated edit in pre-edit coordinates: consume old lines
    [start, stop) and emit lines. An insert consumes nothing (start == stop)."""
    op: str
    start: int
    stop: int
    lines: tuple[str, ...]
    anchor: str


def _validate_anchor(lines, anchor):
    """Return (index) if anchor's line+hash matches current content, else None."""
    parsed = parse_anchor(anchor)
    if parsed is None:
        raise UsageError("invalid anchor %r (want LN#HASH, e.g. 12#NKA)" % (anchor,))
    ln, h = parsed
    if ln < 1 or ln > len(lines):
        return None
    current = hash_line(lines[ln - 1], ln)
    if current != h:
        return None
    return ln - 1


def _as_lines(new_lines: str | Sequence[str]) -> tuple[str, ...]:
    """Normalize an edit's content: a bare str is ONE line, never split.

    (Splitting a str here once corrupted files character by character, so the
    API str means exactly what it says: one line. Use a sequence for several.)
    """
    if isinstance(new_lines, str):
        new_lines = [new_lines]
    return tuple(nl.rstrip("\r\n") for nl in new_lines)


def _lines_from_text(text: str) -> list[str]:
    """Split file or stdin content into lines: a trailing newline terminates
    the last line instead of adding an empty one -- the inverse of the join in
    write_lines_atomic, which appends exactly one ending."""
    return text.splitlines()


def _plan(lines: Sequence[str], spec: _Spec) -> _Planned | EditResult:
    """Validate one edit against the current lines.

    Returns its _Planned, or a stale EditResult when an anchor no longer
    matches ('stale': the line changed since it was read). Raises UsageError
    for a malformed anchor or a reversed range.
    """
    idx = _validate_anchor(lines, spec.anchor)
    if idx is None:
        return _stale(spec.anchor)
    end_idx = idx
    if spec.end_anchor:
        e = _validate_anchor(lines, spec.end_anchor)
        if e is None:
            return _stale(spec.end_anchor)
        if e < idx:
            raise UsageError("end_anchor precedes start anchor")
        end_idx = e
    if spec.op == "insert":
        start = idx if spec.before else idx + 1
        return _Planned("insert", start, start, spec.lines, spec.anchor)
    return _Planned(spec.op, idx, end_idx + 1, spec.lines, spec.anchor)


def _position_key(plan: _Planned) -> tuple[int, int]:
    """Splice order: by start, with an insert ahead of a range that begins at
    the same line (so 'insert before N' lands before N's replacement)."""
    return (plan.start, plan.stop)


def _conflict(plans: Sequence[_Planned]) -> str | None:
    """Why these edits cannot all be applied, or None.

    Every edit is position-keyed, so a pair that would need an ordering
    decision is refused rather than resolved: two inserts at one position,
    an insert inside a range another edit consumes, or overlapping ranges.
    """
    for a, b in itertools.combinations(sorted(plans, key=_position_key), 2):
        if a.start == a.stop and b.start == b.stop:
            if a.start == b.start:
                return ("edits at %s and %s insert at the same position; "
                        "combine them into one edit's lines" % (a.anchor, b.anchor))
        elif a.start == a.stop or b.start == b.stop:
            point, span = (a, b) if a.start == a.stop else (b, a)
            if span.start < point.start < span.stop:
                return ("the insert at %s falls inside the range replaced or "
                        "deleted by %s; split them into separate calls"
                        % (point.anchor, span.anchor))
        elif max(a.start, b.start) < min(a.stop, b.stop):
            return ("edits at %s and %s cover overlapping lines; "
                    "split them into separate calls" % (a.anchor, b.anchor))
    return None


def _splice(lines: Sequence[str], plans: Sequence[_Planned]
            ) -> tuple[list[str], list[tuple[int, int]]]:
    """Build the edited file by splicing the plans into lines in one ascending
    pass. Returns (new_lines, windows), each window being the post-edit 1-based
    range that edit produced -- empty, (start, start - 1), for a delete."""
    out: list[str] = []
    windows: list[tuple[int, int]] = []
    pos, cur = 1, 0     # next output line number (1-based); input cursor (0-based)
    for plan in sorted(plans, key=_position_key):
        if plan.start > cur:
            out.extend(lines[cur:plan.start])
            pos += plan.start - cur
        windows.append((pos, pos + len(plan.lines) - 1))
        out.extend(plan.lines)
        pos += len(plan.lines)
        cur = plan.stop
    out.extend(lines[cur:])
    return out, windows


def _apply(path: str, specs: Sequence[_Spec], preview: bool = False) -> EditResult:
    """Validate and apply edit specs atomically; nothing is written on error.

    A stale anchor or a slot conflict returns EditResult(ok=False) with the
    file untouched. On success the result carries every changed window in
    post-edit coordinates and, unless preview=True, an undo receipt.
    """
    lines, ending = read_lines(path)
    plans: list[_Planned] = []
    for spec in specs:
        plan = _plan(lines, spec)
        if isinstance(plan, EditResult):
            return plan
        plans.append(plan)
    if not plans:
        return EditResult(ok=True, path=path, edits_applied=0)
    problem = _conflict(plans)
    if problem is not None:
        return EditResult(ok=False, path=path, error="validation", message=problem)
    new_lines, windows = _splice(lines, plans)
    first = min(w[0] for w in windows)
    last = max(max(w) for w in windows)
    extent = {"path": path, "first_changed_line": first,
              "last_changed_line": last, "edits_applied": len(plans),
              "windows": tuple(windows)}
    if preview:
        return EditResult(ok=True, preview=True,
                          diff=_unified_diff(path, lines, new_lines), **extent)
    receipt = _write_with_undo(path, new_lines, ending)
    return EditResult(ok=True, undo=receipt, **extent)


def replace(path: str, anchor: str, new_lines: str | Sequence[str],
            end_anchor: str | None = None, preview: bool = False) -> EditResult:
    """Replace the line(s) at anchor (through end_anchor, inclusive).

    new_lines is a sequence of lines, or a str meaning exactly ONE line.
    preview=True returns the diff and writes nothing.
    """
    return _apply(path, [_Spec("replace", anchor, end_anchor,
                               _as_lines(new_lines), False)], preview)


def insert(path: str, anchor: str, new_lines: str | Sequence[str],
           before: bool = False, preview: bool = False) -> EditResult:
    """Insert new_lines before or after the anchor line (default: after)."""
    return _apply(path, [_Spec("insert", anchor, None,
                               _as_lines(new_lines), before)], preview)


def delete(path: str, anchor: str, end_anchor: str | None = None,
           preview: bool = False) -> EditResult:
    """Delete the line(s) at anchor (through end_anchor, inclusive)."""
    return _apply(path, [_Spec("delete", anchor, end_anchor, (), False)], preview)


def _stale(anchor: str) -> EditResult:
    return EditResult(ok=False, error="stale",
                      message="anchor %s: stale" % anchor)


def batch(path: str, edits: Sequence[Edit], preview: bool = False) -> EditResult:
    """Apply multiple edits atomically and order-free.

    Every key and op is validated first, and every edit must have its own slot:
    overlapping ranges, two inserts at one position, and an insert inside a
    range another edit consumes are validation errors (nothing is written).
    The result therefore depends on the set of anchors alone -- reversing the
    list cannot change a byte. Two blocks at one point are one edit with
    several lines, not two inserts.
    """
    specs: list[_Spec] = []
    for e in edits:
        unknown = set(e) - _EDIT_KEYS
        if unknown:
            return EditResult(ok=False, path=path, error="validation",
                              message="unknown key(s) %s (want %s)"
                                      % (", ".join(sorted(repr(k) for k in unknown)),
                                         "|".join(sorted(_EDIT_KEYS))))
        op = e.get("op")
        if op not in ("replace", "insert", "delete"):
            return EditResult(ok=False, path=path, error="validation",
                              message="unknown op %r (want replace|insert|delete)" % (op,))
        before = e.get("before", False)
        if before and op != "insert":
            return EditResult(ok=False, path=path, error="validation",
                              message="'before' is only valid for insert edits")
        anchor = e.get("anchor")
        if not anchor:
            return EditResult(ok=False, path=path, error="validation",
                              message="edit missing 'anchor'")
        if op in ("replace", "insert"):
            raw = e.get("lines", e.get("new_text"))
            if raw is None:
                return EditResult(ok=False, path=path, error="validation",
                                  message="%s edit missing 'lines' (or 'new_text')" % op)
            if isinstance(raw, str):
                raw = _lines_from_text(raw)
            new_lines = tuple(nl.rstrip("\r\n") for nl in raw)
        else:
            new_lines = ()
        specs.append(_Spec(op, anchor, e.get("end_anchor"), new_lines, before))
    return _apply(path, specs, preview)


def verify(result: EditResult, context: int = 1) -> ReadResult:
    """Re-read the lines a committed edit changed -- every region, in one call.

    The result already carries the regions (EditResult.windows), so this is the
    on-demand half: looking costs nothing until you ask. Returns the same
    ReadResult as read_file() -- the touched lines with FRESH anchors, so
    editing can continue at once -- with each region widened by context lines
    (a delete's empty window becomes its seam) and touching ones merged.

    Raises UsageError for a preview (it wrote nothing); a failed edit returns
    an empty ReadResult. undo() results report no windows, so verify() on one
    is empty: there is nothing to look at until you read the file.
    """
    if context < 0:
        raise UsageError("context must be >= 0")
    if result.preview:
        raise UsageError("verify reads a committed edit; a preview wrote nothing")
    if not result.ok or result.path is None or not result.windows:
        return ReadResult(ok=False, lines=(), hits=(), windows=(), payload=(),
                          truncated=False, next_offset=None, total_lines=0,
                          query=None)
    lines, _ = read_lines(result.path)
    total = len(lines)
    wins = _merge_spans((max(1, s - context), min(total, max(s, e) + context))
                        for s, e in result.windows)
    records = _records(lines, [i for (s, e) in wins for i in range(s - 1, e)])
    return _result(records, hits=[], windows=wins, total=total, truncated=False,
                   next_offset=None, plain=False, query=None)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _read_args(parser, args):
    path = args.file
    try:
        res = read_file(path, offset=args.offset, limit=args.limit,
                        grep=args.grep, context=args.context, plain=args.plain,
                        pattern=args.pattern)
    except UsageError as e:
        print(json.dumps({"ok": False, "error": "usage", "message": str(e)}))
        return 2
    except Exception as e:
        print(json.dumps({"ok": False, "error": "io", "message": str(e)}))
        return 1
    if args.json:
        print(json.dumps({"ok": True, "file": path,
                          "lines": [{"line": item.ln, "anchor": item.anchor, "text": item.text}
                                    for item in res.lines],
                          "hits": res.hits, "windows": res.windows,
                          "query": res.query,
                          "truncated": res.truncated,
                          "next_offset": res.next_offset,
                          "total_lines": res.total_lines}))
    else:
        for ln in res.payload:
            print(ln)
        if not res.payload and (args.pattern is not None or args.grep is not None):
            print("# no matches", file=sys.stderr)
        if res.truncated:
            print("# ... truncated (next offset %d)" % res.next_offset,
                  file=sys.stderr)
    return 0


def _content_from(args):
    new_text = getattr(args, "new_text", None)
    if new_text is not None:
        return _lines_from_text(new_text)
    file_input = getattr(args, "file_input", None)
    if file_input:
        with open(file_input, "r", encoding="utf-8") as f:
            return _lines_from_text(f.read())
    # stdin
    return _lines_from_text(sys.stdin.read())


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
    except UsageError as e:
        print(json.dumps({"ok": False, "error": "usage", "message": str(e)}))
        return 2
    except Exception as e:
        print(json.dumps({"ok": False, "error": "io", "message": str(e)}))
        return 1
    if preview:
        if as_json:
            print(json.dumps(res.to_json()))
        else:
            print(res.diff or "(no changes)")
        return 0 if res.ok else 1
    print(json.dumps(res.to_json()))
    return 0 if res.ok else 1


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
            print(json.dumps(res.to_json()))
        else:
            print(res.diff or "(no changes)")
        return 0 if res.ok else 1
    print(json.dumps(res.to_json()))
    return 0 if res.ok else 1


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
    a2 = "%d#%s" % (res.lines[1].ln, res.lines[1].digest)  # line 2 anchor
    r = replace(p, a2, ["TWO"])
    check("replace ok", r.ok is True)
    with open(p) as f:
        check("replace content", f.read() == "one\nTWO\nthree\n")

    # String-arg regression (2026-08-21): plain str new_lines must be ONE
    # line; historically it was iterated char-by-char (silent corruption).
    _ow(p, "one\ntwo\nthree\n")
    res = read_file(p)
    a2 = "%d#%s" % (res.lines[1].ln, res.lines[1].digest)
    replace(p, a2, "TWO-STRING")
    with open(p) as f:
        check("replace str arg = one line", f.read() == "one\nTWO-STRING\nthree\n")
    res = read_file(p)
    a1 = "%d#%s" % (res.lines[0].ln, res.lines[0].digest)
    insert(p, a1, "ZERO-STRING")  # after anchor 1
    with open(p) as f:
        check("insert str arg = one line", f.read() == "one\nZERO-STRING\nTWO-STRING\nthree\n")

    # Stale detection: change file, then old anchor must be rejected
    _ow(p, "one\nCHANGED\nthree\n")
    r = replace(p, a2, ["nope"])
    check("stale rejected", r.ok is False and r.error == "stale")

    # Insert before/after
    _ow(p, "one\ntwo\n")
    res = read_file(p)
    a1 = "%d#%s" % (res.lines[0].ln, res.lines[0].digest)
    insert(p, a1, ["zero"], before=True)
    with open(p) as f:
        check("insert before", f.read() == "zero\none\ntwo\n")

    # Batch atomicity: one stale edit -> nothing applied
    _ow(p, "a\nb\nc\n")
    res = read_file(p)
    a1 = "%d#%s" % (res.lines[0].ln, res.lines[0].digest)
    a3 = "%d#%s" % (res.lines[2].ln, res.lines[2].digest)
    # Corrupt line 3
    _ow(p, "a\nb\nCORRUPT\n")
    r = batch(p, [{"op": "replace", "anchor": a1, "lines": ["A"]},
                  {"op": "replace", "anchor": a3, "lines": ["C"]}])
    check("batch stale aborts all", r.error == "stale")
    with open(p) as f:
        check("batch left file untouched", f.read() == "a\nb\nCORRUPT\n")

    # Atomic write leaves no temp file
    check("no leftover temp", not os.path.exists(p + ".hedit-new"))

    # CLI delete path (regression: delete used to crash on missing new_text)
    _ow(p, "x\ny\nz\n")
    res = read_file(p)
    ay = "%d#%s" % (res.lines[1].ln, res.lines[1].digest)
    fake = argparse.Namespace(file=p, anchor=ay, end_anchor=None,
                              new_text=None, file_input=None)
    check("delete CLI path", _write_cmd(argparse.ArgumentParser(), fake, "delete") == 0)
    with open(p) as f:
        check("delete CLI removed line", f.read() == "x\nz\n")

    # Batch: new_text string alias works
    _ow(p, "a\nb\nc\n")
    res = read_file(p)
    a1 = "%d#%s" % (res.lines[0].ln, res.lines[0].digest)
    r = batch(p, [{"op": "replace", "anchor": a1, "new_text": "A1\nA2"}])
    check("batch new_text alias", r.ok is True)
    with open(p) as f:
        check("batch new_text content", f.read() == "A1\nA2\nb\nc\n")

    # Batch: missing lines on replace -> validation error, NOT silent delete
    _ow(p, "a\nb\nc\n")
    res = read_file(p)
    a1 = "%d#%s" % (res.lines[0].ln, res.lines[0].digest)
    r = batch(p, [{"op": "replace", "anchor": a1}])
    check("batch missing lines rejected",
          r.ok is False and r.error == "validation")
    with open(p) as f:
        check("batch no silent delete", f.read() == "a\nb\nc\n")

    # Batch: unknown op rejected
    r = batch(p, [{"op": "frobnicate", "anchor": a1, "lines": ["Q"]}])
    check("batch unknown op rejected",
          r.ok is False and r.error == "validation")

    # Batch: unknown key rejected (never silently ignored)
    r = batch(p, [{"op": "replace", "anchor": a1, "lines": ["Q"], "befor": True}])
    check("batch unknown key rejected",
          r.ok is False and r.error == "validation")
    with open(p) as f:
        check("batch unknown key wrote nothing", f.read() == "a\nb\nc\n")

    # Batch: insert-before honored
    _ow(p, "a\nb\nc\n")
    res = read_file(p)
    a3 = "%d#%s" % (res.lines[2].ln, res.lines[2].digest)
    r = batch(p, [{"op": "insert", "anchor": a3, "lines": ["before-c"], "before": True}])
    check("batch insert-before ok", r.ok is True)
    with open(p) as f:
        check("batch insert-before content", f.read() == "a\nb\nbefore-c\nc\n")

    # Batch: insert still defaults to after (unchanged behavior)
    _ow(p, "a\nb\nc\n")
    res = read_file(p)
    a1 = "%d#%s" % (res.lines[0].ln, res.lines[0].digest)
    r = batch(p, [{"op": "insert", "anchor": a1, "lines": ["after-a"]}])
    check("batch insert-after default ok", r.ok is True)
    with open(p) as f:
        check("batch insert-after default content", f.read() == "a\nafter-a\nb\nc\n")

    # Batch: 'before' on a non-insert op rejected
    _ow(p, "a\nb\nc\n")
    res = read_file(p)
    a1 = "%d#%s" % (res.lines[0].ln, res.lines[0].digest)
    r = batch(p, [{"op": "delete", "anchor": a1, "before": True}])
    check("batch before on non-insert rejected",
          r.ok is False and r.error == "validation")
    with open(p) as f:
        check("batch before-invalid wrote nothing", f.read() == "a\nb\nc\n")

    # --- patch-preview ---
    _ow(p, "one\ntwo\nthree\n")
    res = read_file(p)
    a2 = "%d#%s" % (res.lines[1].ln, res.lines[1].digest)
    r = replace(p, a2, ["TWO", "2.5"], preview=True)
    check("preview replace ok", r.ok is True and r.preview is True)
    check("preview replace diff -two", "-two\n" in (r.diff or ""))
    check("preview replace diff +TWO", "+TWO\n" in (r.diff or ""))
    with open(p) as f:
        check("preview replace wrote nothing", f.read() == "one\ntwo\nthree\n")

    _ow(p, "one\ntwo\n")
    res = read_file(p)
    a1 = "%d#%s" % (res.lines[0].ln, res.lines[0].digest)
    r = insert(p, a1, ["zero"], before=True, preview=True)
    check("preview insert ok", r.ok is True)
    check("preview insert diff +zero", "+zero\n" in (r.diff or ""))
    with open(p) as f:
        check("preview insert wrote nothing", f.read() == "one\ntwo\n")

    _ow(p, "one\ntwo\nthree\n")
    res = read_file(p)
    a2 = "%d#%s" % (res.lines[1].ln, res.lines[1].digest)
    r = delete(p, a2, preview=True)
    check("preview delete ok", r.ok is True)
    check("preview delete diff -two", "-two\n" in (r.diff or ""))
    with open(p) as f:
        check("preview delete wrote nothing", f.read() == "one\ntwo\nthree\n")

    _ow(p, "a\nb\nc\n")
    res = read_file(p)
    a1 = "%d#%s" % (res.lines[0].ln, res.lines[0].digest)
    a3 = "%d#%s" % (res.lines[2].ln, res.lines[2].digest)
    r = batch(p, [{"op": "replace", "anchor": a1, "lines": ["A"]},
                  {"op": "insert", "anchor": a3, "lines": ["d"]}], preview=True)
    check("preview batch ok", r.ok is True and r.edits_applied == 2)
    check("preview batch diff +A", "+A\n" in (r.diff or ""))
    check("preview batch diff +d", "+d\n" in (r.diff or ""))
    with open(p) as f:
        check("preview batch wrote nothing", f.read() == "a\nb\nc\n")

    _ow(p, "a\nb\nc\n")
    res = read_file(p)
    a1 = "%d#%s" % (res.lines[0].ln, res.lines[0].digest)
    _ow(p, "X\nb\nc\n")
    r = batch(p, [{"op": "replace", "anchor": a1, "lines": ["A"]}], preview=True)
    check("preview stale rejected", r.ok is False and r.error == "stale")
    with open(p) as f:
        check("preview stale wrote nothing", f.read() == "X\nb\nc\n")

    # CLI preview path (regression guard for _write_cmd wiring)
    _ow(p, "one\ntwo\n")
    res = read_file(p)
    a2 = "%d#%s" % (res.lines[1].ln, res.lines[1].digest)
    fake = argparse.Namespace(file=p, anchor=a2, end_anchor=None, new_text="TWO",
                              file_input=None, before=False, preview=True, json=False)
    check("CLI preview rc", _write_cmd(argparse.ArgumentParser(), fake, "replace") == 0)
    with open(p) as f:
        check("CLI preview wrote nothing", f.read() == "one\ntwo\n")

    # --- read_file: pattern regex, hits/windows, ReadResult self-rendering ---
    _ow(p, "import os\ndef alpha():\n    return 1\n\nx = 10\ny = 20\ndef beta():\n    return 2\n\n")
    r1 = read_file(p, pattern=r"^def ")
    check("pattern hits", r1.hits == (2, 7))
    check("pattern no context = hit lines", [row.ln for row in r1.lines] == [2, 7])
    check("grep literal still works", read_file(p, grep="def").hits == (2, 7))
    r2 = read_file(p, pattern=r"^def ", context=1)
    check("context windows skip gap", r2.windows == ((1, 3), (6, 8)))
    check("touching windows merge", read_file(p, pattern=r"^def ", context=2).windows == ((1, 9),))
    check("str(ReadResult) = payload", str(r2) == "\n".join(r2.payload))
    check("ReadResult frozen dataclass", isinstance(r2, ReadResult) and r2.total_lines == 9)

    # Line renders as a usable anchor, never just a hash
    ln0 = r1.lines[0]
    check("Line repr is anchored",
          repr(ln0) == str(ln0) == "%d#%s|%s" % (ln0.ln, ln0.digest, ln0.text))
    check("Line.anchor composes", ln0.anchor == "%d#%s" % (ln0.ln, ln0.digest))
    a, b, c = ln0
    check("Line unpacks as tuple", (a, b, c) == (ln0.ln, ln0.digest, ln0.text))

    # match()/window() composition
    m2 = match(p, grep="def")
    check("match fields", m2.path == p and m2.hits == [2, 7] and m2.query.startswith("grep="))
    w2 = window(m2, before=1, after=1)
    check("window merges", w2.windows == ((1, 3), (6, 8)))
    check("window renders", str(w2) == "\n".join(w2.payload))

    # no-match is explicit
    rn = read_file(p, pattern=r"zzz_nope")
    check("no match: empty hits/lines/windows",
          rn.hits == () and rn.lines == () and rn.windows == ())
    check("no match: not truncated, no next",
          rn.truncated is False and rn.next_offset is None)
    check("no match renders sentinel", str(rn) == "# no matches for pattern='zzz_nope'")

    # .query disambiguates "filter matched nothing" from "this was a page"
    page = read_file(p, offset=1, limit=2)
    check("page: query None, hits ()", page.query is None and page.hits == ())
    check("page: windows is the page range", page.windows == ((1, 2),))
    check("filter: query set on no match", rn.query is not None)
    check("query survives copy",
          asdict(rn)["query"] == rn.query and asdict(page)["query"] is None)

    # usage errors are UsageError (-> CLI "usage"), not generic errors
    for name, fn in [
        ("bad regex", lambda: read_file(p, pattern="(")),
        ("both filters", lambda: read_file(p, pattern="x", grep="y")),
        ("offset+filter", lambda: read_file(p, pattern="x", offset=1)),
        ("context without filter", lambda: read_file(p, context=1)),
        ("match needs one filter", lambda: match(p)),
        ("match rejects both", lambda: match(p, pattern="x", grep="y")),
        ("window negative before", lambda: window(match(p, grep="def"), before=-1)),
        ("malformed anchor", lambda: replace(p, "ACA", ["x"])),
    ]:
        try:
            fn()
            check("UsageError: %s" % name, False)
        except UsageError:
            check("UsageError: %s" % name, True)

    # a Line's anchor round-trips straight into an edit
    rr = replace(p, ln0.anchor, ["# replaced"])
    check("Line.anchor round-trips into replace", rr.ok is True)
    with open(p) as f:
        check("Line.anchor replace content", f.read().split("\n")[1] == "# replaced")

    # --- batch: slot uniqueness (list order is never a decision to make) ---
    _ow(p, "a\nb\nc\nd\ne\n")
    res = read_file(p)
    A, B, C, D, E = (res.lines[i].anchor for i in range(5))
    rejected = {
        "overlapping ranges": [
            {"op": "replace", "anchor": B, "end_anchor": D, "lines": ["X"]},
            {"op": "replace", "anchor": C, "lines": ["Y"]}],
        "two inserts at one anchor": [
            {"op": "insert", "anchor": A, "lines": ["one"]},
            {"op": "insert", "anchor": A, "lines": ["two"]}],
        "insert sharing a slot with insert-before": [
            {"op": "insert", "anchor": A, "lines": ["after-a"]},
            {"op": "insert", "anchor": B, "lines": ["before-b"], "before": True}],
        "insert inside a deleted range": [
            {"op": "delete", "anchor": B, "end_anchor": D},
            {"op": "insert", "anchor": C, "lines": ["in-the-hole"]}],
    }
    for label, edits in rejected.items():
        r = batch(p, edits)
        check("batch rejects %s" % label, r.ok is False and r.error == "validation")
        check("rejected batch wrote nothing (%s)" % label,
              open(p).read() == "a\nb\nc\nd\ne\n")

    # adjacency that is NOT ambiguous still applies, deterministically
    r = batch(p, [{"op": "insert", "anchor": A, "lines": ["after-a"]},
                  {"op": "replace", "anchor": B, "lines": ["B!"]}])
    check("batch allows insert after N beside replace N+1",
          r.ok is True and open(p).read() == "a\nafter-a\nB!\nc\nd\ne\n")

    # order-freedom: disjoint edits -> same bytes whatever the list order
    _ow(p, "a\nb\nc\nd\ne\n")
    res = read_file(p)
    A, C, D, E = (res.lines[i].anchor for i in (0, 2, 3, 4))
    edits = [{"op": "replace", "anchor": A, "lines": ["A1", "A2"]},
             {"op": "delete", "anchor": D},
             {"op": "insert", "anchor": E, "lines": ["E+"]}]
    r = batch(p, edits)
    fwd = open(p).read()
    check("batch disjoint ok", r.ok is True and r.edits_applied == 3)
    check("batch disjoint content", fwd == "A1\nA2\nb\nc\ne\nE+\n")
    check("batch windows are post-edit", r.windows == ((1, 2), (5, 4), (6, 6)))
    _ow(p, "a\nb\nc\nd\ne\n")
    batch(p, list(reversed(edits)))
    check("batch order-free: reversed list = same bytes", open(p).read() == fwd)

    # --- windows: an earlier edit must not corrupt a later one's coordinates ---
    _ow(p, "a\nb\nc\nd\n")
    res = read_file(p)
    r = batch(p, [{"op": "replace", "anchor": res.lines[0].anchor,
                   "lines": ["A1", "A2", "A3"]},
                  {"op": "replace", "anchor": res.lines[3].anchor, "lines": ["D"]}])
    check("windows survive a shift (regression)", r.windows == ((1, 3), (6, 6)))
    check("first/last are the window extent",
          (r.first_changed_line, r.last_changed_line) == (1, 6))
    check("verify shows the shifted edit where it really is",
          [line.text for line in verify(r, context=0).lines] == ["A1", "A2", "A3", "D"])

    # --- verify: on demand, per region, fresh anchors ---
    _ow(p, "".join("l%d\n" % i for i in range(1, 13)))
    res = read_file(p)
    r = batch(p, [{"op": "replace", "anchor": res.lines[1].anchor, "lines": ["L2"]},
                  {"op": "replace", "anchor": res.lines[9].anchor, "lines": ["L10"]}])
    v0 = verify(r)
    check("verify context=1 widens each region", v0.windows == ((1, 3), (9, 11)))
    v1 = verify(r, context=0)
    check("verify context=0 = touched lines only",
          [line.ln for line in v1.lines] == [2, 10])
    check("verify re-anchors for the next edit",
          replace(p, v1.lines[0].anchor, ["L2b"]).ok is True)

    _ow(p, "a\nb\nc\n")
    res = read_file(p)
    r = delete(p, res.lines[1].anchor)
    check("delete window is the empty seam", r.windows == ((2, 1),))
    check("verify shows the seam around a delete",
          [line.text for line in verify(r).lines] == ["a", "c"])

    try:
        verify(replace(p, read_file(p).lines[0].anchor, ["A"], preview=True))
        check("verify rejects a preview", False)
    except UsageError:
        check("verify rejects a preview", True)

    # --- undo receipts ---
    _ow(p, "a\nb\nc\n")
    res = read_file(p)
    before_bytes = open(p, "rb").read()
    r = replace(p, res.lines[1].anchor, ["B", "B2"])
    check("a write returns a receipt", isinstance(r.undo, str) and os.path.exists(r.undo))
    check("previews carry no receipt",
          replace(p, read_file(p).lines[0].anchor, ["A"], preview=True).undo is None)
    u = undo(r.undo)
    check("undo ok", u.ok is True)
    check("undo restores exact bytes", open(p, "rb").read() == before_bytes)
    check("undo removed its receipt", not os.path.exists(r.undo))

    # CRLF + missing final newline survive a round trip
    with open(p, "wb") as f:
        f.write(b"a\r\nb\r\nc")
    res = read_file(p)
    r = replace(p, res.lines[1].anchor, ["B"])
    check("edit on a CRLF/no-final-newline file landed",
          open(p, "rb").read() != b"a\r\nb\r\nc")
    undo(r.undo)
    check("undo restores CRLF + missing final newline",
          open(p, "rb").read() == b"a\r\nb\r\nc")

    # a stale undo refuses instead of clobbering a later change
    _ow(p, "a\nb\nc\n")
    res = read_file(p)
    r = replace(p, res.lines[0].anchor, ["A"])
    _ow(p, "A\nb\nc\n# someone else\n")
    u = undo(r.undo)
    check("stale undo refused", u.ok is False and u.error == "stale")
    check("stale undo wrote nothing",
          open(p).read() == "A\nb\nc\n# someone else\n")

    # one receipt per call: a batch undoes as one step
    _ow(p, "a\nb\nc\n")
    res = read_file(p)
    r = batch(p, [{"op": "replace", "anchor": res.lines[0].anchor, "lines": ["A"]},
                  {"op": "replace", "anchor": res.lines[2].anchor, "lines": ["C"]}])
    check("batch undo is one step", undo(r.undo).ok is True and
          open(p).read() == "a\nb\nc\n")

    # --- content from a file/stdin: a trailing newline is a terminator ---
    stage = os.path.join(d, "stage.new")
    with open(stage, "w", encoding="utf-8") as f:
        f.write("X\nY\n")
    _ow(p, "a\nb\nc\n")
    res = read_file(p)
    fake = argparse.Namespace(file=p, anchor=res.lines[0].anchor, end_anchor=None,
                              new_text=None, file_input=stage, before=False,
                              preview=False, json=True)
    _write_cmd(argparse.ArgumentParser(), fake, "replace")
    check("file content: trailing newline adds no blank line",
          open(p).read() == "X\nY\nb\nc\n")
    check("_lines_from_text drops the final newline",
          _lines_from_text("X\nY\n") == ["X", "Y"])
    check("_lines_from_text keeps a real blank tail",
          _lines_from_text("X\n\n") == ["X", ""])

    print("\n%s" % ("ALL TESTS PASSED" if ok else "SOME TESTS FAILED"))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hedit.py",
        description="Hash-anchored line editor for AI agents (stale-safe edits).",
        epilog=("Examples:\n"
                "  ./hedit.py read foo.py\n"
                "  ./hedit.py read foo.py --grep def --context 2 --json\n"
                "  ./hedit.py read foo.py --pattern 'def ' --context 2 --json\n"
                "  printf 'new line\\n' | ./hedit.py replace foo.py 12#NKA -\n"
                "  ./hedit.py replace foo.py 12#NKA 18#VRC --new-text 'a\\nb\\n'\n"
                "  ./hedit.py insert foo.py 1#QVE --before --file header.txt\n"
                "  ./hedit.py delete foo.py 12#NKA\n"
                "  echo '{\"edits\":[...]}' | ./hedit.py batch foo.py\n"
                "  echo '{\"edits\":[...]}' | ./hedit.py batch foo.py --preview   # show diff, no write\n"
                "  ./hedit.py undo /tmp/hedit-undo/foo.py-1a2b3c4d-x1y2.json   # revert that write\n"
                "  ./hedit.py --selftest"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd")

    p_read = sub.add_parser("read", help="read a file with LN#HASH anchors")
    p_read.add_argument("file")
    p_read.add_argument("--offset", type=int)
    p_read.add_argument("--limit", type=int)
    p_read.add_argument("--grep")
    p_read.add_argument("--pattern")
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

    p_undo = sub.add_parser("undo", help="revert the write a receipt records")
    p_undo.add_argument("receipt", help="the .undo path an edit returned")

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
    if args.cmd == "undo":
        try:
            res = undo(args.receipt)
        except UsageError as e:
            print(json.dumps({"ok": False, "error": "usage", "message": str(e)}))
            return 2
        print(json.dumps(res.to_json()))
        return 0 if res.ok else 1
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
