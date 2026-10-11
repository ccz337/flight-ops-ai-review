from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

try:
    from .validate_ai_review import expected_decision, validate_ai_review
except ImportError:
    from validate_ai_review import expected_decision, validate_ai_review


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE_PATH = (
    PROJECT_ROOT / "examples" / "jfk_lax_2013_carrier_comparison.json"
)
DEFAULT_SAMPLE_REVIEW_PATH = (
    PROJECT_ROOT / "examples" / "jfk_lax_2013_ai_review.json"
)
DEFAULT_GENERATOR_PROMPT_PATH = (
    PROJECT_ROOT / "prompts" / "carrier_comparison_prompt.md"
)
DEFAULT_REVIEWER_PROMPT_PATH = PROJECT_ROOT / "prompts" / "ai_reviewer_prompt.md"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "deepseek_run"
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"

REVIEW_CHECKS = (
    "scope_matches",
    "claims_supported",
    "citations_complete",
    "no_handwritten_numbers",
    "no_causal_overreach",
    "limitations_complete",
    "recommendation_is_cautious",
    "consumer_guidance_is_clear",
)
ALLOWED_REVIEW_ISSUES = {
    "scope_mismatch",
    "unsupported_claim",
    "missing_citation",
    "handwritten_number",
    "causal_overreach",
    "missing_limitations",
    "overconfident_recommendation",
    "unclear_consumer_guidance",
}

ModelCall = Callable[[str, str, dict[str, object]], dict[str, object]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a grounded DeepSeek generation and independent AI review pipeline."
        )
    )
    parser.add_argument("--mode", choices=("sample", "api"), default="sample")
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE_PATH)
    parser.add_argument(
        "--sample-review", type=Path, default=DEFAULT_SAMPLE_REVIEW_PATH
    )
    parser.add_argument(
        "--generator-prompt", type=Path, default=DEFAULT_GENERATOR_PROMPT_PATH
    )
    parser.add_argument(
        "--reviewer-prompt", type=Path, default=DEFAULT_REVIEWER_PROMPT_PATH
    )
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as file_handle:
        value = json.load(file_handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object.")
    return value


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def load_deepseek_config(env_path: Path) -> dict[str, str]:
    load_env_file(env_path)
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise ValueError(
            "DEEPSEEK_API_KEY is missing. Configure it in the ignored .env file."
        )

    base_url = os.environ.get(
        "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
    ).strip().rstrip("/")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash").strip()

    if base_url != "https://api.deepseek.com":
        raise ValueError(
            "DEEPSEEK_BASE_URL must be https://api.deepseek.com for this project."
        )
    if model not in {"deepseek-flash", "deepseek-v4-pro"}:
        raise ValueError("Unsupported DeepSeek model name.")

    return {"api_key": api_key, "base_url": base_url, "model": model}


def evidence_scope(evidence: dict[str, object]) -> dict[str, object]:
    filters = evidence.get("filters")
    if not isinstance(filters, dict):
        raise ValueError("Evidence filters are missing.")
    return {
        "origin": filters.get("origin"),
        "destination": filters.get("destination"),
        "start_date": filters.get("start_date"),
        "end_date": filters.get("end_date"),
    }


def build_pre_call_refusal(evidence: dict[str, object]) -> dict[str, object]:
    return {
        "decision": "refuse",
        "scope": evidence_scope(evidence),
        "summary": "可比较的有效航司不足，当前不提供航司优劣判断。",
        "recommendation_strength": "none",
        "refusal_reason_code": "fewer_than_two_eligible_carriers",
        "generation_disclosure": {
            "generated_by": "deterministic_pre_call_gate",
            "generation_mode": "no_model_call",
        },
    }


def build_fallback(reason_code: str, failure_stage: str) -> dict[str, object]:
    return {
        "decision": "fallback",
        "reason_code": reason_code,
        "failure_stage": failure_stage,
        "message": (
            "AI explanation is unavailable. Show only the verified carrier metrics."
        ),
        "recommendation_strength": "none",
    }


def validate_reviewer_output(value: dict[str, object]) -> None:
    decision = value.get("decision")
    if decision not in {"pass", "revise"}:
        raise ValueError("Reviewer decision must be pass or revise.")

    checks = value.get("checks")
    if not isinstance(checks, dict) or set(checks) != set(REVIEW_CHECKS):
        raise ValueError("Reviewer checks are missing or unexpected.")
    if any(not isinstance(checks[name], bool) for name in REVIEW_CHECKS):
        raise ValueError("Every reviewer check must be boolean.")

    issue_codes = value.get("issue_codes")
    if not isinstance(issue_codes, list) or any(
        not isinstance(code, str) for code in issue_codes
    ):
        raise ValueError("Reviewer issue_codes must be a string list.")
    unknown_codes = set(issue_codes) - ALLOWED_REVIEW_ISSUES
    if unknown_codes:
        raise ValueError(f"Unknown reviewer issue codes: {sorted(unknown_codes)}")

    summary = value.get("review_summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("Reviewer summary is required.")

    all_checks_pass = all(checks[name] for name in REVIEW_CHECKS)
    if decision == "pass" and (not all_checks_pass or issue_codes):
        raise ValueError("A pass decision requires all checks and no issue codes.")
    if decision == "revise" and all_checks_pass and not issue_codes:
        raise ValueError("A revise decision requires a failed check or issue code.")


def redact_error(exc: Exception, secret: str | None = None) -> str:
    message = str(exc)
    if secret:
        message = message.replace(secret, "[REDACTED]")
    return message[:1000]


def execute_pipeline(
    evidence: dict[str, object],
    mode: str,
    model_name: str,
    generator_prompt: str,
    reviewer_prompt: str,
    sample_review: dict[str, object] | None = None,
    model_call: ModelCall | None = None,
    secret_for_redaction: str | None = None,
) -> dict[str, object]:
    audit: dict[str, object] = {
        "mode": mode,
        "provider": "DeepSeek" if mode == "api" else "sample",
        "model": model_name,
        "pipeline_status": "started",
        "model_calls": [],
    }
    result: dict[str, object] = {
        "final_output": None,
        "candidate_review": None,
        "ai_reviewer": None,
        "audit": audit,
    }

    if expected_decision(evidence) == "refuse":
        refusal = build_pre_call_refusal(evidence)
        validate_ai_review(evidence, refusal)
        audit["pipeline_status"] = "refused_by_pre_call_gate"
        audit["model_call_count"] = 0
        result["final_output"] = refusal
        return result

    if mode == "sample":
        if sample_review is None:
            raise ValueError("sample_review is required in sample mode.")
        validate_ai_review(evidence, sample_review)
        audit["pipeline_status"] = "sample_validated"
        audit["model_call_count"] = 0
        result["candidate_review"] = sample_review
        result["final_output"] = sample_review
        return result

    if mode != "api" or model_call is None:
        raise ValueError("API mode requires a model_call implementation.")

    try:
        generator_call = model_call(
            "generator",
            generator_prompt,
            {
                "task": "Return one evidence-grounded carrier review as JSON.",
                "evidence": evidence,
            },
        )
        audit["model_calls"].append(generator_call.get("metadata", {}))
        candidate = generator_call.get("content")
        if not isinstance(candidate, dict):
            raise ValueError("Generator did not return one JSON object.")
        candidate["generation_disclosure"] = {
            "generated_by": f"DeepSeek {model_name}",
            "generation_mode": "online_api",
        }
        result["candidate_review"] = candidate
        validate_ai_review(evidence, candidate)
    except Exception as exc:
        audit["pipeline_status"] = "fallback"
        audit["failure_stage"] = "generator_or_deterministic_validation"
        audit["failure_type"] = type(exc).__name__
        audit["failure_message"] = redact_error(exc, secret_for_redaction)
        audit["model_call_count"] = len(audit["model_calls"])
        result["final_output"] = build_fallback(
            "generator_or_validation_failed",
            "generator_or_deterministic_validation",
        )
        return result

    try:
        reviewer_call = model_call(
            "reviewer",
            reviewer_prompt,
            {
                "task": "Audit the candidate review and return the review result as JSON.",
                "evidence": evidence,
                "candidate_review": result["candidate_review"],
            },
        )
        audit["model_calls"].append(reviewer_call.get("metadata", {}))
        reviewer_output = reviewer_call.get("content")
        if not isinstance(reviewer_output, dict):
            raise ValueError("Reviewer did not return one JSON object.")
        result["ai_reviewer"] = reviewer_output
        validate_reviewer_output(reviewer_output)

        if reviewer_output["decision"] != "pass":
            audit["pipeline_status"] = "fallback"
            audit["failure_stage"] = "ai_reviewer"
            audit["failure_type"] = "ReviewerRequestedRevision"
            audit["model_call_count"] = len(audit["model_calls"])
            result["final_output"] = build_fallback(
                "ai_reviewer_requested_revision", "ai_reviewer"
            )
            return result
    except Exception as exc:
        audit["pipeline_status"] = "fallback"
        audit["failure_stage"] = "ai_reviewer"
        audit["failure_type"] = type(exc).__name__
        audit["failure_message"] = redact_error(exc, secret_for_redaction)
        audit["model_call_count"] = len(audit["model_calls"])
        result["final_output"] = build_fallback(
            "ai_reviewer_failed", "ai_reviewer"
        )
        return result

    audit["pipeline_status"] = "approved"
    audit["model_call_count"] = len(audit["model_calls"])
    result["final_output"] = result["candidate_review"]
    return result


def make_deepseek_model_call(config: dict[str, str]) -> ModelCall:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "The openai package is missing. Install requirements.txt first."
        ) from exc

    client = OpenAI(
        api_key=config["api_key"],
        base_url=config["base_url"],
        timeout=60.0,
        max_retries=1,
    )

    def call_model(
        stage: str,
        system_prompt: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        started = time.perf_counter()
        last_error: Exception | None = None

        for attempt in (1, 2):
            user_content = json.dumps(payload, ensure_ascii=False, indent=2)
            if attempt == 2:
                user_content += (
                    "\n\nReturn a non-empty valid JSON object only. "
                    "Do not use a Markdown code block."
                )

            response = client.chat.completions.create(
                model=config["model"],
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                response_format={"type": "json_object"},
                max_tokens=2400,
                temperature=0.2,
                stream=False,
                extra_body={"thinking": {"type": "disabled"}},
            )
            content = response.choices[0].message.content

            try:
                if not content or not content.strip():
                    raise ValueError("DeepSeek returned empty content.")
                parsed = json.loads(content)
                if not isinstance(parsed, dict):
                    raise ValueError("DeepSeek JSON output is not one object.")
            except (json.JSONDecodeError, ValueError) as exc:
                last_error = exc
                if attempt == 1:
                    continue
                raise

            usage_value = getattr(response, "usage", None)
            if usage_value is None:
                usage = {}
            elif hasattr(usage_value, "model_dump"):
                usage = usage_value.model_dump()
            else:
                usage = {
                    "prompt_tokens": getattr(usage_value, "prompt_tokens", None),
                    "completion_tokens": getattr(
                        usage_value, "completion_tokens", None
                    ),
                    "total_tokens": getattr(usage_value, "total_tokens", None),
                }

            return {
                "content": parsed,
                "metadata": {
                    "stage": stage,
                    "provider": "DeepSeek",
                    "model": config["model"],
                    "request_id": getattr(response, "id", None),
                    "attempts": attempt,
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                    "usage": usage,
                },
            }

        raise RuntimeError("DeepSeek JSON output failed.") from last_error

    return call_model


def main() -> None:
    args = parse_args()
    started_at = datetime.now(timezone.utc)

    evidence_path = args.evidence.resolve()
    generator_prompt_path = args.generator_prompt.resolve()
    reviewer_prompt_path = args.reviewer_prompt.resolve()
    output_dir = args.output_dir.resolve()

    evidence = read_json(evidence_path)
    generator_prompt = generator_prompt_path.read_text(encoding="utf-8")
    reviewer_prompt = reviewer_prompt_path.read_text(encoding="utf-8")

    if args.mode == "sample":
        sample_review = read_json(args.sample_review.resolve())
        model_name = "sample"
        model_call = None
        api_key = None
    else:
        config = load_deepseek_config(args.env_file.resolve())
        sample_review = None
        model_name = config["model"]
        model_call = make_deepseek_model_call(config)
        api_key = config["api_key"]

    result = execute_pipeline(
        evidence=evidence,
        mode=args.mode,
        model_name=model_name,
        generator_prompt=generator_prompt,
        reviewer_prompt=reviewer_prompt,
        sample_review=sample_review,
        model_call=model_call,
        secret_for_redaction=api_key,
    )

    finished_at = datetime.now(timezone.utc)
    audit = result["audit"]
    audit.update(
        {
            "started_at_utc": started_at.isoformat(),
            "finished_at_utc": finished_at.isoformat(),
            "elapsed_seconds": round(
                (finished_at - started_at).total_seconds(), 3
            ),
            "evidence_sha256": sha256_file(evidence_path),
            "generator_prompt_sha256": sha256_file(generator_prompt_path),
            "reviewer_prompt_sha256": sha256_file(reviewer_prompt_path),
            "api_key_recorded": False,
            "cost_note": (
                "Token usage is recorded per call. Apply the current official "
                "DeepSeek pricing; prices are intentionally not hard-coded."
            ),
        }
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    if isinstance(result["candidate_review"], dict):
        write_json(output_dir / "candidate_review.json", result["candidate_review"])
    if isinstance(result["ai_reviewer"], dict):
        write_json(output_dir / "ai_reviewer.json", result["ai_reviewer"])

    final_output = result["final_output"]
    if not isinstance(final_output, dict):
        raise RuntimeError("Pipeline did not create a final output.")

    if final_output.get("decision") == "fallback":
        final_path = output_dir / "fallback.json"
    else:
        final_path = output_dir / "final_review.json"
    write_json(final_path, final_output)
    write_json(output_dir / "run_audit.json", audit)

    print(f"[STATUS] {audit['pipeline_status']}")
    print(f"[PASS] Audit written to: {output_dir / 'run_audit.json'}")
    print(f"[PASS] Final output written to: {final_path}")

    if audit["pipeline_status"] == "fallback":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
