# UVA Faculty Dataset: Progress & Methods Log

Running record of what has been built, what was found, what was decided and why, and what comes next. Written for two readers:

1. **Whoever works in this repo next** (including Claude Code): how each script works, how to run it, its current state, and the known quirks.
2. **A researcher replicating this at another university:** the general procedure is stated alongside UVA's results.

**Last updated:** 2026-11-19

---

## 0. Quick start for this repo

### Goal
A dataset of all UVA faculty by school/department by year (2000–present), plus a replicable methodology.

### Where things stand
| Phase | Description | Status |
|---|---|---|
| 0 | Source inventory (catalogs, schools, coverage matrix) | ✅ Done |
| 1 | Registrar (ureg) rosters 2005–2015 | ✅ Parsed and combined. By-hand fixes and inclusion decisions open (§9) |
| 2 | Website enrichment for 2005–2015, school by school | 🟡 **In progress: Law pilot.** 2005 and 2015 fetched, parsed and matched to ureg (§5.9) |
| 3 | 2016–present, Wayback only | 🟡 **Law 2025 pilot done** (fetched + parsed, 104 resident faculty; §5.10). Other years/schools not started |
| 4 | 2000–2004 from print Records | ⬜ Scope undecided |

### Immediate next step
Law 2005, 2015 and 2025 are fetched and parsed. 2005 and 2015 are matched to the ureg: 91% (2006) and 98% (2015) of ureg regular faculty are found on the website (§5.9). 2025 has no ureg; its roster is the site's "Resident Faculty" filter (§5.10). Next candidates: §5.8 step 5a (2005 bios), step 6 (`fhpmediasub` keywords), step 9 (Law 2016–2024), or step 8 (Python vs. LLM).

### Repo layout (expected)
```
facultyScrape/
├── PROGRESS.md                    this file
├── ureg_layout_a_parser.py        Registrar faculty-list parser (all layouts, despite the name)
├── combine_ureg_rosters.py        merges layout A/B/C rosters into one 2005–2015 table
├── wayback_law_scraper.py         Wayback Machine roster + profile scraper (Law pilot)
├── enumerate_catalogs.py          catalog enumeration (written; console method used instead)
├── ureg_parser.py                 early generic parser (superseded; can be deleted)
├── manifest_layout_a.csv          example manifest (superseded by --folder mode)
├── ureg_catalogs.csv              every catoid: Record type, year, faculty listing, layout
├── school_names.csv               name-in-Record → canonical school
├── school_year_matrix.csv         12 schools × 2000–2026 coverage matrix
├── school_year_matrix_heatmap.png coverage figure
├── ureg_pages/                    saved Registrar pages, layout A (2005–2009)   [gitignored]
├── ureg_pages_b/                  saved Registrar pages, layout B (2010)        [gitignored]
├── ureg_pages_c/                  saved Registrar pages, layout C (2011–2015)   [gitignored]
├── ureg_out/                      parser and combine outputs
└── wayback_law/                   Wayback scraper outputs and cache            [cache gitignored]
```
`.gitignore` (added 2026-11-19): `*.html`, `ureg_out/cache/`, `wayback_law/cache/`, `__pycache__/`, `*.pyc`, `.DS_Store`. Files committed before it (`.DS_Store`, saved `ureg_pages/` HTML, 2 `ureg_out/cache/` and 5 `wayback_law/cache/` pages) are still tracked until removed with `git rm --cached`.

### Environment
Python 3 (Anaconda on macOS). `pip install requests beautifulsoup4 lxml`. No other dependencies.

---

## 1. Project definitions

- **Unit of observation:** one faculty member × one academic year (person-year), linked to a person-level table.
- **Year convention:** academic year labeled by its start year (2010 = 2010–11). Website snapshots target **August 15** of that year.
- **Target fields:**

| Field | Expected source |
|---|---|
| Faculty Name | Registrar Record (ureg); school website |
| Rank | ureg; website |
| Identifiers (Google Scholar, ORCID; SSRN for Law) | Mostly external linkage; occasionally on profiles |
| Website Link | School website roster |
| Bio | Profile page |
| Research Interests | Profile page |
| Key Words | Profile page (stated tags only; derived keywords kept separate) |
| Dept Address | School-level page |
| Office Number | Profile page or directory |

- **Missingness codes** (used in every output cell that has no value):
  - `not_archived`: the page has no Wayback snapshot within the window
  - `not_present`: the page was archived but the field isn't on it
  - `not_applicable`: the field couldn't exist that year (ORCID before 2012; Google Scholar profiles before 2011)

---

## 2. Phase 0: Source inventory (done)

### 2.1 Catalog enumeration
**General procedure.** List every edition of the online catalog and its internal ID. On Acalog / Modern Campus sites each edition has a `catoid`. The catalog dropdown shows only current editions, so use the catalog archive page, and confirm by trying `index.php?catoid=N` over the ID range. Non-existent IDs may redirect to the current catalog instead of erroring.

**UVA result** (`ureg_catalogs.csv`):
- catoids 1–73 checked. 52 are real catalogs; 21 (4, 5, 8, 13–16, 20, 35, 36, 51, 56, 59, 60, 63–66, 69–71) redirect to the 2026–27 Undergraduate Record and are not catalogs.
- **Undergraduate Record** and **Graduate Record**: 2005–06 to 2026–27, 22 editions each, no gaps.
- **Summer Record**: 2005–2012. Excluded (no faculty listings).
- No separate Law or Medicine Records; professional schools are in the Graduate Record.
- **Online archive begins 2005**; 2000–2004 aren't online.

catoids for Records with faculty listings (also hard-coded in `ureg_layout_a_parser.py` as `CATALOGS`):

| Year | Graduate | Undergraduate |
|---|---|---|
| 2005 | 2 | 1 |
| 2006 | 9 | 7 |
| 2007 | 12 | 11 |
| 2008 | 18 | 17 |
| 2009 | 23 | 22 |
| 2010 | 26 | 25 |
| 2011 | 28 | 27 |
| 2012 | 30 | 31 |
| 2013 | 34 | 33 |
| 2014 | 38 | 37 |
| 2015 | 40 | 39 |

### 2.2 Where faculty are listed
| Years | Listed? | Layout | Page |
|---|---|---|---|
| 2005–2009 | Yes, both Records | **A** | Section of the school info page: `preview_entity.php?catoid=…&ent_oid=…#faculty` |
| 2010 | Yes | **B** | Standalone page via "Faculty for the [school]" link: `content.php?catoid=…&navoid=…` |
| 2011–2015 | Yes | **C** | Standalone page via "Faculty" in the school's top nav: `content.php?catoid=…&navoid=…` |
| 2016–2026 | **No** | none | Faculty lists dropped from both Records after 2015–16 |

School-level exceptions: **Medicine** clinical faculty never listed (basic-science departments appear under GSAS, §3.5). **Batten** faculty first listed 2011–12. **Architecture** (Graduate 2005) and **A&S** (Undergraduate 2009) have a faculty section but no link to it.

### 2.3 Schools
12 canonical schools: Architecture, Arts & Sciences, Batten, Commerce, Continuing & Professional Studies, Darden, Data Science, Education, Engineering, Law, Medicine, Nursing. Name changes are in `school_names.csv`. Founded during the period: Batten (2007), Data Science (Sept 2019, from the Data Science Institute, est. 2013).

### 2.4 Coverage matrix (`school_year_matrix.csv`)
| Status | School-years | Share of existing |
|---|---|---|
| `listed` (ureg reference available) | 104 | 35% |
| `not_listed` (Wayback needed) | 144 | 48% |
| `catalog_not_online` (2000–2004) | 50 | 17% |
| `school_not_founded` | 26 | — |

---

## 3. Phase 1: Registrar rosters (done)

### 3.1 Retrieval: pages are saved by hand
records.ureg.virginia.edu returns a **bot-verification page** ("verify that you're not a robot") to scripts. This is deliberate, so **do not automate retrieval or try to get around it**. Pages are saved by hand in a browser with this bookmarklet (a bookmark whose URL is this line):

```
javascript:(()=>{const p=new URLSearchParams(location.search);const n=`ureg_c${p.get('catoid')}_e${p.get('ent_oid')||'0'}_n${p.get('navoid')||'0'}.html`;const a=Object.assign(document.createElement('a'),{href:URL.createObjectURL(new Blob([document.documentElement.outerHTML],{type:'text/html'})),download:n});a.click();})();
```

File names: `ureg_c<catoid>_e<ent_oid>[_n<navoid>].html`. The parser reads year and Record from the catoid and the school from the page heading. Pages saved: 80 (A), 16 (B), 90 (C).

Missing pages (no faculty section exists): 2007 Undergraduate A&S, 2009 Graduate Education. Medicine pages have no faculty section and can be deleted.

### 3.2 `ureg_layout_a_parser.py`
One parser for all three layouts (the faculty lists use the same text format; only the surrounding page differs).

**Usage**
```
python ureg_layout_a_parser.py --folder ureg_pages   --prefix layout_a
python ureg_layout_a_parser.py --folder ureg_pages_b --prefix layout_b
python ureg_layout_a_parser.py --folder ureg_pages_c --prefix layout_c
python combine_ureg_rosters.py
```
Other inputs: `--html FILE --year Y --school S [--record Graduate|Undergraduate]`, `--text FILE` (plain text, one line per entry; handy for tests), `--manifest CSV`.

**Where it starts reading:** the `#faculty` anchor (layout A), else a heading whose text is exactly "Faculty", else the first h1/h2 containing "Faculty" (layouts B/C). It stops at the next h1/h2 that isn't a faculty heading, or at "Return to:" / "Print" / "Help".

**How lines are classified** (by text, not HTML tags; `classify()`):

| Kind | Example | Effect |
|---|---|---|
| `unit` | "Department of Physics", "Corcoran Department of History", "Office of the Dean of …" | sets department |
| `program` | "Landscape Architecture" | sets sub-unit |
| `rank` | "Professors", "Retired Faculty", "Adjunct", "Program Director", "Dean:" | sets rank and faculty type |
| `entry` | "W. G. Clark, Jr., B.Arch., Edmund S. Campbell Professor of Architecture" | a person |
| `skip` / `stop` | page tabs, vacancies, prose, contact details / "Return to:" | ignored / end |
| `unparsed` | anything else | logged in the headings file |

**Key rules**
- Rank comes from the group heading (many lines have no title); if the heading has no rank ("Visiting"), from the person's title.
- Faculty type from heading words: `regular`, `retired`, `emeritus`, `adjunct`, `visiting`, `honorary`, `general faculty`, `lecturer`, `instructor`, `research`, `affiliated`, `secondary appointment`, `postdoctoral`, `in residence`, `coordinator`, `joint appointment`, `courtesy`, `professor of practice`, `program administration`, `administrative and professional`, `administration`. `adjunct`, `emeritus`, `courtesy` are also taken from a person's own title.
- A bare name (no commas) is a person if it follows a person or a rank heading, or contains a middle initial; otherwise it's a program.
- People in the Dean's office are merged with their faculty listing; people listed *only* there are flagged `admin office only; may be staff`.
- Source typos are kept as written.

**Patterns handled** (all found on real pages): named departments and departments with commas; "Jr." in names vs. in chair titles; degrees spaced ("B. A."), without periods ("PhD"), with institutions ("M.A. (Oxon.)"), non-US ("Licenciado en Derecho", "Diplôme"), spelled out ("Doctor of Humanities"), run into the title, after the name without a comma, or continued on the next line; nursing credentials ("RN", "FAAN", "APRN-BC"); honorifics ("Judge"); name particles ("al-Rahim"); a period instead of a comma before degrees ("Brian H. Annex. M.D."); titles continued on the next line; repeated "Faculty" headings (Darden 2008–09); 2012+ page tabs ("About", "Academic Rules", …); group headings in `<h2>` vs `<h3>` (Darden 2014).

**Outputs** (`ureg_out/`):
- `<prefix>_listings.csv`: one row per line in the Record (degrees, raw line, rank source).
- `<prefix>_roster.csv`: one row per person per school-year, Graduate and Undergraduate Records combined. Columns: `year, records, school, department, name, rank, faculty_type, titles, n_listings, flag, source_url`.
- `<prefix>_headings.csv`: every non-person line and how it was classified.

**Check after every run:** rows in the headings file classified `unparsed` or `program`. A misread department or name shows up there even when the roster looks plausible. Legitimate `program` rows: Architecture / Landscape Architecture, "Religion, Culture, and Social Theory", SCPS program names. Legitimate `unparsed`: SCPS committee names.

**Regression targets** (real pages; expected results with the current parser):

| Saved page | School-year | Expected |
|---|---|---|
| `ureg_c23_e1452.html` | Darden 2009 (A) | 191 listings, 179 people, 0 unparsed |
| `ureg_c26_e0_n687.html` | Graduate A&S 2010 (B) | 941 listings, 908 people, 0 unparsed |
| `ureg_c28_e0_n758.html` | Graduate A&S 2011 (C) | 923 listings, 892 people, 0 unparsed |
| `ureg_c38_e0_n2179.html` | Darden 2014 (C) | 132 listings, 123 people: 68 regular (34/19/15), 30 A&P, 20 adjunct, 5 lecturer |

A good first task in Claude Code: turn these into a `tests/` folder (pytest), plus `--text` fixtures for the line patterns above, so parser changes can't silently break earlier years.

**Remaining by-hand fixes** (can't be resolved from text): "Li, Lei," / "Lei, Li," (2008 Undergraduate and 2010 Graduate A&S, surname first); Martin Wu (2009 Graduate A&S); George Sampson and Scot French (2010 Undergraduate Architecture), bare names read as programs. Check the person listed after each; their `program` may be wrong too.

### 3.3 `combine_ureg_rosters.py`
Merges the three roster files into `ureg_out/ureg_roster_2005_2015.csv` (adds `layout` and `core_suggested`) and `ureg_out/ureg_counts_by_school_year.csv`.

`core_suggested` is a **flag, not a filter**: `yes` unless SCPS, retired, emeritus, honorary, administrative and professional, or Dean's-office-only.

### 3.4 Results
22,752 person-school-years. Core counts by year:

| 2005 | 2006 | 2007 | 2008 | 2009 | 2010 | 2011 | 2012 | 2013 | 2014 | 2015 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1,622 | 1,674 | 1,543 | 1,785 | 1,873 | 1,892 | 1,878 | 1,793 | 1,783 | 1,926 | 1,953 |

### 3.5 Record changes (not parser errors; they affect comparability)
| Change | Years | Effect |
|---|---|---|
| Retired/emeritus groups dropped from every school | from 2009 | all-listed counts fall |
| Law "Adjunct and Part-time Faculty" dropped | from 2007 | Law ~160 → ~100 |
| SCPS list roughly halved | from 2008 | ~100 → ~50 |
| Darden honorary appointments grew | 2005 → 2009 | 25 → 54 |
| Nursing degrees without periods + credentials | from 2008 | format only |
| Darden: honorary/visiting/general-faculty groups replaced by "Administrative and Professional" and "Adjunct" | from 2012 | |
| Nursing instructors listed in much larger numbers | from 2012 | 4 → ~35 |
| Architecture lecturers halved | from 2014 | 32 → 16 |
| Medicine Cell Biology, Biochemistry missing from GSAS | 2012–2013 | back in 2014 |
| Departments renamed / punctuation varies | various | needs a department-name mapping |
| No Undergraduate A&S page | 2007 | **A&S 2007 undercount** (Undergraduate-only people missing, e.g., most of Music) |

Coverage findings: Medicine's basic-science departments (Biochemistry & Molecular Genetics, Cell Biology, Microbiology, Molecular Physiology, Pharmacology, Public Health Sciences) are listed under GSAS; clinical departments never. Darden lists full faculty every year. SCPS lists mostly administrators and adjunct instructors.

---

## 4. Phase 2 plan

1. **Law pilot** (current): website roster + profiles for 2005 and 2015 from the Wayback Machine.
2. **Match** website rosters to ureg rosters for the same year; measure agreement (both / web only / ureg only). The 2015 agreement rate is the calibration for 2016+.
3. **Python vs. LLM** on ~30 profiles from two schools, scored field by field.
4. Repeat school by school.

---

## 5. Wayback scraper: `wayback_law_scraper.py` (Law pilot)

### 5.1 Commands
```
python wayback_law_scraper.py discover --year 2005 --year 2015   # find candidate roster pages
python wayback_law_scraper.py fetch    --year 2005 [--no-profiles] # download roster (+ profiles)
python wayback_law_scraper.py inspect  --year 2005                # link patterns, classes, sample text
python wayback_law_scraper.py parse    --year 2005 --year 2015    # extract fields -> CSVs
python wayback_law_scraper.py llm-prep --year 2015                # cleaned HTML + prompt for an LLM
```
`--year` is repeatable; allowed years are the keys of `CONFIG` (2005, 2015, 2025).

**Before first use:** replace `YOUR_EMAIL_HERE` in `USER_AGENT`. The Internet Archive asks automated users to identify themselves.

### 5.2 Settings and CONFIG
Global settings at the top: `DELAY = 2.0` s between requests; `ROSTER_WINDOW_DAYS = 60`; `PROFILE_WINDOW_DAYS = 180`; retries 3 with backoff on 429/5xx.

Per-year `CONFIG[year]` keys:

| Key | Meaning |
|---|---|
| `target` | snapshot target date, `YYYY0815` |
| `roster_url` | the faculty roster page (chosen by hand from `discover` output) |
| `roster_window_days`, `profile_window_days` | optional per-year overrides of the windows |
| `pagination_regex` | links to further pages of the same roster |
| `profile_regex` | links to individual profiles |
| `item`, `name`, `title`, `roster_office` | CSS selectors on the roster (optional; if `item` is None, names come from the profile-link text). All `title` matches are joined with ` | `. Roster title/office fill in when the profile isn't archived |
| `dept_address` | school address typed once, if profiles don't give it |
| `profile` | dict of CSS selectors on profile pages: `name, rank, bio, interests, keywords, office, address` (unused for Law; see `extractor`) |
| `extractor` | name of a function in `EXTRACTORS` that reads a profile page's structure; overrides `profile`. Law: `law_notes_directory_card` (2005), `law_fhp_2015` (2015) |

### 5.3 How each step works
- **discover**: (1) finds the archived homepage closest to the target. If the exact homepage capture isn't a normal page, `resolve_redirect()` lists every capture with its status in `homepage_captures_<year>.csv`, follows the closest **redirect**, or uses a **revisit** record. Writes `homepage_links_<year>.csv` (faculty links on that homepage). (2) Searches the CDX index with a **prefix** query on `www.law.virginia.edu/` filtered to URLs containing "faculty", limited to 2,000 results, 3-minute timeout. Writes `discover_<year>.csv`. Failures are caught and reported, not fatal.
- **fetch**: CDX lookup for the closest capture of each URL within the window, then downloads the raw page (`/web/<ts>id_/<url>`, no Wayback toolbar), cached in `wayback_law/cache/`. Follows `<frame>`/`<iframe>` sources (skipping news/menu/banner frames), `pagination_regex` links, and `profile_regex` links. Profile fallbacks when the exact URL isn't archived: (a) the other capitalization of `Faculty.nsf`/`faculty.nsf`; (b) `notes_fallback()`, a one-time prefix index of every archived page in the `.nsf` database, matched by Lotus Notes **document ID**. Writes `manifest_<year>.json` (role, url, timestamp, days_from_target, cache file, status). Warns if no links match `profile_regex`.
- **inspect**: for each roster, the most common CSS classes, the **most common link patterns** (long hex IDs → `<ID>`, numbers → `<N>`) with an example, and the first lines of text; for one sample profile, classes and headings/labels. Use it to set `profile_regex` and then the `profile` selectors.
- **parse**: roster entries via `item` selectors or profile-link text (links with empty text are skipped); profiles matched to roster links with a loose URL key (case-insensitive, `:80` stripped); profile fields via the year's `extractor` if set, else `profile` selectors, with office from a selector or a regex. Titles split on `<br>` are joined with ` | `; ORCID / Google Scholar / SSRN found automatically from links. Writes `faculty_<year>.csv` (person-year), `people.csv` (person table seed with name variants and identifiers), `changelog.csv` (roster URL per year, change flag, snapshot distance, coverage). Prints field coverage by missingness code.
- **llm-prep**: strips scripts/nav/styles, keeps hrefs, writes `llm/<year>/*.html` and `llm/PROMPT.txt` (fixed JSON schema; copy bios verbatim; `null` instead of guessing; keywords only if shown on the page).

### 5.4 Wayback Machine lessons (apply to any school)
- **Domain-wide CDX queries time out (HTTP 504)** on a site this size. Use `matchType=prefix` on a path, add `limit`, and catch errors.
- **Homepage captures may be redirects or revisits**, not status-200 pages. Check `homepage_captures_<year>.csv`.
- **Revisit records** (status `-`) point to an identical earlier copy; the Wayback Machine serves them normally.
- **Path capitalization matters** to the Archive (`Faculty.nsf` ≠ `faculty.nsf`).
- **The Archive may record a URL with `:80` in the host** (`www.law.virginia.edu:80/...`). Normalize the URL before comparing it with roster links.
- **`inspect` class counts aren't enough.** The useful classes (`fac_sidebar`, `titlepage`, `faculty_body`) were below its top-25 cut-off. Read the raw HTML of one profile before deciding a page has no structure.
- **Framesets**: clicking inside a frame doesn't change the address bar. To get the real page, right-click inside the frame → "Open Frame in New Tab".
- **Deep pages are archived less often** than rosters; expect wider windows and `not_archived` profiles.
- **The site-wide index search in `discover` failed again for 2025** (`ChunkedEncodingError`). A prefix query on the faculty path (`www.law.virginia.edu/faculty`, ±60 days) worked: 192 captures, 182 URLs, saved to `discover_2025.csv` by hand.
- **Filtered list pages are archived as separate URLs.** Drupal directory filters (`?faculty_type=521`) are captured individually, so a specific roster can be archived even when the unfiltered page isn't.

### 5.5 Lotus Notes cheat sheet (Law site, ~2000–2015)
The Law site served faculty pages from a Lotus Notes database, `lawweb/faculty.nsf` (also `Faculty.nsf`).
- `/db.nsf/<View>?OpenView` lists documents in a view. `&ExpandView` expands all categories; `&Expand=1` opens only the first; `&Start=N&Count=N` pages through long views.
- `/db.nsf/<viewID>/<docID>?OpenDocument` opens a document. **The same document is reachable through any view**, so the view part of a profile URL can differ between the roster's links and what the Archive captured.
- `/db.nsf/<View>/<key>` opens a document by key (e.g., `FHPbI/1140145`, `PrFMPbW/rjb6f`).

Views seen:

| View | What it is | Use |
|---|---|---|
| `DBN` | Law Directory, all by name (faculty + staff + offices) | Not a faculty roster |
| `FDBN` | **Faculty** Directory by Name | **2005 roster** |
| `ADBN` | another directory view (2015 nav "Directory") | not examined |
| `fhpmediasub` | Faculty Media Guide by subject (59 topics; people repeated) | Not a roster; **source of stated expertise keywords** |
| `PrFMPbW/<webid>` | media-guide profile by web ID | |
| `FHPbI/<number>` | faculty home page by ID | **2015 profiles** |

### 5.6 Law pilot: current state

**Homepages (`discover`):**
- 2005: `www.law.virginia.edu/` redirected to **`/home2002/html/index.htm`** (snapshot 20050804021610); 3 faculty links. 74 candidates in `discover_2005.csv`.
- 2015: only **revisit** captures in the window; used 20150729050737; 5 faculty links. `discover_2015.csv` hit the 2,000-row cap.

**Rosters chosen (by hand) and set in CONFIG:**

| Year | roster_url | Snapshot used | Notes |
|---|---|---|---|
| 2005 | `http://www.law.virginia.edu/lawweb/faculty.nsf/FDBN?OpenView&ExpandView` | **20060829194319** (379 days after target) | Faculty-only Notes view. No earlier capture within ±400 days. **Effectively a 2006 roster: compare with the 2006 ureg, not 2005.** Fallback for 2005: `DBN` (Nov 2005 capture) includes staff. |
| 2015 | `http://www.law.virginia.edu/html/faculty/resident.htm` | 20150811030108 (4 days) | Title: "Resident and Visiting Faculty, and Faculty From Other Disciplines at UVA" — broader than ureg's regular faculty; extra people will show as web-only. |

**Profile patterns:**
- 2005: `/lawweb/faculty.nsf/<32-hex viewID>/<32-hex docID>?OpenDocument` (78 links; roster links use view `e26cd1274935f294852566d6000372f3`). `pagination_regex`: `/faculty.nsf/F?DBN?OpenView&Start=…`.
- 2015: `/lawweb/[Ff]aculty.nsf/FHPbI/<number>` (125 links: 101 `Faculty.nsf`, 24 `faculty.nsf`).

**Fetch history.** The first fetch got 2005 profiles **0/78** at the exact URLs, and 2015 wasn't attempted because the pattern was wrong. Changes made after that:
- `notes_fallback()` added: looks up 2005 profiles by document ID across all views.
- 2005 `profile_window_days` = 400.
- 2015 `profile_regex` set to `FHPbI`; capitalization fallback added.

**Last fetch result (2026-11-19): complete for both years.**

| | 2005 (FDBN) | 2015 (resident.htm) |
|---|---|---|
| Unique profile links on roster | 78 | 121 (125 links; 4 repeated) |
| Fetched, status `ok` | 78 | 121 |
| Distinct pages / page contains the person's surname | 78 / 78 | 121 / 121 |
| Days from target | 72 at 360–383 (Sept 2006); 6 at ~74 (Oct 2005) | 114 within 30; 7 at 90–171 |

Checked by content hash (no duplicate pages) and by matching each roster name to its page. The manifest doesn't record *which* route found a page (exact URL, capitalization fallback, or `notes_fallback()`), so the document-index size and fallback usage weren't captured (§5.7).

**2005 is a mixed-year roster.** The 6 October 2005 profiles are Hurwitz, Kitch, Kordana, Nachbar, Robinson and Smith (page title "Directory Page for …"). The other 72 are from about September 2006 (page title "Faculty - University of Virginia School of Law"). Record the snapshot date per person-year rather than assuming 2006 for everyone.

**What the profile pages contain:**
- **2005: directory cards only.** Name, title(s), office, phone, fax, email, secretary/assistant, "last updated" date. **No bio, interests, or keywords.** There are two templates: Oct 2005 (`/home2002` layout) and 2006 (`div.content`). Both have the name in `<font size=4>`, the titles in the next bold `<font size=2>`, and a label table (label | spacer | value). On 77/78 cards "Office:" appears twice, and the **second is the assistant's**. **76 of 78 cards link to the person's 2005-era faculty home page** (`/lawweb/faculty.nsf/FHPbI/<id>`, e.g. Abraham = `FHPbI/1509`). Those pages are the likely source of 2005 bios and subjects (§5.8 step 5a). Barzuza and Garrett have no link.
- **2015: full profiles.** `div.fac_sidebar` holds `span.sectionhead` groups (CONTACT: email, phone, Room; ASSISTANT; **SUBJECTS**, the stated keywords) with `span.newsspaced` values. `div.faculty_body` holds the name (`span.titlepage`), the bold title block, italic degree lines ("J.D., Yale Law School, 1971"), and the bio after the last `div.line`.

**Last parse result (2026-11-19):** filled / `not_present` / `not_applicable`

| Field | 2005 (78) | 2015 (121) |
|---|---|---|
| rank (full title text) | 78 / 0 / 0 | 121 / 0 / 0 |
| bio | 0 / 78 / 0 | 121 / 0 / 0 |
| keywords (stated SUBJECTS) | 0 / 78 / 0 | 84 / 37 / 0 |
| research_interests | 0 / 78 / 0 | 0 / 121 / 0 (no separate field on the pages) |
| office_number | 78 / 0 / 0 | 103 / 18 / 0 |
| dept_address | 0 / 78 / 0 | 0 / 121 / 0 (set `dept_address` in CONFIG by hand) |
| ORCID / Scholar | not_applicable | 0 / 121 / 0 |
| SSRN | 0 / 78 / 0 | 1 / 120 / 0 (Michael D. Gilbert) |

- All 18 profiles in 2015 without an office are visiting or other-school faculty with no Law room (e.g. Craver, Saltzburg, Gledhill, Bernheim, Cushman). The blank is real.
- `rank` holds the full title text, with multiple titles separated by ` | ` (e.g. "Professor of Law | Horace W. Goldsmith Research Professor"). An ureg-style rank (Professor / Associate / Lecturer, General Faculty …) still needs to be derived from it, in the matcher or a later step.
- 2015 bios end with the "Scholarship Profile: …" line where the page has one. Degrees are on the page but not yet saved (there's no field for them).
- `people.csv`: 188 name keys across 2005, 2015 and 2025 (not yet reviewed).

### 5.7 Known issues in the scraper
- ~~Names from link text.~~ **Resolved:** the `name` column now comes from the profile page, in "First Middle Last" form with whitespace normalized (0 surname-first names in 2005). Roster link text is used only if the profile has no name.
- ~~Rank not extracted~~, ~~profile selectors all None~~, ~~2005 office takes the assistant's~~: **resolved** by the per-year extractors (2026-11-19).
- ~~Empty link text on the 2015 roster~~: not a bug. `roster_entries()` already skips links with no text (Laycock is parsed from his third link).
- **Fixed 2026-11-19: 3 profiles not matched in 2015** (Deeks, Doran, Ryan). The manifest stored the Archive's URL with `:80`, so the exact-string lookup failed. `parse` now uses a normalized key.
- **One name per page**: `parse` de-duplicates by lowercased name only.
- **`fetch` stores the Archive's URL in the manifest** (sometimes with `:80`), not the requested URL. Storing both would be cleaner.
- **The manifest doesn't record how each page was found** (exact URL / capitalization fallback / `notes_fallback()`). Add a `found_via` field so the methods write-up can report fallback rates.
- The 2015 page includes visiting and other-school faculty; the page sections should be used to tag them (needs a selector or LLM step).
- `notes_fallback()` runs one prefix query on the whole `.nsf` database; it may hit 504s. If it does, narrow the window or query by view.
- The script name says "law" but nearly everything is generic; school-specific values live only in `DOMAIN`, `HOMEPAGE`, `OUT`, and `CONFIG`. A refactor could move these into a per-school config file.

### 5.8 Next steps (in order)
1. ~~Run `fetch --year 2005 --year 2015`.~~ **Done 2026-11-19:** 78/78 and 121/121 (§5.6).
2. ~~If 2005 is still 0, check by hand.~~ Not needed.
3. ~~Run `inspect`; set profile selectors.~~ **Done 2026-11-19.** `inspect` found no usable classes in its top 25, so structure-based extractors were written instead (§5.2, §5.6).
4. ~~Run `parse`.~~ **Done 2026-11-19.** Coverage is in §5.6.
5a. **Optional: 2005 bios.** Fetch the 76 linked 2005-era home pages (`FHPbI/<id>`, about Aug 2005–Sept 2006), write an extractor for them, and fill bio/keywords for 2005. This needs network access and `USER_AGENT` set first (it still says `YOUR_EMAIL_HERE`).
5. ~~Write the matcher.~~ **Done 2026-11-19** (§5.9). The 4 review pairs were provisionally accepted (verify later).
6. Use `fhpmediasub` (2015) as a stated-keywords source: topic → faculty.
7. ~~Law 2025.~~ **Done 2026-11-19** (§5.10).
8. Python vs. LLM comparison on ~30 profiles via `llm-prep`.
9. **Law 2016–2024**: same Drupal-era approach year by year; check when the site moved from Lotus Notes to Drupal. Add years to `CONFIG`. Link people across years by the profile number (§5.10).

### 5.9 Matcher: `match_web_ureg.py`
**General procedure.** For each school-year, pair the website roster with the Registrar roster one-to-one. Label everyone `both` / `web_only` / `ureg_only` and give a reason for every unmatched person. Report agreement at several scopes, because the two sources define "faculty" differently.

**Usage**
```
python match_web_ureg.py                                   # Law: web 2005 -> ureg 2006, web 2015 -> ureg 2015
python match_web_ureg.py --pair 2015:2015 --review-threshold 0.75
```
Standard library only. `difflib` is used instead of `rapidfuzz`, which isn't installed.

**Stages** (in order; each ureg person is used at most once):
1. **manual**: decisions in `match_decisions.csv` (always win).
2. **exact**: identical normalized name. Normalization: lowercase, accents and punctuation removed, Jr./III and honorifics dropped, surname-first flipped.
3. **relaxed**: same surname (hyphens ignored) and same first or given name, or a nickname from the built-in table (Doug/Douglas, Rich/Richard, Chris/Christopher …). Middle initials must not conflict, and the candidate must be the only one.
4. **review** (never accepted automatically): same surname with a different first name, a conflicting middle initial, initials only, several candidates, or a surname spelled differently (difflib ratio ≥ `--review-threshold`, default 0.80) with agreeing first names. These stay unmatched until you write `match` / `no_match` in `match_decisions.csv`. Decided rows are kept across runs.

**Reasons for unmatched people** (checked in this order): *web_only*: in ureg that year under another school → "visiting" in title → another institution in title → another UVA school/discipline in title → in Law ureg only in later years (new hire) / only earlier years (left, or dropped) → unexplained. *ureg_only*: adjunct / retired-emeritus / administration / lecturer-research → last in ureg this year (departed) → unexplained.

**Outputs** (`wayback_law/match/`): `match_<webyear>.csv` (one row per person: status, method, score, reason, both names, web title, ureg rank/type/core flag/department, profile link), `match_summary.csv`, `match_decisions.csv`.

**Law results (2026-11-19, all 4 review pairs provisionally accepted):**

| | web 2005 ↔ ureg 2006 | web 2015 ↔ ureg 2015 |
|---|---|---|
| Website people / ureg people | 78 / 160 | 121 / 102 |
| Matched (exact + relaxed + manual) | 65 (53 + 9 + 3) | 87 (74 + 12 + 1) |
| **ureg regular faculty found on website** | **64/70 = 91.4%** | **85/87 = 97.7%** |
| ureg `core_suggested=yes` found on website | 64/140 = 45.7% (includes 70 adjuncts) | 87/90 = 96.7% |
| ureg all listed found on website | 65/160 = 40.6% | 87/102 = 85.3% |
| Website Law faculty found in ureg (excludes visiting / other-school / other-institution) | 83.3% | 92.6% |

Before manual review: 87.1% / 96.6% regular faculty found (exact + relaxed only).

All 21 relaxed matches were checked by hand and are correct.

**What explains the gaps:**
- **2005/06.** 12 web-only people are **new hires**: on the Aug 2006 website, but first in the ureg in 2007 (Bagley, Harmon, Heytens, Mitchell, Oliar, Brown-Nagin …). The 2006–07 Record was compiled before they arrived. On the ureg side, 70 adjuncts aren't on the website's resident list, plus 11 retired/emeritus, 8 administration, and 4 who left before the snapshot (Malani, the Triantises, Brooks). Unexplained: Kent Olson (web; librarian), McCurdy and Broome (ureg; joint appointments with History/Commerce).
- **2015.** The website roster is broader than Law faculty. 12 are from **other institutions** (Münster, Sydney, Paris 2, GWU, LSE …), 3 are from other UVA departments, and 12 are listed in the 2015 ureg under **another school** (mostly A&S; Bonnie under Batten, Cornell under Education). 3 were in earlier Law ureg years only (Hurwitz, Buck, Schwartzman to 2014). The 12 ureg-only are almost all administration. Unexplained: Givens, Evans, Ware, Murrie (web); Block, Karen Moran (ureg).
- **Implication for 2016+:** a website roster of resident faculty matches regular ureg faculty at about 97% in 2015. Visiting and other-school people need to be filtered out using the title text (or the page sections).

**Review pairs: all 4 accepted as matches provisionally (2026-11-19)**, noted "provisional … verify" in `match_decisions.csv`: Kerry / Karen Abrams (2006 and 2015; check which is her legal name); Margaret Foster Riley / Margaret V. W. Riley; Robert Nelson Sayler / Robert N. Saylor (2006 ureg spelling; the 2015 ureg has "Sayler"). To reverse one, change its `decision` to `no_match` and rerun.

**Known limits (matcher):** fuzzy scoring is surname-only, so a person whose surname changed won't be suggested. The reason text is rule-based (title regexes) and can be wrong for joint appointments (e.g. Murrie's title names both Medicine and an "Institute of Law").

### 5.10 Law 2025 (no Registrar data)
**General procedure for years after the ureg stops (2016+).** Find the roster from the archived homepage, then narrow the archive-index search to the faculty path. Pick the roster whose scope is closest to "regular faculty" and log that choice. With no ureg to check against, the 2015 agreement (§5.9) is the calibration, and the site's own faculty-type labels define scope.

**Discovery (2026-11-19).**
- Homepage 20250813105556 links to `/faculty`, `/faculty/directory`, `/scholarship`.
- The site-wide index search failed (§5.4). A prefix search on `www.law.virginia.edu/faculty` found 182 URLs: `/faculty/profile/` (62), `/faculty/adjunct-profile/` (69), `/faculty/directory/…` (47).
- The site is **Drupal**. `/faculty/directory/faculty` is filtered by `faculty_type`: **521 Resident Faculty**, 6506 Visiting, 6501 University Faculty From Other Disciplines, 11676 Other Scholars, 171 Adjunct, 6476 Emeritus. There's also an `expertise` filter. The unfiltered `/faculty/directory` is a departments page, not a roster.

**Roster chosen:** `https://www.law.virginia.edu/faculty/directory/faculty?faculty_type=521` (Resident Faculty), snapshot **20250524222938 (83 days before target; no closer capture within ±120 days)**. It's one page with 103 profile links and no paging. Visiting, Other Disciplines and Other Scholars are separate archived pages and are **not included**. The 2015 roster (`resident.htm`) mixed all of these, so 2025 is narrower by design and closer to ureg "regular faculty" (decision #25).

**Profiles:** `/faculty/profile/<UVA computing ID>/<number>`. **The number is the old Lotus Notes `FHPbI` ID** (Deeks = 2378410 in both 2015 and 2025), so it's a stable person key across the site change.

**Fetch:** 100/103 profiles archived (median 58 days from target; range 10–149). Not archived within ±180 days: McShane, Schragger, Shin. Their name, titles and room come from the roster card.

**Extractor `law_drupal_2025`** (standard Drupal field classes):

| Field | Source |
|---|---|
| name | `h1.page-title` |
| rank (titles, ` | `-separated) | `.field--name-field-position-title .field__item` |
| office | `.field--name-field-office` (Room) |
| keywords | `.field--name-field-bio-expertise li` (stated **Expertise** list) |
| bio | `.field--name-body` inside the Biography tab (`div[id$='-bio']`); the page's other three body fields are footer blocks |
| dept_address | footer contact block: "580 Massie Road Charlottesville, VA 22903" |
| ORCID | "ORCID Author Profile" link (always the person's own; 1 per page when present) |
| SSRN | only author-page links count. Most SSRN links on profiles are individual papers ("More") and are ignored |

Also on the page but not saved yet: Education (degree, school, year), Affiliations (centers/programs), Current Courses, Publications, email, phone, assistant.

**Parse result (104 rows):** rank 104; bio 99 (+2 not present, 3 not archived); keywords 100; office 101; dept_address 100; **ORCID 63**; Google Scholar 2; SSRN 2. Rows without an office: Jeffries, Robinson, Ryan (administrators).

**Cross-year link by profile number:** 63 people have the same profile number in 2015 and 2025. 63 of the 94 people treated as Law faculty in 2015 are still on the 2025 resident list. Name strings differ for some of these (Rachel A. Harmon / Rachel Harmon; John Monahan / John T. Monahan; Jr. punctuation), so link on the number, not the name.

**Known issues (2025):**
- **Xiao Wang** is on the roster but his card links to a short URL (`/wang`), not `/faculty/profile/…`, so his profile wasn't fetched. His bio and keywords show `not_present` when the truth is "not fetched". Fix: fetch `https://www.law.virginia.edu/wang` (or extend `profile_regex`) and re-parse.
- The 2025 ↔ 2015 comparison uses the profile number only. A proper person table (`people.csv` is still name-keyed) should take the number as its key where available.

---

## 6. Decisions log

| # | Decision | Reason |
|---|---|---|
| 1 | ureg is a **reference standard**, not perfect ground truth | Compiled before the year starts; scope differs from websites |
| 2 | Gather ureg pages by hand | Small number of catalogs; site blocks bots |
| 3 | Exclude Summer Records | No faculty listings |
| 4 | Redirecting catoids = not a catalog | They load the current catalog |
| 5 | One row per school, Graduate + Undergraduate combined; College + GSAS = Arts & Sciences | Same people in both |
| 6 | Coverage matrix at school level | Records list faculty by school |
| 7 | Years 2000–2026, academic start year | Project scope; Record labels |
| 8 | Drop the formal "department across time vs. year across departments" test | ureg is uniform within a year; websites change per school |
| 9 | ureg by catalog/year; websites by school across time | See #8 |
| 10 | Small Python vs. LLM comparison in Phase 2 | Tells replicators which fields can be scripted |
| 11 | Profiles get a wider Wayback window than rosters | Deep pages archived less often |
| 12 | Missingness codes `not_archived` / `not_present` / `not_applicable` | Distinguish gaps |
| 13 | Identifiers via a separate linkage step with match confidence | Rare on websites; Scholar has no API |
| 14 | Two tables: Person and Person-Year | Identifiers are person-level |
| 15 | Respect the ureg bot check | Deliberate block |
| 16 | Classify ureg lines by text, not HTML tags | Markup varies; text patterns stable |
| 17 | Rank from group headings | Many lines have no title |
| 18 | Keep every listing; collapse per person per school-year | People appear in several groups and both Records |
| 19 | Keep source typos as written | Correction belongs to linkage |
| 20 | `core_suggested` is a flag, not a filter | Inclusion decisions still open |
| 21 | Law web roster for "2005" is compared with the **2006** ureg | Earliest archived faculty-only roster is Aug 2006 |
| 22 | Roster pages are chosen by a person from `discover` candidates, and logged | Which page counts as "the faculty list" is a methods decision |
| 23 | Fuzzy and different-first-name matches are never accepted automatically; a person decides, and the decision is saved | Avoids silent false matches; decisions are auditable and survive reruns |
| 24 | Main agreement figure = ureg **regular** faculty found on the website; other scopes reported alongside | `core_suggested` includes adjuncts, which the Law website doesn't list |
| 25 | Law 2025 roster = the site's **Resident Faculty** filter only (visiting / other disciplines / other scholars excluded) | Closest in scope to ureg regular faculty; the site labels faculty type itself. Other types can be added as separate rosters later |

---

## 7. Data model (draft)

**Person:** person_id, name variants, ORCID, Google Scholar, SSRN, id_source, match_confidence
**Person-Year:** person_id, year, school, department, rank, faculty_type, website_link, bio, research_interests, keywords (stated), dept_address, office_number, source (ureg/web), snapshot timestamp, days from target, extraction method

Every empty cell carries a status or missingness code.

---

## 8. Files

| File | Purpose | Status |
|---|---|---|
| `ureg_catalogs.csv` | catoid → type, year, listing, layout | Complete |
| `school_names.csv` | Name-in-Record → canonical school | Complete for snapshot years |
| `school_year_matrix.csv`, `_heatmap.png` | Coverage matrix and figure | Complete; Medicine 2005–2015 should become "partial" |
| `enumerate_catalogs.py` | Catalog enumeration | Not needed (console method used) |
| `ureg_parser.py`, `manifest_layout_a.csv` | Early versions | Superseded |
| `ureg_layout_a_parser.py` | ureg parser, all layouts | Done; run on 186 pages |
| `combine_ureg_rosters.py` | Combine rosters + counts | Done |
| `ureg_out/layout_[a,b,c]_*.csv` | Parser outputs | Complete pending by-hand fixes |
| `ureg_out/ureg_roster_2005_2015.csv` | Combined roster | Complete pending by-hand fixes |
| `ureg_out/ureg_counts_by_school_year.csv` | Counts | Complete |
| `wayback_law_scraper.py` | Wayback scraper | In progress (§5.6) |
| `wayback_law/discover_*.csv`, `homepage_links_*.csv`, `homepage_captures_*.csv` | discover outputs | 2005, 2015 done |
| `wayback_law/manifest_*.json`, `cache/` | fetch outputs | 2005 and 2015 complete: rosters + all profiles (206 cached pages) |
| `wayback_law/faculty_2005.csv`, `faculty_2015.csv`, `faculty_2025.csv`, `people.csv`, `changelog.csv` | parse outputs | 78 + 121 + 104 person-years; 188 people (unreviewed) |
| `wayback_law/discover_2025.csv`, `homepage_links_2025.csv`, `manifest_2025.json` | 2025 discover/fetch outputs | discover_2025 from a `/faculty` prefix search (§5.10) |
| `match_web_ureg.py` | Website ↔ ureg matcher (§5.9) | Done; 4 review pairs provisionally accepted |
| `wayback_law/match/match_*.csv`, `match_summary.csv`, `match_decisions.csv` | matcher outputs; decisions file is hand-edited | Law 2005/06 and 2015 |
| `.gitignore` | Ignores HTML, caches, `__pycache__`, `.DS_Store` | Added 2026-11-19 |

---

## 9. Open items

**Inclusion decisions (before counts are final):**
- [ ] Core definition (suggested: active, non-honorary; exclude retired/emeritus, honorary in every year)
- [ ] Adjunct/part-time: separate category? (Law drops them after 2006)
- [ ] General Faculty (Darden, no rank)
- [ ] Dean's-office-only people: exclude from faculty, keep in listings
- [ ] SCPS: exclude or separate
- [ ] Medicine basic sciences under GSAS: assign to Medicine or A&S
- [ ] Update coverage matrix: Medicine 2005–2015 = partial

**Data checks:**
- [ ] By-hand name fixes (§3.2)
- [ ] Department-name mapping (renames, punctuation)
- [ ] A&S 2007 undercount: document only (suggested), or estimate

**Phase 2:** see §5.8.

**Other:**
- [ ] Email the Registrar: faculty-listing cutoff after 2015–16, pre-2005 Records, bulk access
- [ ] Decide whether 2000–2004 are in scope
- [ ] Add regression tests for the ureg parser (§3.2)

---

## 10. Adapting this to another university

Find these out first:
1. Catalog platform (Acalog/Modern Campus, CourseLeaf, other) and whether past editions are online
2. Earliest online edition
3. Which editions list faculty, and when listings stop
4. Layout changes across years
5. Schools founded, merged, renamed
6. Schools never listed (like UVA Medicine)
7. Website sources for every school-year not covered, and whether the old site used a CMS like Lotus Notes (§5.5)

Then build the coverage matrix before extracting anything.

**Console commands (Acalog sites).** List catalogs and IDs on the catalog archive page:
```js
copy([...new Map([...document.querySelectorAll('a[href*="catoid="]')]
  .map(a => [a.href.match(/catoid=(\d+)/)[1], a.textContent.trim()]))]
  .map(([id, name]) => `${id}\t${name}`).join('\n'))
```
List schools and navoids on any catalog page:
```js
(() => {
  const rows = [...new Map([...document.querySelectorAll('a[href*="navoid="]')]
    .filter(a => /School|College|Darden|Curry|McIntire|Batten/i.test(a.textContent))
    .map(a => [a.href.match(/navoid=(\d+)/)[1], a.textContent.trim().replace(/\s+/g, ' ')]))];
  console.log(rows.length ? rows.map(r => r.join('\t')).join('\n') : 'No school links found on this page');
})();
```

---

## 11. Time log

| Phase / step | Time spent | Notes |
|---|---|---|
| Phase 0: catalog enumeration | | |
| Phase 0: faculty listing checks | | |
| Phase 0: school lists | | |
| Phase 1: layout A page saving (80 pages) | | |
| Phase 1: layout A parser development and review | | |
| Phase 1: layouts B and C page saving (106 pages) | | |
| Phase 1: layouts B and C review | | |
| Phase 2: Law Wayback discovery | | |
