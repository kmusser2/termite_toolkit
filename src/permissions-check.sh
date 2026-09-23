#!/bin/sh
# permission check for llmchat-sandbox - run AFTER granting permissions.
# (deliberately avoids /dev/null redirections, since /dev/null may be denied)
# NOTE: /dev/urandom + /dev/random are granted READ-ONLY (all git needs);
#       O_RDWR on them being denied is expected and fine.
ERR=/var/lib/llmchat-sandbox/.permcheck.err
SCRATCH=${TMPDIR:-/var/lib/llmchat-sandbox}

probe() { # probe <r|rw> <path>  -- r=O_RDONLY, rw=O_RDWR
  mode=$1; path=$2
  if [ "$mode" = "r" ]; then
    if (exec 3<"$path") 2>"$ERR"; then echo "OPEN  r   $path : OK";     else echo "OPEN  r   $path : DENIED"; fi
  else
    if (exec 3<>"$path") 2>"$ERR"; then echo "OPEN  rw  $path : OK";     else echo "OPEN  rw  $path : DENIED"; fi
  fi
}

echo "--- device nodes (mode = what consumers actually need) ---"
probe rw /dev/null      # git hardcodes it (O_RDWR)
probe r  /dev/urandom   # git temp object names (O_RDONLY enough)
probe r  /dev/random
probe r  /dev/zero      # optional; not currently required
probe rw /dev/tty       # optional; interactive prompts only
probe rw /dev/ptmx      # PTY support

echo "--- scratch dirs (note: launcher already sets TMPDIR into the sandbox) ---"
for d in /tmp /var/tmp /dev/shm; do
  if touch "$d/.probe" 2>"$ERR"; then echo "WRITE $d  : OK"; rm -f "$d/.probe"; else echo "WRITE $d  : DENIED (expected; use workdir)"; fi
done

echo "--- rlimits (soft=hard) ---"
echo "CPU     : $(ulimit -t) sec        (target 600)"
echo "NOFILE  : $(ulimit -n)            (target 1024)"
echo "FSIZE   : $(ulimit -f) x512B-blk = $(( $(ulimit -f) / 2048 / 1024 ))GiB  (target 4GiB)"
echo "AS      : $(ulimit -v) KB         (raised from 2GiB; confirm target: 4GiB vs 8GiB)"

echo "--- git native (no LD_PRELOAD shim) ---"
smoke="$SCRATCH/.permcheck-git-$$"
rm -rf "$smoke"; mkdir -p "$smoke" || { echo "git smoke : FAIL (cannot mkdir $smoke)"; exit 1; }
if ( cd "$smoke" && git init -q \
     && echo x > f \
     && git add f \
     && GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t git commit -qm smoke \
     && git fsck --no-dangling >"$ERR" 2>&1 ); then
  echo "git smoke : OK (init/add/commit/fsck)"
else
  echo "git smoke : FAIL (grant /dev/null + read /dev/urandom)"
fi
rm -rf "$smoke"

echo "--- network ---"
for u in https://api.github.com https://pypi.org; do
  if command -v curl >"$ERR" 2>&1; then
    code=$(curl -s -m 5 -o "$SCRATCH/.curl.out" -w "%{http_code}" "$u" 2>"$ERR") || code="FAIL"
    echo "GET $u : HTTP $code"
  elif python3 -c "import urllib.request; urllib.request.urlopen('$u',timeout=5)" 2>"$ERR"; then
    echo "GET $u : OK"
  else
    echo "GET $u : FAIL"
  fi
done
rm -f "$ERR" "$SCRATCH/.curl.out"
echo "done."
