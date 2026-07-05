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

## Running on your own (full) data

**You only need to supply ONE file: `raw_events.csv`** — your full
`InputPatientsData` export (`PatientID,ConceptName,StartTime,EndTime,Value`,
timestamps `yyyy-MM-dd HH:mm:ss`). Everything else is derived or bundled:

- the deduplicated Mediator input is computed from `raw_events.csv` automatically
  (per cohort — no separate file needed);
- `knowledge_table.csv` + `projects.csv` (the concept **dictionary**, which does
  not change with patient count) fall back to the copies bundled in `data/`;
- the knowledge base `tak_entities/2700/` is bundled.

So on a bigger machine/dataset you do **not** carry any data from elsewhere — you
just drop **your** `raw_events.csv` into a folder and point the pipeline at it.

**How the pipeline picks the data folder:** `config.json → data.data_dir`. It ships
set to `data_full`. Put your `raw_events.csv` in a `data_full/` folder next to
`run.py` (or set `data_dir` to any absolute path). If that folder is missing (e.g.
a fresh `git clone`), the runner automatically falls back to the bundled 20-patient
sample in `data/` and says so — so the repo always runs out-of-the-box for a demo.

> The `data/` folder holds a committed 20-patient **sample** (8 stroke, 12
> non-stroke) — enough to see YES/NO windows work, not a real cohort.

If your study uses a different project / knowledge base, update
`config.json → project.PROJECT_ID`, drop in the matching `tak_entities/<Kb_ID>/`,
and place that project's `knowledge_table.csv` + `projects.csv` in your data folder.

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
