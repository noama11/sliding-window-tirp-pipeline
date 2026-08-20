r"""Pack the whole bundle into a handful of pasteable base64 chunks.

The room takes TEXT ONLY, and the existing route in py_pipeline/_paste pastes the
runtime one file at a time: 32 pastes, 1.26 MB, each one a chance for the
clipboard to cut short or for Notepad to add a BOM. Everything in that bundle is
text, and text compresses:

    27 files + tak_2700.json   1254 KB
    deflated into one zip       148 KB
    base64 of that zip          197 KB   <- what actually gets pasted

At the default 40 KB per chunk that is FIVE pastes instead of thirty-two, and
because the real bytes ride inside the zip, the encoding hazards stop existing
rather than being detected afterwards.

    python make_drop.py                  # 5 chunks
    python make_drop.py --chunk-kb 100   # 2 chunks, if the clipboard carries it
    python make_drop.py --chunk-kb 20    # 10 chunks, if 40 KB gets truncated

Output lands in out/drop/: SETUP_ROOM.ps1 (the single console paste that builds
the room folder and writes unpack.py + next.ps1) and p01..pNN.b64 (the chunks you
copy from). Nothing under py_pipeline/_paste is written to -- this reads the
bundle and the shipping list from there and leaves the old route intact.
"""

import argparse
import base64
import hashlib
import io
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
# _transfer/ lives INSIDE the pipeline folder here (it was a sibling of
# py_pipeline/ in the original layout), so _paste/ is one level up, not two.
PASTE = os.path.normpath(os.path.join(HERE, os.pardir, "_paste"))
BUNDLE = os.path.join(PASTE, "out", "bundle")
OUT = os.path.join(HERE, "out", "drop")
WRAP = 100          # base64 columns; long single lines are what editors mangle

# What ships is already stated once, in make_paste_set.py. Import it rather than
# restating it, so a file added to the bundle cannot be in one route and not the
# other. Everything in that module sits behind main(), so importing is inert.
sys.path.insert(0, PASTE)
try:
    from make_paste_set import ORDER, OPTIONAL, LOCAL, TAK
except ImportError as exc:
    sys.exit("cannot import make_paste_set from %s (%s)" % (PASTE, exc))


def content(path):
    """File bytes with CRLF/CR normalised to LF.

    The manifest hashes LF-normalised content, so storing the normalised bytes
    makes the hash of what is stored equal to the hash in the manifest -- the
    room extracts bytes verbatim and verify.py's own normalisation is then a
    no-op rather than a difference to explain.
    """
    with open(path, "rb") as fh:
        return fh.read().replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def collect():
    """[(archive path, bytes)] for everything the room needs, in paste order."""
    out, missing = [], []
    for rel in list(ORDER) + list(OPTIONAL) + [TAK]:
        src = os.path.join(BUNDLE, rel.replace("/", os.sep))
        if os.path.exists(src):
            out.append((rel, content(src)))
        else:
            missing.append("%s (bundle)" % rel)
    # Room tools live next to make_paste_set.py, not in the bundle: they exist to
    # get the bundle in and measure it once there.
    for rel in LOCAL:
        src = os.path.join(PASTE, rel)
        if os.path.exists(src):
            out.append((rel, content(src)))
        else:
            missing.append("%s (_paste)" % rel)
    if missing:
        sys.exit("missing input files:\n  " + "\n  ".join(missing) +
                 "\n\nCut a bundle first:  cd %s && python make_paste_set.py" % PASTE)
    return out


def manifest_text(files):
    """MANIFEST.txt, generated here rather than copied from out/paste_set/.

    That one hashes tak_2700.json over CANONICALISED content -- split_tak() in
    make_paste_set.py terminates every line, because the old route reassembles
    the file line by line and the source happens to end without a newline. The
    zip carries the original bytes, so reusing that manifest would report a
    one-byte difference on a perfect transfer.
    """
    lines = ["# TIRP pipeline drop manifest -- %d files" % len(files),
             "# sha256 of content with CRLF/CR normalised to LF, then line count, "
             "then path"]
    for rel, blob in files:
        lines.append("%s  %d  %s" % (hashlib.sha256(blob).hexdigest(),
                                     blob.count(b"\n"), rel))
    return ("\n".join(lines) + "\n").encode("utf-8")


def build_zip(files):
    buf = io.BytesIO()
    # compresslevel needs 3.7+; the room is on 3.9.7 but only reads this zip.
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel, blob in files:
            # A fixed date_time keeps the archive byte-identical across runs, so
            # regenerating a drop does not invalidate chunks already pasted.
            info = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, blob)
    return buf.getvalue()


def chunks(b64, chunk_kb):
    """Split the base64 into equal pieces, none larger than chunk_kb as a FILE.

    Sized on the wrapped-and-headered result, not on the raw base64, so
    `--chunk-kb 40` means "a 40 KB paste" and can be matched against whatever the
    clipboard has actually been observed to carry.

    Equal rather than fill-then-spill: the same count either way, but every paste
    is then the same size, and the last one is not a 6 KB runt that proves
    nothing about whether the big ones will make it across.
    """
    per = int(chunk_kb * 1024 * WRAP / (WRAP + 1.0)) - 128
    if per < 1024:
        sys.exit("--chunk-kb is too small to be worth it; use 5 or more.")
    n = max(1, -(-len(b64) // per))
    size = -(-len(b64) // n)
    return [b64[i:i + size] for i in range(0, len(b64), size)]


def wrap(text):
    return "\n".join(text[i:i + WRAP] for i in range(0, len(text), WRAP))


def write(path, text):
    """Write LF, UTF-8, no BOM -- and refuse anything non-ASCII.

    PowerShell 5.1 decodes a BOM-less .ps1 as the ANSI codepage, so a single
    smart quote or em dash in SETUP_ROOM.ps1 would arrive as mojibake in the room
    and, in a string it writes to disk, silently corrupt unpack.py.
    """
    try:
        blob = text.encode("ascii")
    except UnicodeEncodeError as exc:
        sys.exit("%s is not pure ASCII (%s) -- PowerShell 5.1 would mis-decode it."
                 % (os.path.basename(path), exc))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(blob.replace(b"\r\n", b"\n"))


NEXT_PS1 = r"""# Opens the next chunk that is still empty, in order.
#   .\next.ps1          open the next empty chunk
#   .\next.ps1 -List    show what is left
param([switch]$List)
$root  = $PSScriptRoot
$order = Get-Content (Join-Path $root 'paste_order.txt')
$empty = @($order | Where-Object {
  $p = Join-Path $root $_
  (-not (Test-Path $p)) -or ((Get-Item $p).Length -eq 0)
})
if ($empty.Count -eq 0) {
  Write-Host 'All chunks have content. Now run:  python unpack.py' -ForegroundColor Green
  return
}
if ($List) {
  Write-Host "$($empty.Count) of $($order.Count) still empty:" -ForegroundColor Yellow
  $empty | ForEach-Object { Write-Host "  $_" }
  return
}
$f = $empty[0]
$done = $order.Count - $empty.Count
Write-Host ("[{0}/{1}] {2}" -f ($done + 1), $order.Count, $f) -ForegroundColor Green
Write-Host '        paste with Ctrl+V, save with Ctrl+S, then close Notepad'
notepad (Join-Path $root $f)
"""


def here_string(name, body):
    """A single-quoted PowerShell here-string, which interpolates nothing.

    It ends at a line beginning with '@ -- so if the payload ever contained one,
    the script would end early and write a truncated file. Nothing here does, but
    the failure would be silent, so check rather than assume.
    """
    for line in body.splitlines():
        if line.startswith("'@"):
            sys.exit("%s contains a line starting with '@ -- it cannot go in a "
                     "here-string." % name)
    return "$%s = @'\n%s\n'@\n" % (name, body.rstrip("\n"))


def setup_script(names, unpack_py, total_kb):
    """SETUP_ROOM.ps1 -- the one paste that makes the room self-sufficient.

    Same shape as py_pipeline/_paste's version: create the files empty and UTF-8
    up front so each is afterwards filled with open -> Ctrl+V -> Ctrl+S. Notepad's
    Save keeps an existing file's path and encoding, so the Save As dialog never
    opens, and with it go the three things that actually go wrong by hand: the
    wrong folder, the wrong encoding, and Notepad appending .txt.
    """
    out = []
    out.append("# Paste this into PowerShell in the research room FIRST.\n")
    out.append("# Creates drop\\ with %d empty chunk files, and writes unpack.py\n"
               % len(names))
    out.append("# and next.ps1 -- which opens the next unfilled chunk for you.\n#\n")
    out.append("# Then, %d times:   .\\next.ps1   ->  Ctrl+V  ->  Ctrl+S  ->  close\n"
               % len(names))
    out.append("# Finally:          python unpack.py\n\n")
    out.append("$root = $PSScriptRoot\n")
    out.append("if (-not $root) { $root = (Get-Location).Path }\n\n")
    out.append("$chunks = @(\n")
    for n in names:
        out.append("  'drop\\%s'\n" % n)
    out.append(")\n\n")
    out.append("New-Item -ItemType Directory -Path (Join-Path $root 'drop') "
               "-Force | Out-Null\n\n")
    out.append("# UTF8Encoding($false) = no BOM.\n")
    out.append("$enc = New-Object System.Text.UTF8Encoding $false\n")
    out.append("foreach ($f in $chunks) {\n")
    out.append("  $p = Join-Path $root $f\n")
    out.append("  if (-not (Test-Path $p)) "
               "{ [System.IO.File]::WriteAllText($p, '', $enc) }\n}\n\n")
    out.append("[System.IO.File]::WriteAllText((Join-Path $root 'paste_order.txt'), "
               "($chunks -join \"`n\"), $enc)\n\n")
    out.append(here_string("next", NEXT_PS1))
    out.append("[System.IO.File]::WriteAllText((Join-Path $root 'next.ps1'), "
               "$next, $enc)\n\n")
    out.append(here_string("unpack", unpack_py))
    out.append("[System.IO.File]::WriteAllText((Join-Path $root 'unpack.py'), "
               "$unpack, $enc)\n\n")
    out.append("Write-Host ''\n")
    out.append("Write-Host (\"ready in {0}\" -f $root) -ForegroundColor Green\n")
    out.append("Write-Host '  drop\\      %d empty chunks, %.0f KB to paste in total'\n"
               % (len(names), total_kb))
    out.append("Write-Host '  next.ps1   opens the next empty chunk'\n")
    out.append("Write-Host '  unpack.py  turns the chunks back into the pipeline'\n")
    out.append("Write-Host ''\n")
    out.append("Write-Host 'Now, %d times:' -ForegroundColor Cyan\n" % len(names))
    out.append("Write-Host '    .\\next.ps1        # opens the next empty chunk'\n")
    out.append("Write-Host '    Ctrl+V, Ctrl+S, close'\n")
    out.append("Write-Host ''\n")
    out.append("Write-Host 'Then:  python unpack.py' -ForegroundColor Cyan\n")
    out.append("Write-Host 'Progress at any time:  .\\next.ps1 -List'\n")
    out.append("Write-Host ''\n")
    return "".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chunk-kb", type=float, default=40,
                    help="target size of one paste, in KB (default 40)")
    ap.add_argument("--out", default=OUT, help="where to write the drop")
    args = ap.parse_args()

    files = collect()
    files.append(("MANIFEST.txt", manifest_text(files)))
    raw = sum(len(b) for _r, b in files)

    blob = build_zip(files)
    b64 = base64.b64encode(blob).decode("ascii")
    pieces = chunks(b64, args.chunk_kb)

    out = os.path.abspath(args.out)
    if not os.path.isdir(out):
        os.makedirs(out)
    for stale in os.listdir(out):
        if stale.endswith(".b64"):
            os.remove(os.path.join(out, stale))

    names, total = [], 0
    for i, piece in enumerate(pieces, 1):
        name = "p%02d.b64" % i
        text = "#DROP %02d/%02d sha256=%s\n%s\n" % (
            i, len(pieces), hashlib.sha256(piece.encode("ascii")).hexdigest(),
            wrap(piece))
        write(os.path.join(out, name), text)
        names.append(name)
        total += len(text)

    unpack_py = open(os.path.join(HERE, "unpack_src.py"), encoding="utf-8").read()
    write(os.path.join(out, "SETUP_ROOM.ps1"),
          setup_script(names, unpack_py, total / 1024.0))

    print("drop: %s\n" % out)
    print("  %d files                %8.1f KB" % (len(files), raw / 1024.0))
    print("  zipped                  %8.1f KB" % (len(blob) / 1024.0))
    print("  %d chunks to paste       %8.1f KB   (%.0f%% of the old route's 1254 KB)"
          % (len(pieces), total / 1024.0, 100.0 * total / (1254 * 1024)))
    print("\n  SETUP_ROOM.ps1  %.1f KB   paste this into PowerShell in the room first"
          % (os.path.getsize(os.path.join(out, "SETUP_ROOM.ps1")) / 1024.0))
    print("  %s   copy each one over: .\\copy_part.ps1" %
          (names[0] + " .. " + names[-1] if len(names) > 1 else names[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
