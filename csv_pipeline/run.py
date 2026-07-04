"""
Self-contained CSV-only TIRP pipeline  (Mediator + KarmaLego, no database).

One folder, one command. Everything the pipeline needs is inside this bundle:
the compiled Mediator + KarmaLego engines, their TAK knowledge base, the CSV
data, and this runner. No SQL Server, no pyodbc, no build step.

All paths are derived at runtime from THIS file's location, so the bundle can be
copied/unzipped anywhere and still run. run.py rewrites the two engine
appsettings.json files to point inside the bundle before launching them.

Requires: Python 3.8+ (standard library only) and the .NET 8 runtime
(ASP.NET Core Runtime 8.0). See README.md.

Usage (from inside this folder):
    python run.py                                  # default: cohort20.txt, all windows
    python run.py --patients cohort20.txt --all-windows
    python run.py --patients cohort20.txt --k-start 2022 --k-end 2024
    python run.py --patients myids.txt   --all-windows --start-year 2018

Patient files live in patients/ (one ID per line). Data (the CSV stand-ins for
the DB tables) lives in data/. Results are written to results/.
"""

import argparse
import json
import os
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

DATA_DIR              = P("data")                       # DB-table stand-ins (shared by both engines)
ABSTRACTIONS_FILE     = P("data", "abstractions.csv")   # rendezvous: Mediator writes, KarmaLego reads
TAK_DIR               = P("tak_entities")               # parent of the KB folder (e.g. 2700/)
KL_CONFIG_FILE        = P("kl_config", "AF_KL_generic_config.json")
PATIENT_LIST_FILE     = P("workspace", "patient_list.txt")
PATIENTS_DIR          = P("patients")
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
STEP                = _cfg["window"]["STEP"]
START_YEAR          = _cfg["window"]["START_YEAR"]
END_DATE            = _cfg["window"]["END_DATE"]

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
    need_core = any(l.startswith("Microsoft.NETCore.App 8.") for l in runtimes.splitlines())
    need_asp  = any(l.startswith("Microsoft.AspNetCore.App 8.") for l in runtimes.splitlines())
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
    for d in (DATA_DIR, os.path.dirname(KL_CONFIG_FILE), os.path.dirname(PATIENT_LIST_FILE),
              RESULTS_DIR, LOGS_DIR, CACHE_DIR, EXTFUNC_DIR):
        os.makedirs(d, exist_ok=True)


def configure_engines():
    """Rewrite both engine appsettings to point inside THIS bundle. Called once at
    startup so the pipeline is portable to any machine/folder."""
    log("Wiring engines to this bundle (DBType=CSV)...")

    med = _read_jsonc(MEDIATOR_APPSETTINGS)
    med["DBType"] = "CSV"
    med["TAKEntitiesPath"] = TAK_DIR
    med["cacheDirectory"] = CACHE_DIR
    med["ExternalFunctionPath"] = EXTFUNC_DIR
    med["PeriodicFunctionFilePath"] = ""
    med.setdefault("ConnectionStrings", {})["CSV"] = {
        "DataPath": DATA_DIR,
        "OutputFile": ABSTRACTIONS_FILE,
    }
    _write_json(MEDIATOR_APPSETTINGS, med)

    kl = _read_jsonc(KARMALEGO_APPSETTINGS)
    cs = kl.setdefault("connectionStrings", {})
    cs["DBType"] = "CSV"
    cs["CSV"] = {"DataPath": DATA_DIR}
    kl["AppSettings"]["KarmalegoConfigPath"] = KL_CONFIG_FILE
    kl["AppSettings"]["LogsPath"] = LOGS_DIR
    kl["AppSettings"]["DomainNames"] = KL_CONFIG.get("domain_name", "AF_KL_Stroke")
    _write_json(KARMALEGO_APPSETTINGS, kl)

    log(f"  data      : {DATA_DIR}")
    log(f"  TAK KB    : {TAK_DIR}")
    log(f"  results   : {RESULTS_DIR}")


def sanity_check_data():
    required = ["mediator_raw_events.csv", "raw_events.csv", "knowledge_table.csv", "projects.csv"]
    missing = [f for f in required if not os.path.exists(P("data", f))]
    if missing:
        log("ERROR: missing required data files in data/: " + ", ".join(missing))
        log("Replace the sample data with your own exports using these exact filenames/columns.")
        sys.exit(2)


def load_patients_from_file(filename):
    path = os.path.join(PATIENTS_DIR, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Patient file not found: {path}")
    with open(path, "r", encoding="utf-8-sig") as f:
        return [line.strip() for line in f if line.strip()]


def clear_abstractions():
    log("Clearing abstractions (data/abstractions.csv)...")
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
    log(f"MEDIATOR (CSV): {len(patients)} patients in {total_batches} batch(es)...")
    time_window = f"{k_start}-01-01 00:00:00-{k_end}-01-01 00:00:00"

    for i in range(0, len(patients), MEDIATOR_BATCH_SIZE):
        batch = patients[i:i + MEDIATOR_BATCH_SIZE]
        log(f"  batch {(i // MEDIATOR_BATCH_SIZE) + 1}/{total_batches} ({len(batch)} patients)...")
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
    log(f"MEDIATOR done. Abstractions written: {count_abstractions()}")


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
    log("KARMALEGO (CSV): mining patterns...")
    kwargs = {"cwd": KARMALEGO_DIR, "stdin": subprocess.DEVNULL}
    if not SHOW_TOOL_OUTPUT:
        kwargs["capture_output"] = True
    result = subprocess.run([KARMALEGO_EXE], **kwargs)
    # KarmaLego ends with Console.ReadKey(); under redirected stdin it throws and
    # exits non-zero AFTER results.csv is fully written — the exit code is cosmetic.
    log(f"KARMALEGO exit code: {result.returncode} (non-zero is expected/cosmetic).")

    files = []
    for root, _dirs, fs in os.walk(results_path):
        for fn in fs:
            files.append(os.path.relpath(os.path.join(root, fn), results_path))
    for f in files:
        log(f"  {f}")
    if not any(f.endswith("results.csv") for f in files):
        log("WARNING: no results.csv produced — check the engine output above.")


# =============================================================================
# MAIN
# =============================================================================

def generate_windows(start_year=None):
    end_year = datetime.strptime(END_DATE, "%Y-%m-%d").year
    windows = []
    k_start = start_year if start_year is not None else START_YEAR
    while True:
        k_end = k_start + K
        if k_end > end_year:
            break
        windows.append((k_start, k_end))
        k_start += STEP
    return windows


def run_window(patients_file, k_start, k_end):
    label = os.path.splitext(patients_file)[0]
    log(f"--- WINDOW {k_start}-{k_end}  ({label}) ---")

    clear_abstractions()
    patients = load_patients_from_file(patients_file)
    log(f"Loaded {len(patients)} patients from {patients_file}.")
    if not patients:
        log("No patients — skipping window.")
        return
    save_patient_list(patients)

    run_mediator(patients, k_start, k_end)

    results_path = os.path.join(RESULTS_DIR, f"{k_start}-{k_end}_{label}_patterns")
    write_kl_config()
    set_results_path(results_path)
    run_karmalego(results_path)
    log(f"Window done -> {results_path}")


def main():
    parser = argparse.ArgumentParser(description="Self-contained CSV-only TIRP pipeline (no DB).")
    parser.add_argument("--patients", default="cohort20.txt",
                        help="Patient ID file in patients/ (default: cohort20.txt)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all-windows", action="store_true",
                       help="Run every window from config.json (default when no window given)")
    group.add_argument("--k-start", type=int, help="Single-window start year")
    parser.add_argument("--k-end", type=int, help="Single-window end year (with --k-start)")
    parser.add_argument("--start-year", type=int, help="Override START_YEAR for --all-windows")
    args = parser.parse_args()

    log("=" * 60)
    log("SELF-CONTAINED CSV TIRP PIPELINE")
    log(f"bundle: {BUNDLE}")
    log("=" * 60)

    check_dotnet()
    ensure_dirs()
    sanity_check_data()
    configure_engines()

    single = args.k_start is not None
    if single and args.k_end is None:
        parser.error("--k-end is required with --k-start")

    if single:
        log(f"Patients: {args.patients} | window {args.k_start}-{args.k_end} | MVS={MVS}")
        run_window(args.patients, args.k_start, args.k_end)
    else:
        effective_start = args.start_year if args.start_year is not None else START_YEAR
        windows = generate_windows(effective_start)
        log(f"Patients: {args.patients} | {len(windows)} window(s) from {effective_start} "
            f"(K={K}, STEP={STEP}) | MVS={MVS}")
        for idx, (ks, ke) in enumerate(windows, 1):
            log("")
            log(f"########## WINDOW {idx}/{len(windows)} ##########")
            run_window(args.patients, ks, ke)

    log("")
    log("=" * 60)
    log(f"ALL DONE. Results in: {RESULTS_DIR}")
    log("=" * 60)


if __name__ == "__main__":
    main()
