#!/usr/bin/env python3
# TYPE: TOOLKIT
"""pty_run.py - drive an interactive terminal program via a pseudo-terminal.
# keywords: pty|terminal|tty|interactive|tui|keys|keystrokes|input|ncurses|ansi|automation|script|drive|type
# examples: ./pty_run.py --cmd "./tetris" --keys "left,left,x,space,q" --strip-ansi --lines 24

Great for testing terminal apps (ncurses/ANSI games, TUIs) without a human.
Captures the raw bytes the program writes to its terminal.

Usage:
  pty_run.py [options] --cmd "command with args"
  pty_run.py --cmd "./tetris" --keys "left,left,x,space,q" --strip-ansi
  pty_run.py --selftest             run self tests

Options:
  --wait SEC        wait for initial output (default 0.5)
  --keys SPEC       comma-separated keys to send (see KEY NAMES below)
  --key-delay SEC   delay between keys (default 0.15)
  --head SEC        keep reading this long after last key (default 0.5)
  --timeout SEC     total timeout before force-killing (default 10)
  --strip-ansi      strip ANSI escape codes from output
  --lines N         print at most N lines of output
  --keep            don't kill the process on timeout (not recommended)

KEY NAMES: left right up down space enter esc tab backspace ctrl-c ctrl-d
  Any single character is sent literally. Tokens containing backslashes are
  decoded as escape sequences, e.g. raw:\\x1b[D or raw:\\x1b[3~.

Examples:
  pty_run.py --cmd ./tetris --keys "left,left,x, ,c,p,p,q" --strip-ansi --lines 24
"""
import codecs
import os
import pty
import select
import signal
import subprocess
import sys
import time
import shlex

import ansi

__all__ = ["encode_key", "parse_cmd", "selftest", "main"]

# workspace root (this file lives in <root>/src/)
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


KEYS = {
    'left': b'\x1b[D', 'right': b'\x1b[C', 'up': b'\x1b[A', 'down': b'\x1b[B',
    'space': b' ', 'enter': b'\r', 'esc': b'\x1b', 'tab': b'\t',
    'backspace': b'\x7f', 'ctrl-c': b'\x03', 'ctrl-d': b'\x04',
}


def encode_key(token: str) -> bytes:
    """Map a key token (named key, single char, or raw:\\xNN escape) to the bytes to send to the PTY."""
    t = token.strip()
    if t in KEYS:
        return KEYS[t]
    if len(t) == 1:
        return t.encode()
    if '\\' in t:
        body = t[4:] if t.startswith('raw:') else t
        return codecs.decode(body, 'unicode_escape').encode('latin-1')
    return t.encode()
def parse_cmd(s: str) -> list[str]:
    """Split a command string honoring quotes (shlex)."""
    return shlex.split(s)


def selftest() -> int:
    """Verify encode_key/parse_cmd + end-to-end PTY driving (keys, strip, lines,
    timeout, exit code, CLI errors). End-to-end checks need a real PTY and are
    SKIPPED (with a notice) when the sandbox denies /dev/ptmx - an environment
    limit, not a code failure. Uses tmp/ only."""
    import shutil
    import tempfile
    ok = True
    pty_ok = False

    def check(name, cond):
        nonlocal ok
        print(("PASS" if cond else "FAIL") + "  " + name)
        if not cond:
            ok = False

    def skip(name):
        print('SKIP  ' + name)

    check('key left', encode_key('left') == b'\x1b[D')
    check('key space', encode_key('space') == b' ')
    check('key single char', encode_key('x') == b'x')
    check('key raw escape', encode_key(r'raw:\x1b[D') == b'\x1b[D')
    check('key raw csi', encode_key(r'raw:\x1b[3~') == b'\x1b[3~')
    check('parse_cmd simple', parse_cmd('python3 /x/probe.py') == ['python3', '/x/probe.py'])
    check('parse_cmd quoted', parse_cmd('printf "a b"') == ['printf', 'a b'])
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        argv_ok = main(['-h']) == 0 and main(['--bogus']) == 1
    check('main(argv) honors argv', argv_ok)

    try:
        m, s = pty.openpty()
    except OSError:
        pty_ok = False
    else:
        pty_ok = True
        os.close(m)
        os.close(s)

    d = tempfile.mkdtemp(dir=os.path.join(BASE, 'tmp'))
    try:
        tool = os.path.abspath(__file__)
        if pty_ok:
            probe = os.path.join(d, 'probe.py')
            with open(probe, 'w') as f:
                f.write('import sys, time, termios, tty\n'
                        'ESC = chr(27)\n'
                        'print("LINE1 %s[31mRED%s[0m END" % (ESC, ESC))\n'
                        'sys.stdout.flush()\n'
                        'time.sleep(0.3)\n'
                        'tty.setraw(sys.stdin.fileno())  # raw mode: read single keys\n'
                        'k = sys.stdin.read(1)\n'
                        'print("GOT:%r" % k)\n'
                        'sys.stdout.flush()\n'
                        'sys.exit(3)\n')
            common = ['--wait', '0.3', '--key-delay', '0.05', '--head', '0.3',
                      '--timeout', '10', '--strip-ansi']
            r = subprocess.run([sys.executable, tool, '--cmd', 'python3 %s' % probe,
                                '--keys', 'z'] + common,
                               capture_output=True, text=True, timeout=25)
            check('program output captured', 'LINE1 RED END' in r.stdout)
            check("key delivered (GOT:'z')", "GOT:'z'" in r.stdout)
            check('exit code 3 propagated', '[exit code: 3]' in r.stderr)
            check('no ANSI remnants', '\x1b[' not in r.stdout)

            r2 = subprocess.run([sys.executable, tool, '--cmd', 'python3 %s' % probe,
                                 '--keys', 'z', '--lines', '1'] + common,
                                capture_output=True, text=True, timeout=25)
            check('--lines 1 truncates', len(r2.stdout.strip().split('\n')) == 1)

            late = os.path.join(d, 'late.py')
            with open(late, 'w') as f:
                f.write('import time\n'
                        'time.sleep(1.0)\n'
                        'print("LATE-OUT")\n'
                        'sys.stdout.flush()\n')
            rl = subprocess.run([sys.executable, tool,
                                 '--cmd', 'python3 %s' % late,
                                 '--wait', '0.2', '--head', '0.1',
                                 '--timeout', '5', '--strip-ansi'],
                                capture_output=True, text=True, timeout=20)
            check('late output drained after wait', 'LATE-OUT' in rl.stdout)

            sleeper = os.path.join(d, 'sleep.py')
            with open(sleeper, 'w') as f:
                f.write('import sys, time\n'
                        'print("never exits")\n'
                        'sys.stdout.flush()\n'
                        'while True:\n'
                        '    time.sleep(1)\n')
            r3 = subprocess.run([sys.executable, tool, '--cmd', 'python3 %s' % sleeper,
                                 '--wait', '0.2', '--head', '0.1', '--timeout', '1'],
                                capture_output=True, text=True, timeout=25)
            check('timeout kills process', '[killed after 1.0s timeout]' in r3.stderr)
        else:
            skip('program output captured (no PTY in sandbox)')
            skip("key delivered (no PTY in sandbox)")
            skip('exit code propagated (no PTY in sandbox)')
            skip('no ANSI remnants (no PTY in sandbox)')
            skip('--lines truncation (no PTY in sandbox)')
            skip('late output drained (no PTY in sandbox)')
            skip('timeout kills process (no PTY in sandbox)')

        r4 = subprocess.run([sys.executable, tool], capture_output=True, text=True, timeout=15)
        check('--cmd required (exit != 0)', r4.returncode != 0)

        r5 = subprocess.run([sys.executable, tool, '-h'], capture_output=True, text=True, timeout=15)
        check('-h exits 0', r5.returncode == 0)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    if ok:
        if pty_ok:
            print('\nALL TESTS PASSED')
        else:
            print('\nALL TESTS PASSED (end-to-end PTY checks SKIPPED: this '
                  'sandbox denies /dev/ptmx; run in a PTY-capable env for full coverage)')
    else:
        print('\nSOME TESTS FAILED')
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point (see the module docstring for usage)."""
    args = sys.argv[1:] if argv is None else list(argv)
    if args and args[0] == '--selftest':
        return selftest()
    opts = {
        'wait': 0.5, 'keys': None, 'key_delay': 0.15, 'head': 0.5,
        'timeout': 10, 'strip': False, 'lines': None, 'keep': False,
    }
    cmd = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--cmd':
            cmd = parse_cmd(args[i + 1])
            i += 2
        elif a == '--wait':
            opts['wait'] = float(args[i + 1]); i += 2
        elif a == '--keys':
            opts['keys'] = args[i + 1]; i += 2
        elif a == '--key-delay':
            opts['key_delay'] = float(args[i + 1]); i += 2
        elif a == '--head':
            opts['head'] = float(args[i + 1]); i += 2
        elif a == '--timeout':
            opts['timeout'] = float(args[i + 1]); i += 2
        elif a == '--strip-ansi':
            opts['strip'] = True; i += 1
        elif a == '--lines':
            opts['lines'] = int(args[i + 1]); i += 2
        elif a == '--keep':
            opts['keep'] = True; i += 1
        elif a in ('-h', '--help'):
            print(__doc__); return 0
        else:
            print(f'unknown option: {a}'); print(__doc__); return 1
    if not cmd:
        print('error: --cmd is required'); return 1

    master, slave = pty.openpty()
    proc = subprocess.Popen(cmd, stdin=slave, stdout=slave, stderr=slave,
                            close_fds=True, preexec_fn=os.setsid)

    def read_available(timeout):
        out = b''
        end = time.time() + timeout
        while time.time() < end:
            r, _, _ = select.select([master], [], [], 0.05)
            if r:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
            elif out:
                time.sleep(0.05)
        return out

    total = b''
    try:
        time.sleep(opts['wait'])
        total += read_available(max(0.1, opts['wait']))
        if opts['keys']:
            for token in opts['keys'].split(','):
                tok = token.strip()
                if not tok:
                    continue
                os.write(master, encode_key(tok))
                time.sleep(opts['key_delay'])
            total += read_available(opts['head'])
        try:
            proc.wait(timeout=opts['timeout'])
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            if opts['keep']:
                print(f'[process still running after {opts["timeout"]}s]', file=sys.stderr)
                rc = None
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                print(f'[killed after {opts["timeout"]}s timeout]', file=sys.stderr)
                rc = -1

        # Drain anything still buffered in the pty since the last read window
        # (e.g. non-interactive programs that printed while we waited).
        total += read_available(0.2)
    finally:
        os.close(master)
        os.close(slave)

    if opts['strip']:
        total = ansi.strip(total)
    text = total.decode('utf-8', errors='replace')
    if opts['lines']:
        text = '\n'.join(text.split('\n')[:opts['lines']])
    sys.stdout.write(text)
    if text and not text.endswith('\n'):
        sys.stdout.write('\n')
    if rc is not None:
        print(f'[exit code: {rc}]', file=sys.stderr)
    return rc if rc is not None and rc >= 0 else 0


if __name__ == '__main__':
    sys.exit(main())
