# TOOL_AUTHORING.md - how to author a tool

A **tool** is a `.py` file that the Termite harness publishes to the model as a
callable API. This briefing is for any repo that contains tools: it describes the
harness's rules only, and assumes nothing about how this repo is laid out.

The harness's normative registry contract is `TOOL_SPECS/HARNESS.md` in the
Termite repo (rules R1-R7). What follows is the short operational form; follow it
literally.

## 1. The header every tool file carries

```python
#!/usr/bin/env python3
# TYPE: TOOLKIT
"""plan.py - One-line purpose of at most 8 plain words.
# keywords: plan|steps|outline|todo
# examples: import $name; print($name.steps("x"))

The description is everything else in the module docstring. Plain text,
no markdown, no emphasis characters.
"""
```

`# TYPE: TOOLKIT` must be on its own line within the first 15 lines of the file,
or the file is not a tool at all.

## 2. The rules, and how each one fails

Every rule is enforced at registration. A violation does not warn: the tool is
**refused** - the harness returns an error payload instead of the API, and the
inventory shows a `doc-error` row.

| rule | what it requires | fails as |
|---|---|---|
| R1 | `# TYPE: TOOLKIT` in the first 15 lines | file is invisible to the registry |
| R2 | the tool's name IS its import path under its toolkit root | see section 3 |
| R3 | docstring line 1 = purpose, <= 8 plain words, no `*` `_` backtick | `R3: no module docstring` |
| R4 | the rest of the docstring is the description | (no separate check) |
| R5 | a `# examples:` line, `\|`-separated | `R5: no # examples: directive` |
| R6 | every public `def` (no leading `_`, not `main`) has a docstring, every parameter and the return annotated, no bare `dict`/`list`/`tuple`/`set` | `R6: <fn> arg 'x' lacks type annotation` |
| R7 | a `# keywords:` line, lowercase words, `\|`-separated | `R7: no # keywords: directive` |

Two details that catch people:

- A `|` **inside quotes is literal text**, not an example separator:
  `pattern="int\|double"` is one argument. Quotes must balance, or the whole line
  is a doc-error.
- Declare payload shapes with `TypedDict`. `-> dict` and `-> dict[str, object]`
  both fail; `-> dict[str, int]` passes. The check is recursive, so `dict \| None`
  and `list[dict]` fail too.

## 3. Imports: the one thing that breaks silently

A tool is never imported by its bare file name. It is a member of a package, and
the package is the toolkit root it sits under:

- a project's own toolkit root is the reserved alias `workspace`, so
  `.../plan.py` is `workspace.plan`;
- a capability's tools install under that capability's alias, so the same file
  can be `tools.plan` or `coding.plan`.

So a file that ships with your tool is imported **as its installed path says**,
never by bare name:

```python
import helper          # WRONG: looks for a top-level module named 'helper'
```

A bare import works when you *run* the file as a script (`./plan.py`), which is
why this defect passes every manual test and then fails for the model. Use these
exact forms:

| your file imports | write |
|---|---|
| a sibling in the same directory | `if __package__:`<br>`    from . import helper`<br>`else:`<br>`    import helper` |
| a file inside your own package (`<name>/...`) | `from . import helper` |
| a file at the toolkit root, from a package tool | `if __package__ and "." in __package__:`<br>`    from .. import helper`<br>`else:`<br>`    import helper` |

**Why two branches.** They are not redundant: a single file is loaded two ways.
Imported as a tool, `__package__` is the package name and sibling files are
reachable only relatively (`from . import helper`); run as a script,
`__package__` is empty, the script's own directory is on the path, and only
`import helper` works. `__package__` is Python's own record of which case you are
in, so the conditional is a fact rather than a guess, and one branch is dead in
any given run. Nothing else changes: the name `helper` refers to the same module
either way.

If the tool does not need to be runnable as `./plan.py`, drop the CLI and keep
`from . import helper` alone - one line, no conditional. Do not use
`try/except ImportError`: it would also swallow a genuine failure inside
`helper` and retry it as a top-level import, turning a clear error into a
mystery.

Do not add the directory to `sys.path` as a workaround. That loads a *second*
copy of the same file under a different name (`helper` alongside
`workspace.helper`), so module state, anchors and `isinstance` checks silently
diverge.

## 4. Never hard-code your own name

A tool's source cannot know the name it will be registered under: it is the
import path chosen by whoever installs it. Write the literal `$name` wherever
your own name belongs, and the harness substitutes the registered one in the
purpose, the description and every example.

```python
# examples: import $name; print($name.steps("x"))
# in one repo this renders as: import workspace.plan; print(workspace.plan.steps(...))
# in another:                  import coding.plan;  print(coding.plan.steps(...))
```

An example that hard-codes a qualified name is wrong under some other alias.

## 5. Verify by importing - the inventory does not import

Listing tools parses the source; it does **not** import it. So a tool with a
broken sibling import (section 3) is advertised perfectly and fails the moment
the model calls it. Always finish by running, inside the harness's Python:

```python
import <root>.<file>              # e.g. workspace.plan, or <alias>.plan
print(<root>.<file>.steps("x"))   # run one published example end to end
```

`<root>` is whatever the inventory printed for this file: `workspace` for a
project's own toolkit root, the capability's alias when the tools are shipped
that way.

Then read your own tool's advertised detail back and run each example in it
verbatim. That is the only check that covers the examples: they must be
*runnable as written*. If an example passes a literal - a path, an id, an
anchor - the literal has to satisfy the tool's own validation, or the example
raises a validation error instead of demonstrating anything.

## 6. Audit the whole tree

The class of defect in section 3 can be found mechanically. Run this from the
repo root; it reports any file importing another file of the tree by bare name,
and any `sys.path` surgery:

```python
import glob, os, re

files = [p for p in glob.glob('**/*.py', recursive=True) if '__pycache__' not in p]
names = {os.path.basename(p)[:-3] for p in files}

for p in files:
    me = os.path.basename(p)[:-3]
    for i, line in enumerate(open(p, encoding='utf-8').read().splitlines(), 1):
        m = re.match(r'(?:import|from)\s+([A-Za-z_]\w*)\b', line)
        # unindented only: the indented `else: import x` fallback is correct
        if m and m.group(1) in names and m.group(1) != me and line[:1] not in (' ', '\t'):
            print("%s:%d: bare sibling import %r - use the section 3 form" % (p, i, m.group(1)))
        if 'sys.path.insert' in line or 'sys.path.append' in line:
            print("%s:%d: sys.path surgery - use the section 3 form" % (p, i))

print("checked %d files" % len(files))
```

Final line always prints, so "nothing reported" cannot be confused with "the
check did not run". It checks syntax only - section 5 covers what it cannot.

After editing any tool, restart Termite: a local manifest re-installs the toolkit
at start-up.
