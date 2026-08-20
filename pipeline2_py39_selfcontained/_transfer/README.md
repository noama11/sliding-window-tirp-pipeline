# One paste carries many files

A replacement for **Step 2** of `../_paste/RUNBOOK.md` — the transfer
step. Same idea as `SETUP_ROOM.ps1` and `.\next.ps1`, same Notepad muscle memory,
but each paste now carries the *content* of many files instead of one file.

|  | pastes | text pasted |
|---|---|---|
| `_paste/` route | 32 | 1,254 KB |
| **this** (default 40 KB chunks) | **6** | **206 KB** |

Everything in the bundle is text, and text compresses. The 26 runtime / config /
doc / tool files plus `tak_2700.json` deflate into one 152 KB zip, which is
206 KB once base64-encoded. `tak_2700.json` alone goes 947 KB → 48 KB, and that
is where most of the saving comes from.

The second win matters more than the count. **The real bytes ride inside the
zip, and base64 is pure ASCII**, so nothing the paste path does to text can reach
them: not a BOM, not CRLF, not a codepage that turns an em dash into noise. The
old route pasted source code directly and had to detect that damage afterwards —
hence `verify.py`'s "final newline only, harmless" branch and the UTF-8 forcing
in `receive.py`. Here it cannot happen, and each zip member carries a CRC that
proves it.

`tak_parts/` and `verify.py --assemble` are not used by this route at all;
`tak_2700.json` arrives whole.

---

## On this machine

```powershell
cd pipeline2_py39_selfcontained/_transfer
python make_drop.py                  # -> out/drop/
.\copy_part.ps1 -Setup               # SETUP_ROOM.ps1 to the clipboard
.\copy_part.ps1                      # then each chunk, Enter between them
```

`make_drop.py` reads `../_paste/out/bundle/` and imports the shipping
list (`ORDER`, `OPTIONAL`, `LOCAL`, `TAK`) straight from `make_paste_set.py`, so
a file added to the bundle cannot end up in one route and not the other. Nothing
under `_paste/` is written to — the 32-file route stays exactly as it was, as a
fallback.

If the bundle is missing: `cd ../_paste && python make_paste_set.py`.

## In the room

1. Paste `SETUP_ROOM.ps1` into PowerShell (or into Notepad, save, and run it).
   It creates `drop\` with the empty chunk files and writes `next.ps1` and
   `unpack.py`. That is the only bootstrap paste.
2. Six times: `.\next.ps1` → Ctrl+V → Ctrl+S → close. It opens the next chunk
   that is still empty, so there is no list to keep track of.
3. `python unpack.py`

Expect:

```
unpacked 27 files (203.1 KB of base64 -> 1239.0 KB on disk)

manifest: 26 OK, 0 missing, 0 CORRUPT
```

Then carry on with the runbook from Step 3 — `python check_bundle.py`, then
`python one_patient.py`. `verify.py` is in the drop and still works; `unpack.py`
runs the same check itself, so it is a second opinion rather than a required step.

`python unpack.py --clean` afterwards removes `drop\`, `next.ps1` and
`paste_order.txt`, leaving just the pipeline.

## When a chunk does not make it

Every chunk carries the sha256 of its own payload and is checked **before**
anything is written, so a short clipboard names the one chunk to redo instead of
quietly corrupting a file:

```
cannot unpack yet:

  p03.b64: TRUNCATED or altered (17288 base64 chars)

Re-paste those. To queue them up for .\next.ps1:
    python unpack.py --reset
```

`--reset` empties the failed chunks so `.\next.ps1` walks them again — necessary
because `next.ps1` looks for *empty* files, and a chunk that arrived short is not
empty. Then `.\copy_part.ps1 3` on this machine to resend just that one.

It also catches a chunk pasted into the wrong file (`holds chunk 2/6 but sits at
position 4 of 6`) and one never pasted at all (`EMPTY`).

## If 40 KB is too big to paste

Chunk size is the one thing to tune against whatever the clipboard actually
carries. Total text is the same either way:

| `--chunk-kb` | chunks | each |
|---|---|---|
| 10 | 21 | 10 KB |
| 20 | 11 | 19 KB |
| **40** (default) | **6** | **34 KB** |
| 70 | 3 | 69 KB |
| 105 | 2 | 103 KB |
| 210 | 1 | 205 KB |

Chunks are split evenly, so every paste is the same size — the last one is not a
runt that proves nothing about whether the big ones will make it. If a chunk
fails its checksum twice, drop a size band rather than retrying the same one.

## Rehearsed

Re-rehearsed for the 3.9 port on 2026-08-04, unpacking and running with
**Python 3.9.7** — the room's interpreter — and with `SETUP_ROOM.ps1` run under
**Windows PowerShell 5.1** (what the room has, not pwsh 7):

- `unpack.py` as written by PowerShell matches `unpack_src.py` except for the
  final newline, which the here-string drops (8,379 bytes against 8,380). ASCII,
  no BOM, and it cannot affect execution. Pre-existing, not port-specific.
- chunks pasted with CRLF, one with a UTF-8 BOM, one with stray blank lines, one
  saved without a final newline — all decode; whitespace is stripped, not trusted
- `unpacked 28 files`, all 27 manifest entries OK, 0 missing, 0 CORRUPT, and
  every extracted file byte-identical to `_paste/out/bundle/` after CRLF→LF
  (which is the form the manifest is defined over)
- failure modes each named their chunk and wrote nothing: truncated, blanked,
  pasted into the wrong file, one character altered
- extracted tree runs on 3.9.7: `check_bundle.py` reports
  `OK: stdlib-only, ASCII-only, text-only, offline, self-contained.`
- **a full pipeline run out of the pasted bundle produced 18 `results.csv`
  byte-identical to `../../py_pipeline/` on Python 3.10** — from the pre-parsed
  `tak_2700.json`, since the drop carries no `tak_entities/`. See
  [`../PY39_PORT.md`](../PY39_PORT.md).

Not rehearsed here: a run against the room's own `raw_events.csv`, which only
exists in the room.

## Files

| | |
|---|---|
| `make_drop.py` | builds `out/drop/` from the bundle |
| `unpack_src.py` | the unpacker; embedded verbatim into `SETUP_ROOM.ps1` |
| `copy_part.ps1` | puts a chunk on the clipboard, on this machine |
| `out/drop/SETUP_ROOM.ps1` | the one console paste |
| `out/drop/p01..pNN.b64` | the chunks |

Edit `unpack_src.py`, not the copy inside `SETUP_ROOM.ps1` — rerun
`make_drop.py` to re-embed it. The generator refuses to build if either script
contains a non-ASCII character (PowerShell 5.1 decodes a BOM-less `.ps1` as the
ANSI codepage) or a line starting with `'@` (which would end the here-string
early and truncate `unpack.py` silently).
