r"""Abstract ONE patient and print what came out. The first thing to run on new data.

`run_pipeline.py` always works in cohorts, so the smallest thing it can do is a
whole window of two cohorts. That is the wrong instrument for the first contact
with an unfamiliar export: it takes minutes, and a mistake in the data shows up
as an empty result long after the interesting part.

This runs the Mediator alone, on a single patient. No cohort selection, no
KarmaLego, so none of the small-cohort blow-up in HANDBOOK.md section 10. It
answers exactly one question -- does this export's vocabulary line up with the
knowledge base -- in a few seconds.

    python one_patient.py --list                       # patient ids in the data
    python one_patient.py 111                          # abstract patient 111
    python one_patient.py 111 --window 2015 2017       # over a specific K-window
    python one_patient.py 111 --data-dir D:\export     # against a real export

Output goes to workspace/one_patient/, and the summary breaks the abstraction
rows down by concept so a thin result is obvious rather than silent.
"""

import argparse
import csv
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "pyengine", "mediator"))
sys.path.insert(0, os.path.join(HERE, "pyengine"))

from engine import Engine                                       # noqa: E402

COMPUTABLE = ("state", "trend", "pattern", "context")


def resolve(args):
    """Reuse run_pipeline's own config handling so this agrees with a real run.

    Importing it rather than re-reading config.json matters: the KB id, the data
    folder and the local->UTC window conversion all have precedence rules, and a
    second implementation of them would eventually disagree with the pipeline it
    is supposed to be diagnosing.
    """
    import run_pipeline as rp
    S = rp.Settings(rp.load_config(), data_dir=args.data_dir)
    return S, rp


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("patient", nargs="?", help="PatientID to abstract")
    ap.add_argument("--list", action="store_true",
                    help="list patient ids present in raw_events.csv and exit")
    ap.add_argument("--data-dir", help="override config.json data.data_dir")
    ap.add_argument("--window", nargs=2, type=int, metavar=("K_START", "K_END"),
                    help="K-window years (default: the config's first window)")
    args = ap.parse_args()

    S, rp = resolve(args)
    if not os.path.exists(S.RAW_EVENTS):
        sys.exit(f"no raw_events.csv in {S.DATA_DIR}")
    print(f"data   : {S.RAW_EVENTS}")

    # ---- patient ids ---------------------------------------------------------
    if args.list:
        ids, order = set(), []
        with open(S.RAW_EVENTS, encoding="utf-8-sig", newline="") as fh:
            r = csv.reader(fh)
            next(r, None)
            for row in r:
                if row and row[0].strip() and row[0].strip() not in ids:
                    ids.add(row[0].strip())
                    order.append(row[0].strip())
        print(f"{len(order)} patients: " + ", ".join(order[:40])
              + (" ..." if len(order) > 40 else ""))
        return 0
    if not args.patient:
        ap.error("give a PatientID, or --list to see which ones exist")

    # ---- cut this patient's rows --------------------------------------------
    out_dir = os.path.join(HERE, "workspace", "one_patient")
    os.makedirs(out_dir, exist_ok=True)
    one = os.path.join(out_dir, "raw_events.csv")
    pid, n, concepts_in = str(args.patient), 0, Counter()
    with open(S.RAW_EVENTS, encoding="utf-8-sig", newline="") as fi, \
            open(one, "w", encoding="utf-8", newline="") as fo:
        r = csv.reader(fi)
        w = csv.writer(fo, lineterminator="\n")
        w.writerow(next(r, None) or
                   ["PatientID", "ConceptName", "StartTime", "EndTime", "Value"])
        for row in r:
            if row and row[0].strip() == pid:
                w.writerow(row)
                concepts_in[row[1].strip()] += 1
                n += 1
    if not n:
        sys.exit(f"patient {pid} has no rows in {S.RAW_EVENTS} "
                 "(run with --list to see the ids that do)")
    print(f"patient: {pid} -- {n} raw rows over {len(concepts_in)} concepts")

    # ---- window --------------------------------------------------------------
    if args.window:
        k_start, k_end = args.window
    else:
        k_start, k_end, _ys, _ye = rp.generate_windows(S)[0]
    ws, we = rp.mediator_window(k_start, k_end)
    print(f"window : K=[{k_start}, {k_end}] -> "
          f"[{ws:%Y-%m-%d %H:%M:%S} .. {we:%Y-%m-%d %H:%M:%S}] UTC")

    # ---- abstract ------------------------------------------------------------
    tak = rp.tak_json(S)
    with open(tak, encoding="utf-8") as fh:
        defs = json.load(fh)["concepts"]
    names = [c["name"] for c in defs.values() if c["op_type"] in COMPUTABLE]

    t0 = time.time()
    eng = Engine(tak)
    eng.load_raw_csv(one)
    rows = eng.run(names, window=(ws, we))
    dt = time.time() - t0
    dst = os.path.join(out_dir, "abstractions.csv")
    eng.write_csv(rows, dst)

    # ---- report --------------------------------------------------------------
    print(f"\nMEDIATOR: {len(names)} computable concepts -> {len(rows)} "
          f"abstraction rows in {dt:.1f}s")
    if getattr(eng, "_concept_failures", None):
        print(f"  ({len(eng._concept_failures)} concept computations raised and "
              "were skipped -- matches the .NET behaviour)")
    if not rows:
        print("\nNO ABSTRACTION ROWS. Almost always a vocabulary mismatch: the\n"
              "export's ConceptName values must match the knowledge base. The\n"
              "concepts present for this patient were:\n  "
              + ", ".join(sorted(concepts_in)[:30]))
        return 1

    by_concept = Counter(r[1] for r in rows)
    print(f"\ntop concepts produced ({len(by_concept)} distinct):")
    for name, cnt in by_concept.most_common(15):
        print(f"  {cnt:>6}  {name}")
    print(f"\nwrote {dst}")
    print("Re-run this exact command: the port is deterministic, so the row "
          "count must be identical.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
