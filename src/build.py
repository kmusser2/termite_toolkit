#!/usr/bin/env python3
# TYPE: TOOLKIT
"""build.py - compile & run C/C++ with sensible flags.
# keywords: compile|build|c|cpp|c++|gcc|g++|make|run|binary|source|flags|syntax|check
# examples: import $name; $name.build("g++", "c++17", "tetris.cpp", out="tetris")

Usage:
  build.py cpp <source.cpp> [output]   compile C++ (default output: source w/o ext)
  build.py c   <source.c>   [output]   compile C
  build.py check <source.cpp>          syntax-check only (output to /dev/null)
  build.py run <binary> [args...]      run a binary and pass through its exit code
  build.py --selftest                run self tests

Examples:
  build.py cpp tetris.cpp
  build.py run tetris --selftest
"""
import os
import subprocess
import sys

__all__ = ["resolve", "build", "syntax_check", "selftest", "main"]

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # workspace root (this file lives in <root>/src/)
SELF = os.path.abspath(__file__)  # this module, for CLI self-invocation in the selftest


def resolve(p: str) -> str:
    """Resolve a path relative to the workspace root to an absolute path."""
    return p if os.path.isabs(p) else os.path.join(BASE, p)


def _intermediates(src, out):
    """-save-temps artifact names (gcc naming), in the source's directory."""
    src_dir = os.path.dirname(src) or '.'
    src_base = os.path.basename(os.path.splitext(src)[0])
    out_base = os.path.basename(os.path.splitext(out)[0])
    names = set()
    for ext in ('.s', '.o', '.i', '.ii'):
        names.add(os.path.join(src_dir, src_base + ext))
        names.add(os.path.join(src_dir, out_base + '-' + src_base + ext))
    return sorted(names)


def build(compiler: str, std: str, src: str, out: str | None = None) -> int:
    """Compile src with the given compiler/std to out (or a default name, the source without its extension). Returns the compiler exit code."""
    if not out:
        out = os.path.splitext(src)[0]
    # ponytail: -save-temps kept as a robust fallback though the sandbox
    # O_TRUNC deny was lifted 2026-08-16; drop it once gcc temp handling is
    # re-verified in the sandbox
    # Stale intermediates are removed first so gcc never re-opens them.
    for p in _intermediates(src, out):
        if os.path.exists(p):
            os.remove(p)
    cmd = [compiler, f'-std={std}', '-O2', '-Wall', '-Wextra', '-save-temps', src, '-o', out]
    print('+', ' '.join(cmd))
    r = subprocess.run(cmd)
    if r.returncode == 0:
        for p in _intermediates(src, out):
            if os.path.exists(p):
                os.remove(p)
        print(f'OK -> {out} ({os.path.getsize(out):,} bytes)')
    return r.returncode


def syntax_check(compiler: str, std: str, src: str) -> int:
    """Check that src compiles, producing no binary. Returns the compiler exit code."""
    # ponytail: compile to a throwaway .o in tmp/ instead of gcc -fsyntax-only,
    # because the sandbox denies opening /dev/null for writing (Errno 13);
    # switch to -fsyntax-only if that deny is ever lifted
    import tempfile
    fd, tmpobj = tempfile.mkstemp(suffix='.o', dir=os.path.join(BASE, 'tmp'))
    os.close(fd)
    os.remove(tmpobj)
    cmd = [compiler, f'-std={std}', '-c', '-Wall', '-Wextra', src, '-o', tmpobj]
    print('+', ' '.join(cmd))
    r = subprocess.run(cmd)
    try:
        os.remove(tmpobj)
    except OSError:
        pass
    if r.returncode == 0:
        print(f'OK syntax check ({src})')
    return r.returncode


def selftest() -> int:
    """Verify compile (fresh, overwrite, c, check, default out), run passthrough,
    intermediate cleanup, error paths, -h. Uses tmp/ only."""
    import shutil
    import tempfile
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("PASS" if cond else "FAIL") + "  " + name)
        if not cond:
            ok = False

    d = tempfile.mkdtemp(dir=os.path.join(BASE, 'tmp'))
    try:
        check('resolve relative', resolve('tmp') == os.path.join(BASE, 'tmp'))
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            passed = main(['-h']) == 0
        check('main(argv) honors argv', passed)
        check('resolve absolute passthrough', resolve('/abs/x') == '/abs/x')
        check('intermediates include .s', '/d/a.s' in _intermediates('/d/a.cpp', '/d/a'))

        src = os.path.join(d, 'probe.cpp')
        out = os.path.join(d, 'probe')
        with open(src, 'w') as f:
            f.write('#include <cstdio>\nint main(){std::printf("v1\\n");return 0;}\n')
        r = build('g++', 'c++17', src, out)
        check('fresh cpp compile rc=0', r == 0)
        check('binary exists', os.path.exists(out))
        rp = subprocess.run([out], capture_output=True, text=True)
        check('binary runs v1', rp.returncode == 0 and rp.stdout.strip() == 'v1')
        leftovers = [f for f in os.listdir(d) if f.endswith(('.s', '.o', '.i', '.ii'))]
        check('no -save-temps leftovers', not leftovers)

        with open(src, 'w') as f:
            f.write('#include <cstdio>\nint main(){std::printf("v2\\n");return 0;}\n')
        r = build('g++', 'c++17', src, out)
        check('overwrite existing -o rc=0', r == 0)
        rp = subprocess.run([out], capture_output=True, text=True)
        check('overwritten binary is v2', rp.stdout.strip() == 'v2')

        r = build('g++', 'c++17', src)
        check('default out name', r == 0 and os.path.exists(os.path.splitext(src)[0]))

        csrc = os.path.join(d, 'probe.c')
        cout = os.path.join(d, 'probec')
        with open(csrc, 'w') as f:
            f.write('#include <stdio.h>\nint main(){printf("c-ok\\n");return 0;}\n')
        r = build('gcc', 'c17', csrc, cout)
        check('c compile rc=0', r == 0)
        rp = subprocess.run([cout], capture_output=True, text=True)
        check('c binary runs', rp.stdout.strip() == 'c-ok')

        r = syntax_check('g++', 'c++17', src)
        check('syntax check ok', r == 0)
        bad = os.path.join(d, 'bad.cpp')
        with open(bad, 'w') as f:
            f.write('int main( { oops }\n')
        r = syntax_check('g++', 'c++17', bad)
        check('syntax error detected', r != 0)
        r = build('g++', 'c++17', os.path.join(d, 'missing.cpp'), out)
        check('missing source -> nonzero', r != 0)

        exsrc = os.path.join(d, 'ex.cpp')
        exout = os.path.join(d, 'ex')
        with open(exsrc, 'w') as f:
            f.write('int main(){return 5;}\n')
        r = build('g++', 'c++17', exsrc, exout)
        check('exit-code binary compiles', r == 0)
        rr = subprocess.run([sys.executable, SELF,
                             'run', exout], capture_output=True, text=True)
        check('run passes exit code 5 through', rr.returncode == 5)
        argsrc = os.path.join(d, 'args.cpp')
        argout = os.path.join(d, 'args')
        with open(argsrc, 'w') as f:
            f.write('#include <cstdio>\nint main(int c, char** v){'
                    'std::printf("%s\\n", v[1]);return 0;}\n')
        r = build('g++', 'c++17', argsrc, argout)
        check('args binary compiles', r == 0)
        rr = subprocess.run([sys.executable, SELF,
                             'run', argout, 'hello'], capture_output=True, text=True)
        check('run passes args through', rr.stdout.strip() == 'hello')
        rr = subprocess.run([sys.executable, SELF, '-h'],
                            capture_output=True, text=True)
        check('-h exits 0', rr.returncode == 0)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print('\n%s' % ('ALL TESTS PASSED' if ok else 'SOME TESTS FAILED'))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point (see the module docstring for usage)."""
    args = sys.argv[1:] if argv is None else list(argv)
    if not args or args[0] in ('-h', '--help', 'help'):
        print(__doc__)
        return 0 if args else 1
    if args[0] == '--selftest':
        return selftest()
    cmd = args[0]
    rest = args[1:]
    if cmd == 'cpp':
        if not rest:
            print('usage: build.py cpp <source.cpp> [output]'); return 1
        return build('g++', 'c++17', resolve(rest[0]), resolve(rest[1]) if len(rest) > 1 else None)
    if cmd == 'c':
        if not rest:
            print('usage: build.py c <source.c> [output]'); return 1
        return build('gcc', 'c17', resolve(rest[0]), resolve(rest[1]) if len(rest) > 1 else None)
    if cmd == 'check':
        if not rest:
            print('usage: build.py check <source.cpp>'); return 1
        return syntax_check('g++', 'c++17', resolve(rest[0]))
    if cmd == 'run':
        if not rest:
            print('usage: build.py run <binary> [args...]'); return 1
        return subprocess.run([resolve(rest[0])] + rest[1:]).returncode
    print(f'unknown command: {cmd}')
    return 1


if __name__ == '__main__':
    sys.exit(main())
