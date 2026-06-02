# TIRP Pipeline

## Overview

This project automates the discovery of **temporal patterns (TIRPs)** from patient medical records using **sliding time windows**.

It connects 3 systems into one workflow:

- **SQL Server** – selects patient groups
- **MEDIATOR** – creates temporal abstractions from patient history
- **KarmaLego** – mines temporal patterns from those abstractions

The pipeline is designed to compare:

- patients who **had a target outcome**
- patients who **did not have that outcome**

Current project variants:

- `pipeline_stroke.py` – stroke YES/NO pipeline
- `pipeline_mortality.py` – mortality YES/NO pipeline
- `pipeline_all_patients.py` – all patients, no YES/NO split
- `pipeline_from_patient_list.py` – all patients from a pre-defined ID file

---

## What this pipeline gives you

After each run, the pipeline creates **pattern output folders** for every time window.

Example:

```text
C:\Users\noama1\Desktop\karma\
├── 2000-2004_YES_patterns\
├── 2000-2004_NO_patterns\
├── 2002-2006_YES_patterns\
├── 2002-2006_NO_patterns\
├── 2004-2008_YES_patterns\
├── 2004-2008_NO_patterns\
└── ...
```

## Goal

**Input:** Patient medical records over time (diagnoses, lab results, medications, etc.)

**Output:** Two sets of temporal pattern files for each time window:
- `YYYY-YYYY_YES_patterns` - patterns found in patients who **had stroke**
- `YYYY-YYYY_NO_patterns` - patterns found in patients who **never had stroke**

These pattern files can later be analyzed to find discriminative patterns that appear more frequently in stroke patients vs. control patients.

---

## The Pipeline Explained

### What Are Sliding Windows?

We use two consecutive time windows:
- **K_range (Knowledge Window):** The period where we look for temporal patterns
- **Y_range (Prediction Window):** The period where we check if the patient had a stroke

Example with K=4 years, Y=3 years, STEP=2 years:
```
Timeline:  2000----2004----2007----2010----2013----2016...
           |--K1---|--Y1---|
                   |--K2---|--Y2---|
                           |--K3---|--Y3---|
```

Window 1: Find patterns in 2000-2004 for patients who had stroke in 2004-2007
Window 2: Find patterns in 2002-2006 for patients who had stroke in 2006-2009
...and so on.

### Pipeline Steps (Per Window)

**Step 0 - Cleanup:**
- Clear the output database table (removes previous abstractions)
- Clear the patient list file

**Step 1 - Find Stroke Patients:**
- Query database for patients who had `Stroke_Ischemic` during Y_range
- Save their IDs to `patient_list.txt`

**Step 2.1 - Calculate Abstractions (MEDIATOR):**
- Run MEDIATOR on the stroke patients
- MEDIATOR converts raw medical events in K_range into symbolic time intervals
- Output goes to `[AF_Simulation].[dbo].[OutputPatientsData]`

**Step 2.2 - Mine Patterns (KarmaLego):**
- Run KarmaLego on the abstractions
- KarmaLego finds frequent temporal patterns (TIRPs)
- Output saved to `YYYY-YYYY_YES_patterns` folder

**Step 3 - Cleanup:**
- Clear the output database table
- Clear the patient list file

**Step 4 - Find Control Patients:**
- Query database for patients who **NEVER had stroke at all**
- Select randomly 3× the number of stroke patients (for balanced comparison)
- Save their IDs to `patient_list.txt`

**Step 4.1 - Calculate Abstractions (MEDIATOR):**
- Same as Step 2.1, but for control patients

**Step 4.2 - Mine Patterns (KarmaLego):**
- Same as Step 2.2
- Output saved to `YYYY-YYYY_NO_patterns` folder

**Step 5 - Repeat:**
- Move to next window (shift by STEP years)
- Repeat until end of timeline

---

## Expected Output

After running the full pipeline, you will have folders like:
```
C:\Users\noama1\Desktop\karma\
├── 2000-2004_YES_patterns\    # Patterns from stroke patients (K=2000-2004)
├── 2000-2004_NO_patterns\     # Patterns from control patients (K=2000-2004)
├── 2002-2006_YES_patterns\
├── 2002-2006_NO_patterns\
├── 2004-2008_YES_patterns\
├── 2004-2008_NO_patterns\
└── ...
```

Each folder contains KarmaLego output files with the discovered TIRPs (temporal patterns), their support values, and statistics.

---

## Requirements

### Software
- Python 3.x
- `pyodbc` library: `pip install pyodbc`
- MEDIATOR executable (compiled .NET 8.0)
- KarmaLego executable (compiled .NET 8.0)
- SQL Server with ODBC driver

### Database Access
- Read access to input patient data
- Write access to output table (for MEDIATOR)

---

## Configuration

**All configuration is in JSON files beside each pipeline script. You MUST review and update these before running.**

| Pipeline | Config file |
|---|---|
| `pipeline_stroke.py` | `config_stroke.json` |
| `pipeline_mortality.py` | `config_mortality.json` |

The JSON is split into four sections:

### 1. Window Parameters (`window`)
```json
{
  "window": {
    "K": 4,
    "Y": 3,
    "STEP": 2,
    "START_YEAR": 2000,
    "END_DATE": "2024-10-08"
  }
}
```

### 2. Database Connection (`database`)
```json
{
  "database": {
    "SERVER_NAME": "MLS05-T\\MEDLAB_DEV",
    "OUTPUT_DATABASE": "AF_Simulation",
    "INPUT_DATABASE": "Af_Clalit_Community",
    "SQL_USERNAME": "visitors",
    "SQL_PASSWORD": "visitors",
    "PROJECT_ID": 40159
  }
}
```

`OUTPUT_DATABASE` is where MEDIATOR writes abstractions (`OutputPatientsData`).
`INPUT_DATABASE` is where patient records are read from (`InputPatientsData`).

### 3. File Paths (`paths`)
```json
{
  "paths": {
    "PATIENT_LIST_PATH": "C:\\Users\\noama1\\Desktop\\karma\\config files\\patient_list.txt",
    "APPSETTINGS_PATH":  "C:\\Users\\noama1\\Desktop\\karma\\KarmaLego\\...\\appsettings.json",
    "KL_CONFIG_PATH":    "C:\\Users\\noama1\\Desktop\\karma\\config files\\AF_KL_generic_config.json",
    "KARMALEGO_DIR":     "C:\\Users\\noama1\\Desktop\\karma\\KarmaLego\\...\\net8.0",
    "KARMALEGO_EXE":     "C:\\Users\\noama1\\Desktop\\karma\\KarmaLego\\...\\KarmaLegoConsoleApp.exe",
    "MEDIATOR_EXE":      "C:\\MediatorCore\\Mediator\\APICore\\bin\\Release\\net8.0\\API.exe",
    "RESULTS_BASE_PATH": "C:\\Users\\noama1\\Desktop\\karma"
  }
}
```

Note: `patient_list.txt` is created automatically if it does not exist.

### 4. KarmaLego Settings (`karmalego`)
```json
{
  "karmalego": {
    "MVS": 0.3,
    "domain_name": "AF_KL_Stroke",
    "concepts": "2000,2001,2002,...",
    "maxGap": 0,
    "timeUnit": "Days",
    "statistics_type_name": "HorizontalSupport"
  }
}
```

### 5. Runtime Settings (`runtime`)
```json
{
  "runtime": {
    "SHOW_TOOL_OUTPUT": true,
    "MEDIATOR_BATCH_SIZE": 200
  }
}
```

---

## Database Tables

### Input Table (read)
**`[Af_Clalit_Community].[dbo].[InputPatientsData]`**

| Column | Description |
|--------|-------------|
| PatientID | Unique patient identifier |
| ConceptName | Medical concept (e.g., 'Stroke_Ischemic') |
| StartTime | When the event occurred |

Stroke is identified by: `ConceptName LIKE 'Stroke_Ischemic'`

### Output Table (write/clear)
**`[AF_Simulation].[dbo].[OutputPatientsData]`**

MEDIATOR writes abstraction results here. The pipeline clears this table before each MEDIATOR run.

---

## Files

| File | Description |
|------|-------------|
| `pipeline_stroke.py` | Stroke YES/NO pipeline (queries DB for patients) |
| `pipeline_mortality.py` | Mortality YES/NO pipeline (queries DB for patients) |
| `pipeline_all_patients.py` | All-patients pipeline, no YES/NO split |
| `pipeline_from_patient_list.py` | All-patients pipeline from a pre-defined patient ID file |
| `run_specific_windows_stroke.py` | One-off script: runs stroke pipeline for specific windows only |
| `config_stroke.json` | Configuration for the stroke pipeline |
| `config_mortality.json` | Configuration for the mortality pipeline |
| `config_all_patients.json` | Configuration for the all-patients and patient-list pipelines |
| `patients/train_ids.txt` | Pre-defined patient ID list – training set |
| `patients/test_ids.txt` | Pre-defined patient ID list – test set |
| `test_pipeline.py` | Test utilities – test parts before full run |
| `README.md` | This documentation |
| `COMMANDS.md` | Quick reference for all run commands |

---

## Usage

### Step 1: Install Dependencies
```bash
pip install pyodbc
```

### Step 2: Update Configuration
Open `config_stroke.json` or `config_mortality.json` and update all values for your environment.

### Step 3: Test Before Running
```bash
# Test database connection
python test_pipeline.py sql

# Preview all windows that will be generated
python test_pipeline.py windows

# Test stroke patient query for specific Y_range
python test_pipeline.py stroke 2004 2007

# Test no-stroke patient query
python test_pipeline.py nostroke 100

# Run single window only (e.g., window #1)
python test_pipeline.py window 1
```

### Step 4: Run Full Pipeline

**YES/NO pipelines (DB-based patient selection):**
```bash
python pipeline_stroke.py
python pipeline_mortality.py
```

**All-patients pipeline (no YES/NO split):**
```bash
python pipeline_all_patients.py
```

---

## pipeline_from_patient_list.py

Runs the all-patients pattern extraction on a **fixed patient ID list** instead of querying the database.
Patient files live in the `patients/` subfolder, one ID per line.
All other settings (DB, paths, MVS, concepts) are loaded from `config_all_patients.json`.

### Single window
```bash
python pipeline_from_patient_list.py --patients train_ids.txt --k-start 2010 --k-end 2012
python pipeline_from_patient_list.py --patients test_ids.txt  --k-start 2015 --k-end 2017
```

### All windows (generated from config)
```bash
python pipeline_from_patient_list.py --patients train_ids.txt --all-windows
```
Uses `K`, `STEP`, `START_YEAR`, and `END_DATE` from `config_all_patients.json` to generate all windows automatically.

### All windows with a custom start year
```bash
python pipeline_from_patient_list.py --patients train_ids.txt --all-windows --start-year 2015
```
Overrides `START_YEAR` from the config without editing any file. The first window will be `[2015 → 2015+K]` and it slides forward by `STEP` until `END_DATE`.

### Output folder naming
Results are saved under `RESULTS_BASE_PATH` (from config) with this pattern:
```
{k_start}-{k_end}_{filename}_patterns
```
Example: `2010-2012_train_ids_patterns`, `2010-2012_test_ids_patterns`

---

## run_specific_windows_stroke.py

One-off script that runs the **stroke pipeline** for a hardcoded list of specific windows, without touching any config file.
Edit the `WINDOWS` list inside the file to change which windows to run.

```bash
python run_specific_windows_stroke.py
```

---

## Sample Log Output

```
[14:32:01] Starting pipeline: K=4, Y=3, STEP=2, MVS=0.3
[14:32:01] Total windows: 9

[14:32:01] === WINDOW 1/9: K=[2000-2004] Y=[2004-2007] ===
[14:32:01] Cleanup...
[14:32:02] Finding YES-stroke patients...
[14:32:02] Found 127 stroke patients
[14:32:02] Running MEDIATOR (YES)...
[14:32:15] Running KarmaLego (YES)...
[14:32:45] Cleanup...
[14:32:46] Finding NO-stroke patients (3x = 381)...
[14:32:46] Found 381 no-stroke patients
[14:32:46] Running MEDIATOR (NO)...
[14:33:10] Running KarmaLego (NO)...
[14:33:40] Window 1 complete.

[14:33:40] === WINDOW 2/9: K=[2002-2006] Y=[2006-2009] ===
...

[15:45:22] === PIPELINE COMPLETE ===
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| DB connection fails | Check SERVER_NAME, credentials, and network access |
| MEDIATOR not found | Verify MEDIATOR_EXE path exists |
| KarmaLego not found | Verify KARMALEGO_DIR and executable exist |
| No stroke patients found | Check Y_range dates and ConceptName in database |
| Permission denied | Check write access to output table and result folders |

---

## Notes

- The pipeline clears the output table before EACH MEDIATOR run (both YES and NO)
- Control patients are selected randomly each time (not deterministic)
- If fewer than 3× control patients exist, pipeline uses all available
- Windows stop generating when Y_range would exceed END_DATE