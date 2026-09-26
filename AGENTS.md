# AGENTS.md

Working notes for AI agents (and humans) working on **Termite-Toolkit**.
Read this first; it is the index to everything else here.

## Purpose

This workspace exists to make the agent's job **naturally and automatically
token-efficient**.
"Token-efficient" means: whatever lands in the agent's context (tool output,
fetched pages, file reads) should be the minimum needed to do the job.
"Naturally and automatically" means the cheap path must be the one the agent
reaches for **without being coached** -- so the design target is not just fast
code but *discoverable* code: tool names, the `# keywords:` / `# examples:`
metadata, and the `list_tools` / `get_tool` descriptions are all written so that
discovery leads to the token-efficient option first.

Consequence for design: when two tools overlap, prefer **merging or renaming so
the efficient option is the obvious default** over adding another peer. A fetch
tool, for example, should surface its "grab just the lines I need" mode as the
natural first reach, not hide it in a separate tool.

## What this repo is

A collection of small, self-contained, dependency-light **TOOLKIT** modules that
an agent can import from `python_eval` or run from the shell. Each module does
one job well and ships with its own `--selftest`. The repo is currently
uncommitted (`main`, no commits) and evolving.

## Layout (and where to look)

| Path | What it is | Notes |
|------|-----------|-------|
| `src/` | The toolkit: reusable tool modules | Auto-registered (see *Tool registry*) |
| `TOOLKIT/` | Seeded **example** tools | `calculator.py`, `current_time.py`, `get_weather.py`; git-ignored |
| `docs/` | Long-form documentation (**empty**) | Prose that doesn't belong in a docstring |
| `tests/` | Test scaffolding (**empty**) | Prefer per-module `--selftest` (see *Validating*) |
| `tmp/` | Scratch space | Ephemeral, throwaway; never commit it |
| `pyproject.toml` | Project metadata | `name = "Termite-Toolkit"`, `version = "0.1.0"`, `requires-python = ">=3.11"` |
| `.gitignore` | Ignores `TOOLKIT/**`, `tmp/`, bytecode, and websearch caches | Caches (`.websearch_cache.json`, `.ddg_cooldown`, `.engine_rr`) are written next to `websearch.py` |

Prefer **`tmp/`** for any generated artifacts (diffs, reports, compiled
binaries) so they stay out of version control — e.g. `project_check.py` writes
reports to `tmp/<name>-check.json` and `diffside.py` examples render into `tmp/`.

## Tool registry

The filesystem **is** the registry: any `.py` file whose first 15 lines contain
`# TYPE: TOOLKIT` is registered automatically.

- Discover: `list_tools`
- Full usage (signatures, TypedDicts, examples): `get_tool(<name>)`

## Authoring a new tool

Follow the house style demonstrated by `TOOLKIT/calculator.py`:

```
#!/usr/bin/env python3
# TYPE: TOOLKIT
# keywords: |a|b|c|
# examples: |import mytool; mytool.run()|./mytool.py --selftest|
"""mytool.py - one-line purpose.

Longer description, Python API, and CLI usage.
"""
```

Rules: docstring's **first line is `"<name>.py - <purpose>"`**; public functions
are **fully type-annotated and documented**; declare shapes with `TypedDict`;
prefix disallowed helpers with `_`; provide a `--selftest` and a `main(argv)`.
New tools live in `src/`; add cross-references to them in this file.

## Validating

- One tool: `./<tool>.py --selftest` (or call its API from `python_eval`).
- Whole toolkit: `src/healthcheck.py -q` — probes toolchains, devices, network,
  rlimits, and runs **every** tool's `--selftest` (exit 0 = green).
- Project checks: `src/project_check.py [--root DIR | --manifest FILE]` runs the
  commands in a `project-check.json`, saved with `--out tmp/<name>.json` and
  re-rendered later with `--render`. See *project_check.py* below.

## Cross-references: which tool for what

**Reading & navigating code** — use `src/symmap.py` to get an outline of every
function/class/method with signatures, line ranges, and anchors.

**Editing** — use `src/hedit.py`, the hash-anchored, stale-safe line editor:
anchors look like `12#a1b2`, and an edit is rejected *before* writing if the
file changed since you read it. `symmap` anchors feed directly into `hedit`.
Prefer `hedit` over ad-hoc writes; use `src/fio.py` (read/list/head/tail/
write/append) or `src/fedit.py` for quick line-based tweaks.

**Reviewing changes** — use `src/diffside.py` to render a unified diff (from
`hedit --preview`, `git diff`, or `difflib`) or two files side-by-side as a
boxed table, in SGR color or HTML.

**C++ semantics** — use `src/clangd.py` to ask a real clangd for
compiler-accurate diagnostics, hover, definitions and references, driven by the
project's `compile_commands.json` (it never runs a build: a missing or stale
database is reported, not regenerated).

**Building/running C/C++** — `src/build.py` (compile C/C++, syntax-check, or run
a binary). **Driving interactive/TUI apps** — `src/pty_run.py` (feed keystrokes
through a pseudo-terminal and capture the raw ANSI stream; pair with
`src/ansi.py` to strip/inspect escapes).

**Formatting output** — `src/table.py` renders ANSI-safe Unicode tables;
`src/ansi.py` counts glyphs and strips/highlights SGR sequences in captured
output.

**Scheduling** — `src/cronexpr.py` computes upcoming fire times for cron
expressions (`next_runs`, `explain`).

**Web** — `src/webfetch.py` (URL -> Markdown/text/HTML; `urlgrep()` pulls just
the matching lines — token-efficient), `src/websearch.py` (DuckDuckGo HTML
search). These use the network; the sandbox network policy must allow internet
access.

## Environment helpers

Use the wrappers so tools find a writable `HOME`, pip cache, and git config:

- `src/envrun <cmd> [args...]` — run with the sandbox's writable HOME/pip/git env.
- `src/pip install ...` — pip via `envrun`.

`src/permissions-check.sh` (run after granting permissions) verifies sandbox
access to the paths the tools need.

## Conventions

- Keep modules **stdlib-only** unless there's a strong reason; if you need a
  package, install it through `src/pip`.
- One concern per module; expose a small, typed public API plus a CLI.
- Write artifacts to `tmp/`; keep `TOOLKIT/` caches untracked.
- Documentation lives in the module docstring; use `docs/` for anything longer.
