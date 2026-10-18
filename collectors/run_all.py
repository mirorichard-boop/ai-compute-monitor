#!/usr/bin/env python3
"""Run every collector, fail soft, and record per-source status in data/status.json.

A failing source keeps its last good data (CSVs are only rewritten on success) and its
status records the error, so the page can mark it stale. Exit code is 0 unless every
source failed, so one broken feed never blocks the daily commit.

    python3 collectors/run_all.py              # all sources
    python3 collectors/run_all.py ornn vast    # just these
"""
import datetime as dt
import json
import sys
import traceback

sys.path.insert(0, __import__("os").path.dirname(__file__))

import artificial_analysis
import edgar
import eia
import epoch
import openrouter
import ornn
import vast
from common import DATA, SkipSource

SOURCES = {
    "openrouter": openrouter, "ornn": ornn, "vast": vast, "epoch": epoch,
    "edgar": edgar, "eia": eia, "artificial_analysis": artificial_analysis,
}


def main(names):
    path = DATA / "status.json"
    status = json.loads(path.read_text()) if path.exists() else {}
    ok = 0
    for name in names:
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        s = status.setdefault(name, {})
        s["last_attempt"] = now
        try:
            note = SOURCES[name].run()
            s.update(state="ok", last_success=now, note=note, error=None)
            ok += 1
            print(f"[ok]   {name}: {note}")
        except SkipSource as e:
            s.update(state="skipped", error=str(e))
            print(f"[skip] {name}: {e}")
        except Exception as e:
            s.update(state="error", error=f"{type(e).__name__}: {e}"[:400])
            print(f"[fail] {name}: {e}")
            traceback.print_exc(limit=2)
    path.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")
    return 0 if ok or not names else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or list(SOURCES)))
