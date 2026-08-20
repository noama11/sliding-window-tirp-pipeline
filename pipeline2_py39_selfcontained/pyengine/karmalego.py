"""
Pure-Python KarmaLego TIRP miner (stdlib only, pasteable as text).

A faithful reimplementation of the .NET KarmaLego CSV engine
(KarmaLego_CSV / KarmaLegoCore, VersionType=Generic) for the stroke pipeline.
It mines Time-Interval Related Patterns (TIRPs) from raw_events + abstractions
and writes the per-patient HorizontalSupport matrix (results.csv), byte-compatible
with the .NET engine's output (order-insensitive on rows and columns).

Configuration reproduced exactly (see PLAN.md / PHASE1_PROMPT.txt):
    RelationsSet = Three, SACtype = CSAC, MaxLegoLevel = 7,
    mvs = 0.2, maxGap = 0, timeUnit = Days, statistics = HorizontalSupport.

Because epsilon = 0 and maxGap = 0, the guard `es >= maxGap` (es = second.Start -
first.End) rejects every non-overlapping pair, so the `Before`/`Meets` relations are
unreachable: only `Overlaps` and `Contain` ever occur. CSAC's backwards-SAC check
applies only to `Before` pairs and is therefore inert here.

Ported from (read-only C# source, do NOT modify):
    KarmaLegoCore/BO/KarmaLego/KL.BO.KarmaLegoDFSBO.cs   (Karma + Lego)
    KarmaLegoCore/BO/KarmaLego/KL.BO.KarmaLegoBaseBO.cs  (NeedFilter, transitive fill)
    KarmaLegoCore/BO/KL.BO.RelationsLogicBO.cs           (RelationsLogic3BO.GetRelation)
    KarmaLegoCore/BO/DataServices/DataAccessCsv.cs       (raw union abstractions loading)
    KarmaLegoCore/BO/KL.BO.EntityManagerBO.cs            (record construction)
    KarmaLegoConsoleApp/Versions/GenericVersion.cs       (driver + results.csv)
    KarmaLegoCore/DS/*                                    (ordering, name building)

This module is equivalence-preserving, not a line-by-line transcription: the
comparator (csv_mode/_prep/compare_results.py) is order-insensitive and checks only
the pattern-name set, the patient set, and each integer cell, so idiomatic Python
data structures are used while every mining decision matches the C# semantics.
"""

import argparse
import csv
import os
import sys
from datetime import datetime

# ---------------------------------------------------------------------------
# Fixed configuration for the stroke run (matches engine appsettings + kl config)
# ---------------------------------------------------------------------------
TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S"
DEFAULT_MVS = 0.2
DEFAULT_MAX_GAP = 0            # in `timeUnit` (Days); epsilon is 0 everywhere
DEFAULT_MAX_LEGO_LEVEL = 7     # = number of components (symbols) in a TIRP


# ---------------------------------------------------------------------------
# Record model
# ---------------------------------------------------------------------------
class Rec:
    """One time-interval instance for a patient. `symbol` is the canonical
    "ConceptName:Value" once matched to a knowledge-table allowed value (or None
    if the record's value matched no allowed value of its concept)."""
    __slots__ = ("name", "value", "start", "end", "symbol", "concept_id", "lidx")

    def __init__(self, name, value, start, end, concept_id):
        self.name = name
        self.value = value            # canonical value (or raw if unmatched)
        self.start = start
        self.end = end
        self.concept_id = concept_id
        self.symbol = None            # set to "name:value" when it survives matching


def _sort_key(r):
    # EntityRecordDS.CompareTo: Start asc, then End asc, then ordinal "Name:Value".
    # Python str comparison is by code point == C# StringComparison.Ordinal for this data.
    return (r.start, r.end, r.name + ":" + r.value)


# ---------------------------------------------------------------------------
# Allen relations -- RelationsLogic3BO.GetRelation with epsilon = 0, maxGap = 0
# ---------------------------------------------------------------------------
def relation(a, b):
    """Temporal relation of interval `a` vs `b` in the canonical earlier->later
    direction. Returns 'Contain', 'Overlaps', or None (NoRelation).

    Deltas are computed in seconds; since epsilon = maxGap = 0 only their sign
    matters, and timestamps are second-resolution so signs are exact.
    Mirrors RelationsLogic3BO.GetRelation (Three model) branch-for-branch.
    """
    # guard: first.CompareTo(second) >= 0  ->  NoRelation
    if _cmp(a, b) >= 0:
        return None
    es = (b.start - a.end).total_seconds()      # second.Start - first.End
    if es >= 0:                                 # es >= maxGap(0)  -> NoRelation
        return None
    # es < 0 (intervals overlap). Before / Meets branches are unreachable here.
    ss = (b.start - a.start).total_seconds()    # second.Start - first.Start
    ee = (b.end - a.end).total_seconds()        # second.End   - first.End
    if ss > 0:
        if (a.end - b.start).total_seconds() > 0 and ee > 0:
            return "Overlaps"
        if ee == 0:                             # FinishBy -> Contain
            return "Contain"
        if (a.end - b.end).total_seconds() > 0:  # Contain
            return "Contain"
        return None                             # ss>0, no sub-branch -> NoRelation
    elif ee > 0:                                # StartBy -> Contain
        return "Contain"
    if ss <= 0 and ee == 0:                     # Equal -> Contain
        return "Contain"
    return None


# RelationsLogic3BO transitive table (row = rel(m, m+1), col = rel(m+1, new)):
# the set of relations rel(m, new) may take. Candidate generation restricts the
# new component's relation to earlier components to this closure; the .NET Lego
# then keeps only instances whose ACTUAL relation matches. So an extension is
# valid iff actual_rel(m,new) lies in this set for every earlier component m.
# Faithfully lossy: e.g. Contain o Contain = {Contain} prunes a geometrically
# real Overlaps, and Overlaps o Overlaps = {Before,Overlaps} prunes a real Contain.
TRANSITIVE = {
    "Before":  {"Before": {"Before"},
                "Contain": {"Before"},
                "Overlaps": {"Before"}},
    "Contain": {"Before": {"Before", "Overlaps", "Contain"},
                "Contain": {"Contain"},
                "Overlaps": {"Overlaps", "Contain"}},
    "Overlaps": {"Before": {"Before"},
                 "Contain": {"Before", "Overlaps", "Contain"},
                 "Overlaps": {"Before", "Overlaps"}},
}


def _cmp(a, b):
    if a.start != b.start:
        return -1 if a.start < b.start else 1
    if a.end != b.end:
        return -1 if a.end < b.end else 1
    ka, kb = a.name + ":" + a.value, b.name + ":" + b.value
    if ka == kb:
        return 0
    return -1 if ka < kb else 1


# ---------------------------------------------------------------------------
# Pattern-name construction -- PatternRelationPairDS.GetName / PatternDS title
# ---------------------------------------------------------------------------
# C# PatternDS builds a size>=2 name as String.Join("", pairStrings.Sorted()).
# List<string>.Sort() uses the culture-aware default comparer, NOT ordinal, and
# that comparer is MULTI-LEVEL: it compares base letters across the whole string
# first and only falls back to case when the strings are otherwise equal.
#
# So the key has two levels:
#   primary   -- case-folded, ordered space < '.' < digits < '@' < '_' < ':' < a-z
#   secondary -- case, lowercase before uppercase, and ONLY as a tiebreak
#
# Collapsing those into one level (weighting a<A<b<B<... per character) gives the
# same answer whenever the strings differ in a letter, which is why the frozen
# Phase-1 reference could not tell the two models apart -- all 29,831 multi-pair
# names there are consistent with both. It is wrong as soon as two pair strings
# differ in case at one position and in letter at a later one, e.g.
#   @@Pair:ALT_Level_State:Normal@Contain@...
#   @@Pair:aPTT_Level_State:Normal@Contain@...
# where single-level puts aPTT first ('a'<'A') but .NET compares A/a as equal and
# decides on L<P. Verified against a live .NET run: two-level reproduces
# 1344/1344 and 359/359 multi-pair names, single-level 1283 and 357.
# (Ordinal sort -- Python's default -- reorders ~13k of the 30,722.)
_COLLATION_ORDER = " ." + "0123456789" + "@_:" + "abcdefghijklmnopqrstuvwxyz"
_COLLATION_WEIGHT = {ch: i for i, ch in enumerate(_COLLATION_ORDER)}


def _collate_key(s, _w=_COLLATION_WEIGHT, _cache={}):
    k = _cache.get(s)
    if k is None:
        low = s.lower()
        k = (tuple(_w.get(ch, 1000 + ord(ch)) for ch in low),
             tuple(0 if ch == orig else 1 for ch, orig in zip(low, s)))
        _cache[s] = k
    return k


def pair_name(sym_first, rel, sym_second):
    # Non-Before form (Before never occurs at maxGap=0): "@@Pair:F@REL@S".
    return "@@Pair:" + sym_first + "@" + rel + "@" + sym_second


def tirp_name(recs, pair_str):
    """Canonical TIRP name = culture-collated concatenation of every pairwise
    @@Pair: string. `recs` are the instance records in sorted (component) order;
    `pair_str` maps a record-index pair to its precomputed pair string."""
    n = len(recs)
    parts = []
    idxs = [r.lidx for r in recs]
    for x in range(n):
        for y in range(x + 1, n):
            parts.append(pair_str[(idxs[x], idxs[y])])
    parts.sort(key=_collate_key)
    return "".join(parts)


# ---------------------------------------------------------------------------
# CSV loading -- DataAccessCsv + EntityManagerBO.ReadByConceptFromWS
# ---------------------------------------------------------------------------
def _read_csv_rows(path):
    """Yield data rows (header skipped) using the stdlib quote-aware reader,
    matching DataAccessCsv.SplitCsvLine for quoted cells like "DEC, SAME, INC"."""
    with open(path, newline="", encoding="utf-8") as fh:
        r = csv.reader(fh)
        next(r, None)  # header
        for row in r:
            if row:
                yield row


def load_knowledge(folder):
    """knowledge_table.csv -> {name: (concept_id, {lower_value: canonical_value})}.
    Concepts with NumericAllowedValues/DateTime contribute no symbol values
    (DataAccessCsv.GetConcepts skips them)."""
    path = os.path.join(folder, "knowledge_table.csv")
    knowledge = {}
    for row in _read_csv_rows(path):
        if len(row) < 3:
            continue
        name, cid_str, allowed = row[0], row[1], row[2]
        cid = int(cid_str)
        values = {}
        if allowed not in ("NumericAllowedValues", "DateTime"):
            for v in allowed.split(","):
                canon = v.strip()
                if canon == "":
                    continue
                values.setdefault(canon.lower(), canon)
        knowledge[name] = (cid, values)
    return knowledge


def load_entities(folder, cohort_ids, concept_filter):
    """Build per-entity sorted record lists from raw_events UNION abstractions,
    filtered to the cohort, for concepts present in the knowledge table.

    Returns (entities, n_all) where entities = {pid: [Rec,...]} (only patients with
    >=1 loaded record) and n_all = len(entities) (the Karma MVS denominator).

    `concept_filter` is "*" (all knowledge concepts) or a set of concept ids.
    Records are NOT deduplicated (mirrors the unique-index EntityDS.Add semantics).
    """
    knowledge = load_knowledge(folder)
    cohort = set(str(c) for c in cohort_ids)

    entities = {}
    for fname in ("raw_events.csv", "abstractions.csv"):
        path = os.path.join(folder, fname)
        if not os.path.exists(path):
            continue
        for row in _read_csv_rows(path):
            if len(row) < 5:
                continue
            pid, name, start_s, end_s, value = row[0], row[1], row[2], row[3], row[4]
            if pid not in cohort:
                continue
            if name not in knowledge:
                continue
            cid, values = knowledge[name]
            if concept_filter != "*" and cid not in concept_filter:
                continue
            start = datetime.strptime(start_s, TIMESTAMP_FMT)
            end = datetime.strptime(end_s, TIMESTAMP_FMT)
            rec = Rec(name, value.strip(), start, end, cid)
            # canonical value + symbol (case-insensitive match, then relabel)
            canon = values.get(rec.value.lower())
            if canon is not None:
                rec.value = canon
                rec.symbol = name + ":" + canon
            entities.setdefault(pid, []).append(rec)

    for pid in entities:
        entities[pid].sort(key=_sort_key)
    return entities, len(entities)


# ---------------------------------------------------------------------------
# Karma + Lego
# ---------------------------------------------------------------------------
def mine(entities, n_all, mvs=DEFAULT_MVS, max_level=DEFAULT_MAX_LEGO_LEVEL,
         progress=None):
    """Run Karma (size 1-2) + Lego (DFS to `max_level`).

    Returns (results, kept_entity_ids) where results is
    {pattern_name: {pid: horizontal_support_int}} for every frequent TIRP of
    size 2..max_level, and kept_entity_ids is the FILTERED entity set -- the
    patients that still hold at least one surviving-symbol record after the
    size-1 MVS cut. That set, not the loaded set, is what GenericVersion writes
    a results.csv row for (it iterates `kl.Entities`, which NeedFilter has
    already pruned)."""

    def log(msg):
        if progress:
            progress(msg)

    # -- Karma size-1: vertical support of each symbol, MVS filter -----------
    sym_entities = {}   # symbol -> set(pid) with >=1 record
    for pid, recs in entities.items():
        seen = set()
        for r in recs:
            if r.symbol is not None:
                seen.add(r.symbol)
        for s in seen:
            sym_entities.setdefault(s, set()).add(pid)

    surviving_symbols = {s for s, ents in sym_entities.items()
                         if len(ents) / n_all >= mvs}
    needed_concept_ids = set()

    # -- Build filtered per-entity records (surviving symbols only) ----------
    # Assign a per-entity local index (lidx) used as record identity.
    fentities = {}      # pid -> [Rec] surviving-symbol records, sorted, lidx set
    for pid, recs in entities.items():
        keep = [r for r in recs if r.symbol in surviving_symbols]
        for i, r in enumerate(keep):
            r.lidx = i
        if keep:
            fentities[pid] = keep
    # neededConcepts = concept ids of surviving symbols
    for pid, recs in entities.items():
        for r in recs:
            if r.symbol in surviving_symbols:
                needed_concept_ids.add(r.concept_id)

    # n_filt = #entities with >=1 record whose concept is in neededConcepts
    n_filt = 0
    for pid, recs in entities.items():
        if any(r.concept_id in needed_concept_ids for r in recs):
            n_filt += 1

    log("Karma: %d surviving size-1 symbols (of %d); n_all=%d n_filt=%d"
        % (len(surviving_symbols), len(sym_entities), n_all, n_filt))

    # -- Precompute per-entity real relations + pair strings -----------------
    # rels[pid][(i, j)] = "Contain"/"Overlaps" for i < j real relations
    # pstr[pid][(i, j)] = "@@Pair:..." string for that pair
    rels = {}
    pstr = {}
    for pid, recs in fentities.items():
        rr = {}
        ps = {}
        n = len(recs)
        for i in range(n):
            ri = recs[i]
            for j in range(i + 1, n):
                rj = recs[j]
                rel = relation(ri, rj)
                if rel is not None:
                    rr[(i, j)] = rel
                    ps[(i, j)] = pair_name(ri.symbol, rel, rj.symbol)
        rels[pid] = rr
        pstr[pid] = ps

    # -- Karma size-2: candidate pairs, MVS filter (denominator = n_all) -----
    pair2_ents = {}     # name -> set(pid)
    pair2_inst = {}     # name -> {pid: [(i, j), ...]}
    for pid, recs in fentities.items():
        ps = pstr[pid]
        local = {}
        for (i, j), name in ps.items():
            local.setdefault(name, []).append((i, j))
        for name, insts in local.items():
            pair2_ents.setdefault(name, set()).add(pid)
            pair2_inst.setdefault(name, {})[pid] = insts

    frequent2 = {name for name, ents in pair2_ents.items()
                 if len(ents) / n_all >= mvs}
    log("Karma: %d frequent size-2 patterns" % len(frequent2))

    # -- Build the extension index from FREQUENT size-2 pairs ----------------
    # freq_next[pid][last_lidx] = [j, ...] where (last, j) is a frequent pair.
    freq_next = {}
    for name in frequent2:
        for pid, insts in pair2_inst[name].items():
            fn = freq_next.setdefault(pid, {})
            for (i, j) in insts:
                fn.setdefault(i, []).append(j)

    # -- Results accumulator -------------------------------------------------
    # results[name] = {pid: horizontal_support}
    results = {}

    # level-2 frequent patterns -> instances as tuples of Rec, seed the DFS
    # current[name] = {pid: [ (rec, rec), ... ]}
    current = {}
    for name in frequent2:
        pmap = {}
        for pid, insts in pair2_inst[name].items():
            recs = fentities[pid]
            pmap[pid] = [(recs[i], recs[j]) for (i, j) in insts]
        current[name] = pmap
        results[name] = {pid: len(insts) for pid, insts in pmap.items()}

    log("Level 2: %d patterns" % len(frequent2))

    # -- Lego: extend level by level up to max_level -------------------------
    level = 2
    while level < max_level and current:
        nxt = {}                    # name -> {pid: [tuple(Rec,...)]}
        nxt_ents = {}               # name -> set(pid)
        for pname, pmap in current.items():
            for pid, insts in pmap.items():
                recs = fentities[pid]
                rr = rels[pid]
                ps = pstr[pid]
                fn = freq_next.get(pid, {})
                for inst in insts:
                    last_lidx = inst[-1].lidx
                    nexts = fn.get(last_lidx)
                    if not nexts:
                        continue
                    L = len(inst)
                    for j in nexts:
                        # j > last (frequent pair guarantees last < j) -> new max.
                        # rel(last, j) is the adjacent (frequent) relation.
                        rel_to_new = {last_lidx: rr[(last_lidx, j)]}
                        valid = True
                        # constrain rel(m, new) for earlier components, top-down,
                        # to the transitive closure of the adjacent-relation chain.
                        for pos in range(L - 2, -1, -1):
                            rm = inst[pos].lidx
                            a = rr.get((rm, j))          # actual rel(m, new)
                            if a is None:
                                valid = False
                                break
                            first_rel = rr[(rm, inst[pos + 1].lidx)]
                            second_rel = rel_to_new[inst[pos + 1].lidx]
                            if a not in TRANSITIVE[first_rel][second_rel]:
                                valid = False
                                break
                            rel_to_new[rm] = a
                        if not valid:
                            continue
                        new_inst = inst + (recs[j],)
                        name = tirp_name(new_inst, ps)
                        d = nxt.get(name)
                        if d is None:
                            d = {}
                            nxt[name] = d
                            nxt_ents[name] = set()
                        d.setdefault(pid, []).append(new_inst)
                        nxt_ents[name].add(pid)

        # MVS filter this level (denominator = n_filt for size >= 3)
        keep = {}
        for name, ents in nxt_ents.items():
            if len(ents) / n_filt >= mvs:
                keep[name] = nxt[name]
        level += 1
        for name, pmap in keep.items():
            results[name] = {pid: len(insts) for pid, insts in pmap.items()}
        log("Level %d: %d patterns" % (level, len(keep)))
        current = keep

    return results, set(fentities)


# ---------------------------------------------------------------------------
# Output -- GenericVersion results.csv writer
# ---------------------------------------------------------------------------
def write_results(path, results, entity_ids):
    """Write id,<pattern>,... matrix. One row per entity; cell = HorizontalSupport
    integer; missing pattern -> 0. Column/row order is irrelevant to the comparator."""
    # Column order is irrelevant to the comparator, but a stable order makes runs
    # byte-reproducible (the .NET engine's own column order is nondeterministic).
    pattern_names = sorted(results.keys(), key=_collate_key)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        fh.write("id," + ",".join(pattern_names) + "\n")
        for pid in entity_ids:
            cells = [str(pid)]
            for name in pattern_names:
                cells.append(str(results[name].get(pid, 0)))
            fh.write(",".join(cells) + "\n")


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
def resolve_concepts(folder, concepts, progress=None):
    """Turn a concept selection into the id set load_entities filters on.

    Accepts "*", numeric ids, or CONCEPT NAMES. Names are strongly preferred:
    ConceptID is a property of whichever knowledge_table.csv is in play, and
    those ids have already drifted once -- the shipped table and the TAK disagree
    on the whole `*_Level_State` family (2000 is HGB in one and ALP in the
    other), so an id list silently selects different concepts depending on which
    table it is resolved against. Names are stable across both.

    Unresolvable entries are reported rather than silently ignored; a typo would
    otherwise just quietly shrink the mined feature set.
    """
    if concepts == "*":
        return "*"
    knowledge = load_knowledge(folder)
    by_name = {n: cid for n, (cid, _v) in knowledge.items()}
    by_name_lower = {n.lower(): cid for n, cid in by_name.items()}
    known_ids = set(by_name.values())

    out, missing = set(), []
    for c in concepts:
        c = str(c).strip()
        if not c:
            continue
        if c.isdigit():
            cid = int(c)
            (out.add(cid) if cid in known_ids else missing.append(c))
            continue
        cid = by_name.get(c, by_name_lower.get(c.lower()))
        if cid is None:
            missing.append(c)
        else:
            out.add(cid)
    if missing:
        msg = ("WARNING: %d requested concept(s) are not in knowledge_table.csv "
               "and will NOT be mined: %s" % (len(missing), ", ".join(missing)))
        (progress or (lambda m: print(m, file=sys.stderr)))(msg)
    return out


def run_karmalego(data_folder, cohort_ids, out_path, mvs=DEFAULT_MVS,
                  max_level=DEFAULT_MAX_LEGO_LEVEL, concepts="*", progress=None):
    """Mine the cohort from a CSV data folder and write results.csv.

    data_folder: dir with raw_events.csv, abstractions.csv, knowledge_table.csv,
                 projects.csv (the DataAccessCsv contract).
    cohort_ids:  iterable of patient ids.
    concepts:    "*" for all knowledge concepts, or an iterable of concept ids.
    """
    concept_filter = resolve_concepts(data_folder, concepts, progress=progress)
    entities, n_all = load_entities(data_folder, cohort_ids, concept_filter)
    results, kept = mine(entities, n_all, mvs=mvs, max_level=max_level,
                         progress=progress)
    # Row set = every cohort patient the size-1 MVS filter KEPT, not every
    # patient that had data. GenericVersion writes one row per entity in
    # kl.Entities, and NeedFilter has already dropped the patients left with no
    # surviving-symbol record. Using the loaded set instead emitted an all-zero
    # row for each of those (75 spurious rows in a 393-patient cohort).
    entity_ids = [str(c) for c in cohort_ids if str(c) in kept]
    write_results(out_path, results, entity_ids)
    return results, entity_ids


def _parse_cohort(entities_arg):
    if os.path.exists(entities_arg):
        with open(entities_arg, encoding="utf-8") as fh:
            text = fh.read()
        ids = [t.strip() for t in text.replace("\n", ",").split(",") if t.strip()]
        return ids
    return [t.strip() for t in entities_arg.split(",") if t.strip()]


def main(argv=None):
    p = argparse.ArgumentParser(description="Pure-Python KarmaLego TIRP miner")
    p.add_argument("--data", required=True,
                   help="folder with raw_events.csv, abstractions.csv, "
                        "knowledge_table.csv, projects.csv")
    p.add_argument("--entities", required=True,
                   help="cohort ids: path to cohort_ids.txt or comma-separated list")
    p.add_argument("--out", required=True, help="output results.csv path")
    p.add_argument("--mvs", type=float, default=DEFAULT_MVS)
    p.add_argument("--max-level", type=int, default=DEFAULT_MAX_LEGO_LEVEL)
    p.add_argument("--concepts", default="*",
                   help='"*" (all) or comma-separated concept ids')
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    cohort = _parse_cohort(args.entities)
    concepts = "*" if args.concepts.strip() == "*" else args.concepts.split(",")
    progress = None if args.quiet else (lambda m: print(m, file=sys.stderr))

    results, entity_ids = run_karmalego(
        args.data, cohort, args.out, mvs=args.mvs, max_level=args.max_level,
        concepts=concepts, progress=progress)
    if not args.quiet:
        print("wrote %d patterns x %d patients -> %s"
              % (len(results), len(entity_ids), args.out), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
