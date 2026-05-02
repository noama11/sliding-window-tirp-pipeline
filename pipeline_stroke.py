"""
TIRP Pipeline - Stroke
Run: python pipeline_stroke.py
Config: config_stroke.json
"""

import subprocess
import pyodbc
import json
import os
import time
from datetime import datetime

# =============================================================================
# LOAD CONFIGURATION
# =============================================================================

_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_stroke.json")

with open(_CONFIG_FILE) as _f:
    _cfg = json.load(_f)

K                   = _cfg["window"]["K"]
Y                   = _cfg["window"]["Y"]
STEP                = _cfg["window"]["STEP"]
START_YEAR          = _cfg["window"]["START_YEAR"]
END_DATE            = _cfg["window"]["END_DATE"]

MVS                 = _cfg["karmalego"]["MVS"]
KL_CONFIG           = {k: v for k, v in _cfg["karmalego"].items() if k != "MVS"}

SERVER_NAME         = _cfg["database"]["SERVER_NAME"]
OUTPUT_DATABASE     = _cfg["database"]["OUTPUT_DATABASE"]
INPUT_DATABASE      = _cfg["database"]["INPUT_DATABASE"]
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

def generate_windows():
    """Generate all (k_start, k_end, y_start, y_end) windows."""
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

def get_stroke_patients(y_start, y_end):
    """Get patients with stroke IN Y_range."""
    query = f"""
        SELECT DISTINCT PatientID
        FROM [{INPUT_DATABASE}].[dbo].[InputPatientsData]
        WHERE ConceptName LIKE 'Stroke_Ischemic'
          AND StartTime >= '{y_start}-01-01' AND StartTime < '{y_end}-01-01'
    """
    conn = get_db_connection()
    patients = [row[0] for row in conn.cursor().execute(query).fetchall()]
    conn.close()
    return patients

def get_no_stroke_patients(count):
    """Get patients who NEVER had stroke (random)."""
    query = f"""
        SELECT TOP ({count}) PatientID FROM (
            SELECT DISTINCT PatientID FROM [{INPUT_DATABASE}].[dbo].[InputPatientsData]
            WHERE PatientID NOT IN (
                SELECT DISTINCT PatientID FROM [{INPUT_DATABASE}].[dbo].[InputPatientsData]
                WHERE ConceptName LIKE 'Stroke_Ischemic'
            )
        ) AS T ORDER BY NEWID()
    """
    conn = get_db_connection()
    patients = [row[0] for row in conn.cursor().execute(query).fetchall()]
    conn.close()
    return patients

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

    # Check how many abstractions were created
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
        os.fsync(f.fileno())  # Force write to disk

    time.sleep(0.5)  # Wait for file system

    # Verify it was saved correctly
    with open(APPSETTINGS_PATH, 'r') as f:
        verify = json.load(f)
    saved_path = verify['AppSettings']['ResultsPath']
    log(f"AppSettings verified: {saved_path}")

def update_kl_config():
    log("Updating KarmaLego config...")
    config = {**KL_CONFIG, "projectId": PROJECT_ID, "entities": PATIENT_LIST_PATH, "mvs": MVS}
    with open(KL_CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)
        f.flush()
        os.fsync(f.fileno())  # Force write to disk

    time.sleep(0.5)  # Wait for file system
    log("KarmaLego config updated.")

def run_karmalego(results_path):
    # Create output folder if it doesn't exist
    log(f"Results path: {results_path}")
    if not os.path.exists(results_path):
        os.makedirs(results_path)
        log(f"Created folder: {results_path}")
    else:
        log(f"Folder already exists: {results_path}")

    # Verify config BEFORE running
    with open(APPSETTINGS_PATH, 'r') as f:
        check = json.load(f)
    log(f"Config check before run: {check['AppSettings']['ResultsPath']}")

    log("Starting KarmaLego...")
    if SHOW_TOOL_OUTPUT:
        result = subprocess.run([KARMALEGO_EXE], cwd=KARMALEGO_DIR, stdin=subprocess.DEVNULL)
    else:
        result = subprocess.run([KARMALEGO_EXE], cwd=KARMALEGO_DIR, stdin=subprocess.DEVNULL, capture_output=True)
    log(f"KarmaLego exit code: {result.returncode}")

    # Check what files exist after run (recursive)
    if os.path.exists(results_path):
        for root, dirs, files in os.walk(results_path):
            rel = os.path.relpath(root, results_path)
            prefix = "" if rel == "." else f"{rel}\\"
            for fname in files:
                log(f"  {prefix}{fname}")
        total = sum(len(fs) for _, _, fs in os.walk(results_path))
        log(f"Total files in output: {total}")
    else:
        log(f"WARNING: Folder does not exist after KarmaLego!")

    log("KarmaLego finished.")

# =============================================================================
# PIPELINE
# =============================================================================

def process_window(k_start, k_end, y_start, y_end, window_num, total):
    """Process a single window."""
    log(f"")
    log(f"========== WINDOW {window_num}/{total}: K=[{k_start}-{k_end}] Y=[{y_start}-{y_end}] ==========")

    # Cleanup
    log("--- STEP 0: CLEANUP ---")
    clear_output_table()
    clear_patient_list()

    # YES-stroke patients
    log("--- STEP 1: FIND YES-STROKE PATIENTS ---")
    yes_patients = get_stroke_patients(y_start, y_end)
    log(f"Found {len(yes_patients)} stroke patients")

    if not yes_patients:
        log("No patients found. Skipping window.")
        return

    save_patients(yes_patients)

    # MEDIATOR for YES
    log("--- STEP 2.1: MEDIATOR (YES) ---")
    run_mediator(yes_patients, k_start, k_end)

    # KarmaLego for YES
    log("--- STEP 2.2: KARMALEGO (YES) ---")
    results_path_yes = f"{RESULTS_BASE_PATH}\\{k_start}-{k_end}_YES_patterns"
    update_appsettings(results_path_yes)
    update_kl_config()
    run_karmalego(results_path_yes)

    # Cleanup
    log("--- STEP 3: CLEANUP ---")
    clear_output_table()
    clear_patient_list()

    # NO-stroke patients (3x)
    log("--- STEP 4: FIND NO-STROKE PATIENTS ---")
    no_count = len(yes_patients) * 3
    log(f"Looking for {no_count} patients (3x stroke count)...")
    no_patients = get_no_stroke_patients(no_count)
    log(f"Found {len(no_patients)} no-stroke patients")

    if not no_patients:
        log("No patients found. Skipping NO-patterns.")
        return

    save_patients(no_patients)

    # MEDIATOR for NO
    log("--- STEP 4.1: MEDIATOR (NO) ---")
    run_mediator(no_patients, k_start, k_end)

    # KarmaLego for NO
    log("--- STEP 4.2: KARMALEGO (NO) ---")
    results_path_no = f"{RESULTS_BASE_PATH}\\{k_start}-{k_end}_NO_patterns"
    update_appsettings(results_path_no)
    update_kl_config()
    run_karmalego(results_path_no)

    log(f"========== WINDOW {window_num} COMPLETE ==========")


def run_pipeline():
    """Run full pipeline on all windows."""
    windows = generate_windows()
    log(f"")
    log(f"============================================")
    log(f"TIRP PIPELINE STARTING - STROKE")
    log(f"Config: K={K}, Y={Y}, STEP={STEP}, MVS={MVS}")
    log(f"Output DB: {OUTPUT_DATABASE} | Input DB: {INPUT_DATABASE}")
    log(f"Total windows: {len(windows)}")
    log(f"Show tool output: {SHOW_TOOL_OUTPUT}")
    log(f"============================================")

    for i, (k_start, k_end, y_start, y_end) in enumerate(windows, 1):
        process_window(k_start, k_end, y_start, y_end, i, len(windows))

    log(f"")
    log(f"============================================")
    log(f"PIPELINE COMPLETE")
    log(f"============================================")


if __name__ == "__main__":
    run_pipeline()
