"""Rebuild the pipeline tree from the pasted drop/ chunks. Runs IN the room.

    python unpack.py            decode drop/, write every file, check the manifest
    python unpack.py --reset    empty the chunks that failed, so next.ps1 reopens them
    python unpack.py --clean    ...and delete drop/ afterwards, on success only

Each chunk is base64 of one zip holding the whole bundle. base64 is pure ASCII,
so nothing the paste path does to text can reach the bytes inside the zip: not a
BOM, not CRLF, not a codepage that turns an em dash into two bytes of noise. The
older file-by-file route pasted source directly and had to detect that damage
after the fact; here it cannot happen, and the zip's own CRC proves it.

What can still go wrong is a clipboard that cuts a paste short, so every chunk
carries the sha256 of its own payload and is checked before anything is written.
A short paste names the one chunk to redo instead of corrupting a file quietly.

This file is written into the room by SETUP_ROOM.ps1 -- it is not pasted.
"""

import argparse
import binascii
import base64
import hashlib
import io
import os
import re
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DROP = os.path.join(HERE, "drop")
MANIFEST = os.path.join(HERE, "MANIFEST.txt")
HEADER = re.compile(r"^#DROP\s+(\d+)/(\d+)\s+sha256=([0-9a-f]{64})\s*$")


def read_pieces():
    """(payload, problems) -- the concatenated base64 and everything wrong with it.

    Whitespace is stripped rather than trusted: a paste arrives with CRLF, may
    gain or lose a trailing newline, and Notepad is happy to leave a stray blank
    line behind. None of that changes the base64, so none of it should fail.
    """
    if not os.path.isdir(DROP):
        sys.exit("no drop/ folder next to unpack.py -- run SETUP_ROOM.ps1 first.")
    names = sorted(f for f in os.listdir(DROP)
                   if f.startswith("p") and f.endswith(".b64"))
    if not names:
        sys.exit("drop/ holds no pNN.b64 files -- run SETUP_ROOM.ps1 first.")

    payload, problems, bad = [], [], []
    for pos, name in enumerate(names, 1):
        path = os.path.join(DROP, name)
        # utf-8-sig: if the paste was saved with a BOM, drop it silently. The
        # payload is ASCII either way, so this is the one encoding fix needed.
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            lines = fh.read().splitlines()
        body = [ln for ln in lines if ln.strip()]
        if not body:
            problems.append("%s: EMPTY -- not pasted yet" % name)
            continue
        m = HEADER.match(body[0].strip())
        if not m:
            problems.append("%s: no #DROP header on the first line -- the paste "
                            "started part-way through" % name)
            bad.append(name)
            continue
        idx, total, want = int(m.group(1)), int(m.group(2)), m.group(3)
        if idx != pos or total != len(names):
            problems.append("%s: holds chunk %d/%d but sits at position %d of %d "
                            "-- pasted into the wrong file" %
                            (name, idx, total, pos, len(names)))
            bad.append(name)
            continue
        text = "".join(ln.strip() for ln in body[1:])
        got = hashlib.sha256(text.encode("ascii", "replace")).hexdigest()
        if got != want:
            problems.append("%s: TRUNCATED or altered (%d base64 chars)" %
                            (name, len(text)))
            bad.append(name)
            continue
        payload.append(text)
    return "".join(payload), problems, bad


def extract(raw):
    """Write every member of the zip under HERE. Returns the paths written."""
    written = []
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        bad = zf.testzip()
        if bad is not None:
            sys.exit("the zip is damaged at %s -- re-paste every chunk." % bad)
        for name in zf.namelist():
            if name.endswith("/"):
                continue
            # The archive is ours, but an archive that writes outside its own
            # folder is the one bug in this shape of tool worth ruling out.
            if os.path.isabs(name) or ".." in name.replace("\\", "/").split("/"):
                sys.exit("refusing to extract %r -- it escapes the folder." % name)
            dst = os.path.join(HERE, name.replace("/", os.sep))
            os.makedirs(os.path.dirname(dst) or HERE, exist_ok=True)
            with open(dst, "wb") as fh:
                fh.write(zf.read(name))
            written.append(name)
    return written


def check_manifest():
    """The same check verify.py runs. Returns (ok, missing, corrupt)."""
    if not os.path.exists(MANIFEST):
        return False, ["MANIFEST.txt"], []
    missing, corrupt, ok = [], [], 0
    with open(MANIFEST, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            sha, _lines, rel = line.split(None, 2)
            path = os.path.join(HERE, rel.replace("/", os.sep))
            if not os.path.exists(path):
                missing.append(rel)
                continue
            with open(path, "rb") as f:
                norm = f.read().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            if hashlib.sha256(norm).hexdigest() == sha:
                ok += 1
            else:
                corrupt.append(rel)
    return ok, missing, corrupt


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reset", action="store_true",
                    help="empty the chunks that failed, so next.ps1 reopens them")
    ap.add_argument("--clean", action="store_true",
                    help="delete drop/ once everything verifies")
    args = ap.parse_args()

    payload, problems, bad = read_pieces()
    if problems:
        print("cannot unpack yet:\n")
        for p in problems:
            print("  " + p)
        # next.ps1 walks the chunks that are EMPTY, so a chunk that arrived short
        # is not empty and would be skipped forever. Emptying it puts it back in
        # the queue -- otherwise the only route is to remember which file it was
        # and open it by hand.
        if args.reset:
            for name in bad:
                open(os.path.join(DROP, name), "w").close()
            print("\nemptied %d chunk(s). Now: .\\next.ps1 -> Ctrl+V -> Ctrl+S, "
                  "then unpack.py again." % len(bad))
        elif bad:
            print("\nRe-paste those. To queue them up for .\\next.ps1:")
            print("    python unpack.py --reset")
        else:
            print("\nPaste the rest with .\\next.ps1, then run unpack.py again.")
        return 1

    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        sys.exit("base64 did not decode (%s) -- a chunk carries a character that "
                 "was not pasted cleanly. Re-paste all of them." % exc)

    written = extract(raw)
    print("unpacked %d files (%.1f KB of base64 -> %.1f KB on disk)\n" %
          (len(written), len(payload) / 1024.0,
           sum(os.path.getsize(os.path.join(HERE, w.replace("/", os.sep)))
               for w in written) / 1024.0))

    ok, missing, corrupt = check_manifest()
    print("manifest: %d OK, %d missing, %d CORRUPT" %
          (ok, len(missing), len(corrupt)))
    for rel in missing:
        print("  missing  " + rel)
    for rel in corrupt:
        print("  CORRUPT  " + rel)
    if missing or corrupt:
        print("\nThat should not happen once every chunk passed its own check. "
              "Re-paste all chunks and run unpack.py again.")
        return 1

    if args.clean:
        shutil.rmtree(DROP, ignore_errors=True)
        for leftover in ("next.ps1", "paste_order.txt"):
            p = os.path.join(HERE, leftover)
            if os.path.exists(p):
                os.remove(p)
        print("\nremoved drop/, next.ps1, paste_order.txt")

    print("\nOK: every file matches the manifest. Next:")
    print("    python check_bundle.py")
    print("    python one_patient.py --list --data-dir <export-dir>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
