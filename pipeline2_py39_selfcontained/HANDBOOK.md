# TIRP Pipeline Handbook

Everything about the pure-Python pipeline: what it is, how it runs, where data
goes in and comes out, what was changed and why, and what was verified.

- [1. What this is](#1-what-this-is)
- [2. Project structure](#2-project-structure)
- [3. How it works](#3-how-it-works)
- [4. Input](#4-input)
- [5. Configuration](#5-configuration)
- [6. Running it](#6-running-it)
- [7. Output](#7-output)
- [8. What was changed](#8-what-was-changed)
- [9. Tests and validation](#9-tests-and-validation)
- [10. Gotchas](#10-gotchas)
- [11. The other documents](#11-the-other-documents)

---

## 1. What this is

A complete reimplementation of the stroke/mortality TIRP pipeline — cohort
selection, Mediator temporal abstraction, KarmaLego pattern mining — in the
Python standard library alone.

**No pip install, no SQL Server, no .NET runtime, no compiled binaries, no
network.** Every file is text. Copy the folder, drop in your export, run it.

It replaces two .NET executables (`API.exe`, `KarmaLegoConsoleApp.exe`) that
previously needed a build toolchain and a database. Those still exist in
`../csv_pipeline/` and remain the reference implementation this port is
validated against.

---

## 2. Project structure

```
pipeline2_py39_selfcontained/
│
├── run_pipeline.py          ← THE ENTRY POINT. Runs the whole pipeline.
├── config.json              ← ALL settings: K, Y, label, MVS, concepts, paths
│
├── data/                    ← INPUT lives here
│   ├── raw_events.csv           your patient data — THE one file you must supply
│   ├── knowledge_table.csv      concept dictionary — optional, derived from the KB
│   └── projects.csv             PROJECT_ID → KB id — optional, see §4
│
├── tak_entities/2700/       ← the knowledge base: 376 concept definitions (XML)
├── tak_2700.json            ← pre-parsed KB, used only if tak_entities/ is absent
│
├── pyengine/                ← the two engines
│   ├── karmalego.py             KarmaLego TIRP miner (Karma + Lego)
│   └── mediator/                the Mediator
│       ├── engine.py                orchestration: ordering, windows, dispatch
│       ├── tak_parse.py             XML knowledge base → concept map
│       ├── pattern.py               Pattern.Calculate (the largest operator)
│       ├── state.py trend.py context.py event.py
│       └── functions.py persistence.py datainstance.py
│
├── build_knowledge_table.py ← regenerate data/knowledge_table.csv from the KB
├── check_bundle.py          ← prove the folder is stdlib-only / offline
├── make_bundle.py           ← cut a clean shippable copy
│
├── workspace/               ← created at run time (intermediate files)
├── results/                 ← created at run time (OUTPUT)
│
├── README.md, HANDBOOK.md   ← this document
└── PHASE*.md, run_gate.py, run_pilot.py, run_full.py, profile_engine.py
                             ← development history and fidelity gates.
                               Not needed to run. `make_bundle.py` strips them.
```

Size: ~11 MB with the bundled 20-patient sample, ~3 MB without.

---

## 3. How it works

```
                       data/raw_events.csv
                               │
        ┌──────────────────────┴───────────────────────┐
        │  for each sliding window (K/Y from config)   │
        └──────────────────────┬───────────────────────┘
                               │
   K-window [k_start, k_end] ──┤── observation period
   Y-window [k_end, k_end+Y] ──┘── outcome period
                               │
                ┌──────────────┴──────────────┐
                │                             │
        YES cohort                      NO cohort
   patients with EVENT_CONCEPT     NEGATIVE_RATIO × |YES| patients
   inside the Y-window             who NEVER had it (seeded sample)
                │                             │
                └──────────────┬──────────────┘
                               │
              one streaming pass over raw_events.csv
              cuts both cohorts → workspace/run_data/{YES,NO}/
                               │
                    ┌──────────┴──────────┐
                    │      MEDIATOR       │  abstracts 273 computable concepts
                    │  over the K-window  │  (state / trend / pattern / context)
                    └──────────┬──────────┘
                               │
                        abstractions.csv
                               │
                    ┌──────────┴──────────┐
                    │     KARMALEGO       │  mines TIRPs from
                    │  raw ∪ abstractions │  the selected concepts
                    └──────────┬──────────┘
                               │
                         results.csv
                  (patients × patterns matrix)
```

Windows slide by `STEP` years until `y_end` passes `END_DATE`. With the shipped
config (K=2, Y=1, STEP=1, 2010→2024) that is **12 windows × 2 cohorts = 24 runs**.

---

## 4. Input

### The one file you supply: `raw_events.csv`

Your `InputPatientsData` export. Put it in `data/`, or point elsewhere with
`config.json` → `data.data_dir`, or `--data-dir`.

```csv
PatientID,ConceptName,StartTime,EndTime,Value
1001,HGB,2015-03-16 00:00:00,2015-03-16 00:00:00,13.2
1001,Visit,2015-04-09 00:00:00,2015-04-09 00:00:00,1
1001,Stroke_Ischemic,2017-06-02 00:00:00,2017-06-02 00:00:00,1
```

| Column | Meaning |
|---|---|
| `PatientID` | any string; used to group and to name cohorts |
| `ConceptName` | must match a name in `knowledge_table.csv` / the KB |
| `StartTime`, `EndTime` | `YYYY-MM-DD HH:MM:SS` (point events have Start = End) |
| `Value` | numeric or nominal, per the concept |

### The minimum input

**`raw_events.csv` + `tak_entities/<kb>/` is enough.** Everything else is
derived:

| File | Required? | If absent |
|---|---|---|
| `data/raw_events.csv` | **yes** | nothing to run on |
| `tak_entities/<kb>/` | **yes** (or `tak_<kb>.json`) | no concept definitions |
| `data/projects.csv` | no | KB id comes from `project.KB_ID`, or from the only folder in `tak_entities/` |
| `data/knowledge_table.csv` | no | generated from the concept XMLs into `workspace/` |

Verified: a folder holding only `data/raw_events.csv` and `tak_entities/2700/`
runs end to end, and the dictionary it generates is identical to the shipped one
on all 376 KB concepts.

Both optional files are still worth shipping when you have them. `projects.csv`
is what the .NET engines use, so keeping it means the same folder works for both
backends. A checked-in `knowledge_table.csv` is a record of exactly which
dictionary a run used — regenerating silently on every run makes results harder
to reproduce later. The generated one also omits the 14 raw data columns that
are not KB concepts (drug dosage parameters, `MCV`, `MPV`); they only matter if
you mine with `"*"`.

### The two dictionary files (shipped, rarely touched)

- **`data/projects.csv`** — maps `PROJECT_ID` → knowledge-base id. `40144 → 2700`.
- **`data/knowledge_table.csv`** — `ConceptName,ConceptID,AllowedValues`. Turns a
  `(name, value)` row into a mineable symbol and resolves `KL_CONCEPTS`.
  **Regenerate it whenever the KB changes** (see §8) — if it drifts, KarmaLego
  silently mines the wrong concepts:

  ```
  python build_knowledge_table.py --dry-run          # show the diff
  python build_knowledge_table.py --data-dir <dir>   # rewrite (keeps a .bak)
  ```

  Run it against **every** data folder you use — a run reads the table sitting
  next to its own `raw_events.csv`, not the bundled one.

### The knowledge base

`tak_entities/2700/*.xml` — 376 concept definitions. Read at run time, so
editing a concept changes the next run. If the folder is missing, the pre-parsed
`tak_2700.json` is used instead.

---

## 5. Configuration

Everything is `config.json`. No code edits.

| Setting | Key | Default | Meaning |
|---|---|---|---|
| Observation window | `window.K` | `2` | years the Mediator abstracts over |
| Outcome window | `window.Y` | `1` | years after K used to label YES/NO |
| Slide | `window.STEP` | `1` | years between windows |
| First window | `window.START_YEAR` | `2010` | |
| Stop after | `window.END_DATE` | `2024-10-08` | windows stop when `y_end` passes this |
| **Outcome** | `label` | `"stroke"` | picks an entry of `labels` |
| Control ratio | `cohort.NEGATIVE_RATIO` | `3` | NO cohort = this × \|YES\| |
| Sampling seed | `cohort.SEED` | `42` | makes the NO cohort reproducible |
| Min vertical support | `karmalego.MVS` | `0.2` | **fraction** of cohort a pattern needs |
| Max pattern size | `karmalego.MAX_LEGO_LEVEL` | `7` | components per TIRP |
| Concepts to mine | `labels.<label>.KL_CONCEPTS` | names list | or `"*"` for all |
| Input folder | `data.data_dir` | `"data"` | |
| Project | `project.PROJECT_ID` | `40144` | → KB 2700 via projects.csv |

### Switching the outcome

The only thing that separated the original `pipeline_stroke.py` from
`pipeline_mortality.py` was the labelling concept. Here it is one key:

```json
"label": "stroke",
"labels": {
  "stroke":    { "EVENT_CONCEPT": "Stroke_Ischemic", "RESULTS_PREFIX": "",           "KL_CONCEPTS": "…68 names…" },
  "mortality": { "EVENT_CONCEPT": "Date_Ptira",      "RESULTS_PREFIX": "MORTALITY_", "KL_CONCEPTS": "…27 names…" }
}
```

Edit `label`, or override per run with `--label mortality`. Adding a third
outcome is a config entry — no code. `EVENT_CONCEPT` is matched against
`ConceptName` case-insensitively (like the original SQL `LIKE`), and the run
warns if the concept is absent rather than silently producing empty cohorts.

`KL_CONCEPTS` is written as concept **names**. Numeric ids still work, but names
are strongly preferred — ConceptIDs belong to whichever `knowledge_table.csv` is
in play, and they have already drifted once (§8). Unresolvable entries produce a
warning, never a silent omission.

### Cohort mode: with or without the YES/NO split

```json
"cohort": { "MODE": "yes-no", "NEGATIVE_RATIO": 3, "SEED": 42 }
```

| `MODE` | Cohorts per window | Output folder |
|---|---|---|
| `yes-no` (default) | YES (outcome in the Y-window) and NO (`NEGATIVE_RATIO`× as many patients who never had it) | `<k_start>-<k_end>_<PREFIX>YES_patterns` and `…NO_patterns` |
| `all` | one: **every** patient with a record in the K-window | `<k_start>-<k_end>_<PREFIX>ALL_patterns` |

Override per run with `--cohort all`. This is the equivalent of the old
`pipeline_all_patients.py`, and it exists for one reason: **`yes-no` mines two
cohort-local matrices, and a prediction model needs one row per patient over the
whole population.** In `all` mode `EVENT_CONCEPT`, `NEGATIVE_RATIO` and `SEED`
are not consulted at all.

Three things to know before running it:

- **The window plan is identical in both modes.** `all` still stops where the
  Y-window would run past `END_DATE`, so every ALL matrix indexes a K-window the
  `yes-no` run also labelled, and the two join on `(window, PatientID)`. (The
  old SQL variant instead ran from 2000 with no Y at all, producing trailing
  windows no label could ever be attached to.)
- **`MVS` is a fraction of the cohort**, so it does not mean the same thing
  here. 0.2 of ~30k patients is a far higher absolute bar than 0.2 of a
  few-hundred-patient YES cohort, and you will get far fewer patterns. The old
  SQL all-patients config used 0.3; `--mvs` overrides it per run.
- **The patterns are re-discovered, not carried over.** `all` mines the
  population from scratch, so its columns are the TIRPs frequent across
  everyone — *not* the ones the YES and NO runs found. If the model's features
  are meant to be the YES/NO patterns specifically, this run does not produce
  them; it produces a population-frequent feature set of its own.

Cohort membership uses the plain calendar year (`StartTime` in
`[k_start-01-01, k_end-01-01)`), which is what the SQL pipeline selected on —
deliberately *not* the local→UTC clamped window the Mediator abstracts over
(§10).

---

## 6. Running it

```bash
python run_pipeline.py --list-windows       # print the plan, do no work
python run_pipeline.py                      # every window, YES + NO each
python run_pipeline.py --cohort all         # every window, all patients, no split
python run_pipeline.py --window 2015        # only K=[2015-2017]
python run_pipeline.py --label mortality    # predict death instead of stroke
python run_pipeline.py --data-dir /data/export --results-dir /out/run7
python run_pipeline.py --window 2015 --max-level 3    # small-cohort smoke test
```

| Flag | Purpose |
|---|---|
| `--list-windows` | print the window plan and each output folder, then exit |
| `--cohort yes-no\|all` | override `cohort.MODE` — YES+NO, or one all-patient cohort (§5) |
| `--window YYYY` | run only the window whose `k_start` is that year |
| `--label NAME` | override `config.json`'s `label` |
| `--data-dir DIR` | override `data.data_dir` |
| `--results-dir DIR` | override the results folder |
| `--max-level N` | override `MAX_LEGO_LEVEL` (see §10 on small cohorts) |
| `--mvs F` | override `MVS` (worth setting for `--cohort all`, §5) |
| `--keep-abstractions` | archive each window's `abstractions.csv` into its results folder |

Requires Python 3.9+ and nothing else — verified on **3.9.7**, which is what the
research room has and cannot be upgraded from. Verify the folder with
`python check_bundle.py --run`; it now runs under 3.9 itself, so the check can be
made *in* the room rather than only before shipping.

### What a run costs

Measured on the full 4,590-patient export (16-core machine, single-threaded):

| | |
|---|---|
| One window (132 + 396 patients) | **7 min 36 s** |
| All 12 windows | **~1.5–2 hours** |
| The .NET pipeline, same window | 17 min 20 s |
| Mediator compute | ~0.25 s/patient, flat in cohort size |
| Peak memory | ~350 MB |
| Disk needed for `workspace/` | ~2 GB |

The cost is dominated by streaming the 1.6 GB source CSV, not by the engines,
which is why both cohorts of a window are cut in a single pass.

---

## 7. Output

**One `results.csv` per window per cohort** — a full run produces a whole set,
not a single file:

```
results/
├── 2010-2012_YES_patterns/AF_KL_Stroke/genreic/results.csv
├── 2010-2012_NO_patterns/ AF_KL_Stroke/genreic/results.csv
├── 2012-2014_YES_patterns/AF_KL_Stroke/genreic/results.csv
├── 2012-2014_NO_patterns/ AF_KL_Stroke/genreic/results.csv
├── 2013-2015_…
│   …
└── 2020-2022_NO_patterns/ AF_KL_Stroke/genreic/results.csv

workspace/run_data/
├── YES/{raw_events.csv, mediator_raw_events.csv, abstractions.csv}
└── NO/{…}                                   ← scratch, REUSED by every window
```

With the shipped config that is up to 24 files (12 windows × 2 cohorts).
Windows whose YES cohort is empty are skipped and produce nothing — on the
bundled 20-patient sample 3 windows are empty, giving 18 files; on the full
export 11 of 12 windows produce output, giving 22.

Each file is a separate cohort with its own patient set and its own discovered
pattern set, so **column sets differ between windows**. Do not expect to
concatenate them directly — align on pattern name if you need a combined table.

`workspace/run_data/` is scratch and is overwritten by each window, so after a
full run it holds only the LAST window's intermediates. Pass
`--keep-abstractions` to archive every window's Mediator output alongside its
patterns:

```
results/2015-2017_YES_patterns/
├── abstractions.csv                      ← with --keep-abstractions
└── AF_KL_Stroke/genreic/results.csv
```

On a real export those are ~45 MB per cohort, so a full run adds ~1 GB.

The nested `<domain>/genreic/` path (including the original's `genreic`
misspelling) matches the .NET engine's layout exactly, so old and new results
are drop-in comparable. With `--label mortality` the folders become
`2015-2017_MORTALITY_YES_patterns` etc.

With `--cohort all` there is one folder per window instead of two, and the
scratch folder is `workspace/run_data/ALL/`:

```
results/
├── 2010-2012_ALL_patterns/AF_KL_Stroke/genreic/results.csv
│   …
└── 2021-2023_ALL_patterns/AF_KL_Stroke/genreic/results.csv
```

Only windows with no patient data at all are skipped, so on a real export
expect a file for every window. Each holds one row per patient in that
K-window — the population-wide matrix the `YES`/`NO` pair cannot give you.
Budget more time than the YES/NO run: the cohort is the whole export rather
than ~4× the YES count, and Mediator cost is flat per patient (~0.25 s).

### `results.csv` — the patients × patterns matrix

One row per patient, one column per discovered TIRP; each cell is that patient's
**horizontal support** (how many times the pattern occurs for them).

```csv
id,@@Pair:ALP_Level_State:Normal@Contain@ALP_TREND:Dec,@@Pair:ALP_Level_State:Normal@Contain@Apixaban_state:Reduced,…
1001,0,0,…
1002,2,11,…
1003,1,14,…
```

A column name encodes the whole pattern as concatenated pairs:

```
@@Pair:ALP_Level_State:Normal@Contain@ALP_TREND:Dec
        └─ first symbol ─┘   └relation┘ └─ second symbol ─┘
```

Only `Contain` and `Overlaps` occur (with `maxGap = 0` the other Allen relations
are unreachable). Bigger patterns concatenate more `@@Pair:` groups — a
3-component TIRP carries its 3 pairwise relations.

This matrix is the feature table for downstream modelling: rows are patients,
columns are temporal patterns, and the YES/NO folders give the labels.

### `abstractions.csv` — the Mediator's output (intermediate)

```csv
PatientID,ConceptName,StartTime,EndTime,Value
1001,ALP_Level_State,2015-03-16 00:00:00,2016-12-31 22:00:00,Normal
1001,ALP_TREND,2015-03-16 00:00:00,2015-08-20 23:59:00,Same
1001,ALP_TREND,2015-08-20 23:59:00,2016-12-31 22:00:00,Dec
```

Raw measurements turned into time intervals with symbolic values. Useful for
debugging: if a pattern you expected is missing, look here first.

---

## 8. What was changed

### Engine defects found by validating against live .NET runs

| # | Defect | Effect |
|---|---|---|
| 1 | **K-window converted local→UTC.** `Controller.ParseDateOrTime` parses both endpoints `AssumeLocal \| AdjustToUniversal`. A window ending `2024-01-01` really ends `2023-12-31 22:00` here — the origin of the unexplained `22:00` in every reference file. | 107 of 1,783 rows disagreed; now reproduced |
| 2 | **O(n²) loop.** `_group_blobs_by_gaps_and_cardinality` did `del items[i]` over ~15,000 intervals. `Hyper_pattern` alone was 67% of all runtime. | 4.74 s → 1.38 s on that concept; 7.1 s → 4.5 s overall |
| 3 | **Output over-filtering.** `Engine.run` dropped rows outside `[Ws,We]` and clamped every row's end to `We`. Neither exists in .NET — `insertBatchCalculatedDataByTime` stores results verbatim. | fixture 5,043 → **5,047/5,048**; recovered ~4,900 rows on 2 real patients |
| 4 | **Single-level collation.** KarmaLego pattern names sort with .NET's culture comparer, which is multi-level (base letters first, case only as tiebreak). | 1,283/1,344 → **1,344/1,344** names reproduced |
| 5 | **Wrong results row set.** A row was emitted per loaded patient instead of per surviving entity. | removed 75 spurious all-zero rows from a 396-patient cohort |
| 6 | **Case-sensitive KB glob.** `tak_parse` used `glob("*.xml")`; 10 KB files are named `.XML`. Windows matched them case-insensitively, **Linux would not** — 10 TREND concepts would have vanished silently. | fixed before it ever bit |

### Data defect: the knowledge table was out of sync with the KB

Found while validating the mortality label. **This is a data problem, not an
engine one — it hit the .NET engine identically**, which is why the port matched
it exactly throughout.

`knowledge_table.csv` disagreed with `tak_entities/2700` three ways:

- **10 ConceptIDs contradicted the KB** — the whole `*_Level_State` family, and
  *permuted*: `2000` meant `HGB_Level_State` in the table and `ALP_Level_State`
  in the KB. So an id list selected different concepts depending on which table
  resolved it.
- **18 of 19 TREND concepts had the wrong values** — declared
  `Decreasing/Stable/Increasing`, but the Mediator writes the .NET enum names
  `Dec/Same/Inc`. Every row of those concepts was silently discarded.
- **114 KB concepts were missing**; 61 table names were not KB concepts (14 real
  raw columns, 47 dead entries from an older KB revision).

The two label lists were also written in **different id spaces** — stroke in TAK
ids, mortality in old-table ids.

Fixes: `build_knowledge_table.py` regenerates the table from the KB with the
trend-enum and boolean-casing rules baked in; `KL_CONCEPTS` now accepts names.

| on a 396-patient cohort | before | after |
|---|---|---|
| stroke concepts resolving | 30 of 68 | **68 of 68** |
| mineable abstraction rows | 9,154 (1.6%) | **28,641 (5.0%)** |
| patterns found (132-patient YES) | 214 | **4,207** |
| window wall clock | 6 m 54 s | 7 m 36 s |

> **Results from the regenerated table are not comparable with earlier runs.**
> Do not mix old and new outputs.

### New tooling

| File | Purpose |
|---|---|
| `run_pipeline.py` | the standalone pipeline (new) |
| `config.json` | generalised: label switch, name-based concept lists |
| `build_knowledge_table.py` | regenerate the dictionary from the KB |
| `check_bundle.py` | prove stdlib-only / text-only / offline |
| `make_bundle.py` | cut and self-verify a shippable copy |
| `run_full.py` | widest fidelity gate (all 273 concepts) |
| `profile_engine.py` | per-concept exclusive wall clock |

`csv_pipeline/run.py` also gained `--engine python` so both backends can be run
side by side on identical cohorts; it is otherwise unchanged and remains the
oracle.

---

## 9. Tests and validation

### Where the reference comes from

The data started in SQL Server (`Af_Clalit_Community`, project 40144). Two
migrations happened, and each was checked on its own before the next was built
on top of it:

```
SQL Server ──(1)──► CSV files ──(2)──► pure Python
```

**(1) SQL → CSV, for KarmaLego.** `csv_mode/reference/` holds two runs of the
*same .NET binary*: `sql_run/` with `DBType=SQLServer` against the live database,
and `csv_run/` with `DBType=CSV` against fixtures exported from those same tables
minutes earlier (`csv_mode/fixtures/_fixtures_meta.json` records the export).
Comparing them: **IDENTICAL**, 20 patients × 30,722 patterns. Swapping the
data-access layer changed nothing.

**(2) SQL → Python, for the Mediator.** This one skips the CSV .NET engine
entirely. The golden `fixtures/abstractions.csv` (5,048 rows) is an export of
**`OutputPatientsData` from SQL Server** — the SQL-mode Mediator's own stored
output. So `run_full.py`'s 5,047 / 5,048 is measured directly against
SQL-produced results.

**(3) .NET-CSV → Python, live.** The head-to-head runs below, on identical
cohorts, at both the 20-patient and the 528-patient scale.

Caveat worth stating plainly: links (1) and (2) rest on the **20-patient fixture
cohort**. The large-scale evidence (528 patients) compares Python against the
.NET *CSV* engine, not against SQL. Re-validating at scale against SQL would
need a SQL-mode run on MLS05-new, which has not been done.

### Fidelity gates (against frozen golden references)

| Gate | Scope | Result |
|---|---|---|
| Phase 1 — KarmaLego | 20 patients × 30,722 patterns | **IDENTICAL** |
| Phase 2 — Mediator, non-pattern | 43 concepts, 943 rows | **943/943 IDENTICAL** |
| Phase 3 — Pattern pilots | 3 patterns | **3/3 IDENTICAL** |
| Full fixture | all 273 computable concepts | **5,047 / 5,048 rows exact** |

The single remaining row is patient 2470's `Context_Age_18_49` (reference has a
point interval, port has it running on).

### Against live .NET runs

The .NET oracle was confirmed **deterministic** first — two runs of the same
window produced byte-identical output — so every residual below is real.

| Comparison | Result |
|---|---|
| KarmaLego, fed the .NET's own abstractions (396 patients) | **IDENTICAL** — 318 patients × 137 patterns |
| Whole pipeline, 20-patient sample, YES | KarmaLego **IDENTICAL**; **0** abstraction rows missed of 1,783 |
| Whole pipeline, 20-patient sample, NO | KarmaLego **IDENTICAL**; **1** row missed of 7,387 |
| Mediator, `data_full` 132-patient cohort | 233,343 / 233,346 = **99.999%** |
| Mediator, `data_full` 396-patient cohort | 570,714 / 570,856 = **99.975%** |

The port also produces rows the .NET omits ("phantom contexts") — those are
extras, never misses. Remaining misses are the patient-2470 class.

### Packaging and portability

| Check | Result |
|---|---|
| `check_bundle.py` — stdlib-only, ASCII-only, text-only, no subprocess/socket/urllib/ctypes | pass |
| Same, on a bare system Python with no virtualenv | pass |
| Folder copied to an unrelated directory and run | **IDENTICAL** to the .NET |
| `make_bundle.py` copy, self-verified | pass, 10.6 MB |
| Standalone `run_pipeline.py` vs `csv_pipeline --engine python` | **IDENTICAL**, both stages |
| Scale test: `run_pipeline.py` on `data_full` | reproduces the validated output exactly |

Only imports used anywhere: `argparse, ast, csv, datetime, json, math, os,
random, shutil, sys, time, xml` (plus `cProfile/pstats/io/subprocess` in
development-only scripts).

---

## 10. Gotchas

**The K-window is machine-dependent.** It is converted local→UTC, reproducing
.NET behaviour. Running in a different timezone shifts the window and therefore
the output. Keep the analysis machine on one timezone (Israel time, to match
existing results). `--no-utc-window` exists on `csv_pipeline/run.py` but then
you will not match the .NET.

**MVS is a fraction, so small cohorts explode.** At 2 patients, `MVS = 0.2` gives
a support threshold of 0.4 — nearly every symbol is "frequent" and mining to 7
components exhausted 18 GB of RAM in testing. The pipeline warns when the
threshold falls below 5 patients. Use `--max-level 3` for smoke tests on the
bundled sample. Real cohorts (hundreds of patients) are unaffected.

**Mortality is only partly runnable on KB 2700.** 27 of its 43 concepts exist;
the other 16 — the `*_HighDose_ctxt` / `*_LowDose_ctxt` dose contexts,
`Heart_Rate_Level_State`, `eGFR_Level_State`, `Blood_Pressure_*_Level_State`,
`Gamma_GT_Level_State` — come from an earlier knowledge base and cannot be mined
until the KB supplies them. They are listed in `config.json`.

**Regenerate the knowledge table for every data folder.** A run reads the table
next to its own `raw_events.csv`. Regenerating the bundled one does not touch
your export's copy.

**`"*"` means computable concepts only** — state, trend, pattern, context
(`isComputableConcept` = `AbstractConcept || Context`). Raw concepts and events
are inputs and never appear in `abstractions.csv`. For KB 2700 that is 273 of
376 concepts.

**The NO cohort is a seeded sample.** `cohort.SEED` makes it reproducible here;
the original SQL pipeline used `ORDER BY NEWID()` and was not reproducible. Same
seed + same data = same controls.

---

## 11. The other documents

| Document | What it is | Current? |
|---|---|---|
| **HANDBOOK.md** | this file — the reference for using the pipeline | living |
| **README.md** | the short version, for someone opening the folder | living |
| **PLAN.md** | the original plan and feasibility analysis (2026-07-10), with a status block at the top | status tracked |
| **PHASE2_RESULTS.md** | Phase 2 record: the 943-row gate | frozen record |
| **PHASE2_ALGO_NOTES.md** | how the non-pattern operators work | frozen |
| **PHASE3_COMPLETE_SUMMARY.md** | Phase 3 record. **Two conclusions superseded** — see its header | frozen, annotated |
| **PHASE4_SUMMARY.md** | Phase 4 record: the port's defects, performance, validation | frozen, annotated |
| **PHASE3_PILOT_*.md**, **PHASE3_HANDOFF.md** | Phase 3 working notes | frozen |
| **PHASE1/2_PROMPT.txt** | the briefs those phases were built from | frozen |

"Frozen record" means the document describes what was true at the end of that
phase and is deliberately *not* rewritten. Later corrections are noted in its
header instead of edited in, so the reasoning trail stays honest — Phase 3's
clamp conclusion, for example, was overturned in Phase 4 and says so at the top.
**For current behaviour, always trust HANDBOOK.md.**

Outside this folder: `csv_mode/reference/REFERENCE_META.json` records the
SQL-mode reference run and its provenance, and `csv_mode/HANDOFF_SESSION*.md`
cover the earlier SQL→CSV work.
