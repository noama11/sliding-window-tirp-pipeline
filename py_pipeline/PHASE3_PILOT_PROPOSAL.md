# Phase 3 — Pattern engine: pilot proposal

Phase 3 is the HIGH-risk half (the plan flagged a go/no-go gate). This proposes a
3-pattern pilot that exercises the entire Pattern machinery on validatable fixture
rows, so we prove feasibility before committing to the full closure.

## The Pattern operator (from the TAK XML schema)
A `<pattern>` finds temporal combinations of *component* instances and emits an
output interval per match:
- `<pattern-definition relation="all"|"any">`
  - `<components>`: each `<component id alias>` with `<local-constraints><value-constraints>`
    (a value/operator filter on that concept's instances — reuses CheckSatisfaction).
  - `<pairwise-constraints>`: `<pairwise-constraint time-comparison-operator=...>`
    between component-i and component-j, with optional `<max-duration>` (temporal join).
  - `<periodic-constraints cardinality-min>` + `<calendar-frequency frequency-granularity
    frequency-value relative-component-id>`: blob-groups instances near an anchor
    (used by score patterns).
- `<pattern-output>`:
  - `<value-local-pattern>`: a function tree (math/logical) whose leaves
    `<concept-id-allowed-values id="N">` are **1-based COMPONENT INDICES** (not concept
    ids). e.g. Age_in_Visit = `minus(comp1, comp2)` = VisitYear − BirthYear.
  - `<value-group-blobs><statistical-function statistical-operator="sum">`: aggregate a
    blob's component values (score sum).
  - `<start-local-pattern>` / `<end-local-pattern>` (alias, boundary-point, time-shift):
    anchor the emitted interval to a component's boundary.

## Fixture landscape
35 patterns appear in the fixture (of 67 in KB 2700). Families:
- **Visit_pattern (870)** — 1 component (Visit_ctxt==True), no pairwise. The foundation.
- **last_X_prev_N (506/507/511-516/517)** — 2 comps (Visit + lab) + 1 pairwise temporal
  (lab within N months before visit); output = the lab value anchored to the visit.
- **Before_visit (126/141-145/149/459/138)** — 2 comps (Visit + condition-state) +
  pairwise; boolean output.
- **score/count (179 CHADS, 830 HAS_BLED, 1399 CCI, 440/441/449/451/878)** — relation
  "any" over point-contribution states + periodic/calendar-frequency blob +
  statistical sum.
- **drug (872/874/875)**, **age (116/590/595 — intermediate, not in fixture)**, misc.

## Unblock impact (of the 63 pattern-dependent non-pattern concepts)
Highest-leverage patterns: **Age_in_Visit (116), last_Birth_Year_Before_Visit (590),
Visit_Year_After_BirthYear (595)** each unblock **25**; Visit_pattern (870) unblocks 14;
last_X / Before_visit / Hyper unblock ~7-11 each.

## Proposed pilot — 3 fixture patterns, tiered by machinery
| # | pattern | rows | new concepts | exercises |
|---|---------|------|--------------|-----------|
| 1 | **Visit_pattern (870)** | 134 | 1 | components + local constraints + relation "all" + anchor output |
| 2 | **last_AST_prev_1_y (514)** | 71 | 1 | + pairwise temporal (max-duration) + value-local-pattern (component-index math) + start/end anchor + time-shift |
| 3 | **CHADS_Vasc_score (179)** | 134 | 22 (13 pat + 9 state) | + relation "any" + periodic/calendar-frequency blob + value-group-blobs statistical sum + the deep age chain (590→595→116→114/115) + before-visit patterns + CHASD point states |

Pilot 1 & 2 are shallow (prove the core join/output engine). Pilot 3 is the full
stack — its 22-concept closure includes the age patterns (which alone unblock 25 of
the 63 downstream concepts), so passing CHADS de-risks most of Phase 3.

## Go / no-go gate
Run each pilot pattern end-to-end and compare its fixture rows with
`compare_abstractions.py` (window [2022-01-01, 2023-12-31 22:00], same harness as
Phase 2):
- **GO** if all three reach RESULT: IDENTICAL on their rows → the pattern engine is
  proven; proceed to the remaining ~32 fixture patterns + 63 downstream concepts.
- **NO-GO / escalate** if CHADS's blob-assembly or score semantics can't be matched
  exactly → surface to the user before sinking weeks into the remaining scores.

## Module plan (`pyengine/mediator/`)
- extend `tak_parse.py`: parse `pattern-definition` (components, pairwise, periodic) +
  `pattern-output` (value-local / value-group-blobs / start/end-local).
- new `pattern.py`: component gathering, combination enumeration (all/any), pairwise
  temporal join, calendar-frequency blob assembly, pattern-output evaluation
  (component-index operands, statistical sum), anchoring + interpolate.
- `engine.py`: wire the `is Pattern` branch (currently returns []).

## Open algorithm questions (being resolved by the C# Pattern.cs scout)
- Exact combination-enumeration order + determinism (cartesian+prune vs join).
- Blob-assembly geometry (calendar-frequency grouping, cardinality-min).
- `relation="any"` semantics (which subsets match; how absent components score).
- Component-index operand resolution + statistical-sum over a blob.
- UNDEF/ERROR sentinels (Pattern.cs:2212) and output persistence.
