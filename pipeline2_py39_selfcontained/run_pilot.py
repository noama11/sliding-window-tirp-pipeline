"""Phase 3 pilot gate -- run the pure-Python Mediator PATTERN engine on the three
pilot patterns and compare each against the golden reference.

    1. Visit_pattern              (870, 134 rows)
    2. last_AST_prev_1_y_pattern  (514,  71 rows)
    3. CHADS_Vasc_score_pattern   (179, 134 rows)

Each is filtered out of csv_mode\\fixtures\\abstractions.csv into
reference_<name>.csv and compared with the same order-insensitive comparator and
the same fixed window [2022-01-01 00:00:00, 2023-12-31 22:00:00] as Phase 2.

Usage: python run_pilot.py [pattern-name ...]
"""
import csv
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "pyengine", "mediator"))

from engine import Engine   # noqa: E402

TAK = os.path.join(HERE, "tak_2700.json")
FIXTURES = r"C:\Users\noama1\Desktop\karma\csv_mode\fixtures"
RAW = os.path.join(FIXTURES, "mediator_raw_events.csv")
ABSTRACTIONS = os.path.join(FIXTURES, "abstractions.csv")
COMPARATOR = r"C:\Users\noama1\Desktop\karma\csv_mode\_prep\compare_abstractions.py"

PILOT = ["Visit_pattern", "last_AST_prev_1_y_pattern", "CHADS_Vasc_score_pattern"]


def write_reference(name, path):
    rows = []
    with open(ABSTRACTIONS, newline="", encoding="utf-8-sig") as fh:
        rdr = csv.DictReader(fh)
        fields = rdr.fieldnames
        for r in rdr:
            if r["ConceptName"].strip() == name:
                rows.append(r)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def main():
    names = sys.argv[1:] or PILOT

    eng = Engine(TAK)
    eng.load_raw_csv(RAW)

    results = []
    for name in names:
        rows = eng.run([name])
        cand = os.path.join(HERE, "candidate_%s.csv" % name)
        ref = os.path.join(HERE, "reference_%s.csv" % name)
        eng.write_csv(rows, cand)
        n_ref = write_reference(name, ref)
        print("%-30s candidate %4d rows | reference %4d rows"
              % (name, len(rows), n_ref))
        results.append((name, ref, cand))

    print()
    for name, ref, cand in results:
        proc = subprocess.run([sys.executable, COMPARATOR, ref, cand],
                              capture_output=True, text=True)
        out = (proc.stdout or "") + (proc.stderr or "")
        verdict = [ln for ln in out.splitlines() if "RESULT" in ln]
        print("=== %s ===" % name)
        print(out.strip()[-2000:] if not verdict else "\n".join(verdict))
        print()


if __name__ == "__main__":
    main()
