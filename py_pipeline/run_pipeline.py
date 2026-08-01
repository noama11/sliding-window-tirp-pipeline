"""
Self-contained pure-Python TIRP pipeline  (Mediator + KarmaLego, no DB, no .NET).

Same design as the original pipeline_stroke.py / pipeline_mortality.py:

  For each sliding window:
    K-window [k_start, k_end]  -> observation period (the Mediator abstracts here)
    Y-window [y_start, y_end]  -> outcome period right after K (y_start = k_end,
                                  y_end = y_start + Y)

    YES cohort = patients whose EVENT_CONCEPT falls in the Y-window
        -> Mediator(K-window) -> KarmaLego -> results/<k_start>-<k_end>_<P>YES_patterns
    NO  cohort = NEGATIVE_RATIO x |YES| patients who never had EVENT_CONCEPT
        -> Mediator(K-window) -> KarmaLego -> results/<k_start>-<k_end>_<P>NO_patterns

  Windows slide by STEP until y_end passes END_DATE.

Which outcome is being predicted is a CONFIG SWITCH, not a separate script:
config.json's `label` picks an entry of `labels` (stroke -> Stroke_Ischemic,
mortality -> Date_Ptira), each carrying its own KarmaLego concept list and
results-folder prefix. That is the only thing that differed between the two
original pipelines.

Everything needed is inside this folder: the engines (pyengine/), the knowledge
base (tak_entities/ + tak_2700.json), the concept dictionary (data/) and a
20-patient sample to smoke-test on. Standard library only -- no pip install, no
SQL Server, no binaries, no network. Paths resolve against THIS file, so the
folder can be copied or unzipped anywhere.

Requires: Python 3.8+ and nothing else.

Usage (from inside this folder):
    python run_pipeline.py                      # every window, YES + NO each
    python run_pipeline.py --window 2015        # just the window whose k_start = 2015
    python run_pipeline.py --label mortality    # predict death instead of stroke
    python run_pipeline.py --list-windows       # print the window plan and exit
    python run_pipeline.py --data-dir /path/to/export   # a different export
"""

import argparse
import csv
import json
import os
import random
import shutil
import sys
import time
from datetime import datetime, timezone

# =============================================================================
# LAYOUT — every path derives from this file, so the folder is portable
# =============================================================================

BUNDLE = os.path.dirname(os.path.abspath(__file__))


def P(*parts):
    return os.path.join(BUNDLE, *parts)


CONFIG_FILE = P("config.json")
PYENGINE_DIR = P("pyengine")
TAK_DIR = P("tak_entities")
WORKSPACE = P("workspace")
RUN_DATA_DIR = os.path.join(WORKSPACE, "run_data")

# The Mediator port and the KarmaLego port. The mediator modules import each
# other flat (`import pattern`), so their own folder goes on sys.path too.
for _p in (PYENGINE_DIR, os.path.join(PYENGINE_DIR, "mediator")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import karmalego                       # noqa: E402  pyengine/karmalego.py
import tak_parse                       # noqa: E402  pyengine/mediator/tak_parse.py
from engine import Engine              # noqa: E402  pyengine/mediator/engine.py

# Mediator "*" = every computable concept, i.e. AbstractConcept (state / trend /
# pattern) or Context -- Controller.cs isComputableConcept. Raws and events are
# inputs and never appear in abstractions.csv.
COMPUTABLE_OPS = ("state", "trend", "pattern", "context")

# =============================================================================
# CONFIG
# =============================================================================


def load_config(path=CONFIG_FILE):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


class Settings:
    """Everything a run needs, resolved from config.json + CLI overrides."""

    def __init__(self, cfg, label=None, data_dir=None, results_dir=None):
        w = cfg["window"]
        self.K = w["K"]
        self.Y = w["Y"]
        self.STEP = w["STEP"]
        self.START_YEAR = w["START_YEAR"]
        self.END_DATE = w["END_DATE"]

        c = cfg["cohort"]
        self.NEGATIVE_RATIO = c["NEGATIVE_RATIO"]
        self.SEED = c["SEED"]

        self.label = label or cfg.get("label", "stroke")
        if self.label not in cfg.get("labels", {}):
            available = ", ".join(sorted(cfg.get("labels", {})))
            die(f"unknown label {self.label!r}. config.json defines: {available}")
        lab = cfg["labels"][self.label]
        self.EVENT_CONCEPT = lab["EVENT_CONCEPT"]
        self.RESULTS_PREFIX = lab.get("RESULTS_PREFIX", "")
        self.KL_CONCEPTS = str(lab.get("KL_CONCEPTS", "*")).strip()

        k = cfg["karmalego"]
        self.MVS = k["MVS"]
        self.MAX_LEGO_LEVEL = k.get("MAX_LEGO_LEVEL", karmalego.DEFAULT_MAX_LEGO_LEVEL)
        self.DOMAIN = k.get("domain_name", "AF_KL_Stroke")
        self.MAX_GAP = k.get("maxGap", 0)
        self.TIME_UNIT = k.get("timeUnit", "Days")
        self.STATISTICS = k.get("statistics_type_name", "HorizontalSupport")

        self.PROJECT_ID = cfg["project"]["PROJECT_ID"]
        self.KB_ID = cfg["project"].get("KB_ID")
        self.SHOW_PROGRESS = cfg.get("runtime", {}).get("SHOW_PROGRESS", True)

        want = data_dir or cfg.get("data", {}).get("data_dir", "data")
        self.DATA_DIR = want if os.path.isabs(want) else P(want)
        self.RAW_EVENTS = os.path.join(self.DATA_DIR, "raw_events.csv")

        rd = results_dir or "results"
        self.RESULTS_DIR = rd if os.path.isabs(rd) else P(rd)
        # Archive each window's Mediator output next to its patterns. Off by
        # default: on a real export these are ~45 MB per cohort, so a 12-window
        # run adds ~1 GB.
        self.KEEP_ABSTRACTIONS = False

    # ---- derived -----------------------------------------------------------
    def cohort_dir(self, leg):
        return os.path.join(RUN_DATA_DIR, leg)

    def results_path(self, k_start, k_end, leg):
        return os.path.join(
            self.RESULTS_DIR,
            f"{k_start}-{k_end}_{self.RESULTS_PREFIX}{leg}_patterns")

    def kl_concepts(self):
        if self.KL_CONCEPTS == "*":
            return "*"
        return [c.strip() for c in self.KL_CONCEPTS.split(",") if c.strip()]


# =============================================================================
# HELPERS
# =============================================================================


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def die(msg, code=2):
    log("ERROR: " + msg)
    sys.exit(code)


def _parse_dt(s):
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def preflight(S):
    """Fail early and legibly rather than halfway through a long run."""
    if not os.path.exists(S.RAW_EVENTS):
        die(f"no raw_events.csv in {S.DATA_DIR}.\n"
            "       Put your InputPatientsData export there with columns\n"
            "       PatientID,ConceptName,StartTime,EndTime,Value\n"
            "       (or point config.json data.data_dir / --data-dir at it).")
    # knowledge_table.csv and projects.csv are OPTIONAL. The knowledge base is
    # the source of truth for both: the KB id comes from projects.csv, config, or
    # the single tak_entities/<kb> folder, and the concept dictionary is derived
    # from the concept XMLs when not supplied. So raw_events.csv + tak_entities/
    # is a complete input set -- see kb_id() and resolve_knowledge_table().

    # The KarmaLego port implements exactly one configuration; refuse anything
    # else rather than quietly computing something different.
    bad = []
    if float(S.MAX_GAP) != float(karmalego.DEFAULT_MAX_GAP):
        bad.append(f"maxGap={S.MAX_GAP} (this engine implements {karmalego.DEFAULT_MAX_GAP})")
    if str(S.TIME_UNIT).lower() != "days":
        bad.append(f"timeUnit={S.TIME_UNIT} (this engine implements Days)")
    if S.STATISTICS != "HorizontalSupport":
        bad.append(f"statistics_type_name={S.STATISTICS} (this engine implements HorizontalSupport)")
    if bad:
        die("config.json karmalego settings this engine does not implement:\n       "
            + "\n       ".join(bad))


def kb_id(S):
    """Which knowledge base to load, in order of preference:

      1. projects.csv (PROJECT_ID -> Kb_ID) -- what the .NET engines use
      2. config.json project.KB_ID          -- explicit override
      3. the only folder under tak_entities/ -- the common case

    projects.csv is therefore optional: it carries a single mapping and nothing
    downstream reads it, so a research-room folder needs only raw_events.csv and
    the knowledge base."""
    if "kb" in _TAK_CACHE:
        return _TAK_CACHE["kb"]
    kb = source = None
    for cand in (os.path.join(S.DATA_DIR, "projects.csv"), P("data", "projects.csv")):
        if not os.path.exists(cand):
            continue
        with open(cand, encoding="utf-8-sig", newline="") as f:
            r = csv.reader(f)
            next(r, None)
            for row in r:
                if len(row) >= 3 and row[0].strip() == str(S.PROJECT_ID):
                    kb, source = row[2].strip(), os.path.basename(cand)
                    break
        if kb:
            break
    if kb is None and S.KB_ID:
        kb, source = str(S.KB_ID), "config.json project.KB_ID"
    if kb is None and os.path.isdir(TAK_DIR):
        subs = sorted(d for d in os.listdir(TAK_DIR)
                      if os.path.isdir(os.path.join(TAK_DIR, d)))
        if len(subs) == 1:
            kb, source = subs[0], "the only folder in tak_entities/"
        elif len(subs) > 1:
            die(f"several knowledge bases in tak_entities/ ({', '.join(subs)}) and no "
                f"projects.csv entry for PROJECT_ID {S.PROJECT_ID}. Set project.KB_ID "
                "in config.json to choose one.")
    if kb is None:
        die("cannot determine the knowledge base: no projects.csv, no "
            "project.KB_ID in config.json, and no tak_entities/<kb>/ folder.")
    log(f"  knowledge base id {kb} (from {source})")
    _TAK_CACHE["kb"] = kb
    return kb


def resolve_knowledge_table(S):
    """Path to the concept dictionary, generating it if none was supplied.

    knowledge_table.csv is derived data -- build_knowledge_table.py produces it
    from the concept XMLs. If neither the data folder nor the bundle ships one,
    generate it rather than refusing to run. That is what makes
    raw_events.csv + tak_entities/ a complete input set."""
    if "kt" in _TAK_CACHE:
        return _TAK_CACHE["kt"]
    for cand in (os.path.join(S.DATA_DIR, "knowledge_table.csv"),
                 P("data", "knowledge_table.csv")):
        if os.path.exists(cand):
            log(f"  concept dictionary: {cand}")
            _TAK_CACHE["kt"] = cand
            return cand

    import build_knowledge_table as bkt          # noqa: E402  same folder
    with open(tak_json(S), encoding="utf-8") as fh:
        concepts = json.load(fh)["concepts"]
    rows = sorted(((c["name"], str(cid), bkt.allowed_values(c))
                   for cid, c in concepts.items()),
                  key=lambda r: (int(r[1]) if r[1].isdigit() else 1 << 30, r[0]))
    out = os.path.join(WORKSPACE, "knowledge_table.csv")
    os.makedirs(WORKSPACE, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["ConceptName", "ConceptID", "AllowedValues"])
        w.writerows(rows)
    log(f"  concept dictionary: none supplied — generated {len(rows)} rows "
        f"from the knowledge base -> {out}")
    _TAK_CACHE["kt"] = out
    return out


_TAK_CACHE = {}


def tak_json(S):
    """The concept map the Mediator reads. Parsed from tak_entities/<kb>/*.xml
    when that folder is present (so the KB stays editable), else the pre-parsed
    tak_<kb>.json shipped with the bundle."""
    if "path" in _TAK_CACHE:
        return _TAK_CACHE["path"]
    kb = kb_id(S)
    kb_dir = os.path.join(TAK_DIR, kb)
    if os.path.isdir(kb_dir):
        out = os.path.join(WORKSPACE, f"tak_{kb}.json")
        os.makedirs(WORKSPACE, exist_ok=True)
        concepts = tak_parse.build_json(kb_dir, out)
        log(f"  TAK KB {kb}: parsed {len(concepts)} concepts from tak_entities/{kb}")
    else:
        out = P(f"tak_{kb}.json")
        if not os.path.exists(out):
            die(f"no tak_entities/{kb}/ folder and no pre-parsed {out}.")
        log(f"  TAK KB {kb}: using pre-parsed {os.path.basename(out)}")
    _TAK_CACHE["path"] = out
    return out


def computable_names(tak_path):
    with open(tak_path, encoding="utf-8") as f:
        concepts = json.load(f)["concepts"]
    return [c["name"] for c in concepts.values() if c["op_type"] in COMPUTABLE_OPS]


# =============================================================================
# COHORT SELECTION  (over data/raw_events.csv, the InputPatientsData stand-in)
# =============================================================================

_RAW_CACHE = {}


def load_raw_index(S):
    """One pass over raw_events.csv: (all patient ids, [(pid, event_datetime)]).
    Cached, so a full 12-window run scans the big file for cohorts exactly once."""
    if "idx" not in _RAW_CACHE:
        t0 = time.time()
        all_p, events = set(), []
        target = S.EVENT_CONCEPT.strip().lower()
        with open(S.RAW_EVENTS, encoding="utf-8-sig", newline="") as f:
            r = csv.reader(f)
            next(r, None)
            for row in r:
                if len(row) < 3:
                    continue
                pid = row[0].strip()
                if not pid:
                    continue
                all_p.add(pid)
                if row[1].strip().lower() == target:
                    dt = _parse_dt(row[2])
                    if dt is not None:
                        events.append((pid, dt))
        _RAW_CACHE["idx"] = (all_p, events)
        log(f"  indexed {len(all_p)} patients, {len(events)} '{S.EVENT_CONCEPT}' "
            f"events in {time.time() - t0:.0f}s")
        if not events:
            log(f"  WARNING: no '{S.EVENT_CONCEPT}' rows at all -- every window "
                f"will have an empty YES cohort. Check the label's EVENT_CONCEPT.")
    return _RAW_CACHE["idx"]


def get_positive_patients(S, y_start, y_end):
    """YES cohort: distinct patients with an EVENT_CONCEPT event in [y_start, y_end)."""
    lo, hi = datetime(y_start, 1, 1), datetime(y_end, 1, 1)
    _all, events = load_raw_index(S)
    return sorted({pid for pid, dt in events if lo <= dt < hi},
                  key=lambda x: (len(x), x))


def get_negative_patients(S, count):
    """NO cohort: `count` patients who NEVER had EVENT_CONCEPT, seeded sample."""
    all_p, events = load_raw_index(S)
    positives = {pid for pid, _dt in events}
    pool = sorted(all_p - positives, key=lambda x: (len(x), x))
    k = min(count, len(pool))
    if k < count:
        log(f"  NOTE: only {len(pool)} eligible controls; sampling {k} (< {count}).")
    return random.Random(S.SEED).sample(pool, k)


# =============================================================================
# PER-WINDOW DATA PREP
# =============================================================================


def prepare_window_data(S, legs):
    """Filter the (large) source table down to each cohort in ONE streaming pass.

    `legs` is [(name, patients), ...]. Both cohorts of a window are cut at once
    because the pass over raw_events.csv dominates the wall clock on a real
    export -- doing it per cohort would read the whole file twice per window.

    Each leg gets its own folder with the cohort's raw rows (duplicates kept,
    for KarmaLego) and its deduplicated Mediator input. Results are identical to
    filtering nothing -- the engines only ever look at cohort patients anyway --
    this is purely speed and memory."""
    t0 = time.time()
    sets = {name: set(map(str, pats)) for name, pats in legs}
    out = {}
    header = ["PatientID", "ConceptName", "StartTime", "EndTime", "Value"]

    for name, _pats in legs:
        d = S.cohort_dir(name)
        if os.path.exists(d):
            shutil.rmtree(d)
        os.makedirs(d, exist_ok=True)
        fraw = open(os.path.join(d, "raw_events.csv"), "w", encoding="utf-8", newline="")
        fmed = open(os.path.join(d, "mediator_raw_events.csv"), "w", encoding="utf-8", newline="")
        out[name] = {"fraw": fraw, "fmed": fmed,
                     "wraw": csv.writer(fraw, lineterminator="\n"),
                     "wmed": csv.writer(fmed, lineterminator="\n"),
                     "seen": set(), "n": 0}

    with open(S.RAW_EVENTS, encoding="utf-8-sig", newline="") as fi:
        r = csv.reader(fi)
        header = next(r, None) or header
        for st in out.values():
            st["wraw"].writerow(header)
            st["wmed"].writerow(header)
        for row in r:
            if not row:
                continue
            pid = row[0].strip()
            for name in out:
                if pid in sets[name]:
                    st = out[name]
                    st["wraw"].writerow(row)
                    st["n"] += 1
                    key = tuple(row)
                    if key not in st["seen"]:
                        st["seen"].add(key)
                        st["wmed"].writerow(row)

    for name, st in out.items():
        st["fraw"].close()
        st["fmed"].close()
        # The concept dictionary is patient-independent; KarmaLego reads it from
        # the cohort folder. projects.csv is copied when available purely so the
        # folder also satisfies the .NET DataAccessCsv contract -- nothing here
        # reads it.
        shutil.copyfile(resolve_knowledge_table(S),
                        os.path.join(S.cohort_dir(name), "knowledge_table.csv"))
        for cand in (os.path.join(S.DATA_DIR, "projects.csv"), P("data", "projects.csv")):
            if os.path.exists(cand):
                shutil.copyfile(cand, os.path.join(S.cohort_dir(name), "projects.csv"))
                break
        log(f"  {name} cohort: {st['n']} raw rows ({len(st['seen'])} distinct) "
            f"for {len(sets[name])} patients")
    log(f"  data prep: {time.time() - t0:.0f}s (one pass over {os.path.basename(S.RAW_EVENTS)})")


# =============================================================================
# ENGINE STEPS
# =============================================================================


def mediator_window(k_start, k_end):
    """The [Ws, We] the Mediator abstracts over.

    The .NET API parses its window string with `AssumeLocal | AdjustToUniversal`
    (Controller.ParseDateOrTime): it reads the endpoints as LOCAL time and
    converts them to UTC. On an Israel-time machine a K-window ending
    2024-01-01 00:00:00 is really 2023-12-31 22:00:00 -- which is the clamp
    stamped on every EndTime in the reference outputs. We reproduce it so this
    pipeline's abstractions match a .NET run of the same window.

    NOTE this makes the window machine-dependent: run in another timezone and
    the offset, and therefore the output, changes. That is inherited .NET
    behaviour, kept deliberately for comparability."""
    lo = datetime(k_start, 1, 1).astimezone(timezone.utc).replace(tzinfo=None)
    hi = datetime(k_end, 1, 1).astimezone(timezone.utc).replace(tzinfo=None)
    return lo, hi


def run_mediator(S, leg, k_start, k_end):
    """Abstract every computable concept over the K-window for this cohort."""
    tak = tak_json(S)
    names = computable_names(tak)
    ws, we = mediator_window(k_start, k_end)
    d = S.cohort_dir(leg)
    log(f"  MEDIATOR: {len(names)} concepts, window "
        f"[{ws:%Y-%m-%d %H:%M:%S} .. {we:%Y-%m-%d %H:%M:%S}]...")

    t0 = time.time()
    eng = Engine(tak)
    eng.load_raw_csv(os.path.join(d, "mediator_raw_events.csv"))
    t_load = time.time() - t0

    t0 = time.time()
    rows = eng.run(names, window=(ws, we))
    t_run = time.time() - t0

    out = os.path.join(d, "abstractions.csv")
    eng.write_csv(rows, out)
    if eng._concept_failures:
        # The .NET Controller catches per concept-per-patient and moves on; so do
        # we. Surface the count so a thin run is never silent.
        log(f"  NOTE: {len(eng._concept_failures)} concept/patient computations "
            "raised and were skipped (matches the .NET behaviour).")
    log(f"  MEDIATOR done in {t_load:.0f}s load + {t_run:.0f}s compute "
        f"-> {len(rows)} abstraction rows")
    return out


def run_karmalego(S, leg, patients, results_path):
    """Mine TIRPs for this cohort. Output goes to the same nested location the
    .NET KarmaLegoConsoleApp uses, so results are drop-in comparable."""
    parameter_path = os.path.join(results_path, S.DOMAIN, "genreic")
    os.makedirs(parameter_path, exist_ok=True)
    out = os.path.join(parameter_path, "results.csv")
    concepts = S.kl_concepts()
    log(f"  KARMALEGO: MVS={S.MVS}, maxLevel={S.MAX_LEGO_LEVEL}, "
        f"concepts={'all' if concepts == '*' else len(concepts)}...")

    # MVS is a FRACTION of the cohort, so on a tiny cohort the support threshold
    # collapses to one or two patients, almost every symbol survives, and Lego's
    # search becomes combinatorial -- a 2-patient cohort at maxLevel 7 will eat
    # tens of GB of RAM before it finishes. Real cohorts (hundreds of patients)
    # are fine; this only bites on samples and near-empty windows.
    threshold = S.MVS * max(1, len(patients))
    if threshold < 5:
        log(f"  WARNING: {len(patients)} patients x MVS {S.MVS} = a support threshold of "
            f"{threshold:.1f} patient(s).")
        log(f"           Nearly every symbol will be 'frequent' and mining at "
            f"maxLevel {S.MAX_LEGO_LEVEL} may exhaust memory.")
        # Only worth suggesting a level that is actually lower than the current
        # one -- telling someone already at 3 to "use --max-level 3" reads as a
        # bug and costs them time working out whether they misread the flag.
        # Measured on a 2-patient cohort: L2 48 MB, L3 223 MB, L4 2.5 GB.
        if S.MAX_LEGO_LEVEL > 3:
            log(f"           For a smoke test on a small cohort use --max-level 3 "
                f"(each extra level has cost ~10x the memory here).")
        else:
            log(f"           Proceeding at maxLevel {S.MAX_LEGO_LEVEL}; that is "
                f"already low enough for a smoke test.")

    t0 = time.time()
    results, entity_ids = karmalego.run_karmalego(
        S.cohort_dir(leg), patients, out,
        mvs=S.MVS, max_level=S.MAX_LEGO_LEVEL, concepts=concepts,
        progress=(lambda m: log("    " + m)) if S.SHOW_PROGRESS else None)
    log(f"  KARMALEGO done in {time.time() - t0:.0f}s: {len(results)} patterns "
        f"x {len(entity_ids)} patients")
    log(f"  -> {out}")
    return out


def run_leg(S, leg, patients, k_start, k_end):
    results_path = S.results_path(k_start, k_end, leg)
    abstractions = run_mediator(S, leg, k_start, k_end)
    run_karmalego(S, leg, patients, results_path)
    if S.KEEP_ABSTRACTIONS:
        # workspace/run_data/<leg>/ is reused by every window, so without this
        # only the LAST window's abstractions survive a full run.
        os.makedirs(results_path, exist_ok=True)
        shutil.copyfile(abstractions, os.path.join(results_path, "abstractions.csv"))
        log(f"  kept abstractions -> {os.path.join(results_path, 'abstractions.csv')}")
    return results_path


# =============================================================================
# WINDOWS
# =============================================================================


def generate_windows(S):
    """(k_start, k_end, y_start, y_end) tuples — same rule as pipeline_stroke.py."""
    end_year = datetime.strptime(S.END_DATE, "%Y-%m-%d").year
    windows = []
    k_start = S.START_YEAR
    while True:
        k_end = k_start + S.K
        y_start = k_end
        y_end = y_start + S.Y
        if y_end > end_year:
            break
        windows.append((k_start, k_end, y_start, y_end))
        k_start += S.STEP
    return windows


def process_window(S, k_start, k_end, y_start, y_end, idx, total):
    log("")
    log(f"########## WINDOW {idx}/{total}: K=[{k_start}-{k_end}]  Y=[{y_start}-{y_end}] ##########")

    yes = get_positive_patients(S, y_start, y_end)
    log(f"YES ({S.label} in Y): {len(yes)} patients")
    if not yes:
        log("No YES patients — skipping window.")
        return

    no_count = len(yes) * S.NEGATIVE_RATIO
    no = get_negative_patients(S, no_count)
    log(f"NO  (never {S.label}): {len(no)} patients "
        f"(target {no_count} = {S.NEGATIVE_RATIO}x YES)")

    legs = [("YES", yes)] + ([("NO", no)] if no else [])
    prepare_window_data(S, legs)
    for leg, patients in legs:
        log(f"--- {leg} cohort ---")
        run_leg(S, leg, patients, k_start, k_end)
    if not no:
        log("No NO patients — NO leg skipped.")
    log(f"Window {k_start}-{k_end} complete.")


# =============================================================================
# MAIN
# =============================================================================


def main():
    cfg = load_config()
    p = argparse.ArgumentParser(
        description="Self-contained pure-Python TIRP pipeline (no DB, no .NET).")
    p.add_argument("--label", choices=sorted(cfg.get("labels", {})),
                   help="Outcome to predict. Default: config.json `label` "
                        f"(currently {cfg.get('label', 'stroke')!r}).")
    p.add_argument("--window", type=int,
                   help="Run only the window whose k_start = this year.")
    p.add_argument("--list-windows", action="store_true",
                   help="Print the window plan and exit.")
    p.add_argument("--data-dir", help="Override config.json data.data_dir.")
    p.add_argument("--results-dir", help="Override the results folder.")
    p.add_argument("--keep-abstractions", action="store_true",
                   help="Archive each window's abstractions.csv into its results folder. "
                        "Without this, workspace/ only holds the LAST window's, because "
                        "every window reuses the same scratch folder.")
    p.add_argument("--max-level", type=int,
                   help="Override karmalego.MAX_LEGO_LEVEL (max components per TIRP). "
                        "Lower it (e.g. 3) when smoke-testing on a small cohort, where "
                        "the fractional MVS threshold makes the search explode.")
    args = p.parse_args()

    S = Settings(cfg, label=args.label, data_dir=args.data_dir,
                 results_dir=args.results_dir)
    if args.max_level:
        S.MAX_LEGO_LEVEL = args.max_level
    S.KEEP_ABSTRACTIONS = args.keep_abstractions
    windows = generate_windows(S)

    if args.list_windows:
        print(f"label: {S.label}  (event concept: {S.EVENT_CONCEPT})")
        print(f"{len(windows)} window(s)  (K={S.K}, Y={S.Y}, STEP={S.STEP}, "
              f"START={S.START_YEAR}, END={S.END_DATE}):")
        for ks, ke, ys, ye in windows:
            print(f"  K=[{ks}-{ke}]  Y=[{ys}-{ye}]  -> "
                  f"{ks}-{ke}_{S.RESULTS_PREFIX}YES_patterns / "
                  f"{S.RESULTS_PREFIX}NO_patterns")
        return

    log("=" * 64)
    log("PURE-PYTHON TIRP PIPELINE  (Mediator + KarmaLego, stdlib only)")
    log(f"bundle : {BUNDLE}")
    log(f"data   : {S.DATA_DIR}")
    log(f"results: {S.RESULTS_DIR}")
    log(f"label  : {S.label}  ->  event concept '{S.EVENT_CONCEPT}', "
        f"results prefix '{S.RESULTS_PREFIX or '(none)'}'")
    log(f"window : K={S.K}y  Y={S.Y}y  STEP={S.STEP}y  "
        f"START={S.START_YEAR}  END={S.END_DATE}")
    log(f"cohort : NO = {S.NEGATIVE_RATIO}x YES, seed {S.SEED}")
    log(f"mining : MVS={S.MVS}, maxLevel={S.MAX_LEGO_LEVEL}, "
        f"{'all' if S.kl_concepts() == '*' else len(S.kl_concepts())} concepts")
    log("=" * 64)

    preflight(S)
    os.makedirs(RUN_DATA_DIR, exist_ok=True)
    os.makedirs(S.RESULTS_DIR, exist_ok=True)

    if args.window is not None:
        sel = [w for w in windows if w[0] == args.window]
        if not sel:
            die(f"no window with k_start={args.window}. "
                "Use --list-windows to see the options.")
        process_window(S, *sel[0], idx=1, total=1)
    else:
        log(f"Running {len(windows)} window(s), YES + NO each.")
        for i, (ks, ke, ys, ye) in enumerate(windows, 1):
            process_window(S, ks, ke, ys, ye, i, len(windows))

    log("")
    log("=" * 64)
    log(f"ALL DONE. Results in: {S.RESULTS_DIR}")
    log("=" * 64)


if __name__ == "__main__":
    main()
