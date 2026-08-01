"""Profile the pure-Python Mediator, with the Pattern engine broken out.

Single-threaded Python replaces a Mediator that parallelises patients across
`ThreadsInBatch` cores, so the port starts ~N_cores behind and has to make it up
in per-patient cost. This script says where that cost actually goes.

Two views, because they answer different questions:

  * PER-CONCEPT (exclusive wall clock, attributed by op type). Which concepts
    are expensive, and is it the Pattern engine or the state/trend/context
    machinery? Exclusive = a concept is not charged for the concepts it derives
    from, so the numbers sum to the total and point at one culprit each.

  * cProfile (per-function). Once a concept is implicated, which loop inside it.

Neither view needs engine.py to change: the per-concept timer wraps the four
op-dispatch methods from the outside.

Usage:
    python profile_engine.py --raw <mediator_raw_events.csv> [options]
    python profile_engine.py --patients 20 --top 25
    python profile_engine.py --only-patterns          # pattern concepts only
    python profile_engine.py --window 2021-2023       # a real pipeline K-window
"""

import argparse
import cProfile
import io
import json
import os
import pstats
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "pyengine", "mediator"))
sys.path.insert(0, os.path.join(HERE, "pyengine"))

from engine import Engine   # noqa: E402

DEFAULT_RAW = r"C:\Users\noama1\Desktop\karma\csv_mode\fixtures\mediator_raw_events.csv"
COMPUTABLE = ("state", "trend", "pattern", "context")


class ConceptTimer:
    """Exclusive wall clock per (concept, op_type), by wrapping the engine's
    four op-dispatch methods. A stack discounts nested time so the totals add up
    to the run's compute time instead of double counting derived-from chains."""

    def __init__(self, eng):
        self.eng = eng
        self.excl = {}          # cid -> seconds (exclusive)
        self.calls = {}         # cid -> times entered
        self._stack = []        # [child_time_accumulated_for_this_frame]
        self._orig = {}

    def install(self):
        for meth in ("_compute_abstract", "_compute_context",
                     "_compute_pattern", "_raw_in_window"):
            self._orig[meth] = getattr(self.eng, meth)
            setattr(self.eng, meth, self._wrap(self._orig[meth], meth))

    def _wrap(self, fn, meth):
        def inner(pid, arg, window):
            # _raw_in_window takes a cid; the others take the concept dict
            cid = arg if meth == "_raw_in_window" else arg.get("id")
            self._stack.append(0.0)
            t0 = time.perf_counter()
            try:
                return fn(pid, arg, window)
            finally:
                elapsed = time.perf_counter() - t0
                child = self._stack.pop()
                own = elapsed - child
                self.excl[cid] = self.excl.get(cid, 0.0) + own
                self.calls[cid] = self.calls.get(cid, 0) + 1
                if self._stack:
                    self._stack[-1] += elapsed
        return inner

    def report(self, knowledge, top):
        rows = []
        for cid, secs in self.excl.items():
            c = knowledge.get(cid) or {}
            rows.append((secs, self.calls[cid], c.get("op_type", "?"),
                         c.get("name", cid), cid))
        rows.sort(reverse=True)
        total = sum(r[0] for r in rows)

        by_op = {}
        for secs, _n, op, _nm, _cid in rows:
            by_op[op] = by_op.get(op, 0.0) + secs
        print(f"\n{'=' * 78}\nEXCLUSIVE TIME BY OP TYPE  (total {total:.1f}s)\n{'=' * 78}")
        for op, secs in sorted(by_op.items(), key=lambda x: -x[1]):
            print(f"  {op:<14} {secs:8.2f}s  {100 * secs / total:5.1f}%")

        print(f"\n{'=' * 78}\nTOP {top} CONCEPTS BY EXCLUSIVE TIME\n{'=' * 78}")
        print(f"  {'seconds':>9} {'%':>6} {'calls':>7}  {'op':<9} concept")
        for secs, n, op, name, cid in rows[:top]:
            print(f"  {secs:9.3f} {100 * secs / total:5.1f}% {n:7d}  {op:<9} {name} ({cid})")
        return total


def _window(spec):
    """--window 2021-2023 -> the same [Ws, We] csv_pipeline/run.py hands the
    engine for that K-window, including the .NET local->UTC conversion."""
    if not spec:
        return None
    a, b = spec.split("-")
    lo, hi = datetime(int(a), 1, 1), datetime(int(b), 1, 1)
    return (lo.astimezone(timezone.utc).replace(tzinfo=None),
            hi.astimezone(timezone.utc).replace(tzinfo=None))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", default=DEFAULT_RAW, help="mediator_raw_events.csv to profile on")
    ap.add_argument("--tak", default=os.path.join(HERE, "tak_2700.json"))
    ap.add_argument("--patients", type=int, help="profile only the first N patients")
    ap.add_argument("--window", help="K-window as YYYY-YYYY (default: engine's fixture window)")
    ap.add_argument("--only-patterns", action="store_true",
                    help="request only pattern concepts (their derived-from chains still run)")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--cprofile", action="store_true", help="also dump a cProfile function table")
    ap.add_argument("--cprofile-top", type=int, default=25)
    args = ap.parse_args()

    print(f"python : {sys.version.split()[0]}")
    print(f"raw    : {args.raw}")

    t0 = time.perf_counter()
    eng = Engine(args.tak)
    eng.load_raw_csv(args.raw)
    t_load = time.perf_counter() - t0

    if args.patients:
        keep = set(eng.patients()[:args.patients])
        eng.raw = {k: v for k, v in eng.raw.items() if k[0] in keep}

    with open(args.tak, encoding="utf-8") as fh:
        knowledge = json.load(fh)["concepts"]
    ops = ("pattern",) if args.only_patterns else COMPUTABLE
    names = [c["name"] for c in knowledge.values() if c["op_type"] in ops]

    n_pat = len(eng.patients())
    n_raw = sum(len(v) for v in eng.raw.values())
    print(f"loaded : {n_raw} raw instances, {n_pat} patients in {t_load:.1f}s")
    print(f"concepts requested: {len(names)} ({'patterns only' if args.only_patterns else 'all computable'})")

    timer = ConceptTimer(eng)
    timer.install()

    win = _window(args.window)
    prof = cProfile.Profile() if args.cprofile else None
    t0 = time.perf_counter()
    if prof:
        prof.enable()
    rows = eng.run(names, window=win)
    if prof:
        prof.disable()
    t_run = time.perf_counter() - t0

    print(f"\ncompute: {t_run:.1f}s for {n_pat} patients "
          f"({t_run / max(1, n_pat):.2f}s/patient) -> {len(rows)} rows")
    if eng._concept_failures:
        print(f"         {len(eng._concept_failures)} concept/patient computations raised "
              "and were skipped")

    timer.report(knowledge, args.top)

    if prof:
        buf = io.StringIO()
        pstats.Stats(prof, stream=buf).sort_stats("tottime").print_stats(args.cprofile_top)
        print(f"\n{'=' * 78}\ncPROFILE (self time)\n{'=' * 78}")
        print(buf.getvalue())

    # Straight-line projection to a real cohort, since that is the actual question.
    print(f"{'=' * 78}\nPROJECTION\n{'=' * 78}")
    per_pat = t_run / max(1, n_pat)
    for cohort in (528, 788, 904):
        print(f"  {cohort:4d}-patient cohort: ~{per_pat * cohort / 60:.1f} min "
              f"single-threaded compute (+ load)")


if __name__ == "__main__":
    main()
