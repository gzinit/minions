"""Summarize and score job postings using a local Ollama LLM."""

import json
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "deepseek-r1:8b"

SUMMARY_SCHEMA = {
    "tech_stack": ["Kubernetes", "AWS", "Terraform", "Go"],
    "visa_relocation_info": (
        "Visa Sponsorship: mentioned | not mentioned; "
        "Relocation Support: mentioned | not mentioned"
    ),
    "work_location_type": "On-site",
    "relocation_friendly": True,
    "core_responsibilities": [
        "Responsibility 1",
        "Responsibility 2",
        "Responsibility 3",
    ],
    "match_score": 8,
    "match_reason": "Brief explanation of the score.",
}

DOMESTIC_REMOTE_PATTERNS = [
    re.compile(r"remote from anywhere", re.IGNORECASE),
    re.compile(r"work from (?:your )?home country", re.IGNORECASE),
    re.compile(r"work remotely from anywhere", re.IGNORECASE),
    re.compile(r"fully remote", re.IGNORECASE),
    re.compile(r"100%\s*remote", re.IGNORECASE),
]


def build_prompt(
    description: str,
    user_profile: Dict[str, Any],
    job_title: str = "",
    company: str = "",
    location: str = "",
    search_params: Optional[Dict[str, Any]] = None,
) -> str:
    search_params = search_params or {}
    profile_text = json.dumps(user_profile, indent=2, ensure_ascii=False)
    schema_text = json.dumps(SUMMARY_SCHEMA, indent=2, ensure_ascii=False)
    target_countries = ", ".join(search_params.get("locations") or [])
    allow_remote = bool(search_params.get("allow_remote", False))
    target_relocation = bool(search_params.get("target_relocation", True))

    relocation_goal = (
        "The candidate wants to relocate and work ON-SITE or HYBRID in the target country "
        f"({target_countries}). They do NOT want to work remotely from their current home country."
    )
    if not allow_remote:
        relocation_goal += (
            " Pure Remote / work-from-home-country roles must be penalized."
        )

    return f"""You are a senior Cloud/Infrastructure hiring analyst specializing in Kubernetes, DevOps, SRE, and Platform Engineering roles.

{relocation_goal}

Candidate profile:
{profile_text}

Job metadata:
- Title: {job_title or "N/A"}
- Company: {company or "N/A"}
- Location: {location or "N/A"}
- Target relocation: {target_relocation}
- Allow remote roles: {allow_remote}

Job description:
{description}

Return ONLY a valid JSON object (no markdown, no commentary) with exactly these fields:

1. tech_stack: array of key technologies mentioned in the JD (e.g. Kubernetes, AWS, GCP, Terraform, Docker, Go, Python, CI/CD, Linux, Observability tools). Use canonical names. Empty array if none found.

2. visa_relocation_info: a single string that explicitly states whether the job description mentions:
   - "Visa Sponsorship"
   - "Relocation Support"
   Use "mentioned", "not mentioned", or "unclear" for each item.

3. work_location_type: one of exactly "On-site", "Hybrid", or "Remote" based on the job description.

4. relocation_friendly: boolean. true if the role supports relocating to the target country (Visa Sponsorship, Relocation Support, or must be located in target country with relocation assistance). false for remote-from-home-country roles.

5. core_responsibilities: array of at most 3 concise bullet points summarizing the core duties. Focus on Cloud/Infra/K8s/platform work.

6. match_score: integer from 1 to 10. Scoring criteria:
   - Skill overlap with candidate's K8s/Infra/DevOps stack (Go, Python, Kubernetes, Terraform, CI/CD, cloud platforms)
   - Role fit for Cloud Engineer / DevOps / SRE / Platform Engineer
   - HIGH SCORE (8-10): On-site or Hybrid in target country, with Visa Sponsorship or Relocation Support, must be located in target country but relocation/visa is offered
   - LOW SCORE (1-4): "Remote from anywhere", "Work from your home country", or similar domestic remote arrangements — prefix match_reason with "[国内远程]"
   - MEDIUM SCORE (5-7): unclear relocation/visa, or hybrid with weak relocation support

7. match_reason: one or two sentences explaining the match_score. If the role is remote-from-home-country, start with "[国内远程]".

Example output shape:
{schema_text}
"""


def _strip_thinking(text: str) -> str:
    patterns = []
    for tag in ("think", "redacted_thinking"):
        open_tag = f"<{tag}>"
        close_tag = f"</{tag}>"
        patterns.append(re.escape(open_tag) + r".*?" + re.escape(close_tag))

    cleaned = text
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.DOTALL)
    return cleaned.strip()


def _extract_json(text: str) -> Dict[str, Any]:
    cleaned = _strip_thinking(text.strip())

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        return json.loads(match.group())

    raise ValueError(f"Could not parse JSON from model response: {text[:500]}")


def _normalize_work_location_type(value: Any) -> str:
    normalized = str(value or "On-site").strip().lower().replace("_", "-")
    mapping = {
        "on-site": "On-site",
        "onsite": "On-site",
        "hybrid": "Hybrid",
        "remote": "Remote",
    }
    return mapping.get(normalized, "On-site")


def _looks_like_domestic_remote(description: str, match_reason: str) -> bool:
    text = f"{description} {match_reason}"
    return any(pattern.search(text) for pattern in DOMESTIC_REMOTE_PATTERNS)


def _normalize_summary(
    raw: Dict[str, Any],
    description: str = "",
    search_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    search_params = search_params or {}
    allow_remote = bool(search_params.get("allow_remote", False))

    tech_stack = raw.get("tech_stack") or []
    if not isinstance(tech_stack, list):
        tech_stack = [str(tech_stack)]

    responsibilities = raw.get("core_responsibilities") or []
    if not isinstance(responsibilities, list):
        responsibilities = [str(responsibilities)]
    responsibilities = [str(item) for item in responsibilities[:3]]

    work_location_type = _normalize_work_location_type(raw.get("work_location_type"))
    relocation_friendly = bool(raw.get("relocation_friendly", False))
    match_reason = str(raw.get("match_reason") or "")

    score = raw.get("match_score", 0)
    try:
        score = int(score)
    except (TypeError, ValueError):
        score = 0
    score = max(1, min(10, score))

    domestic_remote = (
        work_location_type == "Remote"
        or _looks_like_domestic_remote(description, match_reason)
        or "[国内远程]" in match_reason
    )

    if domestic_remote and not allow_remote:
        score = min(score, 4)
        if not match_reason.startswith("[国内远程]"):
            match_reason = f"[国内远程] {match_reason}".strip()

    return {
        "tech_stack": [str(item) for item in tech_stack],
        "visa_relocation_info": str(raw.get("visa_relocation_info") or ""),
        "work_location_type": work_location_type,
        "relocation_friendly": relocation_friendly,
        "core_responsibilities": responsibilities,
        "match_score": score,
        "match_reason": match_reason,
    }


def call_ollama(
    prompt: str,
    model: str = DEFAULT_MODEL,
    ollama_url: str = OLLAMA_URL,
    timeout: int = 120,
) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
    }
    request = urllib.request.Request(
        ollama_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama API error ({exc.code}): {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to reach Ollama at {ollama_url}: {exc.reason}") from exc

    response_text = result.get("response", "")
    if not response_text:
        raise RuntimeError(f"Empty response from Ollama: {result}")
    return response_text


def summarize_job(
    description: str,
    user_profile: Dict[str, Any],
    job_title: str = "",
    company: str = "",
    location: str = "",
    search_params: Optional[Dict[str, Any]] = None,
    model: str = DEFAULT_MODEL,
    ollama_url: str = OLLAMA_URL,
) -> Dict[str, Any]:
    prompt = build_prompt(
        description,
        user_profile,
        job_title,
        company,
        location,
        search_params=search_params,
    )
    response_text = call_ollama(prompt, model=model, ollama_url=ollama_url)
    raw_summary = _extract_json(response_text)
    return _normalize_summary(raw_summary, description=description, search_params=search_params)


def summarize_jobs(
    jobs: List[Dict[str, Any]],
    user_profile: Dict[str, Any],
    search_params: Optional[Dict[str, Any]] = None,
    model: str = DEFAULT_MODEL,
    ollama_url: str = OLLAMA_URL,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for index, job in enumerate(jobs):
        print(f"[{index + 1}/{len(jobs)}] Summarizing: {job.get('title', 'Unknown')}")
        summary = summarize_job(
            description=job.get("description", ""),
            user_profile=user_profile,
            job_title=job.get("title", ""),
            company=job.get("company", ""),
            location=job.get("location", ""),
            search_params=search_params,
            model=model,
            ollama_url=ollama_url,
        )
        results.append({**job, **summary})
    return results


if __name__ == "__main__":
    from job_fetcher import load_config

    sample_description = """
    We are looking for a Senior Platform Engineer to build and operate our Kubernetes platform on AWS
    in Berlin, Germany. Must be located in Germany. Visa sponsorship and relocation support available.
    """

    config = load_config()
    result = summarize_job(
        sample_description,
        config["user_profile"],
        job_title="Senior Platform Engineer",
        location="Berlin, Germany",
        search_params=config["search_params"],
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
