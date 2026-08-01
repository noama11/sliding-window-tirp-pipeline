"""Flatten the 2700 TAK entity XML files into a single tak_2700.json.

The Mediator .NET engine loads concept definitions from a per-KB folder of XML
"TAK entity" files (one concept per file). This module reads that folder and
emits a flat, JSON-serialisable description of every concept, plus the
transitive-closure edges of the concept dependency graph.

Design notes / fidelity
------------------------
* Operator type is classified from the XML **root element**, never the file
  name (file names carry misleading suffixes like `_pattern`, `_ctxt`). Root
  elements observed in KB 2700:
      state (156)  pattern (67)  numeric-raw-concept (46)
      nominal-raw-concept (46)  trend (25)  context (25)  event (11)
  There is no `gradient` or `rate` root -- every trend-like concept is `<trend>`.
* We preserve the raw structure faithfully (thresholds, function trees,
  persistence tables, inducer/clipper geometry). Interpretation of that
  structure lives in the operator modules, not here -- this file only parses.
* `concept-type` attribute is retained but the root element is authoritative;
  they agree in KB 2700 (raw-numeric<->numeric-raw-concept etc.).

Dependency edges
----------------
An edge A -> B means "A is derived from / depends on B" (A needs B's data to be
computed first). Sources of edges:
    * <derived-from><derived-from-id>N   (state / trend)
    * evaluation-tree <concept-id-allowed-values id=N>   (state mapping fns)
    * <inducer-entity id=N> and <clipper-entity id=N>    (context)
    * <abstraction-at-context> context-id references         (context switching)
    * <Attribute-id>N (event) -- kept but events are leaves for our closure.

Public API
----------
    parse_folder(folder)        -> {id(str): concept-dict}
    build_json(folder, out)     -> writes tak_2700.json, returns the dict
    closure(concepts, seeds)    -> set of ids reachable from seeds via edges
    depends_on_pattern(concepts, cid) -> bool

Run as a script to (re)generate the JSON next to the KB folder:
    python tak_parse.py <tak_entities/2700 folder> [out.json]
"""

from __future__ import annotations

import json
import os
import sys
import xml.etree.ElementTree as ET


# ---------------------------------------------------------------------------
# low-level helpers
# ---------------------------------------------------------------------------

def _text(el):
    """Trimmed text of an element, or None."""
    if el is None or el.text is None:
        return None
    t = el.text.strip()
    return t or None


def _attrs(el):
    """Element attributes with the xsi/namespace noise dropped."""
    return {k: v for k, v in el.attrib.items() if not k.startswith("{")}


def _dbl(s):
    """Parse a trapeze attribute to float; 0.0 on absence (C# double default)."""
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def _find(el, tag):
    """First direct-or-descendant child with the given local tag, or None."""
    for c in el.iter(tag):
        return c
    return None


# ---------------------------------------------------------------------------
# recursive evaluation-tree parser (state mapping functions)
# ---------------------------------------------------------------------------
#
# An <evaluation-tree data-type="X"> wraps exactly one typed node.  Node types:
#   logical-function     : logical-operator + <operands><operand>*
#   comparison-function  : comparison-operator + <left> + <right>
#   mathematical-function: mathematical-operator + <left> + <right>
#   concept-id-allowed-values : id  (a variable = the derived-from concept value)
#   double               : a literal numeric constant
#   string / nominal     : a literal symbol (rare)
# Each <operand>/<left>/<right> is itself a data-type wrapper around one node.

def _parse_typed(el):
    """Parse a data-type wrapper element (evaluation-tree / operand / left /
    right) into a nested dict. Returns None for empty wrappers."""
    if el is None:
        return None
    dt = el.attrib.get("data-type")
    # the single typed child element carries the real content
    child = None
    for c in el:
        child = c
        break
    if child is None:
        # some wrappers put the datatype tag directly; fall back on dt
        return {"type": dt} if dt else None
    return _parse_node(child, dt)


def _parse_node(node, dt=None):
    tag = node.tag
    if tag == "logical-function":
        operands = []
        ops_container = node.find("operands")
        if ops_container is not None:
            for operand in ops_container.findall("operand"):
                operands.append(_parse_typed(operand))
        return {
            "type": "logical",
            "operator": node.attrib.get("logical-operator"),
            "operands": operands,
        }
    if tag == "comparison-function":
        return {
            "type": "comparison",
            "operator": node.attrib.get("comparison-operator"),
            "left": _parse_typed(node.find("left")),
            "right": _parse_typed(node.find("right")),
        }
    if tag == "mathematical-function":
        return {
            "type": "math",
            "operator": node.attrib.get("mathematical-operator"),
            # substituted for an absent concept operand (MathematicalFunction.cs:53)
            "missing_concept_def_value": node.attrib.get("missing-concept-def-value"),
            "left": _parse_typed(node.find("left")),
            "right": _parse_typed(node.find("right")),
        }
    if tag == "concept-id-allowed-values":
        return {"type": "concept", "id": node.attrib.get("id")}
    if tag == "double":
        return {"type": "double", "value": _text(node)}
    if tag == "integer":
        return {"type": "integer", "value": _text(node)}
    if tag in ("string", "nominal", "nominal-value"):
        return {"type": "string", "value": _text(node)}
    # unknown leaf -- keep tag + text + attrs so nothing is silently lost
    return {"type": tag, "value": _text(node), "attrs": _attrs(node)}


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------
#
# <persistence>
#   <global-persistence behavior=? granularity=?>
#       <interpolation-table><rows><row><columns><column>N</column>...
#   <local-persistence>
#       <good-before value granularity/> <good-after value granularity/>
#
# The interpolation table is the "max gap that may be bridged" matrix
# (empty <rows/> => default => always bridge / int.MaxValue). We keep it as a
# list of row-lists so persistence.py can interpret it.

def _parse_gap(el):
    if el is None:
        return None
    return {"value": el.attrib.get("value"),
            "granularity": el.attrib.get("granularity")}


def _parse_persistence(container):
    """`container` is the *-allowed-values element that holds <persistence>."""
    if container is None:
        return None
    pers = container.find("persistence")
    if pers is None:
        return None
    out = {}
    gp = pers.find("global-persistence")
    if gp is not None:
        rows = []
        table = gp.find("interpolation-table")
        if table is not None:
            rows_el = table.find("rows")
            if rows_el is not None:
                for row in rows_el.findall("row"):
                    cols_el = row.find("columns")
                    cols = []
                    if cols_el is not None:
                        for col in cols_el.findall("column"):
                            cols.append(_text(col))
                    rows.append(cols)
        out["global"] = {
            "behavior": gp.attrib.get("behavior"),
            "granularity": gp.attrib.get("granularity"),
            "interpolation_table": rows,
        }
    lp = pers.find("local-persistence")
    if lp is not None:
        out["local"] = {
            "good_before": _parse_gap(lp.find("good-before")),
            "good_after": _parse_gap(lp.find("good-after")),
        }
    return out


def _parse_values(container):
    """Ordinal / nominal allowed value list -> [{order?, value}]."""
    if container is None:
        return None
    vals_el = container.find("values")
    if vals_el is None:
        return None
    out = []
    for v in list(vals_el):
        out.append({"order": v.attrib.get("order"), "value": v.attrib.get("value")})
    return out


# ---------------------------------------------------------------------------
# common header
# ---------------------------------------------------------------------------

def _derived_from(root):
    ids = []
    df = root.find("derived-from")
    if df is not None:
        for x in df.findall("derived-from-id"):
            t = _text(x)
            if t:
                ids.append(t)
    return ids


def _temporal_semantic(root):
    ts = root.find("temporal-semantic")
    return _attrs(ts) if ts is not None else None


def _abstraction_at_contexts(root):
    """Context-switching definitions.

    A concept may abstract differently inside different contexts. The XML holds
    an <abstraction-at-contexts> list of <abstraction-at-context> blocks, each:
        <at-context>
            <necessary-contexts><necessary-context-id>N</necessary-context-id>*
            <exclusion-contexts><exclusion-context-id>N</exclusion-context-id>*
        <state-at-context ...>  (or trend-at-context ...)  -- a full nested
            concept definition (its OWN mapping-function / thresholds).

    When abstraction-at-contexts is non-empty the concept's TOP-LEVEL
    mapping-function is typically empty; the real logic lives per-context.
    Returns a list of {necessary_contexts, exclusion_contexts, nested_kind,
    spec} where spec is the parsed nested state/trend spec.
    """
    out = []
    aac = root.find("abstraction-at-contexts")
    if aac is None:
        return out
    for block in aac.findall("abstraction-at-context"):
        nec, exc = [], []
        at = block.find("at-context")
        if at is not None:
            ncs = at.find("necessary-contexts")
            if ncs is not None:
                for x in ncs.findall("necessary-context-id"):
                    t = _text(x)
                    if t:
                        nec.append(t)
            excs = at.find("exclusion-contexts")
            if excs is not None:
                for x in excs.findall("exclusion-context-id"):
                    t = _text(x)
                    if t:
                        exc.append(t)
        nested_kind = None
        nested_spec = None
        nested_derived = []
        for ch in block:
            if ch.tag == "at-context":
                continue
            nested_kind = ch.tag
            nested_derived = _derived_from(ch)
            if ch.tag == "state-at-context":
                nested_spec = _parse_state(ch)
            elif ch.tag == "trend-at-context":
                nested_spec = _parse_trend(ch)
            break
        out.append({
            "necessary_contexts": nec,
            "exclusion_contexts": exc,
            "nested_kind": nested_kind,
            "derived_from": nested_derived,
            "spec": nested_spec,
        })
    return out


# ---------------------------------------------------------------------------
# per-operator parsers
# ---------------------------------------------------------------------------

def _parse_state(root):
    mf = root.find("mapping-function")
    mappings = []
    if mf is not None:
        m2v = mf.find("mapping-functions-to-values")
        if m2v is not None:
            for entry in m2v.findall("mapping-function-2-value"):
                tree = entry.find("evaluation-tree")
                mappings.append({
                    "order": entry.attrib.get("order"),
                    "value": entry.attrib.get("value"),
                    "tree": _parse_typed(tree),
                })
    # AllowedValues.OutputType drives every numeric/ordinal/string decision in
    # the function evaluators, so all four families must be recognised (KB 2700
    # has 100 ordinal + 56 NUMERIC states).
    allowed = None
    for tag in ("ordinal-allowed-values", "nominal-allowed-values",
                "numeric-allowed-values", "boolean-allowed-values"):
        allowed = root.find(tag)
        if allowed is not None:
            break
    return {
        "rank_selection_criteria": mf.attrib.get("rank-selection-criteria") if mf is not None else None,
        "mappings": mappings,
        "output_type": allowed.attrib.get("output-type") if allowed is not None else None,
        "allowed_values": _parse_values(allowed),
        "persistence": _parse_persistence(allowed),
    }


def _parse_trend(root):
    allowed = root.find("gradient-trend-allowed-values")
    ts_el = root.find("time-steady")
    return {
        "significant_variation": root.attrib.get("significant-variation"),
        "output_type": allowed.attrib.get("output-type") if allowed is not None else None,
        "allowed_values": _parse_values(allowed),
        "persistence": _parse_persistence(allowed),
        "time_steady": _parse_gap(ts_el) if ts_el is not None else None,
    }


def _parse_context(root):
    inducers = []
    ie_container = root.find("inducer-entities")
    if ie_container is not None:
        for ie in ie_container.findall("inducer-entity"):
            vcs = []
            vc_container = ie.find("value-constraints")
            if vc_container is not None:
                for vc in vc_container.findall("value-constraint"):
                    vcs.append({"value": vc.attrib.get("value"),
                                "operator": vc.attrib.get("operator")})
            frm = ie.find("from")
            until = ie.find("until")
            inducers.append({
                "id": ie.attrib.get("id"),
                "value_constraints": vcs,
                "from": {
                    "boundary_point": frm.attrib.get("boundary-point"),
                    "time_gap": _parse_gap(frm.find("time-gap")),
                } if frm is not None else None,
                "until": {
                    "boundary_point": until.attrib.get("boundary-point"),
                    "time_gap": _parse_gap(until.find("time-gap")),
                } if until is not None else None,
            })
    clippers = []
    cl_container = root.find("clippers")
    if cl_container is not None:
        for ce in cl_container.findall("clipper-entity"):
            cvcs = []
            cvc_container = ce.find("clipper-value-constraints")
            if cvc_container is not None:
                for cvc in cvc_container.findall("clipper-value-constraint"):
                    cvcs.append({"value": cvc.attrib.get("value"),
                                 "operator": cvc.attrib.get("operator")})
            frm = ce.find("from")
            clippers.append({
                "id": ce.attrib.get("id"),
                "value_constraints": cvcs,
                "from": {
                    "boundary_point": frm.attrib.get("boundary-point"),
                    "time_gap": _parse_gap(frm.find("time-gap")),
                } if frm is not None else None,
            })
    return {"inducers": inducers, "clippers": clippers}


def _parse_event(root):
    attrs = []
    ac = root.find("Attributes")
    if ac is not None:
        for a in ac.findall("Attribute-id"):
            t = _text(a)
            if t:
                attrs.append(t)
    et = root.find("Event-Types")
    return {"attribute_ids": attrs, "event_types": _text(et)}


def _parse_raw_numeric(root):
    allowed = root.find("numeric-allowed-values")
    a = _attrs(allowed) if allowed is not None else {}
    return {
        "output_type": a.get("output-type"),
        "min_value": a.get("min-value"),
        "max_value": a.get("max-value"),
        "units": a.get("units"),
        "scale": a.get("scale"),
        "persistence": _parse_persistence(allowed),
    }


def _parse_raw_nominal(root):
    allowed = root.find("nominal-allowed-values")
    return {
        "output_type": allowed.attrib.get("output-type") if allowed is not None else None,
        "allowed_values": _parse_values(allowed),
        "persistence": _parse_persistence(allowed),
    }


def _parse_time_shift(el):
    if el is None:
        return None
    return {"value": el.attrib.get("value"), "granularity": el.attrib.get("granularity")}


# Which function elements PatternOutput.ReadXml's value-local-pattern switch
# accepts (PatternOutput.cs:121-149). ANY other child throws inside the method's
# try/catch, which silently ABORTS the rest of pattern-output parsing -- see
# `output_parse_aborted` below.
_VALUE_LOCAL_FUNCTIONS = ("mapping-function", "logical-function",
                          "comparison-function", "mathematical-function",
                          "union-function")

# GlobalFunction kinds accepted for value-group-blobs / value-global-pattern.
_GLOBAL_FUNCTIONS = ("statistical-function", "count-function",
                     "get-kth-function", "compliance-function")


def _parse_global_function(container):
    """<value-group-blobs>/<value-global-pattern> wrapper -> {kind, ...}."""
    if container is None:
        return None
    for ch in container:
        if ch.tag not in _GLOBAL_FUNCTIONS:
            continue
        node = {"kind": ch.tag}
        if ch.tag == "statistical-function":
            node["statistical_operator"] = ch.attrib.get("statistical-operator")
        elif ch.tag == "compliance-function":
            # ComplianceFunction.ReadXml: an optional <periodic-function> then an
            # optional <time-constraint-compliance> (a FuzzyFunction). KB 2700's
            # 484/485 carry only the time-constraint (trapezeA..D).
            tcc = ch.find("time-constraint-compliance")
            if tcc is not None:
                node["time_constraint"] = {
                    "a": _dbl(tcc.attrib.get("trapezeA")),
                    "b": _dbl(tcc.attrib.get("trapezeB")),
                    "c": _dbl(tcc.attrib.get("trapezeC")),
                    "d": _dbl(tcc.attrib.get("trapezeD")),
                }
            pf = ch.find("periodic-function")
            if pf is not None:
                node["periodic_function"] = _attrs(pf)
        else:
            node.update(_attrs(ch))
        return node
    return None


def _parse_point_definition(el):
    """<start>/<end> of a time-constraint: PointDefinition(reference-point +
    <time-shift>). A missing reference-point means "now" in the .NET code; we
    record None and let the caller decide (Pattern's default TimeConstraint is
    MinValue..MaxValue)."""
    if el is None:
        return None
    return {"reference_point": el.attrib.get("reference-point"),
            "time_shift": _parse_time_shift(el.find("time-shift"))}


def _parse_local_constraints(comp):
    """<local-constraints> of a pattern component (LocalConstraints.cs).

    Note the .NET XML ctor leaves DurationConstraint NULL when the element is
    absent (so the duration filter is skipped entirely), while TimeConstraint
    always falls back to MinValue..MaxValue."""
    lc = comp.find("local-constraints")
    vcs = []
    tc = None
    dc = None
    save_bad = False
    if lc is not None:
        save_bad = (lc.attrib.get("save-bad-values") or "").lower() == "true"
        vcc = lc.find("value-constraints")
        if vcc is not None:
            for vc in vcc.findall("value-constraint"):
                vcs.append({"value": vc.attrib.get("value"),
                            "operator": vc.attrib.get("operator"),
                            "event_attr_id": vc.attrib.get("event-attribute-id")})
        tc_el = lc.find("time-constraint")
        if tc_el is not None:
            tc = {"start": _parse_point_definition(tc_el.find("start")),
                  "end": _parse_point_definition(tc_el.find("end"))}
        dc_el = lc.find("duration-constraint")
        if dc_el is not None:
            dc = {"min_duration": _parse_gap(dc_el.find("min-duration")),
                  "max_duration": _parse_gap(dc_el.find("max-duration"))}
    return vcs, tc, dc, save_bad


def _parse_priorities(comp):
    """<priorities><priority priority-type=? min-max=?/>*  (PatternComponent.cs).

    The XML ctor starts from an EMPTY list -- an empty <priorities/> therefore
    means "no priority", which in turn means instance REUSE IS ALLOWED
    (IsReuseAllowed, Pattern.cs:2439) and no sorting is applied."""
    out = []
    pr = comp.find("priorities")
    if pr is None:
        return out
    for p in list(pr):
        a = _attrs(p)
        # attribute names vary by serializer config; take the two values in order
        prio = a.get("priority") or a.get("priority-type") or a.get("type")
        minmax = a.get("min-max") or a.get("minmax") or a.get("value")
        if prio is None and a:
            vals = list(a.values())
            prio = vals[0]
            minmax = vals[1] if len(vals) > 1 else None
        out.append({"priority": prio, "min_max": minmax})
    return out


def _parse_pattern(root):
    """Full pattern-definition + pattern-output parse (Phase 3).

    Components (id/alias + local value/time/duration constraints + instance
    priorities), pairwise temporal constraints (boundaries, min/max duration,
    SAC, value operator), periodic/calendar-frequency (blob) constraints, and
    the pattern-output (value-local-pattern function tree with 1-based
    COMPONENT-INDEX leaves / value-group-blobs aggregate / start-end anchoring
    / start-end-group-blobs / value-global-pattern).
    `referenced_ids` is retained for the closure logic.
    """
    # ---- output allowed-values kind + persistence ----
    allowed = None
    for tag in ("numeric-allowed-values", "boolean-allowed-values",
                "nominal-allowed-values", "ordinal-allowed-values"):
        allowed = root.find(tag)
        if allowed is not None:
            break

    pdef = root.find("pattern-definition")
    # PatternDefinition.ReadXml: relation is OPTIONAL, default All (:117-119).
    relation = (pdef.attrib.get("relation") if pdef is not None else None) or "all"
    kofn = pdef.attrib.get("k-of-n-arg") if pdef is not None else None
    if kofn is None and pdef is not None:
        for k, v in pdef.attrib.items():
            if "kof" in k.replace("-", "").lower():
                kofn = v
                break

    components = []
    if pdef is not None:
        comps = pdef.find("components")
        if comps is not None:
            for idx, comp in enumerate(comps.findall("component"), 1):
                vcs, tc, dc, save_bad = _parse_local_constraints(comp)
                components.append({
                    "id": comp.attrib.get("id"),
                    # PatternDefinition.ALIAS_DEFAULT = "component_%i%"
                    "alias": comp.attrib.get("alias") or "component_%d" % idx,
                    "value_constraints": vcs,
                    "time_constraint": tc,
                    "duration_constraint": dc,
                    "save_bad_values": save_bad,
                    "priorities": _parse_priorities(comp),
                })

    pairwise = []
    pairwise_operator = "and"          # PairwiseConstraints.ReadXml default
    if pdef is not None:
        pc = pdef.find("pairwise-constraints")
        if pc is not None:
            pairwise_operator = pc.attrib.get("logical-operator") or "and"
            lst = pc.find("pairwise-constraints-list")
            if lst is not None:
                for p in lst.findall("pairwise-constraint"):
                    ci = p.find("pairwise-constraint-component-i")
                    cj = p.find("pairwise-constraint-component-j")
                    md = p.find("max-duration")
                    mn = p.find("min-duration")
                    pairwise.append({
                        "operator": p.attrib.get("time-comparison-operator"),
                        "value_operator": p.attrib.get("value-comparison-operator"),
                        # PairwiseConstraint.ReadXml: SAC defaults to TRUE
                        "sac": p.attrib.get("sac"),
                        "i": ci.attrib.get("alias") if ci is not None else None,
                        "j": cj.attrib.get("alias") if cj is not None else None,
                        # PairwiseConstraintComponent.ReadXml: boundary is
                        # optional and defaults to START for BOTH i and j
                        # (the ctor's end/start defaults never apply to XML).
                        "boundary_i": (ci.attrib.get("boundary-point")
                                       if ci is not None else None) or "start",
                        "boundary_j": (cj.attrib.get("boundary-point")
                                       if cj is not None else None) or "start",
                        "min_duration": _parse_gap(mn) if mn is not None else None,
                        "max_duration": _parse_gap(md) if md is not None else None,
                    })

    periodic = None
    if pdef is not None:
        per = pdef.find("periodic-constraints")
        if per is not None:
            cf = per.find("calendar-frequency")
            periodic = {
                "cardinality_min": per.attrib.get("cardinality-min"),
                "cardinality_max": per.attrib.get("cardinality-max"),
                "min_gap_within": _parse_gap(per.find("min-gap-within")),
                "max_gap_within": _parse_gap(per.find("max-gap-within")),
                "min_gap_between": _parse_gap(per.find("min-gap-between")),
                "max_gap_between": _parse_gap(per.find("max-gap-between")),
                "frequency_granularity": cf.attrib.get("frequency-granularity") if cf is not None else None,
                "frequency_value": cf.attrib.get("frequency-value") if cf is not None else None,
                # CalendarFrequency.ReadXml parses last-complete with no null
                # guard -> a missing attribute yields FALSE.
                "last_complete": ((cf.attrib.get("last-complete") or "").lower() == "true"
                                  if cf is not None else False),
                "relative_component_id": cf.attrib.get("relative-component-id") if cf is not None else None,
            }

    # ---- pattern-output ----
    out = {"value_local": None, "value_group_blobs": None,
           "start": None, "end": None,
           # PatternOutput ctor / ReadXml default
           "start_end_group_blobs": "by-granularity",
           "value_global_pattern": None,
           "output_parse_aborted": False}
    po = root.find("pattern-output")
    if po is not None:
        out["start_end_group_blobs"] = (po.attrib.get("start-end-group-blobs")
                                        or "by-granularity")
        vlp = po.find("value-local-pattern")
        aborted = False
        if vlp is not None:
            child = None
            for ch in vlp:
                child = ch
                break
            if child is None:
                pass
            elif child.tag in _VALUE_LOCAL_FUNCTIONS:
                out["value_local"] = _parse_node(child)
            else:
                # PatternOutput.ReadXml throws here and the enclosing try/catch
                # swallows it -> ValueLocalPattern stays null AND
                # start/end-local-pattern + value-group-blobs are never read.
                aborted = True
        out["output_parse_aborted"] = aborted
        if not aborted:
            vgb = po.find("value-group-blobs")
            if vgb is not None:
                out["value_group_blobs"] = _parse_global_function(vgb)
            slp = po.find("start-local-pattern")
            if slp is not None:
                out["start"] = {"alias": slp.attrib.get("alias"),
                                "component_id": slp.attrib.get("component-id"),
                                "boundary_point": slp.attrib.get("boundary-point"),
                                "time_shift": _parse_time_shift(slp.find("time-shift"))}
            elp = po.find("end-local-pattern")
            if elp is not None:
                out["end"] = {"alias": elp.attrib.get("alias"),
                              "component_id": elp.attrib.get("component-id"),
                              "boundary_point": elp.attrib.get("boundary-point"),
                              "time_shift": _parse_time_shift(elp.find("time-shift"))}
            vgp = po.find("value-global-pattern")
            if vgp is not None:
                out["value_global_pattern"] = _parse_global_function(vgp)

    # ---- referenced ids for closure ----
    # ONLY real GesherID references count. In particular the
    # <concept-id-allowed-values id="N"> leaves of value-local-pattern are
    # 1-based COMPONENT INDICES (Pattern.cs:2211), not concept ids -- collecting
    # them would fabricate dependencies on whatever concept happens to own that
    # low id (e.g. id="2" -> RBC).
    refs = set()
    for comp in components:
        if comp.get("id"):
            refs.add(comp["id"])
    for key in ("start", "end"):
        blk = out.get(key)
        if blk and blk.get("component_id"):
            refs.add(blk["component_id"])
    if periodic and periodic.get("relative_component_id"):
        refs.add(periodic["relative_component_id"])
    for x in _derived_from(root):
        refs.add(x)
    refs.discard(root.attrib.get("id"))
    refs.discard(None)

    return {
        "relation": relation,
        "kofn": kofn,
        "components": components,
        "pairwise": pairwise,
        "pairwise_operator": pairwise_operator,
        "periodic": periodic,
        "output": out,
        "output_kind": allowed.tag if allowed is not None else None,
        "output_type": allowed.attrib.get("output-type") if allowed is not None else None,
        "allowed_values": _parse_values(allowed) if allowed is not None else None,
        "persistence": _parse_persistence(allowed),
        "referenced_ids": sorted(refs, key=int),
    }


_ROOT_DISPATCH = {
    "state": ("state", _parse_state),
    "trend": ("trend", _parse_trend),
    "context": ("context", _parse_context),
    "event": ("event", _parse_event),
    "numeric-raw-concept": ("raw-numeric", _parse_raw_numeric),
    "nominal-raw-concept": ("raw-nominal", _parse_raw_nominal),
    "pattern": ("pattern", _parse_pattern),
}


# ---------------------------------------------------------------------------
# top-level parse
# ---------------------------------------------------------------------------

def parse_file(path):
    tree = ET.parse(path)
    root = tree.getroot()
    tag = root.tag
    if tag not in _ROOT_DISPATCH:
        raise ValueError(f"{os.path.basename(path)}: unknown root element <{tag}>")
    op_type, parser = _ROOT_DISPATCH[tag]
    concept = {
        "id": root.attrib.get("id"),
        "name": root.attrib.get("name"),
        "op_type": op_type,               # authoritative, from root element
        "concept_type_attr": root.attrib.get("concept-type"),
        "root_element": tag,
        "file": os.path.basename(path),
        "temporal_semantic": _temporal_semantic(root),
        "derived_from": _derived_from(root),
        "abstraction_at_contexts": _abstraction_at_contexts(root),
        "spec": parser(root),
    }
    concept["depends_on"] = _dependency_ids(concept)
    return concept


def _dependency_ids(concept):
    """All concept ids this concept needs computed before it (dedup, no self)."""
    deps = set(concept.get("derived_from") or [])
    spec = concept.get("spec") or {}
    op = concept["op_type"]

    if op == "state":
        for m in spec.get("mappings") or []:
            _collect_tree_concepts(m.get("tree"), deps)
    elif op == "context":
        for ie in spec.get("inducers") or []:
            if ie.get("id"):
                deps.add(ie["id"])
        for ce in spec.get("clippers") or []:
            if ce.get("id"):
                deps.add(ce["id"])
    elif op == "pattern":
        deps.update(spec.get("referenced_ids") or [])

    for blk in concept.get("abstraction_at_contexts") or []:
        deps.update(blk.get("necessary_contexts") or [])
        deps.update(blk.get("exclusion_contexts") or [])
        deps.update(blk.get("derived_from") or [])
        nsp = blk.get("spec") or {}
        for m in nsp.get("mappings") or []:
            _collect_tree_concepts(m.get("tree"), deps)

    deps.discard(concept.get("id"))
    deps.discard(None)
    return sorted(deps, key=lambda x: int(x) if str(x).isdigit() else 1 << 30)


def _collect_tree_concepts(node, out):
    if not isinstance(node, dict):
        return
    if node.get("type") == "concept" and node.get("id"):
        out.add(node["id"])
    for key in ("operands", "left", "right"):
        val = node.get(key)
        if isinstance(val, list):
            for x in val:
                _collect_tree_concepts(x, out)
        elif isinstance(val, dict):
            _collect_tree_concepts(val, out)


def parse_folder(folder):
    """Parse every concept XML in `folder`.

    The extension match is deliberately case-insensitive and done by hand rather
    than with glob("*.xml"): KB 2700 ships 10 files named `*.XML` (the TREND
    concepts -- ALP_TREND.XML, Blood_Glucose_TREND.XML and friends). On Windows
    glob normcases the pattern so `*.xml` happens to match them, but on Linux it
    would not, and those 10 concepts would vanish from the knowledge base with
    no error at all -- just quietly missing abstractions.
    """
    names = sorted(f for f in os.listdir(folder)
                   if f.lower().endswith(".xml"))
    concepts = {}
    for path in [os.path.join(folder, f) for f in names]:
        c = parse_file(path)
        cid = c["id"]
        if cid in concepts:
            raise ValueError(f"duplicate concept id {cid}: "
                             f"{concepts[cid]['file']} vs {c['file']}")
        concepts[cid] = c
    return concepts


# ---------------------------------------------------------------------------
# closure / pattern-dependency analysis
# ---------------------------------------------------------------------------

def closure(concepts, seeds):
    """Set of ids reachable from `seeds` following depends_on edges."""
    seen = set()
    stack = list(seeds)
    while stack:
        cid = str(stack.pop())
        if cid in seen:
            continue
        seen.add(cid)
        c = concepts.get(cid)
        if c:
            stack.extend(c.get("depends_on") or [])
    return seen


def depends_on_pattern(concepts, cid, _cache=None):
    """True if `cid` transitively depends on any pattern concept."""
    if _cache is None:
        _cache = {}
    cid = str(cid)
    if cid in _cache:
        return _cache[cid]
    _cache[cid] = False  # guard against cycles
    c = concepts.get(cid)
    if not c:
        return False
    if c["op_type"] == "pattern":
        _cache[cid] = True
        return True
    result = any(depends_on_pattern(concepts, d, _cache)
                 for d in (c.get("depends_on") or []))
    _cache[cid] = result
    return result


def pattern_free_outputs(concepts):
    """Ids of non-pattern, non-raw output concepts whose whole closure is
    pattern-free (the Phase-2 gate set)."""
    out = []
    cache = {}
    for cid, c in concepts.items():
        if c["op_type"] in ("pattern", "raw-numeric", "raw-nominal", "event"):
            continue
        if not depends_on_pattern(concepts, cid, cache):
            out.append(cid)
    return sorted(out, key=int)


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------

def build_json(folder, out_path):
    concepts = parse_folder(folder)
    payload = {
        "kb": os.path.basename(os.path.normpath(folder)),
        "concept_count": len(concepts),
        "concepts": concepts,
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    return concepts


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    folder = argv[0]
    out = argv[1] if len(argv) > 1 else os.path.join(
        os.path.dirname(os.path.normpath(folder)) or ".", "tak_2700.json")
    concepts = build_json(folder, out)

    by_type = {}
    for c in concepts.values():
        by_type[c["op_type"]] = by_type.get(c["op_type"], 0) + 1
    print(f"parsed {len(concepts)} concepts -> {out}")
    print("by op_type:", dict(sorted(by_type.items())))

    pf = pattern_free_outputs(concepts)
    print(f"pattern-free output concepts: {len(pf)}")
    pf_types = {}
    for cid in pf:
        t = concepts[cid]["op_type"]
        pf_types[t] = pf_types.get(t, 0) + 1
    print("  by type:", dict(sorted(pf_types.items())))
    full = closure(concepts, pf)
    print(f"pattern-free closure (incl raws/intermediates): {len(full)} concepts")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
