#!/usr/bin/env python3
# TYPE: TOOLKIT
"""ansi.py - inspect ANSI escapes in captured output.
# keywords: ansi|escape|color|colour|terminal|sequences|frames|glyph|sgr|256|codes
# examples: ./ansi.py strip frame.bin | ./ansi.py colors frame.bin

Usage:
  ansi.py strip [file]             strip escape codes (stdin if no file)
  ansi.py colors [file]            per-line 256-color bg/fg codes
  ansi.py count <glyph> [file]     count a unicode glyph, e.g. ═ or ▒
  ansi.py frames [file]            count frames (separated by \x1b[H home cursor)
  ansi.py --selftest              run self tests
"""
import os
import re
import sys

# The escape-sequence patterns -- one owner for "what an ANSI escape looks
# like". The str variants are derived from the bytes patterns, never copied.
CSI = re.compile(rb'\x1b\[[0-?]*[ -/]*[@-~]')  # full CSI grammar (ECMA-48)
BG = re.compile(rb'\x1b\[48;5;(\d+)m')
FG = re.compile(rb'\x1b\[38;5;(\d+)m')
OSC = re.compile(rb'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)')  # OSC title/hyperlink sequences
CSI_TEXT = re.compile(CSI.pattern.decode())
OSC_TEXT = re.compile(OSC.pattern.decode())


def strip(data: bytes) -> bytes:
    """Remove every ANSI escape sequence (CSI and OSC) from bytes."""
    return OSC.sub(b'', CSI.sub(b'', data))


def strip_str(text: str) -> str:
    """Remove every ANSI escape sequence (CSI and OSC) from a str."""
    return OSC_TEXT.sub('', CSI_TEXT.sub('', text))


__all__ = ["load", "strip", "strip_str",
           "cmd_strip", "cmd_colors", "cmd_count", "cmd_frames",
           "selftest", "main"]


def load(args: list[str]) -> bytes:
    """Read a file (or stdin when args is empty or '-') and return its raw bytes."""
    if args and args[0] != '-':
        with open(args[0], 'rb') as f:
            return f.read()
    return sys.stdin.buffer.read()


def cmd_strip(data: bytes) -> None:
    """Strip ANSI CSI/OSC sequences from data, write to stdout, ensuring a trailing newline."""
    sys.stdout.buffer.write(strip(data))
    if not data.endswith(b'\n'):
        sys.stdout.buffer.write(b'\n')


def cmd_colors(data: bytes) -> None:
    """Print per-line 256-color bg/fg codes plus counts of ghost (\u2592) and \u2550 glyphs."""
    for i, line in enumerate(data.split(b'\n')):
        bg = [int(x) for x in BG.findall(line)]
        fg = [int(x) for x in FG.findall(line)]
        ghost = line.count(b'\xe2\x96\x92')
        eq = line.count(b'\xe2\x95\x90')
        if bg or fg or ghost or eq:
            print(f'line {i:3d}: bg={bg} fg={fg} ghost_chars={ghost} ═={eq}')


def cmd_count(data: bytes, glyph: str) -> None:
    """Print how many times glyph appears in data."""
    gb = glyph.encode('utf-8')
    print(data.count(gb))


def cmd_frames(data: bytes) -> None:  # frames are separated by the "home cursor" escape \x1b[H
    """Print the number of frames in data (separated by the \x1b[H home-cursor escape)."""
    # frames are separated by the "home cursor" escape \x1b[H
    print(len(data.split(b'\x1b[H')) - 1)


def selftest() -> int:
    """Verify strip/colors/count/frames regexes and commands (in-memory)."""
    import io
    ok = True

    real_stdout = sys.stdout

    def check(name, cond):
        nonlocal ok
        print(("PASS" if cond else "FAIL") + "  " + name, file=real_stdout)
        if not cond:
            ok = False

    check('CSI strips color', CSI.sub(b'', b'\x1b[31mred\x1b[0m') == b'red')
    check('CSI strips 256-color bg', CSI.sub(b'', b'\x1b[48;5;196mX') == b'X')
    check('CSI strips home', CSI.sub(b'', b'\x1b[H') == b'')
    check('CSI strips clear', CSI.sub(b'', b'\x1b[2J') == b'')
    check('CSI leaves plain', CSI.sub(b'', b'plain') == b'plain')
    check('BG finds 196', BG.findall(b'\x1b[48;5;196m') == [b'196'])
    check('FG finds 51', FG.findall(b'\x1b[38;5;51m') == [b'51'])

    class FakeOut:
        def __init__(self):
            self.buffer = io.BytesIO()
        def write(self, s):
            data = s.encode('utf-8') if isinstance(s, str) else s
            self.buffer.write(data)
            return len(data)
        def flush(self):
            pass

    old = sys.stdout
    try:
        sys.stdout = FakeOut()
        cmd_strip(b'hi \x1b[1;32mgreen\x1b[0m bye\n')
        check('strip cmd', sys.stdout.buffer.getvalue() == b'hi green bye\n')
        sys.stdout = FakeOut()
        cmd_strip(b'no newline')
        check('strip cmd adds newline', sys.stdout.buffer.getvalue() == b'no newline\n')
        sys.stdout = FakeOut()
        cmd_count(b'\xe2\x95\x90\xe2\x95\x90x', '\u2550')
        check('count = 2', sys.stdout.buffer.getvalue().strip() == b'2')
        sys.stdout = FakeOut()
        cmd_frames(b'aa\x1b[Hbb\x1b[Hcc')
        check('frames = 2', sys.stdout.buffer.getvalue().strip() == b'2')
        sys.stdout = FakeOut()
        cmd_colors(b'line0\n\x1b[48;5;196m\x1b[38;5;51m\xe2\x96\x92\n')
        out = sys.stdout.buffer.getvalue().decode()
        check('colors bg=196', 'bg=[196]' in out)
        check('colors fg=51', 'fg=[51]' in out)
        check('colors ghost=1', 'ghost_chars=1' in out)
        sys.stdout = FakeOut()
        cmd_strip(b'\x1b[?1049h\x1b[2J\x1b[Hframe')
        check('strip alt-screen seq', sys.stdout.buffer.getvalue() == b'frame\n')
        sys.stdout = FakeOut()
        cmd_strip(b'\x1b]0;title\x07hi \x1b]8;;http://x\x1b\\link\x1b]8;;\x1b\\')
        check('strip OSC title+link', sys.stdout.buffer.getvalue() == b'hi link\n')
    finally:
        sys.stdout = old
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        argv_ok = main(['-h']) == 0 and main([]) == 1
    check('main(argv) honors argv', argv_ok)
    import subprocess
    tool = os.path.abspath(__file__)
    rr = subprocess.run([sys.executable, tool, '-h'], capture_output=True, timeout=15)
    check('CLI -h exits 0', rr.returncode == 0)
    rr = subprocess.run([sys.executable, tool, 'bogus-op'], capture_output=True, timeout=15)
    check('CLI unknown op exits 1', rr.returncode == 1)
    print('\n%s' % ('ALL TESTS PASSED' if ok else 'SOME TESTS FAILED'))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point (see the module docstring for usage)."""
    args = sys.argv[1:] if argv is None else list(argv)
    if not args or args[0] in ('-h', '--help'):
        print(__doc__)
        return 0 if args else 1
    if args[0] == '--selftest':
        return selftest()
    op = args[0]
    rest = args[1:]
    if op == 'strip':
        cmd_strip(load(rest))
    elif op == 'colors':
        cmd_colors(load(rest))
    elif op == 'count':
        if not rest:
            print('usage: ansi.py count <glyph> [file]'); return 1
        cmd_count(load(rest[1:]), rest[0])
    elif op == 'frames':
        cmd_frames(load(rest))
    else:
        print(f'unknown: {op}'); return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
