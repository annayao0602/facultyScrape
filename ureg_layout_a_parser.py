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
  folder:    python ureg_layout_a_parser.py --folder ureg_pages
             (pages saved with the save-page bookmarklet; year, Record and school are read
              from the file name and page heading)
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
RANK_WORDS = re.compile(r"\b(Coordinators?|Professors?|Lecturers?|Instructors?|Emerit(i|us|a|ae)|Fellows?|"
                        r"Scholars?|Librarians?|Faculty|Teaching|Artists?|Writers?|Researchers?|Visiting|Honorary|"
                        r"Retired|Appointments?|Adjunct)\b", re.I)
UNIT_RE = re.compile(r"^(Department|Office|Division|Program|Center|Centre|Institute|School|"
                     r"Section|Area|Committee|Interdepartmental)\b", re.I)
TITLE_RANK_RE = re.compile(r"\b((Distinguished |University |Associate |Assistant |Visiting |Adjunct |"
                           r"Research |Clinical )*(Professor|Lecturer|Instructor))\b")

NAV_TABS = {"About", "Academic Rules", "Departments/Programs", "Courses", "Information", "Programs/Courses",
            "Policies", "Programs", "Degree Information", "Concentrations, Minors, and Tracks"}
STOP_RE = re.compile(r"^(Return to:|Back to Top|Print \(opens|Help \(opens|Add to (My|Portfolio))", re.I)

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
    if not start:  # standalone faculty pages (layouts B/C): first h1/h2 mentioning Faculty
        start = next((h for h in soup.find_all(["h1", "h2"])
                      if "faculty" in h.get_text(strip=True).lower()), None)
    if not start:
        raise ValueError("No faculty section found (no #faculty anchor or 'Faculty' heading)")
    lines, buf, seen_entry = [], [], False

    def flush():
        nonlocal seen_entry
        line = re.sub(r"\s+", " ", "".join(buf)).strip()
        buf.clear()
        if line:
            lines.append(line)
            if classify(line)[0] == "stop":
                return True
            if classify(line)[0] == "entry":
                seen_entry = True

    for el in start.next_elements:
        if isinstance(el, Tag):
            # stop at the next h1/h2 section, but not at a repeated "Faculty" or a group heading
            # (Darden 2008-09 has a second <h2>Faculty</h2> after the Dean's office)
            if el.name in HEADINGS_STOP and seen_entry and \
                    classify(el.get_text(" ", strip=True))[0] not in ("skip", "rank", "unit"):
                break
            if el.name in BLOCKS and flush():
                return lines
        elif isinstance(el, NavigableString) and el.parent.name not in ("script", "style"):
            buf.append(str(el))
    flush()
    return lines


def lines_from_text(text):
    return [re.sub(r"\s+", " ", l).strip() for l in text.splitlines() if l.strip()]


# ----------------------------------------------------------------------------
# Classification and parsing
# ----------------------------------------------------------------------------
PAREN_RE = re.compile(r"\s*\([^)]*\)")


NODOT_DEGREE_RE = re.compile(
    r"^(PhD|EdD|DNSc|DSN|PsyD|DrPH|ScD|DPhil|MPhil|MEd|MEng|MArch|BArch|BSc|MSc|MSt|LLM|LLB|"
    r"[A-Z]{2,5}(-[A-Z]{2,4})?)$")      # all-caps degrees and credentials: MD, MSN, RN, FAAN, APRN-BC


def _is_degree_token(s):
    s = re.sub(r"^and\s+", "", PAREN_RE.sub("", s)).strip()
    if s in SUFFIXES:
        return False
    if NODOT_DEGREE_RE.match(s):
        return True
    return 3 <= len(s) <= 14 and bool(DEGREE_RE.match(s))


SPELLED_DEGREE_RE = re.compile(
    r"^((Honorary )?(Doctor|Master|Bachelor)s? of [A-Z][A-Za-z ]+"
    r"|(Dipl[ôo]me|Licence|Licencia|Licenciado|Licenciatura|Diplom[ae]?|Dipl\.|Doctorado|Doctorat|Doctor en|"
    r"Baccalaur[eé]at|Bachillerato|Magister|Laurea|Dottore|Habilitation|Ma[iî]trise|Agr[eé]gation|"
    r"Staatsexamen|Candidatus)\b.*)$")
SPACED_DEGREE_RE = re.compile(r"\.\s+(?=[A-Z])")


def is_degree(seg):
    """One degree ('Ph.D.'), several separated by spaces ('B.S. M.S. Ph.D.'),
    or a spelled-out degree ('Doctor of Humanities')."""
    if SPELLED_DEGREE_RE.match(seg.strip()):
        return True
    toks = re.sub(r"^and\s+", "", PAREN_RE.sub("", seg).strip()).split()
    if toks and len(toks) <= 5 and all(_is_degree_token(t) for t in toks):
        return True
    # spaced forms: "B. A.", "Ph. D.", "M. Arch", "B.S. Arch"
    joined = SPACED_DEGREE_RE.sub(".", PAREN_RE.sub("", seg).strip())
    joined = re.sub(r"^([A-Z][a-z]?\.?)\s+(Arch|Ed|Phil|Div|Mus|Sc)\b\.?$", r"\1\2.", joined)
    return (" " not in joined and _is_degree_token(joined)
            and all(len(part) <= 4 for part in joined.split(".")))   # rejects "Ph.D. Emeritus"


NAME_RE = re.compile(r"^(?:[A-Z][\w'’\-]*\.?|\([A-Z][\w]*\)|[a-z]{1,3})(?:\s+(?:[A-Z][\w'’\-]*\.?|\([A-Z][\w]*\)|de|da|von|van|der|del|la|le|di|du|(?:al|el|ibn|bin|abu)-?[A-Z][\w'’\-]*)){1,5}$")
TITLE_WORDS = re.compile(r"\b(Professor|Lecturer|Instructor|Dean|Director|Emerit|Fellow|Chair|Visiting|"
                         r"Coordinator|Librarian|Scholar|Researcher|Faculty)", re.I)
ORG_WORDS = re.compile(r"\b(Center|Centre|Program|Institute|Office|School|Committee|Studies|Science|"
                       r"Sciences|Academy|Education|Engineering|Architecture|Development|Fitness|"
                       r"Communication|Administration|Leadership|Law|Planning|History|Languages?)\b")
DEPT_ANY_RE = re.compile(r"\b(Department|School|Division|Program|Institute|Center|Office) (of|for)\b")


def split_segments(line):
    """Comma-split a line, dropping empty pieces ('MA,, Ph.D.') and peeling degrees that follow
    the name without a comma ('Jorg Liebeherr PhD, BS'; 'Weiqiang Wang . BS, MS, PhD')."""
    segs = [x.strip() for x in line.split(",") if x.strip()]
    if not segs:
        return segs
    m = re.match(r"^(.+?[a-z])\.\s+((?:[A-Z][A-Za-z]{0,3}\.){2,}.*)$", segs[0])
    if m and NAME_RE.match(m.group(1)):
        segs = [m.group(1), m.group(2)] + segs[1:]
    toks = segs[0].replace(" .", " ").split()
    peeled = []
    while len(toks) > 2 and _is_degree_token(toks[-1]):
        peeled.insert(0, toks.pop())
    if peeled and NAME_RE.match(" ".join(toks)):
        segs = [" ".join(toks)] + peeled + segs[1:]
    else:
        segs[0] = segs[0].rstrip(" .")
    return segs


def classify(line):
    """Return (kind, payload). kind in: skip, stop, entry, rank, unit, program, unparsed."""
    m = re.match(r"^(.+?[a-z])\.\s+((?:[A-Z][A-Za-z]{0,3}\.){2,}.*)$", line)   # "Brian H. Annex. M.D."
    if m and "," not in m.group(1) and NAME_RE.match(m.group(1)):
        line = f"{m.group(1)}, {m.group(2)}"
    if line.lower() in ("faculty", "faculty:") or len(line) <= 2 or re.match(r"^\^?\s*TOP$", line):
        return "skip", None
    if STOP_RE.match(line):
        return "stop", None
    if line in NAV_TABS:
        return "skip", None            # catalog page tabs: About | Academic Rules | Courses ...
    if re.fullmatch(r"(Language |Graduate |Undergraduate )?Program (Directors?|Administrators?|Coordinators?)", line):
        return "rank", line            # role group inside a department, not a unit
    if re.fullmatch(r"(Senior |Associate |Assistant |Vice )?Deans?:?|Administrative and Professional( Faculty| Staff)?", line):
        return "rank", line
    bare = PAREN_RE.sub("", line).strip()
    if "," not in line and bare != line and NAME_RE.match(bare):
        return "entry", [line]         # "Jae K. Lee (Courtesy Appointment)"
    if re.match(r"^vacant\b", line, re.I) or re.search(r"Search in Progress", line, re.I) \
            or len(line.split()) > 30 or re.search(r"\d{3}[.\-]\d{3}[.\-]\d{4}|e-?mail|@", line, re.I) \
            or line.rstrip().endswith("("):
        return "skip", None            # vacancies, open searches, explanatory paragraphs
    if "," in line:
        segs = split_segments(line)
        nxt = segs[1] if len(segs) > 1 else ""
        if is_degree(nxt) or nxt in SUFFIXES or (len(segs) > 2 and is_degree(segs[2])):
            return "entry", segs
        if len(line.split()) >= 10 and not TITLE_WORDS.search(line):
            return "skip", None        # prose sentences (e.g. Darden Executive Education blurb)
        # group heading with a note: "Affiliated Faculty, The Department of Architectural History, ..."
        if re.match(r"^(Affiliated|Adjunct|Visiting|Joint|Secondary|General|Research|Retired)\s+"
                    r"(Faculty|Professors|Lecturers)$", segs[0]):
            return "rank", line
    elif len(line.split()) >= 14:
        return "skip", None
    # department headings, including named ones and ones with commas:
    # "Corcoran Department of History", "Department of Curriculum, Instruction, and Special Education"
    if DEPT_ANY_RE.search(line) and len(line.split()) <= 20 and \
            not any(is_degree(x) for x in line.split(",")[1:]):
        first = line.split(",")[0]
        if not NAME_RE.match(first) or DEPT_ANY_RE.search(first):
            return "unit", line
    # person with no degrees listed: "Julian V. Noble, Professor Emeritus of Physics"
    if "," in line:
        segs = split_segments(line)
        name = re.sub(r"^(Judge|Justice|Dr\.)\s+", "", segs[0])
        if NAME_RE.match(name) and (len(segs) == 1 or TITLE_WORDS.search(", ".join(segs[1:]))):
            return "entry", segs           # "Julian V. Noble, Professor Emeritus"; "Barbara E. Kurth,"
        # program name with commas: "Religion, Culture, and Social Theory"
        if not NAME_RE.match(segs[0]) and not TITLE_WORDS.search(line) and len(line.split()) <= 10 \
                and not re.search(r"\d", line):
            return "program", line
    words = line.split()
    if "," not in line and UNIT_RE.match(line) and len(words) <= 20:
        return "unit", line
    if re.fullmatch(r"(Research|Researh|Chairs?|Department Chairs?)", line):   # "Researh": Record typo
        return "rank", line
    if len(words) <= 10 and "," not in line:
        if RANK_WORDS.search(line):
            return "rank", line
        if len(words) <= 6:
            return "program", line
    return "unparsed", line


HONORIFIC_RE = re.compile(r"^(Judge|Justice|The Honorable|Hon\.|Dr\.|Professor|Mr\.|Ms\.|Mrs\.)\s+")


def parse_entry(segs):
    name, i = segs[0], 1
    note = ""
    pm = re.search(r"\s*\(([^)]*(Appointment|Courtesy|Joint|Emerit)[^)]*)\)\s*$", name)
    if pm:
        note, name = pm.group(1), name[:pm.start()]
    honorific = ""
    m = HONORIFIC_RE.match(name)
    if m:
        honorific, name = m.group(1), name[m.end():]
    if i < len(segs) and segs[i] in SUFFIXES:
        name, i = f"{name}, {segs[i]}", i + 1
    degrees = []
    while i < len(segs) and is_degree(segs[i]):
        seg = re.sub(r"^and\s+", "", segs[i])
        toks = PAREN_RE.sub("", seg).split()
        whole = SPELLED_DEGREE_RE.match(seg) or "(" in seg or not all(_is_degree_token(t) for t in toks)
        degrees.extend([seg] if whole else seg.split())
        i += 1
    rest = [s for s in segs[i:] if s]
    # a degree stuck to the title by a missing comma: "Ph.D. Paul M. Hammaker Professor ..."
    if rest:
        toks = rest[0].split()
        while len(toks) > 1 and _is_degree_token(toks[0]):
            deg = toks.pop(0)
            if toks and toks[0].startswith("("):       # "M.A. (Oxon.) White Burkett ..."
                deg += " " + toks.pop(0)
            degrees.append(deg)
        rest[0] = " ".join(toks)
    if honorific:
        rest = [honorific] + rest
    if note:
        rest = rest + [note]
    return name, degrees, ", ".join(rest)


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
    if h and re.fullmatch(r"(Senior |Associate |Assistant |Vice )?Deans?:?", h):
        return "administration"
    if h and h.startswith("Administrative and Professional"):
        return "administrative and professional"
    if h and re.fullmatch(r"(Language |Graduate |Undergraduate )?Program (Directors?|Administrators?|Coordinators?)", h):
        return "program administration"
    if unit and re.match(r"Office of the (Dean|Provost)", unit, re.I):
        t.append("administration")
    if not h:
        return "; ".join(t) or "unclassified"
    for word, label in [(r"Emerit", "emeritus"), (r"Visiting", "visiting"), (r"Adjunct", "adjunct"),
                        (r"Research|Researh", "research"), (r"Clinical", "clinical"),
                        (r"General Faculty", "general faculty"), (r"Lecturer", "lecturer"),
                        (r"Instructor", "instructor"), (r"Fellow", "fellow"), (r"Librarian", "librarian"),
                        (r"Honorary", "honorary"), (r"Retired", "retired"),
                        (r"Affiliated", "affiliated"), (r"Secondary", "secondary appointment"),
                        (r"Postdoctoral", "postdoctoral"), (r"in Residence", "in residence"),
                        (r"Coordinator", "coordinator"), (r"Joint Appointment", "joint appointment"),
                        (r"Courtesy", "courtesy"), (r"of[- ]Practice", "professor of practice")]:
        if re.search(word, h, re.I):
            t.append(label)
    return "; ".join(t) or "regular"


def parse_lines(lines, meta):
    listings, headings = [], []
    unit = program = rank_h = ""
    kinds = [classify(l)[0] for l in lines]
    last_kind = ""
    for idx, line in enumerate(lines):
        if idx > 0 and kinds[idx - 1] not in ("skip",):
            last_kind = kinds[idx - 1] if not listings or listings[-1]["raw_line"] != lines[idx - 1] \
                else "entry"
        kind, payload = classify(line)
        chair_line = re.match(r"^(?:[A-Z][\w.'’\-]*\s+){2,}(?:Jr\.?\s+|Sr\.?\s+)?(?:Distinguished\s+)?"
                              r"(?:Professor|Chair) (?:of|in)\b", line)
        if kind in ("unparsed", "rank") and listings and kinds[idx - 1] == "entry" and \
                TITLE_WORDS.search(line) and (kind == "unparsed" or chair_line):
            listings[-1]["title"] = ", ".join(x for x in (listings[-1]["title"], line) if x)
            listings[-1]["raw_line"] += " / " + line
            continue
        # degrees continued on their own line: "Diplôme d’études supérieures, Ph.D. ."
        segs_c = [x.strip(" .") for x in line.split(",") if x.strip(" .")]
        if kind in ("program", "unparsed") and listings and last_kind == "entry" and segs_c and \
                all(is_degree(x) for x in segs_c):
            listings[-1]["degrees"] = "; ".join(x for x in (listings[-1]["degrees"], "; ".join(segs_c)) if x)
            listings[-1]["raw_line"] += " / " + line
            continue
        # a name followed by any description inside a faculty group is a person:
        # "Jordi Nebot, Architect, Polytechnic University of Catalonia"; "Judith Shatin, William R. Kenan, Jr."
        if kind == "unparsed" and rank_h and "," in line and segs_c and NAME_RE.match(segs_c[0]):
            kind, payload = "entry", split_segments(line)
        initial = re.search(r"\s[A-Z]\.\s", line)
        prev_entry = last_kind in ("entry", "rank")
        if kind == "program" and (rank_h or initial) and NAME_RE.match(line) and not ORG_WORDS.search(line):
            nxt = next((k for k in kinds[idx + 1:] if k not in ("skip",)), "")
            if nxt != "rank" or initial or prev_entry:
                kind, payload = "entry", [line]
        if kind == "stop":
            break
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
            # a rank group right after the Dean's office starts the school's faculty list
            # (schools without departments, e.g. Darden, Law); it is not part of the office
            if re.match(r"Office of the (Dean|Provost)", unit, re.I):
                unit, program = "", ""
        elif kind == "entry":
            name, degrees, title = parse_entry(payload)
            rank, src = rank_from_heading(rank_h), "heading"
            if not rank:
                m = TITLE_RANK_RE.search(title)
                rank, src = (rank_from_heading(m.group(1)), "title") if m else ("", "none")
            ftype = type_from_heading(rank_h, unit)
            for word, label in ((r"\bAdjunct\b", "adjunct"), (r"\bEmerit", "emeritus"), (r"Courtesy", "courtesy")):
                if re.search(word, title) and label not in ftype:
                    ftype = ftype.replace("regular", label) if ftype == "regular" else f"{ftype}; {label}"
            listings.append({**meta, "department": unit, "program": program, "name": name,
                             "rank": rank, "rank_source": src, "rank_heading": rank_h,
                             "faculty_type": ftype, "title": title,
                             "degrees": "; ".join(degrees), "raw_line": line})
    return listings, headings


def collapse(listings):
    """One row per person per school-year."""
    people = OrderedDict()
    for l in listings:
        key = (l["year"], l["school"], re.sub(r"\s+", " ", l["name"]).strip().lower())
        p = people.setdefault(key, {k: l[k] for k in ("year", "school", "name")}
                              | {"records": [], "source_urls": [], "departments": [], "ranks": [],
                                 "types": [], "titles": [], "n_listings": 0})
        p["n_listings"] += 1
        for f, v in (("records", l["record"]), ("source_urls", l["source_url"])):
            if v not in p[f]:
                p[f].append(v)
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
        rows.append({"year": p["year"], "records": "; ".join(p["records"]), "school": p["school"],
                     "department": " | ".join(d for d in p["departments"] if not d.startswith("Office of")),
                     "name": p["name"], "rank": best, "faculty_type": "; ".join(dict.fromkeys(types)),
                     "titles": " | ".join(p["titles"]), "n_listings": p["n_listings"],
                     "flag": "admin office only; may be staff" if admin_only else
                             ("no rank found" if not best else ""),
                     "source_url": " ; ".join(p["source_urls"])})
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


def fetch(url, cache_dir=OUT / "cache"):
    import hashlib
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".html")
    if path.exists() and path.stat().st_size > 1000:
        return path.read_bytes()
    time.sleep(2)
    html = _get_requests(url)
    if len(html) < 1000 or b"verify that you're not a robot" in html:
        raise ValueError("site returned a bot-check page instead of the Record; save the page "
                         "from your browser instead (see --folder)")
    path.write_bytes(html)
    return html


# catoid -> (start year, Record) for Records that list faculty (from ureg_catalogs.csv)
CATALOGS = {2: (2005, "Graduate"), 9: (2006, "Graduate"), 12: (2007, "Graduate"), 18: (2008, "Graduate"),
            23: (2009, "Graduate"), 26: (2010, "Graduate"), 28: (2011, "Graduate"), 30: (2012, "Graduate"),
            34: (2013, "Graduate"), 38: (2014, "Graduate"), 40: (2015, "Graduate"),
            1: (2005, "Undergraduate"), 7: (2006, "Undergraduate"), 11: (2007, "Undergraduate"),
            17: (2008, "Undergraduate"), 22: (2009, "Undergraduate"), 25: (2010, "Undergraduate"),
            27: (2011, "Undergraduate"), 31: (2012, "Undergraduate"), 33: (2013, "Undergraduate"),
            37: (2014, "Undergraduate"), 39: (2015, "Undergraduate")}

# name in Record -> canonical school (see school_names.csv); checked in order
SCHOOL_RULES = [("Arts", "Arts & Sciences"), ("Darden", "Darden"), ("Batten", "Batten"),
                ("Commerce", "Commerce"), ("Education", "Education"), ("Engineering", "Engineering"),
                ("Architecture", "Architecture"), ("Law", "Law"), ("Medicine", "Medicine"),
                ("Nursing", "Nursing"), ("Continuing", "Continuing & Professional Studies"),
                ("Data Science", "Data Science")]


def school_from_page(html):
    soup = BeautifulSoup(html, "lxml")
    for el in [*soup.find_all(["h1", "h2"]), soup.title]:
        t = el.get_text(" ", strip=True) if el else ""
        for key, school in SCHOOL_RULES:
            if key.lower() in t.lower():
                return school, t
    return "UNKNOWN", ""


def folder_jobs(folder):
    jobs = []
    for f in sorted(folder.glob("ureg_c*.html")):
        m = re.match(r"ureg_c(\d+)_e(\w+?)(?:_n(\d+))?\.html$", f.name)
        if not m:
            print(f"  skipping {f.name}: file name not in ureg_c<catoid>_e<ent_oid>[_n<navoid>] form")
            continue
        cat, ent, nav = int(m.group(1)), m.group(2), m.group(3)
        if cat not in CATALOGS:
            print(f"  skipping {f.name}: catoid {cat} is not a Record with faculty listings")
            continue
        year, record = CATALOGS[cat]
        school, heading = school_from_page(f.read_bytes())
        if school == "UNKNOWN":
            print(f"  {f.name}: school not recognized from page heading; listed as UNKNOWN")
        if ent.isdigit() and ent != "0":
            url = f"https://records.ureg.virginia.edu/preview_entity.php?catoid={cat}&ent_oid={ent}"
        else:
            url = f"https://records.ureg.virginia.edu/content.php?catoid={cat}&navoid={nav}"
        jobs.append({"year": year, "record": record, "school": school, "file": str(f), "url": url})
    print(f"{len(jobs)} saved pages found in {folder}")
    return jobs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--html")
    src.add_argument("--text")
    src.add_argument("--manifest")
    src.add_argument("--folder", help="folder of pages saved with the save-page bookmarklet")
    ap.add_argument("--year", type=int)
    ap.add_argument("--record", default="Graduate")
    ap.add_argument("--school")
    ap.add_argument("--url", default="")
    ap.add_argument("--prefix", default="layout_a", help="output file prefix, e.g. layout_b")
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    jobs = []
    if a.folder:
        jobs = folder_jobs(Path(a.folder))
    elif a.manifest:
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

    write(OUT / f"{a.prefix}_listings.csv", all_listings)
    write(OUT / f"{a.prefix}_roster.csv", collapse(all_listings))
    write(OUT / f"{a.prefix}_headings.csv", all_headings)


if __name__ == "__main__":
    main()
