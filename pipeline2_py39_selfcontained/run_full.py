"""Full-fixture gate -- run EVERY computable concept and compare against the
golden reference (csv_mode\\fixtures\\abstractions.csv).

This is the widest fidelity check there is: the Phase-2 gate covers 43
pattern-free concepts and the Phase-3 pilots 3 patterns, whereas this runs all
273 computable concepts ("*", exactly what csv_pipeline hands the Mediator) over
the fixture's 20 patients and diffs all 5,048 reference rows.

The comparison is scoped the same way run_gate.py scopes contexts: the engine
legitimately produces rows the reference SQL run omits (documented in
PHASE3_COMPLETE_SUMMARY.md as "phantom contexts"), so `--scoped` restricts the
candidate to the (patient, concept) pairs the reference actually covers. Run it
both ways -- unscoped tells you the extras, scoped tells you the misses.

Usage:
    python run_full.py            # scoped + unscoped verdicts
    python run_full.py --keep     # also leave the candidate CSVs on disk
"""

import argparse
import csv
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "pyengine", "mediator"))

from engine import Engine   # noqa: E402

TAK = os.path.join(HERE, "tak_2700.json")
FIXTURES = r"C:\Users\noama1\Desktop\karma\csv_mode\fixtures"
RAW = os.path.join(FIXTURES, "mediator_raw_events.csv")
REFERENCE = os.path.join(FIXTURES, "abstractions.csv")
COMPARATOR = r"C:\Users\noama1\Desktop\karma\csv_mode\_prep\compare_abstractions.py"

CAND = os.path.join(HERE, "candidate_full.csv")
CAND_SCOPED = os.path.join(HERE, "candidate_full_scoped.csv")
COMPUTABLE = ("state", "trend", "pattern", "context")


def _reference_coverage():
    """(patient, concept) pairs the reference run actually produced, plus its
    row count."""
    pairs, n = set(), 0
    with open(REFERENCE, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            pairs.add((r["PatientID"].strip(), r["ConceptName"].strip()))
            n += 1
    return pairs, n


def _verdict(ref, cand, label):
    proc = subprocess.run([sys.executable, COMPARATOR, ref, cand],
                          capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    print(f"--- {label} ---")
    for ln in out.splitlines():
        if any(k in ln for k in ("rows", "matched", "only in", "RESULT", "top concepts")):
            print("  " + ln.strip())
    print()
    return "IDENTICAL" in out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="keep the candidate CSVs")
    args = ap.parse_args()

    with open(TAK, encoding="utf-8") as fh:
        concepts = json.load(fh)["concepts"]
    names = [c["name"] for c in concepts.values() if c["op_type"] in COMPUTABLE]

    eng = Engine(TAK)
    eng.load_raw_csv(RAW)
    rows = eng.run(names)
    eng.write_csv(rows, CAND)

    cover, n_ref = _reference_coverage()
    scoped = [r for r in rows if (r[0], r[1]) in cover]
    eng.write_csv(scoped, CAND_SCOPED)

    print(f"concepts requested : {len(names)} (all computable, = Mediator \"*\")")
    print(f"patients           : {len(eng.patients())}")
    print(f"reference rows     : {n_ref}")
    print(f"candidate rows     : {len(rows)}  (scoped to reference coverage: {len(scoped)})")
    if eng._concept_failures:
        print(f"skipped            : {len(eng._concept_failures)} concept/patient "
              "computations raised (matches the .NET per-concept catch)")
    print()

    _verdict(REFERENCE, CAND, "unscoped (shows the engine's extra rows)")
    _verdict(REFERENCE, CAND_SCOPED, "scoped to reference (patient, concept) coverage")

    if not args.keep:
        for p in (CAND, CAND_SCOPED):
            os.remove(p)


if __name__ == "__main__":
    main()
