#!/usr/bin/env python3
# TYPE: TOOLKIT
"""project_check.py - run a project's checks from a project-check.json.
# keywords: project|check|manifest|run|verify|status|report|render|selftest|benchmark
# examples: ./project_check.py --root TETRIS --out tmp/tetris-check.json -q | ./project_check.py --render tmp/tetris-check.json

Usage:
  project_check.py [--root DIR | --manifest FILE] [--out FILE] [--no-color]
  project_check.py --render FILE [--no-color]
  project_check.py --selftest

The manifest is deliberately project-specific; this runner only executes its
commands and produces consistent results. `--render FILE` renders a previously
saved JSON report (see `--out`) without re-running any checks.
"""
import argparse
import json
import os
import re
from pathlib import Path
import shlex
import subprocess
import sys
import time

from enum import StrEnum
from typing import TypedDict

import fio
from table import render_table


class Status(StrEnum):
    """One check outcome; the value is the stored/wire form."""
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_RUN = "NOT_RUN"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"
    STALE = "STALE"


class CheckSpec(TypedDict, total=False):
    """One check entry from project-check.json."""
    name: str
    command: str
    timeout_sec: float
    expected_exit: int
    seeds: list[str]
    artifacts: list[str]


class TestCounts(TypedDict):
    """Parsed SELFTEST_RESULT test counts."""
    total: int
    passed: int
    failed: int


class CheckResult(TypedDict, total=False):
    """The result dict produced by run_check."""
    name: str
    status: Status
    duration_ms: int
    command: str
    summary: str
    output: str
    tests: TestCounts
    artifacts: list[str]


class Counts(TypedDict):
    """Status counts across all checks."""
    PASS: int
    FAIL: int
    NOT_RUN: int
    SKIPPED: int
    BLOCKED: int
    STALE: int


class Report(TypedDict, total=False):
    """A full project-check report dict."""
    project: str
    manifest: str
    root: str
    timestamp: str
    results: list[CheckResult]
    counts: Counts
    status: Status


__all__ = ["CheckSpec", "CheckResult", "Counts", "Report", "Status",
           "TestCounts", "assess", "format_duration", "report_status",
           "render_report", "render_table_for_report", "run_check",
           "run_configured_check", "selftest", "main"]

BASE = Path(__file__).resolve().parent.parent
DEFAULT_TIMEOUT = 120
MAX_OUTPUT = 4000


def atomic_json(path: str, value: object) -> None:
    """Write value to path as pretty JSON atomically, creating parent dirs.

    The replace mechanism is fio.atomic_write's (its contract); this adds the
    JSON encoding and parent-dir creation.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fio.atomic_write(str(path), json.dumps(value, indent=2) + '\n')


def find_manifest(root: str | None = None, manifest: str | None = None) -> Path:
    """Locate project-check.json: an explicit manifest path, or search root and its parent directories."""
    if manifest:
        p = Path(manifest).resolve()
        return p
    start = Path(root or Path.cwd()).resolve()
    if start.is_file():
        start = start.parent
    for directory in (start, *start.parents):
        p = directory / 'project-check.json'
        if p.is_file():
            return p
    raise FileNotFoundError('project-check.json not found')


def run_check(check: CheckSpec, cwd: str) -> CheckResult:
    """Run one check's command and return its result dict (status, duration, output, optional tests)."""
    name = check.get('name', 'unnamed')
    command = check.get('command')
    if not command:
        return {'name': name, 'status': Status.STALE, 'summary': 'missing command'}
    timeout = float(check.get('timeout_sec', DEFAULT_TIMEOUT))
    expected = int(check.get('expected_exit', 0))
    started = time.monotonic()
    result_tests = None
    try:
        proc = subprocess.run(command, shell=True, cwd=cwd, text=True,
                              capture_output=True, timeout=timeout)
        status = Status.PASS if proc.returncode == expected else Status.FAIL
        output = (proc.stdout + proc.stderr).strip()
        summary = f'exit {proc.returncode}'
        match = re.search(r'SELFTEST_RESULT\s+total=(\d+)\s+passed=(\d+)\s+failed=(\d+)', output)
        if match:
            total, passed, failed = map(int, match.groups())
            result_tests = {'total': total, 'passed': passed, 'failed': failed}
            summary = f'exit {proc.returncode} | {total} tests | {passed} passed | {failed} failed'
    except subprocess.TimeoutExpired as exc:
        status, output, summary = Status.FAIL, ((exc.stdout or '') if isinstance(exc.stdout, str) else ''), f'timeout after {timeout:g}s'
        proc = None
    except OSError as exc:
        status, output, summary = Status.BLOCKED, '', str(exc)
        proc = None
    duration = round((time.monotonic() - started) * 1000)
    result = {'name': name, 'status': status, 'duration_ms': duration,
              'command': command, 'summary': summary}
    if output:
        result['output'] = output[-MAX_OUTPUT:]
    if result_tests is not None:
        result['tests'] = result_tests
    if check.get('artifacts'):
        result['artifacts'] = check['artifacts']
    return result


def report_status(counts: Counts) -> Status:
    """Overall status: PASS unless any FAIL/BLOCKED/STALE count is nonzero."""
    bad = (Status.FAIL, Status.BLOCKED, Status.STALE)
    return Status.PASS if all(counts.get(s, 0) == 0 for s in bad) else Status.FAIL


def format_duration(ms: int | float) -> str:
    """Format a duration in ms as a short human string (ms, s, or m/s)."""
    if ms < 1000:
        return f'{ms}ms'
    if ms < 60000:
        return f'{ms / 1000:.1f}s'
    minutes, seconds = divmod(ms, 60000)
    return f'{minutes}m{seconds / 1000:.0f}s'


def render_table_for_report(report: Report, color: bool = True) -> str:
    """Build the boxed report table (title, Time column, status colors, and
    the failing command appended to non-PASS rows) from a report dict."""
    results = report.get('results', [])
    counts = report.get('counts', {})
    rows = []
    for r in results:
        status = r.get('status', 'STALE')
        summary = r.get('summary', '')
        if status != 'PASS' and r.get('command'):
            summary = f'{summary} | {r["command"]}'
        rows.append([r.get('name', 'unnamed'), status,
                     format_duration(r.get('duration_ms', 0)), summary])
    internal = [r.get('tests') for r in results if r.get('tests')]
    test_summary = ''
    if internal:
        tt = sum(x['total'] for x in internal)
        tp = sum(x['passed'] for x in internal)
        tf = sum(x['failed'] for x in internal)
        test_summary = f' | tests: {tt} total | {tp} passed | {tf} failed'
    total_ms = sum(r.get('duration_ms', 0) for r in results)
    rows.append([
        'TOTAL', report_status(counts), format_duration(total_ms),
        f"{len(results)} checks | {counts.get('PASS', 0)} passed | {counts.get('FAIL', 0)} failed{test_summary}"
    ])
    title = f"{report.get('project', Path(report.get('manifest', '.')).name)} project health"
    timestamp = report.get('timestamp')
    if timestamp:
        title += f' \u2014 {timestamp}'
    return render_table(['Check', 'Status', 'Time', 'Summary'], rows,
                        title=title, color=color)


def run_configured_check(check: CheckSpec, cwd: str) -> list[CheckResult]:
    """Run a check once, or once per seed if the check declares seeds; returns result dicts."""
    seeds = check.get('seeds')
    if not seeds:
        return [run_check(check, cwd)]
    results = []
    for seed in seeds:
        command = check.get('command', '') + f' --seed {seed}'
        seeded = dict(check, name=f"{check.get('name', 'unnamed')}[seed={seed}]", command=command)
        results.append(run_check(seeded, cwd))
    return results


def assess(manifest_path: str | Path, out: str | None = None, quiet: bool = False, color: bool = True) -> int:
    """Load a manifest, run all checks, optionally save/render the report; return 0 if healthy else 1."""
    manifest_path = Path(manifest_path).resolve()
    data = json.loads(manifest_path.read_text())
    project_root = (manifest_path.parent / data.get('root', '.')).resolve()
    results = []
    for check in data.get('checks', []):
        results.extend(run_configured_check(check, project_root))
    if not results:
        results = [{'name': 'manifest', 'status': Status.STALE, 'summary': 'no checks configured'}]
    counts = {s: sum(r['status'] == s for r in results) for s in Status}
    report = {'project': data.get('project', project_root.name),
              'manifest': str(manifest_path), 'root': str(project_root),
              'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'results': results, 'counts': counts,
              'status': Status.PASS if all(r['status'] == Status.PASS for r in results) else Status.FAIL}
    if out:
        atomic_json(out, report)
    if not quiet:
        print(render_table_for_report(report, color=color))
        if out:
            print(f'results: {out}')
    passed = counts['PASS']
    failed = counts['FAIL']
    print(f"project-check: {len(results)} total | {passed} passed | {failed} failed" +
          ('' if report['status'] == Status.PASS else '  NOT HEALTHY'))
    return 0 if report['status'] == Status.PASS else 1


def render_report(path: str | Path, color: bool = True) -> int:
    """Render a saved JSON report without running any checks."""
    report = json.loads(Path(path).read_text())
    print(render_table_for_report(report, color=color))
    results = report.get('results', [])
    passed = sum(1 for r in results if r.get('status') == Status.PASS)
    failed = sum(1 for r in results if r.get('status') == Status.FAIL)
    status = report.get('status')
    print(f'project-check: {len(results)} total | {passed} passed | {failed} failed' +
          ('' if status == Status.PASS else '  NOT HEALTHY'))
    return 0 if status == Status.PASS else 1


def selftest() -> int:
    """Verify assess/render/table features against a temp manifest (pass, fail, timeout, missing command)."""
    import tempfile
    with tempfile.TemporaryDirectory(dir=BASE / 'tmp') as d:
        d = Path(d)
        manifest = d / 'project-check.json'
        atomic_json(manifest, {'project': 'probe', 'checks': [
            {'name': 'pass', 'command': 'python3 -c "print(\'ok\')"'},
            {'name': 'fail', 'command': 'python3 -c "raise SystemExit(3)"'},
            {'name': 'timeout', 'command': 'python3 -c "import time; time.sleep(2)"', 'timeout_sec': .05},
            {'name': 'missing-command'}]})
        out = d / 'results.json'
        rc = assess(manifest, out, quiet=True)
        report = json.loads(out.read_text())
        assert rc == 1
        assert report['counts']['PASS'] == 1
        assert report['counts']['FAIL'] == 2
        assert report['counts']['STALE'] == 1
        assert find_manifest(manifest) == manifest.resolve()
        # table features: Time column, timestamped title, command on non-PASS,
        # status colors, and a color-less rendering
        tbl = render_table_for_report(report, color=False)
        assert 'Time' in tbl
        assert report['timestamp'] in tbl
        assert 'raise SystemExit(3)' in tbl          # command appended to FAIL row
        assert 'missing command' in tbl              # STALE summary kept
        assert '\033[' not in tbl                     # --no-color
        colored = render_table_for_report(report, color=True)
        assert '\033[32mPASS\033[0m' in colored      # green PASS
        assert '\033[31mFAIL\033[0m' in colored      # red FAIL
        assert '\033[90mSTALE\033[0m' in colored     # gray STALE
        # --render path re-reads the JSON and reproduces the same report
        assert render_report(out, color=False) == 1
    print('project_check.py --selftest PASS')
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: ./project_check.py [--root DIR | --manifest FILE] [--out FILE] ..."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', help='directory containing, or below, project-check.json')
    ap.add_argument('--manifest', help='explicit manifest path')
    ap.add_argument('--out', help='write JSON results to this path')
    ap.add_argument('--render', metavar='FILE', help='render a saved JSON report without running checks')
    ap.add_argument('--no-color', action='store_true', help='disable ANSI color in the rendered table')
    ap.add_argument('-q', '--quiet', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    color = not args.no_color
    try:
        if args.render:
            return render_report(args.render, color=color)
        manifest = find_manifest(args.root, args.manifest)
        return assess(manifest, args.out, args.quiet, color=color)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f'project-check: BLOCKED: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
