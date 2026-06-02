"""
TIRP Pipeline - From Patient List File
Runs all-patients pattern extraction for a given K window (or all windows),
using a pre-defined patient ID list instead of querying the DB.

Usage:
    # Single window:
    python pipeline_from_patient_list.py --patients train_ids.txt --k-start 2010 --k-end 2012

    # All windows (generated from config_all_patients.json: K, STEP, START_YEAR, END_DATE):
    python pipeline_from_patient_list.py --patients train_ids.txt --all-windows

Patient files must be in the patients/ subfolder (one ID per line).
All other settings (DB, paths, MVS, etc.) are loaded from config_all_patients.json.
"""

import argparse
import subprocess
import pyodbc
import json
import os
import time
from datetime import datetime

# =============================================================================
# LOAD CONFIGURATION
# =============================================================================

_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_all_patients.json")

with open(_CONFIG_FILE) as _f:
    _cfg = json.load(_f)

K                   = _cfg["window"]["K"]
STEP                = _cfg["window"]["STEP"]
START_YEAR          = _cfg["window"]["START_YEAR"]
END_DATE            = _cfg["window"]["END_DATE"]

MVS                 = _cfg["karmalego"]["MVS"]
KL_CONFIG           = {k: v for k, v in _cfg["karmalego"].items() if k != "MVS"}

SERVER_NAME         = _cfg["database"]["SERVER_NAME"]
OUTPUT_DATABASE     = _cfg["database"]["OUTPUT_DATABASE"]
SQL_USERNAME        = _cfg["database"]["SQL_USERNAME"]
SQL_PASSWORD        = _cfg["database"]["SQL_PASSWORD"]
PROJECT_ID          = _cfg["database"]["PROJECT_ID"]

PATIENT_LIST_PATH   = _cfg["paths"]["PATIENT_LIST_PATH"]
APPSETTINGS_PATH    = _cfg["paths"]["APPSETTINGS_PATH"]
KL_CONFIG_PATH      = _cfg["paths"]["KL_CONFIG_PATH"]
KARMALEGO_DIR       = _cfg["paths"]["KARMALEGO_DIR"]
KARMALEGO_EXE       = _cfg["paths"]["KARMALEGO_EXE"]
MEDIATOR_EXE        = _cfg["paths"]["MEDIATOR_EXE"]
RESULTS_BASE_PATH   = _cfg["paths"]["RESULTS_BASE_PATH"]

SHOW_TOOL_OUTPUT    = _cfg["runtime"]["SHOW_TOOL_OUTPUT"]
MEDIATOR_BATCH_SIZE = _cfg["runtime"]["MEDIATOR_BATCH_SIZE"]

PATIENTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "patients")

# =============================================================================
# HELPERS
# =============================================================================

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def get_db_connection():
    return pyodbc.connect(
        f"DRIVER={{SQL Server}};SERVER={SERVER_NAME};DATABASE={OUTPUT_DATABASE};"
        f"UID={SQL_USERNAME};PWD={SQL_PASSWORD};"
    )

def load_patients_from_file(filename):
    path = os.path.join(PATIENTS_DIR, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Patient file not found: {path}")
    with open(path, 'r') as f:
        ids = [line.strip() for line in f if line.strip()]
    return ids

def clear_patient_list():
    log("Clearing patient list...")
    with open(PATIENT_LIST_PATH, 'w') as f:
        f.write('')
    log("Patient list cleared.")

def clear_output_table():
    log("Clearing output table...")
    conn = get_db_connection()
    conn.cursor().execute(f"DELETE FROM [{OUTPUT_DATABASE}].[dbo].[OutputPatientsData]")
    conn.commit()
    conn.close()
    log("Output table cleared.")

def save_patients(patients):
    log(f"Saving {len(patients)} patients to file...")
    with open(PATIENT_LIST_PATH, 'w') as f:
        f.write(','.join(map(str, patients)))
    log("Patients saved.")

def run_mediator(patients, k_start, k_end):
    if not patients:
        return

    total_batches = (len(patients) + MEDIATOR_BATCH_SIZE - 1) // MEDIATOR_BATCH_SIZE
    log(f"Starting MEDIATOR ({len(patients)} patients in {total_batches} batches)...")

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
            raise

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM [{OUTPUT_DATABASE}].[dbo].[OutputPatientsData]")
        count = cursor.fetchone()[0]
        conn.close()
        log(f"MEDIATOR finished. Abstractions in table: {count}")
    except Exception as e:
        log(f"MEDIATOR finished. Could not count abstractions: {e}")

def update_appsettings(results_path):
    log(f"Updating AppSettings → ResultsPath: {results_path}")
    with open(APPSETTINGS_PATH, 'r') as f:
        settings = json.load(f)
    settings['AppSettings']['ResultsPath'] = results_path
    with open(APPSETTINGS_PATH, 'w') as f:
        json.dump(settings, f, indent=2)
        f.flush()
        os.fsync(f.fileno())

    time.sleep(0.5)

    with open(APPSETTINGS_PATH, 'r') as f:
        verify = json.load(f)
    log(f"AppSettings verified: {verify['AppSettings']['ResultsPath']}")

def update_kl_config():
    log("Updating KarmaLego config...")
    config = {**KL_CONFIG, "projectId": PROJECT_ID, "entities": PATIENT_LIST_PATH, "mvs": MVS}
    with open(KL_CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)
        f.flush()
        os.fsync(f.fileno())

    time.sleep(0.5)
    log("KarmaLego config updated.")

def run_karmalego(results_path):
    if not os.path.exists(results_path):
        os.makedirs(results_path)
        log(f"Created folder: {results_path}")
    else:
        log(f"Folder already exists: {results_path}")

    with open(APPSETTINGS_PATH, 'r') as f:
        check = json.load(f)
    log(f"Config check before run: {check['AppSettings']['ResultsPath']}")

    log("Starting KarmaLego...")
    if SHOW_TOOL_OUTPUT:
        result = subprocess.run([KARMALEGO_EXE], cwd=KARMALEGO_DIR, stdin=subprocess.DEVNULL)
    else:
        result = subprocess.run([KARMALEGO_EXE], cwd=KARMALEGO_DIR, stdin=subprocess.DEVNULL, capture_output=True)
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
    label = os.path.splitext(patients_file)[0]  # e.g. "train_ids" or "test_ids"

    log("--- STEP 0: CLEANUP ---")
    clear_output_table()
    clear_patient_list()

    log("--- STEP 1: LOAD PATIENTS FROM FILE ---")
    patients = load_patients_from_file(patients_file)
    log(f"Loaded {len(patients)} patients from {patients_file}")

    if not patients:
        log("No patients in file. Aborting.")
        return

    save_patients(patients)

    log("--- STEP 2: MEDIATOR ---")
    run_mediator(patients, k_start, k_end)

    log("--- STEP 3: KARMALEGO ---")
    results_path = f"{RESULTS_BASE_PATH}\\{k_start}-{k_end}_{label}_patterns"
    update_appsettings(results_path)
    update_kl_config()
    run_karmalego(results_path)

    log(f"Done. Results: {results_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run all-patients TIRP pipeline from a patient ID file.")
    parser.add_argument("--patients", required=True, help="Patient ID file in patients/ folder (e.g. train_ids.txt)")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all-windows", action="store_true", help="Run all windows generated from config_all_patients.json")
    group.add_argument("--k-start",     type=int,            help="K window start year for a single window (e.g. 2010)")
    parser.add_argument("--k-end",        type=int, help="K window end year (required with --k-start)")
    parser.add_argument("--start-year",   type=int, help="Override START_YEAR from config when using --all-windows (e.g. 2015)")

    args = parser.parse_args()

    if args.all_windows:
        effective_start = args.start_year if args.start_year is not None else START_YEAR
        windows = generate_windows(effective_start)
        log(f"")
        log(f"============================================")
        log(f"TIRP PIPELINE - FROM PATIENT LIST (ALL WINDOWS)")
        log(f"File: {args.patients}  |  K={K}, STEP={STEP}, START={effective_start}")
        log(f"Total windows: {len(windows)}  |  MVS={MVS}")
        log(f"============================================")
        for i, (ks, ke) in enumerate(windows, 1):
            log(f"")
            log(f"========== WINDOW {i}/{len(windows)}: K=[{ks}-{ke}] ==========")
            run(args.patients, ks, ke)
        log(f"")
        log(f"============================================")
        log(f"ALL WINDOWS COMPLETE")
        log(f"============================================")
    else:
        if args.k_end is None:
            parser.error("--k-end is required when using --k-start")
        log(f"")
        log(f"============================================")
        log(f"TIRP PIPELINE - FROM PATIENT LIST")
        log(f"File: {args.patients}  |  K=[{args.k_start}-{args.k_end}]  |  MVS={MVS}")
        log(f"============================================")
        run(args.patients, args.k_start, args.k_end)
        log(f"============================================")
        log(f"DONE")
        log(f"============================================")
