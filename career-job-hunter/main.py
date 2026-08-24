"""Orchestrate job fetching, AI summarization, storage, and report generation."""

import html
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from ai_summarizer import summarize_job
from job_fetcher import DEFAULT_CONFIG_PATH, fetch_all_jobs, load_config
from storage import DEFAULT_DB_PATH, JobStorage

MIN_HIGH_QUALITY_SCORE = 7


def infer_country(location: str, countries: List[str]) -> str:
    location_lower = location.lower()
    for country in countries:
        if country.lower() in location_lower:
            return country
    return "Other"


def is_relocation_friendly(job: Dict[str, Any]) -> bool:
    if job.get("relocation_friendly"):
        return True
    info = job.get("visa_relocation_info", "").lower()
    return "visa sponsorship: mentioned" in info or "relocation support: mentioned" in info


def is_domestic_remote(job: Dict[str, Any]) -> bool:
    if job.get("work_location_type") == "Remote":
        return True
    return "[国内远程]" in str(job.get("match_reason", ""))


def is_high_quality(job: Dict[str, Any]) -> bool:
    score = job.get("match_score") or 0
    if is_domestic_remote(job):
        return False
    if job.get("work_location_type") not in ("On-site", "Hybrid", None, ""):
        return False
    return score >= MIN_HIGH_QUALITY_SCORE and is_relocation_friendly(job)


def group_jobs_by_country(
    jobs: List[Dict[str, Any]], countries: List[str]
) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {country: [] for country in countries}
    grouped["Other"] = []

    for job in jobs:
        country = infer_country(job.get("location", ""), countries)
        grouped.setdefault(country, []).append(job)

    for country in grouped:
        grouped[country].sort(
            key=lambda item: (item.get("match_score") or 0, item.get("posted_at") or ""),
            reverse=True,
        )
    return grouped


def _esc(value: Any, fallback: str = "N/A") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return html.escape(text) if text else fallback


def _score_class(score: Any) -> str:
    try:
        numeric = float(score)
    except (TypeError, ValueError):
        return "score-na"
    if numeric >= MIN_HIGH_QUALITY_SCORE:
        return "score-high"
    if numeric >= 5:
        return "score-mid"
    return "score-low"


def format_job_entry(job: Dict[str, Any]) -> str:
    title = _esc(job.get("title"), "Untitled")
    company = _esc(job.get("company"), "Unknown")
    url = _esc(job.get("url") or "#", "#")
    score = job.get("match_score", "N/A")
    tech_items = job.get("tech_stack") or []
    tech_stack = ", ".join(_esc(item) for item in tech_items) if tech_items else "N/A"
    responsibilities = job.get("core_responsibilities") or []
    if responsibilities:
        responsibility_items = "\n".join(
            f"<li>{_esc(item)}</li>" for item in responsibilities
        )
    else:
        responsibility_items = "<li>N/A</li>"

    return f"""<article class="job-card">
  <h4><a href="{url}" target="_blank" rel="noopener noreferrer">{title}</a> <span class="company">@ {company}</span></h4>
  <ul class="meta">
    <li><strong>Match Score:</strong> <span class="score {_score_class(score)}">{_esc(score)}/10</span></li>
    <li><strong>Work Type:</strong> {_esc(job.get("work_location_type"))}</li>
    <li><strong>Relocation Friendly:</strong> {_esc(job.get("relocation_friendly"))}</li>
    <li><strong>Location:</strong> {_esc(job.get("location"))}</li>
    <li><strong>Posted:</strong> {_esc(job.get("posted_at"))}</li>
    <li><strong>Visa / Relocation:</strong> {_esc(job.get("visa_relocation_info"))}</li>
    <li><strong>Tech Stack:</strong> {tech_stack}</li>
    <li><strong>Match Reason:</strong> {_esc(job.get("match_reason"))}</li>
  </ul>
  <p class="responsibilities-label">Core Responsibilities</p>
  <ul class="responsibilities">
    {responsibility_items}
  </ul>
</article>"""


def render_country_section(country: str, jobs: List[Dict[str, Any]]) -> str:
    if not jobs:
        return ""
    cards = "\n".join(format_job_entry(job) for job in jobs)
    return f"""<section class="country">
  <h3>{_esc(country)}</h3>
  {cards}
</section>"""


REPORT_CSS = """
:root {
  --bg: #f4f6f8;
  --card: #ffffff;
  --text: #1f2933;
  --muted: #52606d;
  --border: #d9e2ec;
  --accent: #2563eb;
  --high: #059669;
  --mid: #d97706;
  --low: #dc2626;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.5;
}
.page {
  max-width: 880px;
  margin: 0 auto;
  padding: 32px 20px 64px;
}
header h1 { margin: 0 0 8px; font-size: 1.75rem; }
header .date { margin: 0; color: var(--muted); }
.summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin: 24px 0 32px;
}
.stat {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px 16px;
}
.stat .label { display: block; color: var(--muted); font-size: 0.85rem; }
.stat .value { font-size: 1.4rem; font-weight: 700; }
h2 { margin: 32px 0 8px; }
.hint { color: var(--muted); margin: 0 0 16px; }
.empty { color: var(--muted); font-style: italic; }
.country h3 {
  margin: 24px 0 12px;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--border);
}
.job-card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px 18px;
  margin-bottom: 14px;
}
.job-card h4 { margin: 0 0 10px; }
.job-card a { color: var(--accent); text-decoration: none; }
.job-card a:hover { text-decoration: underline; }
.company { color: var(--muted); font-weight: 500; }
.meta, .responsibilities { margin: 0; padding-left: 18px; }
.meta li { margin: 2px 0; }
.responsibilities-label { margin: 12px 0 6px; font-weight: 600; }
.score { font-weight: 700; }
.score-high { color: var(--high); }
.score-mid { color: var(--mid); }
.score-low { color: var(--low); }
.score-na { color: var(--muted); }
""".strip()


def generate_report(
    jobs: List[Dict[str, Any]],
    countries: List[str],
    output_dir: Path,
    report_date: Optional[date] = None,
) -> Path:
    report_date = report_date or date.today()
    report_iso = report_date.isoformat()
    high_quality = [job for job in jobs if is_high_quality(job)]
    others = [job for job in jobs if job not in high_quality]

    hq_grouped = group_jobs_by_country(high_quality, countries)
    other_grouped = group_jobs_by_country(others, countries)
    country_order = countries + ["Other"]

    hq_sections = [
        render_country_section(country, hq_grouped.get(country, []))
        for country in country_order
    ]
    hq_html = "\n".join(section for section in hq_sections if section)
    if not hq_html:
        hq_html = "<p class=\"empty\">No high-quality matches found in this run.</p>"

    other_sections = [
        render_country_section(country, other_grouped.get(country, []))
        for country in country_order
    ]
    other_html = "\n".join(section for section in other_sections if section)
    if not other_html:
        other_html = "<p class=\"empty\">No other jobs in database.</p>"

    document = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Career Job Hunter Report — {html.escape(report_iso)}</title>
  <style>
{REPORT_CSS}
  </style>
</head>
<body>
  <div class="page">
    <header>
      <h1>Career Job Hunter Report</h1>
      <p class="date">{html.escape(report_iso)}</p>
    </header>
    <section class="summary" aria-label="Summary">
      <div class="stat"><span class="label">Total jobs</span><span class="value">{len(jobs)}</span></div>
      <div class="stat"><span class="label">High-quality matches</span><span class="value">{len(high_quality)}</span></div>
      <div class="stat"><span class="label">Other jobs</span><span class="value">{len(others)}</span></div>
    </section>
    <h2>High-Quality Matches</h2>
    <p class="hint">Score ≥ {MIN_HIGH_QUALITY_SCORE}, on-site or hybrid in target countries, with visa or relocation support.</p>
    {hq_html}
    <h2>Other Jobs</h2>
    {other_html}
  </div>
</body>
</html>
"""

    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"report_{report_iso}.html"
    report_path.write_text(document, encoding="utf-8")
    return report_path


def run(
    config_path: Path = DEFAULT_CONFIG_PATH,
    db_path: Path = DEFAULT_DB_PATH,
    output_dir: Optional[Path] = None,
) -> Path:
    output_dir = output_dir or Path(__file__).resolve().parent

    config = load_config(config_path)
    storage = JobStorage(db_path)
    user_profile = config["user_profile"]
    search_params = config["search_params"]
    countries = search_params["locations"]

    print("=== Step 1: Fetching jobs ===")
    all_jobs = fetch_all_jobs(config_path)
    print(f"Fetched {len(all_jobs)} unique jobs.")

    new_jobs = [job for job in all_jobs if not storage.is_job_processed(job["job_id"])]
    skipped = len(all_jobs) - len(new_jobs)
    print(f"Skipping {skipped} already processed job(s).")
    print(f"New jobs to analyze: {len(new_jobs)}")

    print("\n=== Step 2: AI summarization ===")
    analyzed = 0
    failed = 0
    for index, job in enumerate(new_jobs, start=1):
        if not job.get("description", "").strip():
            print(f"[{index}/{len(new_jobs)}] Skipping (no description): {job.get('title', 'Unknown')}")
            continue

        print(f"[{index}/{len(new_jobs)}] Analyzing: {job.get('title', 'Unknown')}")
        try:
            summary = summarize_job(
                description=job["description"],
                user_profile=user_profile,
                job_title=job.get("title", ""),
                company=job.get("company", ""),
                location=job.get("location", ""),
                search_params=search_params,
            )
            storage.save_job({**job, **summary})
            analyzed += 1
        except Exception as exc:
            failed += 1
            print(f"  Failed to analyze job {job.get('job_id')}: {exc}")

    print(f"\nAnalyzed {analyzed} job(s), {failed} failure(s).")

    print("\n=== Step 3: Generating report ===")
    all_stored_jobs = storage.list_jobs()
    report_path = generate_report(all_stored_jobs, countries, output_dir)
    print(f"Report written to {report_path}")
    print(f"Open in browser: open {report_path}")
    return report_path


def main() -> None:
    run()


if __name__ == "__main__":
    main()
