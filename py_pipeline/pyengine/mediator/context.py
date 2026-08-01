"""Context abstraction — induction, clipping, merge.

Faithful port of BusinessEntities\\TAK\\Context.cs:72-409 (+ filterDataByContext,
Controller.cs:4101-4117).

Induction: for each inducer-entity, each satisfying instance of the inducer
concept produces a context interval [shift(from), shift(until)] with value
"True" (hard-coded true.ToString()). Clippers truncate overlapping contexts down
to the clipper's shifted start. Results are start-sorted, de-duplicated
(equal-start), and union-merged (Concatenable=true for Context).

shift(instance, boundary): base = instance.End if boundary point==end else
instance.Start; return base + gap (SIGNED additive; gaps may be negative; month
=30d, year=365d fixed per Duration.cs).
"""

from __future__ import annotations

import math

from datainstance import DataInstance, gap_from_duration_dict

_NAN = float("nan")


# ---------------------------------------------------------------------------
# boundary shift  (Context.cs:319-324)
# ---------------------------------------------------------------------------

def _shift(instance, boundary, default_point):
    """boundary = {boundary_point, time_gap} or None. default_point: 'start'/'end'."""
    if boundary is None:
        point = default_point
        gap = None
    else:
        point = boundary.get("boundary_point") or default_point
        gap = boundary.get("time_gap")
    base = instance.end if point == "end" else instance.start
    return base + gap_from_duration_dict(gap)


# ---------------------------------------------------------------------------
# value-constraint satisfaction  (Context.cs:192-295)
# ---------------------------------------------------------------------------

def _to_double(value, concept):
    """TryGetValue (Context.cs:303-317): parse to double only for RawNumeric
    (or RawOrdinal rank). Otherwise NaN -> string path."""
    if concept is None:
        return _NAN
    op = concept.get("op_type")
    if op == "raw-numeric":
        try:
            return float(value)
        except (TypeError, ValueError):
            return _NAN
    # no raw-ordinal concepts in KB 2700; everything else -> string path
    return _NAN


def _check_satisfaction(instance, concept, constraints):
    """AND over constraints; any failing -> False (Context.cs:288-291)."""
    inst_d = _to_double(instance.value, concept)
    for c in constraints or []:
        op = c.get("operator")
        cval = c.get("value")
        if math.isnan(inst_d):
            # string path (case-insensitive)
            iv = str(instance.value).lower()
            cv = str(cval).lower()
            if op == "equal":
                ok = (iv == cv)
            elif op in ("different", "not-equal"):
                ok = (iv != cv)
            else:
                ok = False
        else:
            const_d = _to_double(cval, concept)
            if math.isnan(const_d):
                ok = False
            elif op == "bigger":
                ok = inst_d > const_d
            elif op == "smaller":
                ok = inst_d < const_d
            elif op in ("bigger-equal", "bigger-or-equal"):
                ok = inst_d >= const_d
            elif op in ("smaller-equal", "smaller-or-equal"):
                ok = inst_d <= const_d
            elif op == "equal":
                ok = inst_d == const_d
            elif op in ("different", "not-equal"):
                ok = inst_d != const_d
            else:
                ok = False
        if not ok:
            return False
    return True


# ---------------------------------------------------------------------------
# strict overlap  (Context.cs:327-334)
# ---------------------------------------------------------------------------

def _intersect(a, b):
    if a.start < b.start:
        return a.end > b.start
    return a.start < b.end


# ---------------------------------------------------------------------------
# merge passes  (Context.cs:343-409)
# ---------------------------------------------------------------------------

def _remove_duplicates(items):
    """Equal-start collapse: left.end = right.end, drop right (:343-372)."""
    if len(items) <= 1:
        return items
    result = [items[0]]
    for right in items[1:]:
        left = result[-1]
        if left.start == right.start:
            left.end = right.end
        else:
            result.append(right)
    return result


def _remove_conflicts(items):
    """Union overlapping (Concatenable): left.end = right.end, drop right
    (:380-409). Adjacent (touching) intervals do NOT merge (strict Intersect)."""
    if len(items) <= 1:
        return items
    result = [items[0]]
    for right in items[1:]:
        left = result[-1]
        if _intersect(left, right):
            left.end = right.end
        else:
            result.append(right)
    return result


# ---------------------------------------------------------------------------
# public entry  (Context.cs:72-187)
# ---------------------------------------------------------------------------

def calculate(entity, name, spec, data, knowledge, concatenable=True):
    """Compute a Context's induced intervals for one entity.

    `spec` = context spec dict {inducers, clippers}. `data` = {concept id ->
    DataInstance list} for inducer & clipper concepts. Returns DataInstance list
    (value "True")."""
    result = []
    for inducer in spec.get("inducers") or []:
        cid = inducer.get("id")
        concept = knowledge.get(cid)
        for inst in data.get(cid) or []:
            if _check_satisfaction(inst, concept, inducer.get("value_constraints")):
                start = _shift(inst, inducer.get("from"), "start")
                end = _shift(inst, inducer.get("until"), "end")
                result.append(DataInstance(inst.entity_id, name, start, end, "True"))

    for clipper in spec.get("clippers") or []:
        cid = clipper.get("id")
        concept = knowledge.get(cid)
        for cinst in data.get(cid) or []:
            if not _check_satisfaction(cinst, concept, clipper.get("value_constraints")):
                continue
            clip_point = _shift(cinst, clipper.get("from"), "start")
            for cand in result:
                if _intersect(cinst, cand):
                    if not cand.clip(clip_point):
                        break

    result.sort(key=lambda x: x.start)
    result = _remove_duplicates(result)
    # RemoveConflicts is gated on TemporalSemantic.Concatenable (Context.cs:392),
    # which the XmlReader ctor reads from the XML -- 4 of KB 2700's 25 contexts
    # declare concatenable="false" and must NOT union their overlaps.
    if concatenable:
        result = _remove_conflicts(result)
    return result


# ---------------------------------------------------------------------------
# filterDataByContext  (Controller.cs:4101-4117)
# ---------------------------------------------------------------------------

def filter_data_by_context(data_list, context_list):
    """Keep di iff fully contained in some context interval."""
    out = []
    for di in data_list:
        for ctx in context_list:
            if di.start >= ctx.start and di.end <= ctx.end:
                out.append(di)
                break
    return out
