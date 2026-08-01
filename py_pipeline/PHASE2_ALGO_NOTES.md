# Phase 2 — Mediator non-pattern algorithm spec (extracted from C# source)

Source root: `C:\MediatorCore\Mediator_CSV\`. All line refs are into that tree.
These notes are the porting spec for `pyengine/mediator/`. Fidelity bar: IDENTICAL
(order-insensitive multiset) on the 43 pattern-free concepts / 943 rows.

## DataInstance  (BusinessEntities\Data\DataInstance.cs)
Fields: `EntityId`(=PatientId, str), `ConceptName`(str), `StartTime`(datetime),
`EndTime`(datetime), `Value`(str always). `TimeSpan = End - Start` (computed).
Point/instant = `Start == End` (TimeSpan.TotalSeconds == 0).
`Clip(newEnd)`: sets End=newEnd iff `Start < newEnd < End` (strict); returns
false otherwise (used by clipper).

## Granularity -> TimeSpan  (Misc\Duration.cs:39-75) — FIXED, NOT calendar
second=v s, minute=v min, hour=v h, day=v d, week=v*7 d,
**month = v*30 days**, **year = v*365 days**. Inverse GetDuration divides by same.
Reproduce with fixed timedeltas — NOT relativedelta / calendar months.

## Pipeline order (Controller.cs ~2508-2589)
raw -> **Smoosh (local persistence)** on raw derived-from data (RawConcept only)
    -> partition by context + abstract (State/Trend/Context)
    -> **Interpolate/Concatenate (global persistence)** on abstracted output.

## LOCAL PERSISTENCE — Smoosh  (Controller.cs:3937-4044)
B=GoodBefore.TimeSpan, A=GoodAfter.TimeSpan (per derived RAW concept).
- Guard: if B.Value==0 and A.Value==0 -> unchanged.
- Single point t: End+=A, Start-=B; clip End to upperSmooshLimit.
- Many (sort by Start asc):
  leftSmooshedSeconds = data[0].dur + A + B (secs); if 0 -> 1.
  for i in 0..N-2, L=data[i], R=data[i+1]:
    L_end = L.End + A ; R_start = R.Start - B
    if L_end >= R_start:  # overlap after extension (>= : touching counts)
      if B.Value==0 and A.Value!=0:      # special case
        L_end = R_start; L_end = L_end.AddMinutes(-1)   # 1 MINUTE, hard-coded
      else:                              # PROPORTIONAL SPLIT
        rightSmooshedSeconds = R.dur + A + B; if 0 -> 1
        p = leftSmooshedSeconds / (leftSmooshedSeconds + rightSmooshedSeconds)
        leftSmooshedSeconds = rightSmooshedSeconds     # STATEFUL carry, only here
        L_end = L.End + (R.Start - L.End).seconds * p  # split the ORIGINAL gap
        R_start = L_end
    # else: no update; leftSmooshedSeconds stays stale (carry old value!)
    L.End = L_end ; R.Start = R_start
    if i==0:   data[0].Start -= B
    if i==N-2: data[N-1].End += A; clip to upperSmooshLimit
  NOTE stateful carry: leftSmooshedSeconds only reset inside proportional branch.
upperSmooshLimit = DateTime.Now OR config AbsoluteSmooshLimitDate OR Today+SmooshLimitInDays (Controller.cs:28-29,2552-2562).
Pre-clean: RemoveDuplicatesAndFixLateStartTime before Smoosh; Smoosh on deep copy.

## GLOBAL PERSISTENCE — Interpolate/Concatenate/Intersect
Interpolate (State.cs:297-328): only if Concatenable and >1. Group **consecutive
equal-Value** runs; flush each run through Concatenate.
Concatenate (AbstractConcept.cs:360-392): fixed-point; for adjacent (L,R) if
Intersect(L,R): L.End=R.End, drop R (merge -> [firstStart,lastEnd], gap absorbed).
Intersect (AbstractConcept.cs:400-412):
  gp = AllowedValues.GetPersistence(L.Value).GlobalPersistence   # per-VALUE table
  gap = GetDuration(R.Start - L.End, gp.Granularity)
  maxGap = gp.GetGlobalPersistance((int)GetDuration(L.TimeSpan,g), (int)GetDuration(R.TimeSpan,g))
  return gap <= maxGap
GetGlobalPersistance(leftDur,rightDur) (GlobalPersistence.cs:72-102):
  EMPTY table -> **int.MaxValue (always bridge)**.  [default when <rows/> empty]
  leftIndex=min(l,r), rightIndex=max(l,r).
  Behavior NegNeg: out-of-range -> return 0 (no bridge); else Rows[li][ri].
  Behavior PosPos (DEFAULT): clamp li,ri to last row/col; return Rows[li][ri].
Per-value persistence: Ordinal/Nominal/Boolean have per-value dict w/ fallback to
concept-level; Numeric/Time always concept-level.

## STATE  (see State + Controller explorer output — pending)
rank-selection-criteria="min": among matching mapping-function-2-value entries,
pick min order. Value = the mapping's `value` attr. Empty top mapping-function +
abstraction-at-contexts => compute per-context (nested state-at-context mapping).
Evaluation-tree: logical(and/or over operands), comparison(bigger-equal/smaller/
equal/... ), math, concept-id-allowed-values(=current value of that concept id),
double/integer literal. Comparison/Logical emit True/False. [confirm culture +
ERROR/UNDEF sentinel from State explorer]

## TREND  (Trend.cs:67-334) — self-contained, no Gradient/Rate reuse
Params: SignificantVariation(double), TimeSteady(Duration). Concatenable=true.
Emitted Value = enum member NAME via ToString(): **"Dec"/"Same"/"Inc"** (NOT the
XML's Decreasing/Stable/Increasing; NOT the customized "dec/same/inc"). Ordinal
Dec(1)<Same(2)<Inc(3). Allowed set overwritten to {Dec,Same,Inc} in ctor.
Steps:
 1. guard: exactly 1 derived-from, must be NumericRawConcept; empty/single point -> [].
 2. split each interval (dur>0) into TWO points: one at Start, one at End. points pass.
 3. sort by Start asc.
 4. cluster: greedy append while Intersect(prev,cur) (AbstractConcept Intersect,
    globalpersistence gap<=maxGap); else new cluster.
 5. local-extreme reduction (cluster>=3): drop interior point unless strict local
    max or min: keep iff (cur>prev and cur>next) or (cur<prev and cur<next).
 6. significant-segment scan per cluster, tracking running Min/Max + their points:
    - seed cand=first, Min=Max=firstVal, Value=Same; leading real interval emits Same.
    - for cur: flip-min if cur<Min (maybe flush Same [MinData.Start,MaxData.Start]);
      flip-max if cur>Max (symmetric); if cur is point and (Max-Min)<=SigVar: extend
      cand.End, continue (absorb, stays Same); else flush GetTrendCandidate, reseed.
    - Variation = Max - Min (ABSOLUTE range, not %, not slope). threshold <=SigVar.
    GetTrendCandidate: l,r=MinData,MaxData; if l==r -> Same[l.Start,r.End]; order by
      Start; diff=r.val-l.val; Dec if diff<0, Inc if diff>0, else Same;
      span = [l.End, r.Start] (inner boundary).
 7. engulf short Sames (time-steady, e.g. 71 days): drop leading Same while
    TimeSpan<=TimeSteady; merge consecutive Same+Same; engulf interior Same into
    left if TimeSpan<=TimeSteady.
 8. Interpolate merge equal adjacent (Concatenable): Same+Same merge iff combined
    Variation<=SigVar; Inc+Inc iff l.Max<=r.Min; Dec+Dec iff l.Max>=r.Min; span
    [l.Start,r.End]. Uses Intersect gap<=maxGap too.
Culture: Double.Parse uses CURRENT culture (hazard under comma locale). Data is
'.'-decimal so use invariant parse. No Dec/Same/Inc error sentinel (Rate has them).

## CONTEXT  (Context.cs:72-187)
For each inducer-entity: for each instance of inducer concept id in data, if
CheckSatisfaction(instance, constraints) -> emit DataInstance:
  Start = shift(instance, inducer.From), End = shift(instance, inducer.Until),
  Value = "True" (hard-coded true.ToString()).
shift(inst, bshift): base = inst.End if bshift.point==End else inst.Start;
  return base + duration_timespan(bshift.gap)  (SIGNED additive; can be negative).
Default From=(0s,Start), Until=(0s,End) -> context = inducer interval verbatim.
CheckSatisfaction (Context.cs:192-295): constraints ANDed (any fail -> false).
  TryGetValue parses to double only for RawNumeric (double.TryParse) / RawOrdinal
  (OrderedValues[value]); else NaN -> STRING path.
  NaN (string): Equals -> instance.Value.ToLower()==constraint.Value.ToLower();
                Different -> != . (case-insensitive)
  numeric: Bigger/Smaller/BiggerOrEqual/SmallerOrEqual/Equals(double.Equals)/Different.
ComparisonOperator: Bigger >, Smaller <, BiggerOrEqual >=, SmallerOrEqual <=,
  Equals =, Different <>.
Clippers (Context.cs:148-176): for each clipper-entity, for each clipper instance
satisfying ClipperValueConstraint: for each already-induced context that
Intersect()s it: context.Clip(shift(clipperInstance, clipper.From)); if Clip fails
(clipPoint<=cand.Start) break.
Intersect(clip,cand) (Context.cs:327-334): if clip.Start<cand.Start: clip.End>cand.Start
  else clip.Start<cand.End. (strict overlap; touching endpoints don't count).
Post (Context.cs:178-182): sort by Start asc; RemoveDuplicates (equal Start -> extend
  left.End=right.End, drop right); RemoveConflicts (Intersect and Concatenable ->
  union left.End=right.End, drop right). Context Concatenable=true.
filterDataByContext (Controller.cs:4101-4117): keep di iff EXISTS ctx with
  di.Start>=ctx.Start AND di.End<=ctx.End (FULL containment).

## EVENT  (Event.cs:94-128)
Pass-through: returns raw event instances unchanged (value + interval as-is);
attributes only validated to exist. Not needed for the 43-concept gate (events are
leaves), but this is the semantics.
