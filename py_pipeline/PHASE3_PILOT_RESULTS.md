# Phase 3 pilot — PATTERN engine: RESULTS

**Gate: MET. All three pilot patterns RESULT: IDENTICAL. → GO.**

| # | pattern | rows | result |
|---|---------|------|--------|
| 1 | `Visit_pattern` (870) | 134 | **IDENTICAL** |
| 2 | `last_AST_prev_1_y_pattern` (514) | 71 | **IDENTICAL** |
| 3 | `CHADS_Vasc_score_pattern` (179) | 134 | **IDENTICAL** |

Reproduce: `python run_pilot.py` (builds `reference_<name>.csv` from
`csv_mode\fixtures\abstractions.csv`, runs the engine on
`mediator_raw_events.csv` over the Phase-2 window
`[2022-01-01 00:00:00, 2023-12-31 22:00:00]`, and calls
`csv_mode\_prep\compare_abstractions.py`). Output is byte-stable across runs.

**No Phase-2 regression**: `python run_gate.py` →
`compare_abstractions.py reference_gate43.csv candidate_gate43_scoped.csv`
→ RESULT: IDENTICAL (943/943).

---

## What was built

| module | change |
|--------|--------|
| `pyengine/mediator/pattern.py` | **new** — full port of `Pattern.Calculate` |
| `tak_parse.py` | full pattern surface (below) + numeric/boolean state allowed-values |
| `functions.py` | allowed-values-driven output typing + pattern value-local evaluator |
| `state.py` / `trend.py` / `context.py` | `concatenable` is now read from the XML |
| `engine.py` | `is Pattern` branch + "cut from the future" on intermediates |

### `tak_parse.py` additions
Pairwise boundary points / min-duration / value-comparison-operator / SAC /
container `logical-operator`; periodic cardinality-max, min/max gap
within/between, `last-complete`; `k-of-n`; component time/duration constraints,
`save-bad-values`, `<priorities>`; output `start-end-group-blobs`,
`value-global-pattern`, global-function kind; the `output_parse_aborted` quirk
(below). `referenced_ids` no longer collects value-local-pattern leaves — those
are **component indices**, and treating them as concept ids fabricated
dependencies (e.g. `id="2"` → RBC), inflating every pattern's closure.

---

## Fidelity findings that decided the outcome

Each of these was a behavioural fork where the obvious reading is wrong.

1. **`TryGetValue` only parses RawNumeric / RawOrdinal / Pattern**
   (Pattern.cs:3384-3403). A **State** value is therefore always `NaN`, which
   drops into the string branch of `SatisfiableByValueConstraints` — and in that
   branch only `Equals`/`Different` have a `case`, so every other operator
   leaves `constraintResult` at its initialised **`true`**. This is what makes
   CHADS's `bigger-equal "0"` on the seven point-contribution states admit all
   instances instead of rejecting all of them.

2. **`concatenable` comes from the XML, not the constructor.** Only the
   *programmatic* `State`/`Trend`/`Context` ctors force `Concatenable = true`
   (State.cs:46); the `XmlReader` ctors read `<temporal-semantic>`. **100 of
   156 states declare `concatenable="false"`**, and their interpolation tables
   are empty (= "always bridge"). Hard-coding `true` merged all 417
   `Visit_ct_0_p_CHASD_state` intervals for a patient into one — which then made
   the CHADS calendar-frequency split produce millions of 3-minute intervals
   (this was the apparent "hang"). The Phase-2 gate never caught it because all
   43 gate concepts that produce >1 candidate are `concatenable="true"`.

3. **`_parse_state` only looked for ordinal/nominal allowed-values**, so the 56
   **numeric** states had `output_type: null`. Combined with `_output_type()`'s
   op_type heuristic they were typed "ordinal", making every comparison against
   them `NaN`. `AllowedValues.OutputType` (the declared attribute) is the single
   source of truth in `ComparisonFunction`/`MathematicalFunction`; `boolean` had
   to be added to the string-comparison branch (ComparisonFunction.cs:125-138)
   or `<state> == "True"` over a boolean pattern like 870 never fires.

4. **The value-local knowledge check is commented out**
   (MathematicalFunction.cs:281-285), which is precisely why
   `<concept-id-allowed-values id="N">` works as a 1-based **component index**:
   the leaf is resolved as `Double.TryParse(data[N].Value)` with no type lookup.

5. **`StatisticalFunction`'s output interval is `data.Last()`'s**, not the
   enclosing interval's (StatisticalFunction.cs:112). The fixture confirms it
   exactly: zero-score CHADS rows carry the `[23:59, 00:01]` Visit-context
   interval (the 860 instance is the only, hence last, blob) while non-zero rows
   collapse to the `[00:00:00, 00:00:00]` point of the last contributing state.

6. **SAC is inert in practice.** `savedBadValues` is fed under the *inverted*
   guard `if (!badValues.Any())` (Pattern.cs:180) and `save-bad-values` is never
   set in KB 2700, so `CheckForSACConstraint` always returns true. SAC still
   matters as a *selector*: `Bigger → result.Last()` is what makes
   `last_AST_prev_1_y` mean "last AST", and `BiggerOrEqual`/`SmallerOrEqual`/
   `Equals` have **no case** in that switch, so an SAC-enabled pwc with those
   operators discards every match.

7. **Pairwise boundaries default to `start` for BOTH i and j.** The ctor
   documents end/start, but `PairwiseConstraintComponent.ReadXml` (:62-69)
   assigns `Start` whenever the attribute is absent — and no KB-2700 pattern
   sets it.

8. **`InitializeUnsetBoundaryLocalPattern` compares index keys against
   GesherIDs** (:1331-1355), so the alias lookup normally fails and the alias is
   then filled from `mappingIndexToAlias[index]` (:1369-1386). Reproduced
   verbatim, including the `iba["component_1"]` override at :2178.

9. **`PatternOutput.ReadXml`'s try/catch swallows an unknown value-local child
   and aborts the rest of the element.** Patterns 508/509 put a bare
   `<concept-id-allowed-values>` under `<value-local-pattern>`, so their
   `start-local-pattern`, `end-local-pattern` **and** `value-group-blobs` are
   never read. Captured as `output_parse_aborted`.

10. **`GetConceptDataByTime` clamps only the LAST element's `EndTime` to the
    window end** (Controller.cs:2764 / :3068), and the Context branch keeps only
    intervals with `StartTime <= endTime` (:2732). Phase 2 applied the clamp at
    output time, which was equivalent for leaf output but not for pattern
    *inputs*; the clamp now happens inside `get_concept_data`.

### Known non-issues
* **KB 2700 declares `<priorities/>` empty on every component**, so
  `InstancePriority` is empty ⇒ no per-component reordering and **reuse is
  always allowed**. Both branches are implemented anyway.
* Also unused by KB 2700: `k-of-n`, `logical-operator="or"`,
  `value-comparison-operator`, `<min-duration>`, `<duration-constraint>`,
  component `at-context`/`context-flag`, `start-end-group-blobs`,
  `value-global-pattern`, default `component_N` aliases.
* **Unstable-sort hazard**: `blobs.Sort(...)` (:380) is .NET introsort, which is
  *not* stable, so ties on `StartTime` have implementation-defined order and
  `data.Last()` could differ. Checked on this fixture: of the 20 CHADS intervals
  holding more than one blob, **none** has an ambiguous last blob.

---

## Coverage

Exercised by the pilot's 39-concept closure: relation `all` + `any`; context,
state, raw, event and pattern components; local value constraints (numeric and
string paths); pairwise `bigger` with and without `max-duration`; SAC-`Last`
selection; `abs` value-local; explicit and defaulted start/end anchoring;
calendar-frequency `minute`/`day` relative-component intervals; cardinality-min;
`FillIntervalsWithBlobs`; statistical `sum`.

The **age chain** (`last_Birth_Year_Before_Visit` 590 -> `Visit_Year_After_BirthYear`
595 -> `Age_in_Visit` 116 -> states 114/115) produces zero rows *inside the
window* -- `Birth_Year` is stamped 1928-1939 -- and the reference agrees. It is
nonetheless exercised over **full history** through the context-induction path:
patient 111's chain yields 417 `Age_in_Visit` rows (age 82 in 2010 -> 94 in 2022)
feeding `State_Age_65Plus` -> `Ctxt_Age_65Plus`. So the `minus` value-local, the
`equal` pairwise operator and the `sac="false"` path *are* verified against data,
via the 7 age contexts and the 8 age/sex-stratified lab states that now match.

---

# Full-fixture status (Phase 3b in progress)

| | concepts | rows exact |
|---|---|---|
| at the pilot gate | 105 / 141 | 3,609 / 5,048 |
| **now** | **137 / 141** | **4,878 / 5,048** |

137 = 122 exact + 15 differing *only* by the known 5-patient context artifact
(`missing 0`, extras confined to patients 227 / 2939 / 3421 / 3804 / 3933).
Re-verified after every change: Phase-2 gate 943/943 IDENTICAL, all three pilot
patterns IDENTICAL.

## The two bugs that closed the gap

**A. The .NET data layer poisons its own full-history cache**
(`engine._raw_poisoned_by_cache`). `ComplementaryDataServicesBL.addToCache`
(:191-199) stores every fetch under both an exact
`"{proj} {pat} {concept} {start} {end}"` key **and a time-less
`"{proj} {pat} {concept}"` key**. `GetDataByTime` (the ByTime pipeline) reads the
exact key only -- but `GetData`, the non-ByTime function that `GetContextsData`
(:3775) and the non-ByTime Pattern branch use, falls back to the time-less key
and merely re-filters it (:156-177). Every raw is computed as a top-level
concept first, ByTime, over the patient window, so the time-less key already
holds the **windowed** list by the time any context induction asks for "all
time". The escape hatch: `GetDataByTime` returns early **before caching** when
the windowed fetch is empty (:255-259), so a raw with *no* in-window data is
never poisoned and really is served full-history.

That asymmetry is exactly what the reference shows, and nothing else explains
both halves: `Live_ctxt` keeps its 1928 birth-year start and `Ctxt_Female` its
birth-minus-120y start (their inducers have no in-window rows), while
`Ctxt_Age_65Plus` starts at patient 111's first **in-window** Visit
(2022-01-21) rather than at 2010-07-16 where `State_Age_65Plus` actually begins.
Fixing this repaired the 7 age contexts and, through them, the 8 age/sex
context-switched lab states -- 15 concepts from one change.

**B. Events were not window-filtered.** The engine returned an event concept's
raw rows verbatim. `GetConceptDataByTime`'s Event branch (:2784) goes through
`DataProvider.GetGroupData(ev, startTime, endTime)`, which applies the same
`StartTime >= start && EndTime <= end` predicate as the raw path
(DataCSVDA.cs:88-97). The two drug patterns `Anti_Platelets_Drugs_pattern` (871)
and `Nsaid_pattern` (872) have **Event** components, so they leaked
2011-vintage rows into `NSAIDs _or_antiPLT_state` -> pattern 149 ->
`HAS_BLED_score`, and into the NOAC chain. One line fixed the whole HAS_BLED
cluster and most of the NOAC cluster.

## Remaining -- 4 concepts, one cluster (~170 rows)

* `Using_Noac_AbsCI_QA_pattern` (484) needs **`ComplianceFunction`**, which is
  not ported. Only 2 KB-2700 patterns use it, and they are also the only 2 that
  use `<time-constraint>`. It additionally raises `KeyError: 'NOAC_state'` for
  patients where component 504 has no data: the compliance branch of the
  zero-fallback accepts the pattern, `dataByAlias.Remove(removal)` drops the
  alias, and `CalculateBlobsOutput` dereferences `iba["NOAC_state"]`. **The C#
  does the same** (unhandled `KeyNotFoundException`, swallowed by
  `insertBatchCalculatedDataByTime`'s try/catch at :1761) -- so the question is
  what the Controller emits for that patient, not whether we crash.
* `StartNoac_AbsCI_ctxt` (80 missing / 112 extra), `NotNoac_AbsCI_ctxt` (4/4),
  `Context_Age_18_49` (1/1) -- the contexts feeding it.

## Next
Port `ComplianceFunction` and settle the exception semantics; then Phase 4 (wire
`--engine python` into `run.py`, package the pasteable bundle) and measure
performance on `data_full\`.
