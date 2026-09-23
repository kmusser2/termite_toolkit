#!/usr/bin/env python3
# TYPE: TOOLKIT
# keywords: |cron|crontab|schedule|scheduler|next-run|fire-time|job|
# examples: |import cronexpr; cronexpr.next_runs('*/15 * * * *', count=3)|import cronexpr; cronexpr.explain('0 9 * * 1-5')|./cronexpr.py '@daily' --count 2|
"""cronexpr.py - compute upcoming fire times for cron expressions.

Parses standard 5-field crontab syntax (minute hour day-of-month month
day-of-week) plus @hourly/@daily/@midnight/@weekly/@monthly/@yearly/
@annually aliases. Supports lists (a,b), ranges (a-b), steps (*/n, a-b/n),
month names (JAN-DEC), weekday names (SUN-SAT), '?' as '*', and Vixie-cron
day semantics: when BOTH day-of-month and day-of-week are restricted (not
'*'), a day matching EITHER one fires. Weekday 7 == 0 (Sunday). Raises
ValueError on malformed input.

Python API (importable from any python_eval call):
  import cronexpr
  cronexpr.next_runs('*/15 * * * *', count=3)
  cronexpr.explain('0 9 * * 1-5')['next_run']

CLI:
  ./cronexpr.py '*/15 * * * *' [--count 3] [--start ISO8601] [--tz ZONE]
  ./cronexpr.py --selftest

Time model: wall-clock arithmetic. With tz=None (default) times are naive
UTC; with an IANA zone name results carry that offset. DST gap/repeat
transitions are not special-cased. Occurrences are strictly AFTER `start`.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from typing import TypedDict

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment,misc]

__all__ = ["CronField", "Explanation", "FireTimes", "parse", "explain", "next_runs", "main"]

_FIELD_ORDER = ("minute", "hour", "day-of-month", "month", "day-of-week")
_RANGES = {"minute": (0, 59), "hour": (0, 23), "day-of-month": (1, 31),
           "month": (1, 12), "day-of-week": (0, 7)}
_MONTHS = {n: i for i, n in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_WEEKDAYS = {n: i for i, n in enumerate(("sun", "mon", "tue", "wed", "thu", "fri", "sat"))}
_ALIASES = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *", "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *",
}
_MAX_SEARCH_STEPS = 400000


class CronField(TypedDict):
    """One expanded cron field."""
    name: str
    raw: str
    values: list[int]


class Explanation(TypedDict):
    """Human-readable parse of an expression plus its next occurrence."""
    expr: str
    normalized: str
    fields: list[CronField]
    next_run: str


class FireTimes(TypedDict):
    """Upcoming occurrence timestamps for an expression."""
    expr: str
    count: int
    runs: list[str]


def _atom(token: str, name: str) -> int:
    lo, hi = _RANGES[name]
    t = token.strip().lower()
    if t.isdigit():
        v = int(t)
    elif name == "month" and t in _MONTHS:
        v = _MONTHS[t]
    elif name == "day-of-week" and t in _WEEKDAYS:
        v = _WEEKDAYS[t]
    else:
        raise ValueError(f"{name}: bad value {token!r}")
    if not lo <= v <= hi:
        raise ValueError(f"{name}: value {v} out of range {lo}..{hi}")
    return v


def _expand_field(raw: str, name: str) -> list[int]:
    lo, hi = _RANGES[name]
    text = raw.strip().lower().replace("?", "*")
    if not text:
        raise ValueError(f"{name}: empty field")
    acc: set[int] = set()
    for item in text.split(","):
        base, sep, step_s = item.partition("/")
        step = 1
        if sep:
            if not step_s.isdigit() or int(step_s) == 0:
                raise ValueError(f"{name}: bad step {step_s!r} in {raw!r}")
            step = int(step_s)
            if not base:
                raise ValueError(f"{name}: missing range before '/' in {raw!r}")
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            left, _, right = base.partition("-")
            start, end = _atom(left, name), _atom(right, name)
            if start > end:
                raise ValueError(f"{name}: inverted range {item!r} in {raw!r}")
        else:
            start = end = _atom(base, name)
        acc.update(range(start, end + 1, step))
    if name == "day-of-week":
        acc = {0 if v == 7 else v for v in acc}
    return sorted(acc)


def parse(expr: str) -> list[CronField]:
    """Parse a cron expression into five expanded fields; raises ValueError."""
    text = (expr or "").strip()
    low = text.lower()
    if low.startswith("@"):
        if low in _ALIASES:
            text = _ALIASES[low]
        else:
            raise ValueError(f"unsupported alias {text!r}; "
                             f"known: {', '.join(sorted(_ALIASES))}")
    parts = text.split()
    if len(parts) != 5:
        raise ValueError(f"expected 5 fields (minute hour dom month dow), "
                         f"got {len(parts)}: {expr!r}")
    return [CronField(name=n, raw=r, values=_expand_field(r, n))
            for n, r in zip(_FIELD_ORDER, parts)]


def _cron_weekday(dt: datetime) -> int:
    return (dt.weekday() + 1) % 7  # cron: 0=Sunday .. 6=Saturday


def _next_after(parsed: list[CronField], after: datetime) -> datetime:
    """Earliest occurrence strictly after `after` (wall-clock search)."""
    minutes = set(parsed[0]["values"])
    hours = set(parsed[1]["values"])
    doms = set(parsed[2]["values"])
    months = set(parsed[3]["values"])
    dows = set(parsed[4]["values"])
    dom_any = parsed[2]["raw"].replace("?", "*") == "*"
    dow_any = parsed[4]["raw"].replace("?", "*") == "*"

    dt = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(_MAX_SEARCH_STEPS):
        try:
            if dt.month not in months:
                y, m = (dt.year + 1, 1) if dt.month == 12 else (dt.year, dt.month + 1)
                dt = dt.replace(year=y, month=m, day=1, hour=0, minute=0)
                continue
            dom_ok = dt.day in doms
            dow_ok = _cron_weekday(dt) in dows
            if dom_any and dow_any:
                day_ok = True
            elif dom_any:
                day_ok = dow_ok
            elif dow_any:
                day_ok = dom_ok
            else:
                day_ok = dom_ok or dow_ok  # classic Vixie OR case
            if not day_ok:
                dt = (dt + timedelta(days=1)).replace(hour=0, minute=0)
                continue
            if dt.hour not in hours:
                dt = (dt + timedelta(hours=1)).replace(minute=0)
                continue
            if dt.minute not in minutes:
                dt = dt + timedelta(minutes=1)
                continue
            return dt
        except (OverflowError, ValueError) as exc:
            raise ValueError(
                f"no occurrence found after {after.isoformat()}: {exc}") from None
    raise RuntimeError("cron search did not converge")  # pragma: no cover


def _zone(tz_name: str | None):
    if tz_name is None:
        return None
    if ZoneInfo is None:  # pragma: no cover
        raise ValueError("zoneinfo database unavailable in this interpreter")
    try:
        return ZoneInfo(tz_name)
    except Exception as exc:
        raise ValueError(f"unknown timezone {tz_name!r}: {exc}") from None


def _coerce_when(when: str | datetime | None, zone):
    if when is None:
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        return now_utc if zone is None else now_utc.replace(tzinfo=zone)
    if isinstance(when, str):
        try:
            dt = datetime.fromisoformat(when)
        except ValueError:
            raise ValueError(f"bad ISO-8601 datetime {when!r}") from None
    else:
        dt = when
    if zone is None:
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    return dt.replace(tzinfo=zone) if dt.tzinfo is None else dt.astimezone(zone)


def next_runs(expr: str, count: int = 5, start: str | datetime | None = None,
              tz: str | None = None) -> FireTimes:
    """Compute the next `count` occurrences of a cron expression.

    Times are strictly after `start` (default: now), computed as wall-clock
    times in `tz` (None => naive UTC). Raises ValueError on bad input.
    """
    if count < 1:
        raise ValueError("count must be >= 1")
    parsed = parse(expr)
    zone = _zone(tz)
    cur = _coerce_when(start, zone)
    runs: list[str] = []
    for _ in range(count):
        cur = _next_after(parsed, cur)
        runs.append(cur.isoformat())
    return FireTimes(expr=expr, count=len(runs), runs=runs)


def explain(expr: str, tz: str | None = None,
            now: str | datetime | None = None) -> Explanation:
    """Parse an expression and report its structure plus next fire time."""
    parsed = parse(expr)
    zone = _zone(tz)
    nxt = _next_after(parsed, _coerce_when(now, zone))
    return Explanation(expr=expr,
                       normalized=" ".join(f["raw"] for f in parsed),
                       fields=parsed, next_run=nxt.isoformat())


def _selftest() -> int:
    ok = True

    def check(name, cond):
        nonlocal ok
        ok = ok and cond
        print(("PASS" if cond else "FAIL") + "  " + name)

    def runs(expr, start, count=1, **kw):
        return next_runs(expr, count=count, start=start, **kw)["runs"]

    check("every-minute sequence",
          runs("* * * * *", "2026-01-01T00:00:00", 3) ==
          ["2026-01-01T00:01:00", "2026-01-01T00:02:00", "2026-01-01T00:03:00"])
    check("step */15",
          runs("*/15 * * * *", "2026-01-01T00:00:00", 4) ==
          ["2026-01-01T00:15:00", "2026-01-01T00:30:00",
           "2026-01-01T00:45:00", "2026-01-01T01:00:00"])
    check("weekday job skips weekend",
          runs("0 9 * * 1-5", "2026-01-02T10:00:00", 2) ==
          ["2026-01-05T09:00:00", "2026-01-06T09:00:00"])
    check("strictly-after start",
          runs("0 * * * *", "2026-03-01T05:00:00") == ["2026-03-01T06:00:00"])
    check("vixie OR semantics (dom OR dow both restricted)",
          runs("0 0 13 * fri", "2026-01-01T00:00:00", 3) ==
          ["2026-01-02T00:00:00", "2026-01-09T00:00:00",
           "2026-01-13T00:00:00"])
    check("AND semantics when only dom restricted",
          runs("0 0 13 * *", "2026-01-01T00:00:00") == ["2026-01-13T00:00:00"])
    check("AND semantics when only dow restricted",
          runs("0 0 * * fri", "2026-01-01T00:00:00") == ["2026-01-02T00:00:00"])
    check("dow 7 folds to sunday",
          runs("0 0 * * 7", "2026-01-01T00:00:00") == ["2026-01-04T00:00:00"])
    check("@weekly alias",
          runs("@weekly", "2026-01-01T00:00:00") == ["2026-01-04T00:00:00"])
    check("@daily alias",
          runs("@daily", "2026-06-15T07:30:00") == ["2026-06-16T00:00:00"])
    check("month names + list",
          runs("0 12 1 JAN,MAR *", "2026-01-02T00:00:00", 2) ==
          ["2026-03-01T12:00:00", "2027-01-01T12:00:00"])
    check("year rollover",
          runs("0 0 1 1 *", "2026-12-31T12:00:00") == ["2027-01-01T00:00:00"])
    ex = explain("*/10 * * * *", now="2026-04-01T00:07:00")
    check("explain structure",
          ex["normalized"] == "*/10 * * * *"
          and ex["fields"][0]["values"] == list(range(0, 60, 10))
          and ex["next_run"] == "2026-04-01T00:10:00")
    check("tz-aware run carries offset",
          runs("0 9 * * *", "2026-07-01T00:00:00", tz="Asia/Tokyo")[0]
          .endswith("+09:00"))
    for bad in ("61 * * * *", "* * * * 8", "*/0 * * * *", "* * *",
                "0 0 32 * *", "@fortnightly", "0 0 JAN-X * *"):
        try:
            parse(bad)
            check(f"rejects {bad!r}", False)
        except ValueError:
            check(f"rejects {bad!r}", True)
    check("cli main exits 0",
          main(["*/5 * * * *", "--count", "2",
                "--start", "2026-01-01T00:00:00"]) == 0)
    print("\n%s" % ("ALL TESTS PASSED" if ok else "SOME TESTS FAILED"))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    """Run the command-line interface; returns process exit status."""
    p = argparse.ArgumentParser(description="cron expression fire-time calculator")
    p.add_argument("expr", nargs="?", help="cron expression or @alias")
    p.add_argument("--count", type=int, default=3,
                   help="how many fire times to print (default 3)")
    p.add_argument("--start", help="ISO-8601 moment to search after (default: now)")
    p.add_argument("--tz", help="IANA timezone (default: naive UTC)")
    p.add_argument("--selftest", action="store_true", help="run built-in tests")
    args = p.parse_args(argv)
    if args.selftest:
        return _selftest()
    if not args.expr:
        p.error("an expression is required (or pass --selftest)")
    try:
        ft = next_runs(args.expr, count=args.count, start=args.start, tz=args.tz)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for r in ft["runs"]:
        print(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
