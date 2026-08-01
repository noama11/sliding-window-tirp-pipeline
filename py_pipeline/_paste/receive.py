"""Write the clipboard to a file. Run this INSIDE the research room.

Paste this file and MANIFEST.txt in by hand (Notepad, Save As). After that,
nothing else needs Notepad: the feeder script on your own machine puts one file
at a time on the clipboard, the remote desktop carries it in, and this writes it
to the right path with the right encoding.

    python receive.py            # take the clipboard, write one file
    python receive.py --loop     # keep going: press Enter after each copy

Each transfer is checked against MANIFEST.txt immediately, so a truncated
clipboard is caught at the moment it happens rather than at the end of a
32-file session.

Why this beats Notepad: no Save As dialog, no navigating to the folder, no
encoding dropdown, no quoting the filename to stop Notepad appending .txt, and
line endings are normalised to LF on write so the hash matches exactly.

The feeder puts a header line on the front of the clipboard --

    ###TIRP:pyengine/mediator/pattern.py
    <the file content>

-- so the destination path travels with the content and you never type it.
"""

import argparse
import hashlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HEADER = "###TIRP:"


def clipboard():
    """The clipboard as text, via PowerShell.

    Windows has no stdlib clipboard API, and the bundle may not import ctypes.
    Get-Clipboard exists from PowerShell 5.0, which is on every Windows 10/11
    box. -Raw keeps the text as one string instead of an array of lines.
    """
    # Both ends of this pipe must agree on UTF-8. PowerShell otherwise emits the
    # console's ANSI codepage and Python decodes with the locale's -- on a
    # Hebrew-locale box that is cp1255, and the first em dash in a docstring
    # raises UnicodeDecodeError. Several files here contain them.
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Get-Clipboard -Raw"],
            capture_output=True, encoding="utf-8", errors="replace", timeout=120)
    except FileNotFoundError:
        sys.exit("powershell not found -- cannot read the clipboard.")
    except subprocess.TimeoutExpired:
        sys.exit("timed out reading the clipboard.")
    if proc.returncode != 0:
        sys.exit(f"Get-Clipboard failed: {(proc.stderr or '').strip()}")
    return proc.stdout or ""


def manifest():
    """Everything checkable on arrival: the final files, plus the tak_2700.json
    chunks. The chunks live in their own manifest because they are scaffolding --
    they are deleted once assembled, so verify.py must not expect them, but
    receive.py should still catch a chunk that arrives short."""
    out = {}
    for name in ("MANIFEST.txt", "MANIFEST_PARTS.txt"):
        path = os.path.join(HERE, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    sha, lines, rel = line.split(None, 2)
                    out[rel] = (sha, int(lines))
    return out


def take_one(expected):
    """Pull one file off the clipboard. Returns (ok, message)."""
    text = clipboard()
    if not text.strip():
        return False, "clipboard is empty -- copy a file on your own machine first"
    if not text.lstrip().startswith(HEADER):
        return False, (f"clipboard does not start with {HEADER} -- it holds "
                       "something else, or the copy did not carry across")

    text = text.lstrip()
    first, _, body = text.partition("\n")
    rel = first[len(HEADER):].strip()
    if not rel:
        return False, "header carries no path"

    body = body.replace("\r\n", "\n").replace("\r", "\n")

    # `Get-Clipboard -Raw` appends a newline that was never in the file, and a
    # few files legitimately end without one. Rather than guess, try both forms
    # and keep whichever the manifest recognises -- the difference is invisible
    # to Python, JSON and csv, but it changes the hash.
    stripped = body.rstrip("\n")
    candidates = [stripped + "\n", stripped]
    want = expected.get(rel)
    body = candidates[0]
    if want:
        for cand in candidates:
            if hashlib.sha256(cand.encode("utf-8")).hexdigest() == want[0]:
                body = cand
                break

    dst = os.path.join(HERE, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    with open(dst, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)

    blob = body.encode("utf-8")
    got = hashlib.sha256(blob).hexdigest()
    n = blob.count(b"\n")

    if not want:
        return True, f"{rel}  ({n} lines)  -- not in the manifest, not checked"
    want_sha, want_lines = want
    if got == want_sha:
        return True, f"{rel}  ({n} lines)  OK"
    if n < want_lines:
        return False, (f"{rel}: TRUNCATED -- got {n} lines, expected {want_lines}. "
                       "Copy it again; the clipboard cut it short.")
    return False, (f"{rel}: WRONG -- {n} lines, expected {want_lines}. "
                   "Copy it again.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--loop", action="store_true",
                    help="keep taking files; press Enter after each copy, q to quit")
    args = ap.parse_args()

    expected = manifest()
    if not expected:
        print("NOTE: no MANIFEST.txt here -- files will be written but not checked.\n")

    if not args.loop:
        ok, msg = take_one(expected)
        print(("  " if ok else "  FAILED: ") + msg)
        return 0 if ok else 1

    done, failed = set(), 0
    print(f"loop mode -- {len(expected)} files in the manifest.")
    print("Copy a file on your own machine, then press Enter here. 'q' quits.\n")
    while True:
        try:
            if input("[Enter] > ").strip().lower() == "q":
                break
        except EOFError:
            break
        ok, msg = take_one(expected)
        if ok:
            done.add(msg.split()[0])
            failed = 0
            print(f"  {len(done)}/{len(expected)}  {msg}")
        else:
            failed += 1
            print(f"  FAILED: {msg}")
            if failed >= 3:
                print("  (three in a row -- is the clipboard sharing across the "
                      "remote desktop?)")
    missing = sorted(set(expected) - done)
    print(f"\nwritten this session: {len(done)}")
    if missing:
        print(f"still outstanding: {len(missing)}")
        for rel in missing[:12]:
            print("  " + rel)
        if len(missing) > 12:
            print(f"  ... and {len(missing) - 12} more")
    print("\nWhen everything is in:  python verify.py --assemble")
    return 0


if __name__ == "__main__":
    sys.exit(main())
