"""Prove that a copy-pasted bundle landed intact.

Paste this file and MANIFEST.txt into the research room FIRST. Then, at any
point during the paste, run it to see exactly which files are still missing and
which arrived corrupted:

    python verify.py                # check everything listed in MANIFEST.txt
    python verify.py --assemble     # first rebuild tak_2700.json from its parts

Hashes are taken over content with CRLF/CR normalised to LF. That matters:
pasting through Notepad turns every LF into CRLF, so a raw byte hash would
mismatch on a perfect paste. Normalising compares what the file *says*, not how
its line endings happen to be spelled.

Exit code 0 = every file present and correct.
"""

import argparse
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "MANIFEST.txt")
PARTS_DIR = os.path.join(HERE, "tak_parts")


def digest(path):
    """(sha256, line_count, sha256_of_trailing_newline_variant) over LF-normalised
    content.

    The third value exists to tell a real corruption apart from an editor that
    added or dropped a final newline on save. That difference is invisible to
    Python, JSON and csv, so it should be reported as harmless rather than as a
    reason to re-paste a 1,000-line file.
    """
    with open(path, "rb") as fh:
        raw = fh.read()
    norm = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    flipped = norm[:-1] if norm.endswith(b"\n") else norm + b"\n"
    return (hashlib.sha256(norm).hexdigest(), norm.count(b"\n"),
            hashlib.sha256(flipped).hexdigest())


def read_manifest():
    if not os.path.exists(MANIFEST):
        sys.exit(f"no MANIFEST.txt next to {os.path.basename(__file__)} "
                 f"(looked in {HERE}) -- paste it first.")
    out = []
    with open(MANIFEST, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            sha, lines, rel = line.split(None, 2)
            out.append((sha, int(lines), rel))
    return out


def assemble():
    """Concatenate tak_parts/tak_2700.partNN.txt back into tak_2700.json.

    Done in Python, not PowerShell: `Set-Content -Encoding utf8` on Windows
    PowerShell 5.1 writes a UTF-8 BOM, and json.load(encoding="utf-8") chokes on
    it. Reading in text mode also normalises whatever line endings the paste
    produced back to LF.
    """
    if not os.path.isdir(PARTS_DIR):
        print(f"no {PARTS_DIR}/ -- nothing to assemble, skipping.")
        return
    parts = sorted(f for f in os.listdir(PARTS_DIR)
                   if f.startswith("tak_") and f.endswith(".txt"))
    if not parts:
        print(f"no tak_*.txt in {PARTS_DIR}/ -- nothing to assemble, skipping.")
        return
    target = os.path.join(HERE, "tak_2700.json")
    n = 0
    with open(target, "w", encoding="utf-8", newline="\n") as out:
        for p in parts:
            with open(os.path.join(PARTS_DIR, p), encoding="utf-8-sig") as fh:
                for line in fh:
                    out.write(line.rstrip("\r\n") + "\n")
                    n += 1
    print(f"assembled {len(parts)} parts -> tak_2700.json ({n} lines)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--assemble", action="store_true",
                    help="rebuild tak_2700.json from tak_parts/ before checking")
    args = ap.parse_args()

    if args.assemble:
        assemble()
        print()

    entries = read_manifest()
    missing, bad, newline_only, ok = [], [], [], 0
    for sha, lines, rel in entries:
        path = os.path.join(HERE, rel.replace("/", os.sep))
        if not os.path.exists(path):
            missing.append(rel)
            continue
        got_sha, got_lines, flipped = digest(path)
        if got_sha == sha:
            ok += 1
        elif flipped == sha:
            newline_only.append(rel)
        else:
            bad.append((rel, lines, got_lines))

    print(f"manifest: {len(entries)} files")
    print(f"  OK       {ok}")
    print(f"  missing  {len(missing)}")
    print(f"  CORRUPT  {len(bad)}")
    if newline_only:
        print(f"  (also {len(newline_only)} differing only by a final newline "
              f"-- harmless)")

    if missing:
        print("\nnot pasted yet:")
        for rel in missing:
            print("  " + rel)
    if newline_only:
        print("\nfinal-newline difference only (content is identical; Python, "
              "JSON and csv all ignore this -- no action needed):")
        for rel in newline_only:
            print("  " + rel)
    if bad:
        print("\nPASTED BUT WRONG -- re-paste each of these:")
        for rel, want, got in bad:
            hint = (f"{got} lines, expected {want}"
                    + ("  <-- TRUNCATED" if got < want else
                       "  <-- extra lines" if got > want else
                       "  <-- same length, so content was altered"))
            print(f"  {rel}: {hint}")

    if missing or bad:
        return 1
    print("\nOK: every file matches the manifest.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
