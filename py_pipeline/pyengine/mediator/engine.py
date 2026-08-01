"""Mediator engine orchestration (non-pattern operators).

Faithful port of the ByTime abstraction flow in
ComputationalServices\\Controller.cs (CalculateAbstractionsInBatchByTime ->
insertBatchCalculatedDataByTime -> GetConceptDataByTime):

  * orderConcepts                 :3048-4099  (stable iterative topological sort)
  * GetConceptDataByTime          :2657-...   (per concept; NOT memoized in C#,
                                               but memoisation is result-neutral
                                               here and we use it for speed)
  * derived-from gathering + Smoosh   :2510-2568
  * GetPartitions / GetPartitionData  :3791-3929
  * AbstractionAtContext selection    :AbstractConcept.cs GetAbstractionAtContext
  * output rows: PatientID,ConceptName,StartTime,EndTime,Value  yyyy-MM-dd HH:mm:ss

For the 43 pattern-free gate concepts we run over full history (no K/Y window):
raw filtering is a no-op, contexts are computed full-history anyway, and the
trailing smoosh is capped at the configured absoluteSmooshLimitDate (11/05/2030).
"""

from __future__ import annotations

import csv
import json
from datetime import datetime

import context as context_op
import event as event_op
import pattern as pattern_op
import state as state_op
import trend as trend_op
from datainstance import DataInstance, duration_to_timedelta, fmt_ts
from persistence import smoosh

# full-history window (raw filter no-op); kept below datetime.max to avoid
# overflow when Smoosh adds good-after (up to 120 years) to a point.
_FULL_START = datetime(1, 1, 1)
_FULL_END = datetime(9000, 1, 1)
_FULL = (_FULL_START, _FULL_END)
# absoluteSmooshLimitDate "11/05/2030" (CommonConfiguration/appsettings.json).
_SMOOSH_LIMIT = datetime(2030, 11, 5)
# The fixture was generated with the K-window [2022-01-01, 2024-01-01] handed to
# CalculateAbstractionsInBatchByTime as a string.  Controller.ParseDateOrTime
# (:1569) parses BOTH endpoints with `AssumeLocal | AdjustToUniversal` -- it
# reads them as local time and converts to UTC -- so on the Israel-time machine
# that produced the fixture the engine actually ran over
# [2021-12-31 22:00:00, 2023-12-31 22:00:00].  That is where the 22:00 stamped
# on every clamped EndTime in abstractions.csv comes from; the start is shifted
# too, it just makes no difference on day-granular raw data (A/B-verified: both
# starts give the identical fixture match).  csv_pipeline\run.py does the same
# conversion so --engine python and --engine dotnet share a window.
_WIN_START = datetime(2021, 12, 31, 22, 0, 0)
_WIN_END = datetime(2023, 12, 31, 22, 0, 0)

_TS_FORMATS = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
               "%Y-%m-%d %H:%M", "%Y-%m-%d")


def _parse_ts(s):
    s = (s or "").strip()
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"unparseable timestamp {s!r}")


class Engine:
    def __init__(self, tak_json_path, smoosh_limit=_SMOOSH_LIMIT):
        with open(tak_json_path, encoding="utf-8") as fh:
            payload = json.load(fh)
        self.knowledge = payload["concepts"]                 # id(str) -> concept
        self.name2id = {c["name"]: cid for cid, c in self.knowledge.items()}
        # raw data concept names are matched case-insensitively (the CSV carries
        # e.g. "sex" while the TAK entity is "Sex").
        self._name2id_lower = {c["name"].lower(): cid
                               for cid, c in self.knowledge.items()}
        self.smoosh_limit = smoosh_limit
        # the batch's per-patient time window, as handed to
        # CalculateAbstractionsInBatchByTime; `run()` overwrites it.
        self.pipeline_window = (_WIN_START, _WIN_END)
        self.raw = {}                # (pid, cid) -> pristine sorted DataInstance list
        self._memo = {}              # (pid, cid) -> computed DataInstance list
        self._concept_failures = []  # (pid, cid, err) skipped per Controller catch

    # ------------------------------------------------------------------ raw
    def load_raw_csv(self, path):
        buckets = {}
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for d in csv.DictReader(fh):
                name = d["ConceptName"].strip()
                cid = self.name2id.get(name) or self._name2id_lower.get(name.lower())
                if cid is None:
                    continue                                # unknown concept -> skip
                pid = str(d["PatientID"]).strip()
                di = DataInstance(pid, name,
                                  _parse_ts(d["StartTime"]), _parse_ts(d["EndTime"]),
                                  str(d["Value"]).strip())
                buckets.setdefault((pid, cid), []).append(di)
        for key, lst in buckets.items():
            lst.sort(key=lambda x: x.start)               # DataCSVDA OrderBy StartTime
        self.raw = buckets

    def patients(self):
        return sorted({pid for (pid, _cid) in self.raw})

    # ---------------------------------------------------------- ordering
    def order_concepts(self, concept_ids):
        """Stable iterative topological sort (Controller.cs:4058-4098). Only
        intra-selection derived-from edges constrain order; input order breaks
        ties."""
        selected = set(concept_ids)
        placed = set()
        ans = []
        remaining = list(concept_ids)
        while len(ans) < len(concept_ids):
            progressed = False
            for cid in remaining:
                if cid in placed:
                    continue
                deps = self.knowledge[cid].get("derived_from") or []
                if all((d not in selected) or (d in placed) for d in deps):
                    ans.append(cid)
                    placed.add(cid)
                    progressed = True
            if not progressed:
                # cycle / unresolved — append the rest in input order (defensive)
                for cid in remaining:
                    if cid not in placed:
                        ans.append(cid)
                        placed.add(cid)
                break
        return ans

    # ------------------------------------------------------ concept data
    def get_concept_data(self, pid, cid, window):
        key = (pid, cid, window)
        if key in self._memo:
            return self._memo[key]
        concept = self.knowledge.get(cid)
        if concept is None:
            self._memo[key] = []
            return []
        op = concept["op_type"]
        if op in ("raw-numeric", "raw-nominal"):
            result = self._raw_in_window(pid, cid, window)
        elif op in ("state", "trend"):
            result = self._compute_abstract(pid, concept, window)
        elif op == "context":
            result = self._compute_context(pid, concept, window)
            # GetConceptDataByTime's Context branch (:2732) keeps only
            # intervals that START inside the window; the trailing clamp below
            # then cuts the last one back to We.
            result = [di for di in result if di.start <= window[1]]
        elif op == "event":
            # GetConceptDataByTime's Event branch (:2784) fetches through
            # DataProvider.GetGroupData(ev, startTime, endTime), which applies
            # the SAME `StartTime >= start && EndTime <= end` predicate as the
            # raw path (DataCSVDA.cs:88-97) — events are window-filtered too.
            result = self._raw_in_window(pid, cid, window)
        elif op == "pattern":
            result = self._compute_pattern(pid, concept, window)
        else:
            result = []
        self._cut_from_future(result, window[1])
        self._memo[key] = result
        return result

    @staticmethod
    def _cut_from_future(result, window_end):
        """"cut from the future" (Controller.cs:2764 / :3068): the LAST element
        of every GetConceptDataByTime result has its EndTime clamped to the
        window end.  Applied to intermediates too, so downstream patterns see
        exactly what the .NET pipeline hands them."""
        if result and result[-1].end > window_end:
            result[-1].end = window_end

    def _raw_in_window(self, pid, cid, window):
        ws, we = window
        full = self.raw.get((pid, cid)) or []
        if (ws, we) == _FULL:
            return self._raw_poisoned_by_cache(full)
        return [di for di in full if di.start >= ws and di.end <= we]

    def _raw_poisoned_by_cache(self, full):
        """A full-history raw fetch, as the .NET data layer actually serves it.

        `ComplementaryDataServicesBL.addToCache` (:191-199) stores every fetch
        under BOTH `"{proj} {pat} {concept} {start} {end}"` AND a **time-less**
        `"{proj} {pat} {concept}"` key.  `GetDataByTime` (the ByTime pipeline)
        looks up the exact key only, but `GetData` -- the NON-ByTime function
        that `GetContextsData` (:3775) and the non-ByTime Pattern branch use --
        falls back to the time-less key and merely re-filters it (:156-177).

        Every raw is computed as a top-level concept first, ByTime, over the
        patient window, so by the time any context induction asks for a raw
        "over all time" the time-less key already holds the WINDOWED list.  The
        one escape hatch: `GetDataByTime` returns early **before caching** when
        the windowed fetch is empty (:255-259), so a raw with no in-window data
        (Birth_Year, dated 1928-1939; `sex`) is never poisoned and really is
        served full-history.

        That asymmetry is exactly what the reference shows: `Live_ctxt` keeps
        its 1928 start and `Ctxt_Female` its birth-minus-120y start, while
        `Ctxt_Age_65Plus` begins at the first IN-WINDOW Visit rather than where
        `State_Age_65Plus` truly starts.
        """
        ws, we = self.pipeline_window
        windowed = [di for di in full if di.start >= ws and di.end <= we]
        return windowed if windowed else list(full)

    # --------------------------------------------------- derived + smoosh
    def _derived_data(self, pid, data_ids, window):
        data = {}
        for did in data_ids:
            lst = self.get_concept_data(pid, did, window)
            dconcept = self.knowledge.get(did)
            if dconcept and dconcept["op_type"] in ("raw-numeric", "raw-nominal"):
                lst = [di.copy() for di in lst]             # deep copy (:2546)
                self._remove_dups_fix_late(lst)             # :2548
                if lst:
                    local = (dconcept.get("spec") or {}).get("persistence", {})
                    local = (local or {}).get("local")
                    smoosh(lst, local, self.smoosh_limit)   # :2563
            data[did] = lst
        return data

    @staticmethod
    def _remove_dups_fix_late(data):
        """RemoveDuplicatesAndFixLateStartTime (Controller.cs:3597-3673).
        Operates in place on a start-sorted list."""
        for di in data:
            if di.start > di.end:
                di.end = di.start
        delete = []
        index = 0
        while index < len(data) - 1:
            di = data[index]
            suc = data[index + 1]
            if di.end > suc.start:
                while di.end >= suc.end:
                    del data[index + 1]
                    if len(data) - 1 == index:
                        break
                    suc = data[index + 1]
            if index < len(data) - 1:
                suc = data[index + 1]
            if di.end > suc.start and di.end < suc.end:
                di.end = suc.start
            if suc.start == di.end:
                if suc.start == suc.end:
                    delete.append(suc)
                if di.start == di.end:
                    delete.append(di)
            while di.start == suc.start and di.end == suc.end:
                if di.value != suc.value:
                    delete.append(di)
                    delete.append(suc)
                else:
                    delete.append(di)
                index += 1
                if index >= len(data) - 1:
                    break
                di = data[index]
                suc = data[index + 1]
            index += 1
        if delete:
            drop = set(id(x) for x in delete)
            data[:] = [d for d in data if id(d) not in drop]

    # ------------------------------------------------ abstract (state/trend)
    def _compute_abstract(self, pid, concept, window):
        op = concept["op_type"]
        aacs = concept.get("abstraction_at_contexts") or []
        # data ids = derived-from excluding contexts (contexts drive partitions)
        data_ids = [d for d in (concept.get("derived_from") or [])
                    if (self.knowledge.get(d) or {}).get("op_type") != "context"]
        derived_data = self._derived_data(pid, data_ids, window)

        # context intervals for partitioning (full history)
        ctx_ids = []
        for blk in aacs:
            for c in (blk.get("necessary_contexts") or []):
                if c not in ctx_ids:
                    ctx_ids.append(c)
        tagged = []
        for ctxid in ctx_ids:
            for di in self.get_concept_data(pid, ctxid, (_FULL_START, _FULL_END)):
                tagged.append((ctxid, di))

        partitions, keyset = self._get_partitions(tagged)
        result = []
        for (pstart, pend) in partitions:
            covering = {cid for (cid, di) in tagged
                        if di.start <= pstart and di.end >= pend}
            spec = self._select_spec(concept, aacs, covering)
            if spec is None:
                continue
            has_ps = (pstart, pstart) in keyset
            has_pe = (pend, pend) in keyset
            pdata = self._get_partition_data(derived_data, pstart, has_ps, pend, has_pe)
            if op == "state":
                result.extend(state_op.calculate(pid, concept["name"], spec,
                                                 pdata, self.knowledge,
                                                 self._concatenable(concept)))
            else:  # trend
                did = data_ids[0] if data_ids else None
                series = pdata.get(did, [])
                result.extend(trend_op.calculate(pid, concept["name"], spec,
                                                 did, series, self.knowledge,
                                                 self._concatenable(concept)))
        return result

    @staticmethod
    def _concatenable(concept):
        """`<temporal-semantic concatenable=...>`. Only the programmatic ctors
        of State/Trend/Context force this true; the XmlReader ctors read it
        from the XML, and it gates Interpolate (State.cs:299, Trend.cs:364) and
        RemoveConflicts (Context.cs:392)."""
        ts = concept.get("temporal_semantic") or {}
        return str(ts.get("concatenable", "true")).lower() == "true"

    @staticmethod
    def _select_spec(concept, aacs, covering):
        """GetAbstractionAtContext (Specific flag). Returns the spec dict to use,
        or the top-level spec as fallback (whose empty mapping yields nothing for
        context-switched concepts outside any context)."""
        if not aacs:
            return concept.get("spec")
        cands = [b for b in aacs
                 if set(b.get("necessary_contexts") or []) <= covering
                 and not (set(b.get("exclusion_contexts") or []) & covering)]
        if cands:
            cands.sort(key=lambda b: -len(b.get("necessary_contexts") or []))
            return cands[0].get("spec")
        return concept.get("spec")               # top-level (empty) -> no output

    # --------------------------------------------------- partitioning
    @staticmethod
    def _get_partitions(tagged):
        """GetPartitions (Controller.cs:3791-3842). `tagged` = [(ctxid, di)].
        Returns (list of (start,end) partitions, set of partition keys)."""
        times = set()
        point_times = []
        for _cid, di in tagged:
            times.add(di.start)
            times.add(di.end)
            if di.start == di.end:
                point_times.append(di.start)
        times.add(_FULL_START)
        times.add(_FULL_END)
        ordered = sorted(list(times) + point_times)
        partitions = []
        keyset = set()
        for i in range(len(ordered) - 1):
            key = (ordered[i], ordered[i + 1])
            partitions.append(key)
            keyset.add(key)
        return partitions, keyset

    def _get_partition_data(self, data, start, has_point_start, end, has_point_end):
        """GetPartitionData (Controller.cs:3854-3929)."""
        out = {}
        for did, lst in data.items():
            concept = self.knowledge.get(did)
            dh = ((concept or {}).get("temporal_semantic") or {}).get(
                "downward-hereditary") == "true"
            cur = []
            for inst in lst:
                ni = inst.copy()
                if inst.start < start or inst.end > end:
                    if dh:
                        left = inst.start < start and inst.end > start
                        right = inst.end > end and inst.start < end
                        if left:
                            ni.start = start
                        if right:
                            ni.end = end
                        elif not left:
                            continue
                    else:
                        continue
                if inst.start == inst.end and start < end:
                    if inst.start == start and has_point_start:
                        continue
                    if inst.end == end and has_point_end:
                        continue
                cur.append(ni)
            out[did] = cur
        return out

    # ------------------------------------------------------- pattern
    def _compute_pattern(self, pid, concept, window):
        """Controller.cs:2821-2893 (ByTime): fetch each component's concept data
        keyed by its 1-BASED INDEX, plus a knowledge map keyed by the
        component's GesherID, then call Pattern.Calculate.

        The component's own TimeConstraint narrows the fetch window
        (`newStart`/`newEnd`, :2857-2858); KB 2700's default constraint is
        MinValue..MaxValue so the pattern window normally passes through."""
        spec = concept.get("spec") or {}
        ws, we = window
        data = {}
        knowledge = {}
        for i, comp in enumerate(spec.get("components") or [], 1):
            cid = str(comp["id"])
            tc = comp.get("time_constraint")
            cws, cwe = ws, we
            if tc:
                tcs = pattern_op._point_datetime(tc.get("start"), _FULL_START)
                tce = pattern_op._point_datetime(tc.get("end"), _FULL_END)
                cws = ws if ws >= tcs else tcs
                cwe = we if we <= tce else tce
            lst = self.get_concept_data(pid, cid, (cws, cwe))
            # hand over copies: Pattern.Calculate filters its component lists in
            # place and renames the surviving instances.
            data[str(i)] = [di.copy() for di in lst]
            knowledge.setdefault(cid, self.knowledge.get(cid))
        return pattern_op.calculate(pid, concept["name"], spec, data, knowledge)

    # ------------------------------------------------------- context
    def _compute_context(self, pid, concept, window=_FULL):
        spec = concept.get("spec") or {}
        data = {}
        # Controller.cs:2683-2717 — INDUCERS are fetched over all time
        # (MinValue..MaxValue, :2691-2692) but CLIPPERS get the caller's window
        # (:2710-2711).  The asymmetry matters: StartNoac_AbsCI_ctxt's clipper
        # 483 only yields its 2-minute Visit-shaped intervals inside the window,
        # and without them the 1-year induced contexts are never clipped back
        # to `…00:01`.  Worth +156 rows on the fixture.
        for inducer in spec.get("inducers") or []:
            cid = inducer.get("id")
            data[cid] = self.get_concept_data(pid, cid, _FULL)
        for clip in spec.get("clippers") or []:
            cid = clip.get("id")
            data[cid] = self.get_concept_data(pid, cid, window)
        return context_op.calculate(pid, concept["name"], spec, data,
                                    self.knowledge, self._concatenable(concept))

    # ------------------------------------------------------- run / output
    def run(self, requested_names, window=None):
        """Compute the requested concepts (by name) for every patient. Returns a
        list of output row tuples (pid, name, start, end, value).

        The output stage does **no** windowing.  `insertBatchCalculatedDataByTime`
        (Controller.cs:1766-1770) does `data.AddRange(conceptData)` on whatever
        GetConceptDataByTime returned and stores it verbatim -- the code even
        comments that it deliberately does not touch data outside the window
        ("Deleting might remove data outside the window").  The only clamp is
        `_cut_from_future` on the LAST element of each concept's result, applied
        inside `get_concept_data` where the .NET applies it.

        The port used to additionally drop intervals lying wholly outside
        [Ws, We] and clamp EVERY row's end to We.  Neither has a .NET
        counterpart.  They were invisible on the fixture (whose 20 patients all
        have in-window data) but wrong in general: a patient whose raws stop
        before Ws still gets contexts induced over full history -- the raw cache
        escape hatch in `_raw_poisoned_by_cache` -- and the .NET writes those
        out.  Dropping them cost ~4,900 rows on two patients of one real
        data_full cohort, and the per-row clamp accounted for every remaining
        EndTime mismatch against a live .NET run.  Removing both took the
        fixture from 5,043 to 5,047 of 5,048 with fewer extras, not more."""
        if window is None:
            window = (_WIN_START, _WIN_END)
        self.pipeline_window = window
        req_ids = [self.name2id[n] for n in requested_names if n in self.name2id]
        ordered = self.order_concepts(req_ids)
        rows = []
        for pid in self.patients():
            self._memo.clear()                            # per-patient (ByTime)
            for cid in ordered:
                # Controller.cs:889-908 (calculateMissingData) wraps each
                # concept-per-patient in try/catch: an exception is logged and
                # the concept simply yields nothing for that patient.  This is
                # what keeps compliance patterns like 484 faithful — a patient
                # whose anchor component (NOAC_state) has no data throws
                # KeyNotFound in .NET too, so those patients are absent from the
                # reference rather than erroring the whole run.
                try:
                    computed = self.get_concept_data(pid, cid, window)
                except Exception as ex:                   # noqa: BLE001
                    self._concept_failures.append((pid, cid, repr(ex)))
                    continue
                for di in computed:
                    rows.append((di.entity_id, di.concept_name,
                                 fmt_ts(di.start), fmt_ts(di.end), di.value))
        return rows

    @staticmethod
    def write_csv(rows, path):
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["PatientID", "ConceptName", "StartTime", "EndTime", "Value"])
            w.writerows(rows)
