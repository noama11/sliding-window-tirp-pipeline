# Research-Room Runbook

Everything to do inside the room, in order, with the numbers this machine
produced so you can tell "it worked" from "it ran".

The room takes **text only**. Python **3.9.7** is present, and cannot be
upgraded — this bundle is the 3.9 port and needs nothing newer. The room already
holds its own `raw_events.csv`.

Rehearsed end to end on 2026-08-01 against a simulated room folder: paste,
verify, `check_bundle`, `one_patient`, and a full window all pass, and the
abstractions produced from the pasted bundle are **byte-identical** to the
development tree's.

---

## What you are carrying

`pipeline2_py39_selfcontained\_paste\out\paste_set\` — regenerate any time with
`cd pipeline2_py39_selfcontained\_paste && python make_paste_set.py --rebuild`

| | |
|---|---|
| 26 code / config / dictionary / doc files | 359 KB |
| 9 chunks of `tak_2700.json` | 947 KB |
| **35 paste operations** | **1.31 MB** |

Plus `PASTE_ORDER.txt` (the checklist) and `MANIFEST.txt` (integrity hashes).
`verify.py`, `measure_run.ps1` and this runbook are themselves in the paste set.

The knowledge base ships as the pre-parsed `tak_2700.json`, **not** the 376
`tak_entities/2700/*.xml` files: one file instead of 376, for 69 KB more text.
`run_pipeline.py` falls back to it automatically. Verified equivalent — patient
111 abstracted from the JSON gives a byte-identical `abstractions.csv` to the
same patient abstracted from the XMLs.

---

## Step 1 — Environment (5 min)

```powershell
python --version              # need >= 3.9  (the room has 3.9.7)
[TimeZoneInfo]::Local.Id      # see below -- this changes your results
Get-PSDrive C                 # need ~4 GB free
```

**The timezone is not a formality.** The K-window is converted local→UTC to
reproduce the .NET engine (`run_pipeline.py:447`), so a window ending
`2024-01-01` really ends `2023-12-31 22:00` on an Israel-time box. A machine on
a different timezone silently shifts every window and every result. If the room
box is not on Israel time, stop and decide deliberately before running anything.

Then confirm the export:

```powershell
$csv = "<path>\raw_events.csv"
(Get-Item $csv).Length / 1GB
Get-Content $csv -TotalCount 3    # PatientID,ConceptName,StartTime,EndTime,Value
```

---

## Step 2 — Transfer and prove it landed (30–45 min)

If the remote desktop shares the clipboard from your machine into the room, use
the feeder — it removes the Save As dialog, which is where the time actually
goes.

**First, by hand** (Notepad → Save As → Encoding **UTF-8**, filename in
`"quotes"` so Notepad does not append `.txt`) — three small files into the room
folder:

```
MANIFEST.txt        MANIFEST_PARTS.txt        receive.py
```

**Then everything else rides the clipboard.** In the room:

```powershell
python receive.py --loop
```

On your own machine:

```powershell
cd pipeline2_py39_selfcontained\_paste
.\feed_paste.ps1
```

Now alternate: **Enter here → Enter there → Enter here**. Each press puts the
next file on the clipboard; `receive.py` writes it to the right path with the
right encoding and checks it against the manifest immediately, so a short
clipboard is caught on the spot rather than at the end.

If one fails, press `r` in the feeder to resend it. Occasional transient
clipboard failures are normal — in a 33-file rehearsal exactly one came through
empty and resending fixed it.

To resume after a break: `.\feed_paste.ps1 -Start 14`. To resend one file:
`.\feed_paste.ps1 -Only 'pattern.py'`.

### Doing it by hand instead

Paste **`SETUP_ROOM.ps1`** into PowerShell in the room first. It creates the
folder tree, creates all 36 files empty and UTF-8, and writes `next.ps1`. That
one paste is the only thing standing between you and a self-guiding session:

```powershell
cd C:\tirp
.\next.ps1          # opens the next still-empty file in Notepad
                    # Ctrl+V, Ctrl+S, close. Repeat.
.\next.ps1 -List    # what is left
```

The point of creating the files first is that **Notepad's Ctrl+S keeps an
existing file's path and encoding**, so the Save As dialog never opens. That
removes all three things that actually go wrong by hand: saving to the wrong
folder, choosing the wrong encoding, and Notepad silently appending `.txt`.

`PASTE_ORDER.txt` lists the same files with line counts and sizes if you would
rather work from a printed checklist.

Either way, finish with:

```powershell
python verify.py --assemble    # rebuilds tak_2700.json, then checks everything
```

Must report **0 missing, 0 CORRUPT**. It classifies failures for you — tested
against all four modes:

| What it says | What happened |
|---|---|
| `not pasted yet` | file absent |
| `TRUNCATED — 900 lines, expected 1246` | clipboard cut the paste short |
| `same length, so content was altered` | a character changed |
| `final newline only — harmless` | editor added/dropped a trailing newline; ignore |

Hashes are taken over content with CRLF normalised to LF, so Notepad's line
endings do not cause false alarms.

Finally:

```powershell
Remove-Item tak_parts -Recurse -Force
python check_bundle.py
```

Expect `OK: stdlib-only, ASCII-only, text-only, offline, self-contained.` It will note
`no data/raw_events.csv` — correct, you point at the room's export instead.
(`check_bundle.py --run` needs a CSV in `data/`; the static check is the one
that matters here.)

---

## Step 3 — One patient (15 min)

Isolates the one thing most likely to be wrong with a new export — whether its
`ConceptName` vocabulary matches KB 2700 — with no cohort logic, no KarmaLego,
and none of the memory hazard in Step 4.

```powershell
python one_patient.py --list --data-dir <export-dir>
python one_patient.py <some-id> --data-dir <export-dir>
```

**Baseline here** (patient 111, 9,929 raw rows, K=[2010,2012]):

```
MEDIATOR: 273 computable concepts -> 1724 abstraction rows in 0.7s
top concepts produced (89 distinct)
```
2.9 s wall clock, 38 MB peak.

**Pass:** non-zero abstraction rows across dozens of distinct concepts. Run it
twice — the port is deterministic, so the row count must be identical.

**If it prints `NO ABSTRACTION ROWS`** it lists the concepts the patient
actually had; compare those against `data/knowledge_table.csv`. That is a
vocabulary mismatch, and nothing downstream will work until it is resolved.

---

## Step 4 — Smallest full pipeline (30 min)

A single patient cannot exercise this: the NO cohort is drawn from patients who
*never* had the event (`run_pipeline.py:360`), so one patient gives an empty
control leg. Use ~2 YES + 6 NO.

```powershell
mkdir data_mini
$ids = Get-Content ids.txt
Get-Content <export>\raw_events.csv -TotalCount 1 | Set-Content data_mini\raw_events.csv -Encoding utf8
Get-Content <export>\raw_events.csv | Select-Object -Skip 1 |
  Where-Object { $ids -contains ($_ -split ',')[0] } |
  Add-Content data_mini\raw_events.csv -Encoding utf8
Copy-Item data\knowledge_table.csv, data\projects.csv data_mini\
```

```powershell
python run_pipeline.py --list-windows
.\measure_run.ps1 -Args 'run_pipeline.py','--data-dir','data_mini','--window','2015','--max-level','2','--keep-abstractions'
```

**Baseline here** (2 YES + 6 NO, `--max-level 2`): 6.6 s, 48 MB peak,
`YES 2 rows x 3,039 cols`, `NO 6 rows x 1,561 cols`.

### `--max-level 2` is mandatory here, and here is why

`MVS` is a *fraction* of the cohort, so at 2 patients the support threshold is
0.4 and nearly every symbol counts as frequent. Measured on exactly this cohort:

| `--max-level` | wall clock | peak RAM | patterns (YES) |
|---|---|---|---|
| 2 | 6.6 s | **48 MB** | 3,038 |
| 3 | 14.0 s | **223 MB** | 43,078 |
| 4 | 90.8 s | **2,557 MB** | 347,548 |

Roughly ×10 memory per level. Extrapolated, the default level 7 is the 18 GB
the handbook warns about. Real cohorts (hundreds of patients) are unaffected —
this is a small-cohort artifact only.

---

## Step 5 — One real window (the actual measurement)

```powershell
python run_pipeline.py --list-windows
.\measure_run.ps1 -Args 'run_pipeline.py','--data-dir','<export-dir>','--window','2015','--keep-abstractions' -Log 'win2015.log'
```

Note: `measure_run.ps1` walks the **process tree**. A venv `python.exe` is a
stub that spawns the real interpreter as a child, and measuring the stub reports
~16 MB regardless of what the run does. Do not replace it with
`$p.PeakWorkingSet64` — that reads 0 after the process exits.

**Reference numbers** (this machine, 4,590 patients, 1.6 GB CSV, full concept set):

| | |
|---|---|
| One window (132 YES + 396 NO) | **7 min 36 s** |
| Peak RAM | **~350 MB** |
| `workspace/` disk | ~2 GB |
| `--keep-abstractions` | +45 MB per cohort |

**How it scales:**

| Cost | Scales with | Rate here |
|---|---|---|
| One-time index pass | CSV size | ~40 s/GB, once per run |
| Per-window data prep | CSV size | ~40 s/GB, once per window |
| Mediator | cohort size | **0.25 s/patient**, flat |
| KarmaLego | frequent-pattern count | ~4 min at 528 patients |

Mediator and the streaming passes are linear and predictable. **KarmaLego is the
term to watch** — its cost tracks the discovered pattern count, which is
data-dependent, not linear in patients. That is the whole reason this step
exists before committing to twelve of them.

---

## Step 6 — Full run

Project `12 × (Step 5 time)` minus the one-time index pass — 1.5–2 h on this
machine's data. Then:

```powershell
.\measure_run.ps1 -Args 'run_pipeline.py','--data-dir','<export-dir>','--keep-abstractions' -Log 'full_run.log'
```

Up to 24 outputs at
`results\<k_start>-<k_end>_{YES,NO}_patterns\AF_KL_Stroke\genreic\results.csv`
(the `genreic` misspelling is the original .NET engine's and is deliberate).
Each is a patients × patterns matrix; the YES/NO folders give the labels.

Windows with an empty YES cohort are skipped and produce nothing — expected, not
a failure.

If Step 5 says a full run is too slow, run windows individually across sessions
with `--window`. **Do not quietly lower `--max-level`** — level 5 results are
not comparable with level 7 results.

`--label mortality` switches outcome; only 27 of its 43 concepts exist in KB
2700.

### Step 6b — the all-patients run

The YES/NO run gives two cohort-local matrices. A prediction model also needs
one row per patient across the whole population, which is a separate run:

```powershell
.\measure_run.ps1 -Args 'run_pipeline.py','--cohort','all','--data-dir','<export-dir>' -Log 'full_run_all.log'
```

One output per window at
`results\<k_start>-<k_end>_ALL_patterns\AF_KL_Stroke\genreic\results.csv`,
covering every patient with a record in that K-window. Same window plan as
Step 6, so the two join on (window, PatientID).

Budget more time than Step 6 — the cohort is the whole export instead of ~4×
the YES count, and Mediator cost is flat per patient. Do a `--window 2015` pass
first, as in Step 5, and read HANDBOOK §5 before trusting the output: `MVS` is a
*fraction of the cohort*, so the config's 0.2 is a much higher absolute bar over
everyone than over one YES cohort (`--mvs` overrides it), and the patterns are
re-discovered across the population rather than inherited from the YES/NO run.

---

## Things that will bite

1. **Timezone changes results.** Step 1. Not cosmetic.
2. **Small cohorts explode.** `--max-level 2` for anything under ~25 patients.
3. **Concept dictionary must match the KB.** Run
   `python build_knowledge_table.py --dry-run` against the room's data folder
   and expect no differences. A run reads the `knowledge_table.csv` sitting next
   to its own `raw_events.csv`, not the bundled one. This bug has already cost
   one full re-run.
4. **`workspace/run_data/` is scratch**, overwritten every window. Without
   `--keep-abstractions` a 12-window run leaves only the last window's
   intermediates.
5. **Do not concatenate `results.csv` across windows** — each cohort discovers
   its own pattern set, so the columns differ. Align on pattern name.
6. **Results are not comparable with anything produced before commit
   `d4b8357`**, when the knowledge table was fixed.
