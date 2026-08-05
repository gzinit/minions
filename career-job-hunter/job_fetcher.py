"""Fetch jobs with single-request bulk strategy, local filtering, and quota protection."""

import json
import os
import random
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")
SEARCH_MODE_ENV = "SEARCH_MODE"
FULL_MODE = "FULL"
HTTP_TIMEOUT = 30
APIFY_DEFAULT_TIMEOUT = 300
LINKEDIN_JOB_ID_PATTERN = re.compile(r"jobs/view/(\d+)")
LINKEDIN_SEARCH_BASE = "https://www.linkedin.com/jobs/search/"
REQUESTS_PER_RUN = 1
LINKEDIN_WORK_TYPE_CODES = {
    "on_site": "1",
    "hybrid": "3",
    "remote": "2",
}
REMOTE_EXCLUSION_PATTERNS = [
    r"remote from anywhere",
    r"work from (?:your )?home country",
    r"work remotely from anywhere",
    r"fully remote",
    r"100%\s*remote",
    r"remote worldwide",
    r"anywhere in the world",
]

RAPIDAPI_FIELD_MAPPING: Dict[str, List[str]] = {
    "job_id": ["linkedin_id", "id", "job_id"],
    "title": ["title"],
    "company": ["organization", "company"],
    "location": ["location", "locations_derived"],
    "url": ["url", "link"],
    "description": ["description_text", "description_html", "description"],
    "posted_at": ["date_posted", "date_created", "posted_at"],
}

APIFY_FIELD_MAPPING: Dict[str, List[str]] = {
    "job_id": ["id", "jobId", "linkedin_id", "job_id"],
    "title": ["title"],
    "company": ["companyName", "company", "organization"],
    "location": ["location", "jobLocation"],
    "url": ["link", "jobUrl", "url", "applyUrl"],
    "description": ["descriptionText", "descriptionHtml", "description"],
    "posted_at": ["postedAt", "posted_date", "datePosted", "posted_at"],
}


class ProviderRetryableError(Exception):
    """Raised when a data source should failover to the next provider."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RapidAPIQuotaTracker:
    """Track monthly RapidAPI request usage in a local JSON file."""

    def __init__(self, quota_path: Path, monthly_limit: int = 20) -> None:
        self.quota_path = Path(quota_path)
        self.monthly_limit = monthly_limit

    def _month_key(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m")

    def _load(self) -> Dict[str, Any]:
        if not self.quota_path.exists():
            return {}
        with open(self.quota_path, encoding="utf-8") as f:
            return json.load(f)

    def _save(self, data: Dict[str, Any]) -> None:
        with open(self.quota_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def get_monthly_count(self) -> int:
        data = self._load()
        month_data = data.get(self._month_key(), {})
        return int(month_data.get("count", 0))

    def can_request(self) -> bool:
        return self.get_monthly_count() < self.monthly_limit

    def remaining(self) -> int:
        return max(0, self.monthly_limit - self.get_monthly_count())

    def record_request(self, keyword: str, location: str, raw_count: int) -> None:
        data = self._load()
        month_key = self._month_key()
        month_data = data.get(month_key, {"count": 0, "requests": []})
        month_data["count"] = int(month_data.get("count", 0)) + 1
        month_data.setdefault("requests", []).append(
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "keyword": keyword,
                "location": location,
                "raw_jobs": raw_count,
            }
        )
        data[month_key] = month_data
        self._save(data)


def load_config(config_path: Path = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


def get_search_mode() -> str:
    return os.environ.get(SEARCH_MODE_ENV, "").strip().upper()


def is_full_search_mode() -> bool:
    return get_search_mode() == FULL_MODE


def get_apify_config(config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    apify_cfg = config.get("apify") or config.get("apify_config")
    return apify_cfg if isinstance(apify_cfg, dict) else None


def build_work_type_filter(search_params: Dict[str, Any]) -> str:
    """Build LinkedIn f_WT filter from search_params work type preferences."""
    allow_remote = bool(search_params.get("allow_remote", False))
    work_types = search_params.get("work_types") or ["on_site", "hybrid"]

    codes: List[str] = []
    for work_type in work_types:
        code = LINKEDIN_WORK_TYPE_CODES.get(work_type)
        if code:
            codes.append(code)

    if not allow_remote:
        codes = [code for code in codes if code != LINKEDIN_WORK_TYPE_CODES["remote"]]
        if not codes:
            codes = [
                LINKEDIN_WORK_TYPE_CODES["on_site"],
                LINKEDIN_WORK_TYPE_CODES["hybrid"],
            ]

    unique_codes = sorted(set(codes), key=codes.index)
    return ",".join(unique_codes)


def build_linkedin_search_url(
    keyword: str,
    location: str,
    search_params: Optional[Dict[str, Any]] = None,
) -> str:
    """Build a LinkedIn public job search URL for curious_coder/linkedin-jobs-scraper."""
    encoded_keyword = urllib.parse.quote(keyword, safe="")
    encoded_location = urllib.parse.quote(location, safe="")
    url = (
        f"{LINKEDIN_SEARCH_BASE}?keywords={encoded_keyword}&location={encoded_location}"
    )

    if search_params is not None:
        work_type_filter = build_work_type_filter(search_params)
        if work_type_filter:
            url = f"{url}&f_WT={work_type_filter}"

    return url


def resolve_search_combinations(
    search_params: Dict[str, Any],
) -> Tuple[List[Tuple[str, str]], str]:
    """Build keyword/location pairs based on SEARCH_MODE."""
    all_keywords = search_params["keywords"]
    locations = search_params["locations"]
    core_keywords = search_params.get("core_keywords") or all_keywords

    if is_full_search_mode():
        combinations = [
            (keyword, location)
            for location in locations
            for keyword in all_keywords
        ]
        return combinations, "FULL"

    keyword = random.choice(core_keywords)
    location = random.choice(locations)
    return [(keyword, location)], "SAVE"


def build_search_urls(search_params: Dict[str, Any]) -> Tuple[List[str], List[Tuple[str, str]], str]:
    combinations, mode_label = resolve_search_combinations(search_params)
    urls = [
        build_linkedin_search_url(keyword, location, search_params)
        for keyword, location in combinations
    ]
    return urls, combinations, mode_label


def resolve_env_vars(value: str) -> str:
    return ENV_VAR_PATTERN.sub(
        lambda match: os.environ.get(match.group(1), ""), value
    )


def resolve_headers(headers: Dict[str, str]) -> Dict[str, str]:
    return {key: resolve_env_vars(str(value)) for key, value in headers.items()}


def build_quota_tracker(config: Dict[str, Any]) -> RapidAPIQuotaTracker:
    rapidapi_cfg = config.get("rapidapi_config") or {}
    quota_file = rapidapi_cfg.get("quota_file", "rapidapi_quota.json")
    quota_path = Path(__file__).resolve().parent / quota_file
    monthly_limit = int(rapidapi_cfg.get("monthly_request_limit", 20))
    return RapidAPIQuotaTracker(quota_path, monthly_limit=monthly_limit)


def _actor_id_to_path(actor_id: str) -> str:
    return actor_id.strip().replace("/", "~")


def _extract_linkedin_job_id(url: str) -> Optional[str]:
    match = LINKEDIN_JOB_ID_PATTERN.search(url)
    return match.group(1) if match else None


def _get_nested_value(data: Any, path: str) -> Any:
    current = data
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _pick_value(raw: Dict[str, Any], field: str, mapping: Dict[str, List[str]]) -> Any:
    for key in mapping.get(field, [field]):
        value = raw.get(key)
        if value is not None and value != "":
            return value
    return None


def _format_location(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value or "")


def normalize_job(
    raw: Dict[str, Any],
    field_mapping: Optional[Dict[str, List[str]]] = None,
) -> Optional[Dict[str, str]]:
    mapping = field_mapping or RAPIDAPI_FIELD_MAPPING
    job_id = _pick_value(raw, "job_id", mapping)
    url = str(_pick_value(raw, "url", mapping) or "")

    if job_id is None and url:
        job_id = _extract_linkedin_job_id(url)
    if job_id is None:
        return None

    location_value = _pick_value(raw, "location", mapping)
    description = _pick_value(raw, "description", mapping)
    if not description:
        description = raw.get("descriptionHtml") or raw.get("description_html") or ""

    return {
        "job_id": str(job_id),
        "title": str(_pick_value(raw, "title", mapping) or ""),
        "company": str(_pick_value(raw, "company", mapping) or ""),
        "location": _format_location(location_value),
        "url": url,
        "description": str(description),
        "posted_at": str(_pick_value(raw, "posted_at", mapping) or ""),
    }


def extract_jobs_from_payload(
    payload: Any,
    jobs_path: Optional[str] = None,
    field_mapping: Optional[Dict[str, List[str]]] = None,
) -> List[Dict[str, str]]:
    if jobs_path:
        items = _get_nested_value(payload, jobs_path)
    elif isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = None
        for key in ("jobs", "results", "data", "items"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                items = candidate
                break
        if items is None:
            raise ValueError("Could not find jobs array in API response.")
    else:
        raise ValueError(f"Unexpected API response type: {type(payload)!r}")

    if not isinstance(items, list):
        raise ValueError("Resolved jobs payload is not a list.")

    jobs: List[Dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        job = normalize_job(item, field_mapping)
        if job:
            jobs.append(job)
    return jobs


def compile_filter_patterns(search_params: Dict[str, Any]) -> List[re.Pattern[str]]:
    local_filter = search_params.get("local_filter") or {}
    patterns = local_filter.get("patterns") or [
        "kubernetes",
        "k8s",
        "devops",
        "terraform",
        "cloud infrastructure",
        "platform engineer",
        "site reliability",
        r"\bsre\b",
        "aws",
        "gcp",
    ]
    return [re.compile(str(pattern), re.IGNORECASE) for pattern in patterns]


def exclude_remote_jobs_locally(
    jobs: List[Dict[str, str]],
    search_params: Dict[str, Any],
) -> List[Dict[str, str]]:
    """Drop obvious work-from-home-country / fully-remote jobs when remote is disabled."""
    if search_params.get("allow_remote", False):
        return jobs

    patterns = [re.compile(pattern, re.IGNORECASE) for pattern in REMOTE_EXCLUSION_PATTERNS]
    kept: List[Dict[str, str]] = []
    for job in jobs:
        text = f"{job.get('title', '')} {job.get('description', '')} {job.get('location', '')}"
        if any(pattern.search(text) for pattern in patterns):
            continue
        kept.append(job)
    return kept


def filter_jobs_locally(
    jobs: List[Dict[str, str]],
    search_params: Dict[str, Any],
) -> List[Dict[str, str]]:
    """Secondary filter on fetched jobs — zero additional network cost."""
    local_filter = search_params.get("local_filter") or {}
    match_fields = local_filter.get("match_fields") or ["title", "description"]
    patterns = compile_filter_patterns(search_params)

    filtered: List[Dict[str, str]] = []
    for job in jobs:
        text = " ".join(str(job.get(field, "")) for field in match_fields)
        if any(pattern.search(text) for pattern in patterns):
            filtered.append(job)
    return filtered


def build_data_sources(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    primary = str(config.get("primary_source", "apify")).strip().lower()
    apify_cfg = get_apify_config(config)
    rapidapi_cfg = config.get("rapidapi_config")

    apify_source = {"name": "apify", "type": "apify", "config": apify_cfg} if apify_cfg else None
    rapidapi_source = (
        {"name": "rapidapi", "type": "rapidapi", "config": rapidapi_cfg}
        if rapidapi_cfg
        else None
    )

    if primary == "rapidapi":
        ordered = [rapidapi_source, apify_source]
    else:
        ordered = [apify_source, rapidapi_source]

    sources = [source for source in ordered if source is not None]
    if not sources:
        raise ValueError(
            "config.json must include at least one of apify or rapidapi_config."
        )
    return sources


def _apify_token_env_var(source: Dict[str, Any]) -> str:
    return str(source["config"].get("token_env_var", "APIFY_TOKEN"))


def _rapidapi_required_env_vars(source: Dict[str, Any]) -> List[str]:
    headers = source["config"].get("headers") or {}
    vars_found: List[str] = []
    for value in headers.values():
        vars_found.extend(ENV_VAR_PATTERN.findall(str(value)))
    return vars_found


def source_is_ready(source: Dict[str, Any]) -> bool:
    if source["type"] == "apify":
        token_var = _apify_token_env_var(source)
        return bool(os.environ.get(token_var, "").strip())

    if source["type"] == "rapidapi":
        for var in _rapidapi_required_env_vars(source):
            if not os.environ.get(var, "").strip():
                return False
        return True

    return False


def get_ready_sources(
    config: Dict[str, Any],
    quota_tracker: Optional[RapidAPIQuotaTracker] = None,
) -> List[Dict[str, Any]]:
    sources = [source for source in build_data_sources(config) if source_is_ready(source)]

    if quota_tracker is None:
        return sources

    filtered: List[Dict[str, Any]] = []
    for source in sources:
        if source["type"] == "rapidapi" and not quota_tracker.can_request():
            print(
                f"[Warning] RapidAPI monthly quota reached "
                f"({quota_tracker.get_monthly_count()}/{quota_tracker.monthly_limit}). "
                "Skipping RapidAPI and switching to fallback source if available."
            )
            continue
        filtered.append(source)
    return filtered


def _is_quota_or_rate_limit(status_code: Optional[int], body: str) -> bool:
    if status_code == 429:
        return True
    body_lower = body.lower()
    return "quota" in body_lower or "rate limit" in body_lower


def _should_failover(status_code: Optional[int], body: str) -> bool:
    if status_code is None:
        return True
    if status_code in (402, 429) or status_code >= 500:
        return True
    return _is_quota_or_rate_limit(status_code, body)


def _http_request(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    body: Optional[Dict[str, Any]] = None,
    timeout: int = HTTP_TIMEOUT,
) -> Any:
    payload_bytes = None
    request_headers = dict(headers or {})
    if body is not None:
        payload_bytes = json.dumps(body).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")

    request = urllib.request.Request(
        url,
        data=payload_bytes,
        headers=request_headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else []
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        if _should_failover(exc.code, body_text):
            raise ProviderRetryableError(
                f"HTTP {exc.code}: {body_text}", exc.code
            ) from exc
        raise RuntimeError(f"HTTP {exc.code}: {body_text}") from exc
    except urllib.error.URLError as exc:
        reason = str(exc.reason)
        if isinstance(exc.reason, socket.timeout) or "timed out" in reason.lower():
            raise ProviderRetryableError(f"Network timeout: {reason}") from exc
        raise ProviderRetryableError(f"Network error: {reason}") from exc


def build_apify_run_input(
    apify_config: Dict[str, Any],
    urls: List[str],
) -> Dict[str, Any]:
    """Build run_input matching curious_coder/linkedin-jobs-scraper schema."""
    if not urls:
        raise ValueError("Apify run_input requires at least one LinkedIn search URL.")

    count = int(apify_config.get("count_per_search", 20))
    scrape_company = bool(apify_config.get("scrape_company", False))

    run_input: Dict[str, Any] = {
        "urls": urls,
        "count": count,
        "scrapeCompany": scrape_company,
    }

    if "split_by_location" in apify_config:
        run_input["splitByLocation"] = bool(apify_config["split_by_location"])

    run_input.update(apify_config.get("extra_input") or {})
    return run_input


def fetch_from_apify(
    apify_config: Dict[str, Any],
    urls: List[str],
) -> List[Dict[str, str]]:
    actor_id = apify_config.get("actor_id", "curious_coder/linkedin-jobs-scraper")
    token_var = apify_config.get("token_env_var", "APIFY_TOKEN")
    token = os.environ.get(token_var, "").strip()
    if not token:
        raise ValueError(f"Missing Apify token in environment variable {token_var!r}.")

    timeout_secs = int(apify_config.get("timeout_secs", APIFY_DEFAULT_TIMEOUT))
    actor_path = _actor_id_to_path(actor_id)
    query = urllib.parse.urlencode(
        {
            "token": token,
            "timeout": str(timeout_secs),
            "format": "json",
        }
    )
    api_url = (
        f"https://api.apify.com/v2/acts/{actor_path}/run-sync-get-dataset-items"
        f"?{query}"
    )
    run_input = build_apify_run_input(apify_config, urls)
    payload = _http_request(
        api_url,
        method="POST",
        body=run_input,
        timeout=timeout_secs + 30,
    )
    field_mapping = apify_config.get("field_mapping") or APIFY_FIELD_MAPPING
    return extract_jobs_from_payload(payload, field_mapping=field_mapping)


def build_rapidapi_params(
    rapidapi_config: Dict[str, Any],
    keyword: str,
    location: str,
    limit: int,
    time_frame: str,
) -> Dict[str, str]:
    provider_params = rapidapi_config.get("params") or {}
    params = {
        "title": keyword,
        "location": location,
        "limit": str(limit),
        "time_frame": time_frame,
        "description_format": "text",
        "source": "linkedin",
    }
    params.update({key: str(value) for key, value in provider_params.items()})
    return params


def fetch_from_rapidapi(
    rapidapi_config: Dict[str, Any],
    keyword: str,
    location: str,
    limit: int = 1000,
    time_frame: str = "7d",
) -> List[Dict[str, str]]:
    endpoint = rapidapi_config.get("endpoint", "").strip()
    if not endpoint:
        raise ValueError("rapidapi_config.endpoint is required.")

    headers = resolve_headers(rapidapi_config.get("headers") or {})
    params = build_rapidapi_params(rapidapi_config, keyword, location, limit, time_frame)
    url = f"{endpoint}?{urllib.parse.urlencode(params)}"

    payload = _http_request(url, headers=headers)
    field_mapping = rapidapi_config.get("field_mapping") or RAPIDAPI_FIELD_MAPPING
    jobs_path = rapidapi_config.get("response_jobs_path")
    return extract_jobs_from_payload(payload, jobs_path, field_mapping)


def _log_source_failover(source_name: str) -> None:
    print(
        f"[Warning] API {source_name} 已达额度限制/报错，"
        "自动切换至下一个备用 API..."
    )


def fetch_single_bulk(
    sources: Sequence[Dict[str, Any]],
    search_urls: List[str],
    combinations: Sequence[Tuple[str, str]],
    fetch_limit: int,
    time_frame: str,
) -> Tuple[List[Dict[str, str]], Optional[str]]:
    """Send at most one successful network request across all sources."""
    if not sources:
        print("  No ready data sources.")
        return [], None

    if not combinations:
        print("  No search combinations resolved.")
        return [], None

    keyword, location = combinations[0]
    last_error: Optional[str] = None

    for index, source in enumerate(sources):
        name = str(source.get("name", source.get("type", "unknown")))

        try:
            if source["type"] == "apify":
                jobs = fetch_from_apify(source["config"], search_urls)
            elif source["type"] == "rapidapi":
                jobs = fetch_from_rapidapi(
                    source["config"],
                    keyword,
                    location,
                    limit=fetch_limit,
                    time_frame=time_frame,
                )
            else:
                raise ValueError(f"Unsupported data source type: {source['type']!r}")

            print(f"  Source {name!r} returned {len(jobs)} raw job(s) in 1 request")
            return jobs, name
        except ProviderRetryableError as exc:
            last_error = str(exc)
            _log_source_failover(name)
            if index == len(sources) - 1:
                print(f"  All sources exhausted: {exc}")
        except Exception as exc:
            last_error = str(exc)
            _log_source_failover(name)
            if index == len(sources) - 1:
                print(f"  All sources exhausted: {exc}")

    if last_error:
        print("  Bulk fetch failed for current search target(s).")
    return [], None


def _missing_credentials_hint(config: Dict[str, Any]) -> str:
    hints: List[str] = []
    for source in build_data_sources(config):
        if source["type"] == "apify":
            hints.append(_apify_token_env_var(source))
        elif source["type"] == "rapidapi":
            hints.extend(_rapidapi_required_env_vars(source))
    return ", ".join(sorted(set(hints)))


def fetch_all_jobs(config_path: Path = DEFAULT_CONFIG_PATH) -> List[Dict[str, str]]:
    config = load_config(config_path)
    quota_tracker = build_quota_tracker(config)
    all_sources = build_data_sources(config)
    sources = get_ready_sources(config, quota_tracker)

    if not sources:
        print(
            f"[Error] No data source is ready. Set environment variable(s): "
            f"{_missing_credentials_hint(config)}"
        )
        return []

    search_params = config["search_params"]
    search_urls, combinations, mode_label = build_search_urls(search_params)
    fetch_limit = int(search_params.get("fetch_limit_per_request", 1000))
    time_frame = search_params.get("time_frame", "7d")
    primary_source = config.get("primary_source", "apify")
    apify_cfg = get_apify_config(config) or {}

    ready_names = [source.get("name", source.get("type")) for source in sources]
    print("Strategy: single bulk request per run (maximize jobs, minimize API calls)")
    print(f"Primary source: {primary_source}")
    print(f"Search mode: {mode_label}")
    print(f"LinkedIn search URLs: {len(search_urls)}")
    for url in search_urls[:3]:
        print(f"  - {url}")
    if len(search_urls) > 3:
        print(f"  ... and {len(search_urls) - 3} more")
    if apify_cfg:
        allow_remote = search_params.get("allow_remote", False)
        print(
            "Apify run_input: "
            f"count={apify_cfg.get('count_per_search', 20)}, "
            f"scrapeCompany={apify_cfg.get('scrape_company', False)}, "
            f"allow_remote={allow_remote}, "
            f"f_WT={build_work_type_filter(search_params)}"
        )
    print(f"Failover order: {' -> '.join(str(name) for name in ready_names)}")
    print(f"RapidAPI quota: {quota_tracker.get_monthly_count()}/{quota_tracker.monthly_limit} used this month")

    if len(sources) < len(all_sources):
        print("  Note: some configured sources were skipped due to missing credentials or quota.")

    print(f"Sending {REQUESTS_PER_RUN} API request...")
    raw_jobs, used_source = fetch_single_bulk(
        sources,
        search_urls,
        combinations,
        fetch_limit=fetch_limit,
        time_frame=time_frame,
    )

    if used_source == "rapidapi" and raw_jobs and combinations:
        keyword, location = combinations[0]
        quota_tracker.record_request(keyword, location, len(raw_jobs))
        print(
            f"RapidAPI quota updated: "
            f"{quota_tracker.get_monthly_count()}/{quota_tracker.monthly_limit} "
            f"({quota_tracker.remaining()} remaining)"
        )

    if not raw_jobs:
        print("[Error] Bulk fetch returned no jobs.")
        return []

    print(f"Local filtering {len(raw_jobs)} raw job(s) (no extra network requests)...")
    cloud_jobs = filter_jobs_locally(raw_jobs, search_params)
    filtered_jobs = exclude_remote_jobs_locally(cloud_jobs, search_params)
    print(
        f"Local filter matched {len(filtered_jobs)}/{len(raw_jobs)} "
        "Cloud/Infra on-site/hybrid job(s)."
    )

    seen: Dict[str, Dict[str, str]] = {}
    for job in filtered_jobs:
        seen[job["job_id"]] = job

    return list(seen.values())


if __name__ == "__main__":
    results = fetch_all_jobs()
    print(f"\nFetched {len(results)} unique jobs after local filtering.")
    print(json.dumps(results[:3], indent=2, ensure_ascii=False))
