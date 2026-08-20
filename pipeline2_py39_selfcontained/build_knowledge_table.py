"""Regenerate data/knowledge_table.csv from the TAK knowledge base.

`knowledge_table.csv` is the concept dictionary BOTH engines use to turn a
(ConceptName, Value) row into a mineable symbol, and to resolve the concept-id
list in config.json. If it disagrees with the knowledge base, KarmaLego silently
mines the wrong set of concepts -- no error, just fewer or different patterns.

The shipped table did disagree: 10 concepts (the whole `*_Level_State` family)
carried a different ConceptID than the TAK, 61 names were not TAK entities at
all, and many real concepts were missing outright. Because the config's concept
list is written in TAK ids, asking for 68 stroke concepts actually selected 30.

This script rebuilds the table straight from tak_entities/<kb>/*.xml so ids and
allowed values cannot drift again.

    python build_knowledge_table.py --dry-run    # report the diff, write nothing
    python build_knowledge_table.py              # rewrite, keeping a .bak
    python build_knowledge_table.py --tak-only   # drop non-TAK rows entirely

Non-TAK rows: the old table carried 61 names the KB does not define. Some are
real raw columns present in the data (drug dosage parameters, MCV, MPV); the
rest are leftovers from another KB revision. By default the ones that actually
occur in raw_events.csv are carried over -- dropping them would silently make
data unmineable -- and the dead ones are reported and dropped.

AllowedValues follows what DataAccessCsv.GetConcepts expects:
    raw-numeric, or a pattern with no symbolic output -> NumericAllowedValues
    everything else                                   -> its declared values
Values reading exactly "true"/"false" are written "True"/"False": that is what
the Mediator actually emits, and the value's spelling here becomes the mined
symbol name (`Visit_pattern:True`), so it has to match.
"""

import argparse
import csv
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "pyengine", "mediator"))

import tak_parse   # noqa: E402

NUMERIC = "NumericAllowedValues"


def _cfg():
    with open(os.path.join(HERE, "config.json"), encoding="utf-8-sig") as f:
        return json.load(f)


def _kb_id(data_dir, project_id):
    for cand in (os.path.join(data_dir, "projects.csv"),
                 os.path.join(HERE, "data", "projects.csv")):
        if os.path.exists(cand):
            with open(cand, encoding="utf-8-sig", newline="") as f:
                r = csv.reader(f)
                next(r, None)
                for row in r:
                    if len(row) >= 3 and row[0].strip() == str(project_id):
                        return row[2].strip()
    sys.exit(f"ERROR: project {project_id} not found in projects.csv")


def load_tak(data_dir, project_id):
    kb = _kb_id(data_dir, project_id)
    kb_dir = os.path.join(HERE, "tak_entities", kb)
    if os.path.isdir(kb_dir):
        return kb, tak_parse.parse_folder(kb_dir), f"tak_entities/{kb}"
    pre = os.path.join(HERE, f"tak_{kb}.json")
    if os.path.exists(pre):
        with open(pre, encoding="utf-8") as f:
            return kb, json.load(f)["concepts"], os.path.basename(pre)
    sys.exit(f"ERROR: no tak_entities/{kb}/ and no tak_{kb}.json")


def allowed_values(concept):
    """The AllowedValues cell for one concept."""
    spec = concept.get("spec") or {}
    vals = [v.get("value") for v in (spec.get("allowed_values") or [])
            if v.get("value") is not None]
    op = concept["op_type"]

    if op == "raw-numeric":
        return NUMERIC
    if op == "trend":
        # NOT the XML's Decreasing/Stable/Increasing. The Mediator writes the
        # .NET enum member names (GradientTrendValues.X.ToString()), so a trend
        # row's Value is "Dec"/"Same"/"Inc" -- see trend.py:24. The old table
        # had it right for PLT_TREND and wrong for the other 18, which is why
        # e.g. every HGB_TREND row was being silently discarded.
        return "Dec, Same, Inc"
    if op in ("context", "event"):
        # Neither declares values in the XML; both are boolean occurrences and
        # the Mediator writes "True".
        return "True, False"
    if not vals:
        # A pattern (or anything else) with no symbolic output is numeric.
        return NUMERIC
    out = ["True" if v.lower() == "true" else "False" if v.lower() == "false" else v
           for v in vals]
    seen, uniq = set(), []
    for v in out:
        if v.lower() not in seen:
            seen.add(v.lower())
            uniq.append(v)
    return ", ".join(uniq)


def read_table(path):
    rows = {}
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            rows[r["ConceptName"].strip()] = (r["ConceptID"].strip(),
                                              r["AllowedValues"])
    return rows


def names_in_data(data_dir):
    """Concept names that actually occur in raw_events.csv."""
    path = os.path.join(data_dir, "raw_events.csv")
    if not os.path.exists(path):
        return None
    seen = set()
    with open(path, encoding="utf-8-sig", newline="") as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            if len(row) > 1:
                seen.add(row[1].strip())
    return seen


def main():
    cfg = _cfg()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=cfg.get("data", {}).get("data_dir", "data"),
                    help="folder holding raw_events.csv / projects.csv / the table")
    ap.add_argument("--out", help="output path (default: <data-dir>/knowledge_table.csv, "
                                  "falling back to the bundled data/)")
    ap.add_argument("--tak-only", action="store_true",
                    help="do not carry over non-TAK rows, even ones present in the data")
    ap.add_argument("--dry-run", action="store_true", help="report the diff, write nothing")
    args = ap.parse_args()

    data_dir = args.data_dir if os.path.isabs(args.data_dir) else os.path.join(HERE, args.data_dir)
    out = args.out or os.path.join(data_dir, "knowledge_table.csv")
    if not os.path.exists(out) and not args.out:
        out = os.path.join(HERE, "data", "knowledge_table.csv")

    kb, concepts, src = load_tak(data_dir, cfg["project"]["PROJECT_ID"])
    old = read_table(out)
    print(f"knowledge base : {src}  ({len(concepts)} concepts)")
    print(f"existing table : {out}  ({len(old)} rows)")

    new = {}
    for cid, c in concepts.items():
        new[c["name"]] = (str(cid), allowed_values(c))

    # --- carry over non-TAK rows that the data actually uses -----------------
    in_data = names_in_data(data_dir)
    carried, dropped = [], []
    for name, (cid, av) in old.items():
        if name in new:
            continue
        if args.tak_only or in_data is None or name not in in_data:
            dropped.append(name)
        else:
            new[name] = (cid, av)
            carried.append(name)

    # --- diff ---------------------------------------------------------------
    added = sorted(n for n in new if n not in old)
    id_fixed = sorted(n for n in new if n in old and new[n][0] != old[n][0])
    val_changed = sorted(n for n in new
                         if n in old and new[n][0] == old[n][0] and new[n][1] != old[n][1])
    print(f"\n  added rows           : {len(added)}")
    print(f"  ConceptID corrected  : {len(id_fixed)}")
    for n in id_fixed:
        print(f"      {n:<34} {old[n][0]:>6} -> {new[n][0]}")
    print(f"  AllowedValues changed: {len(val_changed)}")
    for n in val_changed[:10]:
        print(f"      {n:<34} {old[n][1]!r} -> {new[n][1]!r}")
    if len(val_changed) > 10:
        print(f"      ... and {len(val_changed) - 10} more")
    print(f"  carried over (non-TAK but present in the data): {len(carried)}")
    if carried:
        print(f"      {', '.join(sorted(carried)[:8])}{' ...' if len(carried) > 8 else ''}")
    print(f"  dropped (non-TAK and absent from the data)    : {len(dropped)}")
    if dropped:
        print(f"      {', '.join(sorted(dropped)[:8])}{' ...' if len(dropped) > 8 else ''}")
    print(f"\n  result: {len(new)} rows")

    # --- what the config's concept lists will now select ---------------------
    def resolvable(entry, table):
        """A KL_CONCEPTS entry is either a ConceptID or a ConceptName."""
        if entry.isdigit():
            return entry in {cid for cid, _av in table.values()}
        return entry.lower() in {n.lower() for n in table}

    print("\n  effect on config.json concept lists:")
    for label, lab in cfg.get("labels", {}).items():
        want = [i.strip() for i in str(lab.get("KL_CONCEPTS", "")).split(",") if i.strip()]
        if not want:
            continue
        before = sum(1 for i in want if resolvable(i, old))
        after = sum(1 for i in want if resolvable(i, new))
        flag = "" if after == len(want) else f"   <-- {len(want) - after} UNRESOLVABLE"
        print(f"    {label:<10} asks for {len(want):>3} concepts: "
              f"{before:>3} resolvable before -> {after:>3} after{flag}")
        for i in want:
            if not resolvable(i, new):
                print(f"        missing: {i}")

    if args.dry_run:
        print("\n(dry run -- nothing written)")
        return 0

    if os.path.exists(out):
        shutil.copyfile(out, out + ".bak")
        print(f"\nbacked up: {out}.bak")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["ConceptName", "ConceptID", "AllowedValues"])
        for name in sorted(new, key=lambda n: (int(new[n][0]) if new[n][0].isdigit() else 1 << 30, n)):
            cid, av = new[name]
            w.writerow([name, cid, av])
    print(f"wrote: {out}  ({len(new)} rows)")
    print("\nNOTE: results from this table are NOT comparable with runs made "
          "against the old one -- more concepts are now mineable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
