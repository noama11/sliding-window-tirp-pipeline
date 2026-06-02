"""
One-off: run stroke pipeline for specific K windows only.
Does not modify any existing config or pipeline files.
"""
from pipeline_stroke import process_window, log

WINDOWS = [
    (2015, 2017, 2017, 2018),
    (2016, 2018, 2018, 2019),
]

log(f"Running {len(WINDOWS)} specific windows")
for i, (k_start, k_end, y_start, y_end) in enumerate(WINDOWS, 1):
    process_window(k_start, k_end, y_start, y_end, i, len(WINDOWS))
log("Done.")
