#!/usr/bin/env python3
# TYPE: TOOLKIT
"""healthcheck.py - one-shot sandbox + toolkit health probe.
# keywords: health|probe|check|selftest|diagnostics|status|toolchain|devices|network|rlimits|verify|green
# examples: ./healthcheck.py -q | ./healthcheck.py --selftest

Checks toolchains, devices, network, symlink/venv, rlimits and runs every
TOOLKIT tool's --selftest. Exit 0 = all green; 1 = failures (printed).

Usage:
  healthcheck.py              full check (default)
  healthcheck.py -q           quiet: only print failures, summary line
  healthcheck.py --deep       also: 1.5GiB alloc probe, venv + pip install
  healthcheck.py --selftest   self-test the checker itself
  healthcheck.py -h           help
"""
from __future__ import annotations
import argparse, glob, os, resource, shutil, socket, subprocess, sys, tempfile, time
from typing import Sequence

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))  # tool modules live beside this file
BASE = os.path.dirname(TOOLS_DIR)                       # workspace root (contains src/ and tmp/)
SCRATCH = os.path.join(BASE, "tmp")                     # probe artifacts go here (per AGENTS.md)

__all__ = ["run", "scratch", "selftest_self", "main"]


def scratch() -> str:
    """Writable scratch dir for probe artifacts (BASE/tmp), created on demand.

    Raises OSError when the workspace offers no writable tmp/ -- probe callers
    turn that into a failed check rather than letting the run crash."""
    os.makedirs(SCRATCH, exist_ok=True)
    return SCRATCH


def run(cmd: Sequence[str], timeout: float = 30, input: str | None = None) -> tuple[int | None, str]:
    """Run a shell command capturing output; return (returncode, trimmed output or 'TIMEOUT')."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=timeout, input=input)
        return r.returncode, (r.stdout or r.stderr).strip()
    except subprocess.TimeoutExpired:
        return None, "TIMEOUT"
    except Exception as e:
        return None, str(e)

class Check:
    def __init__(self, quiet):
        self.quiet = quiet; self.results = []
    def add(self, name, ok, detail=""):
        self.results.append((name, ok, detail))
        if not self.quiet or not ok:
            print(f"[{'OK ' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    def done(self):
        bad = [n for n, ok, _ in self.results if not ok]
        print(f"\nhealthcheck: {len(self.results)-len(bad)}/{len(self.results)} passed"
              + (f"  FAILED: {', '.join(bad)}" if bad else "  ALL GREEN"))
        return 1 if bad else 0

def probe_devices(c: Check) -> None:
    """Probe /dev device accessibility (null, urandom, random, ptmx) and report to c."""
    for dev, mode, code in [
        ("/dev/null rw",      "wb", "open('/dev/null','wb').write(b'x')"),
        ("/dev/urandom r",    "rb", "open('/dev/urandom','rb').read(8)"),
        ("/dev/random r",     "rb", "open('/dev/random','rb').read(8)"),
        ("/dev/ptmx open",    "rw", "import os; os.close(os.open('/dev/ptmx', os.O_RDWR))"),
    ]:
        rc, out = run(f'python3 -c "{code}"', timeout=15)
        c.add(f"device {dev}", rc == 0, out[-80:])

def probe_toolchains(c: Check) -> None:
    """Probe gcc/g++ compile+run and the presence/version of common toolchain tools."""
    compiles = {
        "gcc":      (".c",   '#include <stdio.h>\nint main(){{puts("ok");return 0;}}\n'),
        "g++":      (".cpp", '#include <iostream>\nint main(){{std::cout<<"ok\\n";}}\n'),
    }
    try:
        tmpdir = scratch()
        for cc, (ext, src) in compiles.items():
            t = tempfile.NamedTemporaryFile("w", suffix=ext, delete=False, dir=tmpdir)
            t.write(src); t.close()
            exe = t.name + ".out"
            rc1, _ = run(f"{cc} {t.name} -o {exe} && {exe}", timeout=30)
            c.add(f"toolchain {cc} compile+run", rc1 == 0)
            for f in (t.name, exe):
                try: os.unlink(f)
                except OSError: pass
    except OSError as exc:
        c.add("toolchain compile+run", False, "no writable scratch: %s" % exc)
    for t in ["make", "cmake", "python3", "java", "javac", "node", "git", "curl"]:
        rc, out = run(f"command -v {t} >/dev/null && {t} --version 2>&1 | head -1", timeout=15)
        c.add(f"toolchain {t}", rc == 0, out[:60])

def probe_network(c: Check) -> None:
    """Probe DNS resolution and HTTPS reachability for example.com and pypi.org."""
    rc, out = run("getent hosts example.com | head -1", timeout=15)
    c.add("dns example.com", rc == 0, out[:60])
    for host in ("https://example.com/", "https://pypi.org/simple/six/"):
        rc, out = run(f"curl -sS -o /dev/null -w '%{{http_code}}' -m 10 {host}", timeout=20)
        c.add(f"https {host.split('/')[2]}", rc == 0 and out == "200", f"http {out}")

def probe_symlink_venv(c: Check) -> None:
    """Probe symlink(2) support and that python3 -m venv works end-to-end."""
    try:
        tmpdir = scratch()
    except OSError as exc:
        c.add("symlink(2)", False, "no writable scratch: %s" % exc)
        c.add("python3 -m venv (e2e)", False, "no writable scratch: %s" % exc)
        return
    t = os.path.join(tmpdir, "hc_sl")
    rc, out = run(f"python3 -c \"import os; os.symlink('/etc/hosts', '{t}'); os.unlink('{t}')\"", timeout=15)
    c.add("symlink(2)", rc == 0, out[-80:])
    v = os.path.join(tmpdir, "hc_venv")
    rc, out = run(f"rm -rf {v} && python3 -m venv {v} && {v}/bin/python -c 'import sys; print(sys.prefix!=sys.base_prefix)'", timeout=60)
    ok = rc == 0 and out.endswith("True")
    c.add("python3 -m venv (e2e)", ok, out[-80:])
    shutil.rmtree(v, ignore_errors=True)

def probe_rlimits(c: Check) -> None:
    """Report current soft/hard limits for CPU, NOFILE, FSIZE, and AS."""
    names = {"RLIMIT_CPU": "CPU", "RLIMIT_NOFILE": "NOFILE", "RLIMIT_FSIZE": "FSIZE", "RLIMIT_AS": "AS"}
    for rl, tag in names.items():
        cur, mx = getattr(resource, "getrlimit")(getattr(resource, rl))
        c.add(f"rlimit {tag}", True, f"soft={cur} hard={mx}")

def probe_deep(c: Check) -> None:
    """Deep probe: allocate a 1.5 GiB bytearray to test memory headroom."""
    rc, out = run("python3 -c \"b=bytearray(1536*1024*1024); b[0]=1; print('1.5GiB ok')\"", timeout=30)
    c.add("alloc 1.5GiB", rc == 0, out[:60])

def run_toolkit_selftests(c: Check) -> None:
    """Run every tool module's --selftest (the .py files next to this file) and report results."""
    tools = sorted(glob.glob(os.path.join(TOOLS_DIR, "*.py")))
    tools = [t for t in tools if not t.endswith("healthcheck.py")]
    for t in tools:
        rc, out = run(f"python3 {t} --selftest", timeout=90)
        last = out.splitlines()[-1] if out else ""
        c.add(f"selftest {os.path.basename(t)}", rc == 0, last[:60])

def selftest_self() -> int:
    """Verify failure detection: a forced-fail probe makes the check return 1."""
    c = Check(quiet=True); c.add("forced-fail-probe", False, "expected"); sys.argv = [sys.argv[0]]
    rc = c.done()
    assert rc == 1 and len(c.results) == 1 and not c.results[0][1]
    s = scratch()
    assert os.path.isdir(s) and os.path.commonpath([s, BASE]) == BASE, s
    print("healthcheck.py --selftest PASS (failure detection works)")
    return 0

def main(argv: list[str] | None = None) -> int:
    """CLI entry point (see the module docstring for usage)."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-q", "--quiet", action="store_true")
    ap.add_argument("--deep", action="store_true", help="extra probes (big alloc, venv+pip)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest_self()
    c = Check(quiet=a.quiet)
    t0 = time.time()
    print(f"healthcheck for {BASE}  ({time.strftime('%Y-%m-%d %H:%M:%S')})")
    probe_toolchains(c)
    probe_devices(c)
    probe_network(c)
    probe_symlink_venv(c)
    probe_rlimits(c)
    if a.deep:
        probe_deep(c)
    run_toolkit_selftests(c)
    print(f"elapsed {time.time()-t0:.1f}s")
    return c.done()

if __name__ == "__main__":
    sys.exit(main())
