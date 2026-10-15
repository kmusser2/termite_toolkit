# TOOL_AUTHORING_NOTES.md - applying the authoring briefing here

`docs/TOOL_AUTHORING.md` is the harness's authoring contract and is kept
**repo-agnostic** for publication. This file is its companion for *this* repo
(Termite-Toolkit): the concrete parameters the briefing deliberately leaves
open, and how its rules land on the files here. If this file and the briefing
ever disagree about a generic rule, the briefing wins -- a mismatch here is the
bug.

## Where the tools live, and how the briefing's names map here

- Tools are the `.py` files under **`src/`**. The filesystem is the registry: a
  file whose first 15 lines carry `# TYPE: TOOLKIT` is published.
- Install and discovery are grouped by **capability manifest**
  (`edit.json`, `misc.json`, `network.json`, `cpp.json` at the repo root); the
  manifest schema is owned by `docs/CAPABILITIES.md`.
- A tool's import path is `<alias>.<file>`, and the alias is the capability's
  manifest name: here the installed capability is `edit` (the `edit.json`
  manifest), so `src/hedit.py` imports as **`edit.hedit`**. The tool source
  cannot know that alias, so prose and examples say `$name` (briefing sections
  3-4).
- A **sibling** import is code, not `$name`: it uses the conditional form of
  briefing section 3. `$name` means *this* tool only; there is no placeholder for
  another tool, so an example that needs a sibling names it as a filename in
  prose. See also `AGENTS.md`, *`$name` -- how docs name this tool*.

## Applying section 3 (the one silent failure)

Every sibling import in `src/` uses the briefing's conditional form. This repo
previously carried two `sys.path.insert` workarounds (`symmap.py`,
`websearch.py`); both are gone. Do **not** reintroduce that pattern -- it loads a
second copy of a module and silently diverges anchors and `isinstance` checks
(briefing section 3). A new tool that imports a sibling copies the form from any
existing `src/*.py`.

Whether any bare sibling import remains is answered mechanically by the audit
snippet in briefing section 6, run from the repo root; it currently reports
clean. That snippet walks `**/*.py`, which here also picks up the git-ignored
`tmp/`; scope it to `src/` when you want src-only, noise-free output.

## Applying section 5 (verifying, and example literals)

- Script mode, per tool: `./src/<tool>.py --selftest` -- this exercises the
  `else: import <sibling>` branch. Whole toolkit: `src/healthcheck.py -q` runs
  every tool's `--selftest`.
- Import mode: `import workspace.<tool>` from the harness's Python, then call one
  example end to end.
- An example's literal must satisfy the tool's own validation (briefing section
  5). The trap here is `hedit` anchors, which are `LN#HASH` with `HASH` exactly
  **3 characters** from `HASH_ALPHABET` (32 symbols, no `0/O/1/I`) -- see
  `src/hedit.py`. `12#NKA` is a valid example literal; the `12#a1b2` that used to
  appear in `hedit`'s own examples and in `AGENTS.md` was malformed, and has been
  corrected.

## Repo conventions the briefing does not state

These live in `AGENTS.md`, which is the index; keep them there rather than here:
stdlib-only modules, one concern per module, generated artifacts to the
git-ignored `tmp/`, documentation in the module docstring, and adding new tools
to the `AGENTS.md` cross-reference and to a capability manifest.

## After editing any tool

Restart Termite: a local manifest re-installs the toolkit at start-up (briefing,
closing note).
