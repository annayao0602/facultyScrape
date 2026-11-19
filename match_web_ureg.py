#!/usr/bin/env python3
"""
match_web_ureg.py: match a school's website roster (Wayback) to the Registrar (ureg) roster
for the same people, and label everyone both / web_only / ureg_only with a reason.

Pairs (website year -> ureg year) default to Law's: 2005 -> 2006 (the "2005" web roster is an
Aug 2006 snapshot; PROGRESS.md decision #21) and 2015 -> 2015.

Matching stages, applied in order, one-to-one (each ureg person used at most once):
  1. manual   decisions recorded in match_decisions.csv (always win)
  2. exact    identical normalized full name
  3. relaxed  same surname + same first/given name or nickname, middle names/initials not in conflict
  4. review   anything close but not certain goes to match_decisions.csv with an empty decision:
                - surnames spelled differently (difflib ratio >= --review-threshold) with
                  agreeing first names or initials (e.g. Sayler / Saylor)
                - same surname, different first name (e.g. Kerry / Karen Abrams)
                - same surname + initials only, or conflicting middle initials
              These stay UNMATCHED until you fill in decision = match / no_match and rerun.

Every unmatched person gets a reason (visiting / other school per title, in ureg another
year, adjunct, emeritus, listed under another school in ureg, ...).

Usage
  python match_web_ureg.py                         # Law, 2005->2006 and 2015->2015
  python match_web_ureg.py --pair 2015:2015 --review-threshold 0.75

Inputs   wayback_law/faculty_<webyear>.csv, ureg_out/ureg_roster_2005_2015.csv
Outputs  wayback_law/match/match_<webyear>.csv      one row per person (both / web_only / ureg_only / review)
         wayback_law/match/match_summary.csv         counts and agreement rates
         wayback_law/match/match_decisions.csv       manual review queue + your decisions (kept across runs)
Standard library only.
"""
import argparse
import csv
import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

UREG = Path("ureg_out/ureg_roster_2005_2015.csv")
WEB_DIR = Path("wayback_law")
OUT = WEB_DIR / "match"
DECISIONS = OUT / "match_decisions.csv"

SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
HONORIFICS = {"prof", "professor", "dr", "judge", "hon", "honorable", "the", "mr", "ms", "mrs"}

# Nickname groups: any two names in a group are treated as the same first name.
NICKNAMES = [
    "douglas doug", "william bill will billy", "robert bob rob bobby", "richard dick rick rich",
    "james jim jimmy jamie", "michael mike", "thomas tom tommy", "edward ed ted eddie",
    "theodore ted teddy", "kimberly kim", "elizabeth liz beth betsy libby", "katherine kate kathy katie",
    "catherine kate cathy", "christopher chris", "christine chris", "daniel dan danny", "david dave",
    "joseph joe", "john jack", "lawrence larry", "patrick pat", "patricia pat patty", "margaret peggy meg maggie",
    "mary molly", "anthony tony", "andrew andy drew", "alexander alex", "benjamin ben", "charles charlie chuck",
    "frederick fred", "gregory greg", "jeffrey jeff", "jonathan jon", "kenneth ken", "matthew matt",
    "nicholas nick", "peter pete", "philip phil", "phillip phil", "ronald ron", "samuel sam", "stephen steve",
    "steven steve", "susan sue", "timothy tim", "walter walt", "gerald jerry", "henry hank harry",
    "rebecca becky", "deborah debbie deb", "jennifer jen jenny", "cynthia cindy", "barbara barb",
    "frances fran", "francis frank", "abigail abby", "leslie les", "leonard len lenny", "raymond ray",
    "donald don", "eugene gene", "albert al", "allen al", "alfred al", "zachary zach",
]
NICK = defaultdict(set)
for g in NICKNAMES:
    names = g.split()
    for n in names:
        NICK[n].update(names)

# Web-title cues that the person isn't UVA Law faculty proper.
VISITING_RE = re.compile(r"\bvisiting\b", re.I)
# Another institution named in the title ("..., George Washington University Law School",
# "University of Sydney", "London School of Economics", "Münster University").
OTHER_INST_RE = re.compile(r"\b(University|Universit[éä]t?|College|School of Economics|Faculty of Law)\b", re.I)
# Another UVA school/department named in the title.
OTHER_UVA_RE = re.compile(
    r"School of Medicine|Department of|Medical|Neurology|Psychiatr|Environmental Sciences|English|"
    r"Spanish|Media Studies|Curry School|Education|Batten|Darden|Engineering|Commerce|Nursing", re.I)


# ----------------------------------------------------------------------------
# Names
# ----------------------------------------------------------------------------
def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def parse_name(raw):
    """Return dict: tokens, first, given (first non-initial), last, middles, key."""
    s = strip_accents(raw or "")
    s = re.sub(r"\(.*?\)", " ", s)
    if s.count(",") == 1 and not re.search(r",\s*(jr|sr|ii|iii|iv)\b\.?", s, re.I):
        last, rest = s.split(",")          # surname-first ("Abraham, Kenneth S.")
        s = f"{rest} {last}"
    s = s.lower().replace("’", "'")
    s = re.sub(r"[.,]", " ", s)
    toks = [t.strip("'") for t in s.split()]
    toks = [t for t in toks if t and t not in SUFFIXES and t not in HONORIFICS]
    if not toks:
        return {"tokens": [], "first": "", "given": "", "last": "", "middles": [], "key": ""}
    first, last = toks[0], toks[-1]
    given = next((t for t in toks[:-1] if len(t) > 1), first)
    return {"tokens": toks, "first": first, "given": given, "last": last,
            "middles": toks[1:-1], "key": " ".join(toks)}


def same_first(a, b):
    return a == b or b in NICK.get(a, ()) or a in NICK.get(b, ())


def first_names_agree(p, q):
    return (same_first(p["first"], q["first"]) or same_first(p["given"], q["given"])
            or same_first(p["given"], q["first"]) or same_first(p["first"], q["given"]))


def middles_conflict(p, q):
    """True if both have a middle name/initial and the first letters differ."""
    pm = [m for m in p["middles"] if m != p["given"]]
    qm = [m for m in q["middles"] if m != q["given"]]
    return bool(pm and qm and pm[0][0] != qm[0][0])


def surname_eq(p, q):
    return p["last"] == q["last"] or p["last"].replace("-", "") == q["last"].replace("-", "")


def score(p, q):
    return SequenceMatcher(None, f"{p['given']} {p['last']}", f"{q['given']} {q['last']}").ratio()


# ----------------------------------------------------------------------------
# IO
# ----------------------------------------------------------------------------
def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, cols):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


DEC_COLS = ["web_year", "ureg_year", "web_name", "ureg_name", "why_flagged", "score",
            "web_title", "ureg_title", "decision", "note"]


def load_decisions():
    if not DECISIONS.exists():
        return []
    rows = read_csv(DECISIONS)
    for r in rows:
        r["decision"] = (r.get("decision") or "").strip().lower()
    return rows


# ----------------------------------------------------------------------------
# Matching
# ----------------------------------------------------------------------------
def match_pair(web_year, ureg_year, school, web_rows, ureg_all, decisions, review_threshold):
    ureg = [r for r in ureg_all if r["school"] == school and r["year"] == str(ureg_year)]
    W = [{"row": r, "n": parse_name(r["name"])} for r in web_rows]
    U = [{"row": r, "n": parse_name(r["name"])} for r in ureg]
    pairs = {}            # web index -> (ureg index, method, score)
    used_u = set()

    def link(wi, ui, method, sc):
        pairs[wi] = (ui, method, sc)
        used_u.add(ui)

    # 1. manual decisions
    mine = [d for d in decisions if d["web_year"] == str(web_year) and d["ureg_year"] == str(ureg_year)]
    rejected = {(d["web_name"], d["ureg_name"]) for d in mine if d["decision"] == "no_match"}
    for d in mine:
        if d["decision"] != "match":
            continue
        wi = next((i for i, w in enumerate(W) if w["row"]["name"] == d["web_name"] and i not in pairs), None)
        ui = next((i for i, u in enumerate(U) if u["row"]["name"] == d["ureg_name"] and i not in used_u), None)
        if wi is not None and ui is not None:
            link(wi, ui, "manual", "")

    # 2. exact
    by_key = defaultdict(list)
    for i, u in enumerate(U):
        by_key[u["n"]["key"]].append(i)
    for wi, w in enumerate(W):
        if wi in pairs:
            continue
        cands = [i for i in by_key.get(w["n"]["key"], []) if i not in used_u]
        if len(cands) == 1:
            link(wi, cands[0], "exact", "1.00")

    # 3. relaxed (unique candidate only; ambiguous goes to review)
    review = []
    for wi, w in enumerate(W):
        if wi in pairs:
            continue
        cands = [i for i, u in enumerate(U) if i not in used_u and surname_eq(w["n"], u["n"])]
        ok = [i for i in cands if first_names_agree(w["n"], U[i]["n"]) and not middles_conflict(w["n"], U[i]["n"])]
        ok = [i for i in ok if (W[wi]["row"]["name"], U[i]["row"]["name"]) not in rejected]
        if len(ok) == 1:
            link(wi, ok[0], "relaxed", f"{score(w['n'], U[ok[0]]['n']):.2f}")
            continue
        # 4. review candidates for this web person
        for i in cands:
            u = U[i]
            if first_names_agree(w["n"], u["n"]) and middles_conflict(w["n"], u["n"]):
                why = "same first and last name, middle initials differ"
            elif len(ok) > 1 and i in ok:
                why = "several ureg people fit"
            elif w["n"]["first"][:1] == u["n"]["first"][:1] and min(len(w["n"]["first"]), len(u["n"]["first"])) == 1:
                why = "same surname, first name is only an initial"
            else:
                why = "same surname, different first name"
            review.append((wi, i, why))
    for wi, w in enumerate(W):
        if wi in pairs:
            continue
        for i, u in enumerate(U):
            if i in used_u or surname_eq(w["n"], u["n"]):
                continue
            # Fuzzy = surname spelled differently (Sayler / Saylor). Require a similar surname AND
            # agreeing first names (or initials), so a shared first name alone can't qualify.
            sur = SequenceMatcher(None, w["n"]["last"], u["n"]["last"]).ratio()
            firsts_ok = first_names_agree(w["n"], u["n"]) or w["n"]["first"][:1] == u["n"]["first"][:1]
            if sur >= review_threshold and firsts_ok:
                review.append((wi, i, f"fuzzy surname score {sur:.2f}"))

    # drop review items already decided, or whose people got matched elsewhere
    decided = {(d["web_name"], d["ureg_name"]) for d in mine if d["decision"] in ("match", "no_match")}
    queue = []
    for wi, ui, why in review:
        if wi in pairs or ui in used_u:
            continue
        key = (W[wi]["row"]["name"], U[ui]["row"]["name"])
        if key in decided:
            continue
        queue.append({"web_year": web_year, "ureg_year": ureg_year, "web_name": key[0], "ureg_name": key[1],
                      "why_flagged": why, "score": f"{score(W[wi]['n'], U[ui]['n']):.2f}",
                      "web_title": W[wi]["row"].get("rank", ""), "ureg_title":
                      " | ".join(x for x in (U[ui]["row"]["rank"], U[ui]["row"]["titles"]) if x),
                      "decision": "", "note": ""})
    pending_w = {W_index for W_index, _, _ in review if W_index not in pairs}
    pending_u = {u for wi, u, _ in review if wi not in pairs and u not in used_u}
    pending_w = {wi for wi in pending_w
                 if any(q["web_name"] == W[wi]["row"]["name"] for q in queue)}
    pending_u = {ui for ui in pending_u
                 if any(q["ureg_name"] == U[ui]["row"]["name"] for q in queue)}

    # ------------------------------------------------------------------ reasons
    # Name lookups across all ureg years/schools, by surname then nickname-aware first name.
    by_surname = defaultdict(list)   # surname -> [(parsed name, school, year)]
    for r in ureg_all:
        n = parse_name(r["name"])
        by_surname[n["last"].replace("-", "")].append((n, r["school"], int(r["year"])))

    def lookups(n):
        return [(s, y) for m, s, y in by_surname.get(n["last"].replace("-", ""), [])
                if first_names_agree(n, m)]

    def school_years(n):
        return {y for s, y in lookups(n) if s == school}

    def web_only_reason(w):
        title = w["row"].get("rank", "")
        n = w["n"]
        other = sorted({s for s, y in lookups(n) if y == ureg_year and s != school})
        if other:
            return f"in ureg {ureg_year} under {', '.join(other)}"
        if VISITING_RE.search(title):
            return "visiting (per web title)"
        if OTHER_INST_RE.search(title) and not re.search(r"Virginia", title):
            return "other institution (per web title)"
        if OTHER_UVA_RE.search(title) and not re.search(r"Professor of Law|of Law\b", title):
            return "other UVA school or discipline (per web title)"
        yrs = school_years(n)
        later = sorted(y for y in yrs if y > ureg_year)
        earlier = sorted(y for y in yrs if y < ureg_year)
        if later and not earlier:
            return f"not yet in ureg; first in ureg {later[0]} (likely new hire)"
        if earlier and not later:
            return f"in ureg until {earlier[-1]} (left, or dropped from ureg)"
        if earlier and later:
            return f"in ureg {earlier[-1]} and {later[0]} but not {ureg_year} (ureg gap)"
        return "unexplained"

    def ureg_only_reason(u):
        r = u["row"]
        ft = r["faculty_type"]
        if "adjunct" in ft:
            return "adjunct (website roster lists resident faculty)"
        if "emeritus" in ft or "retired" in ft:
            return "retired / emeritus"
        if ft == "administration":
            return "administration / Dean's office"
        if ft in ("lecturer", "research", "visiting"):
            return f"{ft} (may not be on website roster)"
        yrs = school_years(u["n"])
        if not any(y > ureg_year for y in yrs) and ureg_year < 2015:
            return f"last in ureg {ureg_year} (likely departed before web snapshot)"
        return "unexplained"

    # ------------------------------------------------------------------ rows
    out = []
    for wi, w in enumerate(W):
        r = w["row"]
        base = {"web_year": web_year, "ureg_year": ureg_year, "school": school,
                "web_name": r["name"], "web_title": r.get("rank", ""), "website_link": r.get("website_link", ""),
                "profile_snapshot_ts": r.get("profile_snapshot_ts", "")}
        if wi in pairs:
            ui, method, sc = pairs[wi]
            u = U[ui]["row"]
            out.append({**base, "status": "both", "match_method": method, "score": sc,
                        "ureg_name": u["name"], "ureg_rank": u["rank"], "ureg_faculty_type": u["faculty_type"],
                        "ureg_core_suggested": u["core_suggested"], "ureg_department": u["department"],
                        "ureg_titles": u["titles"], "reason": ""})
        elif wi in pending_w:
            out.append({**base, "status": "review", "match_method": "", "score": "",
                        "reason": "candidate match in match_decisions.csv"})
        else:
            out.append({**base, "status": "web_only", "match_method": "", "score": "",
                        "reason": web_only_reason(w)})
    for ui, u in enumerate(U):
        if ui in used_u:
            continue
        r = u["row"]
        out.append({"web_year": web_year, "ureg_year": ureg_year, "school": school,
                    "status": "review" if ui in pending_u else "ureg_only",
                    "ureg_name": r["name"], "ureg_rank": r["rank"], "ureg_faculty_type": r["faculty_type"],
                    "ureg_core_suggested": r["core_suggested"], "ureg_department": r["department"],
                    "ureg_titles": r["titles"],
                    "reason": "candidate match in match_decisions.csv" if ui in pending_u else ureg_only_reason(u)})
    return out, queue


MATCH_COLS = ["web_year", "ureg_year", "school", "status", "match_method", "score", "reason",
              "web_name", "ureg_name", "web_title", "ureg_rank", "ureg_faculty_type", "ureg_core_suggested",
              "ureg_department", "ureg_titles", "website_link", "profile_snapshot_ts"]


def summarize(rows):
    """Agreement at three scopes. 'found' = share of the ureg group that is on the website."""
    s = Counter(r["status"] for r in rows)
    ureg_side = [r for r in rows if r.get("ureg_name")]
    web_side = [r for r in rows if r.get("web_name")]
    web_law = [r for r in web_side if not (r["status"] == "web_only" and
               re.match(r"(in ureg \d{4} under|visiting|other institution|other UVA)", r["reason"]))]

    def found(group):
        n = len(group)
        b = sum(r["status"] == "both" for r in group)
        return n, b, f"{b / n:.1%}" if n else ""

    scopes = {
        "ureg all listed": ureg_side,
        "ureg core_suggested=yes": [r for r in ureg_side if r["ureg_core_suggested"] == "yes"],
        "ureg regular faculty": [r for r in ureg_side if r["ureg_faculty_type"] == "regular"],
    }
    out = {"web_people": len(web_side), "ureg_people": len(ureg_side),
           "both": s["both"], "web_only": s["web_only"], "ureg_only": s["ureg_only"],
           "review rows (web + ureg side)": s["review"],
           "web_in_ureg": f"{sum(r['status'] == 'both' for r in web_side) / len(web_side):.1%}" if web_side else "",
           "web_law_faculty_in_ureg": f"{sum(r['status'] == 'both' for r in web_law) / len(web_law):.1%}"
                                      if web_law else ""}
    for name, g in scopes.items():
        n, b, pct = found(g)
        out[f"{name}: n"], out[f"{name}: on web"], out[f"{name}: share on web"] = n, b, pct
    out["match methods"] = "; ".join(f"{k} {v}" for k, v in
                                     Counter(r["match_method"] for r in rows if r["status"] == "both").items())
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pair", action="append", help="WEBYEAR:UREGYEAR (repeatable). Default 2005:2006 2015:2015")
    ap.add_argument("--school", default="Law")
    ap.add_argument("--review-threshold", type=float, default=0.80,
                    help="surname similarity (difflib ratio) at or above which a pair with agreeing first "
                         "names is queued for review (default 0.80). Fuzzy pairs are never accepted automatically.")
    args = ap.parse_args()
    pairs = [tuple(map(int, p.split(":"))) for p in (args.pair or ["2005:2006", "2015:2015"])]

    ureg_all = read_csv(UREG)
    decisions = load_decisions()
    summary, new_queue = [], []
    for web_year, ureg_year in pairs:
        web_rows = read_csv(WEB_DIR / f"faculty_{web_year}.csv")
        rows, queue = match_pair(web_year, ureg_year, args.school, web_rows, ureg_all, decisions,
                                 args.review_threshold)
        order = {"both": 0, "review": 1, "web_only": 2, "ureg_only": 3}
        rows.sort(key=lambda r: (order[r["status"]], r["reason"], r.get("web_name") or r.get("ureg_name")))
        write_csv(OUT / f"match_{web_year}.csv", rows, MATCH_COLS)
        new_queue += queue
        s = {"web_year": web_year, "ureg_year": ureg_year, "school": args.school, **summarize(rows)}
        summary.append(s)
        print(f"\n[{args.school} web {web_year} vs ureg {ureg_year}] -> {OUT / f'match_{web_year}.csv'}")
        for k, v in s.items():
            if k not in ("web_year", "ureg_year", "school"):
                print(f"   {k:38s} {v}")
        print("   reasons:")
        for (st, reason), c in Counter((r["status"], re.sub(r"\d{4}", "YYYY", r["reason"]))
                                       for r in rows if r["status"] != "both").most_common():
            print(f"     {c:4d}  {st:9s} {reason}")

    write_csv(OUT / "match_summary.csv", summary, list(summary[0].keys()))
    # keep every decided row; replace the undecided queue with this run's
    kept = [d for d in decisions if d["decision"] in ("match", "no_match")]
    write_csv(DECISIONS, kept + new_queue, DEC_COLS)
    print(f"\nreview queue: {len(new_queue)} undecided pairs (+{len(kept)} decided) -> {DECISIONS}")
    print("   fill in decision = match / no_match, then rerun.")


if __name__ == "__main__":
    main()
