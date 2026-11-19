#!/usr/bin/env python3
"""
wayback_law_scraper.py
Scrape archived UVA School of Law faculty rosters AND profile pages from the Wayback Machine.

Output fields (person-year): name, rank, website link, bio, research interests, keywords,
dept address, office number, identifiers found on the page (ORCID, Google Scholar, SSRN),
plus provenance and missingness codes.

Workflow (run per year, or pass several --year flags):
  1. discover  -> faculty links on the archived homepage + archive-index search near the target date
  2. (you)     -> paste the roster URL for each year into CONFIG below
  3. fetch     -> download roster, pagination and every linked profile page (cached on disk)
  4. inspect   -> print repeated HTML classes on roster and a sample profile, to choose CSS selectors
  5. parse     -> person-year CSV per year, people.csv across years, and changelog.csv
  6. llm-prep  -> cleaned HTML for roster/profile pages + an extraction prompt, for LLM extraction

Example:
  python wayback_law_scraper.py discover --year 2005 --year 2015 --year 2025
  python wayback_law_scraper.py fetch    --year 2005
  python wayback_law_scraper.py inspect  --year 2005
  python wayback_law_scraper.py parse    --year 2005 --year 2015 --year 2025
  python wayback_law_scraper.py llm-prep --year 2025

Missingness codes used in output cells:
  not_archived    the profile page has no snapshot within the profile window
  not_present     the page was archived but the field was not found on it
  not_applicable  the field could not exist that year (ORCID before 2012, Google Scholar profiles before 2011)

Requires: pip install requests beautifulsoup4 lxml
"""
import argparse
import csv
import hashlib
import json
import re
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------
CDX = "https://web.archive.org/cdx/search/cdx"
RAW = "https://web.archive.org/web/{ts}id_/{url}"   # id_ = original HTML, no Wayback toolbar
DOMAIN = "law.virginia.edu"
HOMEPAGE = "https://www.law.virginia.edu/"   # stable 2005-2025; roster paths beneath it may not be
OUT = Path("wayback_law")
CACHE = OUT / "cache"
DELAY = 2.0                  # seconds between requests (be polite to the Archive)
ROSTER_WINDOW_DAYS = 60      # roster snapshots within +/- this of the target
PROFILE_WINDOW_DAYS = 180    # profiles are archived less often, so allow a wider window
USER_AGENT = "UVA-faculty-research/0.2 (contact: annayao0602@gmail.com)"

ORCID_START, SCHOLAR_START = 2012, 2011   # earlier years -> not_applicable

NA, NOT_ARCHIVED, NOT_PRESENT = "not_applicable", "not_archived", "not_present"

# Fill these in after `discover` and `inspect`.
#   roster_url        main faculty listing page
#   pagination_regex  regex matching links to further roster pages (or None)
#   profile_regex     regex matching individual faculty profile links (REQUIRED for profiles)
#   item / name / title  CSS selectors on the roster (item=None -> profile-link heuristic)
#   dept_address      school-level address, typed once by hand if not on profiles (or None)
#   profile           CSS selectors on a profile page; None = use regex fallback or mark not_present
def _blank(target):
    return {"target": target, "roster_url": None, "pagination_regex": None, "profile_regex": None,
            "item": None, "name": None, "title": None, "dept_address": None,
            "profile": {"name": None, "rank": None, "bio": None, "interests": None,
                        "keywords": None, "office": None, "address": None}}

CONFIG = {2005: _blank("20050815"), 2015: _blank("20150815"), 2025: _blank("20250815")}

# Roster pages chosen by hand from discover output (see PROGRESS.md change log).
# 2005: the site is a frameset; the faculty-only list is the inner frame (Fr1), so that frame
#       is the roster. Seen at snapshot 20051114031816 (closest capture found by hand).
CONFIG[2005]["roster_url"] = "http://www.law.virginia.edu/lawweb/faculty.nsf/FDBN?OpenView&ExpandView"   # Faculty Directory By Name, all sections; found at snapshot 20060829194319
CONFIG[2005]["profile_window_days"] = 400
CONFIG[2005]["roster_window_days"] = 400      # FDBN may be archived only in 2006; closest capture is used and its distance logged
# 2015: Lotus Notes view of the faculty directory, snapshot 20150905163133
CONFIG[2015]["roster_url"] = "http://www.law.virginia.edu/html/faculty/resident.htm"   # resident faculty list; snapshot 20150905083840

# Profile links (from inspect): Lotus Notes document links
CONFIG[2005]["profile_regex"] = r"/lawweb/faculty\.nsf/[0-9a-fA-F]{32}/[0-9a-fA-F]{32}\?OpenDocument$"
CONFIG[2005]["pagination_regex"] = r"/faculty\.nsf/F?DBN\?OpenView&Start=[\d.]+$"   # next pages of a long view
CONFIG[2015]["profile_regex"] = r"/lawweb/[Ff]aculty\.nsf/FHPbI/\d+$"   # faculty home page by ID (resident.htm links)

# 2025: Drupal site. Faculty directory filtered by type; Resident Faculty = faculty_type=521
# (others: 6506 Visiting, 6501 Other Disciplines, 11676 Other Scholars, 171 Adjunct, 6476 Emeritus).
# One page, 103 profile links, closest capture 20250524222938 (83 days before target; none closer within 120).
CONFIG[2025]["roster_url"] = "https://www.law.virginia.edu/faculty/directory/faculty?faculty_type=521"
CONFIG[2025]["roster_window_days"] = 120
# /faculty/profile/<computing id>/<number>; the number is the old FHPbI ID (e.g. Deeks 2378410 in 2015 and 2025)
CONFIG[2025]["profile_regex"] = r"/faculty/profile/[a-z0-9]+/\d+$"
CONFIG[2025]["extractor"] = "law_drupal_2025"           # 2025 Drupal profiles: standard field classes
# Roster cards carry name, titles and room, used when a profile isn't archived (3 of 103).
CONFIG[2025]["item"] = "div.view-faculty-staff div.views-row"
CONFIG[2025]["name"] = ".field--name-title"
CONFIG[2025]["title"] = ".field--name-field-position-title .field__item"
CONFIG[2025]["roster_office"] = ".field--name-field-office"

# Profile extractors (structure-based; CSS selectors alone can't reach these fields).
# Name of a function in EXTRACTORS; it overrides the CSS selectors in CONFIG[year]["profile"].
CONFIG[2005]["extractor"] = "law_notes_directory_card"   # 2005-06 directory cards: name, title, Office:
CONFIG[2015]["extractor"] = "law_fhp_2015"               # 2015 faculty home pages: sidebar + body

FIELDS = ["year", "name", "rank", "website_link", "bio", "research_interests", "keywords",
          "dept_address", "office_number", "orcid", "google_scholar", "ssrn",
          "roster_source_url", "roster_snapshot_ts", "profile_snapshot_ts",
          "profile_days_from_target", "method"]

# ----------------------------------------------------------------------------
# HTTP helpers
# ----------------------------------------------------------------------------
_last_request = 0.0


def make_session():
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    retry = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def get(session, url, params=None, timeout=90):
    global _last_request
    wait = DELAY - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)
    r = session.get(url, params=params, timeout=timeout)
    _last_request = time.time()
    r.raise_for_status()
    return r


# ----------------------------------------------------------------------------
# Date helpers
# ----------------------------------------------------------------------------
def window(target, days):
    t = datetime.strptime(target, "%Y%m%d")
    d = timedelta(days=days)
    return (t - d).strftime("%Y%m%d"), (t + d).strftime("%Y%m%d")


def days_off(ts, target):
    return abs((datetime.strptime(ts[:8], "%Y%m%d") - datetime.strptime(target, "%Y%m%d")).days)


# ----------------------------------------------------------------------------
# Wayback CDX + raw fetch
# ----------------------------------------------------------------------------
def cdx(session, url, start, end, match_type="exact", extra_filters=(), limit=None, timeout=90):
    params = [("url", url), ("matchType", match_type), ("from", start), ("to", end),
              ("output", "json"), ("filter", "statuscode:200"),
              ("filter", "mimetype:text/html"), ("fl", "timestamp,original,length")]
    params += [("filter", f) for f in extra_filters]
    if limit:
        params += [("collapse", "urlkey"), ("limit", str(limit))]
    r = get(session, CDX, params=params, timeout=timeout)
    if not r.text.strip():
        return []
    rows = r.json()
    if len(rows) < 2:
        return []
    header, data = rows[0], rows[1:]
    return [dict(zip(header, row)) for row in data]


def closest_capture(session, url, target, days=ROSTER_WINDOW_DAYS):
    start, end = window(target, days)
    caps = cdx(session, url, start, end)
    return min(caps, key=lambda c: days_off(c["timestamp"], target)) if caps else None


def fetch_raw(session, ts, url):
    key = hashlib.sha1(f"{ts}|{url}".encode()).hexdigest()[:16]
    path = CACHE / f"{ts}_{key}.html"
    if not path.exists():
        r = get(session, RAW.format(ts=ts, url=url))
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
    return path.read_bytes(), path


def txt(el):
    return el.get_text(" ", strip=True) if el else ""


def soup_of(path):
    return BeautifulSoup(Path(path).read_bytes(), "lxml")


# ----------------------------------------------------------------------------
# discover
# ----------------------------------------------------------------------------
def discover(session, year):
    """Homepage links first (fast); then a broader archive-index search, which can be slow."""
    homepage_links(session, year)
    cfg = CONFIG[year]
    start, end = window(cfg["target"], ROSTER_WINDOW_DAYS)
    print(f"[{year}] searching the archive index for faculty pages "
          f"(can take a few minutes; Ctrl+C skips it, homepage results are already saved)...", flush=True)
    try:
        caps = cdx(session, "www." + DOMAIN + "/", start, end, match_type="prefix",
                   extra_filters=["original:.*[Ff]aculty.*"], limit=2000, timeout=180)
    except (requests.RequestException, KeyboardInterrupt) as e:
        print(f"[{year}] index search skipped ({type(e).__name__}); use homepage_links_{year}.csv")
        return
    best = {}
    for c in caps:
        key = c["original"].split("://", 1)[-1].lower().replace("www.", "").rstrip("/")
        if key not in best or days_off(c["timestamp"], cfg["target"]) < days_off(best[key]["timestamp"], cfg["target"]):
            best[key] = c
    OUT.mkdir(exist_ok=True)
    path = OUT / f"discover_{year}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["original", "timestamp", "days_from_target", "length", "view_in_browser"])
        for c in sorted(best.values(), key=lambda c: c["original"]):
            w.writerow([c["original"], c["timestamp"], days_off(c["timestamp"], cfg["target"]),
                        c["length"], f"https://web.archive.org/web/{c['timestamp']}/{c['original']}"])
    print(f"[{year}] {len(best)} candidate faculty URLs -> {path}")


def resolve_redirect(session, url, target, year):
    """The homepage capture may be a redirect (301/302) rather than a page. List every capture
    near the target with its status, then let the Wayback Machine follow the closest redirect
    to the archived page it points to, and return that page's timestamp and URL."""
    start, end = window(target, ROSTER_WINDOW_DAYS)
    params = [("url", url), ("from", start), ("to", end), ("output", "json"),
              ("fl", "timestamp,original,statuscode,mimetype")]
    r = get(session, CDX, params=params)
    rows = r.json() if r.text.strip() else []
    caps = [dict(zip(rows[0], x)) for x in rows[1:]] if len(rows) > 1 else []
    OUT.mkdir(exist_ok=True)
    with open(OUT / f"homepage_captures_{year}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "original", "statuscode", "mimetype"])
        for c in caps:
            w.writerow([c["timestamp"], c["original"], c["statuscode"], c["mimetype"]])
    redirects = [c for c in caps if c["statuscode"].startswith("3")]
    revisits = [c for c in caps if c["statuscode"] in ("-", "200")]
    print(f"[{year}] homepage: {len(caps)} captures in window, {len(redirects)} redirects, "
          f"{len(revisits)} revisit/unchanged copies", flush=True)
    if not redirects and revisits:
        # "revisit" records point to an identical earlier copy; the Wayback Machine serves them normally
        c = min(revisits, key=lambda c: days_off(c["timestamp"], target))
        print(f"[{year}] using revisit capture {c['timestamp']}")
        return {"timestamp": c["timestamp"], "original": c["original"]}
    if not redirects:
        return None
    c = min(redirects, key=lambda c: days_off(c["timestamp"], target))
    # no id_ here: the Wayback Machine follows the redirect to the archived target page
    r = get(session, f"https://web.archive.org/web/{c['timestamp']}/{c['original']}")
    m = re.match(r"https?://web\.archive\.org/web/(\d{14})[a-z_]*/(.+)$", r.url)
    if not m:
        print(f"[{year}] could not follow the homepage redirect (ended at {r.url})")
        return None
    print(f"[{year}] homepage redirects to {m.group(2)} (snapshot {m.group(1)})")
    return {"timestamp": m.group(1), "original": m.group(2)}


def homepage_links(session, year):
    """List faculty-related links on the archived homepage closest to the target date."""
    cfg = CONFIG[year]
    print(f"[{year}] finding the archived homepage closest to {cfg['target']}...", flush=True)
    cap = closest_capture(session, HOMEPAGE, cfg["target"])
    if not cap:
        cap = resolve_redirect(session, HOMEPAGE, cfg["target"], year)
    if not cap:
        print(f"[{year}] no homepage capture within +/-{ROSTER_WINDOW_DAYS} days "
              f"(see homepage_captures_{year}.csv for what the Archive has)")
        return
    html, _ = fetch_raw(session, cap["timestamp"], cap["original"])
    soup = BeautifulSoup(html, "lxml")
    links = {}
    for a in soup.find_all("a", href=True):
        link = urldefrag(urljoin(cap["original"], a["href"]))[0]
        if DOMAIN in urlparse(link).netloc and re.search(r"faculty", link + " " + txt(a), re.I):
            links.setdefault(link, txt(a))
    path = OUT / f"homepage_links_{year}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["homepage_snapshot", "link_text", "url", "view_in_browser"])
        for link, text in sorted(links.items()):
            w.writerow([cap["timestamp"], text, link,
                        f"https://web.archive.org/web/{cap['timestamp']}/{link}"])
    print(f"[{year}] homepage {cap['timestamp']}: {len(links)} faculty links -> {path}")


# ----------------------------------------------------------------------------
# fetch
# ----------------------------------------------------------------------------
NOTES_DOC_RE = re.compile(r"^(https?://[^/]+/.+?\.nsf)/[^/]+/([0-9a-fA-F]{32})\?OpenDocument$", re.I)
_notes_index = {}


def notes_fallback(session, url, target, days):
    """Lotus Notes serves one document under any view: /db.nsf/<viewA>/<doc> and /db.nsf/<viewB>/<doc>
    are the same page. The Archive often captured a profile under a different view than the one the
    roster links to. Index every archived page in the database once (by document ID) and look the
    profile up there."""
    m = NOTES_DOC_RE.match(url)
    if not m:
        return None
    db, doc = m.group(1), m.group(2).lower()
    key = (db.lower(), target, days)
    if key not in _notes_index:
        start, end = window(target, days)
        prefix = re.sub(r"^https?://", "", db) + "/"
        print(f"  building document index for {prefix} (one-time, may take a minute)...", flush=True)
        index = {}
        try:
            caps = cdx(session, prefix, start, end, match_type="prefix", limit=50000, timeout=240)
        except requests.RequestException as e:
            print(f"  document index failed ({type(e).__name__}); profiles stay not_archived")
            caps = []
        for c in caps:
            mm = re.search(r"/([0-9a-fA-F]{32})\?OpenDocument", c["original"], re.I)
            if mm:
                d = mm.group(1).lower()
                if d not in index or days_off(c["timestamp"], target) < days_off(index[d]["timestamp"], target):
                    index[d] = c
        _notes_index[key] = index
        print(f"  document index: {len(index)} archived documents", flush=True)
    return _notes_index[key].get(doc)


def fetch_year(session, year, profiles=True):
    cfg = CONFIG[year]
    if not cfg["roster_url"]:
        raise SystemExit(f"Set CONFIG[{year}]['roster_url'] first (run discover).")
    if profiles and not cfg["profile_regex"]:
        print(f"[{year}] WARNING: profile_regex not set; only roster pages will be fetched. "
              f"Run inspect on the roster, set profile_regex, then fetch again (cache is reused).")
    queue, seen, pages = [("roster", cfg["roster_url"])], set(), []
    while queue:
        role, url = queue.pop(0)
        url = urldefrag(url)[0]
        if url in seen:
            continue
        seen.add(url)
        days = cfg.get("profile_window_days", PROFILE_WINDOW_DAYS) if role == "profile" \
            else cfg.get("roster_window_days", ROSTER_WINDOW_DAYS)
        cap = closest_capture(session, url, cfg["target"], days)
        if not cap and re.search(r"/[Ff]aculty\.nsf/", url):
            # the Archive treats Faculty.nsf and faculty.nsf as different addresses; try the other case
            alt = re.sub(r"/([Ff])aculty\.nsf/", lambda m: "/" + ("f" if m.group(1) == "F" else "F") + "aculty.nsf/", url)
            cap = closest_capture(session, alt, cfg["target"], days)
        if not cap and role == "profile":
            cap = notes_fallback(session, url, cfg["target"], days)
        if not cap:
            pages.append({"role": role, "url": url, "status": NOT_ARCHIVED})
            print(f"  [{role}] NOT ARCHIVED {url}")
            continue
        html, path = fetch_raw(session, cap["timestamp"], cap["original"])
        pages.append({"role": role, "url": url, "timestamp": cap["timestamp"],
                      "days_from_target": days_off(cap["timestamp"], cfg["target"]),
                      "cache_file": str(path), "status": "ok"})
        print(f"  [{role}] {cap['timestamp']} {url}")
        if role in ("roster", "pagination"):
            soup = BeautifulSoup(html, "lxml")
            # framesets (common on 2000s sites): the content is in the frames, so fetch those too
            for fr in soup.find_all(["frame", "iframe"], src=True):
                link = urldefrag(urljoin(cap["original"], fr["src"]))[0]
                if DOMAIN in urlparse(link).netloc and not re.search(r"news|banner|header|footer|menu|nav", link, re.I):
                    queue.append((role, link))
            for a in soup.find_all("a", href=True):
                link = urldefrag(urljoin(cap["original"], a["href"]))[0]
                if DOMAIN not in urlparse(link).netloc:
                    continue
                if cfg["pagination_regex"] and re.search(cfg["pagination_regex"], link):
                    queue.append(("pagination", link))
                elif profiles and cfg["profile_regex"] and re.search(cfg["profile_regex"], link):
                    queue.append(("profile", link))
    if profiles and cfg["profile_regex"] and not any(p["role"] == "profile" for p in pages):
        print(f"[{year}] WARNING: no links on the roster match profile_regex; run inspect and "
              f"check the most common link patterns")
    path = OUT / f"manifest_{year}.json"
    path.write_text(json.dumps(pages, indent=2))
    for role in ("roster", "pagination", "profile"):
        got = [p for p in pages if p["role"] == role]
        if got:
            ok = sum(p["status"] == "ok" for p in got)
            print(f"[{year}] {role}: {ok}/{len(got)} archived")
    print(f"[{year}] manifest -> {path}")


def load_manifest(year):
    path = OUT / f"manifest_{year}.json"
    if not path.exists():
        raise SystemExit(f"No manifest for {year}; run fetch first.")
    return json.loads(path.read_text())


# ----------------------------------------------------------------------------
# inspect
# ----------------------------------------------------------------------------
def _class_counts(soup, require_link):
    counts = Counter()
    for tag in soup.find_all(class_=True):
        if not require_link or tag.find("a", href=True):
            counts[f"{tag.name}.{'.'.join(tag['class'])}"] += 1
    return counts


def inspect_year(year):
    pages = [p for p in load_manifest(year) if p["status"] == "ok"]
    roster = [p for p in pages if p["role"] == "roster"]
    profiles = [p for p in pages if p["role"] == "profile"]
    for p in roster:
        soup = soup_of(p["cache_file"])
        print(f"\n[{year}] ROSTER {p['url']}  (visible text: {len(soup.get_text(strip=True))} chars)")
        for sel, n in _class_counts(soup, True).most_common(15):
            print(f"   {n:4d}  {sel}")
        # link patterns: IDs and long hex strings collapsed, so profile links group together
        pats, examples = Counter(), {}
        for a in soup.find_all("a", href=True):
            link = urljoin(p["url"], a["href"])
            if DOMAIN not in urlparse(link).netloc:
                continue
            pat = re.sub(r"[0-9A-Fa-f]{16,}", "<ID>", urlparse(link).path + ("?" + urlparse(link).query if urlparse(link).query else ""))
            pat = re.sub(r"\d+", "<N>", pat)
            pats[pat] += 1
            examples.setdefault(pat, (txt(a)[:40], link))
        print("   most common link patterns (count | pattern | example text | example link):")
        for pat, n in pats.most_common(8):
            print(f"   {n:4d} | {pat[:70]} | {examples[pat][0]} | {examples[pat][1][:110]}")
        lines = [l for l in soup.get_text("\n", strip=True).splitlines() if l.strip()]
        print("   first text lines:", " / ".join(lines[:25])[:600])
    if profiles:
        p = profiles[0]
        soup = soup_of(p["cache_file"])
        print(f"\n[{year}] SAMPLE PROFILE {p['url']}  (visible text: {len(soup.get_text(strip=True))} chars)")
        for sel, n in _class_counts(soup, False).most_common(25):
            print(f"   {n:4d}  {sel}")
        heads = [txt(h) for h in soup.find_all(["h1", "h2", "h3", "h4", "strong", "dt"]) if txt(h)]
        print("   headings/labels:", " | ".join(heads[:20]))
    elif not roster:
        print(f"[{year}] nothing to inspect")


# ----------------------------------------------------------------------------
# parse
# ----------------------------------------------------------------------------
OFFICE_RE = re.compile(r"\b(?:Office|Room|Rm\.?)\s*[:#]?\s*([A-Z]{0,3}\s?\d{1,4}[A-Z]?)", re.I)
ORCID_RE = re.compile(r"orcid\.org/(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", re.I)
SCHOLAR_RE = re.compile(r"scholar\.google\.[a-z.]+/citations\?[^\"'\s]*user=([\w-]+)", re.I)
SSRN_RE = re.compile(r"ssrn\.com/(?:author=|sol3/cf_dev/AbsByAuth\.cfm\?per_id=)(\d+)", re.I)


def sel_text(soup, sel, joiner=" "):
    if not sel:
        return ""
    return joiner.join(t for t in (txt(e) for e in soup.select(sel)) if t)


def roster_entries(cfg, page):
    """Yield (name, roster_title, profile_url, roster_office) from one roster page.
    With `item`: `name`/`title`/`roster_office` are CSS selectors inside each item; every element
    matching `title` is kept, joined with ' | '."""
    soup = soup_of(page["cache_file"])
    if cfg["item"]:
        for item in soup.select(cfg["item"]):
            name = _norm(txt(item.select_one(cfg["name"]))) if cfg["name"] else _norm(txt(item))
            title = " | ".join(_norm(txt(e)) for e in item.select(cfg["title"]) if txt(e)) if cfg["title"] else ""
            office = _norm(txt(item.select_one(cfg["roster_office"]))) if cfg.get("roster_office") else ""
            a = item.find("a", href=True)
            yield name, title, urldefrag(urljoin(page["url"], a["href"]))[0] if a else "", office
    elif cfg["profile_regex"]:
        for a in soup.find_all("a", href=True):
            link = urldefrag(urljoin(page["url"], a["href"]))[0]
            if re.search(cfg["profile_regex"], link) and txt(a):
                yield txt(a), "", link, ""


def _norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def title_lines(el):
    """Split a title block on <br> into separate titles, re-joining lines that were wrapped
    mid-phrase (previous line ends with 'and', 'of', ',', '&', or the next starts lowercase)."""
    if not el:
        return []
    parts = [_norm(p) for p in el.get_text("\n").split("\n")]
    out = []
    for p in (p for p in parts if p):
        if out and (re.search(r"(\band|\bof|\bthe|,|&|;)$", out[-1]) or p[0].islower()):
            out[-1] = f"{out[-1]} {p}"
        else:
            out.append(p)
    return out


def law_notes_directory_card(soup):
    """Law 2005-06 Lotus Notes directory card. Two templates (Oct 2005 and 2006) share:
    name in <font size=4>, titles in the next bold <font size=2>, then a label table whose rows are
    label | spacer | value. 'Office:' appears twice (person, then assistant): take the first."""
    name_el = soup.find("font", attrs={"size": "4"})
    titles = []
    if name_el:
        for f in name_el.find_all_next("font", attrs={"size": "2"}):
            t = _norm(f.get_text(" "))
            if not t:
                continue
            if f.find_parent("b") and not t.endswith(":"):
                titles = title_lines(f)
            break
    labels = {}
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) >= 3:
            lab = _norm(tds[0].get_text(" "))
            if lab.endswith(":"):
                labels.setdefault(lab.rstrip(":").strip(), _norm(tds[2].get_text(" ")))
    fhp = name_el.find_parent("a", href=True) if name_el else None
    return {"name": _norm(name_el.get_text(" ")) if name_el else "",
            "rank": " | ".join(titles), "office_number": labels.get("Office", ""),
            "bio": "", "research_interests": "", "keywords": "",
            "_home_page": fhp["href"] if fhp else ""}


def law_fhp_2015(soup):
    """Law 2015 faculty home page. Sidebar (div.fac_sidebar) has span.sectionhead groups
    CONTACT / ASSISTANT / SUBJECTS with span.newsspaced values; body (div.faculty_body) has
    span.titlepage name, a bold title block, italic degree lines, then the bio after the last div.line."""
    side, body = soup.select_one("div.fac_sidebar"), soup.select_one("div.faculty_body")
    sec, cur = {}, None
    for el in (side.find_all("span") if side else []):
        cls = el.get("class") or []
        if "sectionhead" in cls:
            cur = _norm(el.get_text(" "))
            sec[cur] = []
        elif cur and "newsspaced" in cls and not el.find("a", href=re.compile(r"\.pdf$", re.I)):
            sec[cur].append(_norm(el.get_text(" ")))
    contact = sec.get("CONTACT", [])
    office = contact[contact.index("Room") + 1] if "Room" in contact[:-1] else ""
    bio = ""
    if body:
        lines = body.find_all("div", class_="line")
        if len(lines) >= 2:
            bio = "\n".join(p for p in (_norm(s.get_text(" ") if hasattr(s, "get_text") else str(s))
                                        for s in lines[-1].next_siblings) if p)
    return {"name": _norm(soup.select_one("span.titlepage").get_text(" ")) if soup.select_one("span.titlepage") else "",
            "rank": " | ".join(title_lines(body.find("b"))) if body else "",
            "office_number": office, "bio": bio, "research_interests": "",
            "keywords": " | ".join(s for s in sec.get("SUBJECTS", []) if s)}


def law_drupal_2025(soup):
    """Law 2025 Drupal profile. Standard field classes: h1.page-title (name), field--name-field-position-title
    items (titles), field--name-field-office (Room), field--name-field-bio-expertise <li> items (stated
    expertise), bio = field--name-body inside the Biography tab (div[id$=-bio]); the other body fields
    on the page are footer blocks. School address from the footer contact block."""
    def items(sel):
        el = soup.select_one(sel)
        return [_norm(i.get_text(" ")) for i in el.select(".field__item")] if el else []
    exp = soup.select_one(".field--name-field-bio-expertise")
    keywords = [_norm(li.get_text(" ")) for li in exp.find_all("li")] if exp else []
    if exp and not keywords:
        keywords = items(".field--name-field-bio-expertise")
    bio_el = soup.select_one("div[id$='-bio'] .field--name-body") or soup.select_one("article .field--name-body")
    bio = ""
    if bio_el:
        paras = [_norm(p.get_text(" ")) for p in bio_el.find_all(["p", "li"])] or [_norm(bio_el.get_text(" "))]
        bio = "\n".join(p for p in paras if p)
    addr = ""
    contact = soup.select_one("#block-footer-contact-block")
    if contact:
        m = re.search(r"(\d+ [A-Z][\w ]+?(?:Road|Rd|Street|St|Avenue|Ave)\.?,? Charlottesville, VA \d{5})",
                      _norm(contact.get_text(" ")))
        addr = m.group(1) if m else ""
    name = soup.select_one("h1.page-title")
    return {"name": _norm(name.get_text(" ")) if name else "",
            "rank": " | ".join(t for t in items(".field--name-field-position-title") if t),
            "office_number": (items(".field--name-field-office") or [""])[0],
            "bio": bio, "research_interests": "", "keywords": " | ".join(k for k in keywords if k),
            "_address": addr}


EXTRACTORS = {"law_notes_directory_card": law_notes_directory_card, "law_fhp_2015": law_fhp_2015,
              "law_drupal_2025": law_drupal_2025}


def parse_profile(cfg, year, soup):
    pc = cfg["profile"]
    html = str(soup)
    if cfg.get("extractor"):
        data = EXTRACTORS[cfg["extractor"]](soup)
        def ident_(regex, start):
            m = regex.search(html)
            return m.group(1) if m else (NA if start and year < start else NOT_PRESENT)
        data.pop("_home_page", None)
        data.update({"address": data.pop("_address", ""), "orcid": ident_(ORCID_RE, ORCID_START),
                     "google_scholar": ident_(SCHOLAR_RE, SCHOLAR_START), "ssrn": ident_(SSRN_RE, None)})
        return data, cfg["extractor"]
    office = sel_text(soup, pc["office"])
    office_method = "css" if office else ""
    if not office:
        m = OFFICE_RE.search(soup.get_text(" ", strip=True))
        office, office_method = (m.group(1), "regex") if m else ("", "")

    def ident(regex, start):
        m = regex.search(html)
        if m:
            return m.group(1)
        return NA if start and year < start else NOT_PRESENT

    out = {
        "name": sel_text(soup, pc["name"]),
        "rank": sel_text(soup, pc["rank"], " | "),
        "bio": sel_text(soup, pc["bio"], "\n"),
        "research_interests": sel_text(soup, pc["interests"], " | "),
        "keywords": sel_text(soup, pc["keywords"], " | "),
        "office_number": office,
        "address": sel_text(soup, pc["address"]),
        "orcid": ident(ORCID_RE, ORCID_START),
        "google_scholar": ident(SCHOLAR_RE, SCHOLAR_START),
        "ssrn": ident(SSRN_RE, None),
    }
    return out, office_method


def parse_year(year):
    cfg = CONFIG[year]
    manifest = load_manifest(year)
    by_url = {p["url"]: p for p in manifest}
    # Loose key: the manifest may hold the Archive's spelling of a URL (":80" in the host) and the
    # roster may link one page as both Faculty.nsf and faculty.nsf.
    def url_key(u):
        return re.sub(r"^(https?://[^/]+):80(?=/|$)", r"\1", u).lower()
    by_url_ci = {url_key(p["url"]): p for p in manifest}
    roster_pages = [p for p in manifest if p["status"] == "ok" and p["role"] in ("roster", "pagination")]
    rows, seen, empty_flag = [], set(), False
    roster_method = "css" if cfg["item"] else ("profile-link heuristic" if cfg["profile_regex"] else "none")

    for rp in roster_pages:
        if len(soup_of(rp["cache_file"]).get_text(strip=True)) < 500:
            empty_flag = True  # likely a JS-rendered shell
        for name, roster_title, link, roster_office in roster_entries(cfg, rp):
            key = re.sub(r"\s+", " ", name).strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            row = {"year": year, "name": name, "website_link": link or NOT_PRESENT,
                   "roster_source_url": rp["url"], "roster_snapshot_ts": rp["timestamp"]}
            prof = by_url.get(link) or by_url_ci.get(url_key(link))
            if prof and prof["status"] == "ok":
                data, office_method = parse_profile(cfg, year, soup_of(prof["cache_file"]))
                rank = data["rank"] or roster_title
                row.update({
                    "name": data["name"] or name,
                    "rank": rank or NOT_PRESENT,
                    "bio": data["bio"] or NOT_PRESENT,
                    "research_interests": data["research_interests"] or NOT_PRESENT,
                    "keywords": data["keywords"] or NOT_PRESENT,
                    "dept_address": data["address"] or cfg["dept_address"] or NOT_PRESENT,
                    "office_number": data["office_number"] or NOT_PRESENT,
                    "orcid": data["orcid"], "google_scholar": data["google_scholar"], "ssrn": data["ssrn"],
                    "profile_snapshot_ts": prof["timestamp"],
                    "profile_days_from_target": prof["days_from_target"],
                    "method": f"roster:{roster_method}; profile:{office_method}" if cfg.get("extractor") else
                              f"roster:{roster_method}; profile:css" +
                              (f"; office:{office_method}" if office_method else ""),
                })
            else:
                code = NOT_ARCHIVED if prof else NOT_PRESENT  # no profile link at all -> not_present
                row.update({
                    "rank": roster_title or code,
                    "bio": code, "research_interests": code, "keywords": code,
                    "dept_address": cfg["dept_address"] or code, "office_number": roster_office or code,
                    "orcid": NA if year < ORCID_START else code,
                    "google_scholar": NA if year < SCHOLAR_START else code,
                    "ssrn": code, "profile_snapshot_ts": "", "profile_days_from_target": "",
                    "method": f"roster:{roster_method}; profile:{code}" +
                              ("; rank/office from roster" if roster_title or roster_office else ""),
                })
            rows.append(row)

    path = OUT / f"faculty_{year}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"[{year}] {len(rows)} person-year rows -> {path}")
    print_coverage(year, rows)

    profiles = [p for p in manifest if p["role"] == "profile"]
    roster = next((p for p in roster_pages if p["role"] == "roster"), {})
    return rows, {
        "year": year, "target": cfg["target"], "roster_url": cfg["roster_url"],
        "roster_snapshot_ts": roster.get("timestamp", ""),
        "roster_days_from_target": roster.get("days_from_target", ""),
        "roster_pages_missing": sum(p["status"] != "ok" for p in manifest if p["role"] != "profile"),
        "profiles_linked": len(profiles),
        "profiles_archived": sum(p["status"] == "ok" for p in profiles),
        "records_parsed": len(rows), "roster_method": roster_method,
        "flag": "possible JS shell / empty page" if empty_flag else ("0 records" if not rows else ""),
    }


def print_coverage(year, rows):
    if not rows:
        return
    print(f"[{year}] field coverage (filled / {NOT_PRESENT} / {NOT_ARCHIVED} / {NA}):")
    for f in ["rank", "website_link", "bio", "research_interests", "keywords", "dept_address",
              "office_number", "orcid", "google_scholar", "ssrn"]:
        c = Counter(r[f] if r[f] in (NOT_PRESENT, NOT_ARCHIVED, NA) else "filled" for r in rows)
        print(f"   {f:20s} {c['filled']:4d} / {c[NOT_PRESENT]:4d} / {c[NOT_ARCHIVED]:4d} / {c[NA]:4d}")


def write_changelog(entries):
    prev = None
    for e in sorted(entries, key=lambda e: e["year"]):
        path_now = urlparse(e["roster_url"] or "").path.rstrip("/")
        e["url_changed_from_prev"] = "" if prev is None else (
            "N" if path_now == prev else f"Y: {prev or '(none)'} -> {path_now}")
        prev = path_now
        e["page_structure"] = ""        # fill in by hand
        e["capture_completeness"] = ""  # fill in by hand
    path = OUT / "changelog.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(entries[0].keys()))
        w.writeheader()
        w.writerows(entries)
    print(f"change log -> {path}")


def write_people(all_rows):
    """Person-level seed table. Name-based keys only; review and merge variants by hand,
    then add identifiers found off-site (ORCID API, manual Scholar lookup) with match confidence."""
    def norm(n):
        n = re.sub(r"\b(Prof(essor)?|Dr|Mr|Ms|Mrs|J\.?D|Ph\.?D|LL\.?M|Esq)\b\.?", "", n, flags=re.I)
        return re.sub(r"[^a-z ]", "", re.sub(r"\s+", " ", n).lower()).strip()

    people = {}
    for r in all_rows:
        k = norm(r["name"])
        if not k:
            continue
        p = people.setdefault(k, {"person_id": "", "name_key": k, "name_variants": set(),
                                  "years": set(), "orcid": set(), "google_scholar": set(), "ssrn": set()})
        p["name_variants"].add(r["name"])
        p["years"].add(str(r["year"]))
        for f in ("orcid", "google_scholar", "ssrn"):
            if r[f] not in (NOT_PRESENT, NOT_ARCHIVED, NA):
                p[f].add(r[f])
    path = OUT / "people.csv"
    cols = ["person_id", "name_key", "name_variants", "years", "orcid", "google_scholar", "ssrn",
            "id_source", "match_confidence"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for i, (k, p) in enumerate(sorted(people.items()), 1):
            ids_on_page = any(p[f] for f in ("orcid", "google_scholar", "ssrn"))
            w.writerow({"person_id": f"LAW{i:04d}", "name_key": k,
                        **{f: "; ".join(sorted(p[f])) for f in
                           ("name_variants", "years", "orcid", "google_scholar", "ssrn")},
                        "id_source": "law website" if ids_on_page else "",
                        "match_confidence": "high (on own profile)" if ids_on_page else ""})
    print(f"people table ({len(people)} people) -> {path}")


# ----------------------------------------------------------------------------
# llm-prep
# ----------------------------------------------------------------------------
LLM_PROMPT = """You are extracting data from one archived faculty web page (HTML below).
Return ONLY a JSON object, no other text, with exactly these keys:

{
  "name": string or null,
  "rank": string or null,              // academic title exactly as written, e.g. "Professor of Law"
  "bio": string or null,               // copy the biography VERBATIM; do not summarize
  "research_interests": [string] or null,
  "keywords": [string] or null,        // only tags/keywords/areas of expertise SHOWN on the page
  "dept_address": string or null,
  "office_number": string or null,
  "orcid": string or null,             // only if an ORCID iD appears on the page
  "google_scholar": string or null,    // only if a Google Scholar profile link appears
  "ssrn": string or null               // only if an SSRN author page link appears
}

Rules:
- Use null for anything not on the page. Never infer, guess, or use outside knowledge.
- Do not generate keywords from the bio; keywords must appear on the page as keywords/tags.
- For a roster page listing many people, return a JSON array of objects with the keys
  name, rank, and profile_url instead.
"""


def llm_prep(year):
    outdir = OUT / "llm" / str(year)
    outdir.mkdir(parents=True, exist_ok=True)
    (OUT / "llm" / "PROMPT.txt").write_text(LLM_PROMPT, encoding="utf-8")
    n = 0
    for p in load_manifest(year):
        if p["status"] != "ok":
            continue
        soup = soup_of(p["cache_file"])
        for t in soup(["script", "style", "noscript", "nav", "header", "footer",
                       "form", "iframe", "svg", "img", "link", "meta"]):
            t.decompose()
        for t in soup.find_all(True):
            t.attrs = {k: v for k, v in t.attrs.items() if k == "href"}
        cleaned = re.sub(r"\n\s*\n+", "\n", str(soup.body or soup))
        slug = re.sub(r"[^\w]+", "_", urlparse(p["url"]).path).strip("_")[-60:] or "index"
        path = outdir / f"{p['role']}_{slug}.html"
        path.write_text(f"<!-- source: {p['url']} snapshot: {p['timestamp']} -->\n{cleaned}",
                        encoding="utf-8")
        n += 1
    print(f"[{year}] {n} cleaned pages -> {outdir}  (prompt: {OUT / 'llm' / 'PROMPT.txt'})")


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["discover", "fetch", "inspect", "parse", "llm-prep"])
    ap.add_argument("--year", type=int, action="append", required=True, choices=sorted(CONFIG))
    ap.add_argument("--no-profiles", action="store_true", help="fetch: roster pages only")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    session = make_session()

    if args.command == "discover":
        for y in args.year:
            discover(session, y)
    elif args.command == "fetch":
        for y in args.year:
            fetch_year(session, y, profiles=not args.no_profiles)
    elif args.command == "inspect":
        for y in args.year:
            inspect_year(y)
    elif args.command == "parse":
        all_rows, log = [], []
        for y in args.year:
            rows, entry = parse_year(y)
            all_rows += rows
            log.append(entry)
        write_changelog(log)
        write_people(all_rows)
    elif args.command == "llm-prep":
        for y in args.year:
            llm_prep(y)


if __name__ == "__main__":
    main()
