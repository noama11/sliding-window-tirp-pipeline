"""Evaluation-tree + MappingFunction evaluation (State abstraction functions).

Faithful port of the Mediator function evaluators:
  * BusinessEntities\\TAK\\Functions\\MappingFunction.cs
  * BusinessEntities\\TAK\\Functions\\LogicalFunction.cs
  * BusinessEntities\\TAK\\Functions\\ComparisonFunction.cs
  * BusinessEntities\\TAK\\Functions\\MathematicalFunction.cs

A mapping is evaluated against ONE partition: `data` maps a concept id (str) to
the single DataInstance covering the partition; `knowledge` maps a concept id to
its parsed tak concept dict (for output-type resolution).

Three-valued logic (True / False / None) is used throughout, mirroring the .NET
`bool?`.  `None` means "indeterminate / missing".  The .NET pipeline can also
produce the sentinel string ``"ERROR"`` (e.g. a present-but-unparseable value);
we surface that as `ERROR`.  For the clean gate data (every contributing concept
covers its partition with a valid value) comparisons resolve to a definite bool
and the ERROR path is not exercised, but it is implemented for fidelity.

Value canonicalization (verified against C#):
  * Logical / Comparison functions emit the .NET `bool.ToString()` -> "True"/"False".
  * The MappingFunction gate compares against the exact string "True"
    (case-sensitive) and, on a match, substitutes the mapping-2-value `value`
    attribute verbatim (no trimming).
  * String comparisons are OrdinalIgnoreCase; numeric literals parse as double.
"""

from __future__ import annotations

import math

ERROR = "ERROR"          # TAKentity.cs:27  VALUE_ERROR
_NAN = float("nan")


# ---------------------------------------------------------------------------
# concept output-type resolution
# ---------------------------------------------------------------------------
# The evaluation tree needs to know a referenced concept's allowed-value kind to
# decide numeric vs ordinal-rank vs string comparison. Map the tak op_type ->
# the AllowedValues family the .NET code branches on.

def _output_type(concept):
    """'numeric' | 'ordinal' | 'nominal' | 'boolean' | None for a tak concept.

    Mirrors `((Concept)knowledge[id]).AllowedValues.OutputType`: the *declared*
    `output-type` attribute of the concept's `<*-allowed-values>` element, NOT
    the operator family.  KB 2700 has 56 states and 26 patterns whose output is
    numeric, and 41 patterns whose output is boolean — treating those as
    ordinal (the old heuristic) makes every comparison against them NaN.
    """
    if not concept:
        return None
    declared = (concept.get("spec") or {}).get("output_type")
    if declared in ("numeric", "ordinal", "nominal", "boolean"):
        return declared
    op = concept.get("op_type")
    if op == "raw-numeric":
        return "numeric"
    if op == "raw-nominal":
        return "nominal"
    if op == "state":
        return "ordinal"
    if op == "trend":
        return "ordinal"
    if op == "context":
        return "nominal"      # value "True"; contexts carry no allowed-values
    return None


def _ordinal_rank(concept, value):
    """Integer rank (order) of `value` among an ordinal concept's allowed values,
    case-insensitively; None if not found. OrdinalAllowedValues.OrderedValues."""
    if not concept or value is None:
        return None
    spec = concept.get("spec") or {}
    for v in spec.get("allowed_values") or []:
        av = v.get("value")
        if av is not None and av.lower() == str(value).lower():
            try:
                return int(v.get("order"))
            except (TypeError, ValueError):
                return None
    return None


# ---------------------------------------------------------------------------
# numeric representation  (GetNumericalRepresentation)
# ---------------------------------------------------------------------------

def _num_repr(operand, data, knowledge):
    """Operand -> float, NaN if unresolvable. ComparisonFunction/Mathematical
    GetNumericalRepresentation."""
    if not isinstance(operand, dict):
        return _NAN
    t = operand.get("type")
    if t in ("double", "integer"):
        try:
            return float(operand.get("value"))
        except (TypeError, ValueError):
            return _NAN
    if t == "concept":
        cid = operand.get("id")
        di = data.get(cid)
        if di is None:
            return _NAN
        kt = _output_type(knowledge.get(cid))
        if kt == "numeric":
            try:
                return float(di.value)
            except (TypeError, ValueError):
                return _NAN
        if kt == "ordinal":
            rank = _ordinal_rank(knowledge.get(cid), di.value)
            return _NAN if rank is None else float(rank)
        return _NAN
    if t == "math":
        v = _math_value(operand, data, knowledge)
        if v is None or v == ERROR:
            return _NAN
        try:
            return float(v)
        except (TypeError, ValueError):
            return _NAN
    # bare string literal only meaningful vs an ordinal concept (handled in
    # _comparison's string branch); here it has no numeric value.
    return _NAN


# ---------------------------------------------------------------------------
# mathematical-function  (MathematicalFunction.cs)
# ---------------------------------------------------------------------------

def _math_value(node, data, knowledge):
    """-> number string (repr) or ERROR. MathematicalFunction.Calculate."""
    op = node.get("operator")
    l = _num_repr(node.get("left"), data, knowledge)
    r = _num_repr(node.get("right"), data, knowledge)
    if math.isnan(l):
        return ERROR
    try:
        if op == "abs":
            res = abs(l)
        elif op == "ceil":
            res = math.ceil(l)
        elif op == "floor":
            res = math.floor(l)
        elif op == "sqrt":
            res = math.sqrt(l)
        elif op == "trunc":
            res = math.trunc(l)
        elif op == "log":
            res = math.log(l, r)
        elif op == "pow":
            res = math.pow(l, r)
        elif op == "plus":
            res = l + r
        elif op == "minus":
            res = l - r
        elif op == "mult":
            res = l * r
        elif op == "div":
            res = (l / r) if r != 0 else _NAN
        else:
            return ERROR
    except (ValueError, ZeroDivisionError, OverflowError):
        return ERROR
    if isinstance(res, float) and math.isnan(res):
        return ERROR
    # .NET double.ToString() — for whole numbers no trailing .0; mirror loosely.
    if isinstance(res, float) and res.is_integer():
        return str(int(res))
    return str(res)


# ---------------------------------------------------------------------------
# comparison-function  (ComparisonFunction.cs)  -> True | False | None | ERROR
# ---------------------------------------------------------------------------

_NUM_CMP = {
    "bigger": lambda a, b: a > b,
    "smaller": lambda a, b: a < b,
    "bigger-equal": lambda a, b: a >= b,
    "smaller-equal": lambda a, b: a <= b,
    "equal": lambda a, b: a == b,
    "not-equal": lambda a, b: a != b,
}


def _string_cmp(op, value, literal):
    """String equality branch (OrdinalIgnoreCase). value=None -> None."""
    if value is None:
        return None
    if op == "equal":
        return value.lower() == str(literal).lower()
    if op == "not-equal":
        return value.lower() != str(literal).lower()
    return ERROR      # other operators on strings -> ERROR (GetStringComparison)


def _comparison(node, data, knowledge):
    op = node.get("operator")
    L = node.get("left") or {}
    R = node.get("right") or {}

    # branch 1: left concept (nominal/ordinal/BOOLEAN) vs right string literal
    # (ComparisonFunction.cs:125-138 — boolean was added by the 10/20 patch and
    # is what makes `<state> == "True"` over a boolean pattern work).
    if (L.get("type") == "concept"
            and _output_type(knowledge.get(L.get("id"))) in ("nominal", "ordinal", "boolean")
            and R.get("type") == "string"):
        di = data.get(L.get("id"))
        return _string_cmp(op, di.value if di is not None else None, R.get("value"))
    # branch 2: right concept vs left string literal (:141-153 — NOMINAL only)
    if (R.get("type") == "concept"
            and _output_type(knowledge.get(R.get("id"))) == "nominal"
            and L.get("type") == "string"
            and data.get(R.get("id")) is not None):
        di = data.get(R.get("id"))
        return _string_cmp(op, di.value, L.get("value"))

    # numeric branch
    ln = _num_repr(L, data, knowledge)
    rn = _num_repr(R, data, knowledge)
    if math.isnan(ln) or math.isnan(rn):
        return None
    fn = _NUM_CMP.get(op)
    if fn is None:
        return ERROR
    return bool(fn(ln, rn))


# ---------------------------------------------------------------------------
# logical-function  (LogicalFunction.cs)  three-valued
# ---------------------------------------------------------------------------

def _bool_operand(op, data, knowledge):
    """An operand -> True | False | None (GetBooleanRepresentation).

    Inner comparison/logical functions returning ERROR are treated as False
    (LogicalFunction.cs ALEX branch :287-291); a null/indeterminate inner result
    stays None."""
    if not isinstance(op, dict):
        return None
    t = op.get("type")
    if t == "comparison":
        v = _comparison(op, data, knowledge)
        return False if v == ERROR else v
    if t == "logical":
        v = _logical(op, data, knowledge)
        return False if v == ERROR else v
    if t == "concept":
        di = data.get(op.get("id"))
        if di is None:
            return None
        s = str(di.value).strip().lower()
        if s == "true":
            return True
        if s == "false":
            return False
        return None       # Boolean.Parse would throw -> function null
    return None


def _logical(node, data, knowledge):
    """-> True | False | None | ERROR. Three-valued And/Or/Not/KFromN."""
    op = node.get("operator")
    operands = node.get("operands") or []
    vals = [_bool_operand(o, data, knowledge) for o in operands]
    n = len(vals)
    defined = [v for v in vals if v is not None]

    if op == "and":
        if len(defined) == n and n > 0:
            result = all(v is True for v in vals)
        elif any(v is False for v in vals):
            result = False
        else:
            result = None
    elif op == "or":
        if any(v is True for v in vals):
            result = True
        elif len(defined) == n and n > 0:
            result = False
        else:
            result = None
    elif op == "not":
        v0 = vals[0] if vals else None
        result = (not v0) if v0 is not None else None
    elif op == "k-from-n":
        try:
            k = int(node.get("argument"))
        except (TypeError, ValueError):
            k = 1
        trues = sum(1 for v in vals if v is True)
        falses = sum(1 for v in vals if v is False)
        if trues >= k:
            result = True
        elif falses >= (n - k):
            result = False
        else:
            result = None
    else:
        result = None
    return result


# ---------------------------------------------------------------------------
# evaluation-tree entry  (wrapped LogicalFunction(And,[tree]) semantics)
# ---------------------------------------------------------------------------

def eval_tree(tree, data, knowledge):
    """Evaluate a mapping evaluation-tree to True | False | None.

    The .NET MappingFunction wraps the tree in LogicalFunction(And,[tree]); the
    And of a single operand equals that operand's boolean representation, in
    which an ERROR / indeterminate inner result becomes non-True.  We therefore
    return the operand's three-valued boolean and the mapping gate matches only
    on exact True.
    """
    return _bool_operand(tree, data, knowledge)


# ---------------------------------------------------------------------------
# MappingFunction  (MappingFunction.cs)
# ---------------------------------------------------------------------------

def evaluate_mapping(mappings, rank_selection, data, knowledge):
    """Return the winning mapping value string, or None if none matched.

    `mappings` = list of {order, value, tree}.  `rank_selection` = "min"/"max"
    (default min).  Entries are sorted by order (asc for min, desc for max) and
    the FIRST whose tree evaluates to True wins (MappingFunction.cs:94-114).
    """
    reverse = (str(rank_selection).lower() == "max")

    def _order(m):
        try:
            return int(m.get("order"))
        except (TypeError, ValueError):
            return 0

    for m in sorted(mappings, key=_order, reverse=reverse):
        if eval_tree(m.get("tree"), data, knowledge) is True:
            return m.get("value")      # verbatim, no trim
    return None


# ---------------------------------------------------------------------------
# Pattern value-local-pattern  (Pattern.cs:2204-2213 -> the function's
# Calculate(Dictionary<long,DataInstance>, knowledge) with data keyed by the
# 1-based COMPONENT INDEX)
# ---------------------------------------------------------------------------

def _dotnet_double_str(x):
    """`Double.ToString()` — no trailing `.0` for whole numbers, shortest
    round-trip otherwise (matches .NET Core's default format)."""
    if isinstance(x, float) and x.is_integer() and abs(x) < 1e16:
        return str(int(x))
    return repr(float(x))


def _pattern_num(operand, data, missing):
    """MathematicalFunction.GetNumericalRepresentation (MathematicalFunction.cs
    :256-297) under pattern keying.

    Returns a float, `float('nan')`, or None (the C# `Double?` null).  The
    knowledge-based type check is COMMENTED OUT in the source, so a
    `<concept-id-allowed-values id="N">` leaf is resolved purely as
    `Double.TryParse(data[N].Value)` — N being the component index.
    """
    if operand is None or not isinstance(operand, dict):
        return missing
    t = operand.get("type")
    if t in ("double", "integer"):
        try:
            return float(operand.get("value"))
        except (TypeError, ValueError):
            return _NAN
    if t == "concept":
        di = data.get(str(operand.get("id")))
        if di is None:
            return missing
        try:
            return float(di.value)
        except (TypeError, ValueError):
            return _NAN
    if t == "math":
        inner = calculate_pattern_value_local(operand, data)
        if inner is None:
            return None
        try:
            return float(inner)
        except (TypeError, ValueError):
            return _NAN
    return None


def _pattern_math_apply(op, l, r):
    """MathematicalFunction.Calculate(Double?,Double?) (:178-250). Unary
    operators need only `left`; binary operators require BOTH operands to be
    non-null and non-NaN, otherwise the result is null."""
    def has(x):
        return x is not None and not math.isnan(x)

    try:
        if op == "abs":
            return abs(l) if l is not None else None
        if op == "ceil":
            return float(math.ceil(l)) if l is not None else None
        if op == "floor":
            return float(math.floor(l)) if l is not None else None
        if op == "sqrt":
            return math.sqrt(l) if l is not None else None
        if op == "trunc":
            return float(math.trunc(l)) if l is not None else None
        if op == "log":
            return math.log(l, r) if has(l) and has(r) else None
        if op == "pow":
            return math.pow(l, r) if has(l) and has(r) else None
        if op == "plus":
            return l + r if has(l) and has(r) else None
        if op == "minus":
            return l - r if has(l) and has(r) else None
        if op == "mult":
            return l * r if has(l) and has(r) else None
        if op == "div":
            if not (has(l) and has(r)):
                return None
            return (l / r) if r != 0 else _NAN
    except (ValueError, ZeroDivisionError, OverflowError):
        return _NAN
    return None


def calculate_pattern_value_local(node, data_by_index):
    """Evaluate a pattern's `<value-local-pattern>` for one blob.

    `data_by_index` maps the 1-based component index (as a **str**) to that
    component's DataInstance in the blob.  Returns the value string, or None
    when the .NET function would have returned a null DataInstance (the caller
    then emits `UNDEF`, Pattern.cs:2212).

    Only MathematicalFunction is supported, which is exhaustive for KB 2700
    (12 `abs` + 2 `minus`).  A Comparison/Logical/Mapping value-local would
    resolve its `knowledge[id]` lookups against the GesherID-keyed knowledge
    dict while `data` is index-keyed, so every leaf misses and the .NET code
    returns null — which is exactly the None we return here.
    """
    if not isinstance(node, dict):
        return None
    if node.get("type") != "math":
        return None
    missing = node.get("missing_concept_def_value")
    missing = float(missing) if missing not in (None, "") else None
    l = _pattern_num(node.get("left"), data_by_index, missing)
    r = _pattern_num(node.get("right"), data_by_index, missing)
    if (l is not None and math.isnan(l)) or (r is not None and math.isnan(r)):
        result = _NAN
    else:
        result = _pattern_math_apply(node.get("operator"), l, r)
        if result is None:
            return None                      # null DataInstance -> UNDEF
    return ERROR if math.isnan(result) else _dotnet_double_str(result)
