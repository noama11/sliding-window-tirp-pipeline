"""
TIRP Pipeline - MORTALITY Version
Run: python tirp_pipeline_mortality.py
"""

import subprocess
import pyodbc
import json
import os
import time
from datetime import datetime

# =============================================================================
# CONFIGURATION
# =============================================================================

K = 4                       # Knowledge window (years)
Y = 3                       # Prediction window (years)
STEP = 2                    # Sliding step (years)
START_YEAR = 2000
END_DATE = "2024-10-08"
MVS = 0.3                   # Minimum Vertical Support

# --- OUTPUT CONTROL ---
SHOW_TOOL_OUTPUT = True     # True = show MEDIATOR/KarmaLego output, False = hide
MEDIATOR_BATCH_SIZE = 50    # Patients per MEDIATOR batch (reduce if command too long error)

# --- PATHS ---
PATIENT_LIST_PATH = "C:\\Users\\noama1\\Desktop\\karma\\config files\\patient_list.txt"
APPSETTINGS_PATH = "C:\\Users\\noama1\\Desktop\\karma\\KarmaLego\\KarmaLegoConsoleApp\\bin\\Release\\net8.0\\appsettings.json"
KL_CONFIG_PATH = "C:\\Users\\noama1\\Desktop\\karma\\config files\\AF_KL_generic_config.json"
KARMALEGO_DIR = "C:\\Users\\noama1\\Desktop\\karma\\KarmaLego\\KarmaLegoConsoleApp\\bin\\Release\\net8.0"
KARMALEGO_EXE = "C:\\Users\\noama1\\Desktop\\karma\\KarmaLego\\KarmaLegoConsoleApp\\bin\\Release\\net8.0\\KarmaLegoConsoleApp.exe"
MEDIATOR_EXE = "C:\\MediatorCore\\Mediator\\APICore\\bin\\Release\\net8.0\\API.exe"
RESULTS_BASE_PATH = "C:\\Users\\noama1\\Desktop\\karma"

# --- DATABASE ---
SERVER_NAME = "MLS05-T\\MEDLAB_DEV"
DATABASE_NAME = "AF_Simulation"
INPUT_DATABASE = "Af_Clalit_Community"
SQL_USERNAME = "visitors"
SQL_PASSWORD = "visitors"
PROJECT_ID = 40159

# --- KARMALEGO FIXED CONFIG ---
KL_CONFIG = {
    "domain_name": "AF_KL_Stroke",
    "concepts": "2000,2001,2002,2003,2004,2005,2006,2007,2008,2009,2010,2011,1200,1201,1202,1100,1101,1102,1103,1104,1105,1106,1107,4000,4001,4002,4003,4004,4005,4006,4007,4008,4009,4010,4011,4012,4013,4014,4015,3000,3001,3002,3003",
    "maxGap": 0,
    "timeUnit": "Days",
    "statistics_type_name": "HorizontalSupport"
}

# =============================================================================
# HELPERS
# =============================================================================

def log(msg): 
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def get_db_connection():
    return pyodbc.connect(
        f"DRIVER={{SQL Server}};SERVER={SERVER_NAME};DATABASE={DATABASE_NAME};"
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
    conn.cursor().execute("DELETE FROM [AF_Simulation].[dbo].[OutputPatientsData]")
    conn.commit()
    conn.close()
    log("Output table cleared.")

def get_mortality_patients(y_start, y_end):
    """Get patients who died IN Y_range."""
    query = f"""
        SELECT DISTINCT PatientID 
        FROM [{INPUT_DATABASE}].[dbo].[InputPatientsData]
        WHERE ConceptName LIKE 'Date_Ptira'
          AND StartTime >= '{y_start}-01-01' AND StartTime < '{y_end}-01-01'
    """
    conn = get_db_connection()
    patients = [row[0] for row in conn.cursor().execute(query).fetchall()]
    conn.close()
    return patients

def get_alive_patients(count):
    """Get patients who NEVER died (random)."""
    query = f"""
        SELECT TOP ({count}) PatientID FROM (
            SELECT DISTINCT PatientID FROM [{INPUT_DATABASE}].[dbo].[InputPatientsData]
            WHERE PatientID NOT IN (
                SELECT DISTINCT PatientID FROM [{INPUT_DATABASE}].[dbo].[InputPatientsData]
                WHERE ConceptName LIKE 'Date_Ptira'
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
        cursor.execute("SELECT COUNT(*) FROM [AF_Simulation].[dbo].[OutputPatientsData]")
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
    # Note: KarmaLego may crash on "Press any key" with DEVNULL stdin, but work is done before that
    if SHOW_TOOL_OUTPUT:
        subprocess.run([KARMALEGO_EXE], cwd=KARMALEGO_DIR, stdin=subprocess.DEVNULL)
    else:
        subprocess.run([KARMALEGO_EXE], cwd=KARMALEGO_DIR, stdin=subprocess.DEVNULL, capture_output=True)
    
    # Check what files exist after run
    if os.path.exists(results_path):
        files = os.listdir(results_path)
        log(f"Files in folder: {len(files)} files")
        if files:
            log(f"  {files[:5]}")  # Show first 5 files
        else:
            log("WARNING: Folder is empty!")
    else:
        log("WARNING: Folder does not exist after KarmaLego!")
    
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
    
    # DEAD patients
    log("--- STEP 1: FIND MORTALITY PATIENTS ---")
    yes_patients = get_mortality_patients(y_start, y_end)
    log(f"Found {len(yes_patients)} mortality patients")
    
    if not yes_patients:
        log("No patients found. Skipping window.")
        return
    
    save_patients(yes_patients)
    
    # MEDIATOR for DEAD
    log("--- STEP 2.1: MEDIATOR (DEAD) ---")
    run_mediator(yes_patients, k_start, k_end)
    
    # KarmaLego for DEAD
    log("--- STEP 2.2: KARMALEGO (DEAD) ---")
    results_path_yes = f"{RESULTS_BASE_PATH}\\{k_start}-{k_end}_MORTALITY_YES_patterns"
    update_appsettings(results_path_yes)
    update_kl_config()
    run_karmalego(results_path_yes)
    
    # Cleanup
    log("--- STEP 3: CLEANUP ---")
    clear_output_table()
    clear_patient_list()
    
    # ALIVE patients (3x)
    log("--- STEP 4: FIND ALIVE PATIENTS ---")
    no_count = len(yes_patients) * 3
    log(f"Looking for {no_count} patients (3x mortality count)...")
    no_patients = get_alive_patients(no_count)
    log(f"Found {len(no_patients)} alive patients")
    
    if not no_patients:
        log("No patients found. Skipping NO-patterns.")
        return
    
    save_patients(no_patients)
    
    # MEDIATOR for ALIVE
    log("--- STEP 4.1: MEDIATOR (ALIVE) ---")
    run_mediator(no_patients, k_start, k_end)
    
    # KarmaLego for ALIVE
    log("--- STEP 4.2: KARMALEGO (ALIVE) ---")
    results_path_no = f"{RESULTS_BASE_PATH}\\{k_start}-{k_end}_MORTALITY_NO_patterns"
    update_appsettings(results_path_no)
    update_kl_config()
    run_karmalego(results_path_no)
    
    log(f"========== WINDOW {window_num} COMPLETE ==========")


def run_pipeline():
    """Run full pipeline on all windows."""
    windows = generate_windows()
    log(f"")
    log(f"============================================")
    log(f"TIRP PIPELINE STARTING - MORTALITY")
    log(f"Config: K={K}, Y={Y}, STEP={STEP}, MVS={MVS}")
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