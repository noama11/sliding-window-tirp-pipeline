"""Trend abstraction — Dec / Same / Inc segmentation.

Faithful port of BusinessEntities\\TAK\\AbstractConcepts\\Trend.cs:67-476.
Self-contained (no Gradient/Rate reuse). Pipeline per derived numeric series:

  1. guard: exactly one derived-from (a NumericRawConcept); empty / single point -> [].
  2. convertIntervalsIntoPoints (:336-351): every interval (dur>0) -> TWO points
     (one at Start, one at End), points pass through. After this ALL instances are
     points, so the interval-specific branches (:171-177, :254-265) are dead.
  3. sort by StartTime asc.
  4. cluster: greedy, new cluster whenever !Intersect(prevLast, cur) — Intersect is
     the global-persistence gap test (AbstractConcept.cs:400-412). Empty trend
     interpolation table => int.MaxValue => single cluster.
  5. remove non-local-extreme interior points (cluster>=3): keep iff strict local
     max or min.
  6. significant-segment scan: track running Min/Max (+ their source points);
     flip-min / flip-max flush a Same leg; while a point keeps Variation
     (=Max-Min) <= SignificantVariation absorb it (stays Same), else flush via
     GetTrendCandidate and reseed. Direction = sign of (later extreme - earlier
     extreme). Emitted value = GradientTrendValues.X.ToString() = "Dec"/"Same"/"Inc".
  7. engulf short Sames (<= TimeSteady): drop leading, merge consecutive, engulf.
  8. Interpolate: merge equal adjacent (Concatenable) with Same/Inc/Dec rules.

Value canonicalization: "Dec" / "Same" / "Inc" (enum member names), NOT the XML's
Decreasing/Stable/Increasing. Doubles parsed with invariant '.' decimals.
"""

from __future__ import annotations

from datainstance import DataInstance, duration_to_timedelta
from persistence import _intersect_global

DEC = "Dec"
SAME = "Same"
INC = "Inc"


def _pf(s):
    """Parse a numeric value string (invariant '.' decimal)."""
    return float(s)


class _Candidate:
    """Trend.Candidate: a DataInstance-like segment carrying running extremes."""
    __slots__ = ("entity", "name", "start", "end", "value",
                 "min_value", "max_value", "min_data", "max_data")

    def __init__(self, entity, name, start, end, value):
        self.entity = entity
        self.name = name
        self.start = start
        self.end = end
        self.value = value
        # C# Candidate.MinValue/MaxValue are value-type doubles -> default 0.0;
        # GetTrendCandidate / flip-Same segments leave them at the default, and
        # Interpolate's Inc/Dec/Same merge conditions rely on that.
        self.min_value = 0.0
        self.max_value = 0.0
        self.min_data = None
        self.max_data = None

    @property
    def timespan(self):
        return self.end - self.start

    @property
    def variation(self):
        return self.max_value - self.min_value


def _convert_intervals_to_points(series):
    """:336-351 — split each interval into two points (start, end)."""
    result = []
    for d in series:
        if d.timespan.total_seconds() > 0:
            d1 = d.copy(); d1.end = d1.start
            d2 = d.copy(); d2.start = d2.end
            result.append(d1)
            result.append(d2)
        else:
            result.append(d)
    return result


def _get_trend_candidate(cand):
    """:453-476 — resolve a running candidate to a Dec/Same/Inc segment."""
    left = cand.min_data
    right = cand.max_data
    if left is right:
        return _mk(cand.entity, cand.name, left.start, right.end, SAME)
    if left.start > right.start:
        left, right = right, left
    diff = _pf(right.value) - _pf(left.value)
    value = DEC if diff < 0 else (INC if diff > 0 else SAME)
    return _mk(cand.entity, cand.name, left.end, right.start, value)


def _mk(entity, name, start, end, value):
    c = _Candidate(entity, name, start, end, value)
    return c


def calculate(entity, name, spec, derived_id, series, knowledge,
              concatenable=True):
    """Compute a Trend's Dec/Same/Inc intervals for one entity.

    `spec` is the trend spec dict {significant_variation, time_steady,
    persistence, ...}. `series` is the derived numeric raw's (Smooshed)
    DataInstance list. Returns a list of DataInstance.
    """
    if series is None or len(series) == 0:
        return []
    if len(series) == 1 and series[0].timespan.total_seconds() == 0:
        return []

    sig = float(spec.get("significant_variation") or 0)
    ts = spec.get("time_steady") or {}
    time_steady = duration_to_timedelta(ts.get("value"), ts.get("granularity"))
    gp = ((spec.get("persistence") or {}).get("global")) or {}

    series = _convert_intervals_to_points(series)
    series.sort(key=lambda d: d.start)

    # --- clusters (:115-129) ---
    clusters = [[series[0]]]
    for cur in series[1:]:
        if not _intersect_global(clusters[-1][-1], cur, gp):
            clusters.append([])
        clusters[-1].append(cur)

    # --- remove non-local-extreme interior points (:135-157) ---
    for cluster in clusters:
        if len(cluster) < 3:
            continue
        i = 1
        while i < len(cluster) - 1:
            cur = _pf(cluster[i].value)
            prev = _pf(cluster[i - 1].value)
            nxt = _pf(cluster[i + 1].value)
            if (cur > prev and cur > nxt) or (cur < prev and cur < nxt):
                i += 1
                continue
            del cluster[i]

    # --- significant-segment scan (:165-275) ---
    clusters_sig = []
    for cluster in clusters:
        seg = []
        first = cluster[0]
        # first.TimeSpan>0 branch (:171) is dead after point-conversion.
        first_val = _pf(first.value)
        cand = _Candidate(first.entity_id, name, first.end, first.end, SAME)
        cand.min_value = cand.max_value = first_val
        cand.min_data = cand.max_data = first

        for cur in cluster[1:]:
            cur_val = _pf(cur.value)
            # flip min (:197-214)
            if cur_val < cand.min_value:
                if cand.min_data.start < cand.max_data.start:
                    s = _mk(cand.min_data.entity_id, name,
                            cand.min_data.start, cand.max_data.start, SAME)
                    seg.append(s)
                    cand.start = cand.end
                cand.end = cur.start
                cand.min_value = cur_val
                cand.min_data = cur
            # flip max (:217-234)
            if cur_val > cand.max_value:
                if cand.min_data.start > cand.max_data.start:
                    s = _mk(cand.max_data.entity_id, name,
                            cand.max_data.start, cand.min_data.start, SAME)
                    seg.append(s)
                    cand.start = cand.end
                cand.end = cur.start
                cand.max_value = cur_val
                cand.max_data = cur
            # absorb steady point (:237-241); cur is always a point here
            if cand.variation <= sig:
                cand.end = cur.end
                continue
            # flush + reseed (:243-252); reseeded cur is a point -> continue (:254)
            seg.append(_get_trend_candidate(cand))
            cand = _Candidate(cur.entity_id, name, cur.start, cur.end, SAME)
            cand.min_value = cand.max_value = cur_val
            cand.min_data = cand.max_data = cur

        if cand.timespan.total_seconds() > 0:
            seg.append(cand)
        if seg:
            clusters_sig.append(seg)

    # --- engulf short Sames (:279-322) ---
    for cluster in clusters_sig:
        if len(cluster) <= 1:
            continue
        first = cluster[0]
        while (first is not None and first.value == SAME
               and first.timespan <= time_steady):
            cluster[1].start = first.start
            del cluster[0]
            if len(cluster) <= 1:
                first = None
                continue
            first = cluster[0]
        i = 0
        while i < len(cluster) - 1:
            while (cluster[i + 1].value == SAME and i + 2 < len(cluster)
                   and cluster[i + 2].value == SAME):
                cluster[i + 1].end = cluster[i + 2].end
                del cluster[i + 2]
            if (cluster[i + 1].value == SAME
                    and cluster[i + 1].timespan <= time_steady):
                cluster[i].end = cluster[i + 1].end
                del cluster[i + 1]
                i -= 1
            i += 1

    # --- interpolate merge equal adjacent (:362-446) ---
    # Trend.cs:364 gates this on TemporalSemantic.Concatenable, which the
    # XmlReader ctor reads from the XML (all 25 KB-2700 trends declare true).
    if concatenable:
        result = _interpolate(clusters_sig, sig, gp)
    else:
        result = [c for cluster in clusters_sig for c in cluster]
    return [DataInstance(c.entity, name, c.start, c.end, c.value) for c in result]


def _interpolate(clusters_sig, sig, gp):
    if not clusters_sig:
        return []
    result = []
    for cl in clusters_sig:
        result.extend(cl)
    i = 0
    while i < len(result) - 1:
        left = result[i]
        right = result[i + 1]
        merged = None
        if left.value == right.value and _intersect_global(left, right, gp):
            if left.value == SAME:
                t = _mk(left.entity, left.name, left.start, right.end, left.value)
                t.min_value, t.min_data = left.min_value, left.min_data
                t.max_value, t.max_data = left.max_value, left.max_data
                if right.min_value < t.max_value:
                    t.min_value, t.min_data = right.min_value, right.min_data
                if right.max_value > t.max_value:
                    t.max_value, t.max_data = right.max_value, right.max_data
                if t.variation <= sig:
                    merged = t
            elif left.value == INC:
                if left.max_value <= right.min_value:
                    merged = _mk(left.entity, left.name, left.start, right.end, left.value)
                    merged.min_value, merged.min_data = left.min_value, left.min_data
                    merged.max_value, merged.max_data = right.max_value, right.max_data
            elif left.value == DEC:
                if left.max_value >= right.min_value:
                    merged = _mk(left.entity, left.name, left.start, right.end, left.value)
                    merged.min_value, merged.min_data = right.min_value, right.min_data
                    merged.max_value, merged.max_data = left.max_value, left.max_data
        if merged is not None:
            del result[i:i + 2]
            result.insert(i, merged)
            i -= 1
        i += 1
    return result
