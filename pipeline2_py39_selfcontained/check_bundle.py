"""Verify that py_pipeline/ is a self-contained, stdlib-only, offline bundle.

The deliverable is a folder you can paste into an air-gapped research room as
text: no pip install, no .NET, no compiled artifacts, no network. This script
proves that mechanically instead of by inspection.

Checks
  1. Every module in pyengine/ parses under the declared minimum Python.
  2. Every top-level import resolves to either the standard library or another
     module inside pyengine/ -- no third-party package anywhere.
  3. No module reaches outside the process: no subprocess/socket/urllib/ctypes
     and friends, at import time or anywhere in the source.
  4. Every file is plain text (pasteable) -- no .pyd/.dll/.exe/.so, no bytes
     that would not survive a copy-paste through a terminal, and every .py is
     pure ASCII so printing cannot fail on a legacy console codepage.
  5. The engines actually import and run from a clean interpreter, given only
     a raw-events CSV and the bundled tak_<kb>.json.

Usage:
    python check_bundle.py            # checks 1-4 (static, instant)
    python check_bundle.py --run      # also check 5 (needs the fixture CSV)
"""

import argparse
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PYENGINE = os.path.join(HERE, "pyengine")

# Modules that would take the bundle off-box or out of pure Python. ctypes and
# importlib.util can load binaries; the rest are I/O to the outside world.
FORBIDDEN = {
    "subprocess", "socket", "ssl", "urllib", "urllib2", "urllib3", "http",
    "httplib", "ftplib", "telnetlib", "smtplib", "requests", "ctypes",
    "multiprocessing", "asyncio", "webbrowser", "xmlrpc",
}
# Text-only file extensions allowed in the bundle.
TEXT_EXT = {".py", ".json", ".md", ".txt", ".csv", ".xml"}

# Scripts that are not the pipeline: fidelity gates and profilers, which compare
# against golden files outside the bundle and shell out to the comparator, and
# the transfer tools, which exist to get the bundle into the room in the first
# place. All are allowed subprocess. Nothing the pipeline runs imports any of
# them, and deleting the lot leaves a working bundle.
#
# receive.py is the one to understand: it reads the Windows clipboard by calling
# `powershell Get-Clipboard`, because there is no stdlib clipboard API and the
# bundle may not import ctypes. It runs BEFORE the pipeline exists, so holding
# it to the pipeline's offline guarantee would be checking the wrong thing.
DEV_ONLY = {"run_gate.py", "run_pilot.py", "run_full.py", "profile_engine.py",
            "check_bundle.py", "make_bundle.py", "receive.py"}

# The research room runs Python 3.9.7 and cannot be upgraded, so 3.9 is the
# floor. Nothing in the bundle needs anything newer -- the only 3.10-ism was the
# stdlib check below, which now carries its own name list.
MIN_PY = (3, 9)

# sys.stdlib_module_names exists from 3.10 on. On 3.9 we fall back to this
# frozen copy of it (3.10's list, plus formatter/parser/symbol, which 3.9 still
# ships and 3.10 dropped -- so it is a true superset for 3.9). Frozen rather
# than probed with importlib because this script's whole job is to assert that
# the bundle imports nothing; it should not import anything to find out.
_STDLIB_NAMES = frozenset((
    "__future__ _abc _aix_support _ast _asyncio _bisect _blake2 "
    "_bootsubprocess _bz2 _codecs _codecs_cn _codecs_hk _codecs_iso2022 "
    "_codecs_jp _codecs_kr _codecs_tw _collections _collections_abc "
    "_compat_pickle _compression _contextvars _crypt _csv _ctypes _curses "
    "_curses_panel _datetime _dbm _decimal _elementtree _frozen_importlib "
    "_frozen_importlib_external _functools _gdbm _hashlib _heapq _imp _io "
    "_json _locale _lsprof _lzma _markupbase _md5 _msi _multibytecodec "
    "_multiprocessing _opcode _operator _osx_support _overlapped _pickle "
    "_posixshmem _posixsubprocess _py_abc _pydecimal _pyio _queue _random "
    "_scproxy _sha1 _sha256 _sha3 _sha512 _signal _sitebuiltins _socket "
    "_sqlite3 _sre _ssl _stat _statistics _string _strptime _struct _symtable "
    "_thread _threading_local _tkinter _tracemalloc _uuid _warnings _weakref "
    "_weakrefset _winapi _zoneinfo abc aifc antigravity argparse array ast "
    "asynchat asyncio asyncore atexit audioop base64 bdb binascii binhex "
    "bisect builtins bz2 cProfile calendar cgi cgitb chunk cmath cmd code "
    "codecs codeop collections colorsys compileall concurrent configparser "
    "contextlib contextvars copy copyreg crypt csv ctypes curses dataclasses "
    "datetime dbm decimal difflib dis distutils doctest email encodings "
    "ensurepip enum errno faulthandler fcntl filecmp fileinput fnmatch "
    "formatter fractions ftplib functools gc genericpath getopt getpass "
    "gettext glob graphlib grp gzip hashlib heapq hmac html http idlelib "
    "imaplib imghdr imp importlib inspect io ipaddress itertools json keyword "
    "lib2to3 linecache locale logging lzma mailbox mailcap marshal math "
    "mimetypes mmap modulefinder msilib msvcrt multiprocessing netrc nis "
    "nntplib nt ntpath nturl2path numbers opcode operator optparse os "
    "ossaudiodev parser pathlib pdb pickle pickletools pipes pkgutil platform "
    "plistlib poplib posix posixpath pprint profile pstats pty pwd py_compile "
    "pyclbr pydoc pydoc_data pyexpat queue quopri random re readline reprlib "
    "resource rlcompleter runpy sched secrets select selectors shelve shlex "
    "shutil signal site smtpd smtplib sndhdr socket socketserver spwd sqlite3 "
    "sre_compile sre_constants sre_parse ssl stat statistics string "
    "stringprep struct subprocess sunau symbol symtable sys sysconfig syslog "
    "tabnanny tarfile telnetlib tempfile termios textwrap this threading time "
    "timeit tkinter token tokenize trace traceback tracemalloc tty turtle "
    "turtledemo types typing unicodedata unittest urllib uu uuid venv "
    "warnings wave weakref webbrowser winreg winsound wsgiref xdrlib xml "
    "xmlrpc zipapp zipfile zipimport zlib zoneinfo "
).split())


def _py_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in sorted(filenames):
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def _local_module_names():
    """Module names importable from inside the bundle (pyengine/ and
    pyengine/mediator/ both go on sys.path, so both levels count)."""
    names = {os.path.splitext(f)[0] for f in os.listdir(HERE) if f.endswith(".py")}
    for dirpath, dirnames, filenames in os.walk(PYENGINE):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if fn.endswith(".py"):
                names.add(os.path.splitext(fn)[0])
        names.add(os.path.basename(dirpath))
    return names


def _imports(tree):
    """(module_name, lineno) for every top-level package an import touches."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.append((a.name.split(".")[0], node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:                       # relative import -> local
                continue
            if node.module:
                out.append((node.module.split(".")[0], node.lineno))
    return out


def check_static():
    fails = []
    if not os.path.isdir(PYENGINE):
        return [f"pyengine/ not found at {PYENGINE}"]

    if sys.version_info < MIN_PY:
        fails.append(f"need Python >= {MIN_PY[0]}.{MIN_PY[1]}, this is "
                     f"{sys.version_info[0]}.{sys.version_info[1]}")
        return fails
    # Authoritative on 3.10+, frozen copy on 3.9 -- see _STDLIB_NAMES.
    stdlib = getattr(sys, "stdlib_module_names", None) or _STDLIB_NAMES
    local = _local_module_names()

    # Everything shipped: the top-level scripts (run_pipeline.py and the gates)
    # plus the engines. A third-party import in the driver breaks the bundle
    # just as badly as one in the engine.
    top = [os.path.join(HERE, f) for f in sorted(os.listdir(HERE))
           if f.endswith(".py")]
    engine_files = list(_py_files(PYENGINE))
    files = top + engine_files
    runtime = [f for f in top if os.path.basename(f) not in DEV_ONLY]
    print(f"modules: {len(runtime)} runtime + {len(top) - len(runtime)} dev-only "
          f"(top level) + {len(engine_files)} in pyengine/")

    all_imports = set()
    for path in files:
        rel = os.path.relpath(path, HERE)
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        # ASCII-only. A room console on codepage 437 or 862 cannot encode an em
        # dash, so a single one inside a printed string aborts the run with
        # UnicodeEncodeError. Enforcing it over the whole file rather than over
        # printed strings alone keeps the rule mechanical -- and the same rule
        # already guards the .ps1 files in the transfer route.
        for i, line in enumerate(src.splitlines(), 1):
            if not line.isascii():
                bad = next(c for c in line if ord(c) > 127)
                fails.append(f"{rel}:{i}: non-ASCII character {bad!r} "
                             f"(U+{ord(bad):04X}) -- the room console may not "
                             "be able to encode it")
                break
        try:
            tree = ast.parse(src, filename=path)
        except SyntaxError as e:
            fails.append(f"{rel}: syntax error: {e}")
            continue
        dev_only = os.path.basename(path) in DEV_ONLY
        for mod, lineno in _imports(tree):
            all_imports.add(mod)
            if mod in FORBIDDEN and not dev_only:
                fails.append(f"{rel}:{lineno}: forbidden import {mod!r} "
                             "(bundle must not reach outside the process)")
            elif mod not in stdlib and mod not in local:
                fails.append(f"{rel}:{lineno}: third-party import {mod!r} "
                             "(bundle must be stdlib-only)")

    third = sorted(m for m in all_imports if m not in local)
    print("imports (all must be stdlib):", ", ".join(third) or "(none)")

    # Text-only, pasteable
    for dirpath, dirnames, filenames in os.walk(PYENGINE):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, HERE)
            if ext not in TEXT_EXT:
                fails.append(f"{rel}: non-text file in the bundle "
                             "(bundle must be pasteable as text)")
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    fh.read()
            except UnicodeDecodeError as e:
                fails.append(f"{rel}: not valid UTF-8 text ({e})")

    # --- everything a run needs must be inside the folder ------------------
    taks = sorted(f for f in os.listdir(HERE)
                  if f.startswith("tak_") and f.endswith(".json"))
    kb_dirs = []
    tak_root = os.path.join(HERE, "tak_entities")
    if os.path.isdir(tak_root):
        kb_dirs = sorted(d for d in os.listdir(tak_root)
                         if os.path.isdir(os.path.join(tak_root, d)))
    if taks:
        print("bundled TAK (pre-parsed):", ", ".join(taks))
    if kb_dirs:
        for kb in kb_dirs:
            n = len([f for f in os.listdir(os.path.join(tak_root, kb))
                     if f.lower().endswith(".xml")])
            print(f"bundled TAK (source)   : tak_entities/{kb}  ({n} concept XMLs)")
    if not taks and not kb_dirs:
        fails.append("no knowledge base bundled -- need tak_entities/<kb>/*.xml "
                     "or a pre-parsed tak_<kb>.json")

    if not os.path.exists(os.path.join(HERE, "config.json")):
        fails.append("config.json missing -- run_pipeline.py has nothing to read")
    if not os.path.exists(os.path.join(HERE, "run_pipeline.py")):
        fails.append("run_pipeline.py missing -- the folder cannot run a pipeline")

    data = os.path.join(HERE, "data")
    for f in ("knowledge_table.csv", "projects.csv"):
        if not os.path.exists(os.path.join(data, f)):
            fails.append(f"data/{f} missing -- the project dictionary must ship "
                         "with the bundle")
    if os.path.exists(os.path.join(data, "raw_events.csv")):
        print("bundled sample data    : data/raw_events.csv")
    else:
        print("NOTE: no data/raw_events.csv -- supply your export before running "
              "run_pipeline.py")
    return fails


def check_run():
    """Import both engines and abstract one patient end to end."""
    fails = []
    sys.path.insert(0, os.path.join(PYENGINE, "mediator"))
    sys.path.insert(0, PYENGINE)
    try:
        import karmalego                                   # noqa: F401
        from engine import Engine
    except Exception as e:                                  # noqa: BLE001
        return [f"import failed: {e!r}"]
    print("imported: pyengine.karmalego, pyengine.mediator.engine")

    # Prefer a CSV inside the bundle -- that is what proves self-containment.
    # The dev-tree fallbacks only exist so this passes without copying data around.
    candidates = [
        os.path.join(HERE, "data", "mediator_raw_events.csv"),
        os.path.join(HERE, "data", "raw_events.csv"),
        os.path.join(HERE, "sample_raw_events.csv"),
        os.path.join(os.path.dirname(HERE), "csv_pipeline", "data",
                     "mediator_raw_events.csv"),
        r"C:\Users\noama1\Desktop\karma\csv_mode\fixtures\mediator_raw_events.csv",
    ]
    raw = next((p for p in candidates if os.path.exists(p)), None)
    if raw is None:
        print("SKIP run check: no raw-events CSV "
              "(put one at py_pipeline/sample_raw_events.csv)")
        return fails
    inside = os.path.abspath(raw).startswith(HERE + os.sep)
    print(f"input  : {raw}" + ("" if inside else "   (outside the bundle)"))

    tak = os.path.join(HERE, "tak_2700.json")
    eng = Engine(tak)
    eng.load_raw_csv(raw)
    import json
    with open(tak, encoding="utf-8") as fh:
        concepts = json.load(fh)["concepts"]
    names = [c["name"] for c in concepts.values()
             if c["op_type"] in ("state", "trend", "pattern", "context")]
    rows = eng.run(names)
    print(f"ran {len(names)} concepts over {len(eng.patients())} patients "
          f"-> {len(rows)} abstraction rows")
    if not rows:
        fails.append("engine produced no rows")
    return fails


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", action="store_true",
                    help="also import and execute the engines")
    args = ap.parse_args()

    print(f"bundle: {HERE}")
    print(f"python: {sys.version.split()[0]} ({sys.executable})")
    print()
    fails = check_static()
    if args.run:
        print()
        fails += check_run()

    print()
    if fails:
        print(f"FAIL ({len(fails)} problem(s)):")
        for f in fails:
            print("  " + f)
        return 1
    print("OK: stdlib-only, ASCII-only, text-only, offline, self-contained.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
