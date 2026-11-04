"""
Real-browser crawl of careers pages (runs in GitHub Actions, which has full internet).

For every URL in browse_list.txt it opens the page in headless Chromium, waits for
JavaScript job lists to load, clicks common "accept cookies" / "load more" buttons,
and saves a snapshot to snapshots/<slug>.md:
  * every link whose text looks like a job (title + URL)
  * the visible page text (trimmed)
Claude then reads these snapshots to find and verify roles.

browse_list.txt format:  <label> | <url>      (lines starting with # are ignored)
"""
from __future__ import annotations

import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).parent
OUT = ROOT / "snapshots"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
JOBISH = re.compile(r"graduate|analyst|associate|consultant|intern|programme|program|trainee|"
                    r"strategy|commercial|market access|supply chain|marketing|operations|"
                    r"future leaders|2027|2028|junior|entry|early career|placement|business", re.I)
COOKIE_BUTTONS = ["Accept all", "Accept All", "Accept all cookies", "Accept All Cookies",
                  "Accept", "I agree", "Allow all", "Got it", "OK"]
MORE_BUTTONS = ["Load more", "Show more", "View more", "See more jobs", "More jobs"]


def slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:80]


def clean(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip()


def snapshot(browser, label: str, url: str) -> str:
    ctx = browser.new_context(user_agent=UA, locale="en-GB", viewport={"width": 1366, "height": 2000})
    page = ctx.new_page()
    status = "ok"
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        status = f"HTTP {resp.status if resp else '?'}"
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        for txt in COOKIE_BUTTONS:
            try:
                page.get_by_role("button", name=txt, exact=True).first.click(timeout=1200)
                break
            except Exception:
                continue
        for _ in range(3):
            clicked = False
            for txt in MORE_BUTTONS:
                try:
                    page.get_by_role("button", name=re.compile(txt, re.I)).first.click(timeout=1200)
                    clicked = True
                    page.wait_for_timeout(1500)
                except Exception:
                    continue
            if not clicked:
                break
        page.mouse.wheel(0, 20000)
        page.wait_for_timeout(2500)
        links = page.eval_on_selector_all(
            "a[href]", "els => els.map(e => [e.innerText || e.getAttribute('aria-label') || '', e.href])")
        text = page.inner_text("body")
        final = page.url
    except Exception as e:
        links, text, final = [], "", url
        status = f"FAILED {type(e).__name__}: {str(e)[:150]}"
    finally:
        ctx.close()

    seen, joblinks = set(), []
    for t, h in links:
        t = clean(t)
        if 6 <= len(t) <= 200 and JOBISH.search(t) and h not in seen and not h.startswith(("mailto:", "javascript:")):
            seen.add(h)
            joblinks.append(f"- {t} -> {urljoin(final, h)}")
    lines = [l for l in (clean(x) for x in text.split("\n")) if l]
    body = "\n".join(lines)[:12000]
    md = [f"# {label}", f"URL: {url}", f"Final URL: {final}", f"Status: {status}",
          f"Captured: {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}", "",
          f"## Job-like links ({len(joblinks)})", *joblinks[:250], "", "## Page text", body]
    (OUT / f"{slug(label)}.md").write_text("\n".join(md), encoding="utf-8")
    return f"{label}: {status}, {len(joblinks)} job-like links"


def main() -> int:
    from playwright.sync_api import sync_playwright
    OUT.mkdir(exist_ok=True)
    items = []
    for line in (ROOT / "browse_list.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "|" in line:
            label, url = (p.strip() for p in line.split("|", 1))
            items.append((label, url))

    results = []
    # 4 parallel browsers keeps a ~150-page crawl around 15 minutes.
    def worker(chunk):
        out = []
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            for label, url in chunk:
                out.append(snapshot(b, label, url))
            b.close()
        return out

    chunks = [items[i::4] for i in range(4)]
    with ThreadPoolExecutor(4) as ex:
        for r in ex.map(worker, chunks):
            results += r
    summary = ["# Crawl summary", f"Run: {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}", ""]
    summary += [f"- {r}" for r in sorted(results)]
    (OUT / "_summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print("\n".join(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
