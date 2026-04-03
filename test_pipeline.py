"""
TIRP Pipeline - Test Utilities
Use this to test specific parts before running full pipeline.

Usage:
    python test_pipeline.py windows          # Show all windows
    python test_pipeline.py window 1         # Run single window (1-indexed)
    python test_pipeline.py sql              # Test DB connection
    python test_pipeline.py stroke 2004 2007 # Test stroke query for Y_range
    python test_pipeline.py nostroke 100     # Test no-stroke query (get 100)
"""

import sys
from tirp_pipeline import (
    K, Y, STEP, MVS, START_YEAR, END_DATE, SHOW_TOOL_OUTPUT,
    generate_windows, get_db_connection,
    get_stroke_patients, get_no_stroke_patients,
    process_window, log
)

def show_windows():
    """Display all windows that will be generated."""
    windows = generate_windows()
    print(f"\nConfig: K={K}, Y={Y}, STEP={STEP}, MVS={MVS}")
    print(f"Timeline: {START_YEAR} to {END_DATE}")
    print(f"Show tool output: {SHOW_TOOL_OUTPUT}")
    print(f"Total windows: {len(windows)}\n")
    print(f"{'#':<4} {'K_range':<12} {'Y_range':<12}")
    print("-" * 30)
    for i, (k_start, k_end, y_start, y_end) in enumerate(windows, 1):
        print(f"{i:<4} {k_start}-{k_end:<7} {y_start}-{y_end}")

def test_single_window(window_num):
    """Run a single window by number (1-indexed)."""
    windows = generate_windows()
    if window_num < 1 or window_num > len(windows):
        print(f"Invalid window number. Valid: 1-{len(windows)}")
        return
    k_start, k_end, y_start, y_end = windows[window_num - 1]
    process_window(k_start, k_end, y_start, y_end, window_num, len(windows))

def test_db_connection():
    """Test database connection."""
    try:
        conn = get_db_connection()
        print("DB connection: OK")
        conn.close()
    except Exception as e:
        print(f"DB connection: FAILED - {e}")

def test_stroke_query(y_start, y_end):
    """Test stroke patient query for given Y_range."""
    print(f"Querying stroke patients in {y_start}-{y_end}...")
    patients = get_stroke_patients(y_start, y_end)
    print(f"Found: {len(patients)} patients")
    if patients and len(patients) <= 20:
        print(f"IDs: {patients}")
    elif patients:
        print(f"First 20: {patients[:20]}")

def test_no_stroke_query(count):
    """Test no-stroke patient query."""
    print(f"Querying {count} no-stroke patients...")
    patients = get_no_stroke_patients(count)
    print(f"Found: {len(patients)} patients")
    if patients and len(patients) <= 20:
        print(f"IDs: {patients}")
    elif patients:
        print(f"First 20: {patients[:20]}")

def print_usage():
    print(__doc__)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(0)
    
    cmd = sys.argv[1].lower()
    
    if cmd == "windows":
        show_windows()
    elif cmd == "window" and len(sys.argv) == 3:
        test_single_window(int(sys.argv[2]))
    elif cmd == "sql":
        test_db_connection()
    elif cmd == "stroke" and len(sys.argv) == 4:
        test_stroke_query(int(sys.argv[2]), int(sys.argv[3]))
    elif cmd == "nostroke" and len(sys.argv) == 3:
        test_no_stroke_query(int(sys.argv[2]))
    else:
        print_usage()