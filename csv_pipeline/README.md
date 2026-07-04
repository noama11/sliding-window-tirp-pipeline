# Self-contained CSV TIRP pipeline

A complete, **database-free** version of the two-stage temporal-pattern pipeline
(Mediator → KarmaLego), bundled into this one folder so it can be copied to
another machine (e.g. the research room) and run with one click.

Everything it needs is inside this folder: the compiled engines, their knowledge
base, the CSV data, and the runner. **No SQL Server, no pyodbc, no build step.**

```
mediator_raw_events.csv ──▶ Mediator (API.exe, DBType=CSV) ──▶ data/abstractions.csv
raw_events.csv + abstractions.csv + knowledge_table.csv + projects.csv
                        ──▶ KarmaLego (DBType=CSV) ──▶ results/<window>_<cohort>_patterns/…/results.csv
```

## One-time setup (research-room machine)

1. **Python 3.8+** — https://www.python.org/downloads/ (tick *Add python.exe to PATH*).
   The runner uses only the standard library — no `pip install` needed.
2. **.NET 8 runtime** — install the **ASP.NET Core Runtime 8.0.x** from
   https://dotnet.microsoft.com/download/dotnet/8.0
   (Mediator is an ASP.NET app, so the plain .NET runtime is not enough.)

That's it. `run.py` checks for .NET 8 on startup and tells you if it's missing.

## Run it

**One click:** double-click **`run.bat`** (runs `patients/cohort20.txt` over all
windows in `config.json`).

**Or from a terminal:**
```
python run.py                                     # default cohort, all windows
python run.py --patients cohort20.txt --k-start 2022 --k-end 2024   # one window
python run.py --patients myids.txt --all-windows  # your own cohort
python run.py --patients myids.txt --all-windows --start-year 2018
```

Output lands in `results/<k_start>-<k_end>_<cohort>_patterns/AF_KL_Stroke/genreic/results.csv`
as an `id,<pattern1>,<pattern2>,…` matrix (one row per patient).

## Folder layout

| Path | What it is | In git? |
|------|-----------|---------|
| `run.py`, `run.bat` | The portable runner + one-click launcher | ✔ |
| `config.json` | Windows, KarmaLego params, concept list, project id | ✔ |
| `engines/mediator/` | Compiled Mediator (`API.exe` + deps, CSV build) | ✔ |
| `engines/karmalego/` | Compiled KarmaLego (`KarmaLegoConsoleApp.exe` + deps, CSV build) | ✔ |
| `tak_entities/2700/` | TAK knowledge base for project 40144 (concept definitions) | ✔ |
| `data/*.csv` | CSV stand-ins for the DB tables (see below) | ✔ (sample) |
| `patients/*.txt` | Patient-ID lists (one ID per line) | ✔ |
| `workspace/`, `results/`, `logs/`, `cache/`, `kl_config/` | Created at runtime | no (gitignored) |

`run.py` derives every path from its own location and rewrites the two engine
`appsettings.json` files to point inside this bundle on each run — so wherever you
unzip it, it just works.

## Using your own data

The `data/` folder ships with a **20-patient sample**. To run real cohorts,
replace these files with your own exports (same filenames, same columns):

- **`mediator_raw_events.csv`** — raw events Mediator abstracts.
  `PatientID,ConceptName,StartTime,EndTime,Value` (deduplicated raw input).
- **`raw_events.csv`** — raw events KarmaLego reads (same columns; may contain the
  full raw history, not only the abstraction window).
- **`knowledge_table.csv`** — `ConceptName,ConceptID,AllowedValues`.
- **`projects.csv`** — `ProjectID,ProjectName,Kb_ID` (the row for your project).

Timestamps: `yyyy-MM-dd HH:mm:ss`. Put your patient IDs in `patients/<name>.txt`
and pass `--patients <name>.txt`.

If your project id / knowledge base differ, update `config.json` (`project.PROJECT_ID`)
and drop the matching `tak_entities/<Kb_ID>/` folder in.

## Concept scope (important)

`config.json → karmalego.concepts` controls what KarmaLego mines:

- The shipped value is the **stroke 68-concept list** (matches the SQL
  `config_stroke.json`). On the sample cohort this yields a compact pattern set.
- Set it to `"*"` to mine **all** concepts (much larger pattern set).

## Validation

This bundle's engines are the CSV builds that were checked against the production
SQL pipeline:

- **KarmaLego CSV** reproduces the SQL KarmaLego reference **byte-for-byte**
  (20 patients × 30,722 patterns) under `concepts="*"`.
- **Mediator CSV** matches the SQL abstractions on 99.8% of rows; the differences
  are rows the SQL run *lost* to a concurrency bug, so the CSV output is a strict
  superset (more complete). It opens **no** database connection.
