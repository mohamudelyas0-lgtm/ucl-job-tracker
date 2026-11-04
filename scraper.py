"""
Graduate job tracker for a UCL MSc Biotech & Pharmaceutical Management profile.

Targets: graduate / entry-level roles paying >= £35k in management or strategy
consulting (healthcare / life sciences first), Big 4 strategy and consulting, and
pharma commercial, market access, supply chain and clinical-operations schemes.
Avoids data-analyst, lab-science, pure-economics and generic retail schemes.

Each run (every 8h via GitHub Actions) it:
  1. Queries job-board APIs (Workday, Greenhouse, Lever, SmartRecruiters) and
     rendered careers pages for matching roles, and appends NEW ones to
     tracker.csv. Existing rows are never changed, so your `status` / `notes`
     edits are kept.
  2. "Watches" careers pages of firms that don't use job boards (Cairneagle,
     Teneo, Candesic, ...). When the job-related text on a page changes, it logs
     a "PAGE CHANGED" row so you catch roles that are only posted on the firm's
     own site (the GSK 16-hour posting problem).
  3. Writes TRACKER.md (readable view) and new_roles.md (just this run's finds,
     used by the workflow to open a GitHub issue = phone/email notification).

Usage:  python scraper.py            (needs internet; run in GitHub Actions)
        python scraper.py --render   (only rebuild TRACKER.md, no network)
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).parent
TRACKER = ROOT / "tracker.csv"
TARGETS_CSV = ROOT / "targets.csv"
STATE = ROOT / "state" / "page_state.json"
HEALTH = ROOT / "state" / "source_health.md"
TRACKER_MD = ROOT / "TRACKER.md"
NEW_MD = ROOT / "new_roles.md"

FIELDS = ["date_found", "company", "title", "location", "salary", "url",
          "source", "status", "notes"]
MIN_SALARY = 35_000

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# --------------------------------------------------------------------------
# SOURCES. If one shows FAILED in state/source_health.md, the firm has moved
# its careers site - open it in a browser and update the entry.
#   workday:  host + tenant + site from the myworkdayjobs.com URL
#   greenhouse: board token from job-boards.greenhouse.io/<token>
#   lever:    company from jobs.lever.co/<company>
#   smartrecruiters: company id from jobs.smartrecruiters.com/<id>
#   html:     any page - job-looking links are extracted
#   watch:    page is fingerprinted; any change in job-related text is flagged
# `any_location: True` keeps non-UK hits (global programmes such as Novo's).
# --------------------------------------------------------------------------
SOURCES = [
    # ---------------- Pharma (Workday JSON API - reliable) ----------------
    {"company": "GSK", "type": "workday", "host": "gsk.wd5.myworkdayjobs.com",
     "tenant": "gsk", "site": "GSKCareers"},
    {"company": "AstraZeneca", "type": "workday", "host": "astrazeneca.wd3.myworkdayjobs.com",
     "tenant": "astrazeneca", "site": "Careers"},
    {"company": "Pfizer", "type": "workday", "host": "pfizer.wd1.myworkdayjobs.com",
     "tenant": "pfizer", "site": "PfizerCareers"},
    {"company": "Novartis", "type": "workday", "host": "novartis.wd3.myworkdayjobs.com",
     "tenant": "novartis", "site": "Novartis_Careers"},
    {"company": "Takeda", "type": "workday", "host": "takeda.wd3.myworkdayjobs.com",
     "tenant": "takeda", "site": "External"},
    {"company": "Johnson & Johnson", "type": "workday", "host": "jj.wd5.myworkdayjobs.com",
     "tenant": "jj", "site": "JJ"},
    {"company": "MSD", "type": "workday", "host": "msd.wd5.myworkdayjobs.com",
     "tenant": "msd", "site": "SearchJobs"},
    {"company": "Bristol Myers Squibb", "type": "workday",
     "host": "bristolmyerssquibb.wd5.myworkdayjobs.com", "tenant": "bristolmyerssquibb",
     "site": "BMS"},
    {"company": "Haleon", "type": "workday", "host": "haleon.wd3.myworkdayjobs.com",
     "tenant": "haleon", "site": "Haleon_Careers"},
    {"company": "Sanofi", "type": "workday", "host": "sanofi.wd3.myworkdayjobs.com",
     "tenant": "sanofi", "site": "SanofiCareers"},
    {"company": "IQVIA", "type": "workday", "host": "iqvia.wd1.myworkdayjobs.com",
     "tenant": "iqvia", "site": "IQVIA"},
    {"company": "Alira Health", "type": "workday", "host": "alirahealth.wd3.myworkdayjobs.com",
     "tenant": "alirahealth", "site": "AliraHealth"},

    # ---------------- Consulting on Greenhouse / Lever ----------------
    {"company": "Teneo", "type": "greenhouse", "token": "teneolinkedin"},
    {"company": "ClearView Healthcare Partners", "type": "greenhouse",
     "token": "clearviewhealthcarepartners"},
    {"company": "Putnam (Inizio Advisory)", "type": "greenhouse", "token": "putnamassociates"},
    {"company": "Health Advances", "type": "greenhouse", "token": "healthadvances"},
    {"company": "Trinity Life Sciences", "type": "greenhouse", "token": "trinitylifesciences"},
    {"company": "Lumanity", "type": "greenhouse", "token": "lumanity"},
    {"company": "Simon-Kucher", "type": "greenhouse", "token": "simonkucher"},
    {"company": "Candesic", "type": "lever", "company_id": "candesic"},
    {"company": "Novo Nordisk", "type": "smartrecruiters", "company_id": "NovoNordisk",
     "any_location": True},

    # ---------------- Careers pages (job links extracted) ----------------
    {"company": "ZS Associates", "type": "html", "url": "https://jobs.zs.com/jobs?keywords=london"},
    {"company": "OC&C Strategy", "type": "html", "url": "https://careers.occstrategy.com/vacancies"},
    {"company": "L.E.K. Consulting", "type": "html", "url": "https://www.lek.com/join-lek/search-jobs"},
    {"company": "Accenture", "type": "html", "url": "https://accenture.pinpointhq.com/"},
    {"company": "PA Consulting", "type": "html", "url": "https://www.paconsulting.com/careers/jobs"},
    {"company": "Syneos Health Consulting", "type": "html",
     "url": "https://www.syneoshealth.com/careers/search/consulting/jobs"},
    {"company": "Deloitte", "type": "html", "url": "https://www.deloitte.com/uk/en/careers/students.html"},
    {"company": "PwC / Strategy&", "type": "html",
     "url": "https://www.pwc.co.uk/careers/early-careers/graduate.html"},
    {"company": "KPMG", "type": "html",
     "url": "https://www.kpmgcareers.co.uk/search/vacancies/?searchText=Graduate&intakeType=Student"},

    # ---------------- Watched pages (unadvertised / own-site only) --------
    {"company": "Cairneagle", "type": "watch",
     "url": "https://www.cairneagle.com/work-with-us/graduate-hires/"},
    {"company": "Teneo", "type": "watch", "url": "https://www.teneo.com/careers/open-positions/"},
    {"company": "Candesic", "type": "watch", "url": "https://candesic.com/careers/"},
    {"company": "Carnall Farrar", "type": "watch", "url": "https://www.carnallfarrar.com/careers/"},
    {"company": "GSK (Early Careers)", "type": "watch",
     "url": "https://www.gsk.com/en-gb/careers/early-careers/graduate-programme/"},
    {"company": "AstraZeneca (Early Talent)", "type": "watch",
     "url": "https://careers.astrazeneca.com/early-talent"},
    {"company": "Novo Nordisk (Graduate)", "type": "watch",
     "url": "https://www.novonordisk.com/careers/early-career-programmes/graduate.html"},
    {"company": "Takeda UK (Graduate)", "type": "watch",
     "url": "https://www.takeda.com/en-gb/careers/graduate-internship-programmes/"},
    {"company": "Pfizer UK (Early Careers)", "type": "watch",
     "url": "https://www.pfizer.co.uk/careers/early-careers"},
    {"company": "EY-Parthenon / EY", "type": "watch",
     "url": "https://www.ey.com/en_uk/careers/students/programmes/graduate"},
]

# --------------------------------------------------------------------------
# Title filters (case-insensitive). A title must hit a ROLE word AND a LEVEL
# word (or come from a source where every role is relevant) and no EXCLUDE.
# --------------------------------------------------------------------------
ROLE = [
    "consultant", "consulting", "strategy", "strategic", "commercial",
    "market access", "marketing", "brand", "launch", "pricing",
    "supply chain", "procurement", "operations", "planning", "logistics",
    "clinical operations", "clinical study", "clinical trial", "clinical delivery",
    "study delivery", "study management", "global clinical",
    "business", "management", "transformation", "portfolio", "advisory",
    "due diligence", "future leaders", "leadership development",
    "life sciences", "life science", "healthcare", "health care", "pharma", "pharmaceutical", "biotech",
]
LEVEL = [
    "graduate", "grad", "2027", "2028", "associate", "analyst", "entry level",
    "entry-level", "junior", "early career", "early careers", "programme",
    "program", "trainee", "future leaders", "rotational", "leadership development",
    "new grad", "register your interest", "intern", "internship", "summer associate",
    "master", "masters", "trainee",
]
EXCLUDE = [
    # seniority
    "senior", "sr", "director", "head", "manager", "lead", "principal",
    "vice president", "vp", "partner", "executive director", "expert", "specialist ii",
    # off-target families
    "data analyst", "data analytics", "data scientist", "data science", "data engineer",
    "analytics", "statistician", "statistics", "biostatistic", "scientist",
    "chemist", "biochemist", "laboratory", "lab", "technician", "engineer", "engineering",
    "economist", "economics", "econometric", "health economics", "heor", "modeller",
    "evidence review", "medical writer", "pharmacovigilance", "regulatory affairs",
    "finance", "accountant", "audit", "tax", "actuar", "software", "developer", "it ",
    "sales representative", "territory", "nurse", "pharmacist", "cleaner",
    # wrong stage
    "placement", "industrial placement", "year in industry", "apprentice",
    "apprenticeship", "phd", "postdoc", "work experience", "insight",
    # generic retail/management schemes the user doesn't want
    "enterprise", "rent-a-car", "retail", "store", "aldi", "lidl",
    # Teneo PR-side roles
    "communications", "financial communications", "political", "crisis", "campaigning",
]
UK_WORDS = ["uk", "united kingdom", "london", "england", "scotland", "wales", "great britain",
            "gb", "brentford", "stevenage", "ware", "barnard castle", "cambridge",
            "macclesfield", "luton", "welwyn", "reading", "high wycombe", "weybridge",
            "tadworth", "basingstoke", "maidenhead", "slough", "uxbridge",
            # abroad where a UK graduate can realistically apply
            "switzerland", "basel", "zurich", "zug", "geneva", "denmark", "copenhagen",
            "bagsvaerd", "bagsværd", "ireland", "dublin", "cork", "netherlands", "amsterdam",
            "germany", "munich", "berlin", "frankfurt", "belgium", "brussels", "france", "paris",
            "united arab emirates", "dubai", "abu dhabi"]
MULTI_LOC = re.compile(r"^\d+\s+locations?$|multiple", re.I)


def _rx(words):
    return re.compile(r"(?<![a-z])(" + "|".join(re.escape(w.strip()) for w in words) + r")(?![a-z])", re.I)


ROLE_RX, LEVEL_RX, EXCLUDE_RX, UK_RX = _rx(ROLE), _rx(LEVEL), _rx(EXCLUDE), _rx(UK_WORDS)
SALARY_RX = re.compile(r"£\s?(\d{2,3})(?:,(\d{3})|\.(\d)?\s?k|k)", re.I)


def title_ok(title: str) -> bool:
    return bool(ROLE_RX.search(title) and LEVEL_RX.search(title) and not EXCLUDE_RX.search(title))


def location_ok(loc: str, src: dict) -> bool:
    if src.get("any_location") or not loc or MULTI_LOC.search(loc):
        return True
    return bool(UK_RX.search(loc))


def parse_salary(text: str) -> tuple[str, int | None]:
    """Return ('£xx,xxx-£yy,yyy', highest value) from free text, if any."""
    vals = []
    for m in SALARY_RX.finditer(text or ""):
        if m.group(2):
            vals.append(int(m.group(1)) * 1000 + int(m.group(2)))
        else:
            vals.append(int(m.group(1)) * 1000)
    vals = [v for v in vals if 15_000 <= v <= 200_000]
    if not vals:
        return "", None
    lo, hi = min(vals), max(vals)
    label = f"£{lo:,}" if lo == hi else f"£{lo:,}-£{hi:,}"
    return label, hi


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(text or "")).strip()


# --------------------------------------------------------------------------
# Adapters: each returns [{"title", "location", "url", "salary_text"}]
# --------------------------------------------------------------------------
def http():
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json"})
    return s


def scrape_workday(src, s):
    api = f"https://{src['host']}/wday/cxs/{src['tenant']}/{src['site']}/jobs"
    base = f"https://{src['host']}/{src['site']}"
    jobs = {}
    for term in ["graduate", "graduate programme", "future leaders", "associate consultant",
                 "commercial", "market access", "supply chain", "clinical", "strategy", "2027"]:
        for offset in range(0, 60, 20):
            r = s.post(api, json={"appliedFacets": {}, "limit": 20, "offset": offset,
                                  "searchText": term}, timeout=30)
            r.raise_for_status()
            posts = r.json().get("jobPostings", [])
            for p in posts:
                url = base + p.get("externalPath", "")
                jobs[url] = {"title": clean(p.get("title")), "location": clean(p.get("locationsText")),
                             "url": url, "salary_text": ""}
            if len(posts) < 20:
                break
    return list(jobs.values())


def scrape_greenhouse(src, s):
    r = s.get(f"https://boards-api.greenhouse.io/v1/boards/{src['token']}/jobs?content=true", timeout=30)
    r.raise_for_status()
    return [{"title": clean(j.get("title")), "location": clean((j.get("location") or {}).get("name")),
             "url": j.get("absolute_url", ""), "salary_text": clean(re.sub("<[^>]+>", " ", unescape(j.get("content", ""))))}
            for j in r.json().get("jobs", [])]


def scrape_lever(src, s):
    r = s.get(f"https://api.lever.co/v0/postings/{src['company_id']}?mode=json", timeout=30)
    r.raise_for_status()
    return [{"title": clean(j.get("text")), "location": clean((j.get("categories") or {}).get("location")),
             "url": j.get("hostedUrl", ""), "salary_text": clean(j.get("descriptionPlain", ""))}
            for j in r.json()]


def scrape_smartrecruiters(src, s):
    jobs = []
    for q in ["graduate", "graduate programme"]:
        r = s.get(f"https://api.smartrecruiters.com/v1/companies/{src['company_id']}/postings",
                  params={"q": q, "limit": 100}, timeout=30)
        r.raise_for_status()
        for j in r.json().get("content", []):
            loc = j.get("location") or {}
            jobs.append({"title": clean(j.get("name")),
                         "location": clean(", ".join(filter(None, [loc.get("city"), loc.get("country")]))),
                         "url": f"https://jobs.smartrecruiters.com/{src['company_id']}/{j.get('id')}",
                         "salary_text": ""})
    return jobs


def render(url, browser):
    from playwright.sync_api import TimeoutError as PWTimeout
    page = browser.new_page(user_agent=UA)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except PWTimeout:
            pass
        return page.content(), page.url
    finally:
        page.close()


def scrape_html(src, browser):
    from bs4 import BeautifulSoup
    html, final = render(src["url"], browser)
    soup = BeautifulSoup(html, "html.parser")
    jobs = {}
    for a in soup.find_all("a", href=True):
        title = clean(a.get_text(" "))
        href = a["href"]
        if not (8 <= len(title) <= 140) or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = urljoin(final, href)
        jobs.setdefault(url, {"title": title, "location": "", "url": url, "salary_text": ""})
    return list(jobs.values())


WATCH_LINE = re.compile(r"graduate|analyst|associate|consultant|apply|application|deadline|"
                        r"open|closed|2027|2028|intake|start date|programme|program|vacanc|role", re.I)


def watch_page(src, browser, state):
    """Fingerprint the job-related lines of a page; return added lines if changed."""
    from bs4 import BeautifulSoup
    html, _ = render(src["url"], browser)
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
        t.decompose()
    lines = {clean(l) for l in soup.get_text("\n").split("\n")}
    lines = sorted(l for l in lines if 6 <= len(l) <= 300 and WATCH_LINE.search(l)
                   and not re.search(r"cookie|privacy|©", l, re.I))
    digest = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    prev = state.get(src["url"])
    state[src["url"]] = {"hash": digest, "lines": lines[:400]}
    if prev is None or prev.get("hash") == digest:
        return None  # first sighting = baseline, or unchanged
    added = [l for l in lines if l not in set(prev.get("lines", []))]
    return added or ["(text removed / reworded - check the page)"]


# --------------------------------------------------------------------------
# Tracker I/O + rendering
# --------------------------------------------------------------------------
def load_csv(path):
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_tracker(rows):
    with TRACKER.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in FIELDS} for r in rows)


def md(text):
    return (text or "").replace("|", "/").replace("\n", " ")


def render_markdown(rows, new_rows, run_time):
    targets = load_csv(TARGETS_CSV)
    out = [f"# Graduate job tracker - Elyas Mohamud", "",
           f"_Last updated: {run_time}. Auto-refreshed every 8 hours. "
           f"Edit `status`/`notes` in `tracker.csv`; edit firms in `targets.csv`._", ""]

    out += [f"## New this run ({len(new_rows)})", ""]
    if new_rows:
        out += ["| Company | Role | Location | Salary | Link |", "|---|---|---|---|---|"]
        out += [f"| {md(r['company'])} | {md(r['title'])} | {md(r['location'])} | {md(r['salary'])} | "
                f"[open]({r['url']}) |" for r in new_rows]
    else:
        out.append("Nothing new since the last check.")
    out.append("")

    active = [r for r in rows if r.get("status", "new") not in ("rejected", "closed", "ignore")]
    active.sort(key=lambda r: r["date_found"], reverse=True)
    out += [f"## Live postings found ({len(active)})", "",
            "| Found | Company | Role | Location | Salary | Status | Link |",
            "|---|---|---|---|---|---|---|"]
    out += [f"| {r['date_found']} | {md(r['company'])} | {md(r['title'])} | {md(r['location'])} | "
            f"{md(r['salary'])} | {md(r['status'])} | [open]({r['url']}) |" for r in active]
    out.append("")

    for pri, label in [("A", "Priority A - best fit for the MSc"), ("B", "Priority B - strong backups"),
                       ("C", "Priority C - long shots / borderline")]:
        group = [t for t in targets if t["priority"] == pri]
        if not group:
            continue
        out += [f"## Target firms: {label} ({len(group)})", "",
                "| Firm | Programme | Salary | 2027 cycle status | Window | Notes |", "|---|---|---|---|---|---|"]
        out += [f"| [{md(t['company'])}]({t['careers_url']}) | {md(t['programme'])} | {md(t['salary_gbp'])} "
                f"({md(t['salary_basis'])}) | {md(t['status_2027_cycle'])} | {md(t['typical_window'])} | "
                f"{md(t['why_fit_and_notes'])} |" for t in group]
        out.append("")
    TRACKER_MD.write_text("\n".join(out), encoding="utf-8")


def write_health(results):
    HEALTH.parent.mkdir(exist_ok=True)
    lines = ["# Source health (last run)", "", "| Source | Type | Result |", "|---|---|---|"]
    lines += [f"| {c} | {t} | {md(r)} |" for c, t, r in results]
    HEALTH.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
def main(argv) -> int:
    run_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rows = load_csv(TRACKER)
    if "--render" in argv:
        render_markdown(rows, [], run_time)
        return 0

    from playwright.sync_api import sync_playwright

    today = run_time[:10]
    seen = {r["url"].lower().rstrip("/") for r in rows if r.get("url")}
    seen |= {f"{r['company']}|{r['title']}".lower() for r in rows}
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    new_rows, health = [], []
    s = http()

    def add(company, title, location, url, source, salary="", notes=""):
        k1, k2 = url.lower().rstrip("/"), f"{company}|{title}".lower()
        if k1 in seen or k2 in seen:
            return False
        seen.update({k1, k2})
        new_rows.append({"date_found": today, "company": company, "title": title,
                         "location": location, "salary": salary, "url": url,
                         "source": source, "status": "new", "notes": notes})
        return True

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for src in SOURCES:
            company, kind = src["company"], src["type"]
            label = src.get("url") or src.get("host") or src.get("token") or src.get("company_id")
            try:
                if kind == "watch":
                    added = watch_page(src, browser, state)
                    if added:
                        snippet = " / ".join(added[:5])[:400]
                        add(company, f"PAGE CHANGED - check careers page ({today})", "",
                            src["url"] + f"#changed-{today}", src["url"], notes=snippet)
                        health.append((company, kind, f"CHANGED: {snippet[:120]}"))
                    else:
                        health.append((company, kind, "OK (no change)"))
                    continue

                found = {"workday": scrape_workday, "greenhouse": scrape_greenhouse,
                         "lever": scrape_lever, "smartrecruiters": scrape_smartrecruiters}[kind](src, s) \
                    if kind != "html" else scrape_html(src, browser)
                n = 0
                for j in found:
                    if not (title_ok(j["title"]) and location_ok(j["location"], src)):
                        continue
                    sal_label, sal_hi = parse_salary(j.get("salary_text", ""))
                    if sal_hi is not None and sal_hi < MIN_SALARY:
                        continue  # even the top of the advertised range is under £35k
                    n += add(company, j["title"], j["location"], j["url"], label, sal_label)
                health.append((company, kind, f"OK - {len(found)} listings, {n} new"))
            except Exception as e:  # one broken site shouldn't stop the run
                health.append((company, kind, f"FAILED: {type(e).__name__}: {str(e)[:100]}"))
                print(f"[ERROR] {company}: {e}", file=sys.stderr)
        browser.close()

    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
    rows += new_rows
    save_tracker(rows)
    write_health(health)
    render_markdown(rows, new_rows, run_time)

    if new_rows:
        body = [f"{len(new_rows)} new role(s) / page change(s) found at {run_time}:", ""]
        body += [f"- **{r['company']}** - {r['title']} {('(' + r['location'] + ')') if r['location'] else ''}"
                 f" {r['salary']} - {r['url']}" + (f"\n  > {r['notes']}" if r['notes'] else "")
                 for r in new_rows]
        NEW_MD.write_text("\n".join(body) + "\n", encoding="utf-8")
    elif NEW_MD.exists():
        NEW_MD.unlink()

    for c, t, r in health:
        print(f"[{t}] {c}: {r}")
    print(f"\nDone: {len(new_rows)} new.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
