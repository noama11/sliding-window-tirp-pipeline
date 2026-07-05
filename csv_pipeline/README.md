# Self-contained CSV STROKE TIRP pipeline

A complete, **database-free** version of the stroke temporal-pattern pipeline
(Mediator → KarmaLego), bundled into this one folder so it can be copied to
another machine (e.g. the research room) and run with one click.

It reproduces **`pipeline_stroke.py`** exactly — the same K/Y sliding windows, the
same YES/NO cohorts, the same `_YES_patterns` / `_NO_patterns` output — but selects
cohorts from a CSV file and runs the engines off CSV, with **no SQL Server, no
pyodbc, and no build step**.

## How the pipeline works (the rhythm)

Each sliding window is a pair of periods:

- **K-window `[k_start, k_end]`** — observation period. Mediator computes temporal
  abstractions over this range (`k_end = k_start + K`).
- **Y-window `[y_start, y_end]`** — outcome period, immediately after K
  (`y_start = k_end`, `y_end = y_start + Y`). Used to label patients.

For every window, two cohorts are built, run separately, and written to two folders:

| Cohort | Definition | Output |
|--------|-----------|--------|
| **YES** | patients whose `Stroke_Ischemic` event falls in the **Y-window** | `results/<k_start>-<k_end>_YES_patterns/…/results.csv` |
| **NO**  | `3 × |YES|` patients who **never** had `Stroke_Ischemic`, randomly sampled | `results/<k_start>-<k_end>_NO_patterns/…/results.csv` |

Each `results.csv` is an `id,<pattern1>,<pattern2>,…` matrix, one row per patient —
the exact format the downstream prediction stage consumes.

Windows slide by `STEP` until `y_end` passes `END_DATE`. With the shipped config
(`K=2, Y=1, STEP=1, START_YEAR=2010, END_DATE=2024-10-08`) that's 12 windows,
`K=[2010-2012] Y=[2012-2013]` … `K=[2021-2023] Y=[2023-2024]`.

### Reproducibility (important)

- **YES** is deterministic — a fixed query, so the same raw data always yields the
  same YES cohort and the same YES patterns.
- **NO** is a **random control sample**. The original SQL pipeline samples it with
  `ORDER BY NEWID()`, which picks *different* NO patients on every run — so the NO
  half was never reproducible there. This CSV version samples with a fixed `SEED`
  (in `config.json`), so NO is stable run-to-run here. Change the seed to draw a
  different control set.

## One-time setup (research-room machine)

1. **Python 3.8+** — https://www.python.org/downloads/ (tick *Add python.exe to PATH*).
   The runner uses only the standard library — no `pip install`.
2. **.NET 8 runtime** — install the **ASP.NET Core Runtime 8.0.x** from
   https://dotnet.microsoft.com/download/dotnet/8.0
   (Mediator is an ASP.NET app, so the plain .NET runtime is not enough.)

`run.py` checks for .NET 8 on startup and tells you if it's missing.

## Run it

**One click:** double-click **`run.bat`** — runs every window (YES + NO each).

**Or from a terminal:**
```
python run.py                 # all windows
python run.py --list-windows  # print the window plan and exit
python run.py --window 2015   # run only the window whose k_start = 2015
```

Output goes to `results/<k_start>-<k_end>_{YES,NO}_patterns/AF_KL_Stroke/genreic/results.csv`.

## Folder layout

| Path | What it is | In git? |
|------|-----------|---------|
| `run.py`, `run.bat` | Portable runner + one-click launcher | yes |
| `config.json` | Windows (K/Y/STEP), cohort rules, KarmaLego params | yes |
| `engines/mediator/` | Compiled Mediator (`API.exe` + deps, CSV build) | yes |
| `engines/karmalego/` | Compiled KarmaLego (`KarmaLegoConsoleApp.exe` + deps, CSV build) | yes |
| `tak_entities/2700/` | TAK knowledge base for project 40144 (concept definitions) | yes |
| `data/*.csv` | CSV stand-ins for the DB tables (20-patient sample) | yes (sample) |
| `workspace/`, `results/`, `logs/`, `cache/`, `kl_config/` | Created at runtime | no (gitignored) |

`run.py` derives every path from its own location and rewrites the two engine
`appsettings.json` files to point inside this bundle on each run — so wherever you
unzip it, it just works.

## Data: sample vs. full

The bundle uses whichever folder `config.json → data.data_dir` points at, and
**falls back to the committed sample** if that folder is missing:

- **`data/`** — a **20-patient sample** (8 stroke, 12 non-stroke), committed to git.
  Enough to see YES/NO windows work; not a real cohort.
- **`data_full/`** — the **full `InputPatientsData` export** (~22.8 M rows / 4,590
  patients / ~1.7 GB). `config.json` ships pointing here (`"data_dir": "data_full"`).
  It is **NOT in git** (too big for GitHub), so it must be copied to the machine
  separately (USB / network share). On a fresh `git clone` it's absent, so the
  runner automatically uses the sample and says so.

So: **clone from GitHub → runs the sample demo. Copy the full folder (incl.
`data_full/`) → runs the real pipeline.** Nothing to edit either way.

### The four data files (same filenames/columns in either folder)

- **`raw_events.csv`** — full `InputPatientsData`; used for cohort selection (YES/NO)
  and as KarmaLego's raw input. `PatientID,ConceptName,StartTime,EndTime,Value`.
- **`mediator_raw_events.csv`** — events Mediator abstracts (deduplicated
  `InputPatientsData`, i.e. `SELECT DISTINCT`; same columns).
- **`knowledge_table.csv`** — `ConceptName,ConceptID,AllowedValues` (concept dictionary).
- **`projects.csv`** — `ProjectID,ProjectName,Kb_ID` (row for your project).

Timestamps: `yyyy-MM-dd HH:mm:ss`. To point at data somewhere else entirely, set
`config.json → data.data_dir` to an absolute path. If your project id / knowledge
base differ, update `config.json` (`project.PROJECT_ID`) and drop the matching
`tak_entities/<Kb_ID>/` folder in.

## Configuration (`config.json`)

- `window`: `K`, `Y`, `STEP`, `START_YEAR`, `END_DATE`.
- `cohort`: `STROKE_CONCEPT` (default `Stroke_Ischemic`), `NO_RATIO` (default 3),
  `SEED` (default 42).
- `karmalego.concepts`: the shipped value is the **stroke 68-concept list** (matches
  `config_stroke.json`). Set to `"*"` to mine all concepts.

## Validation

- The engines are the CSV builds checked against the production SQL pipeline:
  **KarmaLego CSV** reproduces the SQL KarmaLego reference **byte-for-byte**
  (20 patients × 30,722 patterns) under `concepts="*"`; **Mediator CSV** matches the
  SQL abstractions on 99.8% of rows (the differences are rows the SQL run *lost* to a
  concurrency bug, so CSV is a strict superset). No database connection is opened.
- The YES/NO windowed flow was smoke-tested on the sample: window `K=[2015-2017]
  Y=[2017-2018]` selects YES=`{2894, 3804}` and a seeded NO of 6, producing both
  `_YES_patterns` and `_NO_patterns` result matrices.
