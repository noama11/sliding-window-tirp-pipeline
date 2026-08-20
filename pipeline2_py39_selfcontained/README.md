# pipeline2_py39_selfcontained — the TIRP pipeline in pure Python, on Python 3.9

> **[HANDBOOK.md](HANDBOOK.md)** is the full reference: structure, data flow,
> every setting, output formats, what was changed, and the validation record —
> plus an index of every other document (§11). **[PLAN.md](PLAN.md)** carries the
> phase-by-phase status. **[PY39_PORT.md](PY39_PORT.md)** is the record of this
> folder: what changed from `py_pipeline/` and how it was proven equivalent.
> This page is the short version.

The whole stroke/mortality TIRP pipeline — cohort selection, Mediator
abstraction, KarmaLego mining — as one self-contained folder. **No pip install,
no SQL Server, no .NET, no compiled binaries, no network.** Standard library
only; every file is text you can paste through a terminal.

Copy this folder anywhere, drop your export into `data/`, run `python
run_pipeline.py`.

## Python 3.9

This folder is the **Python 3.9 port** of `../py_pipeline/`, cut because the
research room has 3.9.7 and cannot be upgraded. It runs on **3.9 and on 3.10+**
from the same source, and produces **byte-identical results** to the original —
see [PY39_PORT.md](PY39_PORT.md) for the diff and the equivalence proof. The
engines were not modified at all.

`../py_pipeline/` is untouched and stays the development tree.

## Quick start

```
python run_pipeline.py --list-windows        # what will run, no work done
python run_pipeline.py                       # every window, YES + NO each
python run_pipeline.py --cohort all          # every window, all patients, no split
python run_pipeline.py --window 2015         # just the K=[2015-2017] window
python run_pipeline.py --label mortality     # predict death instead of stroke
```

It ships with a 20-patient sample in `data/`, so the commands above work
immediately — but add `--max-level 3` when smoke-testing on it. `MVS` is a
*fraction* of the cohort, so on 2–6 patients the support threshold collapses to
one patient, nearly every symbol counts as frequent, and mining to 7 components
will exhaust memory. The pipeline warns when it detects this. Real cohorts
(hundreds of patients) are unaffected.

For real work, put your `raw_events.csv` export somewhere and point
`config.json`'s `data.data_dir` at it (or pass `--data-dir`).

## Configuring a run — all of it is `config.json`

| What | Where | Notes |
|---|---|---|
| **Observation window K** | `window.K` | years the Mediator abstracts over |
| **Outcome window Y** | `window.Y` | years after K used to label YES/NO |
| **Slide** | `window.STEP` | years between consecutive windows |
| **Range** | `window.START_YEAR`, `window.END_DATE` | windows stop when `y_end` passes `END_DATE` |
| **Label (stroke / mortality)** | `label` | picks an entry of `labels`; `--label` overrides |
| **Cohort mode** | `cohort.MODE` | `yes-no` (default) or `all` — no split; `--cohort` overrides |
| **Control ratio** | `cohort.NEGATIVE_RATIO` | NO cohort = this × \|YES\| |
| **Sampling seed** | `cohort.SEED` | makes the NO cohort reproducible |
| **Min vertical support** | `karmalego.MVS` | fraction of the cohort a pattern needs |
| **Max pattern size** | `karmalego.MAX_LEGO_LEVEL` | components per TIRP |
| **Concepts mined** | `labels.<label>.KL_CONCEPTS` | concept **names** (or ids), or `"*"` for all |
| **Input folder** | `data.data_dir` | `--data-dir` overrides |

`--list-windows` prints the resulting plan, including the folder each leg will
be written to — check it before committing to a long run.

### The label switch

`label` is the only thing that separated the original `pipeline_stroke.py` from
`pipeline_mortality.py`, so here it is one config key:

```json
"label": "stroke",
"labels": {
  "stroke":    { "EVENT_CONCEPT": "Stroke_Ischemic", "RESULTS_PREFIX": "",           "KL_CONCEPTS": "…68 ids…" },
  "mortality": { "EVENT_CONCEPT": "Date_Ptira",      "RESULTS_PREFIX": "MORTALITY_", "KL_CONCEPTS": "…43 ids…" }
}
```

`EVENT_CONCEPT` is matched against `ConceptName` in `raw_events.csv`
(case-insensitively, like the original SQL `LIKE`). To add an outcome, add an
entry — you do not need to touch any code. If the concept is missing from your
export the run warns instead of silently producing empty cohorts.

### The knowledge table — read this before your first real run

`data/knowledge_table.csv` (`ConceptName,ConceptID,AllowedValues`) is what turns
a `(ConceptName, Value)` row into a mineable symbol, and what `KL_CONCEPTS`
resolves against. If it disagrees with the knowledge base, KarmaLego quietly
mines the wrong set of concepts — no error, just fewer patterns.

The originally shipped table *did* disagree with `tak_entities/2700`: 10
concepts carried a different ConceptID (the whole `*_Level_State` family — `2000`
meant `HGB_Level_State` there and `ALP_Level_State` in the KB), 18 of the 19
TREND concepts declared `Decreasing/Stable/Increasing` when the Mediator writes
`Dec/Same/Inc`, and 114 concepts were missing outright. The net effect: the
stroke list asked for 68 concepts and got 30, and only 1.6% of abstraction rows
were mineable.

Regenerate it whenever the KB changes:

```
python build_knowledge_table.py --dry-run       # report the diff, write nothing
python build_knowledge_table.py                 # rewrite, keeping a .bak
python build_knowledge_table.py --data-dir /path/to/export
```

Run it against **every** data folder you use — a real run reads the table next
to its `raw_events.csv`, not the bundled one.

`KL_CONCEPTS` is written as concept **names** for the same reason: ConceptIDs
belong to whichever table is in play, and they have already drifted once. Names
are stable. Ids still work, and anything that fails to resolve is reported as a
warning rather than silently dropped.

## What it does

```
for each sliding window:
    K = [k_start, k_end]        observation period
    Y = [k_end,   k_end + Y]    outcome period

    YES = patients with EVENT_CONCEPT inside Y
    NO  = NEGATIVE_RATIO × |YES| patients who never had it (seeded sample)

    each cohort:  Mediator over K  →  abstractions.csv  →  KarmaLego  →  results.csv
```

Output layout matches the .NET engine's exactly, so results are drop-in
comparable with previous runs:

```
results/<k_start>-<k_end>_<PREFIX>YES_patterns/AF_KL_Stroke/genreic/results.csv
results/<k_start>-<k_end>_<PREFIX>NO_patterns/AF_KL_Stroke/genreic/results.csv
workspace/run_data/{YES,NO}/                        scratch, reused by every window
```

One `results.csv` **per window per cohort** — a full run yields up to 24 of them
(12 windows × 2 cohorts; empty-YES windows are skipped). Each has its own
patient set and its own pattern columns. `--keep-abstractions` also archives
every window's Mediator output next to its patterns, which `workspace/`
otherwise overwrites.

`--cohort all` replaces the two cohorts with one holding **every** patient who
has a record in the K-window, into
`results/<k_start>-<k_end>_<PREFIX>ALL_patterns/…`. Same windows, no outcome
involved — the population-wide patients × patterns matrix a prediction model
needs, which two cohort-local matrices cannot supply. See HANDBOOK §5 before
using it: `MVS` is a fraction of the cohort and so means something quite
different here, and the patterns are re-discovered over the population rather
than inherited from the YES/NO run.

## Layout

```
run_pipeline.py       the pipeline (this is the entry point)
config.json           everything configurable
data/                 raw_events.csv + knowledge_table.csv + projects.csv
tak_entities/2700/    the knowledge base, 376 concept XMLs (editable)
tak_2700.json         pre-parsed KB, used if tak_entities/ is absent
pyengine/
  karmalego.py        KarmaLego TIRP miner (Karma + Lego, Generic version)
  mediator/           the Mediator: engine, pattern, state, trend, context, …
check_bundle.py       proves the folder is stdlib-only / text-only / offline
workspace/, results/  created at run time
```

Development-only extras — `run_gate.py`, `run_pilot.py`, `run_full.py`,
`profile_engine.py` and the `PHASE*` notes — compare against golden files that
live outside the folder. Delete them and the pipeline still runs.

To cut a clean copy with those stripped out:

```
python make_bundle.py                  # -> ../tirp_pipeline_bundle/  (~10.6 MB)
python make_bundle.py /path/out --zip  # somewhere else, and zip it
python make_bundle.py --no-sample-data # drop the 20-patient sample (~8 MB)
```

It runs `check_bundle.py` inside the copy before handing it back, so a bundle
that would not run is never produced.

## Verifying it

```
python check_bundle.py --run    # stdlib-only, ASCII-only, text-only, offline, and it runs
python run_gate.py              # 943/943 IDENTICAL         (dev only)
python run_pilot.py             # 3/3 IDENTICAL             (dev only)
python run_full.py              # 5,047 of 5,048 rows exact (dev only)
python build_knowledge_table.py --dry-run   # is the table in sync with the KB?
```

`check_bundle.py` walks every module's AST and fails on any import that is
neither stdlib nor local, on `subprocess`/`socket`/`urllib`/`ctypes` and
friends, on non-text files, and on a missing knowledge base or dictionary. It
passes on a bare system Python with no virtualenv — that is the actual claim.

## Fidelity

| Gate | Result |
|---|---|
| KarmaLego (Phase 1) | IDENTICAL — 20 patients × 30,722 patterns |
| Mediator non-pattern (Phase 2) | 943 / 943 IDENTICAL |
| Pattern pilots (Phase 3) | 3 / 3 IDENTICAL |
| Full fixture, all 273 concepts | 5,047 / 5,048 rows exact |
| KarmaLego vs live .NET, same input | IDENTICAL — 318 patients × 137 patterns |
| **Whole pipeline vs live .NET, sample** | **KarmaLego IDENTICAL both cohorts; 0 and 1 abstraction rows missed** |
| Mediator vs live .NET, 528-patient cohort | 99.999% and 99.975% of .NET rows |

The port also produces some rows the .NET omits ("phantom contexts") — those are
extras, never misses. `PHASE4_SUMMARY.md` has the full validation.

## Performance

Single-threaded, and it still wins: on a 396-patient cohort the Mediator took
152 s against the 14-thread .NET's 662 s. Compute is ~0.25 s/patient and flat in
cohort size, so a real 500–900 patient cohort abstracts in 2–4 minutes and a
whole K-window runs end to end in under 7 minutes. On a large export the cost is
dominated by streaming `raw_events.csv`, not by the engines — both cohorts of a
window are therefore cut in a single pass.

`profile_engine.py` attributes exclusive wall clock per concept and per op type;
start there before optimising anything.

## Two things that bite

**The K-window is converted from local time to UTC.** The .NET API parses its
window string with `AssumeLocal | AdjustToUniversal`, so a K-window ending
`2024-01-01 00:00:00` is really `2023-12-31 22:00:00` on an Israel-time machine
— which is the clamp stamped on every `EndTime` in the reference outputs. This
pipeline reproduces it so results stay comparable. It does mean the window is
**machine-dependent**: run in a different timezone and the output moves. That is
inherited .NET behaviour, kept deliberately.

**`"*"` means computable concepts only.** State, trend, pattern, context —
`isComputableConcept` in `Controller.cs` is `AbstractConcept || Context`. Raws
and events are inputs and never appear in `abstractions.csv`. For KB 2700 that
is 273 of 376 concepts.
