# Phase 3 Complete — Summary & Conclusions

_Last updated: 2026-07-28. Reconciles two parallel sessions that worked the same files._

> **Partly superseded by `PHASE4_SUMMARY.md`.** Two conclusions below no longer
> hold, both corrected against the .NET source during Phase-4 validation:
>
> * **§5 "Per-row vs last-only We-clamp"** — the trade-off was an artefact of the
>   experiment. `Engine.run` was *also* dropping intervals lying wholly outside
>   `[Ws, We]`, which the .NET never does (`insertBatchCalculatedDataByTime`
>   stores `conceptData` verbatim). The earlier A/B changed the clamp while
>   keeping that filter, so it looked like "win 4, cost 156". Removing **both**
>   wins 4 and costs nothing: the full fixture went 5,043 → **5,047 / 5,048**,
>   and on a real `data_full` cohort it recovered ~4,900 rows on two patients.
> * **The headline numbers** — full fixture is now 5,047/5,048, not 5,043/5,048.
>
> Everything else here still stands, including §3's two dead ends (blanket
> abstract cache-poisoning and a rootId cache) — do not revisit those.

## TL;DR

The pure-Python port of the stroke TIRP pipeline is **fidelity-complete**:

- **Full fixture: 5,043 / 5,048 rows exact** (`abstractions.csv`).
- **Phase-2 gate: 943/943 IDENTICAL.**
- **Phase-3 pilots: 3/3 IDENTICAL** (Visit_pattern, last_AST_prev_1_y, CHADS_Vasc_score).
- **KarmaLego (Phase 1): IDENTICAL** (20 patients × 30,722 patterns, byte-stable).
- `Using_Noac_AbsCI_QA_pattern` (484) and `Not_Using_NoacAbsCI_QA_pattern` (485): **IDENTICAL**.

Remaining genuine gap = **5 rows, patient 2470 only** (a KB corner, see below). Everything else is either exact or a known, scoped-out artifact.

**Recommendation: stop on fidelity, move to Phase 4** (wire `--engine python` into `run.py`, package the pasteable bundle, measure performance on `data_full\`).

---

## Git state (branch `master`)

| Commit | What |
|---|---|
| `d7341f1` | py_pipeline through Phase-3 pilot (KarmaLego + Mediator non-pattern + Pattern engine) |
| `dfcfc01` | ComplianceFunction (pattern `value-group-blobs` output) — 485 IDENTICAL |
| `8c0769c` | Context clipper window fix (inducers full / clippers windowed) — 5,043/5,048 |

Working tree is **clean** at `8c0769c`. Both parallel sessions' work is now merged and committed.

---

## What each phase established

### Phase 1 — KarmaLego ✅
`pyengine/karmalego.py` (~500 LOC, stdlib). IDENTICAL to the frozen reference. Fidelity facts that mattered: input = raw ∪ abstractions; concepts=`"*"`; symbol value = knowledge_table canonical allowed-value (case-insensitive relabel); no record dedup; maxGap=0 ⇒ only Contain/Overlaps; MVS denominator differs Karma vs Lego; pair-string join uses .NET culture-collation; non-adjacent relation must lie in the lossy 3-relation transitive closure.

### Phase 2 — Mediator non-pattern ✅
`pyengine/mediator/{tak_parse,datainstance,functions,persistence,state,trend,context,event,engine}.py`. 43 pattern-free concepts / 943 rows IDENTICAL. Run `python run_gate.py`.

### Phase 3 — Pattern engine + full closure ✅
`pyengine/mediator/pattern.py` (full `Pattern.Calculate` port). Pilot gate passed 3/3; bonus sweep 32/35 → now full fixture 5,043/5,048.

---

## Key technical conclusions (the hard-won ones)

### 1. ComplianceFunction is a pattern OUTPUT, not a concept type
KB 2700 has **zero** compliance-typed concepts. "Compliance" exists only as `value-group-blobs → compliance-function`, used by exactly **2** patterns (484/485). Both carry `<time-constraint-compliance trapezeA=0 B=0 C=8544 D=8544>` (a FuzzyFunction; no periodic-function).
- `tak_parse.py _parse_global_function` parses the nested trapeze params.
- `pattern.py _fuzzy` + `_calc_compliance_rate` = FuzzyFunction.cs + TimeConstraintFunction.CalcIntervalRate. Per interval, rate = max over its blobs of `fuzzy(|blob.start − interval.start|)` in **hours**; empty-blob interval → 0. With A=B=0/C=D=8544 the fuzzy is a binary 0/1 gate. Wired behind `is_compliance` (skips GroupBlobsByTimeGapsAndCardinality; doesn't early-return on empty blobs).
- `engine.py run()` wraps each concept-per-patient in try/except (faithful to Controller.cs:889-908) — a patient whose anchor has no data throws KeyNotFound in **both** .NET and Python and is absent from the reference.

### 2. The NOAC cluster fix = clipper/inducer WINDOW ASYMMETRY (NOT a cache model)
`_compute_context`: **inducers fetched over `_FULL`; clippers fetched with the caller's `window`** (Controller.cs:2683-2717 — MinValue..MaxValue to inducers :2691, startTime/endTime to clippers :2710).
- Why: clipper `Condition_Abs_NotNoac_state` (483) only yields its 2-minute Visit-shaped intervals **in-window**; without them `StartNoac_AbsCI_ctxt` (853)'s 1-year induced contexts never clip back to `…00:01`.
- Worth **+156 rows**; unblocked 484.

### 3. Two dead ends — do NOT revisit
- **Blanket abstract cache-poisoning** (extending `_raw_poisoned_by_cache` to state/trend/context/pattern on `_FULL` requests): **WRONG**. A/B: with it 137/141 & 4,878; without it (clipper fix instead) 139/141 & 5,034+. It also *masked* the clipper fix. Poisoning belongs to **raws/events only**. Do not re-add.
- **"rootId-aware cache port"**: a mis-diagnosis from one session. The clipper/inducer window asymmetry is the actual faithful fix. Ignore any note suggesting a rootId cache is needed.

### 4. Earlier prerequisite fixes (from prior Phase-3b work, already in the tree)
- **Cache poisoning for RAWS** (`_raw_poisoned_by_cache`): the .NET data layer's time-less cache serves the *windowed* list for a full-history raw fetch, **unless** the windowed fetch was empty (escape hatch → true full history). This is why age contexts (Live_ctxt/Ctxt_Female keep 1928/birth-120y starts) behave as the reference shows.
- **Events are window-filtered** (one line): the Event branch goes through GetGroupData(ev, start, end), same predicate as raws. This unblocked `NSAIDs_or_antiPLT_state`/149/HAS_BLED and the NOAC chain. **484 was never actually blocked on 483** — 483 stopped being empty once events were window-filtered.
- The **age chain IS exercised** over full history via context induction (patient 111 → 417 Age_in_Visit rows, age 82→94), correctly empty inside the window. The `minus` value-local, `equal` pairwise, and `sac="false"` paths are verified against real data.

### 5. Known artifacts (not bugs)
- **Phantom contexts** (~163 only-in-candidate rows, patients 3804/227/3933/3421/2939…): the engine legitimately produces contexts the reference SQL run omits. Handled by the **scoped** comparison (same mechanism as `candidate_gate43_scoped.csv`). Not a fidelity failure.
- **Per-row vs last-only We-clamp**: the .NET clamps only the **last element** of each result; the port clamps **every** output row to We. `NotNoac_AbsCI_ctxt` is the one place the fixture shows interior intervals running past We into 2024. Switching to last-only wins 4 rows but **costs 156** — so per-row clamp stays. This is the source of the remaining 5-row gap (patient 2470: `NotNoac_AbsCI_ctxt` ×4, `Context_Age_18_49` ×1).

---

## Coordination note (important)

Two sessions edited `engine.py` concurrently and clobbered each other. The blanket-poisoning edits repeatedly reverted the clipper fix. **Both are now resolved and committed at `8c0769c`.** If any older session is still open holding an uncommitted clipper edit, it should **discard local changes and sync to `8c0769c`** rather than re-apply. Going forward: **one session touches `engine.py` at a time.**

---

## Validation commands

```
cd py_pipeline
python run_gate.py     # Phase-2 gate: 943/943 IDENTICAL
python run_pilot.py    # Phase-3 pilots: 3/3 IDENTICAL
# Full fixture (all 141 concepts) — see run_pilot.py for the compare pattern;
# compares eng.run(all_names) against csv_mode/fixtures/abstractions.csv
```

Golden inputs: `C:\Users\noama1\Desktop\karma\csv_mode\fixtures\{mediator_raw_events.csv, abstractions.csv}`.
Comparator: `C:\Users\noama1\Desktop\karma\csv_mode\_prep\compare_abstractions.py` (order-insensitive).

---

## Phase 4 — what's left (the actual undelivered value)

The deliverable is a **pasteable, stdlib-only bundle** for the offline research room. Fidelity is done; the bundle is not yet wired or validated end-to-end.

1. **Wire `--engine python` into `csv_pipeline/run.py`** — replace the 2 subprocess calls (`run_mediator`→API.exe, `run_karmalego`→KarmaLegoConsoleApp.exe) with in-process calls to the Python engines (`engine.Engine.run(...)` and `karmalego.run_karmalego(...)`). Do **not** modify the .NET `csv_pipeline` logic otherwise — it stays the oracle.
2. **Package `py_pipeline/` as the self-contained bundle** — verify it runs with only stdlib, from `raw_events.csv` + bundled `tak_2700.json`, no internet/binaries.
3. **Measure performance on `data_full\`** — single-threaded Python vs multithreaded .NET is the open risk. Profile the Pattern engine (the quadratic-prone part).
4. **End-to-end validation** — run the full pipeline (cohort selection → Mediator → KarmaLego) via `--engine python` and diff against the .NET run.

**Do NOT chase the last 5 rows** (patient 2470) — it's the per-row-clamp tradeoff and costs 156 rows to win 4.
