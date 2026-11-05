#!/usr/bin/env python3
"""
ureg_layout_a_parser.py
Phase 1 parser for Registrar Records layout A (2005-06 to 2009-10): the faculty section
of a school's information page (preview_entity.php ... #faculty).

The section is a sequence of lines of four kinds, classified by their text:
  unit heading     "Department of ...", "Office of the Dean ...", "Program in ..."  -> department
  program heading  short line under a department, e.g. "Architecture"            -> program
  rank heading     "Professors", "Associate Professors", "Professors Emeriti"     -> rank, faculty_type
  faculty line     "Name[, Jr.], degree, degree, ..., title(s)"

Input, either:
  one page:  python ureg_layout_a_parser.py --html arch_2005.html --year 2005 --school Architecture --url "..."
  one text:  python ureg_layout_a_parser.py --text arch_2005.txt  --year 2005 --school Architecture
  batch:     python ureg_layout_a_parser.py --manifest manifest_layout_a.csv
             manifest columns: year,record,school,url,file   (file = saved HTML; if blank, url is fetched)

Outputs (in ureg_out/):
  layout_a_listings.csv   one row per line in the Record (a person can appear more than once)
  layout_a_roster.csv     one row per person per school-year
  layout_a_headings.csv   every heading seen and how it was classified -> review for new rank/type words

Requires: pip install beautifulsoup4 lxml requests
"""
import argparse
import csv
import re
import time
from collections import OrderedDict
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

OUT = Path("ureg_out")
HEADINGS_STOP = {"h1", "h2"}
BLOCKS = {"p", "div", "li", "tr", "td", "br", "dd", "dt", "h3", "h4", "h5", "h6",
          "table", "ul", "ol", "strong", "b", "em", "hr"}

SUFFIXES = {"Jr.", "Sr.", "Jr", "Sr", "II", "III", "IV"}
# degree token: no spaces, contains a period, short (B.A., M.Arch., Ph.D., M.Arch/U.D., LL.M.)
DEGREE_RE = re.compile(r"^[A-Z][A-Za-z.\-/]*\.[A-Za-z.\-/]*$|^[A-Z][a-z]?\.[A-Z][A-Za-z.\-/]*$")
RANK_WORDS = re.compile(r"\b(Professors?|Lecturers?|Instructors?|Emerit(i|us|a|ae)|Fellows?|"
                        r"Scholars?|Librarians?|Faculty|Teaching|Artists?|Writers?|Researchers?)\b", re.I)
UNIT_RE = re.compile(r"^(Department|Office|Division|Program|Center|Centre|Institute|School|"
                     r"Section|Area|Committee|Interdepartmental)\b", re.I)
TITLE_RANK_RE = re.compile(r"\b((Distinguished |University |Associate |Assistant |Visiting |Adjunct |"
                           r"Research |Clinical )*(Professor|Lecturer|Instructor))\b")

RANK_ORDER = ["Professor", "Associate Professor", "Assistant Professor", "Lecturer", "Instructor"]


# ----------------------------------------------------------------------------
# Line extraction
# ----------------------------------------------------------------------------
def lines_from_html(html, anchor="faculty"):
    soup = BeautifulSoup(html, "lxml")
    start = soup.find(id=anchor) or soup.find(attrs={"name": anchor})
    if not start:  # fall back to a heading whose text is exactly "Faculty"
        start = next((h for h in soup.find_all(["h1", "h2", "h3", "h4", "strong", "b", "p"])
                      if h.get_text(strip=True).lower() == "faculty"), None)
    if not start:
        raise ValueError("No faculty section found (no #faculty anchor or 'Faculty' heading)")
    lines, buf, seen_entry = [], [], False

    def flush():
        nonlocal seen_entry
        line = re.sub(r"\s+", " ", "".join(buf)).strip()
        buf.clear()
        if line:
            lines.append(line)
            if classify(line)[0] == "entry":
                seen_entry = True

    for el in start.next_elements:
        if isinstance(el, Tag):
            if el.name in HEADINGS_STOP and seen_entry:
                break
            if el.name in BLOCKS:
                flush()
        elif isinstance(el, NavigableString) and el.parent.name not in ("script", "style"):
            buf.append(str(el))
    flush()
    return lines


def lines_from_text(text):
    return [re.sub(r"\s+", " ", l).strip() for l in text.splitlines() if l.strip()]


# ----------------------------------------------------------------------------
# Classification and parsing
# ----------------------------------------------------------------------------
def is_degree(seg):
    s = seg.strip()
    return bool(s) and " " not in s and len(s) <= 14 and s not in SUFFIXES and bool(DEGREE_RE.match(s))


def classify(line):
    """Return (kind, payload). kind in: skip, entry, rank, unit, program."""
    if line.lower() in ("faculty", "faculty:"):
        return "skip", None
    if "," in line:
        segs = [s.strip() for s in line.split(",")]
        nxt = segs[1] if len(segs) > 1 else ""
        if is_degree(nxt) or nxt in SUFFIXES or (len(segs) > 2 and is_degree(segs[2])):
            return "entry", segs
    words = line.split()
    if len(words) <= 10 and "," not in line:
        if UNIT_RE.match(line):
            return "unit", line
        if RANK_WORDS.search(line):
            return "rank", line
        if len(words) <= 6:
            return "program", line
    return "unparsed", line


def parse_entry(segs):
    name, i = segs[0], 1
    if i < len(segs) and segs[i] in SUFFIXES:
        name, i = f"{name}, {segs[i]}", i + 1
    degrees = []
    while i < len(segs) and is_degree(segs[i]):
        degrees.append(segs[i])
        i += 1
    title = ", ".join(s for s in segs[i:] if s)
    return name, degrees, title


def rank_from_heading(h):
    """'Associate Professors' -> 'Associate Professor'; 'Professors Emeriti' -> 'Professor'."""
    m = TITLE_RANK_RE.search(re.sub(r"s\b", "", h)) or TITLE_RANK_RE.search(h)
    if not m:
        return ""
    r = m.group(1)
    for mod in ("Visiting ", "Adjunct ", "Research ", "Clinical ", "Distinguished ", "University "):
        r = r.replace(mod, "")
    return r.strip()


def type_from_heading(h, unit):
    t = []
    if unit and re.match(r"Office of the (Dean|Provost)", unit, re.I):
        t.append("administration")
    if not h:
        return "; ".join(t) or "unclassified"
    for word, label in [(r"Emerit", "emeritus"), (r"Visiting", "visiting"), (r"Adjunct", "adjunct"),
                        (r"Research", "research"), (r"Clinical", "clinical"),
                        (r"General Faculty", "general faculty"), (r"Lecturer", "lecturer"),
                        (r"Instructor", "instructor"), (r"Fellow", "fellow"), (r"Librarian", "librarian")]:
        if re.search(word, h, re.I):
            t.append(label)
    return "; ".join(t) or "regular"


def parse_lines(lines, meta):
    listings, headings = [], []
    unit = program = rank_h = ""
    for line in lines:
        kind, payload = classify(line)
        if kind == "skip":
            continue
        if kind in ("unit", "program", "rank", "unparsed"):
            headings.append({**meta, "heading": line, "classified_as": kind})
        if kind == "unit":
            unit, program, rank_h = payload, "", ""
        elif kind == "program":
            program, rank_h = payload, ""
        elif kind == "rank":
            rank_h = payload
        elif kind == "entry":
            name, degrees, title = parse_entry(payload)
            rank, src = rank_from_heading(rank_h), "heading"
            if not rank:
                m = TITLE_RANK_RE.search(title)
                rank, src = (m.group(1), "title") if m else ("", "none")
            listings.append({**meta, "department": unit, "program": program, "name": name,
                             "rank": rank, "rank_source": src, "rank_heading": rank_h,
                             "faculty_type": type_from_heading(rank_h, unit), "title": title,
                             "degrees": "; ".join(degrees), "raw_line": line})
    return listings, headings


def collapse(listings):
    """One row per person per school-year."""
    people = OrderedDict()
    for l in listings:
        key = (l["year"], l["record"], l["school"], l["name"].lower())
        p = people.setdefault(key, {k: l[k] for k in ("year", "record", "school", "name", "source_url")}
                              | {"departments": [], "ranks": [], "types": [], "titles": [], "n_listings": 0})
        p["n_listings"] += 1
        for f, v in (("departments", " > ".join(x for x in (l["department"], l["program"]) if x)),
                     ("ranks", l["rank"]), ("types", l["faculty_type"]), ("titles", l["title"])):
            if v and v not in p[f]:
                p[f].append(v)
    rows = []
    for p in people.values():
        ranked = [r for r in p["ranks"] if r in RANK_ORDER]
        best = min(ranked, key=RANK_ORDER.index) if ranked else (p["ranks"][0] if p["ranks"] else "")
        types = [t for t in p["types"] if t != "administration"] or p["types"]
        admin_only = p["types"] == ["administration"]
        rows.append({"year": p["year"], "record": p["record"], "school": p["school"],
                     "department": " | ".join(d for d in p["departments"] if not d.startswith("Office of")),
                     "name": p["name"], "rank": best, "faculty_type": "; ".join(dict.fromkeys(types)),
                     "titles": " | ".join(p["titles"]), "n_listings": p["n_listings"],
                     "flag": "admin office only; may be staff" if admin_only else
                             ("no rank found" if not best else ""),
                     "source_url": p["source_url"]})
    return rows


# ----------------------------------------------------------------------------
# I/O
# ----------------------------------------------------------------------------
def write(path, rows):
    if not rows:
        print(f"  (nothing to write for {path.name})")
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"  {len(rows):5d} rows -> {path}")


BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                   "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_session = None
USE_BROWSER = False   # set by --browser: fetch with Playwright (a real browser) instead


def _get_requests(url):
    global _session
    import requests
    if _session is None:
        _session = requests.Session()
        _session.headers.update(BROWSER_HEADERS)
        # visit the catalog home first so the site sets any session cookies
        m = re.search(r"catoid=(\d+)", url)
        if m:
            _session.get(f"https://records.ureg.virginia.edu/index.php?catoid={m.group(1)}", timeout=60)
            time.sleep(2)
    r = _session.get(url, timeout=60)
    r.raise_for_status()
    return r.content


def _get_browser(url):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(user_agent=BROWSER_HEADERS["User-Agent"])
        page.goto(url, wait_until="networkidle", timeout=60000)
        html = page.content().encode("utf-8")
        b.close()
    return html


def fetch(url, cache_dir=OUT / "cache"):
    import hashlib
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".html")
    if path.exists() and path.stat().st_size > 1000:
        return path.read_bytes()
    time.sleep(2)
    html = _get_browser(url) if USE_BROWSER else _get_requests(url)
    if len(html) < 1000:
        raise ValueError(f"site returned an empty page ({len(html)} bytes); try --browser, "
                         f"or save the page from your browser and use the manifest's file column")
    path.write_bytes(html)
    return html


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--html")
    src.add_argument("--text")
    src.add_argument("--manifest")
    ap.add_argument("--year", type=int)
    ap.add_argument("--record", default="Graduate")
    ap.add_argument("--school")
    ap.add_argument("--url", default="")
    ap.add_argument("--browser", action="store_true",
                    help="fetch pages with a real browser (pip install playwright; playwright install chromium)")
    a = ap.parse_args()
    global USE_BROWSER
    USE_BROWSER = a.browser
    OUT.mkdir(exist_ok=True)

    jobs = []
    if a.manifest:
        with open(a.manifest, newline="", encoding="utf-8") as f:
            jobs = list(csv.DictReader(f))
    else:
        if not (a.year and a.school):
            raise SystemExit("--year and --school are required with --html/--text")
        jobs = [{"year": a.year, "record": a.record, "school": a.school, "url": a.url,
                 "file": a.html or a.text, "is_text": bool(a.text)}]

    all_listings, all_headings = [], []
    for j in jobs:
        meta = {"year": int(j["year"]), "record": j.get("record", ""), "school": j["school"],
                "source_url": j.get("url", "")}
        try:
            if j.get("is_text"):
                lines = lines_from_text(Path(j["file"]).read_text(encoding="utf-8"))
            else:
                html = Path(j["file"]).read_bytes() if j.get("file") else fetch(j["url"])
                lines = lines_from_html(html)
        except Exception as e:
            print(f"[{meta['year']} {meta['school']}] ERROR: {e}")
            continue
        listings, headings = parse_lines(lines, meta)
        print(f"[{meta['year']} {meta['record']} {meta['school']}] {len(listings)} listings, "
              f"{sum(h['classified_as'] == 'unparsed' for h in headings)} unparsed lines")
        all_listings += listings
        all_headings += headings

    write(OUT / "layout_a_listings.csv", all_listings)
    write(OUT / "layout_a_roster.csv", collapse(all_listings))
    write(OUT / "layout_a_headings.csv", all_headings)


if __name__ == "__main__":
    main()
