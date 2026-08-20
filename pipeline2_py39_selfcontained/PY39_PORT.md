# The Python 3.9 port

What this folder is, what changed from `../py_pipeline/`, and how it was proven
to compute exactly the same thing.

## Why

The research room runs **Python 3.9.7**, has no internet, cannot install
packages, and cannot be upgraded. `py_pipeline/` declared a 3.10 floor
(`check_bundle.py`, `_paste/RUNBOOK.md`), so on paper the room did not meet the
bundle's requirement.

It turned out the pipeline itself never needed 3.10. The whole tree is standard
library only, uses no 3.10+ syntax or API, and every engine module already
carries `from __future__ import annotations`. **The engines were not modified at
all** — not one line of `pyengine/`. The 3.10 floor came from a single call in
the bundle's own self-check.

`../py_pipeline/` is untouched and remains the development tree. This folder is
the shippable 3.9 copy.

## What actually changed

Four things, in descending order of how much they matter.

### 1. `check_bundle.py` no longer needs 3.10

`sys.stdlib_module_names` arrived in 3.10, and `check_bundle.py` used it to tell
a stdlib import from a third-party one. On 3.9 it bailed out with
`need Python >= 3.10 to run this check`.

It now carries a frozen copy of that list (306 names, ~3.4 KB of ASCII) and uses
it only when the real one is absent:

```python
stdlib = getattr(sys, "stdlib_module_names", None) or _STDLIB_NAMES
```

So the check is authoritative on 3.10+ and correct on 3.9 — which means it can
now be run **inside the room**, on the machine that will actually execute the
pipeline, rather than only on the machine that packed it. `MIN_PY` is `(3, 9)`.

The list is frozen rather than probed with `importlib`, because this script's
whole job is to assert that the bundle imports nothing; it should not import
anything to find out.

### 2. Every `.py` is now pure ASCII, and stays that way

The source carried 75 non-ASCII characters — 73 em dashes, one ellipsis, one
set-union sign — almost all in comments and docstrings. Harmless there. But
**eight of them sat inside strings that get printed**, and a Windows console on
codepage 437 or 862 cannot encode an em dash. Printing one raises
`UnicodeEncodeError` and kills the run.

Measured, with the tree at an ASCII-only path so the machine's own directory
name is not a factor:

| | cp437 | cp862 | cp1255 |
|---|---|---|---|
| `py_pipeline/` `build_knowledge_table.py --dry-run` | **crash** | **crash** | ok |
| this folder, same command | ok | ok | ok |

The eight printed strings were in `build_knowledge_table.py` (2),
`run_pipeline.py` (3) and `make_bundle.py` (3) — including
`"No YES patients -- skipping window."`, which is on a normal execution path.

`check_bundle.py` now fails the bundle if any `.py` contains a non-ASCII byte,
so this cannot come back. That is the same rule `_transfer/make_drop.py` already
enforced on the `.ps1` files. Its success line reads:

```
OK: stdlib-only, ASCII-only, text-only, offline, self-contained.
```

Substitutions used: `--` for em dash, `...` for ellipsis, `union` for U+222A.
Nothing else was touched. The 6 knowledge-base XMLs carrying a UTF-8 BOM were
deliberately left alone — changing their bytes would have invalidated the
equivalence proof, ElementTree handles them, and the drop route ships
`tak_2700.json` (pure ASCII) rather than the XMLs anyway.

### 3. The transfer route was repointed

`_transfer/` is the old `try-paste-py_pipeline/`, which was a *sibling* of
`py_pipeline/` and is now a *child* of this folder. One line:

```python
PASTE = os.path.normpath(os.path.join(HERE, os.pardir, "_paste"))
```

`_paste/make_paste_set.py` needed no change — everything in it resolves from
`__file__`. The shipping lists (`ORDER`, `OPTIONAL`, `LOCAL`, `TAK`) are still
imported by `make_drop.py` from `make_paste_set.py`, so a file added to the
bundle still cannot end up in one route and not the other.

### 4. The version claims were corrected

`run_pipeline.py`, `HANDBOOK.md`, `_paste/RUNBOOK.md` and `_transfer/make_drop.py`
said 3.8+ or 3.10+ in various places. They now all say 3.9, verified on 3.9.7.

## How it was proven

Verification ran with the room's exact interpreter — Python **3.9.7** — against
Python 3.10.11 as the control.

### Static

```
26 .py files checked
PASS: all parse on 3.9, all ASCII, no 3.10+ API, every import resolves
```

The 3.10+/3.11+/3.12+ scan covers `match`/`case`, `except*`, PEP 695 generics and
type aliases, `tomllib`, `StrEnum`, `ExceptionGroup`, `TaskGroup`,
`assert_never`, `contextlib.chdir`, `hashlib.file_digest`, `datetime.UTC`,
`typing.Self`, `itertools.pairwise`, `itertools.batched`, `int.bit_count`,
`zip(strict=)`, `dataclass(slots=/kw_only=)`, `typing.TypeAlias` and
`sys.stdlib_module_names`. Zero hits.

Third-party imports: **none**. The full import set is `argparse ast collections
cProfile csv datetime io json math os pstats random shutil subprocess sys time
xml` plus `__future__`.

### `check_bundle.py` on 3.9.7

```
OK: stdlib-only, ASCII-only, text-only, offline, self-contained.
```

and with `--run`: `ran 273 concepts over 20 patients -> 5259 abstraction rows`.

### Equivalence — the part that matters

Full `run_pipeline.py` runs over the 20-patient / 117k-row fixture
(`../csv_pipeline/data/`, `--max-level 3`), written to separate output folders
and compared file-by-file by SHA-256:

| Comparison | Files | Result |
|---|---|---|
| stroke: `py_pipeline` @3.10 vs this @**3.9.7** | 18 | **IDENTICAL** |
| stroke: `py_pipeline` @3.10 vs this @3.10 | 18 | **IDENTICAL** |
| stroke: this @**3.9.7** vs this @3.10 | 18 | **IDENTICAL** |
| mortality: `py_pipeline` @3.10 vs this @**3.9.7** | 4 | **IDENTICAL** |
| `workspace/` (abstractions + cohort cuts) | 11 | **IDENTICAL** |

That is 12 sliding windows, both cohorts, both labels, 1.96 MB of `results.csv`,
byte for byte. The third row is the one that says this folder is not a 3.9 fork:
the same source produces the same bytes on both interpreters.

Component-level checks, run under 3.9.7 and 3.10.11 and hashed:

| | |
|---|---|
| Mediator, 273 concepts over the fixture | 5,441 rows, same SHA-256 |
| KarmaLego `load_entities` + `mine` (MVS 0.5, level 4) | 19 patterns, same SHA-256 |
| `tak_parse.parse_folder` over all 376 KB XMLs | same JSON, same key order |
| `random.Random(seed).sample()` cohort draw | same on 3.9.7 / 3.10.11 / 3.12.2 |
| `datetime(...).astimezone(timezone.utc)` K-window clamp | same on both |

The ASCII sweep was proven behaviour-neutral separately: each file was parsed
before and after, the same character substitution was applied to every string
constant of the *before* tree, and the two ASTs compared equal — in all 16 files.
No statement, name or operator moved.

### The transfer route, end to end on 3.9.7

Rehearsed into a simulated room folder, with `SETUP_ROOM.ps1` run under
**Windows PowerShell 5.1** (what the room has, not pwsh 7):

```
unpacked 28 files (211.5 KB of base64 -> 1252.6 KB on disk)
manifest: 27 OK, 0 missing, 0 CORRUPT
```

- chunks pasted with CRLF, one with a UTF-8 BOM, one with stray blank lines, one
  saved without a final newline — all decoded
- inside the unpacked copy, on 3.9.7:
  `OK: stdlib-only, ASCII-only, text-only, offline, self-contained.`
- **the pasted bundle ran the full pipeline and its 18 `results.csv` are
  byte-identical to `py_pipeline/` @3.10** — and it did so from the pre-parsed
  `tak_2700.json`, since the drop does not carry `tak_entities/`. That closes the
  loop: what arrives in the room computes what the development tree computes.
- all four failure modes named their chunk and wrote nothing: truncated,
  blanked, pasted into the wrong file, one character altered

One correction to `_transfer/README.md`: the `unpack.py` that `SETUP_ROOM.ps1`
writes is **not** byte-identical to `unpack_src.py` — PowerShell's here-string
drops the final newline, so it is 8,379 bytes against 8,380. Every other byte
matches and the file is ASCII with no BOM, so it cannot affect execution. This
is pre-existing behaviour, reproduced on the original `try-paste-py_pipeline/`
route as well; it is not something this port introduced.

`../py_pipeline/` and `../try-paste-py_pipeline/` were hashed before the work
started and re-hashed after: **421 and 4 files, unchanged.**

### What was not verified, and why

`run_gate.py`, `run_pilot.py`, `run_full.py` and `profile_engine.py` compare
against golden fixtures under `C:\Users\noama1\Desktop\karma\csv_mode\` — a
different machine. Those files do not exist here, so **these four scripts cannot
be executed on this machine at any Python version.** They are shipped and covered
by the static gate; `run_full.py` and `profile_engine.py` also answer `--help`,
while `run_gate.py` and `run_pilot.py` take no arguments and go straight to the
missing fixture. That last failure is identical on `py_pipeline` @3.10 and here
@3.9.7 — same file, same line — so it is the absent fixture, not the interpreter.
Their fidelity numbers were not reproduced.

This does not weaken the guarantee. Those gates proved *the Python port matches
.NET*, which was settled in Phases 1-4 and is unaffected by anything here. What
had to be shown now is that this tree matches the tree that passed them — and the
byte-for-byte table above shows exactly that, without needing the golden files.

## Still true, and still worth reading

Two things from `README.md` are unchanged by this port and will bite regardless
of Python version:

- **The K-window is machine-timezone-dependent.** It is converted local->UTC to
  reproduce the .NET engine, so a room box that is not on Israel time silently
  shifts every window and every result. Check `[TimeZoneInfo]::Local.Id` in the
  room before running anything.
- **`MVS` is a fraction of the cohort**, so on a tiny cohort the support
  threshold collapses and mining to 7 components will exhaust memory. Use
  `--max-level 3` when smoke-testing on the 20-patient sample. Real cohorts are
  unaffected.

One more, specific to the room: the transfer route (`SETUP_ROOM.ps1`,
`copy_part.ps1`, `next.ps1`) is Windows PowerShell 5.1. If the room turns out to
be Linux, the pipeline itself is fine — it is pure stdlib and every path derives
from `__file__` — but that route needs a shell equivalent.
