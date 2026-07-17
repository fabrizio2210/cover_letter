from __future__ import annotations

import argparse
import json
import os
import re
import uuid
from collections import Counter
from dataclasses import asdict, dataclass

from src.python.ai_scorer.job_fingerprint import description_fingerprint, stable_json_hash
from src.python.ai_scorer.training.dataset_split import (
    DEFAULT_PROMOTION_FIXTURES,
    DEFAULT_SPLIT_MANIFEST,
    file_sha256,
    load_golden_fingerprints,
    load_split_manifest,
)
from src.python.ai_scorer.training.preferences import default_preferences_path, load_preferences
from src.python.ai_scorer.training.schema import TrainingCase, dump_cases, load_cases, validate_cases


DEFAULT_JOB_POOL = "src/python/ai_scorer/training/data/proposed/job-pool.json"
DEFAULT_PREFERENCES = default_preferences_path()
DEFAULT_OUTPUT = "src/python/ai_scorer/training/data/proposed/high-score-candidates.json"
DEFAULT_REPORT = "src/python/ai_scorer/training/data/proposed/high-score-candidate-report.json"
DEFAULT_EXCLUDE_CASES = "src/python/ai_scorer/training/data/proposed/labeled.json"
SELECTOR_VERSION = "seed-preference-high-score-v1"
CASE_ID_NAMESPACE = uuid.UUID("ad692171-eb26-58c1-96db-8b8a620cf920")


Pattern = tuple[str, re.Pattern[str], int]


def _patterns(items: list[tuple[str, str, int]]) -> tuple[Pattern, ...]:
    return tuple(
        (name, re.compile(pattern, re.IGNORECASE), weight)
        for name, pattern, weight in items
    )


def _negative(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


@dataclass(frozen=True)
class PreferenceHeuristic:
    title_patterns: tuple[Pattern, ...]
    evidence_patterns: tuple[Pattern, ...]
    negative_title: re.Pattern[str]
    min_title_score: int
    min_evidence_score: int


HEURISTICS: dict[str, PreferenceHeuristic] = {
    "pref_01_product_delivery": PreferenceHeuristic(
        title_patterns=_patterns(
            [
                ("product_engineer", r"\bproduct (?:software )?engineer\b", 15),
                ("product_platform", r"\bproduct platform\b", 13),
                ("product_manager", r"\bproduct manager\b", 12),
                ("software_engineer", r"\bsoftware engineer(?:ing)?\b", 7),
                ("engineering_manager", r"\bengineering manager\b", 7),
            ]
        ),
        evidence_patterns=_patterns(
            [
                ("product_delivery", r"\bproduct delivery\b|\bdeliver(?:ing)? products?\b", 12),
                ("ship_features", r"\bship(?:ping)? (?:new )?(?:products?|features?)\b", 10),
                ("roadmap", r"\bproduct roadmap\b|\broadmap execution\b", 8),
                ("end_to_end", r"\bend-to-end\b|\bfull (?:product )?lifecycle\b", 7),
                ("maintainability", r"\bmaintainab(?:ility|le)\b|\blong-term maintenance\b", 8),
                ("technical_debt", r"\btechnical debt\b|\bsustainable (?:software|systems?|solutions?)\b", 7),
                ("customer_value", r"\bcustomer value\b|\buser value\b|\bproduct outcomes?\b", 5),
                ("production_ownership", r"\bproduction ownership\b|\bown .* in production\b", 5),
            ]
        ),
        negative_title=_negative(r"\b(?:designer|sales|support|marketing|advisor|faculty)\b"),
        min_title_score=7,
        min_evidence_score=15,
    ),
    "pref_02_incident_response": PreferenceHeuristic(
        title_patterns=_patterns(
            [
                ("incident_response", r"\bincident response\b", 17),
                ("site_reliability", r"\bsite reliability\b|\bSRE\b", 15),
                ("production_engineer", r"\bproduction engineer\b", 13),
                ("reliability_engineer", r"\breliability engineer\b", 13),
                ("devops", r"\bdevops\b|\bdevsecops\b", 10),
                ("security_engineer", r"\bsecurity engineer\b", 8),
            ]
        ),
        evidence_patterns=_patterns(
            [
                ("incident_response", r"\bincident response\b|\brespond(?:ing)? to incidents?\b", 12),
                ("on_call", r"\bon-call\b|\bon call rotation\b", 10),
                ("production_incident", r"\bproduction incidents?\b|\bservice incidents?\b", 9),
                ("outage", r"\boutages?\b", 9),
                ("postmortem", r"\bpostmortems?\b|\bpost-incident reviews?\b", 9),
                ("root_cause", r"\broot cause\b|\bRCA\b", 7),
                ("remediation", r"\bremediation\b|\bprevent recurrence\b", 6),
                ("runbook", r"\brunbooks?\b|\boperational playbooks?\b", 5),
            ]
        ),
        negative_title=_negative(r"\b(?:designer|product|sales|marketing|advisor|faculty)\b"),
        min_title_score=8,
        min_evidence_score=17,
    ),
    "pref_03_architecture_ownership": PreferenceHeuristic(
        title_patterns=_patterns(
            [
                ("architect", r"\barchitect\b", 17),
                ("principal_engineer", r"\bprincipal (?:software )?engineer\b", 15),
                ("staff_engineer", r"\bstaff (?:software |platform |cloud )?engineer\b", 12),
                ("tech_lead", r"\btech(?:nical)? lead\b", 11),
                ("lead_engineer", r"\blead (?:software |platform |cloud )?engineer\b", 10),
            ]
        ),
        evidence_patterns=_patterns(
            [
                ("architecture", r"\b(?:system|software|technical|platform) architecture\b", 11),
                ("architect", r"\barchitect(?:ing|ed)?\b", 9),
                ("technical_direction", r"\btechnical direction\b|\btechnical strategy\b", 9),
                ("system_design", r"\bsystem design\b|\bdesign complex systems?\b", 8),
                ("design_decisions", r"\bdesign decisions?\b|\barchitectural decisions?\b", 8),
                ("ownership", r"\bend-to-end ownership\b|\bown the architecture\b", 8),
                ("tradeoffs", r"\btrade-?offs?\b", 6),
                ("standards", r"\btechnical standards?\b|\barchitecture standards?\b", 5),
            ]
        ),
        negative_title=_negative(r"\b(?:designer|product|sales|support|marketing|advisor|faculty)\b"),
        min_title_score=10,
        min_evidence_score=16,
    ),
    "pref_04_platform_reliability": PreferenceHeuristic(
        title_patterns=_patterns(
            [
                ("site_reliability", r"\bsite reliability\b|\bSRE\b", 16),
                ("reliability_engineer", r"\breliability engineer\b", 15),
                ("platform_engineer", r"\bplatform (?:software )?engineer\b", 13),
                ("infrastructure", r"\binfra(?:structure)? engineer\b", 11),
                ("cloud_platform", r"\bcloud platform\b", 10),
                ("devops", r"\bdevops\b|\bdevsecops\b", 9),
            ]
        ),
        evidence_patterns=_patterns(
            [
                ("slo", r"\bSLOs?\b|\bservice level objectives?\b", 12),
                ("reliability", r"\breliab(?:ility|le)\b", 10),
                ("availability", r"\bavailability\b|\buptime\b", 9),
                ("observability", r"\bobservability\b", 7),
                ("metrics", r"\bmetrics?\b|\bdata-driven\b|\bmeasurable outcomes?\b", 6),
                ("monitoring", r"\bmonitoring\b|\balerting\b", 6),
                ("resilience", r"\bresilien(?:ce|t)\b|\bfault toleran(?:ce|t)\b", 6),
                ("scale", r"\bscalab(?:ility|le)\b", 4),
            ]
        ),
        negative_title=_negative(r"\b(?:designer|product|sales|support|marketing|advisor|faculty)\b"),
        min_title_score=9,
        min_evidence_score=17,
    ),
    "pref_05_cross-team_communication": PreferenceHeuristic(
        title_patterns=_patterns(
            [
                ("engineering_manager", r"\bengineering manager\b", 15),
                ("technical_program_manager", r"\btechnical program manager\b", 14),
                ("staff_engineer", r"\bstaff (?:software |platform |cloud )?engineer\b", 11),
                ("principal_engineer", r"\bprincipal (?:software )?engineer\b", 11),
                ("tech_lead", r"\btech(?:nical)? lead\b|\blead engineer\b", 10),
                ("product_manager", r"\bproduct manager\b", 9),
            ]
        ),
        evidence_patterns=_patterns(
            [
                ("cross_team", r"\bcross-team\b|\bacross (?:multiple )?teams\b", 11),
                ("cross_functional", r"\bcross-functional\b", 10),
                ("stakeholders", r"\bstakeholders?\b", 7),
                ("partner", r"\bpartner(?:ing)? with\b|\bwork closely with\b", 6),
                ("communication", r"\bcommunicat(?:e|es|ing|ion)\b", 6),
                ("alignment", r"\balign(?:ment|ing)?\b", 5),
                ("engineering_standards", r"\bengineering standards?\b|\btechnical standards?\b", 8),
                ("quality", r"\bengineering quality\b|\bhigh-quality\b|\bquality bar\b", 6),
            ]
        ),
        negative_title=_negative(r"\b(?:designer|sales|marketing|advisor|faculty)\b"),
        min_title_score=9,
        min_evidence_score=16,
    ),
    "pref_06_mentorship": PreferenceHeuristic(
        title_patterns=_patterns(
            [
                ("engineering_manager", r"\bengineering manager\b", 17),
                ("manager_engineering", r"\bmanager,? (?:software )?engineering\b", 17),
                ("lead_engineer", r"\blead (?:software |platform |cloud )?engineer\b", 13),
                ("tech_lead", r"\btech(?:nical)? lead\b", 13),
                ("staff_engineer", r"\bstaff (?:software |platform |cloud )?engineer\b", 10),
                ("principal_engineer", r"\bprincipal (?:software )?engineer\b", 10),
            ]
        ),
        evidence_patterns=_patterns(
            [
                ("mentorship", r"\bmentor(?:ing|ship|s|ed)?\b", 13),
                ("coaching", r"\bcoach(?:ing|es|ed)?\b", 11),
                ("grow_engineers", r"\bgrow (?:and develop )?engineers?\b|\bdevelop engineers?\b", 9),
                ("career_growth", r"\bcareer (?:growth|development)\b", 7),
                ("feedback", r"\bfeedback\b", 6),
                ("accountability", r"\baccountab(?:ility|le)\b", 7),
                ("ownership", r"\bclear ownership\b|\bownership culture\b", 6),
                ("hiring", r"\bhiring\b|\binterviewing\b", 4),
            ]
        ),
        negative_title=_negative(r"\b(?:designer|product|sales|marketing|advisor|faculty)\b"),
        min_title_score=10,
        min_evidence_score=15,
    ),
    "pref_07_api_design": PreferenceHeuristic(
        title_patterns=_patterns(
            [
                ("api", r"\bAPI\b", 17),
                ("backend", r"\bback[ -]?end\b", 14),
                ("integration", r"\bintegration(?:s)? engineer\b", 11),
                ("platform_engineer", r"\bplatform (?:software )?engineer\b", 9),
                ("software_engineer", r"\bsoftware engineer\b", 8),
            ]
        ),
        evidence_patterns=_patterns(
            [
                ("api_design", r"\bAPI design\b|\bdesign(?:ing)? APIs?\b", 13),
                ("api", r"\bAPIs?\b", 7),
                ("interfaces", r"\binterfaces?\b", 7),
                ("contracts", r"\bAPI contracts?\b|\bservice contracts?\b", 8),
                ("rest", r"\bREST(?:ful)?\b", 7),
                ("graphql", r"\bGraphQL\b|\bgRPC\b", 7),
                ("versioning", r"\bversion(?:ed|ing) APIs?\b|\bbackward compatibility\b", 7),
                ("sdk", r"\bSDKs?\b", 5),
                ("maintainability", r"\bmaintainab(?:ility|le)\b", 5),
            ]
        ),
        negative_title=_negative(
            r"\b(?:designer|product|sales|support|marketing|advisor|faculty|frontend|front-end|mobile)\b"
        ),
        min_title_score=8,
        min_evidence_score=18,
    ),
    "pref_08_performance_tuning": PreferenceHeuristic(
        title_patterns=_patterns(
            [
                ("performance", r"\bperformance engineer\b|\bperformance engineering\b", 18),
                ("backend", r"\bback[ -]?end\b", 11),
                ("systems_engineer", r"\bsystems engineer\b", 10),
                ("site_reliability", r"\bsite reliability\b|\bSRE\b", 9),
                ("database", r"\bdatabase engineer\b|\bstorage engineer\b", 9),
                ("platform_engineer", r"\bplatform (?:software )?engineer\b", 8),
            ]
        ),
        evidence_patterns=_patterns(
            [
                ("performance", r"\bperformance (?:tuning|optimization|engineering|improvements?)\b", 12),
                ("latency", r"\blatency\b", 10),
                ("throughput", r"\bthroughput\b", 9),
                ("optimize", r"\boptimi[sz](?:e|es|ed|ing|ation)\b", 8),
                ("profiling", r"\bprofil(?:e|es|ed|ing)\b", 8),
                ("bottleneck", r"\bbottlenecks?\b", 7),
                ("benchmark", r"\bbenchmarks?\b|\bbenchmarking\b", 7),
                ("scalability", r"\bscalab(?:ility|le)\b", 5),
                ("resource_efficiency", r"\bCPU\b|\bmemory usage\b|\bresource efficiency\b", 4),
            ]
        ),
        negative_title=_negative(r"\b(?:manager|director|designer|product|sales|support|marketing|advisor|faculty)\b"),
        min_title_score=8,
        min_evidence_score=16,
    ),
    "pref_09_data_pipelines": PreferenceHeuristic(
        title_patterns=_patterns(
            [
                ("data_engineer", r"\bdata engineer\b", 18),
                ("analytics_engineer", r"\banalytics engineer\b", 15),
                ("data_platform", r"\bdata platform\b", 14),
                ("data_infrastructure", r"\bdata infrastructure\b", 13),
                ("ml_engineer", r"\bmachine learning engineer\b|\bML engineer\b", 8),
            ]
        ),
        evidence_patterns=_patterns(
            [
                ("data_pipeline", r"\bdata pipelines?\b", 13),
                ("etl", r"\bETL\b|\bELT\b", 11),
                ("ingestion", r"\bdata ingestion\b|\bingestion pipelines?\b", 9),
                ("airflow", r"\bAirflow\b|\bDagster\b|\bPrefect\b", 9),
                ("dbt", r"\bdbt\b", 9),
                ("spark", r"\bSpark\b|\bFlink\b|\bKafka\b", 8),
                ("streaming", r"\bdata streaming\b|\bstream processing\b", 8),
                ("warehouse", r"\bdata warehouse\b|\bSnowflake\b|\bBigQuery\b", 7),
                ("orchestration", r"\bworkflow orchestration\b|\bdata orchestration\b", 7),
                ("maintainability", r"\bmaintainab(?:ility|le)\b", 4),
            ]
        ),
        negative_title=_negative(r"\b(?:manager|director|designer|product|sales|support|marketing|advisor|faculty)\b"),
        min_title_score=8,
        min_evidence_score=17,
    ),
    "pref_10_developer_tooling": PreferenceHeuristic(
        title_patterns=_patterns(
            [
                ("developer_experience", r"\bdeveloper experience\b|\bdeveloper productivity\b", 18),
                ("devinfra", r"\bDevInfra\b|\bdeveloper infrastructure\b", 16),
                ("developer_tooling", r"\bdeveloper tooling\b|\btooling engineer\b", 15),
                ("release_engineering", r"\brelease engineering\b|\bbuild engineer\b", 12),
                ("platform_engineer", r"\bplatform (?:software )?engineer\b", 8),
                ("devops", r"\bdevops\b", 7),
            ]
        ),
        evidence_patterns=_patterns(
            [
                ("developer_tooling", r"\bdeveloper tooling\b|\bengineering tools?\b", 13),
                ("developer_experience", r"\bdeveloper experience\b|\bdeveloper productivity\b", 11),
                ("internal_tools", r"\binternal tools?\b|\binternal developer platform\b", 10),
                ("build_system", r"\bbuild systems?\b|\bBazel\b|\bBuck\b", 9),
                ("cicd", r"\bCI/CD\b|\bcontinuous integration\b|\bcontinuous delivery\b", 8),
                ("automation", r"\bautomation\b|\bautomate\b", 6),
                ("sdk", r"\bSDKs?\b|\bCLIs?\b", 5),
                ("pragmatic", r"\bpragmatic\b|\bover-engineer(?:ing|ed)?\b", 5),
            ]
        ),
        negative_title=_negative(r"\b(?:manager|director|designer|product|sales|support|marketing|advisor|faculty)\b"),
        min_title_score=7,
        min_evidence_score=16,
    ),
}

SUPPORTED_PREFERENCES = tuple(HEURISTICS)


def _matched(text: str, patterns: tuple[Pattern, ...]) -> list[tuple[str, int]]:
    return [(name, weight) for name, pattern, weight in patterns if pattern.search(text)]


def _rank_snippets(description: str, patterns: tuple[Pattern, ...]) -> tuple[list[str], list[str], int]:
    from src.python.ai_scorer.ai_scorer import SNIPPET_TOP_K, generate_hybrid_chunks

    ranked: list[tuple[int, str]] = []
    all_signals: set[str] = set()
    for chunk in generate_hybrid_chunks(description):
        matches = _matched(chunk, patterns)
        if not matches:
            continue
        all_signals.update(name for name, _ in matches)
        ranked.append((sum(weight for _, weight in matches), chunk))
    ranked.sort(key=lambda item: (-item[0], item[1].casefold(), item[1]))
    selected = ranked[:SNIPPET_TOP_K]
    return [item[1] for item in selected], sorted(all_signals), sum(item[0] for item in selected)


def _score_job(job: dict, preference_key: str) -> dict | None:
    heuristic = HEURISTICS.get(preference_key)
    if heuristic is None:
        raise ValueError(f"Unsupported seed preference: {preference_key}")

    title = str(job.get("title", "") or "")
    if heuristic.negative_title.search(title):
        return None
    title_matches = _matched(title, heuristic.title_patterns)
    title_score = sum(weight for _, weight in title_matches)
    snippets, description_signals, evidence_score = _rank_snippets(
        str(job.get("description", "") or ""),
        heuristic.evidence_patterns,
    )
    if (
        title_score < heuristic.min_title_score
        or len(snippets) < 2
        or evidence_score < heuristic.min_evidence_score
    ):
        return None
    return {
        "rank_score": title_score * 100 + evidence_score,
        "title_signals": sorted(name for name, _ in title_matches),
        "description_signals": description_signals,
        "snippets": snippets,
    }


def _case_id(job_fingerprint: str, preference_key: str, guidance: str) -> str:
    identity = f"{SELECTOR_VERSION}\n{job_fingerprint}\n{preference_key}\n{guidance}"
    return str(uuid.uuid5(CASE_ID_NAMESPACE, identity))


def _validate_job(job: dict) -> None:
    fingerprint = str(job.get("job_fingerprint", "") or "")
    actual, basis = description_fingerprint(
        str(job.get("description", "") or ""),
        title=str(job.get("title", "") or ""),
        location=str(job.get("location", "") or ""),
    )
    if fingerprint != actual or job.get("fingerprint_basis") != basis:
        raise ValueError(f"Job pool fingerprint mismatch: {fingerprint!r}")


def mine_high_score_cases(
    jobs: list[dict],
    preferences: list[dict],
    *,
    excluded_fingerprints: set[str],
    target_per_preference: int,
    max_preferences_per_job: int,
    max_evidence_reuse: int = 2,
) -> tuple[list[TrainingCase], dict]:
    if target_per_preference <= 0:
        raise ValueError("target_per_preference must be greater than zero")
    if max_preferences_per_job <= 0:
        raise ValueError("max_preferences_per_job must be greater than zero")
    if max_evidence_reuse <= 0:
        raise ValueError("max_evidence_reuse must be greater than zero")

    preference_by_key = {str(item.get("key", "")): item for item in preferences}
    if tuple(preference_by_key) != SUPPORTED_PREFERENCES:
        raise ValueError(
            "High-score preferences must be the ordered seed set: "
            + ", ".join(SUPPORTED_PREFERENCES)
        )

    eligible_jobs: list[dict] = []
    seen_fingerprints: set[str] = set()
    for job in jobs:
        _validate_job(job)
        fingerprint = str(job["job_fingerprint"])
        if fingerprint in seen_fingerprints:
            raise ValueError(f"Duplicate job pool fingerprint: {fingerprint}")
        seen_fingerprints.add(fingerprint)
        if fingerprint not in excluded_fingerprints:
            eligible_jobs.append(job)

    ranked: dict[str, list[tuple[dict, dict]]] = {}
    available_counts: dict[str, int] = {}
    for preference_key in SUPPORTED_PREFERENCES:
        candidates = [
            (job, scoring)
            for job in eligible_jobs
            if (scoring := _score_job(job, preference_key)) is not None
        ]
        candidates.sort(
            key=lambda item: (-int(item[1]["rank_score"]), str(item[0]["job_fingerprint"]))
        )
        ranked[preference_key] = candidates
        available_counts[preference_key] = len(candidates)

    selected: dict[str, list[tuple[dict, dict]]] = {key: [] for key in SUPPORTED_PREFERENCES}
    positions = {key: 0 for key in SUPPORTED_PREFERENCES}
    job_usage: Counter[str] = Counter()
    evidence_usage: dict[str, Counter[tuple[str, ...]]] = {
        key: Counter() for key in SUPPORTED_PREFERENCES
    }
    while any(len(selected[key]) < target_per_preference for key in SUPPORTED_PREFERENCES):
        made_progress = False
        for preference_key in SUPPORTED_PREFERENCES:
            if len(selected[preference_key]) >= target_per_preference:
                continue
            options = ranked[preference_key]
            while positions[preference_key] < len(options):
                job, scoring = options[positions[preference_key]]
                positions[preference_key] += 1
                fingerprint = str(job["job_fingerprint"])
                if job_usage[fingerprint] >= max_preferences_per_job:
                    continue
                evidence_signature = tuple(
                    re.sub(r"\s+", " ", snippet).strip().casefold()
                    for snippet in scoring["snippets"]
                )
                if evidence_usage[preference_key][evidence_signature] >= max_evidence_reuse:
                    continue
                selected[preference_key].append((job, scoring))
                job_usage[fingerprint] += 1
                evidence_usage[preference_key][evidence_signature] += 1
                made_progress = True
                break
        if not made_progress:
            break

    shortfalls = {
        key: target_per_preference - len(selected[key])
        for key in SUPPORTED_PREFERENCES
        if len(selected[key]) < target_per_preference
    }
    empty_preferences = [key for key in SUPPORTED_PREFERENCES if not selected[key]]
    if empty_preferences:
        raise ValueError(
            "No high-confidence candidates for seed preferences: "
            f"{empty_preferences}; available before caps: {available_counts}"
        )

    from src.python.ai_scorer.ai_scorer import build_prompt

    cases: list[TrainingCase] = []
    report_records: list[dict] = []
    for preference_key in SUPPORTED_PREFERENCES:
        preference = preference_by_key[preference_key]
        guidance = str(preference["guidance"])
        for job, scoring in selected[preference_key]:
            fingerprint = str(job["job_fingerprint"])
            title = str(job.get("title", "") or "")
            location = str(job.get("location", "") or "")
            snippets = list(scoring["snippets"])
            system_prompt, user_prompt = build_prompt(
                {"title": title, "location": location},
                {},
                {},
                preference,
                snippets=snippets,
            )
            case = TrainingCase(
                case_id=_case_id(fingerprint, preference_key, guidance),
                job_fingerprint=fingerprint,
                fingerprint_basis=str(job["fingerprint_basis"]),
                title=title,
                location=location,
                preference_key=preference_key,
                preference_guidance=guidance,
                relevant_snippets=snippets,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            cases.append(case)
            report_records.append(
                {
                    "case_id": case.case_id,
                    "job_fingerprint": fingerprint,
                    "title": title,
                    "location": location,
                    "preference_key": preference_key,
                    "estimated_score": 5,
                    "heuristic_rank_score": scoring["rank_score"],
                    "title_signals": scoring["title_signals"],
                    "description_signals": scoring["description_signals"],
                    "selected_snippets": snippets,
                }
            )

    errors = validate_cases(cases)
    if errors:
        raise ValueError("Generated invalid cases: " + "; ".join(errors))

    cases.sort(key=lambda case: (case.preference_key, case.job_fingerprint, case.case_id))
    report_records.sort(key=lambda item: (item["preference_key"], item["job_fingerprint"]))
    selected_fingerprints = sorted({case.job_fingerprint for case in cases})
    report = {
        "schema_version": "1",
        "selector_version": SELECTOR_VERSION,
        "preference_source_policy": (
            "candidate preferences and guidance come exclusively from training_preferences.seed.json; "
            "promotion fixtures are used only to exclude overlapping job fingerprints"
        ),
        "estimate_policy": (
            "estimated_score is a lexical candidate-mining hypothesis, not a label; "
            "Gemini must independently label every case"
        ),
        "selection": {
            "target_score": 5,
            "target_per_preference": target_per_preference,
            "max_preferences_per_job": max_preferences_per_job,
            "max_evidence_reuse_per_preference": max_evidence_reuse,
            "preference_order": list(SUPPORTED_PREFERENCES),
        },
        "counts": {
            "input_jobs": len(jobs),
            "excluded_job_fingerprints": len(seen_fingerprints & excluded_fingerprints),
            "eligible_jobs": len(eligible_jobs),
            "available_pairs_by_preference": available_counts,
            "selected_cases": len(cases),
            "selected_unique_jobs": len(selected_fingerprints),
            "selected_cases_by_preference": dict(Counter(case.preference_key for case in cases)),
            "selection_shortfall_by_preference": shortfalls,
            "selected_jobs_by_case_count": {
                str(count): sum(1 for value in job_usage.values() if value == count)
                for count in sorted(set(job_usage.values()))
            },
            "max_selected_evidence_reuse_by_preference": {
                key: max(evidence_usage[key].values(), default=0)
                for key in SUPPORTED_PREFERENCES
            },
        },
        "selected_job_fingerprints": selected_fingerprints,
        "records": report_records,
        "candidate_set_sha256": stable_json_hash([asdict(case) for case in cases]),
    }
    return cases, report


def _load_job_pool(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ValueError(f"Expected a job-pool object with a jobs array in {path}")
    return payload["jobs"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Mine unpaid, likely-score-5 cases for the maintained seed preferences"
    )
    parser.add_argument("--job-pool", default=DEFAULT_JOB_POOL)
    parser.add_argument("--preferences", default=DEFAULT_PREFERENCES)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--report-out", default=DEFAULT_REPORT)
    parser.add_argument("--target-per-preference", type=int, default=40)
    parser.add_argument("--max-preferences-per-job", type=int, default=2)
    parser.add_argument("--max-evidence-reuse", type=int, default=2)
    parser.add_argument("--exclude-cases", default=DEFAULT_EXCLUDE_CASES)
    parser.add_argument("--split-manifest", default=DEFAULT_SPLIT_MANIFEST)
    parser.add_argument("--promotion-fixtures", default=DEFAULT_PROMOTION_FIXTURES)
    args = parser.parse_args(argv)

    preferences = load_preferences(args.preferences)
    golden_fingerprints, golden_case_count = load_golden_fingerprints(args.promotion_fixtures)
    split_manifest = load_split_manifest(args.split_manifest)
    excluded = set(golden_fingerprints)
    excluded.update(split_manifest["promotion_exclusion_fingerprints"])
    existing_case_count = 0
    if args.exclude_cases:
        existing_cases = load_cases(args.exclude_cases)
        existing_case_count = len(existing_cases)
        excluded.update(case.job_fingerprint for case in existing_cases)

    cases, report = mine_high_score_cases(
        _load_job_pool(args.job_pool),
        preferences,
        excluded_fingerprints=excluded,
        target_per_preference=args.target_per_preference,
        max_preferences_per_job=args.max_preferences_per_job,
        max_evidence_reuse=args.max_evidence_reuse,
    )
    report["inputs"] = {
        "job_pool": args.job_pool,
        "job_pool_sha256": file_sha256(args.job_pool),
        "preferences": args.preferences,
        "preferences_sha256": file_sha256(args.preferences),
        "exclude_cases": args.exclude_cases,
        "exclude_case_count": existing_case_count,
        "exclude_cases_sha256": file_sha256(args.exclude_cases) if args.exclude_cases else "",
        "split_manifest": args.split_manifest,
        "split_manifest_sha256": file_sha256(args.split_manifest),
        "promotion_fixtures": args.promotion_fixtures,
        "promotion_fixture_sha256": file_sha256(args.promotion_fixtures),
        "promotion_case_count": golden_case_count,
        "promotion_fixture_usage": "job-fingerprint exclusion only",
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    dump_cases(cases, args.output)
    os.makedirs(os.path.dirname(os.path.abspath(args.report_out)), exist_ok=True)
    with open(args.report_out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    print(f"[training.high-score] cases={len(cases)} unique_jobs={report['counts']['selected_unique_jobs']}")
    for key in SUPPORTED_PREFERENCES:
        selected_count = report["counts"]["selected_cases_by_preference"][key]
        available_count = report["counts"]["available_pairs_by_preference"][key]
        print(f"[training.high-score] preference={key} selected={selected_count} available={available_count}")
    print(f"[training.high-score] candidates -> {args.output}")
    print(f"[training.high-score] report -> {args.report_out}")
    print("[training.high-score] paid_calls=0 (selection only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
