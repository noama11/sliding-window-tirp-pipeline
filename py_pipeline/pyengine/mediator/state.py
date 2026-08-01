"""State abstraction — sweep-line partitioner + mapping + interpolate.

Faithful port of BusinessEntities\\TAK\\AbstractConcepts\\State.cs:
  * CalculateSingleFunction  :141-215  (sweep-line partitioning)
  * NextPartition            :254-290
  * Interpolate              :297-328  (-> persistence.interpolate)

State turns per-partition evaluations of the MappingFunction into value
intervals, then merges equal-valued adjacent intervals via global persistence.

This module operates on a *state spec* (the parsed tak spec dict: mappings,
rank_selection_criteria, persistence) rather than a whole concept, so the engine
can drive it with either the top-level mapping or a nested state-at-context
mapping (context switching) using the same code.

`data` maps a derived concept id -> its (already Smooshed) DataInstance list.
`knowledge` maps concept id -> parsed tak concept (for value-type resolution).
`concatenable` comes from the concept's <temporal-semantic concatenable=...>:
only the PROGRAMMATIC State ctor forces it true (State.cs:46); the XmlReader
ctor (:48-58) reads it from the XML, and 100 of KB 2700's 156 states declare
`concatenable="false"` -- for those Interpolate is skipped entirely
(State.cs:299) and equal-valued adjacent intervals must NOT be merged.
"""

from __future__ import annotations

from datetime import datetime

from datainstance import DataInstance
from functions import evaluate_mapping, ERROR
from persistence import interpolate

_DT_MIN = datetime.min


# ---------------------------------------------------------------------------
# NextPartition  (State.cs:254-290)
# ---------------------------------------------------------------------------

def _next_partition(data, frontier, next_end, next_start):
    for cid in list(frontier.keys()):
        idx = frontier[cid]["idx"]
        candidate_instance = data[cid][idx]
        if candidate_instance.end <= next_start:
            nxt = idx + 1
            del frontier[cid]
            if nxt < len(data[cid]):
                frontier[cid] = {"inst": data[cid][nxt], "idx": nxt}
    end_copy = next_end
    cands = [f["inst"].start for f in frontier.values() if f["inst"].start > end_copy]
    cands += [f["inst"].end for f in frontier.values() if f["inst"].end > end_copy]
    return min(cands) if cands else _DT_MIN


# ---------------------------------------------------------------------------
# CalculateSingleFunction  (State.cs:141-215)
# ---------------------------------------------------------------------------

def _calculate_single_function(entity, name, mappings, rank, data, knowledge):
    frontier = {}
    for cid, lst in data.items():
        if lst:
            frontier[cid] = {"inst": lst[0], "idx": 0}
    if not frontier:
        return []

    next_start = min(f["inst"].start for f in frontier.values())
    # nextEnd = min( (starts except nextStart) ∪ (all ends) )   :163-165
    starts = [f["inst"].start for f in frontier.values()]
    ends = [f["inst"].end for f in frontier.values()]
    pool = [s for s in starts if s != next_start] + ends
    next_end = min(pool)

    results = []
    while frontier:
        candidate_partition = {}
        for cid, f in frontier.items():
            if f["inst"].start <= next_start and f["inst"].end >= next_end:  # full cover :172
                candidate_partition[cid] = DataInstance(
                    entity, name, next_start, next_end, f["inst"].value)
        if candidate_partition:
            val = evaluate_mapping(mappings, rank, candidate_partition, knowledge)
            if val is not None and val != ERROR:            # drop null / ERROR :185
                results.append(DataInstance(entity, name, next_start, next_end, val))

        # advance  :196-212
        if next_start < next_end:
            next_start = next_end
            has_start = any(f["inst"].start == next_start for f in frontier.values())
            has_end = any(f["inst"].end == next_start for f in frontier.values())
            if has_start and has_end:
                continue                                    # zero-length partition :204
            next_end = _next_partition(data, frontier, next_end, next_start)
        else:
            next_end = _next_partition(data, frontier, next_end, next_start)
    return results


# ---------------------------------------------------------------------------
# public entry
# ---------------------------------------------------------------------------

def calculate(entity, name, spec, data, knowledge, concatenable=True):
    """Compute a State's value intervals for one entity.

    `spec` is the state spec dict {mappings, rank_selection_criteria,
    persistence, ...}. `data` is {derived_id -> sorted DataInstance list}
    (Smooshed upstream). Returns the interpolated interval list.
    """
    if not data:
        return []
    # State.Calculate sorts each derived list in place by StartTime (:106)
    local = {}
    for cid, lst in data.items():
        if lst:
            local[cid] = sorted(lst, key=lambda x: x.start)
    if not local:
        return []

    mappings = spec.get("mappings") or []
    rank = spec.get("rank_selection_criteria") or "min"
    candidates = _calculate_single_function(entity, name, mappings, rank, local, knowledge)

    gp = ((spec.get("persistence") or {}).get("global")) or {}
    return interpolate(candidates, gp, concatenable=concatenable)
