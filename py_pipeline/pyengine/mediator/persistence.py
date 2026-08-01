"""Persistence: local (Smoosh) + global (Interpolate / Concatenate / Intersect).

Faithful port of:
  * Smoosh                       ComputationalServices\\Controller.cs:3937-4044
  * Interpolate                  BusinessEntities\\TAK\\AbstractConcepts\\State.cs:297-328
  * Concatenate / Intersect      BusinessEntities\\TAK\\AbstractConcepts\\AbstractConcept.cs:360-412
  * GetGlobalPersistance         BusinessEntities\\TAK\\Misc\\GlobalPersistence.cs:72-102

Pipeline order: raw -> Smoosh (local) -> partition/abstract -> Interpolate (global).

A `local` persistence dict is  {good_before:{value,granularity}, good_after:{...}}.
A `global` persistence dict is {behavior, granularity, interpolation_table:[[..]]}.
Both come straight from tak_parse spec['persistence'].
"""

from __future__ import annotations

import math
from datetime import timedelta

from datainstance import duration_to_timedelta, timespan_in_granularity
from functions import ERROR

_INT_MAX = 2147483647          # int.MaxValue -> "always bridge"


# ---------------------------------------------------------------------------
# LOCAL PERSISTENCE — Smoosh  (Controller.cs:3937-4044)
# ---------------------------------------------------------------------------

def smoosh(instances, local_persistence, upper_limit):
    """Extend raw points by good-before/good-after with proportional gap-split.

    Mutates and returns `instances` (caller passes copies). `local_persistence`
    is the derived RAW concept's local persistence dict; `upper_limit` clamps the
    right edge (DateTime.Now / configured smoosh limit).
    """
    if not instances:
        return instances
    lp = local_persistence or {}
    gb = lp.get("good_before") or {"value": "0", "granularity": "second"}
    ga = lp.get("good_after") or {"value": "0", "granularity": "second"}
    Bv = float(gb.get("value") or 0)
    Av = float(ga.get("value") or 0)
    B = duration_to_timedelta(gb.get("value"), gb.get("granularity"))
    A = duration_to_timedelta(ga.get("value"), ga.get("granularity"))

    # guard: both zero -> unchanged (Controller.cs:3941-3943)
    if Bv == 0 and Av == 0:
        return instances

    # single point (Controller.cs:3945-3955)
    if len(instances) == 1:
        d = instances[0]
        d.end = d.end + A
        d.start = d.start - B
        if d.end > upper_limit:
            d.end = upper_limit
        return instances

    instances.sort(key=lambda x: x.start)          # :3960
    N = len(instances)

    # leftSmooshedSeconds = data[0].dur + A + B  (:3961-3968)
    left_smooshed = ((instances[0].end + A) - (instances[0].start - B)).total_seconds()
    if left_smooshed == 0:
        left_smooshed = 1

    for i in range(N - 1):                          # :3971
        L = instances[i]
        R = instances[i + 1]
        L_end = L.end + A                           # :3976
        R_start = R.start - B                       # :3978

        if L_end >= R_start:                        # overlap after extension :3981
            if Bv == 0 and Av != 0:                 # special case :3986-3989
                L_end = R_start - timedelta(minutes=1)
            else:                                   # proportional split :3993-4013
                right_smooshed = ((R.end + A) - R_start).total_seconds()  # R.dur+A+B
                if right_smooshed == 0:
                    right_smooshed = 1
                p = left_smooshed / (left_smooshed + right_smooshed)      # :4004
                left_smooshed = right_smooshed                            # STATEFUL carry :4007
                gap = (R.start - L.end).total_seconds()   # ORIGINAL raw gap
                L_end = L.end + timedelta(seconds=gap * p)                # :4009-4012
                R_start = L_end                                           # :4013
        # else: no overlap -> keep extended values; left_smooshed NOT updated
        #       (stale carry into next overlapping pair) — faithful to :3981 else

        L.end = L_end                               # :4017
        R.start = R_start                           # :4018

        if i == 0:                                  # extend leftmost back :4024-4028
            instances[0].start = instances[0].start - B
        if i == N - 2:                              # extend rightmost fwd + clip :4030-4038
            instances[N - 1].end = instances[N - 1].end + A
            if instances[N - 1].end > upper_limit:
                instances[N - 1].end = upper_limit
    return instances


# ---------------------------------------------------------------------------
# GLOBAL PERSISTENCE table lookup  (GlobalPersistence.cs:72-102)
# ---------------------------------------------------------------------------

def _global_maxgap(gp, left_dur, right_dur):
    """GetGlobalPersistance(leftDuration, rightDuration) in granularity units."""
    table = (gp or {}).get("interpolation_table") or []
    # empty table -> int.MaxValue (always bridge)  :74-83
    if not table or not table[0]:
        return _INT_MAX
    behavior = (gp.get("behavior") or "pos-pos")
    li = min(left_dur, right_dur)                   # :86-87 upper triangle
    ri = max(left_dur, right_dur)
    rows = len(table)
    if behavior == "neg-neg":                        # :89-97
        if li > rows - 1 or ri > len(table[li]) - 1:
            return 0
        return int(table[li][ri])
    # pos-pos (default) -> clamp into range :99-101
    li = min(li, rows - 1)
    ri = min(ri, len(table[li]) - 1)
    return int(table[li][ri])


def _intersect_global(L, R, gp):
    """AbstractConcept.Intersect :400-412 — gap <= maxGap under global persist."""
    gran = (gp or {}).get("granularity") or "second"
    gap = timespan_in_granularity(R.start - L.end, gran)
    left_dur = int(timespan_in_granularity(L.timespan, gran))
    right_dur = int(timespan_in_granularity(R.timespan, gran))
    maxgap = _global_maxgap(gp, left_dur, right_dur)
    return gap <= maxgap


# ---------------------------------------------------------------------------
# Concatenate  (AbstractConcept.cs:360-392)
# ---------------------------------------------------------------------------

def _concatenate(group, gp):
    """Fixed-point merge of adjacent intervals within an equal-value group."""
    if not group:
        return []
    result = list(group)
    if result[0].value == ERROR:                     # :364
        return result
    changed = True
    while changed:                                   # fixed point :371
        changed = False
        i = 0
        while i < len(result) - 1:
            L = result[i]
            R = result[i + 1]
            if _intersect_global(L, R, gp):          # :381
                L.end = R.end                        # :387 merge -> [firstStart,lastEnd]
                del result[i + 1]                    # :388
                changed = True
            else:
                i += 1
    return result


# ---------------------------------------------------------------------------
# Interpolate  (State.cs:297-328)
# ---------------------------------------------------------------------------

def interpolate(candidates, gp, concatenable=True):
    """Group consecutive equal-Value runs, merge each via Concatenate.

    `gp` = the concept's global-persistence dict (per-value tables collapse to
    concept-level in KB 2700). Value equality is case-SENSITIVE ordinal ==.
    """
    if not concatenable or len(candidates) <= 1:
        return list(candidates)
    result = []
    group = [candidates[0]]
    for cand in candidates[1:]:
        if cand.value == group[0].value:             # :309 case-sensitive
            group.append(cand)
        else:
            result.extend(_concatenate(group, gp))   # :315
            group = [cand]
    result.extend(_concatenate(group, gp))           # :322
    return result
