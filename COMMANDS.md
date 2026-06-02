# Commands Reference

All commands run from the pipeline folder:
```
cd "C:\Users\noama1\Desktop\karma\config files\pipeline"
```

---

## YES/NO Pipelines (DB-based)

```powershell
# Run full stroke pipeline (all windows, queries DB for patients)
.\venv\Scripts\python.exe pipeline_stroke.py

# Run full mortality pipeline (all windows, queries DB for patients)
.\venv\Scripts\python.exe pipeline_mortality.py
```

---

## All-Patients Pipeline (DB-based)

```powershell
# Run all windows, all patients, no YES/NO split
.\venv\Scripts\python.exe pipeline_all_patients.py
```

---

## Patient-List Pipeline (from file)

```powershell
# Single window - train set
.\venv\Scripts\python.exe pipeline_from_patient_list.py --patients train_ids.txt --k-start 2010 --k-end 2012

# Single window - test set
.\venv\Scripts\python.exe pipeline_from_patient_list.py --patients test_ids.txt --k-start 2015 --k-end 2017

# All windows (start year from config_all_patients.json)
.\venv\Scripts\python.exe pipeline_from_patient_list.py --patients train_ids.txt --all-windows

# All windows with custom start year
.\venv\Scripts\python.exe pipeline_from_patient_list.py --patients train_ids.txt --all-windows --start-year 2015

# All windows, test set, custom start year
.\venv\Scripts\python.exe pipeline_from_patient_list.py --patients test_ids.txt --all-windows --start-year 2015
```

---

## One-Off Scripts

```powershell
# Run stroke pipeline for specific hardcoded windows (edit WINDOWS list inside the file)
.\venv\Scripts\python.exe run_specific_windows_stroke.py
```

---

## Tests & Diagnostics

```powershell
# Test DB connection
.\venv\Scripts\python.exe test_pipeline.py sql

# Preview all windows that will be generated
.\venv\Scripts\python.exe test_pipeline.py windows

# Test stroke patient query for a Y range
.\venv\Scripts\python.exe test_pipeline.py stroke 2012 2013

# Test no-stroke patient query
.\venv\Scripts\python.exe test_pipeline.py nostroke 100

# Run a single window by number
.\venv\Scripts\python.exe test_pipeline.py window 1
```

---

## Dependencies

```powershell
# Install required packages
.\venv\Scripts\pip install pyodbc

# Upgrade pyodbc (fix Python 3.13 compatibility issue)
.\venv\Scripts\pip install --upgrade pyodbc

# Check pyodbc version
.\venv\Scripts\pip show pyodbc
```
