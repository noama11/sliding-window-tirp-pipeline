# Phase 4 Complete — the pasteable bundle, wired and validated

_Phase record. Written 2026-07-28, extended through 2026-08-01. Follows
`PHASE3_COMPLETE_SUMMARY.md`._

> This documents the port and its validation. Work continued after the section
> numbering was fixed — §4b (the knowledge table) and §7 (what came after) were
> appended rather than folded in, so the order of discovery stays visible.
> **For how the pipeline behaves today, read `HANDBOOK.md`.**

## TL;DR

`py_pipeline/` is a working, stdlib-only, offline bundle, wired into the real
pipeline behind `--engine python` and validated against live .NET runs on both
the 20-patient sample and the full 4,590-patient export.

- **Wired.** `csv_pipeline/run.py --engine python` replaces both subprocess calls
  with in-process engine calls. `--engine dotnet` is untouched and still the oracle.
- **Stdlib-only.** `check_bundle.py` proves it mechanically. Verified by copying
  the bundle to an unrelated directory and running it on a bare system Python.
- **Fast.** A full `data_full` window (132 + 396 patients) runs end to end in
  **6 min 54 s**. The single-threaded port's Mediator is **~4× faster** than the
  14-thread .NET one. Streaming the 1.6 GB source CSV, not compute, is the cost.
- **Validated end to end.** KarmaLego output is **IDENTICAL** to the .NET's.
  Abstractions match **99.97–100%** of .NET rows, with 0–3 misses per cohort.

Validating found **five** real defects. Those are the substance of this phase.

---

## 1. Wiring (`csv_pipeline/run.py`)

`run_mediator` and `run_karmalego` became thin dispatchers. The .NET path is the
code that was already there; the Python path is new:

| .NET | Python |
|---|---|
| `API.exe Query CalculateAbstractionsInBatchByTime …` per 28-patient batch | `Engine.run(names, window)` once for the whole cohort |
| `KarmaLegoConsoleApp.exe` reading `appsettings.json` | `karmalego.run_karmalego(RUN_DATA_DIR, patients, out, mvs=…)` |

Everything around them is shared, so the backends are diffable path-for-path —
including the results layout, which the Python KarmaLego writes to the same
nested `<results>/<domain>/genreic/results.csv` the .NET engine uses.

New flags, all optional, all off by default except `--engine`:

```
--engine {dotnet,python}   backend for the two engine steps (default dotnet)
--data-dir DIR             override config.json data.data_dir
--results-dir DIR          override the results folder (run both engines side by side)
--keep-abstractions        archive each cohort's abstractions.csv next to its patterns
--no-utc-window            do NOT convert the K-window local->UTC (see 2a)
```

Batching was dropped on the Python path deliberately: it existed only to keep
API.exe's linear-scanning CSV reader off a quadratic. `Engine.load_raw_csv`
indexes by `(patient, concept)` on load, so the whole cohort goes in one pass.

The TAK is parsed at run time from the same `tak_entities/<kb>/*.xml` the .NET
engine reads (`PROJECT_ID` → `Kb_ID` via `projects.csv`), cached under
`workspace/`, falling back to the bundled `tak_2700.json`. Mediator `"*"` is
resolved the way `Controller.isComputableConcept` does — `AbstractConcept ||
Context`, i.e. state / trend / pattern / context, 273 of KB 2700's 376 concepts.
Raws and events are inputs and never appear in `abstractions.csv`.

---

## 2. Five defects the end-to-end validation found

### 2a. The K-window is silently converted from local time to UTC

`Controller.ParseDateOrTime` (`Controller.cs:1569`) parses **both** window
endpoints with `DateTimeStyles.AssumeLocal | DateTimeStyles.AdjustToUniversal` —
it reads them as local time and returns UTC. So on the Israel-time machine that
produced the fixture, `--window 2022` (K = `[2022-01-01, 2024-01-01]`) actually
abstracts over `[2021-12-31 22:00, 2023-12-31 22:00]`.

That is the origin of the `22:00` stamped throughout `abstractions.csv`,
previously carried in `engine.py` as an unexplained `_WIN_END` constant with the
matching `_WIN_START` shift missing. Before the fix `--engine python` disagreed
with the .NET on **107 of 1,783** rows of one sample cohort, essentially all
boundary clamps. `run.py` now applies the same conversion (`--no-utc-window`
opts out). `_WIN_START` was corrected and verified inert on this data.

**This is machine-dependent .NET behaviour.** Run the pipeline in another
timezone and the window — and therefore the output — moves. Worth knowing before
the research room's box is set up.

### 2b. `_group_blobs_by_gaps_and_cardinality` was O(n²) — two thirds of all runtime

The C# walks its interval list with an index and `del`etes rejected entries in
place. Transcribed literally into Python that is `del items[i]` in a loop, and
each `del` is O(n).

`Hyper_pattern` (505) splits a patient's whole history into ~15,000 two-day
intervals (`frequency = 2/day`, `cardinality_min = 2`) and rejects almost all of
them, so the deletion alone was **67% of the entire engine's runtime**.

Rewritten to append survivors to a fresh list — provably the same computation,
since `items[j]` is by construction the last surviving entry and `j > -1` is
"`kept` is non-empty" (the `IndexError` an empty `kept[-1].blobs` would raise is
preserved; the C# `items[j].blobs.Last()` throws in the same case).

| | before | after |
|---|---|---|
| `Hyper_pattern` exclusive | 4.74 s | 1.38 s |
| whole engine, 20 patients | 7.1 s | 4.5 s |

### 2c. The output stage was filtering and clamping rows the .NET keeps verbatim

`Engine.run` dropped every interval lying wholly outside `[Ws, We]` and clamped
every surviving row's end to `We`. **Neither has a .NET counterpart.**
`insertBatchCalculatedDataByTime` (`Controller.cs:1766-1770`) does
`data.AddRange(conceptData)` and stores it as-is; the code comments that it
deliberately leaves data outside the window alone. The only clamp is
`_cut_from_future` on the **last** element of each concept's result, applied
inside `GetConceptDataByTime` — which the port already models in the right place.

This was invisible on the fixture, whose 20 patients all have in-window data. It
is badly wrong in general. A patient whose raws stop before `Ws` still gets
contexts induced over full history — the raw cache escape hatch in
`_raw_poisoned_by_cache` — and the .NET writes those rows out. On one real
`data_full` cohort, patients 161 and 4255 (last `Visit` in 2019 and 2012, K-window
2020–2022) got **2,153 and 2,711 rows from the .NET and 5 and 93 from the port**.

Removing both the filter and the extra clamp:

| | before | after |
|---|---|---|
| full fixture, matched / 5,048 | 5,043 | **5,047** |
| full fixture, extra rows | 216 | 212 |
| `data_full` YES cohort, rows missed | 7,726 | **3** |
| `data_full` NO cohort, rows missed | thousands | **142** |

Note this supersedes Phase 3's "per-row vs last-only We-clamp" conclusion. That
A/B changed the clamp while **keeping** the drop-outside filter, which is why it
looked like a trade-off (win 4, cost 156). Removing both wins 4 and costs
nothing, and it is what the source does.

### 2d. KarmaLego pattern names used a single-level collation

`PatternDS` builds a size ≥ 2 name as `String.Join("", pairStrings.Sorted())`,
and `List<string>.Sort()` uses the culture-aware comparer. That comparer is
**multi-level**: base letters across the whole string first, case only as a
tiebreak when the strings are otherwise equal.

The port modelled it as one level (`a < A < b < B < …` per character). Both
models agree whenever the strings differ in a letter — which is exactly why the
frozen Phase-1 reference could not tell them apart. It breaks as soon as two
pair strings differ in case at one position and in letter at a later one:

```
@@Pair:ALT_Level_State:Normal@Contain@…
@@Pair:aPTT_Level_State:Normal@Contain@…
```

Single-level puts `aPTT` first (`'a' < 'A'`); .NET compares `A`/`a` as equal at
the primary level and decides on `L < P`.

| multi-pair names reproduced | single-level | two-level |
|---|---|---|
| Phase-1 frozen reference | 29,831 / 29,831 | 29,831 / 29,831 |
| live .NET run, YES cohort | 1,283 / 1,344 | **1,344 / 1,344** |
| live .NET run, NO cohort | 357 / 359 | **359 / 359** |

### 2e. KarmaLego wrote a results row for patients the .NET drops

`run_karmalego` emitted a row per cohort patient that had *loaded* data.
`GenericVersion` writes one row per entity in `kl.Entities`, which `NeedFilter`
has already pruned to patients still holding a surviving-symbol record after the
size-1 MVS cut. On a 396-patient cohort the port emitted **75 spurious all-zero
rows**. `mine()` now returns the filtered entity set alongside the results.

---

## 3. Performance

16 cores; the .NET Mediator gets `ThreadsInBatch = 14`, the port gets one thread.

### Profile — where the time goes

`profile_engine.py` attributes exclusive wall clock per concept and per op type.
After 2b, over the fixture's 20 patients:

| op type | exclusive | share |
|---|---|---|
| pattern | 2.72 s | 60.7% |
| state | 1.60 s | 35.8% |
| trend / context / raw / event | 0.15 s | 3.5% |

`Hyper_pattern` is still the largest single concept at 31%; the rest of the
Pattern engine is a flat tail of ~0.07 s concepts. There is no second quadratic —
the remaining `Hyper_pattern` cost is inherent interval construction, faithful to
the .NET.

### Full `data_full` window — head to head

`--window 2020` (K = `[2020-2022]`), 4,590-patient source, identical cohorts:

| step | YES (132 pat.) | NO (396 pat.) |
|---|---|---|
| cohort filter (streams 1.6 GB, engine-independent) | 59 s | 66 s |
| **Mediator — .NET, 14 threads** | **231 s** | **662 s** |
| **Mediator — port, 1 thread** | **63 s** (24 load + 40 compute) | **152 s** (51 load + 101 compute) |
| KarmaLego — .NET | 11 s | 19 s |
| KarmaLego — port | 6 s | 14 s |

**Whole window: 6 min 54 s (port) vs 17 min 20 s (.NET).**

Compute is **0.25 s/patient**, flat in cohort size, so the largest window
(904 patients) projects to under 4 minutes of compute. Single-threaded Python was
not the risk it looked like — it beat the multithreaded .NET by ~4× on the
Mediator step. Memory stayed modest (`DataInstance` uses `__slots__`; the
396-patient cohort's 1.3 M distinct rows load without trouble).

---

## 4. End-to-end validation

The .NET oracle was confirmed **deterministic** first — two runs of the same
window produced byte-identical abstractions — so every residual below is real,
not threading noise.

### Sample data (`--data-dir data --window 2015`)

| | .NET rows | port rows | matched | **port missed** | port extra |
|---|---|---|---|---|---|
| YES abstractions | 1,783 | 1,787 | 1,783 | **0** | 4 |
| NO abstractions | 7,387 | 7,410 | 7,386 | **1** | 24 |
| YES KarmaLego | 1,490 patterns × 2 | | | **IDENTICAL** | |
| NO KarmaLego | 458 patterns × 6 | | | **IDENTICAL** | |

The one miss is patient 111's `Blood_Pressure_Diastole_State`. The extras are
`UREA_State` and that same patient's BP chain — the "phantom" class from Phase 3.

### Full export (`--data-dir data_full --window 2020`)

| | .NET rows | port rows | matched | **port missed** | port extra |
|---|---|---|---|---|---|
| YES abstractions (132 pat.) | 233,346 | 238,209 | 233,343 | **3** (99.999%) | 4,866 |
| NO abstractions (396 pat.) | 570,856 | 574,805 | 570,714 | **142** (99.975%) | 4,091 |

Remaining misses are the 2470 class — `NotNoac_AbsCI_ctxt`, `Context_Age_18_49`,
`StartNoac_AbsCI_ctxt`, `Using_Noac_AbsCI_QA_pattern` — concentrated in a handful
of patients. Most of the port's extras are three patients per cohort that the
.NET drops entirely (1426 / 1871 / 3313 and 1873 / 3990 / 906).

### KarmaLego in isolation

To separate KarmaLego fidelity from Mediator fidelity, the Python miner was run
on the **.NET's own abstractions** for the 396-patient cohort:

```
patients: OK (318)
pattern set: OK (137)
values: OK (318 patients x 137 patterns)
RESULT: IDENTICAL
```

Phase-1's frozen reference (20 patients × 30,722 patterns) is also still
IDENTICAL after 2d and 2e.

### Full fixture

`run_full.py` (new) runs all 273 computable concepts against the golden fixture:
**5,047 of 5,048 rows exact**, up from 5,043. The single remaining miss is
patient 2470's `Context_Age_18_49`, where the reference has a point interval and
the port has it running to 2023-11-07.

---

## 4b. The knowledge table was out of sync with the KB

Found while checking whether the mortality concept list was valid. It is a data
problem, not an engine one — and it hit the .NET engine identically, which is
exactly why the port matched it IDENTICAL throughout.

`data/knowledge_table.csv` maps `ConceptName -> (ConceptID, AllowedValues)`. Both
engines use it to build mineable symbols and to resolve the configured concept
list. It disagreed with `tak_entities/2700` three ways:

| | |
|---|---|
| ConceptIDs that disagree with the KB | **10** — the whole `*_Level_State` family, permuted (`2000` = `HGB_Level_State` there, `ALP_Level_State` in the KB) |
| TREND concepts with the wrong values | **18 of 19** — declared `Decreasing/Stable/Increasing`; the Mediator writes the .NET enum names `Dec/Same/Inc` (`trend.py:24`), so every row was discarded |
| KB concepts missing from the table | **114** |
| Table names not in the KB | 61 (14 are real raw columns; 47 are dead) |

Worse, the two label lists were written in **different id spaces**: the stroke
list in TAK ids (only 30 of 68 resolved, 10 to the *wrong* concept), the
mortality list in old-table ids (43 resolved, but 16 name concepts KB 2700 does
not contain — dose contexts and `*_Level_State` variants from an earlier KB).

Fixes:

- `build_knowledge_table.py` regenerates the table straight from
  `tak_entities/<kb>/*.xml`, with the trend-enum and boolean-casing rules baked
  in. It reports the full diff and keeps a `.bak`.
- `KL_CONCEPTS` now accepts concept **names** (ids still work), and both shipped
  lists were converted — each resolved through the id space it was actually
  written in, so intent is preserved. Unresolvable entries warn instead of
  vanishing.

Measured on a 396-patient cohort's 574,805 abstraction rows:

| | old table | regenerated |
|---|---|---|
| stroke concepts resolving | 30 of 68 | **68 of 68** |
| mineable rows | 9,154 (1.6%) | **28,641 (5.0%)** |
| patterns found (132-patient YES cohort) | 214 | **4,207** |
| whole window wall clock | 6m54s | 7m36s |

The 16 mortality concepts KB 2700 lacks are listed in `config.json`; they cannot
be mined until the knowledge base supplies them.

**Results from the regenerated table are not comparable with earlier runs** —
that is the point, but it means prior outputs should not be mixed with new ones.

One operational consequence: `MVS` is a fraction of the cohort, so on a tiny
cohort (the 20-patient sample) the threshold falls to ~1 patient, nearly every
symbol is frequent, and Lego at level 7 exhausts memory. The pipeline now warns
and `--max-level 3` makes small smoke tests finish in seconds. Real cohorts are
unaffected.

## 5. Deliberately not done

- **The last fixture row** (patient 2470's `Context_Age_18_49`). One row, one
  patient.
- **Abstract cache-poisoning / a rootId cache.** Both A/B-tested dead ends in
  Phase 3. Not revisited. (2c touches the *raw* cache escape hatch, which Phase 3
  established as correct — it is the output stage that was wrong, not the cache.)
- **Multithreading the port.** The measurement says it is unnecessary; adding
  threads would put the cache model at risk for a speedup on the step that is
  already 4× ahead.

## 6. Reproducing all of it

```
cd py_pipeline
python check_bundle.py --run     # stdlib-only, text-only, offline, and it runs
python run_gate.py               # Phase-2:  943/943 IDENTICAL
python run_pilot.py              # Phase-3:  3/3 IDENTICAL
python run_full.py               # full fixture: 5,047/5,048
python profile_engine.py --window 2021-2023

cd ../csv_pipeline
python run.py --engine python --window 2015 --data-dir data \
              --results-dir results_py  --keep-abstractions
python run.py --engine dotnet --window 2015 --data-dir data \
              --results-dir results_net --keep-abstractions
python <csv_mode>/_prep/compare_abstractions.py \
       results_net/2015-2017_YES_patterns/abstractions.csv \
       results_py/2015-2017_YES_patterns/abstractions.csv
python <csv_mode>/_prep/compare_results.py \
       results_net/2015-2017_YES_patterns/AF_KL_Stroke/genreic/results.csv \
       results_py/2015-2017_YES_patterns/AF_KL_Stroke/genreic/results.csv
```

---

## 7. What came after this summary was written

The sections above describe the port and its `--engine python` wiring inside
`csv_pipeline`. Everything below happened afterwards, in response to what the
validation and the packaging work turned up.

| Change | Why | Commit |
|---|---|---|
| **Standalone `run_pipeline.py`** — the whole pipeline in `py_pipeline/`, no dependency on `csv_pipeline` | the plan only called for wiring `--engine python`; a self-contained folder is what the research room actually needs | `d24feb3` |
| **`config.json` generalised** — `label` switch for stroke/mortality, name-based concept lists | `pipeline_stroke.py` and `pipeline_mortality.py` differed in one line; that belongs in config | `d24feb3`, `d4b8357` |
| **`make_bundle.py`, `check_bundle.py`** | cut and mechanically verify a shippable copy | `d24feb3` |
| **Knowledge-table fix** (§4b) | it disagreed with the KB; mining resolved 30 of 68 concepts | `d4b8357` |
| **`HANDBOOK.md`** | one reference document instead of eight phase notes | `75776de` |
| **`--keep-abstractions`** | `workspace/` is reused per window, so a full run kept only the last window's intermediates | `9620362` |
| **`projects.csv` / `knowledge_table.csv` made optional** | both are derived from the KB; `raw_events.csv` + `tak_entities/` is now a complete input | `37104cd` |

Additional validation done in that period:

- **Standalone folder vs `csv_pipeline --engine python`** — IDENTICAL, both stages.
- **Standalone folder at scale** — `data_full --window 2020` reproduces the
  validated output exactly (233,343 and 570,714 rows matched, 3 and 142 missed).
- **Copied to an unrelated directory, bare system Python** — IDENTICAL to .NET.
- **Minimum-input folder** (`raw_events.csv` + `tak_entities/` only) — runs end
  to end; the dictionary it generates matches the shipped one on all 376 KB concepts.
- **Full 12-window run** on all 4,590 patients — see `HANDBOOK.md`.
