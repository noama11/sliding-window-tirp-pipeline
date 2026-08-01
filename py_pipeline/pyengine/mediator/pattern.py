"""Pattern abstraction — the Mediator PATTERN operator.

Faithful port of `BusinessEntities\\TAK\\Patterns\\Pattern.cs` (`Calculate`,
:105-478) plus the helper classes it drives (PatternDefinition,
PatternComponent, LocalConstraints, PairwiseConstraint(s), PeriodicConstraints,
CalendarFrequency, PatternOutput, StatisticalFunction, Interval).  The Controller
calls it at Controller.cs:2821-2893 (ByTime flow) with

    data      : {1-based component INDEX -> that component's derived data}
    knowledge : {component GesherID       -> the component's TAK concept}

— note the index/GesherID split; it is what makes the value-local-pattern's
`<concept-id-allowed-values id="N">` leaves resolve as component indices.

Pipeline
--------
  1. local constraints          value / duration / time filters, in place
  2. enough-components gate     + the "absent numeric == 0" zero-fallback
  3. relation dispatch          Any -> every instance is its own blob
                                All/KofN -> pairwise compliance-tree join
  4. blobs output               start/end anchoring + value-local-pattern
  5. periodic constraints       calendar-frequency intervals, blob grouping,
                                value-group-blobs aggregation
  6. final output               rename to the pattern's name (Pattern is NOT
                                concatenable: no Interpolate/Concatenate)

Determinism notes (Pattern.cs:2439, 1396, 2505)
-----------------------------------------------
KB 2700 declares `<priorities/>` empty on every component, so `InstancePriority`
is empty, which means (a) no per-component reordering and (b) instance REUSE IS
ALLOWED everywhere.  Both branches are still implemented.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from datainstance import (DataInstance, duration_to_timedelta,
                          gap_from_duration_dict)
from functions import calculate_pattern_value_local

_NAN = float("nan")

VALUE_UNDEFINED = "UNDEF"          # TAKentity.cs VALUE_UNDEFINED
VALUE_ERROR = "ERROR"              # TAKentity.cs VALUE_ERROR

# Pattern.cs:36-37 — the bounds CalculateCountMissingFunction pads to.
_PATTERN_START = datetime(1900, 1, 1)
_PATTERN_END = datetime(2100, 1, 1)

# PairwiseConstraint ctor defaults (:70-73) and PeriodicConstraints ctor
# (:84-88).  Duration.MaxValue == 1000 (Duration.cs:77).
_DEFAULT_MIN_DURATION = timedelta(0)
_DEFAULT_MAX_DURATION = timedelta(days=1000 * 365)
# Interval ctor (:3624) — deliberately capped to the same 1000 years.
_INTERVAL_MAX_GAP = timedelta(days=365000)

_DT_MIN = datetime.min
_DT_MAX = datetime.max


# ---------------------------------------------------------------------------
# value constraints  (Pattern.cs:3262-3403)
# ---------------------------------------------------------------------------

# TryGetValue (:3384-3403) parses to double for exactly THREE TAK types.
# Everything else (State / Trend / Context / Event / RawNominal) stays NaN and
# therefore takes the string path.
_NUMERIC_TAK_TYPES = ("raw-numeric", "pattern")


def _try_get_value(value, concept):
    """TryGetValue: NaN unless the concept is RawNumeric / RawOrdinal / Pattern."""
    if concept is None:
        return _NAN
    op = concept.get("op_type")
    if op in _NUMERIC_TAK_TYPES:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0          # Double.TryParse leaves 0 on failure (out param)
    # KB 2700 has no ordinal RAW concepts; an OrdinalRawConcept would rank here.
    return _NAN


def _satisfiable_by_value_constraints(instance, concept, constraints):
    """SatisfiableByValueConstraints (:3262-3376).

    IMPORTANT: in the NaN (string) branch only Equals/Different are handled --
    every other operator leaves `constraintResult` at its initialised **True**,
    i.e. the constraint PASSES.  That is what lets `bigger-equal "0"` on the
    CHA2DS2-VASc point states (which are States, hence NaN) admit every
    instance.
    """
    inst_d = _try_get_value(instance.value, concept)
    for c in constraints or []:
        op = c.get("operator")
        cval = c.get("value")
        result = True
        if math.isnan(inst_d):
            iv = str(instance.value).lower()
            cv = str(cval).lower()
            if op == "equal":
                result = (iv == cv)
            elif op in ("different", "not-equal"):
                result = (iv != cv)
            # any other operator -> stays True (faithful to the C# switch)
        else:
            const_d = _try_get_value(cval, concept)
            if op == "bigger":
                result = inst_d > const_d
            elif op == "smaller":
                result = inst_d < const_d
            elif op in ("bigger-equal", "bigger-or-equal"):
                result = inst_d >= const_d
            elif op in ("smaller-equal", "smaller-or-equal"):
                result = inst_d <= const_d
            elif op == "equal":
                result = inst_d == const_d
            elif op in ("different", "not-equal"):
                result = inst_d != const_d
            else:
                result = False          # "More operators - not implemented."
        if not result:
            return False
    return True


def _point_datetime(pd, default):
    """A TimeConstraint <start>/<end> PointDefinition -> datetime."""
    if not pd:
        return default
    ref = pd.get("reference_point")
    if not ref:
        base = default
    else:
        base = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
                    "%d/%m/%Y", "%m/%d/%Y"):
            try:
                base = datetime.strptime(ref.strip(), fmt)
                break
            except ValueError:
                continue
        if base is None:
            base = default
    shift = gap_from_duration_dict(pd.get("time_shift"))
    try:
        return base + shift
    except OverflowError:
        return base


def _filter_component_data(comp, concept, instances):
    """The three in-place local filters (:159-182 -> :3128-3218).

    Returns the list of dropped instances (`badValues`), which is only ever
    populated when the component sets `save-bad-values` (never in KB 2700).
    """
    bad = []
    save_bad = bool(comp.get("save_bad_values"))

    # 1. value constraints
    kept = []
    for di in instances:
        if _satisfiable_by_value_constraints(di, concept, comp.get("value_constraints")):
            kept.append(di)
        elif save_bad:
            bad.append(di)
    instances[:] = kept

    # 2. duration constraints — SKIPPED when the element is absent, because the
    #    .NET XML ctor leaves DurationConstraint null (LocalConstraints.cs:105).
    dc = comp.get("duration_constraint")
    if dc is not None:
        lo = gap_from_duration_dict(dc.get("min_duration")) or timedelta(0)
        hi = (gap_from_duration_dict(dc.get("max_duration"))
              if dc.get("max_duration") else _DEFAULT_MAX_DURATION)
        kept = []
        for di in instances:
            if lo <= di.timespan <= hi:
                kept.append(di)
            elif save_bad:
                bad.append(di)
        instances[:] = kept

    # 3. time constraints — always present (default MinValue..MaxValue)
    tc = comp.get("time_constraint")
    if tc is not None:
        start = _point_datetime(tc.get("start"), _DT_MIN)
        end = _point_datetime(tc.get("end"), _DT_MAX)
        kept = []
        for di in instances:
            if di.start >= start and di.end <= end:
                kept.append(di)
            elif save_bad:
                bad.append(di)
        instances[:] = kept

    return bad


# ---------------------------------------------------------------------------
# priorities  (SortDataInstancesByPriorities, :1396-1491)
# ---------------------------------------------------------------------------

def _is_reuse_allowed(comp):
    """IsReuseAllowed (:2439): reuse iff the component declares NO priority."""
    return not (comp.get("priorities") or [])


def _sort_by_priorities(instances, comp):
    prios = comp.get("priorities") or []
    if not prios:
        return list(instances)          # untouched (:1404-1408)
    out = list(instances)
    # chained OrderBy/ThenBy == stable sorts applied last-key-first
    for p in reversed(prios):
        rev = (str(p.get("min_max")).lower() == "max")
        if str(p.get("priority")).lower() in ("by-value", "byvalue"):
            def key(di):
                try:
                    return int(round(float(di.value)))   # Convert.ToInt64
                except (TypeError, ValueError):
                    return 0
        else:
            def key(di):
                return di.start
        out.sort(key=key, reverse=rev)
    return out


# ---------------------------------------------------------------------------
# pairwise constraints  (:2235-2414)
# ---------------------------------------------------------------------------

_FLIP = {"bigger": "smaller", "smaller": "bigger",
         "bigger-equal": "smaller-equal", "smaller-equal": "bigger-equal",
         "bigger-or-equal": "smaller-or-equal",
         "smaller-or-equal": "bigger-or-equal"}


def _flip(op):
    """FlipOperator (:2449) — Equals/Different are unchanged."""
    return _FLIP.get(op, op)


def _boundary(di, point):
    return di.end if point == "end" else di.start


def _time_predicate(op, a, b):
    if op == "bigger":
        return a > b
    if op in ("bigger-equal", "bigger-or-equal"):
        return a >= b
    if op in ("different", "not-equal"):
        return a != b
    if op == "equal":
        return a == b
    if op == "smaller":
        return a < b
    if op in ("smaller-equal", "smaller-or-equal"):
        return a <= b
    return True                    # unhandled operator leaves shouldAdd True


def _pwc_durations(pwc):
    lo = (gap_from_duration_dict(pwc.get("min_duration"))
          if pwc.get("min_duration") else _DEFAULT_MIN_DURATION)
    hi = (gap_from_duration_dict(pwc.get("max_duration"))
          if pwc.get("max_duration") else _DEFAULT_MAX_DURATION)
    return lo, hi


def _sac_enabled(pwc):
    """PairwiseConstraint.ReadXml (:126-136): sac defaults to TRUE."""
    v = pwc.get("sac")
    if v is None:
        return True
    return str(v).lower() == "true"


def _get_all_matches(alias1, alias2, di1, dis2, pwc, ctx):
    """GetAllMatchesToDataInstance (:2235-2368)."""
    time_opr = pwc.get("operator")
    value_opr = pwc.get("value_operator")

    # evaluate as if di1 were component-i (:2242-2246)
    if alias1 != pwc.get("i"):
        time_opr = _flip(time_opr) if time_opr else time_opr
        value_opr = _flip(value_opr) if value_opr else value_opr

    dt_i = _boundary(di1, pwc.get("boundary_i") or "start")
    lo, hi = _pwc_durations(pwc)

    result = []
    for di in dis2:
        should_add = True
        if time_opr:
            dt_j = _boundary(di, pwc.get("boundary_j") or "start")
            duration = abs(dt_i - dt_j)         # TimeSpan.Duration() (:2279)
            should_add = _time_predicate(time_opr, dt_i, dt_j)
            should_add = should_add and lo <= duration <= hi
        if should_add and value_opr:
            # a synthetic value-constraint on di1 against di's value (:2315)
            concept = ctx["knowledge"].get(ctx["components"][alias1]["id"])
            should_add = _satisfiable_by_value_constraints(
                di1, concept, [{"operator": value_opr, "value": di.value}])
        if should_add:
            result.append(di)

    if not _sac_enabled(pwc) or not result or not time_opr:
        return result

    # ---- SAC: keep only the closest match with nothing in between (:2328-2367)
    out = []
    if time_opr == "bigger":
        if _check_sac(result[-1], alias1, di1, alias2, ctx):
            out.append(result[-1])
    elif time_opr == "smaller":
        if _check_sac(di1, alias1, result[0], alias2, ctx):
            out.append(result[0])
    elif time_opr in ("different", "not-equal"):
        after = [d for d in result if d.start > di1.start]
        before = [d for d in result if d.start < di1.start]
        if not after or not before:
            return []                          # .First()/.Last() would throw
        smallest, largest = after[0], before[-1]
        if (_check_sac(di1, alias1, smallest, alias2, ctx)
                and _check_sac(di1, alias1, largest, alias2, ctx)):
            out.append(smallest)
            out.append(largest)
    else:
        # BiggerOrEqual / SmallerOrEqual / Equals have NO case in the switch,
        # so recordsWithSatisfiedSAC stays empty and the match set is dropped.
        return []
    return out


def _check_sac(earlier, alias1, later, alias2, ctx):
    """CheckForSACConstraint (:2380-2414).

    Only `savedBadValues` is consulted (the "other records" block is commented
    out), and `savedBadValues` is only ever fed by a component with
    save-bad-values — plus Pattern.cs:180 guards the call with the INVERTED
    condition `if (!badValues.Any())`, so in practice the dictionary only ever
    holds empty lists and this always returns True.
    """
    saved = ctx["saved_bad_values"]
    lo, hi = earlier.start, later.start
    for alias in (alias1, alias2):
        for di in saved.get(alias) or []:
            if lo <= di.start <= hi:
                return False
    return True


def _get_relevant_pwcs(alias, pairwise, components_order):
    """GetRelevantPWCs (:2505) — pwcs touching `alias`, ordered by the OTHER
    component's index in the definition."""
    idx = {a: i for i, a in enumerate(components_order)}
    rel = [p for p in pairwise
           if p.get("i") == alias or p.get("j") == alias]
    rel.sort(key=lambda p: idx.get(p.get("j") if p.get("i") == alias
                                   else p.get("i"), -1))
    return rel


# ---------------------------------------------------------------------------
# compliance trees  (:1505-1832)
# ---------------------------------------------------------------------------

class _ComponentNode:
    __slots__ = ("alias", "reuse", "children")

    def __init__(self, alias, reuse):
        self.alias = alias
        self.reuse = reuse
        self.children = []          # list of _DataInstanceNode


class _DataInstanceNode:
    __slots__ = ("di", "children")

    def __init__(self, di):
        self.di = di
        self.children = []          # list of _ComponentNode


def _next_di(data_records, pcc_data, alias, comp):
    """GetNextDataInstanceByOrderAndRemoveFromDataRecords (:1816-1832)."""
    if not data_records:
        return None
    di = data_records.pop(0)
    if not _is_reuse_allowed(comp):
        lst = pcc_data.get(alias)
        if lst is not None and any(x is di for x in lst):
            pcc_data[alias] = [x for x in lst if x is not di]
            if not pcc_data[alias]:
                del pcc_data[alias]
    return di


def _compliance_tree(alias, pairwise, ctx, data_records, parent_reuse_allowed):
    """GetComplianceTreeForComponent (:1541-1641)."""
    comp = ctx["undealt"][alias]
    reuse_allowed = _is_reuse_allowed(comp)
    root = _ComponentNode(alias, reuse_allowed)
    relevant = _get_relevant_pwcs(alias, pairwise, ctx["components_order"])

    di = _next_di(data_records, ctx["data"], alias, comp)

    if not relevant:
        while di is not None:
            root.children.append(_DataInstanceNode(di))
            if not parent_reuse_allowed:
                break
            di = _next_di(data_records, ctx["data"], alias, comp)
        return root if root.children else None

    dealt = set()
    while di is not None:
        for pwc in relevant:
            other = pwc.get("j") if pwc.get("i") == alias else pwc.get("i")
            if other not in ctx["undealt"]:
                continue
            from_parent = other in ctx["parents"]
            if from_parent:
                matches = [ctx["parents"][other]]
            else:
                other_data = list(ctx["data"].get(other) or [])
                matches = _get_all_matches(alias, other, di, other_data, pwc, ctx)
                dealt.add(other)

            if not matches:
                break                       # AND: one failed pwc kills this di

            if from_parent:
                root.children.append(_DataInstanceNode(di))
                continue

            remaining = [p for p in pairwise if p is not pwc]
            ctx["parents"][alias] = di
            child = _compliance_tree(other, remaining, ctx, matches,
                                     parent_reuse_allowed and reuse_allowed)
            if child is None:
                ctx["parents"].pop(alias, None)
                break

            node = _DataInstanceNode(di)
            node.children.append(child)
            root.children.append(node)

        di = _next_di(data_records, ctx["data"], alias, comp)
        ctx["parents"].pop(alias, None)
        if root.children and not parent_reuse_allowed:
            break

    dealt.add(alias)
    for a in dealt:
        ctx["undealt"].pop(a, None)

    return root if root.children else None


def _tree_routes(node):
    """GetTreeRoutes (:1670-1701) -> list of (blob, reuse) with blob a list of
    (DataInstance, alias) pairs."""
    result = []
    for di_node in node.children:
        groups = [_tree_routes(c) for c in di_node.children]
        combined = _combine_routes(groups)
        if not combined:
            combined = [([], True)]
        out = []
        for blob, reuse in combined:
            blob = list(blob)
            blob.append((di_node.di, node.alias))
            out.append((blob, reuse and node.reuse))
        result.extend(out)
    return result


def _combine_routes(groups):
    """CombineRoutes (:1708-1784)."""
    if len(groups) == 1:
        return groups[0]
    if not groups:
        return []

    all_combinations = all(reuse for g in groups for (_blob, reuse) in g)

    if not all_combinations:
        # "zip": consume one route per non-reusable group each round
        groups = [list(g) for g in groups]
        combined = []
        no_empty = True
        while no_empty:
            blob = []
            reuse = True
            for g in groups:
                if not g:
                    no_empty = False
                    break
                blob.extend(g[0][0])
                if not g[0][1]:
                    reuse = False
                    g.pop(0)
                    no_empty = no_empty and bool(g)
            combined.append((blob, reuse))
        return combined

    acc = []
    for g in groups:
        if not acc:
            acc = list(g)
            continue
        nxt = []
        for a_blob, _a_reuse in acc:
            for b_blob, _b_reuse in g:
                merged = list(a_blob)
                seen = set(id(x[0]) for x in merged)
                for item in b_blob:            # Enumerable.Union == distinct
                    if id(item[0]) not in seen:
                        merged.append(item)
                        seen.add(id(item[0]))
                nxt.append((merged, True))
        acc = nxt
    return acc


def _blobs_and(spec, ctx):
    """LogicalOperator.And branch (:340-346)."""
    trees = []
    for comp in spec["components"]:
        alias = comp["alias"]
        if alias in ctx["undealt"] and alias in ctx["data"]:
            tree = _compliance_tree(alias, spec["pairwise"], ctx,
                                    list(ctx["data"][alias]), True)
            if tree is not None:
                trees.append(tree)
            elif (len(spec["pairwise"]) > 1
                  and (spec.get("pairwise_operator") or "and") == "and"):
                return []                       # ayelet 10/14/2019 (:1520-1523)
    routes = [_tree_routes(t) for t in trees]
    return [blob for blob, _reuse in _combine_routes(routes)]


def _blobs_or(spec, ctx):
    """GetBlobsWithAtLeastOnePairwiseConstraint (:1869-2065), the OR / KofN
    assembly.  KB 2700 never sets `logical-operator="or"`, so this is a
    structural stand-in: each instance is completed to a full-size blob with
    its pairwise matches plus fillers."""
    relation = (spec.get("relation") or "all").lower()
    if relation == "kofn":
        target = int(spec.get("kofn") or 0)
    else:
        target = len(spec["components"])
    if len(ctx["data"]) < target:
        return []

    blobs = []
    for comp in spec["components"]:
        alias = comp["alias"]
        for di in list(ctx["data"].get(alias) or []):
            blob = [(di, alias)]
            for pwc in _get_relevant_pwcs(alias, spec["pairwise"],
                                          ctx["components_order"]):
                other = pwc.get("j") if pwc.get("i") == alias else pwc.get("i")
                matches = _get_all_matches(alias, other, di,
                                           list(ctx["data"].get(other) or []),
                                           pwc, ctx)
                if matches:
                    blob.append((matches[0], other))
            for other_comp in spec["components"]:
                oa = other_comp["alias"]
                if oa == alias or any(b[1] == oa for b in blob):
                    continue
                pool = ctx["data"].get(oa) or []
                if pool:
                    blob.append((pool[0], oa))
            if len(blob) >= target:
                blobs.append(blob)

    # Distinct(ListDataInstanceComparer) — SequenceEqual by reference (:351)
    seen = set()
    out = []
    for blob in blobs:
        key = tuple(id(di) for di, _a in blob)
        if key not in seen:
            seen.add(key)
            out.append(blob)
    return out


# ---------------------------------------------------------------------------
# blob output  (:1317-1386, 2132-2223)
# ---------------------------------------------------------------------------

def _initialize_unset_boundaries(spec, data_by_index, output):
    """InitializeUnsetBoundaryLocalPattern (:1317-1362) +
    InitializeUnsetAliasInBoundaryLocalPattern (:1369-1386).

    Reproduces the id/index confusion verbatim: min/maxCompId are taken from
    the *index* keys of `data`, then matched against component **GesherIDs** to
    find an alias — which normally fails, leaving the alias empty so that the
    second pass fills it from `mappingIndexToAlias[index]`.
    """
    start = output.get("start")
    end = output.get("end")
    if (start and end) or not data_by_index:
        pass
    else:
        min_comp, max_comp = 1, 1
        min_start, max_start = _DT_MAX, _DT_MIN
        for key in sorted(data_by_index, key=int):
            lst = data_by_index[key]
            if not lst:
                continue
            st = lst[0].start
            if st >= max_start:
                max_comp, max_start = int(key), st
            if st <= min_start:
                min_comp, min_start = int(key), st
        min_alias = max_alias = ""
        for comp in spec["components"]:
            if str(comp["id"]) == str(min_comp):
                min_alias = comp["alias"]
            if str(comp["id"]) == str(max_comp):
                max_alias = comp["alias"]
        if start is None:
            start = {"alias": min_alias, "component_id": str(min_comp),
                     "boundary_point": "start", "time_shift": None}
        if end is None:
            end = {"alias": max_alias, "component_id": str(max_comp),
                   "boundary_point": "end", "time_shift": None}

    index_to_alias = {str(i): c["alias"]
                      for i, c in enumerate(spec["components"], 1)}
    for blk in (start, end):
        if blk is not None and not blk.get("alias"):
            blk = blk
            cid = str(blk.get("component_id"))
            if cid not in index_to_alias:
                raise ValueError("Mismatch in Component Id")
            blk["alias"] = index_to_alias[cid]
    return start, end


def _time_shift(blk):
    if not blk:
        return timedelta(0)
    ts = blk.get("time_shift")
    if not ts:
        return timedelta(0)
    return duration_to_timedelta(ts.get("value"), ts.get("granularity"))


def _calculate_blobs_output(blobs, spec, start_blk, end_blk, alias_to_index, name):
    """CalculateBlobsOutput (:2132-2223)."""
    if not blobs:
        return []
    entity_id = blobs[0][0][0].entity_id
    out = []
    for blob in blobs:
        iba = {alias: di for di, alias in blob}

        sp = (start_blk or {}).get("boundary_point") or "start"
        # :2178 quirk — a component literally aliased "component_1" wins.
        anchor = iba.get("component_1")
        if sp == "start":
            src = anchor if anchor is not None else iba[start_blk["alias"]]
            start_time = src.start + _time_shift(start_blk)
        else:
            src = anchor if anchor is not None else iba[end_blk["alias"]]
            start_time = src.end + _time_shift(start_blk)

        ep = (end_blk or {}).get("boundary_point") or "end"
        tgt = iba[end_blk["alias"]]
        end_time = (tgt.start if ep == "start" else tgt.end) + _time_shift(end_blk)

        vlp = spec["output"].get("value_local")
        if vlp is None:
            value = "True"
        else:
            by_index = {str(alias_to_index[a]): di for a, di in iba.items()}
            value = calculate_pattern_value_local(vlp, by_index)
            if value is None:
                value = VALUE_UNDEFINED

        out.append(DataInstance(entity_id, name, start_time, end_time, value))
    return out


# ---------------------------------------------------------------------------
# periodic constraints  (:2528-2738)
# ---------------------------------------------------------------------------

class _Interval:
    __slots__ = ("start", "end", "blobs", "min_gap", "max_gap")

    def __init__(self, start, end):
        self.start = start
        self.end = end
        self.blobs = []
        self.min_gap = timedelta(0)
        self.max_gap = _INTERVAL_MAX_GAP

    def add_blob(self, blob):
        """Interval.AddBlob (:3636-3653) — reproduced including the `> MinGap`
        typo that governs MaxGapWithin."""
        if self.blobs:
            temp = blob.start - self.blobs[-1].end
            if temp.total_seconds() < 0:
                temp = self.blobs[-1].end - blob.start
            if temp < self.min_gap:
                self.min_gap = temp
            if temp > self.min_gap:
                self.max_gap = temp
        self.blobs.append(blob)


def _add_calendar_months(dt, months):
    """DateTime.AddMonths — calendar-aware, clamping the day."""
    total = dt.month - 1 + months
    year = dt.year + total // 12
    month = total % 12 + 1
    day = min(dt.day, [31, 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0))
                       else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return dt.replace(year=year, month=month, day=day)


def _add_calendar_years(dt, years):
    return _add_calendar_months(dt, 12 * years)


def _split_timeline_by_calendar_frequency(first, last, cf, is_relative):
    """SplitTimelineByCalendarFrequency (:2622-2738).

    `earliestTC` is DateTime.MinValue for every KB-2700 component (the default
    TimeConstraint), so `startTime` is always `first.StartTime`.
    """
    gran = (cf.get("frequency_granularity") or "").lower()
    try:
        fv = int(cf.get("frequency_value"))
    except (TypeError, ValueError):
        fv = 0
    start_time = first.start

    diff = timedelta(0)
    if gran == "second":
        interval_start = start_time if is_relative else start_time.replace(microsecond=0)
        diff = timedelta(seconds=fv)
        interval_end = interval_start + diff
    elif gran == "minute":
        interval_start = (start_time if is_relative
                          else start_time.replace(second=0, microsecond=0))
        diff = timedelta(minutes=fv)
        interval_end = interval_start + diff
    elif gran == "hour":
        interval_start = (start_time if is_relative
                          else start_time.replace(minute=0, second=0, microsecond=0))
        diff = timedelta(hours=fv)
        interval_end = interval_start + diff
    elif gran == "day":
        interval_start = (start_time if is_relative
                          else start_time.replace(hour=0, minute=0, second=0, microsecond=0))
        diff = timedelta(days=fv)
        interval_end = interval_start + diff
    elif gran == "week":
        interval_start = (start_time if is_relative
                          else start_time.replace(hour=0, minute=0, second=0, microsecond=0))
        if not is_relative:
            # DayOfWeek.Sunday(0) - startTime.DayOfWeek ; python Monday==0
            dow = (start_time.weekday() + 1) % 7
            interval_start = interval_start + timedelta(days=-dow)
        diff = timedelta(days=fv * 7)
        interval_end = interval_start + diff
    elif gran == "month":
        interval_start = (start_time if is_relative
                          else start_time.replace(day=1, hour=0, minute=0,
                                                  second=0, microsecond=0))
        interval_end = _add_calendar_months(interval_start, fv)
    elif gran == "year":
        interval_start = (start_time if is_relative
                          else start_time.replace(month=1, day=1, hour=0, minute=0,
                                                  second=0, microsecond=0))
        interval_end = _add_calendar_years(interval_start, fv)
    else:
        interval_start = start_time
        interval_end = last.end

    intervals = []
    guard = 0
    while interval_end < last.end:
        intervals.append(_Interval(interval_start, interval_end))
        interval_start = interval_end
        if gran == "month":
            interval_end = _add_calendar_months(interval_start, fv)
        elif gran == "year":
            interval_end = _add_calendar_years(interval_start, fv)
        else:
            interval_end = interval_start + diff
        guard += 1
        if guard > 5_000_000 or diff == timedelta(0) and gran not in ("month", "year"):
            break
    intervals.append(_Interval(interval_start, last.end))

    if cf.get("last_complete"):
        tail = intervals[-1]
        if gran == "month":
            should_remove = tail.end > _add_calendar_months(tail.start, fv)
        elif gran == "year":
            should_remove = tail.end > _add_calendar_years(tail.start, fv)
        else:
            should_remove = tail.end > tail.start + diff
        if should_remove:
            intervals.pop()

    return intervals


def _fill_intervals_with_blobs(intervals, blobs):
    """FillIntervalsWithBlobs (:2591-2613) — a single forward two-pointer walk,
    so each blob lands in at most one interval."""
    i = j = 0
    while i < len(intervals) and j < len(blobs):
        if blobs[j].start >= intervals[i].end:
            i += 1
            continue
        if blobs[j].end > intervals[i].end or blobs[j].end < intervals[i].start:
            j += 1
            continue
        intervals[i].add_blob(blobs[j])
        j += 1


def _group_blobs_by_gaps_and_cardinality(intervals, pc):
    """GroupBlobsByTimeGapsAndCardinality (:2528-2584)."""
    try:
        card_min = int(pc.get("cardinality_min"))
    except (TypeError, ValueError):
        card_min = 0
    try:
        card_max = int(pc.get("cardinality_max"))
    except (TypeError, ValueError):
        card_max = 2 ** 31 - 1
    min_within = gap_from_duration_dict(pc.get("min_gap_within"))
    max_within = (gap_from_duration_dict(pc.get("max_gap_within"))
                  if pc.get("max_gap_within") else _DEFAULT_MAX_DURATION)
    min_between = gap_from_duration_dict(pc.get("min_gap_between"))
    max_between = (gap_from_duration_dict(pc.get("max_gap_between"))
                   if pc.get("max_gap_between") else _DEFAULT_MAX_DURATION)

    # The C# walks `items` with an index and `del`etes rejected entries in
    # place, keeping `j` on the last SURVIVING entry.  Appending survivors to a
    # fresh list is the same computation without the O(n^2): `kept[-1]` is by
    # construction what `items[j]` was, and the `j > -1` guard is `kept` being
    # non-empty.  This matters -- Hyper_pattern splits a full patient history
    # into ~15,000 two-day intervals and rejects almost all of them, so the
    # in-place deletion alone was two thirds of the whole engine's runtime.
    # (The IndexError an empty `kept[-1].blobs` would raise is preserved: the
    # C# `items[j].blobs.Last()` throws in exactly the same case.)
    kept = []
    for iv in intervals:
        if len(iv.blobs) < card_min or len(iv.blobs) > card_max:
            continue
        if iv.min_gap < min_within or iv.max_gap > max_within:
            continue
        if kept and iv.blobs:
            gap = iv.blobs[0].start - kept[-1].blobs[-1].end
            if gap < min_between or gap > max_between:
                continue
        kept.append(iv)
    return kept


# ---------------------------------------------------------------------------
# group-of-blobs output  (:1243-1306) + StatisticalFunction.cs:51-116
# ---------------------------------------------------------------------------

def _statistical(op, blobs):
    """StatisticalFunction.Calculate — the emitted interval is data.Last()'s
    (`exampleInstance = data.Last()`, :112)."""
    values = []
    if op != "count":
        for di in blobs:
            try:
                values.append(float(di.value))
            except (TypeError, ValueError):
                return DataInstance(di.entity_id, di.concept_name,
                                    blobs[0].start, blobs[-1].end, VALUE_ERROR)
    if op == "average":
        answer = sum(values) / len(values)
    elif op == "min":
        answer = min(values)
    elif op == "max":
        answer = max(values)
    elif op == "stdev":
        M = S = 0.0
        k = 1
        for v in values:
            tmp = M
            M += (v - tmp) / k
            S += (v - tmp) * (v - M)
            k += 1
        answer = math.sqrt(S / (k - 2)) if k > 2 else 0.0
    elif op == "count":
        answer = float(len(blobs))
    else:                                   # sum
        answer = float(sum(values))
    example = blobs[-1]
    txt = str(int(answer)) if float(answer).is_integer() else repr(float(answer))
    return DataInstance(example.entity_id, example.concept_name,
                        example.start, blobs[-1].end, txt)


def _calculate_group_of_blobs_output(intervals, spec):
    """CalculateGroupOfBlobsOutput (:1243-1306)."""
    vgb = spec["output"].get("value_group_blobs")
    result = []
    if vgb is not None:
        kind = vgb.get("kind")
        for iv in intervals:
            if not iv.blobs:
                continue                    # .First()/.Last() would throw
            if kind == "count-function":
                result.append(_statistical("count", iv.blobs))
            else:
                result.append(_statistical(
                    (vgb.get("statistical_operator") or "sum").lower(), iv.blobs))
        return result

    mode = (spec["output"].get("start_end_group_blobs") or "by-granularity").lower()
    for iv in intervals:
        if not iv.blobs:
            continue
        example = iv.blobs[0]
        if mode == "first":
            result.append(iv.blobs[0])
        elif mode == "last":
            result.append(iv.blobs[-1])
        elif mode == "by-granularity":
            result.append(DataInstance(example.entity_id, example.concept_name,
                                       iv.start, iv.end, "True"))
        else:                               # first-to-last
            result.append(DataInstance(example.entity_id, example.concept_name,
                                       example.start, iv.blobs[-1].end, "True"))
    return result


def _fuzzy_linear(x1, y1, x2, y2, point):
    """FuzzyFunction.CalculateLinearFunction (FuzzyFunction.cs:76-83)."""
    m = (y2 - y1) / (x2 - x1)
    b = y1 - m * x1
    return m * point + b


def _fuzzy(a, b, c, d, point):
    """FuzzyFunction.CalculateFuzzyFunction (:37-73) — trapezoid a<=b<=c<=d.
    With a==b==0, c==d==8544 (the only compliance shape in KB 2700) this is a
    binary gate: 1 for 0<=point<8544 hours, 0 otherwise."""
    if point >= d:
        return 0.0
    if point >= c:
        return _fuzzy_linear(c, 1.0, d, 0.0, point)
    if point >= b:
        return 1.0
    if point <= a:
        return 0.0
    if a == 0 and b == 0:                       # no A point -> always 1
        return 1.0
    return _fuzzy_linear(a, 0.0, b, 1.0, point)


def _calc_compliance_rate(intervals, vgb, name, patient_id):
    """ComplianceFunction.Calculate -> TimeConstraintFunction.CalcIntervalRate
    (ComplianceFunction.cs:45-64, TimeConstraintFunction.cs:72-101).

    For each interval, the rate is the max fuzzy score over its blobs of the
    absolute hour-distance between the blob start and the interval start; an
    interval with no blobs stays at rate 0.  KB 2700 carries only the
    time-constraint branch (no periodic-function)."""
    tc = vgb.get("time_constraint")
    if tc is None:
        return []
    a, b, c, d = tc["a"], tc["b"], tc["c"], tc["d"]
    result = []
    for iv in intervals:
        max_rate = 0.0
        for point in iv.blobs:
            distance = abs((point.start - iv.start).total_seconds()) / 3600.0
            rate = _fuzzy(a, b, c, d, distance)
            if max_rate < rate:
                max_rate = rate
        txt = (str(int(max_rate)) if float(max_rate).is_integer()
               else repr(float(max_rate)))
        result.append(DataInstance(patient_id, name, iv.start, iv.end, txt))
    return result


def _calculate_count_missing(blobs):
    """CalculateCountMissingFunction (:1148-1164) — zero-pad the gaps."""
    result = []
    start = _PATTERN_START
    for blob in blobs:
        result.append(DataInstance(blob.entity_id, blob.concept_name,
                                   start, blob.start, "0"))
        result.append(blob)
        start = blob.end
    last = blobs[-1]
    result.append(DataInstance(last.entity_id, last.concept_name,
                               last.end, _PATTERN_END, "0"))
    return result


# ---------------------------------------------------------------------------
# public entry — Pattern.Calculate (:105-478)
# ---------------------------------------------------------------------------

def calculate(entity, name, spec, data, knowledge):
    """Compute a Pattern's output intervals for one entity.

    `spec`      the parsed `pattern` spec dict (tak_parse `_parse_pattern`)
    `data`      {component index as str -> list[DataInstance]} — the engine
                must hand over COPIES, the local filters mutate them in place
    `knowledge` {component GesherID (str) -> parsed concept dict}
    """
    components = spec["components"]
    if not components:
        return []

    relation = (spec.get("relation") or "all").lower()
    output = spec["output"]
    alias_to_index = {c["alias"]: i for i, c in enumerate(components, 1)}

    patient_id = ""
    for key in sorted(data, key=int):
        if data[key]:
            patient_id = data[key][0].entity_id
            break
    if not patient_id:
        patient_id = entity

    # NOTE: the .NET Pattern object is shared across patients and these two
    # helpers MUTATE PatternOutput, so the boundary defaults are frozen on the
    # first patient that has data.  We recompute per patient, which differs only
    # for multi-component patterns that declare NEITHER boundary — in KB 2700
    # those are exactly the ones whose components are all anchored identically.
    start_blk, end_blk = _initialize_unset_boundaries(spec, data, output)

    # ---- 1. local constraints -------------------------------------------
    # `dataByAlias` shares its List objects with `data` in the C# (ToDictionary
    # copies the dictionary, not the lists), so the in-place filters below are
    # visible through BOTH views.  `filtered_by_index` is that second view; it
    # keeps every component (removals only drop keys from `dataByAlias`) and is
    # what the calendar-frequency `relativeConceptId` lookup reads (:392).
    data_by_alias = {}
    filtered_by_index = {}
    for i, comp in enumerate(components, 1):
        lst = list(data.get(str(i)) or [])
        data_by_alias[comp["alias"]] = lst
        filtered_by_index[str(i)] = lst

    removals = []
    no_data_components = []
    saved_bad_values = {}
    for comp in components:
        alias = comp["alias"]
        comp_data = data_by_alias[alias]
        if not comp_data:
            no_data_components.append(alias)
        bad = _filter_component_data(comp, knowledge.get(str(comp["id"])), comp_data)
        if not comp_data:
            removals.append(alias)
        # Pattern.cs:180 — the guard is INVERTED in the source (`if
        # (!badValues.Any())`), so only empty lists are ever stored.
        if not bad:
            saved_bad_values.setdefault(alias, bad)

    # ---- 2. enough-components gate + zero-fallback ------------------------
    kofn = 0
    try:
        kofn = int(spec.get("kofn") or 0)
    except (TypeError, ValueError):
        kofn = 0
    not_enough = ((removals and relation == "all")
                  or (relation == "kofn"
                      and kofn > len(data_by_alias) - len(removals)))
    if not_enough:
        accepted = False
        if (no_data_components and relation == "all"
                and len(no_data_components) < len(components)):
            accepted = True
            for alias in no_data_components:
                comp = components[alias_to_index[alias] - 1]
                concept = knowledge.get(str(comp["id"]))
                vcs = comp.get("value_constraints") or []
                if not vcs:
                    accepted = False
                    break
                vc = vcs[0]
                numeric = ((concept or {}).get("spec") or {}).get("output_type") == "numeric"
                zero_eq = (vc.get("operator") == "equal" and vc.get("value") == "0")
                compliance = ((output.get("value_group_blobs") or {}).get("kind")
                              == "compliance-function")
                if not ((numeric and zero_eq) or compliance):
                    accepted = False
                    break
        if not accepted:
            if (output.get("value_group_blobs") or {}).get("kind") == "count-function":
                return [DataInstance(patient_id, name, _DT_MIN, _DT_MAX, "0")]
            return []

    for alias in removals:
        data_by_alias.pop(alias, None)
    if not data_by_alias:
        if (output.get("value_group_blobs") or {}).get("kind") == "count-function":
            return [DataInstance(patient_id, name, _DT_MIN, _DT_MAX, "0")]
        return []

    # (:299-313 strips event-attribute rows; KB 2700's pilot closure has no
    # event components, and event attributes are never merged into `data` here.)

    # ---- 3. relation dispatch --------------------------------------------
    if relation == "any":
        blobs = []
        for comp in components:                 # dictionary insertion order
            blobs.extend(data_by_alias.get(comp["alias"]) or [])
    else:
        undealt = {c["alias"]: c for c in components}
        sorted_data = {}
        for alias, lst in data_by_alias.items():
            sorted_data[alias] = _sort_by_priorities(lst, undealt[alias])
        ctx = {
            "undealt": undealt,
            "saved_bad_values": saved_bad_values,
            "parents": {},
            "data": sorted_data,
            "knowledge": knowledge,
            "components": {c["alias"]: c for c in components},
            "components_order": [c["alias"] for c in components],
        }
        if (spec.get("pairwise_operator") or "and").lower() == "or":
            blobs_before = _blobs_or(spec, ctx)
        else:
            blobs_before = _blobs_and(spec, ctx)

        blobs = _calculate_blobs_output(blobs_before, spec, start_blk, end_blk,
                                        alias_to_index, name)
        if not blobs:
            if (output.get("value_group_blobs") or {}).get("kind") == "count-function":
                return [DataInstance(patient_id, name, _DT_MIN, _DT_MAX, "0")]
            return []

    # ---- 4. periodic constraints -----------------------------------------
    blobs.sort(key=lambda x: x.start)

    periodic = spec.get("periodic")
    if periodic is not None:
        intervals = []
        cf_gran = periodic.get("frequency_granularity")
        if cf_gran:
            rel_id = periodic.get("relative_component_id")
            rel_id = str(rel_id) if rel_id not in (None, "", "0") else None
            if rel_id:
                by_gid = {}
                for i, comp in enumerate(components, 1):
                    by_gid[str(comp["id"])] = filtered_by_index[str(i)]
                rel_data = by_gid.get(rel_id, [])
                if rel_data:
                    patient_id = rel_data[0].entity_id
                try:
                    fv = int(periodic.get("frequency_value"))
                except (TypeError, ValueError):
                    fv = 0
                if (cf_gran or "").lower() == "hour" and fv <= 24:
                    intervals.extend(_split_less_than_day(rel_data, periodic))
                else:
                    for rel in rel_data:
                        intervals.extend(_split_timeline_by_calendar_frequency(
                            rel, rel, periodic, True))
            else:
                if not blobs:
                    return []
                intervals.extend(_split_timeline_by_calendar_frequency(
                    blobs[0], blobs[-1], periodic, False))
        else:
            if not blobs:
                return []
            intervals.append(_Interval(blobs[0].start, blobs[-1].end))

        vgb = output.get("value_group_blobs") or {}
        is_compliance = vgb.get("kind") == "compliance-function"
        # :1070 — a compliance function keeps computing even with no blobs.
        if not blobs and not is_compliance:
            return []
        _fill_intervals_with_blobs(intervals, blobs)
        if is_compliance:
            # :1079-1085 — compliance skips GroupBlobsByTimeGapsAndCardinality
            # and rates every interval (StartEndGroupBlobs is ignored).
            blobs = _calc_compliance_rate(intervals, vgb, name, patient_id)
        else:
            groups = _group_blobs_by_gaps_and_cardinality(intervals, periodic)
            blobs = _calculate_group_of_blobs_output(groups, spec)

    elif output.get("value_group_blobs") is not None:
        intervals = []
        if blobs:
            intervals.append(_Interval(blobs[0].start, blobs[-1].end))
        _fill_intervals_with_blobs(intervals, blobs)
        blobs = _calculate_group_of_blobs_output(intervals, spec)

    if ((output.get("value_group_blobs") or {}).get("kind") == "count-function"
            and not blobs):
        return [DataInstance(patient_id, name, _DT_MIN, _DT_MAX, "0")]

    # ---- 5. final output  (CalculateFinalPatternOutput, :1120-1141) --------
    if (output.get("value_group_blobs") or {}).get("kind") == "count-function" and blobs:
        blobs = _calculate_count_missing(blobs)

    vgp = output.get("value_global_pattern")
    if vgp is None:
        for di in blobs:
            di.concept_name = name
        return blobs

    if not blobs:
        return []
    op = (vgp.get("statistical_operator") or "sum").lower()
    if vgp.get("kind") == "count-function":
        op = "count"
    di = _statistical(op, blobs)
    di.concept_name = name
    return [di]


def _split_less_than_day(rel_data, cf):
    """SplitTimelineByCalendarFrequencyLessThanDay (:2746-...) — only reachable
    for an Hour granularity of <=24 relative to a concept; unused in KB 2700's
    pilot closure but kept so the dispatch above stays faithful."""
    if not rel_data:
        return []
    return _split_timeline_by_calendar_frequency(rel_data[0], rel_data[-1], cf, True)
