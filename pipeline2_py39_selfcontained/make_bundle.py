"""Cut the shippable copy of this folder -- what actually goes to the research room.

This folder doubles as a development tree: alongside the pipeline it carries
fidelity gates, a profiler, phase notes and their golden-reference CSVs. None of
that is needed to run anything, and all of it points at files outside the
folder. This script copies out just the runtime.

    python make_bundle.py                     -> ../tirp_pipeline_bundle/
    python make_bundle.py /path/to/out        -> that folder
    python make_bundle.py --zip               -> also writes <out>.zip
    python make_bundle.py --no-sample-data    -> omit data/raw_events.csv (~8 MB)

The result is verified before it is handed back: the copy's own check_bundle.py
is executed inside it, so a bundle that would not run is never produced.
"""

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# Everything the pipeline needs, and nothing else.
RUNTIME_FILES = ["run_pipeline.py", "one_patient.py", "config.json",
                 "check_bundle.py", "build_knowledge_table.py",
                 "README.md", "HANDBOOK.md", "PY39_PORT.md", "tak_2700.json"]
RUNTIME_DIRS = ["pyengine", "tak_entities", "data"]

# Present here, deliberately not shipped: gates and profiler read golden files
# from outside the folder, and the phase notes are project history.
DEV_ONLY = ["run_gate.py", "run_pilot.py", "run_full.py", "profile_engine.py",
            "make_bundle.py", "phase2_gate_concepts.json"]


def _ignore(_dir, names):
    return [n for n in names if n == "__pycache__" or n.endswith(".pyc")]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", nargs="?",
                    default=os.path.join(os.path.dirname(HERE), "tirp_pipeline_bundle"),
                    help="output folder (default: ../tirp_pipeline_bundle)")
    ap.add_argument("--zip", action="store_true", help="also write <out>.zip")
    ap.add_argument("--no-sample-data", action="store_true",
                    help="omit data/raw_events.csv; keep the dictionary CSVs")
    ap.add_argument("--no-tak-entities", action="store_true",
                    help="omit tak_entities/; the pre-parsed tak_<kb>.json is enough")
    ap.add_argument("--force", action="store_true",
                    help="overwrite the output folder if it exists")
    args = ap.parse_args()

    out = os.path.abspath(args.out)
    if os.path.abspath(out) == HERE:
        print("ERROR: refusing to write the bundle over its own source folder.")
        return 2
    if os.path.exists(out):
        if not args.force:
            print(f"ERROR: {out} already exists (use --force to overwrite).")
            return 2
        shutil.rmtree(out)
    os.makedirs(out)

    for f in RUNTIME_FILES:
        src = os.path.join(HERE, f)
        if not os.path.exists(src):
            print(f"ERROR: missing {f} -- cannot build a runnable bundle.")
            return 2
        shutil.copy2(src, os.path.join(out, f))
    # The 376 concept XMLs are the only reason a bundle path can get long enough
    # to hit Windows' 260-character limit: their names run to 67 characters and
    # they sit four directories deep. run_pipeline.py falls back to the
    # pre-parsed tak_<kb>.json when the folder is absent, and the two were
    # verified to produce byte-identical abstractions, so a bundle that ships
    # only the JSON is the same pipeline with shorter paths and 878 KB less.
    dirs = [d for d in RUNTIME_DIRS
            if not (args.no_tak_entities and d == "tak_entities")]
    for d in dirs:
        src = os.path.join(HERE, d)
        if not os.path.isdir(src):
            print(f"ERROR: missing {d}/ -- cannot build a runnable bundle.")
            return 2
        shutil.copytree(src, os.path.join(out, d), ignore=_ignore)

    if args.no_sample_data:
        sample = os.path.join(out, "data", "raw_events.csv")
        if os.path.exists(sample):
            os.remove(sample)
        for f in ("mediator_raw_events.csv", "abstractions.csv"):
            p = os.path.join(out, "data", f)
            if os.path.exists(p):
                os.remove(p)

    total = sum(os.path.getsize(os.path.join(r, f))
                for r, _d, fs in os.walk(out) for f in fs)
    n = sum(len(fs) for _r, _d, fs in os.walk(out))
    print(f"bundle: {out}")
    print(f"  {n} files, {total / 1e6:.1f} MB")
    print(f"  excluded (development only): {', '.join(DEV_ONLY)}")
    print(f"  excluded: PHASE*/PLAN notes and their reference_*/candidate_* CSVs")

    # Verify the COPY, not the source -- a bundle that cannot run is not a bundle.
    print("\nverifying the copy...")
    proc = subprocess.run([sys.executable, "check_bundle.py", "--run"],
                          cwd=out, capture_output=True, text=True)
    print((proc.stdout or "") + (proc.stderr or ""))
    if proc.returncode != 0:
        print("BUNDLE IS NOT VALID -- see above.")
        return 1

    if args.zip:
        archive = shutil.make_archive(out, "zip", root_dir=out)
        print(f"zip: {archive}  ({os.path.getsize(archive) / 1e6:.1f} MB)")

    print("\nTo use it:  cd <bundle> && python run_pipeline.py --list-windows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
