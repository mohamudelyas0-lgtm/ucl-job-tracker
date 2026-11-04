"""
Graduate job tracker for a UCL MSc Biotech & Pharma Management profile.

Checks a hardcoded list of careers pages, keeps roles whose titles match the
target keywords (and none of the excluded ones), and appends new roles to
tracker.csv. Existing rows are never modified, so you can edit the `status`
and `notes` columns by hand and they'll be preserved.

Two kinds of source are supported:
  * "workday" - firms on Workday ATS (e.g. GSK). Queried through Workday's
                public JSON search endpoint, which is far more reliable than
                scraping the rendered page.
  * "html"    - any other careers page. Rendered with Playwright (so
                JavaScript-built job lists load), then parsed with
                BeautifulSoup; every link whose text looks like a job title
                is considered.

Usage:  python scraper.py
"""

from __future__ import annotations

import csv
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

TRACKER = Path(__file__).with_name("tracker.csv")
FIELDS = ["date_found", "company", "title", "location", "url", "source", "status", "notes"]

# --------------------------------------------------------------------------
# Target firms. Careers sites move around - if a source logs 0 links or an
# error in the Actions run, open the URL in a browser and update it here.
# --------------------------------------------------------------------------
TARGETS = [
    # --- Pharma ---
    {"company": "GSK", "type": "workday",
     "tenant": "gsk", "host": "gsk.wd5.myworkdayjobs.com", "site": "GSKCareers"},
    {"company": "GSK (Early Careers)", "type": "html",
     "url": "https://www.gsk.com/en-gb/careers/early-careers/"},
    {"company": "AstraZeneca", "type": "html",
     "url": "https://careers.astrazeneca.com/early-talent"},

    # --- Strategy / advisory ---
    {"company": "Teneo", "type": "html", "url": "https://www.teneo.com/careers/"},
    {"company": "Cairneagle", "type": "html", "url": "https://www.cairneagle.com/careers"},
    {"company": "ZS Associates", "type": "html", "url": "https://jobs.zs.com/jobs?keywords=london"},
    {"company": "IQVIA", "type": "html",
     "url": "https://jobs.iqvia.com/en/search-jobs?k=graduate&l=United+Kingdom"},

    # --- Big 4 graduate portals (UK) ---
    {"company": "Deloitte", "type": "html",
     "url": "https://www.deloitte.com/uk/en/careers/students.html"},
    {"company": "PwC", "type": "html",
     "url": "https://www.pwc.co.uk/careers/early-careers/graduate.html"},
    {"company": "EY", "type": "html",
     "url": "https://www.ey.com/en_uk/careers/students/programmes/graduate"},
    {"company": "KPMG", "type": "html",
     "url": "https://www.kpmgcareers.co.uk/graduate/"},
]

# --------------------------------------------------------------------------
# Title filters (case-insensitive, whole-word/phrase match)
# --------------------------------------------------------------------------
INCLUDE = ["graduate", "consultant", "strategy", "commercial", "supply chain"]

EXCLUDE = [
    "data analyst",
    "biochemist",
    "economics", "economist",
    # Generic retail / rental graduate schemes
    "enterprise", "rent-a-car", "retail", "store", "aldi", "lidl",
]

# Workday searches are global; drop roles clearly outside the UK.
# Applied only when a location is known. Set to [] to disable.
LOCATION_KEYWORDS = ["uk", "united kingdom", "london", "england", "brentford",
                     "stevenage", "ware", "barnard castle", "locations"]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _phrase_re(words: list[str]) -> re.Pattern:
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.I)


INCLUDE_RE = _phrase_re(INCLUDE)
EXCLUDE_RE = _phrase_re(EXCLUDE)
LOCATION_RE = _phrase_re(LOCATION_KEYWORDS) if LOCATION_KEYWORDS else None


def title_matches(title: str) -> bool:
    return bool(INCLUDE_RE.search(title)) and not EXCLUDE_RE.search(title)


def location_ok(location: str) -> bool:
    return not location or LOCATION_RE is None or bool(LOCATION_RE.search(location))


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


# --------------------------------------------------------------------------
# Source adapters - each returns a list of {"title", "location", "url"}
# --------------------------------------------------------------------------
def scrape_workday(t: dict) -> list[dict]:
    api = f"https://{t['host']}/wday/cxs/{t['tenant']}/{t['site']}/jobs"
    base = f"https://{t['host']}/{t['site']}"
    jobs: dict[str, dict] = {}
    for term in INCLUDE:
        for offset in range(0, 100, 20):  # first 100 hits per term
            r = requests.post(
                api,
                json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": term},
                headers={"User-Agent": UA, "Accept": "application/json"},
                timeout=30,
            )
            r.raise_for_status()
            postings = r.json().get("jobPostings", [])
            for p in postings:
                url = base + p.get("externalPath", "")
                jobs[url] = {"title": clean(p.get("title", "")),
                             "location": clean(p.get("locationsText", "")),
                             "url": url}
            if len(postings) < 20:
                break
    return list(jobs.values())


def scrape_html(t: dict, browser) -> list[dict]:
    page = browser.new_page(user_agent=UA)
    try:
        page.goto(t["url"], wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except PWTimeout:
            pass  # some sites never go idle (analytics beacons); use what we have
        html = page.content()
        final_url = page.url
    finally:
        page.close()

    soup = BeautifulSoup(html, "html.parser")
    jobs: dict[str, dict] = {}
    for a in soup.find_all("a", href=True):
        title = clean(a.get_text(" "))
        if not (6 <= len(title) <= 140):
            continue
        href = a["href"]
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = urljoin(final_url, href)
        jobs.setdefault(url, {"title": title, "location": "", "url": url})
    return list(jobs.values())


# --------------------------------------------------------------------------
# Tracker I/O
# --------------------------------------------------------------------------
def load_tracker() -> list[dict]:
    if not TRACKER.exists() or TRACKER.stat().st_size == 0:
        return []
    with TRACKER.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_tracker(rows: list[dict]) -> None:
    with TRACKER.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def key(company: str, title: str, url: str) -> str:
    return url.lower().rstrip("/") or f"{company}|{title}".lower()


def main() -> int:
    rows = load_tracker()
    seen = {key(r["company"], r["title"], r["url"]) for r in rows}
    seen |= {f"{r['company']}|{r['title']}".lower() for r in rows}
    today = date.today().isoformat()
    new_rows: list[dict] = []
    failures = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for t in TARGETS:
            company = t["company"]
            try:
                found = scrape_workday(t) if t["type"] == "workday" else scrape_html(t, browser)
            except Exception as e:  # one broken site shouldn't kill the run
                failures += 1
                print(f"[ERROR] {company}: {type(e).__name__}: {e}", file=sys.stderr)
                continue

            matched = [j for j in found if title_matches(j["title"]) and location_ok(j["location"])]
            added = 0
            for j in matched:
                k = key(company, j["title"], j["url"])
                ct = f"{company}|{j['title']}".lower()
                if k in seen or ct in seen:
                    continue
                seen.update({k, ct})
                new_rows.append({
                    "date_found": today, "company": company, "title": j["title"],
                    "location": j["location"], "url": j["url"],
                    "source": t.get("url") or t.get("host"), "status": "new", "notes": "",
                })
                added += 1
            print(f"[OK] {company}: {len(found)} links, {len(matched)} matched, {added} new")
        browser.close()

    if new_rows:
        save_tracker(rows + new_rows)
    print(f"\nDone: {len(new_rows)} new role(s) added, {failures} source(s) failed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
