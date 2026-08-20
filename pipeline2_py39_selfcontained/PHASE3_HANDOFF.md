# Phase 3 handoff — Mediator PATTERN engine (pure-Python port)

This is a self-contained handoff for implementing **Phase 3** of the pure-Python
(stdlib-only) port of the Stroke TIRP pipeline. Phases 1 (KarmaLego) and 2
(Mediator non-pattern) are DONE and validated to RESULT: IDENTICAL. Phase 3 =
the **Pattern operator** + the 63 pattern-dependent non-pattern concepts.

Read alongside: `PHASE2_RESULTS.md`, `PHASE2_ALGO_NOTES.md`, `PHASE3_PILOT_PROPOSAL.md`,
and memory `python-port-plan` (Phase 1/2 DONE sections). Source of truth for the
.NET engine: `C:\MediatorCore\Mediator_CSV`. Validation oracle:
`csv_mode\fixtures\abstractions.csv` (+ `_prep\compare_abstractions.py`). Do NOT
modify `csv_pipeline\` or `pyengine\karmalego.py`.

---

## 0. Where things stand
- **Phase 1 DONE**: `pyengine/karmalego.py` — IDENTICAL (20 pts × 30,722 patterns).
- **Phase 2 DONE**: `pyengine/mediator/{tak_parse,datainstance,functions,persistence,state,trend,context,event,engine}.py`
  — 43 pattern-free concepts / 943 rows RESULT: IDENTICAL. Run `python run_gate.py`.
- **Phase 3 NOT STARTED** (this handoff). `engine.py`'s `is Pattern` branch returns [].
- **Already prepped for Phase 3**: `tak_parse.py` now FULLY parses `pattern-definition`
  (components, pairwise, periodic) + `pattern-output` (value-local / value-group-blobs /
  start-end anchoring) into `tak_2700.json`. Verified on 870/514/116/179.

## 1. Scope
- **35 patterns** appear in the fixture (of 67 in KB 2700) + **63 pattern-dependent
  non-pattern concepts** (age/sex-stratified lab states, last-lab-before-visit states,
  score-input states). Together these are the whole remaining 5,048−943 = 4,105 rows.
- **Highest-leverage patterns** (unblock counts of the 63 downstream concepts):
  `Age_in_Visit`(116), `last_Birth_Year_Before_Visit`(590), `Visit_Year_After_BirthYear`(595)
  → **25 each**; `Visit_pattern`(870) → 14; last_X / Before_visit / Hyper → ~7-11 each.

## 2. Pilot (go/no-go gate) — build these first
| # | pattern | rows | new concepts | exercises |
|---|---------|------|--------------|-----------|
| 1 | Visit_pattern (870) | 134 | 1 | components + local constraints + relation "all" + default anchor |
| 2 | last_AST_prev_1_y (514) | 71 | 1 | + pairwise temporal (max-duration) + value-local math (component-index) + start/end anchor + time-shift |
| 3 | CHADS_Vasc_score (179) | 134 | 22 (13 pat + 9 state) | + relation "any" + periodic/calendar-frequency blob + value-group-blobs statistical sum + age chain (590→595→116→114/115) + before-visit patterns + CHASD point states |

Gate: each → `compare_abstractions.py` on its rows → IDENTICAL (same window/harness
as Phase 2). All three pass → GO for the rest. CHADS blob/score unmatchable → ESCALATE.

CHADS's 22-concept closure (all must be built): states 114,115,125,400,401,402,403,404,860;
patterns 116,126,143,144,145,179,187,188,505,550,590,595,870.

---

## 3. PATTERN algorithm — exact extraction from `BusinessEntities\TAK\Patterns\Pattern.cs`
Reimplement `Pattern.Calculate` (Pattern.cs:105); Controller calls it at Controller.cs:2487.
(`CalculateByTime`:487 is a near-dup — ignore.)

### 3.1 Data keying contract (Controller.cs:2391-2490 → Pattern.Calculate)
- `data`: key = **1-based component index** (position in `Components`), value = that
  component's derived-from `List<DataInstance>` (2406,2448,2467).
- `knowledge`: key = component **GesherID** (`component.Id`), value = TAKEntity (2450).
- `dataByAlias[alias] = data[index]` via `Components[index-1].Alias` (Pattern.cs:149-150).
This index-vs-GesherID split matters for value-function evaluation (§3.6).

### 3.2 Component gathering + local value-constraint filter (Pattern.cs:159-182)
Per component, filter its instances in place (3 filters, AND):
1. `FilterComponentDataByValueConstraints` (3179) → `SatisfiableByValueConstraints` (3262):
   parse instance & constraint to double via `TryGetValue` (RawNumeric/Pattern→Double.TryParse;
   RawOrdinal→OrderedValues rank; else NaN). NaN(nominal/bool/string)→only Equals/Different,
   **case-insensitive** `.ToLower()` compare. Numeric→ Bigger>,Smaller<,BiggerOrEqual>=,
   SmallerOrEqual<=,Equals==,Different!=. All constraints AND.
2. `FilterComponentDataByDurationConstraints` (3128): keep iff TimeSpan ∈ [MinDuration,MaxDuration].
3. `FilterComponentDataByTimeConstraints` (3153): keep iff Start≥tc.Start and End≤tc.End.
If a component empties after filtering → add its alias to `removals`.

### 3.3 Enough-components gate + zero-fallback (Pattern.cs:189-313)
- Fail iff `(removals.Any() && Relation==All)` or `(Relation==KofN && K > n_components − removals)`.
- **Zero-fallback** (199-283): under Relation==All, a component that had NO data is still
  accepted iff its concept is Numeric AND its first value-constraint is `Equals "0"`
  (3243-3245) — OR output is a ComplianceFunction. This lets score patterns treat an
  absent risk factor as 0.
- On failure: if `ValueGroupBlobs is CountFunction` → return one
  `DataInstance(pid, Name, DateTime.MinValue, DateTime.MaxValue, "0")`; else empty.
- Strip event-attribute rows from `data`/`dataByAlias` (301-313).

### 3.4 Relation dispatch + instance-finding (Pattern.cs:318-376)
- **Any** (318-324): NO pairwise; every DataInstance of every component becomes its own
  1-element blob. (Enum doc-comments are swapped — trust the code.)
- **All / KofN** (326-376):
  1. `SortDataInstancesByPriorities` (1396): per component sort by `InstancePriority`
     (`ByTime`→StartTime, `ByValue`→`Convert.ToInt64(Value)`, Asc=Min/Desc=Max, chained
     ThenBy). **Default = (ByTime, Min) = ascending StartTime** (PatternComponent.cs:71).
  2. Switch on `PairwiseConstraints.LogicalOperator` (default **And**):
     - **And**: `candidates = GetPairwiseConstraintsCandidadates` → `CompleteCandidatesToBlobs`.
     - **Or**: `GetBlobsWithAtLeastOnePairwiseConstraint`, dedup blobs by SequenceEqual.

**AND compliance-tree join** (`GetComplianceTreeForComponent`, 1541-1641): recursive DFS
building `ComponentNode→DataInstanceNode→ComponentNode…`; edges = pairwise constraints,
ordered by the OTHER component's definition index (`GetRelevantPWCs`, 2505). Instances
pulled via `.First()` and removed (`GetNextDataInstanceByOrderAndRemoveFromDataRecords`,
1816); **reuse allowed iff the component has NO InstancePriority** (`IsReuseAllowed`, 2439).
If a pwc yields no match → break (AND kills that instance). Then `GetTreeRoutes` (1670)
enumerates root-to-leaf routes; `CombineRoutes` (1708) cross-combines routes across trees:
full cartesian Union if all reuse, else "zip" (one instance/component, pop consumed). A blob
= `List<DataInstanceInBlob{DataInstance, Alias}>`.

**OR / KofN assembly** (`GetBlobsWithAtLeastOnePairwiseConstraint`, 1869): target size =
Components.Count (All) or K (KofN); for each component/instance complete it to a blob of
that size using matches + filler; K-of-N = "any K components filled".

### 3.5 Pairwise temporal/value semantics (`GetAllMatchesToDataInstance`, 2235-2368)
For di1(alias1) vs candidates(alias2) under pwc:
1. `timeOpr, valueOpr = pwc operators`; **if alias1 ≠ ComponentI.Alias, FLIP both**
   (Bigger↔Smaller, BiggerOrEqual↔SmallerOrEqual; Equals/Different unchanged) — so it's
   always evaluated as if di1 is component-i.
2. `dtI = di1.[ComponentI.Boundary]` (Start→StartTime, End→EndTime).
3. `dtJ = di.[ComponentJ.Boundary]`.
4. `duration = |dtI − dtJ|` (**absolute**).
5. time predicate on (flipped) timeOpr: Bigger dtI>dtJ, BiggerOrEqual >=, Equals ==,
   Different !=, Smaller <, SmallerOrEqual <=.
6. AND `MinDuration ≤ duration ≤ MaxDuration` (**max-duration caps |gap|; operator sets
   direction**). Defaults: Min=0s, Max=1000y. Boundary defaults: ComponentI=End,
   ComponentJ=Start; if XML omits boundary attr → Start (PairwiseConstraintComponent.cs:69).
7. value pairwise (if valueOpr): compare di1.Value vs di.Value via same numeric/ordinal machinery.
8. **SAC** (if pwc.SAC, default true): reduce to the single closest match with "no data in
   between" (`CheckForSACConstraint`, 2380). Bigger→Last, Smaller→First, Different→nearest both sides.
Granularity: **Month=30d, Year=365d fixed** (Duration.cs:39-75) — matches our `datainstance.py`.

### 3.6 Pattern-output (`CalculateBlobsOutput`, 2132-2223)
Defaults first: `InitializeUnsetBoundaryLocalPattern` (1317) → Start = min-Start component,
End = max-End component; fill alias from component-id (1369). Per blob (alias→DataInstance):
- **Start** (2173): `StartLocalPattern.BoundaryPoint` on referenced instance (Start→.Start,
  End→.End) `+ TimeShift`. Quirk: prefers `iba["component_1"]` if present (2178).
- **End** (2192): `EndLocalPattern.BoundaryPoint` on `iba[alias]` `+ TimeShift`.
- **Value** (2204): if `ValueLocalPattern==null` → literal `"True"`. Else re-key blob by
  1-based component index (`dataInstanceByIndex`, 2211) and call `ValueLocalPattern.Calculate`.
  Result `.Value ?? "UNDEF"`.
- Emit `DataInstance(entityId, this.Name, start, end, value)`.

**Value function tree** (reuses `functions.py` machinery) — leaves
`<concept-id-allowed-values id="N">` are **1-based COMPONENT INDICES**, resolved as
`data[N]` (the knowledge lookup is bypassed for patterns). MathematicalFunction:
Plus/Minus/Mult/Div/Pow/Log/Abs/Ceil/Floor/Sqrt/Trunc; Div0→NaN→"ERROR";
`MissingConceptDefValue` substitutes for absent components. Booleans→"True"/"False"
(InvariantCulture), numbers→InvariantCulture ToString, error→"ERROR", null→"UNDEF".
(e.g. Age_in_Visit output = minus(comp1, comp2) = VisitYear − BirthYear = age.)

### 3.7 Grouping / periodic / persistence (Pattern.cs:358-467, 1120-1306)
Sort blobs by Start (380). Then:
- **Periodic** (382-446): split timeline into Intervals by CalendarFrequency
  (frequency-granularity/value, relative-component-id), `FillIntervalsWithBlobs`,
  `GroupBlobsByTimeGapsAndCardinality` (2528, cardinality-min), `CalculateGroupOfBlobsOutput`.
- **No periodic but ValueGroupBlobs set** (449): one Interval first.Start→last.End, fill, aggregate.
- `CalculateGroupOfBlobsOutput` (1243): `ValueGroupBlobs.Calculate(interval.Blobs)` —
  StatisticalFunction Sum sums blob values (StatisticalFunction.cs:101), Count counts;
  else collapse per `StartEndGroupBlobs` (default ByGranularity → interval Start..End, "True").
- `CalculateFinalPatternOutput` (1120): CountFunction zero-pads gaps (bounds 1900..2100);
  if no ValueGlobalPattern → rename ConceptName=this.Name, return blobs. **NO
  Interpolate/Concatenate** (Pattern is Concatenable=false).

### 3.8 Determinism
Component order = XML order (1-based index). Per-component instance order = priority sort
(default asc StartTime). Pairwise edge order = other-component index. `.First()` consume;
reuse iff no InstancePriority. Final blobs sorted by StartTime; OR blobs deduped by SequenceEqual.

---

## 4. `pattern.py` module design (new)
`calculate(entity, name, spec, data, knowledge)` where `data` = {component_index(1-based,str) →
[DataInstance]} (engine builds this from `spec['components']` derived ids), `knowledge` =
{gesher_id → concept}. Steps mirror §3: filter by local value_constraints (reuse
`context._check_satisfaction`/`functions` value logic) → gate + zero-fallback → relation
all/any/kofn instance-finding → pairwise `get_all_matches` (flip/abs-duration/SAC) → blobs →
`pattern-output` (value-local via `functions.eval` with index-keyed data; value-group-blobs
sum/count; start/end anchor + time-shift) → periodic/calendar-frequency grouping → emit.
Then wire `engine._compute_pattern` and the `is Pattern` branch (currently `[]`).

Reuse: `datainstance.py` (Duration, DataInstance), `functions.py` (math/comparison trees —
extend to resolve component-index leaves from `data[index]`), `context._check_satisfaction`
(value constraints), `persistence` NOT used (Pattern doesn't concatenate).

## 5. Engine wiring notes
- Patterns derive from other patterns/states/contexts/raws → recursion already handled by
  `get_concept_data`; just add the pattern branch. Component derived data uses the SAME
  window as the pattern (states/trends windowed; contexts full-history — see Phase 2).
- Score patterns need the age chain + CHASD point states (ordinary states, already supported).
- Watch the zero-fallback: absent numeric component with `Equals "0"` constraint contributes 0.

## 6. Calibration facts (from Phase 2 — reuse verbatim)
- **Window** = fixed `[2022-01-01 00:00:00, 2023-12-31 22:00:00]` per patient. States/trends
  filter raw to it (filter-first, full-interval-inside, inclusive); contexts induce
  full-history then clamp end to window-end + keep only window-intersecting. Output: drop
  intervals wholly outside, clamp end to We.
- **Smoosh cap** = absoluteSmooshLimitDate 11/05/2030.
- **Concept-name matching is CASE-INSENSITIVE** (raw CSV `sex` vs TAK `Sex`).
- **Duration**: Month=30d, Year=365d fixed (no calendar math).
- **Reference artifact**: 5 patients (227,2939,3421,3804,3933) have zero contexts in the
  fixture despite inducing data; engine correctly produces them. `run_gate.py` scopes
  contexts to the reference's 15-patient coverage for the exact IDENTICAL. Patterns may hit
  similar per-patient omissions — check before assuming an engine bug.

## 7. Validation
Extend `run_gate.py` (or add `run_pilot.py`): request the pilot pattern names, run engine,
`compare_abstractions.py reference_<pat>.csv candidate_<pat>.csv` → RESULT: IDENTICAL.
Build `reference_<pat>.csv` by filtering `csv_mode\fixtures\abstractions.csv` to the pattern
name (like `reference_gate43.csv` was built). Input = `csv_mode\fixtures\mediator_raw_events.csv`.

## 8. File map
- Engine: `py_pipeline\pyengine\mediator\*.py` (+ new `pattern.py`).
- Knowledge: `py_pipeline\tak_2700.json` (regenerate via
  `python pyengine/mediator/tak_parse.py csv_pipeline/tak_entities/2700 py_pipeline/tak_2700.json`).
- Gate list: `py_pipeline\phase2_gate_concepts.json`; golden subset `reference_gate43.csv`.
- Fixtures: `C:\Users\noama1\Desktop\karma\csv_mode\fixtures\` (mediator_raw_events.csv,
  abstractions.csv, knowledge_table.csv, cohort_ids.txt).
- Comparator: `C:\Users\noama1\Desktop\karma\csv_mode\_prep\compare_abstractions.py`.
- .NET source: `C:\MediatorCore\Mediator_CSV\BusinessEntities\TAK\Patterns\Pattern.cs`.
