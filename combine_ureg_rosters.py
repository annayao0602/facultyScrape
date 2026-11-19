#!/usr/bin/env python3
"""
combine_ureg_rosters.py
Combine the per-layout ureg rosters into one 2005-2015 table and a school-by-year count table.

  python combine_ureg_rosters.py \
      --a ureg_out/layout_a_roster.csv --b ureg_out/layout_b_roster.csv --c ureg_out/layout_c_roster.csv

Outputs (in ureg_out/):
  ureg_roster_2005_2015.csv        one row per person per school-year, with a core_suggested flag
  ureg_counts_by_school_year.csv   all-listed and core_suggested counts by school and year

core_suggested is a FLAG, not a filter. It marks a provisional core-faculty definition:
excludes Continuing & Professional Studies, retired, emeritus, honorary,
administrative and professional, and people listed only in a Dean's office.
"""
import argparse
import csv
from pathlib import Path

OUT = Path("ureg_out")
COLS = ["year", "school", "department", "name", "rank", "faculty_type", "core_suggested", "titles",
        "records", "n_listings", "flag", "layout", "source_url"]


def core(r):
    t = r["faculty_type"]
    if r["school"] == "Continuing & Professional Studies":
        return "no: SCPS"
    for k in ("retired", "emeritus", "honorary", "administrative and professional"):
        if k in t:
            return f"no: {k}"
    if r["flag"].startswith("admin office only") or t == "administration":
        return "no: administration only"
    return "yes"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--a", default=str(OUT / "layout_a_roster.csv"))
    ap.add_argument("--b", default=str(OUT / "layout_b_roster.csv"))
    ap.add_argument("--c", default=str(OUT / "layout_c_roster.csv"))
    a = ap.parse_args()

    rows = []
    for path, layout in ((a.a, "A"), (a.b, "B"), (a.c, "C")):
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                r["layout"] = layout
                r["core_suggested"] = core(r)
                rows.append(r)
    rows.sort(key=lambda r: (int(r["year"]), r["school"], r["department"], r["name"]))

    OUT.mkdir(exist_ok=True)
    with open(OUT / "ureg_roster_2005_2015.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    years = sorted({r["year"] for r in rows}, key=int)
    schools = sorted({r["school"] for r in rows})
    with open(OUT / "ureg_counts_by_school_year.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["school", "measure"] + years)
        for s in schools + ["TOTAL"]:
            sel = [r for r in rows if s == "TOTAL" or r["school"] == s]
            w.writerow([s, "all listed"] + [sum(r["year"] == y for r in sel) for y in years])
            w.writerow([s, "core_suggested"] + [sum(r["year"] == y and r["core_suggested"] == "yes" for r in sel)
                                                for y in years])
    print(f"{len(rows)} person-school-years -> {OUT / 'ureg_roster_2005_2015.csv'}")
    print("core_suggested by year:",
          ", ".join(f"{y}: {sum(r['year'] == y and r['core_suggested'] == 'yes' for r in rows)}" for y in years))


if __name__ == "__main__":
    main()
