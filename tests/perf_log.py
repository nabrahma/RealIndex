"""Record performance measurements into tests/_out/perf.md (a table rebuilt on every record)."""

import datetime
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "tests", "_out")
STORE = os.path.join(OUT, "perf.json")
DOC = os.path.join(OUT, "perf.md")


def record(name, value, unit, budget=None, note=""):
    os.makedirs(OUT, exist_ok=True)
    data = {}
    if os.path.exists(STORE):
        with open(STORE, encoding="utf-8") as fh:
            data = json.load(fh)
    data[name] = {"value": float("{:.4g}".format(float(value))), "unit": unit, "budget": budget, "note": note,
                  "date": datetime.date.today().isoformat()}
    with open(STORE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    rows = ["# Performance measurements", "",
            "| Measure | Result | Budget | Pass | Date | Note |", "| --- | --- | --- | --- | --- | --- |"]
    for k in sorted(data):
        d = data[k]
        ok = "" if d["budget"] is None else ("yes" if d["value"] <= d["budget"] else "NO")
        bud = "" if d["budget"] is None else "{} {}".format(d["budget"], d["unit"])
        rows.append("| {} | {:.4g} {} | {} | {} | {} | {} |".format(k, d["value"], d["unit"], bud, ok, d["date"],
                                                                  d["note"]))
    with open(DOC, "w", encoding="utf-8") as fh:
        fh.write("\n".join(rows) + "\n")
