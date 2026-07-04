"""
TIRP Pipeline - From Patient List File - CSV MODE (no database)

Same orchestration as pipeline_from_patient_list.py, but runs the CSV builds of
Mediator and KarmaLego against CSV files instead of SQL Server. No pyodbc, no DB
connection at any step.

Data flow per window:
    mediator_raw_events.csv --(Mediator CSV, API.exe DBType=CSV)--> abstractions.csv
    raw_events.csv + abstractions.csv + knowledge_table.csv + projects.csv
                            --(KarmaLego CSV, DBType=CSV)--> {window}_{label}_patterns/.../results.csv

All settings load from config_all_patients_csv.json. The SQL script/config
(pipeline_from_patient_list.py / config_all_patients.json) are left untouched.

Usage:
    # Single window:
    python pipeline_from_patient_list_csv.py --patients cohort20.txt --k-start 2022 --k-end 2024

    # All windows (from config_all_patients_csv.json: K, STEP, START_YEAR, END_DATE):
    python pipeline_from_patient_list_csv.py --patients cohort20.txt --all-windows

Patient files must be in the patients/ subfolder (one ID per line).
"""

import argparse
import subprocess
import json
import os
import time
from datetime import datetime

# =============================================================================
# LOAD CONFIGURATION
# =============================================================================

_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_all_patients_csv.json")

with open(_CONFIG_FILE) as _f:
    _cfg = json.load(_f)

K                    = _cfg["window"]["K"]
STEP                 = _cfg["window"]["STEP"]
START_YEAR           = _cfg["window"]["START_YEAR"]
END_DATE             = _cfg["window"]["END_DATE"]

MVS                  = _cfg["karmalego"]["MVS"]
KL_CONFIG            = {k: v for k, v in _cfg["karmalego"].items() if k != "MVS"}

PROJECT_ID           = _cfg["project"]["PROJECT_ID"]

CSV_DATA_DIR         = _cfg["csv"]["DATA_DIR"]
ABSTRACTIONS_FILE    = _cfg["csv"]["ABSTRACTIONS_FILE"]

PATIENT_LIST_PATH    = _cfg["paths"]["PATIENT_LIST_PATH"]
KL_CONFIG_PATH       = _cfg["paths"]["KL_CONFIG_PATH"]
MEDIATOR_EXE         = _cfg["paths"]["MEDIATOR_EXE"]
MEDIATOR_APPSETTINGS = _cfg["paths"]["MEDIATOR_APPSETTINGS"]
KARMALEGO_DIR        = _cfg["paths"]["KARMALEGO_DIR"]
KARMALEGO_EXE        = _cfg["paths"]["KARMALEGO_EXE"]
KARMALEGO_APPSETTINGS = _cfg["paths"]["KARMALEGO_APPSETTINGS"]
RESULTS_BASE_PATH    = _cfg["paths"]["RESULTS_BASE_PATH"]

SHOW_TOOL_OUTPUT     = _cfg["runtime"]["SHOW_TOOL_OUTPUT"]
MEDIATOR_BATCH_SIZE  = _cfg["runtime"]["MEDIATOR_BATCH_SIZE"]

PATIENTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "patients")

# =============================================================================
# HELPERS
# =============================================================================

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def _read_jsonc(path):
    """Read a JSON file that may contain // line comments (Mediator's appsettings
    is JSONC). Quote-aware so // inside strings (e.g. base64) is preserved."""
    with open(path, "r", encoding="utf-8-sig") as f:
        text = f.read()
    out = []
    in_str = False
    esc = False
    i = 0
    n = len(text)
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
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            # skip to end of line
            while i < n and text[i] not in "\r\n":
                i += 1
            continue
        out.append(c)
        i += 1
    return json.loads("".join(out))


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
        f.flush()
        os.fsync(f.fileno())


def load_patients_from_file(filename):
    path = os.path.join(PATIENTS_DIR, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Patient file not found: {path}")
    with open(path, "r") as f:
        ids = [line.strip() for line in f if line.strip()]
    return ids


def configure_mediator_csv():
    """Point the Mediator CSV build at the CSV workspace (DBType=CSV, DataPath,
    OutputFile). Static for the whole run; set once."""
    log("Configuring Mediator CSV appsettings (DBType=CSV)...")
    settings = _read_jsonc(MEDIATOR_APPSETTINGS)
    settings["DBType"] = "CSV"
    conn = settings.setdefault("ConnectionStrings", {})
    conn["CSV"] = {"DataPath": CSV_DATA_DIR, "OutputFile": ABSTRACTIONS_FILE}
    _write_json(MEDIATOR_APPSETTINGS, settings)
    log(f"  DataPath   = {CSV_DATA_DIR}")
    log(f"  OutputFile = {ABSTRACTIONS_FILE}")


def configure_karmalego_csv():
    """Point the KarmaLego CSV build at the CSV workspace (DBType=CSV, CSV:DataPath).
    ResultsPath is set per-window in update_appsettings(). Set once."""
    log("Configuring KarmaLego CSV appsettings (DBType=CSV)...")
    settings = _read_jsonc(KARMALEGO_APPSETTINGS)
    cs = settings.setdefault("connectionStrings", {})
    cs["DBType"] = "CSV"
    cs["CSV"] = {"DataPath": CSV_DATA_DIR}
    _write_json(KARMALEGO_APPSETTINGS, settings)
    log(f"  CSV:DataPath = {CSV_DATA_DIR}")


def clear_abstractions():
    """CSV analogue of DELETE FROM OutputPatientsData: remove the rendezvous file
    so each window starts clean. Mediator recreates it."""
    log("Clearing abstractions file...")
    if os.path.exists(ABSTRACTIONS_FILE):
        os.remove(ABSTRACTIONS_FILE)
        log("Abstractions file removed.")
    else:
        log("No abstractions file to remove.")


def clear_patient_list():
    log("Clearing patient list...")
    with open(PATIENT_LIST_PATH, "w") as f:
        f.write("")
    log("Patient list cleared.")


def save_patients(patients):
    log(f"Saving {len(patients)} patients to file...")
    with open(PATIENT_LIST_PATH, "w") as f:
        f.write(",".join(map(str, patients)))
    log("Patients saved.")


def count_abstractions():
    if not os.path.exists(ABSTRACTIONS_FILE):
        return 0
    with open(ABSTRACTIONS_FILE, "r", encoding="utf-8-sig") as f:
        # subtract header
        return max(0, sum(1 for _ in f) - 1)


def run_mediator(patients, k_start, k_end):
    if not patients:
        return

    total_batches = (len(patients) + MEDIATOR_BATCH_SIZE - 1) // MEDIATOR_BATCH_SIZE
    log(f"Starting MEDIATOR CSV ({len(patients)} patients in {total_batches} batches)...")

    time_window = f"{k_start}-01-01 00:00:00-{k_end}-01-01 00:00:00"

    for i in range(0, len(patients), MEDIATOR_BATCH_SIZE):
        batch = patients[i:i + MEDIATOR_BATCH_SIZE]
        batch_num = (i // MEDIATOR_BATCH_SIZE) + 1
        log(f"  Batch {batch_num}/{total_batches} ({len(batch)} patients)...")

        cmd = [
            MEDIATOR_EXE, "Query", "CalculateAbstractionsInBatchByTime",
            str(PROJECT_ID), ",".join(map(str, batch)), "*",
            ";".join([time_window] * len(batch)), "null", "1"
        ]
        try:
            if SHOW_TOOL_OUTPUT:
                subprocess.run(cmd, check=True)
            else:
                subprocess.run(cmd, check=True, capture_output=True)
        except OSError as e:
            if "too long" in str(e).lower() or e.errno == 206:
                log(f"  ERROR: Command too long! Reduce MEDIATOR_BATCH_SIZE (current: {MEDIATOR_BATCH_SIZE})")
            raise

    log(f"MEDIATOR finished. Abstractions in file: {count_abstractions()}")


def update_appsettings(results_path):
    log(f"Updating KarmaLego AppSettings -> ResultsPath: {results_path}")
    settings = _read_jsonc(KARMALEGO_APPSETTINGS)
    settings["AppSettings"]["ResultsPath"] = results_path
    _write_json(KARMALEGO_APPSETTINGS, settings)

    time.sleep(0.5)
    verify = _read_jsonc(KARMALEGO_APPSETTINGS)
    log(f"AppSettings verified: {verify['AppSettings']['ResultsPath']}")


def update_kl_config():
    log("Updating KarmaLego config...")
    config = {**KL_CONFIG, "projectId": PROJECT_ID, "entities": PATIENT_LIST_PATH, "mvs": MVS}
    _write_json(KL_CONFIG_PATH, config)
    time.sleep(0.5)
    log("KarmaLego config updated.")


def run_karmalego(results_path):
    if not os.path.exists(results_path):
        os.makedirs(results_path)
        log(f"Created folder: {results_path}")
    else:
        log(f"Folder already exists: {results_path}")

    check = _read_jsonc(KARMALEGO_APPSETTINGS)
    log(f"Config check before run: {check['AppSettings']['ResultsPath']}")

    log("Starting KarmaLego CSV...")
    if SHOW_TOOL_OUTPUT:
        result = subprocess.run([KARMALEGO_EXE], cwd=KARMALEGO_DIR, stdin=subprocess.DEVNULL)
    else:
        result = subprocess.run([KARMALEGO_EXE], cwd=KARMALEGO_DIR, stdin=subprocess.DEVNULL, capture_output=True)
    # NOTE: KarmaLego ends with Console.ReadKey(); under redirected stdin it throws
    # and exits ~82 AFTER results.csv is fully written. The exit code is cosmetic.
    log(f"KarmaLego exit code: {result.returncode}")

    if os.path.exists(results_path):
        for root, dirs, files in os.walk(results_path):
            rel = os.path.relpath(root, results_path)
            prefix = "" if rel == "." else f"{rel}\\"
            for fname in files:
                log(f"  {prefix}{fname}")
        total = sum(len(fs) for _, _, fs in os.walk(results_path))
        log(f"Total files in output: {total}")
    else:
        log("WARNING: Folder does not exist after KarmaLego!")

    log("KarmaLego finished.")

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


def run(patients_file, k_start, k_end):
    label = os.path.splitext(patients_file)[0]  # e.g. "cohort20"

    log("--- STEP 0: CLEANUP ---")
    clear_abstractions()
    clear_patient_list()

    log("--- STEP 1: LOAD PATIENTS FROM FILE ---")
    patients = load_patients_from_file(patients_file)
    log(f"Loaded {len(patients)} patients from {patients_file}")

    if not patients:
        log("No patients in file. Aborting.")
        return

    save_patients(patients)

    log("--- STEP 2: MEDIATOR (CSV) ---")
    run_mediator(patients, k_start, k_end)

    log("--- STEP 3: KARMALEGO (CSV) ---")
    results_path = f"{RESULTS_BASE_PATH}\\{k_start}-{k_end}_{label}_patterns"
    update_appsettings(results_path)
    update_kl_config()
    run_karmalego(results_path)

    log(f"Done. Results: {results_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run all-patients TIRP pipeline from a patient ID file (CSV mode, no DB).")
    parser.add_argument("--patients", required=True, help="Patient ID file in patients/ folder (e.g. cohort20.txt)")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all-windows", action="store_true", help="Run all windows generated from config_all_patients_csv.json")
    group.add_argument("--k-start",     type=int,            help="K window start year for a single window (e.g. 2022)")
    parser.add_argument("--k-end",        type=int, help="K window end year (required with --k-start)")
    parser.add_argument("--start-year",   type=int, help="Override START_YEAR from config when using --all-windows")

    args = parser.parse_args()

    # One-time CSV wiring of both exes.
    configure_mediator_csv()
    configure_karmalego_csv()

    if args.all_windows:
        effective_start = args.start_year if args.start_year is not None else START_YEAR
        windows = generate_windows(effective_start)
        log("")
        log("============================================")
        log("TIRP PIPELINE - FROM PATIENT LIST - CSV MODE (ALL WINDOWS)")
        log(f"File: {args.patients}  |  K={K}, STEP={STEP}, START={effective_start}")
        log(f"Total windows: {len(windows)}  |  MVS={MVS}")
        log("============================================")
        for i, (ks, ke) in enumerate(windows, 1):
            log("")
            log(f"========== WINDOW {i}/{len(windows)}: K=[{ks}-{ke}] ==========")
            run(args.patients, ks, ke)
        log("")
        log("============================================")
        log("ALL WINDOWS COMPLETE")
        log("============================================")
    else:
        if args.k_end is None:
            parser.error("--k-end is required when using --k-start")
        log("")
        log("============================================")
        log("TIRP PIPELINE - FROM PATIENT LIST - CSV MODE")
        log(f"File: {args.patients}  |  K=[{args.k_start}-{args.k_end}]  |  MVS={MVS}")
        log("============================================")
        run(args.patients, args.k_start, args.k_end)
        log("============================================")
        log("DONE")
        log("============================================")
