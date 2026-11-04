# UCL graduate job tracker (pharma management & strategy consulting)

**Open [`TRACKER.md`](TRACKER.md)** for the readable view.

| File | What it is |
|---|---|
| `TRACKER.md` | Readable dashboard: new finds, live postings, and the target-firm list ranked A/B/C. Rebuilt on every run. |
| `tracker.csv` | Every live posting found. Edit `status` (new / applying / applied / interview / offer / rejected / closed / ignore) and `notes`. The scraper never overwrites your edits. |
| `targets.csv` | Curated firms and programmes (healthcare strategy boutiques, strategy consulting, Big 4, pharma commercial/supply chain) with salary, 2027-cycle status and notes. |
| `scraper.py` | The checker. Filters, sources and keywords are at the top of the file. |
| `state/source_health.md` | Which sources worked on the last run. If one says FAILED, its careers URL has changed. |

## How the "every 8 hours" check works
1. **GitHub Actions** (`.github/workflows/check_jobs.yml`) runs `scraper.py` at 00:17, 08:17 and 16:17 UTC.
   * It queries the Workday, Greenhouse, Lever and SmartRecruiters job APIs for GSK, AstraZeneca, Pfizer, Novartis, Takeda, J&J, MSD, BMS, Haleon, Sanofi, IQVIA, Teneo, ClearView, Putnam, Lumanity and others. It keeps graduate and entry-level roles in consulting, strategy, commercial, market access, supply chain and clinical operations, and drops anything senior, data, lab, economics, internship or placement, plus any role advertised below £35k.
   * It **watches** the own-site careers pages of firms that don't advertise anywhere else, such as Cairneagle, Teneo, Candesic, the GSK and AZ early-careers pages, and Novo Nordisk. If the job-related text changes, it logs a `PAGE CHANGED` row with the new text.
   * When anything new appears it **opens a GitHub issue**, and GitHub notifies you by email or in the app. Turn on "Watch → All activity" on the repo to get these.
2. **A Claude scheduled task** also runs every 8 hours. It web-searches for new postings the APIs can't see (for example on job boards and LinkedIn reposts), updates `targets.csv` and `tracker.csv`, and pushes.

> GitHub only runs scheduled workflows from the **default branch (`main`)**, so this branch has to be merged into `main` before the schedule starts.

Run it manually: Actions tab → *Check jobs* → *Run workflow*.
