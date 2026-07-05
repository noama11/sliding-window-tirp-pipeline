"""
Self-contained CSV-only STROKE TIRP pipeline  (Mediator + KarmaLego, no database).

Reproduces pipeline_stroke.py's rhythm exactly, but with zero database:

  For each sliding window:
    K-window [k_start, k_end]  -> observation period (Mediator abstracts here)
    Y-window [y_start, y_end]  -> outcome period right after K (y_start=k_end, y_end=y_start+Y)

    YES cohort = patients whose Stroke_Ischemic event falls in the Y-window
        -> Mediator(K-window) -> KarmaLego -> results/<k_start>-<k_end>_YES_patterns
    NO  cohort = 3 x |YES| patients who never had Stroke_Ischemic (seeded random sample)
        -> Mediator(K-window) -> KarmaLego -> results/<k_start>-<k_end>_NO_patterns

  Windows slide by STEP until y_end passes END_DATE.

Cohorts are selected from data/raw_events.csv (the CSV stand-in for InputPatientsData)
instead of SQL. YES is deterministic; NO is a seeded random control sample (the original
SQL pipeline samples NO with ORDER BY NEWID(), which is non-reproducible run-to-run).

Everything the pipeline needs is inside this bundle (engines, TAK KB, data). No SQL
Server, no pyodbc, no build step. All paths are derived at runtime from THIS file's
location, so the bundle can be copied/unzipped anywhere.

Requires: Python 3.8+ (standard library only) and the .NET 8 runtime
(ASP.NET Core Runtime 8.0). See README.md.

Usage (from inside this folder):
    python run.py                 # all windows from config.json (YES + NO each)
    python run.py --window 2015   # just the window whose k_start = 2015
    python run.py --list-windows  # print the window plan and exit
"""

import argparse
import csv
import json
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime

# =============================================================================
# BUNDLE LAYOUT — every path is relative to this file, so the bundle is portable
# =============================================================================

BUNDLE = os.path.dirname(os.path.abspath(__file__))

def P(*parts):
    return os.path.join(BUNDLE, *parts)

CONFIG_FILE           = P("config.json")

ENGINES_MEDIATOR      = P("engines", "mediator")
MEDIATOR_EXE          = P("engines", "mediator", "API.exe")
MEDIATOR_APPSETTINGS  = P("engines", "mediator", "appsettings.json")

KARMALEGO_DIR         = P("engines", "karmalego")
KARMALEGO_EXE         = P("engines", "karmalego", "KarmaLegoConsoleApp.exe")
KARMALEGO_APPSETTINGS = P("engines", "karmalego", "appsettings.json")

def _resolve_data_dir():
    """Data folder holding the 4 CSV table stand-ins. Configurable via config.json
    ("data": {"data_dir": ...}); defaults to the bundled sample. If the configured
    folder has no raw_events.csv (e.g. a fresh git clone without the big full-data
    folder), fall back to the sample so the pipeline still runs."""
    with open(CONFIG_FILE, encoding="utf-8-sig") as f:
        cfg = json.load(f)
    want = cfg.get("data", {}).get("data_dir", "data")
    cand = want if os.path.isabs(want) else os.path.join(BUNDLE, want)
    if os.path.exists(os.path.join(cand, "raw_events.csv")):
        return cand, None
    return os.path.join(BUNDLE, "data"), cand   # (used_dir, missing_configured_dir)

DATA_DIR, _DATA_FALLBACK_FROM = _resolve_data_dir()     # SOURCE tables (cohort SELECTION reads these)
RAW_EVENTS_FILE       = os.path.join(DATA_DIR, "raw_events.csv")   # InputPatientsData stand-in (cohort select)
RUN_DATA_DIR          = P("workspace", "run_data")      # per-cohort FILTERED tables the engines actually read
ABSTRACTIONS_FILE     = os.path.join(RUN_DATA_DIR, "abstractions.csv")  # rendezvous: Mediator writes, KarmaLego reads
TAK_DIR               = P("tak_entities")               # parent of the KB folder (e.g. 2700/)
KL_CONFIG_FILE        = P("kl_config", "AF_KL_generic_config.json")
PATIENT_LIST_FILE     = P("workspace", "patient_list.txt")
RESULTS_DIR           = P("results")
LOGS_DIR              = P("logs")
CACHE_DIR             = P("cache")
EXTFUNC_DIR           = P("external_functions")         # unused by KB 2700; kept non-null for the engine

DOTNET_DOWNLOAD = "https://dotnet.microsoft.com/download/dotnet/8.0  (install the 'ASP.NET Core Runtime 8.0.x')"

# =============================================================================
# CONFIG
# =============================================================================

with open(CONFIG_FILE, encoding="utf-8-sig") as _f:
    _cfg = json.load(_f)

K                   = _cfg["window"]["K"]
Y                   = _cfg["window"]["Y"]
STEP                = _cfg["window"]["STEP"]
START_YEAR          = _cfg["window"]["START_YEAR"]
END_DATE            = _cfg["window"]["END_DATE"]

STROKE_CONCEPT      = _cfg["cohort"]["STROKE_CONCEPT"]
NO_RATIO            = _cfg["cohort"]["NO_RATIO"]
SEED                = _cfg["cohort"]["SEED"]

MVS                 = _cfg["karmalego"]["MVS"]
KL_CONFIG           = {k: v for k, v in _cfg["karmalego"].items()
                       if k not in ("MVS",) and not k.startswith("_")}

PROJECT_ID          = _cfg["project"]["PROJECT_ID"]

SHOW_TOOL_OUTPUT    = _cfg["runtime"]["SHOW_TOOL_OUTPUT"]
MEDIATOR_BATCH_SIZE = _cfg["runtime"]["MEDIATOR_BATCH_SIZE"]

# =============================================================================
# HELPERS
# =============================================================================

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def _read_jsonc(path):
    """Read JSON that may contain // line comments (Mediator's appsettings is JSONC).
    Quote-aware so // inside strings is preserved."""
    with open(path, "r", encoding="utf-8-sig") as f:
        text = f.read()
    out, in_str, esc, i, n = [], False, False, 0, len(text)
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c); i += 1; continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] not in "\r\n":
                i += 1
            continue
        out.append(c); i += 1
    return json.loads("".join(out))


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
        f.flush()
        os.fsync(f.fileno())


def check_dotnet():
    """Warn (do not hard-block) if the .NET 8 runtime the engines need can't be seen."""
    try:
        out = subprocess.run(["dotnet", "--list-runtimes"], capture_output=True, text=True)
        runtimes = out.stdout or ""
    except (OSError, FileNotFoundError):
        print("\n" + "!" * 70)
        print("  Could not find the 'dotnet' command on PATH.")
        print("  The engines need the .NET 8 runtime installed:")
        print("    " + DOTNET_DOWNLOAD)
        print("  (If .NET 8 is already installed, you can ignore this and continue.)")
        print("!" * 70 + "\n")
        return
    lines = runtimes.splitlines()
    need_core = any(l.startswith("Microsoft.NETCore.App 8.") for l in lines)
    need_asp  = any(l.startswith("Microsoft.AspNetCore.App 8.") for l in lines)
    missing = []
    if not need_core:
        missing.append("Microsoft.NETCore.App 8.x")
    if not need_asp:
        missing.append("Microsoft.AspNetCore.App 8.x  (Mediator is an ASP.NET app)")
    if missing:
        print("\n" + "!" * 70)
        print("  Missing .NET 8 runtime component(s): " + ", ".join(missing))
        print("  Install: " + DOTNET_DOWNLOAD)
        print("!" * 70 + "\n")
    else:
        log(".NET 8 runtime OK (NETCore.App 8 + AspNetCore.App 8 present).")


def ensure_dirs():
    for d in (DATA_DIR, RUN_DATA_DIR, os.path.dirname(KL_CONFIG_FILE), os.path.dirname(PATIENT_LIST_FILE),
              RESULTS_DIR, LOGS_DIR, CACHE_DIR, EXTFUNC_DIR):
        os.makedirs(d, exist_ok=True)


def configure_engines():
    """Rewrite both engine appsettings to point inside THIS bundle (portable). The
    engines read RUN_DATA_DIR — the per-cohort filtered tables — not the full source."""
    log("Wiring engines to this bundle (DBType=CSV)...")
    med = _read_jsonc(MEDIATOR_APPSETTINGS)
    med["DBType"] = "CSV"
    med["TAKEntitiesPath"] = TAK_DIR
    med["cacheDirectory"] = CACHE_DIR
    med["ExternalFunctionPath"] = EXTFUNC_DIR
    med["PeriodicFunctionFilePath"] = ""
    # Use most of the machine's cores for the abstraction step (Mediator parallelizes
    # patients across ThreadsInBatch). Auto-scaled so the bundle adapts to whatever
    # machine it lands on; leave 2 cores for the OS + this orchestrator.
    threads = max(2, (os.cpu_count() or 8) - 2)
    med["ThreadsInBatch"] = threads
    log(f"  Mediator ThreadsInBatch = {threads} (of {os.cpu_count()} cores)")
    med.setdefault("ConnectionStrings", {})["CSV"] = {
        "DataPath": RUN_DATA_DIR, "OutputFile": ABSTRACTIONS_FILE,
    }
    _write_json(MEDIATOR_APPSETTINGS, med)

    kl = _read_jsonc(KARMALEGO_APPSETTINGS)
    cs = kl.setdefault("connectionStrings", {})
    cs["DBType"] = "CSV"
    cs["CSV"] = {"DataPath": RUN_DATA_DIR}
    kl["AppSettings"]["KarmalegoConfigPath"] = KL_CONFIG_FILE
    kl["AppSettings"]["LogsPath"] = LOGS_DIR
    kl["AppSettings"]["DomainNames"] = KL_CONFIG.get("domain_name", "AF_KL_Stroke")
    _write_json(KARMALEGO_APPSETTINGS, kl)


def sanity_check_data():
    required = ["mediator_raw_events.csv", "raw_events.csv", "knowledge_table.csv", "projects.csv"]
    missing = [f for f in required if not os.path.exists(os.path.join(DATA_DIR, f))]
    if missing:
        log(f"ERROR: missing required data files in {DATA_DIR}: " + ", ".join(missing))
        log("Provide these exports (same filenames/columns) or point config.json data.data_dir at a folder that has them.")
        sys.exit(2)

# -----------------------------------------------------------------------------
# COHORT SELECTION over data/raw_events.csv (CSV stand-in for InputPatientsData)
# -----------------------------------------------------------------------------

_RAW_CACHE = None

def _parse_dt(s):
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None

def _load_raw():
    """Read raw_events.csv once: returns (all_patients:set, stroke_events:list[(pid, dt)])."""
    global _RAW_CACHE
    if _RAW_CACHE is None:
        all_p, stroke = set(), []
        target = STROKE_CONCEPT.strip().lower()
        with open(RAW_EVENTS_FILE, encoding="utf-8-sig", newline="") as f:
            r = csv.reader(f)
            next(r, None)  # header
            for row in r:
                if len(row) < 3:
                    continue
                pid = row[0].strip()
                if not pid:
                    continue
                all_p.add(pid)
                if row[1].strip().lower() == target:   # SQL LIKE 'Stroke_Ischemic' == exact, collation-insensitive
                    dt = _parse_dt(row[2])
                    if dt is not None:
                        stroke.append((pid, dt))
        _RAW_CACHE = (all_p, stroke)
    return _RAW_CACHE


def get_stroke_patients(y_start, y_end):
    """YES cohort: distinct patients with a Stroke_Ischemic event in [y_start, y_end).
    Deterministic (mirrors get_stroke_patients in pipeline_stroke.py)."""
    lo, hi = datetime(y_start, 1, 1), datetime(y_end, 1, 1)
    _all, stroke = _load_raw()
    pats = {pid for pid, dt in stroke if lo <= dt < hi}
    return sorted(pats, key=lambda x: (len(x), x))


def get_no_stroke_patients(count):
    """NO cohort: `count` patients who NEVER had Stroke_Ischemic, seeded random sample
    (mirrors get_no_stroke_patients, replacing SQL's ORDER BY NEWID() with SEED)."""
    all_p, stroke = _load_raw()
    stroke_ids = {pid for pid, _dt in stroke}
    pool = sorted(all_p - stroke_ids, key=lambda x: (len(x), x))
    k = min(count, len(pool))
    if k < count:
        log(f"  NOTE: only {len(pool)} non-stroke patients available; sampling {k} (< requested {count}).")
    return random.Random(SEED).sample(pool, k)

# -----------------------------------------------------------------------------
# ENGINE STEPS
# -----------------------------------------------------------------------------

def prepare_cohort_data(patients):
    """Filter the (large) source tables down to just this cohort's patients into
    RUN_DATA_DIR, so each engine loads a few MB instead of the full ~1GB+ file.

    Results are identical either way — the engines already filter by patient in
    memory (SQL mode only ever queries the cohort too); this is purely speed/RAM.
    One streaming pass over raw_events.csv produces both the cohort's raw file
    (with duplicates, for KarmaLego) and its deduplicated Mediator input."""
    os.makedirs(RUN_DATA_DIR, exist_ok=True)
    pset = set(map(str, patients))
    raw_out = os.path.join(RUN_DATA_DIR, "raw_events.csv")
    med_out = os.path.join(RUN_DATA_DIR, "mediator_raw_events.csv")
    seen = set()
    n_raw = 0
    with open(RAW_EVENTS_FILE, encoding="utf-8-sig", newline="") as fi, \
         open(raw_out, "w", encoding="utf-8", newline="") as fraw, \
         open(med_out, "w", encoding="utf-8", newline="") as fmed:
        r = csv.reader(fi)
        header = next(r, None) or ["PatientID", "ConceptName", "StartTime", "EndTime", "Value"]
        wraw = csv.writer(fraw, lineterminator="\n")
        wmed = csv.writer(fmed, lineterminator="\n")
        wraw.writerow(header)
        wmed.writerow(header)
        for row in r:
            if not row or row[0].strip() not in pset:
                continue
            wraw.writerow(row)
            n_raw += 1
            key = tuple(row)
            if key not in seen:
                seen.add(key)
                wmed.writerow(row)
    for f in ("knowledge_table.csv", "projects.csv"):
        shutil.copyfile(os.path.join(DATA_DIR, f), os.path.join(RUN_DATA_DIR, f))
    log(f"  cohort data: {n_raw} raw rows ({len(seen)} distinct) for {len(pset)} patients")


def clear_abstractions():
    if os.path.exists(ABSTRACTIONS_FILE):
        os.remove(ABSTRACTIONS_FILE)


def save_patient_list(patients):
    with open(PATIENT_LIST_FILE, "w", encoding="utf-8") as f:
        f.write(",".join(map(str, patients)))


def count_abstractions():
    if not os.path.exists(ABSTRACTIONS_FILE):
        return 0
    with open(ABSTRACTIONS_FILE, "r", encoding="utf-8-sig") as f:
        return max(0, sum(1 for _ in f) - 1)


def run_mediator(patients, k_start, k_end):
    if not patients:
        return
    total_batches = (len(patients) + MEDIATOR_BATCH_SIZE - 1) // MEDIATOR_BATCH_SIZE
    log(f"  MEDIATOR (CSV): {len(patients)} patients in {total_batches} batch(es)...")
    time_window = f"{k_start}-01-01 00:00:00-{k_end}-01-01 00:00:00"
    for i in range(0, len(patients), MEDIATOR_BATCH_SIZE):
        batch = patients[i:i + MEDIATOR_BATCH_SIZE]
        cmd = [
            MEDIATOR_EXE, "Query", "CalculateAbstractionsInBatchByTime",
            str(PROJECT_ID), ",".join(map(str, batch)), "*",
            ";".join([time_window] * len(batch)), "null", "1",
        ]
        kwargs = {"cwd": ENGINES_MEDIATOR, "check": True}
        if not SHOW_TOOL_OUTPUT:
            kwargs["capture_output"] = True
        try:
            subprocess.run(cmd, **kwargs)
        except OSError as e:
            if "too long" in str(e).lower() or getattr(e, "errno", None) == 206:
                log(f"  ERROR: command line too long — reduce MEDIATOR_BATCH_SIZE (now {MEDIATOR_BATCH_SIZE}).")
            raise
    log(f"  MEDIATOR done. Abstractions written: {count_abstractions()}")


def write_kl_config():
    config = {**KL_CONFIG, "projectId": PROJECT_ID, "entities": PATIENT_LIST_FILE, "mvs": MVS}
    _write_json(KL_CONFIG_FILE, config)


def set_results_path(results_path):
    kl = _read_jsonc(KARMALEGO_APPSETTINGS)
    kl["AppSettings"]["ResultsPath"] = results_path
    _write_json(KARMALEGO_APPSETTINGS, kl)
    time.sleep(0.3)


def run_karmalego(results_path):
    os.makedirs(results_path, exist_ok=True)
    log("  KARMALEGO (CSV): mining patterns...")
    kwargs = {"cwd": KARMALEGO_DIR, "stdin": subprocess.DEVNULL}
    if not SHOW_TOOL_OUTPUT:
        kwargs["capture_output"] = True
    result = subprocess.run([KARMALEGO_EXE], **kwargs)
    # KarmaLego ends with Console.ReadKey(); under redirected stdin it throws and exits
    # non-zero AFTER results.csv is fully written — the exit code is cosmetic.
    produced = any(f == "results.csv" for _r, _d, fs in os.walk(results_path) for f in fs)
    log(f"  KARMALEGO exit {result.returncode} (non-zero expected). results.csv: {'yes' if produced else 'NO'}")
    if not produced:
        log("  WARNING: no results.csv produced — check the engine output above.")


def run_cohort(label, patients, k_start, k_end):
    """One YES or NO leg: filter data -> fresh abstractions -> Mediator -> KarmaLego -> _<label>_patterns."""
    prepare_cohort_data(patients)
    clear_abstractions()
    save_patient_list(patients)
    run_mediator(patients, k_start, k_end)
    results_path = os.path.join(RESULTS_DIR, f"{k_start}-{k_end}_{label}_patterns")
    write_kl_config()
    set_results_path(results_path)
    run_karmalego(results_path)
    return results_path

# =============================================================================
# WINDOWS
# =============================================================================

def generate_windows():
    """(k_start, k_end, y_start, y_end) tuples — identical logic to pipeline_stroke.py."""
    end_year = datetime.strptime(END_DATE, "%Y-%m-%d").year
    windows = []
    k_start = START_YEAR
    while True:
        k_end = k_start + K
        y_start = k_end
        y_end = y_start + Y
        if y_end > end_year:
            break
        windows.append((k_start, k_end, y_start, y_end))
        k_start += STEP
    return windows


def process_window(k_start, k_end, y_start, y_end, idx, total):
    log("")
    log(f"########## WINDOW {idx}/{total}: K=[{k_start}-{k_end}]  Y=[{y_start}-{y_end}] ##########")

    yes = get_stroke_patients(y_start, y_end)
    log(f"YES (stroke in Y): {len(yes)} patients")
    if not yes:
        log("No YES patients — skipping window.")
        return
    run_cohort("YES", yes, k_start, k_end)

    no_count = len(yes) * NO_RATIO
    no = get_no_stroke_patients(no_count)
    log(f"NO (never stroke): {len(no)} patients (target {no_count} = {NO_RATIO}x YES)")
    if not no:
        log("No NO patients — skipping NO leg.")
        return
    run_cohort("NO", no, k_start, k_end)
    log(f"Window {k_start}-{k_end} complete: _YES_patterns + _NO_patterns")


def main():
    parser = argparse.ArgumentParser(description="Self-contained CSV-only STROKE TIRP pipeline (no DB).")
    parser.add_argument("--window", type=int, help="Run only the window whose k_start = this year.")
    parser.add_argument("--list-windows", action="store_true", help="Print the window plan and exit.")
    args = parser.parse_args()

    windows = generate_windows()

    if args.list_windows:
        print(f"{len(windows)} window(s)  (K={K}, Y={Y}, STEP={STEP}, START={START_YEAR}, END={END_DATE}):")
        for ks, ke, ys, ye in windows:
            print(f"  K=[{ks}-{ke}]  Y=[{ys}-{ye}]")
        return

    log("=" * 60)
    log("SELF-CONTAINED CSV STROKE TIRP PIPELINE")
    log(f"bundle: {BUNDLE}")
    log(f"data  : {DATA_DIR}")
    if _DATA_FALLBACK_FROM:
        log(f"NOTE: configured data folder '{_DATA_FALLBACK_FROM}' has no raw_events.csv — using the bundled sample instead.")
    log(f"K={K} Y={Y} STEP={STEP} START={START_YEAR} END={END_DATE} | MVS={MVS} | NO={NO_RATIO}x seed={SEED}")
    log("=" * 60)

    check_dotnet()
    ensure_dirs()
    sanity_check_data()
    configure_engines()

    if args.window is not None:
        sel = [w for w in windows if w[0] == args.window]
        if not sel:
            log(f"No window with k_start={args.window}. Use --list-windows to see options.")
            sys.exit(2)
        process_window(*sel[0], 1, 1)
    else:
        log(f"Running {len(windows)} window(s), YES + NO each.")
        for idx, (ks, ke, ys, ye) in enumerate(windows, 1):
            process_window(ks, ke, ys, ye, idx, len(windows))

    log("")
    log("=" * 60)
    log(f"ALL DONE. Results in: {RESULTS_DIR}")
    log("=" * 60)


if __name__ == "__main__":
    main()
