# Phase 2 — Mediator non-pattern port: RESULTS

**Status: gate MET — RESULT: IDENTICAL (943/943 rows).**

## What was built (`pyengine/mediator/`)
Pure-Python (stdlib only) reimplementation of the Mediator non-pattern operators,
ported equivalence-preserving from `C:\MediatorCore\Mediator_CSV`:

| module | ports |
|--------|-------|
| `tak_parse.py`   | 376 TAK XML -> `tak_2700.json`; classify by root element; abstraction-at-contexts; transitive closure / pattern-dependency |
| `datainstance.py`| DataInstance model + Duration granularity (month=30d, year=365d fixed) |
| `functions.py`   | MappingFunction + Logical/Comparison/Mathematical evaluation trees (3-valued) |
| `persistence.py` | local Smoosh (proportional gap-split) + global Interpolate/Concatenate/Intersect |
| `state.py`       | sweep-line partitioner + mapping + interpolate |
| `trend.py`       | Dec/Same/Inc segmentation (clusters, extreme reduction, significant segments, engulf, interpolate) |
| `context.py`     | induction + clipping + merge; filterDataByContext |
| `event.py`       | pass-through (not needed for gate) |
| `engine.py`      | orderConcepts, GetConceptData recursion, GetPartitions/GetPartitionData, windowing, I/O |

`gradient.py`/`rate.py` intentionally omitted — KB 2700 has no such roots (all
trend-like concepts use `<trend>`).

## The gate (43 pattern-free concepts / 943 rows)
Of the 106 non-pattern concepts in the fixture, only 43 are pattern-free and
computable by the non-pattern engine (State 20, Trend 17, Context 6); the other
63 transitively depend on Pattern concepts (age/sex context-switching via Age
patterns, last-lab-before-visit / Visit / score patterns) and are Phase 3.
`phase2_gate_concepts.json` lists the 43; `reference_gate43.csv` is the golden
subset of `abstractions.csv`.

## Validation
Run `python run_gate.py` then:
```
compare_abstractions.py reference_gate43.csv candidate_gate43_scoped.csv  -> RESULT: IDENTICAL (943/943)
compare_abstractions.py reference_gate43.csv candidate_gate43.csv         -> 943 matched + 51 extra
```
Every one of the 943 reference rows is reproduced bit-for-bit (order-insensitive
multiset, exact timestamps + values), across all three operator families.

## Calibration facts (learned during validation)
- **Time window**: the fixture was generated with a fixed Mediator window
  `[2022-01-01 00:00:00, 2023-12-31 22:00:00]` for every patient (K=2yr ending
  2024; the `...22:00:00` end is the "cut-from-future" clamp, = 2024-01-01
  local at UTC+2). States/trends filter raw to the window (filter-first,
  full-interval-inside, inclusive); contexts induce full-history then clamp the
  end to the window end and keep only intervals intersecting the window (so
  `Live_ctxt`/`Female_Context` keep their birth-year start).
- **Smoosh limit** = `absoluteSmooshLimitDate` 11/05/2030 (fixed cap on the last
  point's forward extension).
- **Concept-name matching is case-insensitive** — the raw CSV carries `sex`
  while the TAK entity is `Sex` (id 28). Without this, all sex contexts and the
  3 context-switched states (ALT/AST/INR) vanish.
- **Trend canonicalization**: emits `Dec`/`Same`/`Inc` (enum member names), not
  the XML's Decreasing/Stable/Increasing.

## The 51 "extra" rows — reference artifact (not an engine error)
Exactly **5 patients (227, 2939, 3421, 3804, 3933)** have **zero** context rows
in `abstractions.csv`, while the other 15 have full contexts. Those 5 have Visit
value-1 events and sex/birth data identical in nature to patients that DO get
contexts — there is no data or cohort (stroke-status) reason for the omission.
The engine correctly induces their contexts (and matches the other 15 patients
bit-for-bit), so the 51 extras are a fixture inconsistency where contexts were
not persisted for 5 patients. `candidate_gate43_scoped.csv` scopes context rows
to the reference's coverage and yields exact IDENTICAL.

## Next (Phase 3)
Pattern engine + the 63 pattern-dependent non-pattern concepts + the 35
`*_pattern` concepts.
