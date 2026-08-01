r"""Turn a bundle into a copy-paste-ready set for the research room.

The room takes TEXT ONLY -- no USB, no share, no attachment. So the bundle has
to arrive through the clipboard, one file at a time. This script prepares that:

  * selects what actually has to be pasted (the runtime, minus the 376 KB XMLs)
  * splits tak_2700.json into clipboard-sized chunks
  * writes MANIFEST.txt -- a line-ending-normalised SHA-256 per file
  * copies in verify.py, which recomputes the manifest inside the room

    python make_paste_set.py             # cuts the bundle if needed, then the paste set
    python make_paste_set.py --rebuild   # re-cut the bundle first (after code changes)

Both land in _paste/out/ -- out/bundle/ is the shippable pipeline, out/paste_set/
is what you actually paste from. Both are gitignored.

Why the knowledge base ships as tak_2700.json and not tak_entities/2700/:
947 KB in ONE file beats 878 KB spread over 376 files when every file costs a
separate paste. run_pipeline.py:287 tak_json() falls back to the pre-parsed JSON
whenever tak_entities/ is absent -- this is the designed path, not a workaround.
The KB id then has to come from data/projects.csv or config.json project.KB_ID,
both of which are in the paste set.
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Generated output lives under _paste/out/ -- next to the tools that make it, so
# there is one place to look, and gitignored because it is a disposable copy of
# files that are already tracked.
DEFAULT_BUNDLE = os.path.join(HERE, "out", "bundle")
DEFAULT_OUT = os.path.join(HERE, "out", "paste_set")

# Paste order. Smallest and most structural first, so `python verify.py` becomes
# a running progress report; the knowledge base -- by far the bulkiest item --
# goes last, when everything else is already proven good.
ORDER = [
    "config.json",
    "data/projects.csv",
    "data/knowledge_table.csv",
    "pyengine/__init__.py",
    "pyengine/mediator/event.py",
    "pyengine/mediator/state.py",
    "pyengine/mediator/datainstance.py",
    "pyengine/mediator/context.py",
    "pyengine/mediator/persistence.py",
    "pyengine/mediator/trend.py",
    "pyengine/mediator/functions.py",
    "pyengine/mediator/engine.py",
    "pyengine/karmalego.py",
    "pyengine/mediator/tak_parse.py",
    "pyengine/mediator/pattern.py",
    "run_pipeline.py",
    "one_patient.py",
    "build_knowledge_table.py",
    "check_bundle.py",
]
# Pasted, but not part of the runtime -- reference material, worth having in the
# room and cheap enough to carry.
OPTIONAL = ["README.md", "HANDBOOK.md"]

# Room tools that live here rather than in the bundle: they exist to get the
# bundle INTO the room and to measure it once there, so they are not part of the
# pipeline and make_bundle.py rightly does not ship them.
LOCAL = ["RUNBOOK.md", "measure_run.ps1", "receive.py", "verify.py"]

TAK = "tak_2700.json"


def digest(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    norm = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(norm).hexdigest(), norm.count(b"\n")


def copy(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)


def split_tak(src, out_dir, chunk_lines):
    """tak_2700.json -> tak_parts/tak_2700.partNN.txt, LF endings.

    The file is pretty-printed at <=85 chars per line and is pure ASCII, so it
    chunks on line boundaries with nothing to escape.

    Returns the CANONICAL content the room will end up with, and hashes are taken
    over that rather than over `src`. The source file has no trailing newline;
    any line-based reassembly gives every line a terminator, so the rebuilt file
    would differ from the original by exactly one byte and verify.py would cry
    corruption on a perfect paste. Canonicalising here -- every line terminated,
    LF throughout -- makes the round trip exact by construction. JSON is
    indifferent to the trailing newline.
    """
    os.makedirs(out_dir, exist_ok=True)
    with open(src, encoding="utf-8") as fh:
        lines = [ln.rstrip("\r\n") + "\n" for ln in fh]
    parts = [lines[i:i + chunk_lines] for i in range(0, len(lines), chunk_lines)]
    names = []
    for i, chunk in enumerate(parts, 1):
        name = f"tak_2700.part{i:02d}.txt"
        with open(os.path.join(out_dir, name), "w", encoding="utf-8", newline="\n") as out:
            out.writelines(chunk)
        names.append((name, len(chunk)))
    return "".join(lines), names


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", default=DEFAULT_BUNDLE, help="bundle to ship")
    ap.add_argument("--out", default=DEFAULT_OUT, help="where to write the paste set")
    ap.add_argument("--chunk-lines", type=int, default=4500,
                    help="lines per tak_2700.json chunk (default 4500, ~120 KB)")
    ap.add_argument("--rebuild", action="store_true",
                    help="re-cut the bundle even if one is already there")
    args = ap.parse_args()

    bundle, out = os.path.abspath(args.bundle), os.path.abspath(args.out)

    # Cut the bundle ourselves when it is missing or stale. Two commands that
    # must be run in the right order is one more thing to get wrong, and
    # make_bundle.py already self-verifies the copy it produces.
    if args.rebuild or not os.path.isdir(bundle):
        print(f"cutting a fresh bundle -> {bundle}\n")
        proc = subprocess.run(
            [sys.executable, "make_bundle.py", bundle, "--no-sample-data", "--force"],
            cwd=os.path.dirname(HERE))
        if proc.returncode != 0:
            sys.exit("make_bundle.py failed -- see above.")
        print()
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(out)

    # ---- the files, in paste order -------------------------------------------
    manifest, plan, missing = [], [], []
    for rel in ORDER + OPTIONAL:
        src = os.path.join(bundle, rel.replace("/", os.sep))
        if not os.path.exists(src):
            missing.append(rel)
            continue
        copy(src, os.path.join(out, rel.replace("/", os.sep)))
        sha, lines = digest(src)
        manifest.append((sha, lines, rel))
        plan.append((rel, lines, os.path.getsize(src)))
    if missing:
        sys.exit("bundle is missing: " + ", ".join(missing))

    for rel in LOCAL:
        src = os.path.join(HERE, rel)
        if not os.path.exists(src):
            sys.exit(f"missing room tool {rel} next to make_paste_set.py")
        copy(src, os.path.join(out, rel))
        sha, lines = digest(src)
        manifest.append((sha, lines, rel))
        plan.append((rel, lines, os.path.getsize(src)))

    # ---- the knowledge base, chunked ----------------------------------------
    tak_src = os.path.join(bundle, TAK)
    canonical, parts = split_tak(tak_src, os.path.join(out, "tak_parts"),
                                 args.chunk_lines)
    blob = canonical.encode("utf-8")
    total_lines = blob.count(b"\n")
    # The manifest entry is for the ASSEMBLED file, not the parts -- tak_parts/
    # is scaffolding and gets deleted in the room once verify.py --assemble runs.
    manifest.append((hashlib.sha256(blob).hexdigest(), total_lines, TAK))

    # Chunk hashes go in their own manifest: receive.py checks each chunk as it
    # lands, but verify.py must not expect them -- tak_parts/ is deleted once
    # assembled. Without this a short chunk would only surface at final assembly.
    with open(os.path.join(out, "MANIFEST_PARTS.txt"), "w",
              encoding="utf-8", newline="\n") as fh:
        fh.write(f"# tak_2700.json chunks -- checked on arrival by receive.py,\n")
        fh.write("# then deleted after `python verify.py --assemble`.\n")
        for name, _n in parts:
            p = os.path.join(out, "tak_parts", name)
            sha_, lines_ = digest(p)
            fh.write(f"{sha_}  {lines_}  tak_parts/{name}\n")

    # ---- MANIFEST.txt --------------------------------------------------------
    with open(os.path.join(out, "MANIFEST.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"# TIRP pipeline paste manifest -- {len(manifest)} files\n")
        fh.write("# sha256 of content with CRLF/CR normalised to LF, then line count, then path\n")
        for sha_, lines_, rel in manifest:
            fh.write(f"{sha_}  {lines_}  {rel}\n")

    # ---- the human-facing checklist -----------------------------------------
    with open(os.path.join(out, "PASTE_ORDER.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("PASTE ORDER -- research room\n")
        fh.write("=" * 74 + "\n\n")
        fh.write("Save each file at the path shown, relative to the bundle folder you\n")
        fh.write("create in the room. In Notepad use File > Save As, set Encoding to\n")
        fh.write("UTF-8 (NOT 'UTF-8 with BOM'), and put the filename in \"quotes\" so\n")
        fh.write("Notepad does not append .txt.\n\n")
        fh.write("STEP 0 - paste these two first; then `python verify.py` at any time\n")
        fh.write("         tells you what is still missing or corrupt.\n")
        fh.write("           MANIFEST.txt\n           verify.py\n\n")
        fh.write("STEP 1 - the runtime, in this order:\n\n")
        fh.write(f"  {'#':>3}  {'lines':>6}  {'KB':>7}  path\n")
        for i, (rel, lines_, size) in enumerate(plan, 1):
            tag = ("   (optional)" if rel in OPTIONAL else
                   "   (room tool)" if rel in LOCAL else "")
            fh.write(f"  {i:>3}  {lines_:>6}  {size / 1024:>7.1f}  {rel}{tag}\n")
        fh.write(f"\nSTEP 2 - the knowledge base: {len(parts)} chunks of {TAK}\n")
        fh.write(f"         ({total_lines} lines total). Save each into tak_parts/.\n\n")
        for name, n in parts:
            fh.write(f"       {n:>6} lines  tak_parts/{name}\n")
        fh.write("\nSTEP 3 - assemble and verify:\n\n")
        fh.write("           python verify.py --assemble\n\n")
        fh.write("         That concatenates tak_parts/*.txt into tak_2700.json (in\n")
        fh.write("         Python, because PowerShell 5.1 Set-Content -Encoding utf8\n")
        fh.write("         adds a BOM that json.load rejects) and then checks every\n")
        fh.write("         file against the manifest. It must report 0 missing,\n")
        fh.write("         0 CORRUPT before you run anything.\n\n")
        fh.write("STEP 4 - delete tak_parts/, then:\n\n")
        fh.write("           python check_bundle.py\n")

    code_bytes = sum(s for _r, _l, s in plan)
    tak_bytes = os.path.getsize(tak_src)
    print(f"paste set: {out}")
    print(f"  {len(plan)} code/config/dictionary files  {code_bytes / 1024:>8.1f} KB")
    print(f"  {len(parts)} chunks of {TAK}          {tak_bytes / 1024:>8.1f} KB")
    print(f"  ---------------------------------------------------")
    print(f"  {len(plan) + len(parts)} paste operations             "
          f"{(code_bytes + tak_bytes) / 1024:>8.1f} KB total")
    print(f"\n  MANIFEST.txt   {len(manifest)} entries")
    print(f"  PASTE_ORDER.txt  the checklist to work through in the room")
    return 0


if __name__ == "__main__":
    sys.exit(main())
