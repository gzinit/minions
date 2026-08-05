"""Orchestrate job fetching, AI summarization, storage, and report generation."""

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


def format_job_entry(job: Dict[str, Any]) -> str:
    tech_stack = ", ".join(job.get("tech_stack") or []) or "N/A"
    responsibilities = job.get("core_responsibilities") or []
    responsibility_lines = "\n".join(f"  - {item}" for item in responsibilities)

    return f"""#### [{job.get('title', 'Untitled')}]({job.get('url', '#')}) @ {job.get('company', 'Unknown')}

- **Match Score:** {job.get('match_score', 'N/A')}/10
- **Work Type:** {job.get('work_location_type', 'N/A')}
- **Relocation Friendly:** {job.get('relocation_friendly', 'N/A')}
- **Location:** {job.get('location', 'N/A')}
- **Posted:** {job.get('posted_at', 'N/A')}
- **Visa / Relocation:** {job.get('visa_relocation_info', 'N/A')}
- **Tech Stack:** {tech_stack}
- **Match Reason:** {job.get('match_reason', 'N/A')}

**Core Responsibilities:**
{responsibility_lines or '  - N/A'}
"""


def render_country_section(country: str, jobs: List[Dict[str, Any]]) -> str:
    if not jobs:
        return ""
    lines = [f"### {country}", ""]
    for job in jobs:
        lines.append(format_job_entry(job))
        lines.append("")
    return "\n".join(lines)


def generate_report(
    jobs: List[Dict[str, Any]],
    countries: List[str],
    output_dir: Path,
    report_date: Optional[date] = None,
) -> Path:
    report_date = report_date or date.today()
    high_quality = [job for job in jobs if is_high_quality(job)]
    others = [job for job in jobs if job not in high_quality]

    hq_grouped = group_jobs_by_country(high_quality, countries)
    other_grouped = group_jobs_by_country(others, countries)

    country_order = countries + ["Other"]
    lines = [
        f"# LinkedIn Cloud/Infra Job Report — {report_date.isoformat()}",
        "",
        "## Summary",
        "",
        f"- **Total jobs in database:** {len(jobs)}",
        f"- **High-quality matches (score ≥ {MIN_HIGH_QUALITY_SCORE}, on-site/hybrid, relocation-friendly):** {len(high_quality)}",
        f"- **Other jobs:** {len(others)}",
        "",
        "## High-Quality Matches",
        "",
        f"Jobs with match score ≥ {MIN_HIGH_QUALITY_SCORE}, on-site or hybrid in target countries, with visa or relocation support.",
        "",
    ]

    has_high_quality = False
    for country in country_order:
        section = render_country_section(country, hq_grouped.get(country, []))
        if section:
            has_high_quality = True
            lines.append(section)

    if not has_high_quality:
        lines.append("_No high-quality matches found in this run._")
        lines.append("")

    lines.extend(["## Other Jobs", ""])
    has_others = False
    for country in country_order:
        section = render_country_section(country, other_grouped.get(country, []))
        if section:
            has_others = True
            lines.append(section)

    if not has_others:
        lines.append("_No other jobs in database._")
        lines.append("")

    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"report_{report_date.isoformat()}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
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
    return report_path


def main() -> None:
    run()


if __name__ == "__main__":
    main()
