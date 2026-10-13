# Capability manifests

The repo root holds four manifests -- `workspace.json`, `network.json`,
`cpp.json`, `misc.json` -- each partitioning the `src/` tools into one
capability. This document owns the schema and its rules; the manifests
themselves are the allocation.

## Schema

```json
{
    "skills": [],
    "tools": [
        {
            "init": "src/envwrap.py",
            "files": [
                "src/envwrap.py",
                "src/envrun",
                "src/pip",
                "src/permissions-check.sh"
            ],
            "root": false
        }
    ]
}
```

- `skills` -- `SKILL.md` paths for skills the capability needs; `[]` when none.
- `tools` -- tool entries, one per tool.

Per tool entry:

- `init` -- the **entry module**: a `.py` path relative to the manifest's own
  directory. Every file in `files` is fetched, and the entry is always written
  as `__init__.py` of the installed package, whatever its published filename.
- `files` -- every file belonging to the tool. The entry must be among them,
  and every file must sit under the entry's own directory.
- `root` -- chooses only the install root (default `false`).

## Install and discovery

> `init` is the entry module and is installed as the package's `__init__.py`.
> `root: true` means that package is placed at the alias root, so the tool is
> discovered as the alias and imported as `import <alias>`; `root: false`
> places it under `tools/<name>/`, discovered as the entry's basename (or,
> for an entry named `__init__.py`, its directory) and imported as
> `import <alias>.<name>`. A file named `websearch.py` does not force
> `root: false` -- with `root: true` it simply becomes `<alias>/__init__.py`.

- `root: true` -- the discovered name is the alias (`tool_spec_name`). At most
  one `root: true` per manifest.
- `root: false` -- `<name>` is the entry basename minus `.py`, or, only if the
  entry is *literally* named `__init__.py`, the name of the directory it
  sits in.

| Manifest entry | Discovered name | Installs at | Imported as |
|---|---|---|---|
| `{"init": "src/websearch.py", "files": [...], "root": true}` | the alias | `tools/__init__.py`, `tools/helper.py` | `import <alias>` |
| `{"init": "src/websearch.py", "files": [...]}` | `websearch` | `tools/websearch/__init__.py`, `tools/websearch/helper.py` | `import <alias>.websearch` |
| `{"init": "websearch/__init__.py", "files": [...]}` | `websearch` (its directory) | `tools/websearch/__init__.py`, ... | `import <alias>.websearch` |
| `{"init": "websearch/__init__.py", "files": [...], "root": true}` | the alias | `tools/__init__.py`, ... | `import <alias>` |

## Validation

- The entry must be a `.py` path. A shell entry (e.g. `src/permissions-check.sh`)
  fails the `.py` check -- and, being `permissions-check`, the name check below.
- The entry's basename must be a valid identifier, even when `root: true`.
- All `files` must sit under the entry's own directory. So
  `{"init": "src/websearch.py", "files": ["src/websearch.py", "other.py"]}` is
  refused ("... is outside the 'init' directory 'src'").
- Duplicate names are checked on the **init-derived** name, not the alias -- a
  quirk worth knowing: `root: true` on `src/websearch.py` plus a sibling
  `other/websearch.py` is refused as "lists the tool 'websearch' twice", even
  though the two would resolve to different import paths.

## Shell helpers: payload behind a Python entry

A non-`.py` script can never be an entry. The project convention is the smaller
version: **scripts as package payload behind a Python entry, with sh-invocation
documented in the wrapper**. The wrapper's `files` carry the scripts, and its
module docstring documents `sh` invocation.

Worked example: `src/envwrap.py` (misc.json) carries `envrun`, `pip` and
`permissions-check.sh` as payload and documents how to invoke them.

## The manifests

| Manifest | Capability |
|---|---|
| `workspace.json` | navigating and manipulating a workspace |
| `cpp.json` | C/C++ toolchain semantics (compiler, language server) |
| `network.json` | efficient access to internet/localnet resources |
| `misc.json` | other |

Allocation rules:

- A manifest groups by **capability**. A language-specific manifest is for a
  **toolchain** (compiler/LSP), not for every tool that happens to touch that
  language -- `symmap.py` stays in `workspace.json`.
- Fewer manifests over more peers: two tools sharing a theme do not justify a
  manifest until the capability grows. Keep the split point clean so the move
  is cheap later (e.g. `healthcheck.py` + `project_check.py` can become a
  `checks.json` when check tooling grows).
