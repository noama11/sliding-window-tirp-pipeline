# Pure-Python Port of the Stroke TIRP Pipeline (Mediator + KarmaLego)

> ## STATUS — all phases complete (last updated 2026-08-01)
>
> | Phase | Scope | Status | Record |
> |---|---|---|---|
> | 1 | KarmaLego port | **done** — IDENTICAL, 20 patients x 30,722 patterns | `PHASE1_PROMPT.txt` |
> | 2 | Mediator, non-pattern operators | **done** — 943/943 IDENTICAL | `PHASE2_RESULTS.md` |
> | 3 | Mediator Pattern engine (the high-risk one) | **done** — pilot gate met, then full closure | `PHASE3_COMPLETE_SUMMARY.md` |
> | 4 | End-to-end wiring + packaging | **done** — standalone folder, validated at scale | `PHASE4_SUMMARY.md` |
>
> Beyond the original plan, because validation turned them up:
>
> | | Status | Record |
> |---|---|---|
> | Standalone `run_pipeline.py` (the plan only called for wiring `--engine python`) | **done** | `HANDBOOK.md` |
> | Six engine defects found by A/B against live .NET runs | **fixed** | `PHASE4_SUMMARY.md` §2 |
> | `knowledge_table.csv` out of sync with the KB — mining used the wrong concepts | **fixed** | `PHASE4_SUMMARY.md` §4b |
> | Stroke/mortality unified behind a config `label` switch | **done** | `HANDBOOK.md` §5 |
> | `projects.csv` / `knowledge_table.csv` made optional inputs | **done** | `HANDBOOK.md` §4 |
>
> **Not done:** the `all_patients` variant (`pipeline_all_patients.py` — no
> YES/NO split, no Y window, MVS 0.3, from 2000) has no equivalent here.
>
> **For current behaviour read `HANDBOOK.md`.** Everything below is the original
> 2026-07-10 plan, kept as written so the reasoning and risk assessment stay
> auditable. Where it disagrees with the handbook, the handbook is right.


## Context

**Why:** The stroke temporal-pattern pipeline must run inside a **research room** with no
internet and (reportedly) **text-copy-paste only** — no USB, no network share, no binary
transfer. The current `csv_pipeline/` bundle works and is validated, but it depends on two
**compiled .NET engines** (`API.exe` = Mediator, `KarmaLegoConsoleApp.exe`) plus the ~55 MB
.NET 8 runtime — none of which are copy-pasteable. See `RESEARCH_ROOM.md`.

**Goal (user's preferred direction):** reimplement the whole pipeline in **pure Python
(standard library only)** so it can be pasted into the room as text, produce the same
`{window}_YES_patterns` / `_NO_patterns` matrices, and be **validated here against the
original** before it ships. This is "Option C" (faithful port) from `RESEARCH_ROOM.md`.

**Feasibility verdict:** Feasible. The orchestration is already Python; KarmaLego is a
well-bounded ~2k-LOC algorithm with a frozen reference; the Mediator non-pattern abstractions
are self-contained and modest. The one genuine risk is the **Mediator Pattern engine** (67
derived concepts) — intricate, lightly-documented, weeks of work. The plan is therefore
**phased and validation-gated**, with an explicit go/no-go after a 3-pattern pilot.

---

## Deliverable location — a NEW self-contained folder `py_pipeline/`

Everything for the pure-Python pipeline lives in its own new bundle **`py_pipeline/`** at the
repo root — kept SEPARATE from the .NET `csv_pipeline/` (which stays as the validation oracle).
`py_pipeline/` is fully self-contained and pasteable-as-text: no compiled engines, no .NET, no
non-stdlib deps. Target layout:

```
py_pipeline/
  run.py                 # orchestrator (ported from csv_pipeline/run.py; in-process, no subprocess)
  config.json            # mirrors csv_pipeline/config.json (windows, cohort, karmalego, project)
  pyengine/
    __init__.py
    tak_parse.py         # one-time TAK XML -> tak_2700.json flattener
    karmalego.py         # Phase 1
    mediator/            # Phase 2-3 (state.py, trend.py, gradient.py, context.py, event.py,
                         #            functions.py, persistence.py, patterns.py, engine.py, ...)
  tak_2700.json          # pre-flattened rule bundle (stroke-config closure subset)
  data/                  # raw_events.csv (+ knowledge_table.csv, projects.csv) — user-supplied
  README.md
  PLAN.md                # this file
  PHASE1_PROMPT.txt      # ready-to-paste kickoff prompt for a fresh CC session
```

The .NET source trees and `csv_mode/` fixtures/reference are read-only inputs used HERE for
validation; only the finished `py_pipeline/` folder travels to the research room.

## What we already have (reuse — do not rebuild)

- **Orchestration is done in Python:** `csv_pipeline/run.py` already does windowing, cohort
  selection (YES deterministic / NO seeded 3×), per-cohort CSV filtering/dedup, batching, and
  config rewriting. Only two lines shell out to the engines (`run_mediator` → `API.exe`,
  `run_karmalego` → `KarmaLegoConsoleApp.exe`). The port **replaces those two subprocess calls
  with in-process Python**; the rest of `run.py` stays.
- **Validation harness exists** in `C:\Users\noama1\Desktop\karma\csv_mode\`:
  - `_prep/compare_abstractions.py` — order-insensitive check of Mediator output vs
    `fixtures/abstractions.csv` (5,048 rows, 141 concepts, the 20-patient cohort).
  - `_prep/compare_results.py` — order-insensitive check of KarmaLego output vs the frozen
    `reference/sql_run/AF_KL_Ref/genreic/results.csv` (**30,722 patterns × 20 patients**).
  - `fixtures/` — `raw_events.csv`, `abstractions.csv`, `knowledge_table.csv`, `projects.csv`,
    `cohort_ids.txt` (the exact 20 patient ids). These are the golden inputs/outputs.
- **The engines themselves stay here** as the oracle: we validate the Python port against them
  and the frozen fixtures on this machine; only the *validated Python* travels to the room.
- **The rule data:** all abstraction logic is in the 376 XML files at
  `csv_pipeline/tak_entities/2700/` (~0.86 MB). `knowledge_table.csv` is only a name↔id index,
  NOT a rule dictionary — the port must parse the XML.

## Reference: engine source to port from (read-only, do NOT modify)

- Mediator: `C:\MediatorCore\Mediator_CSV` — key files, in porting order:
  `BusinessEntities/Data/DataInstance.cs`; `AllowedValues/*` + `Misc/{LocalPersistence,
  GlobalPersistence,Duration,TemporalSemantic}.cs`; `AbstractConcepts/State.cs` +
  `Functions/{MappingFunction,LogicalFunction,ComparisonFunction,MathematicalFunction}.cs`;
  `AbstractConcepts/{Trend,Gradient,Rate}.cs`; `Context.cs`; `Event.cs`;
  `ComputationalServices/Controller.cs` (methods `GetConceptData` @2183, `orderConcepts`
  @4048, `Smoosh` @3937, `GetPartitions`/`GetPartitionData`, `filterDataByContext`);
  `Patterns/*` LAST (the hard part).
- KarmaLego: `C:\Users\noama1\Desktop\karma\KarmaLego_CSV\KarmaLegoCore` — core algorithm in
  `BO/KarmaLego/KL.BO.KarmaLegoDFSBO.cs` (~950 LOC), `KarmaLegoBaseBO.cs`,
  `BO/RelationsLogicBO.cs` (Allen relations), output in `KarmaLegoConsoleApp/Controller.cs`
  (`CsvSerializer.SerializeToCsv`). Config semantics: RelationsSet=Three, SACtype=CSAC,
  MaxLegoLevel=7, mvs=0.2, maxGap=0, timeUnit=Days, statistics=HorizontalSupport.

---

## I/O contracts the port must preserve (byte-compatible CSVs)

- `raw_events.csv` / `mediator_raw_events.csv` / `abstractions.csv`: columns
  `PatientID,ConceptName,StartTime,EndTime,Value`; timestamps `yyyy-MM-dd HH:mm:ss`.
- KarmaLego input = **raw ∪ abstractions**, filtered by patient set + concept list.
- Output `results.csv`: header `id,<pattern_1>,<pattern_2>,...`; one row/patient; cell =
  HorizontalSupport integer (missing → `0`); pattern name form
  `@@Pair:<Concept:Value>@<Relation>@<Concept:Value>` extended per level.
- `knowledge_table.csv` = `ConceptName,ConceptID,AllowedValues`; `projects.csv` maps
  40144→Kb_ID 2700. Keep these schemas.

---

## Plan (phased, each phase gated by a validation script)

### Phase 0 — Scaffolding & rule extraction (days)
- Create the new self-contained bundle `py_pipeline/` with the layout above (stdlib-only).
  Copy `csv_pipeline/run.py` + `config.json` in as the starting orchestrator; the two engine
  subprocess calls become in-process `pyengine` calls as each phase lands. Keep `csv_pipeline/`
  untouched as the .NET oracle for diffing.
- **Pre-flatten the TAK XML once, here,** into a single compact `tak_2700.json` (concepts,
  types, derived-from edges, allowed-values, function trees, persistence, contexts, pattern
  defs). This (a) isolates all the messy schema-drift parsing (`.xml`/`.XML`, capitalized
  event tags, mixed quotes, 12 dangling refs) into one offline step, and (b) becomes the
  compact, **pasteable** rule bundle for the room (full KB ≈ a few hundred KB of JSON; the
  stroke-closure subset is far smaller). Parser lives in `pyengine/tak_parse.py`.
- Wire `compare_abstractions.py` + `compare_results.py` as the test gates.

### Phase 1 — KarmaLego in Python (≈1–2 weeks) — LOW RISK, do first
- `py_pipeline/pyengine/karmalego.py`: Allen relations (Three model), Karma (size-1 → frequent pairs by
  MVS), Lego (DFS extension, CSAC adjacency, MaxLegoLevel=7), HorizontalSupport stat, matrix
  writer.
- **Validate decoupled from Mediator:** feed the *fixture* `abstractions.csv` in, compare
  output to the frozen 30,722-pattern reference via `compare_results.py` → require
  `RESULT: IDENTICAL`. This proves half the pipeline before touching Mediator.

### Phase 2 — Mediator non-pattern abstractions (≈2–3 weeks) — MEDIUM RISK
- `py_pipeline/pyengine/mediator/` modules: `datainstance.py`, `persistence.py` (local smoosh + global
  gap-bridge/concatenate), `functions.py` (mapping/logical/comparison/mathematical trees),
  `state.py` (sweep-line partitions + first-match mapping + `abstraction-at-contexts`
  context-switched thresholds), `trend.py`, `gradient.py`, `rate.py`, `context.py`
  (inducer/clipper interval induction), `event.py`, and `engine.py` (topological
  `orderConcepts` + memoized recursive `GetConceptData` + context partitioning).
- Single-threaded & deterministic (safe: engine non-determinism is row-order only).
- **Validate:** run on the 20-patient fixture; `compare_abstractions.py` restricted to
  non-pattern concept rows must match the fixture exactly.

### Phase 3 — Mediator Pattern engine (the hard part) — HIGH RISK, staged
- **3a. Pilot (≈1–2 weeks):** implement the pattern operator for **3 representative patterns**
  — `CHADS_Vasc_score_pattern` (179, sum-of-8, periodic anchor), `CCI_score_pattern` (1399,
  widest fan-out=17, depth 7), `HAS_BLED` (fan-out 12). Covers components + local/value
  constraints + K-of-N/All/Any relation + pairwise + periodic/calendar-frequency +
  statistical output. Validate those concepts' rows against the fixture.
- **★ GO/NO-GO GATE:** if the 3 pilots reproduce the fixture rows exactly (order-insensitive),
  the remaining 64 patterns are more of the same → proceed. If they diverge and can't be
  reconciled against the C# in reasonable time, escalate the fidelity decision (fall back to
  approximate/Option-B for the intractable patterns, documented).
- **3b. Remaining patterns (≈2–4 weeks):** port the rest of the closure, each gated by its
  fixture rows. Only patterns in the transitive closure of the target concept list are needed.

### Phase 4 — End-to-end wiring + packaging (≈1 week)
- `py_pipeline/run.py` now calls `pyengine` in-process end to end (no subprocess, no .NET).
- **Full-pipeline validation:** run `csv_pipeline` (.NET) and `py_pipeline` (Python) on the same
  20-patient cohort; `compare_results.py` on the final matrices must be `IDENTICAL`. Then diff on
  a larger cohort from `data_full/` to check scale/perf.
- `py_pipeline/` IS the pasteable bundle (`pyengine/*.py` + `tak_2700.json` + `run.py` +
  `config.json` + `README.md`). Document the copy-paste procedure (chunking if the room caps
  paste size).

---

## Testing / validation strategy (against the original)

1. **Unit/operator level:** for each abstraction operator, diff Python output vs the .NET
   engine on the fixture cohort (per-concept row subset of `abstractions.csv`).
2. **Mediator whole:** `compare_abstractions.py` — Python `abstractions.csv` == fixture
   (5,048 rows), order-insensitive, exact values.
3. **KarmaLego whole:** `compare_results.py` — Python matrix == frozen 30,722-pattern
   reference, order-insensitive.
4. **End-to-end:** run `csv_pipeline` (.NET) and `py_pipeline` (Python) on the same cohort;
   compare final `results.csv`. Gate = `RESULT: IDENTICAL` on the 20-patient reference.
5. **Regression at scale:** repeat on a `data_full/` window; accept documented, bounded
   divergence only where it traces to the known .NET row-order non-determinism.

---

## Decisions locked (confirmed with user)

- **Fidelity bar = FAITHFUL / IDENTICAL.** The Python port must reproduce the frozen reference
  exactly (order-insensitive, exact values) for both abstractions and mined patterns. The full
  Pattern engine (for the in-scope closure) is therefore **mandatory** — no approximate/Option-B
  fallback. If a specific pattern proves genuinely intractable to reconcile against the C#, that
  is a **user escalation**, not a silent simplification.
- **Scope = STROKE-CONFIG CLOSURE.** Only the transitive dependency closure of the 68-concept
  list in `csv_pipeline/config.json` (`karmalego.concepts`) must be ported/validated — NOT all
  376 concepts. First implementation step is to **compute this closure** from `tak_2700.json`
  (walk `derived-from` + pattern `components` + context inducers/clippers from the 68 roots) and
  freeze the exact concept set the engine must support. This bounds both the Pattern-engine work
  and the pasteable rule-bundle size. (For reference: CHADS_Vasc's closure alone is 41 concepts,
  CCI's is 58; the union across the 68 roots is the real target set.)
- **Kickoff = HOLD.** Leave this as a reviewed plan; do not start implementation this session.

## Consequence for the go/no-go gate (Phase 3a)

Because the bar is faithful, the pilot gate is a **reconcile-or-escalate**, not a fall-back-to-
approximate: if the 3 pilot patterns can't be made to match the fixture exactly, stop and bring
the specific divergence + options to the user rather than shipping an approximation.

## Risks

- Pattern engine divergence (mitigated by the 3-pattern pilot gate before full commitment).
- Schema drift in the XML (isolated to the one-time `tak_parse.py` extraction).
- Paste-size ceiling in the room may force chunked transfer of `tak_2700.json` (bounded by
  scoping to the stroke closure).
- Perf: Python single-threaded will be slower than the multithreaded .NET engine; acceptable
  for a research-room batch run, but measure on `data_full/`.
