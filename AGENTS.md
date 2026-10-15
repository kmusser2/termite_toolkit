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
| `docs/` | Long-form documentation | `docs/TOOL_AUTHORING.md` (generic tool-authoring contract), `docs/TOOL_AUTHORING_NOTES.md` (how it applies here), `docs/CAPABILITIES.md` (capability manifest schema & rules) |
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

Capability manifests (`edit.json`, `misc.json`, `network.json`, `cpp.json`
at the repo root) partition the tools; their schema and rules are documented
in `docs/CAPABILITIES.md`.

## Authoring a new tool

Follow the house style demonstrated by `TOOLKIT/calculator.py`:

```
#!/usr/bin/env python3
# TYPE: TOOLKIT
# keywords: |a|b|c|
# examples: |import $name; $name.run()|./mytool.py --selftest|
"""mytool.py - one-line purpose.

Longer description, Python API, and CLI usage.

    import $name
    $name.run("thing")   # -> Result
"""
```

Rules: docstring's **first line is `"<name>.py - <purpose>"`** (the literal
filename); public functions are **fully type-annotated and documented**; declare
shapes with `TypedDict`; prefix disallowed helpers with `_`; provide a
`--selftest` and a `main(argv)`. New tools live in `src/`; add cross-references
to them in this file.

The **full, normative authoring contract** is `docs/TOOL_AUTHORING.md` -- header
shape, the registry rules (R1-R7) and how each one fails, the import forms for
sibling tools, and the `$name` placeholder. Read it before writing or editing
any tool; `docs/TOOL_AUTHORING_NOTES.md` records how it applies to *this* repo
(`src/`, the capability manifests, the `--selftest` checks).

### `$name` -- how docs name this tool

The rendered docs are read by a model running in *someone else's* sandbox, and
the import root there is the user's to choose: the same tool is
`edittools.hedit` in one workspace and `capability1.hedit` in another. The
author cannot know it, so Python code in a tool's docs refers to the tool as
`$name`, and the harness substitutes the real import path when it renders the
card in `list_tools()` / `get_tool()`:

    in the source                 rendered, for a capability named 'edittools'
    import $name                  import edittools.hedit
    $name.read_file("a.py")       edittools.hedit.read_file("a.py")

- `$name` goes in the `# examples:` directive and in any docstring block that
  shows Python calling *this* tool.
- The literal name stays wherever it is not code: the docstring's first line
  (`hedit.py - ...`), CLI invocations (`./hedit.py read <file>`), usage lists,
  and prose.
- Never write `$name.py` -- it renders as `edittools.hedit.py`.
- Never in executable code: a sibling tool is imported with the *conditional
  sibling form* -- `from . import fio` when this file is imported as part of a
  package, plain `import fio` when it is run as a script -- and never with
  `sys.path` surgery. `$name` means *this* tool only; there is no placeholder
  for another tool, so an example that needs a sibling names it as a filename
  in prose instead. Details: `docs/TOOL_AUTHORING.md`, section 3.

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
anchors look like `12#NKA`, and an edit is rejected *before* writing if the
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

These shell helpers ship as payload behind the Python entry `src/envwrap.py`
(capability manifest `misc.json`); `import envwrap` or `./envwrap.py --selftest`.

## Conventions

- Keep modules **stdlib-only** unless there's a strong reason; if you need a
  package, install it through `src/pip`.
- One concern per module; expose a small, typed public API plus a CLI.
- Write artifacts to `tmp/`; keep `TOOLKIT/` caches untracked.
- Documentation lives in the module docstring; use `docs/` for anything longer.
