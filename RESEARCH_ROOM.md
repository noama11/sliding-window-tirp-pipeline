# Research-Room Deployment — Situation & Options

**Purpose:** lay out clearly what we have, what blocks us in the research room, and
the realistic options — so we can decide the direction before building anything.

---

## 1. The goal

Run the stroke temporal-pattern pipeline (Mediator → KarmaLego) **inside the
research room, on the room's own (larger) full dataset**, producing the
`{window}_YES_patterns` / `_NO_patterns` result matrices — with **no database**.

## 2. What we already have (works today)

A self-contained, **CSV-only** pipeline in `csv_pipeline/`
(github.com/noama11/sliding-window-tirp-pipeline, `master`):

- Reproduces `pipeline_stroke.py` exactly (K/Y sliding windows, YES/NO cohorts,
  3× controls, same output format).
- Runs the two **compiled engines** off CSV — no SQL Server, no build step.
- Validated end-to-end on real data; you only supply **one file** in the room:
  your `raw_events.csv`.
- A full 11-window run takes ~3–4 h.

**This is done and proven.** The only problem is *getting it onto the research
machine*.

## 3. The research-room constraints (the blocker)

1. **No internet** on the machine → cannot download/install anything (including
   the .NET runtime the engines need).
2. **No file transfer** — reportedly **copy-paste of text only**. No USB, no
   network share, no email attachment.

These two together are the whole problem.

## 4. Why those constraints are hard to satisfy

The pipeline is not pure text. It needs three things that are **not** copy-pasteable:

| Needs to be present | What it is | Size | Copy-paste-able? |
|---|---|---|---|
| **Mediator + KarmaLego** engines | Compiled `.exe` + `.dll` (binary) | ~55 MB | ❌ binary |
| **.NET 8 runtime** | Microsoft runtime that runs the `.exe`s | ~55 MB installer | ❌ binary, needs install |
| **TAK knowledge base** | 376 XML rule files (concept definitions) | 1.9 MB text | ⚠️ text, but 376 files |

- Pasting the binaries as **base64 text** would be ~70 MB of text — no
  clipboard/terminal handles that reliably. Not a real option.
- The data itself is **not** a problem — the room already has its own bigger
  `raw_events.csv`.

## 5. The pipeline's three parts — how portable each is to pure Python

If we go "pure Python so it can be pasted," here's the honest difficulty:

| Part | What it does | Port to Python |
|---|---|---|
| **Cohort selection** | pick YES (stroke in Y-window) / NO (3× controls) from `raw_events.csv` | **Trivial** — already essentially Python |
| **KarmaLego** | mine Time-Interval-Related-Patterns from the abstractions | **Feasible** — well-defined algorithm, ~1 file, validatable against our frozen reference |
| **Mediator** | turn raw events into symbolic abstraction intervals | **The blocker — a large engine** |

### Why Mediator is the blocker

It is a real **temporal-abstraction engine** driven by **376 interdependent rule
files**:

- 193 **states** (value → Low/Normal/High via threshold logic trees)
- 67 **patterns** (e.g. `CHADS_Vasc` is *derived from 8 other concepts*, which
  derive from others — a dependency graph)
- 25 **trends** (gradient over time, per-concept parameters)
- 25 **contexts** (temporal windows that gate other abstractions)
- 11 **events** + 92 raw concept definitions

Plus the engine machinery: persistence, interval concatenation ("smooshing"),
context induction, dependency ordering. Reimplementing this **faithfully** in
Python = reimplementing a research engine (weeks of work), with real risk it won't
match the original — and it's hard to validate offline.

> Note: the engine is also **non-deterministic** (multithreaded) — identical runs
> already give slightly different patterns. This is true of the original SQL
> pipeline too. So "exact reproduction" was never fully on the table regardless.

## 6. The options

### Option A — Get ~55 MB onto the machine once (RECOMMENDED)
Find **any** sanctioned one-time channel (IT-approved USB/CD, internal share, or an
IT request to install .NET 8 + drop the folder). Then the **exact, validated,
faithful** pipeline just runs.
- **Effort:** ~1% of a rewrite. Prep an offline install kit + instructions.
- **Fidelity:** perfect (it *is* the pipeline).
- **Risk:** depends entirely on whether such a channel exists.

### Option B — Approximate all-Python pipeline
Build a copy-pasteable Python pipeline: cohort selection + the **tractable**
abstractions (states via thresholds + trends) + a Python KarmaLego, but
**skip/approximate the complex patterns & contexts**.
- **Effort:** medium (days).
- **Fidelity:** **produces different patterns than the original** — a
  research-methodology change you would have to accept and justify.
- **Copy-paste:** yes (a few `.py` files + embedded rule parameters).

### Option C — Full faithful Python port
Reimplement the entire Mediator abstraction engine + KarmaLego in Python to match
the original.
- **Effort:** large (weeks), multi-file.
- **Fidelity:** aims for faithful, but real risk of divergence; hard to validate
  offline.
- **Copy-paste:** eventually yes, but it's a big body of code + the 1.9 MB of
  rules must travel too.

## 7. Recommendation & decision needed

**Try Option A first.** Getting ~55 MB in *once* — even through a slow/bureaucratic
IT channel — is dramatically less effort than rewriting a research engine, and it
keeps the output faithful and already-validated.

Only if a one-time transfer is **truly impossible** should we choose between:
- **B** (fast, offline, but non-faithful output — a research change), or
- **C** (faithful-ish, but a large project with fidelity risk).

### Questions to resolve to decide
1. Is there **really** no sanctioned way to move ~55 MB onto the machine once
   (IT-approved USB, CD, internal share, or an IT-performed .NET install)?
2. If Python-only is forced: is **non-faithful/approximate output acceptable** for
   the research (Option B), or must it match the original (Option C)?
3. How much text can the room's copy-paste channel realistically take at once
   (a few KB? a few MB?) — this bounds even the Python options (the 1.9 MB of rules
   / embedded parameters still has to get in somehow).
