# Capability manifests

The repo root holds four manifests -- `edit.json`, `misc.json`, `network.json`,
`cpp.json` -- each partitioning the `src/` tools into one capability. This
document owns the schema and its rules; the manifests themselves are the
allocation.

## Schema

```json
{
    "description": "One line on what this capability is for.",
    "skills": [],
    "tools": [
        { "tool": "src/symmap.py", "requires": [ "src/ansi.py" ] },
        { "tool": "src/hedit.py",  "requires": [ "src/ansi.py" ] }
    ]
}
```

Top level:

- `description` -- a one-line summary of the capability.
- `skills` -- `SKILL.md` paths the capability needs; `[]` when none.
- `tools` -- the capability's tool entries, one per tool.

Per tool entry:

- `tool` -- the tool's entry `.py` file, a path relative to the manifest's own
  directory (`src/...` here).
- `requires` -- optional; the other files the tool installs with -- a sibling
  tool it imports (`src/ansi.py`), or non-Python payload a wrapper carries
  (`envrun`, `pip`, `permissions-check.sh` in `envwrap`'s entry).

There is no install-root field: every tool installs the same way (see
*Discovery*). A tool may appear in more than one manifest -- `ansi.py` is in
both `edit.json` and `misc.json`.

## Discovery

A manifest installs as one capability under an alias the installer chooses, and
each tool in it is imported as `<alias>.<tool>` -- this repo's editing tools are
`<alias>.symmap`, `<alias>.hedit`, and so on, whatever the alias turns out to
be. A tool's source cannot know the alias, so its own docs call it `$name`; the
import rules, including the sibling-import form, are in
`docs/TOOL_AUTHORING.md`.

## Shell helpers: payload behind a Python entry

An entry is always a tool's `.py` module; a non-`.py` script is never an entry.
The project convention is **scripts as payload behind a Python wrapper**: the
wrapper's `requires` carries the scripts, and the wrapper's module docstring
documents the `sh` invocation. Worked example: `src/envwrap.py` (misc.json)
carries `envrun`, `pip` and `permissions-check.sh`.

## The manifests

| Manifest | Groups |
|---|---|
| `edit.json` | reading and editing workspace text files |
| `cpp.json` | C/C++ toolchain semantics (compiler, language server) |
| `network.json` | internet / local-network access |
| `misc.json` | everything else |

Each manifest's own `description` says what its capability is for.

Allocation rules:

- A manifest groups by **capability**. A language-specific manifest is for a
  **toolchain** (compiler/LSP), not for every tool that happens to touch that
  language -- `symmap.py` stays with the editing tools, not with `cpp.json`.
- Fewer manifests over more peers: two tools sharing a theme do not justify a
  manifest until the capability grows. Keep the split point clean so the move
  is cheap later (e.g. `healthcheck.py` + `project_check.py` can become a
  `checks.json` when check tooling grows).
