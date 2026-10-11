from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


METRIC_FIELDS = (
    "planned_flights",
    "cancelled_flights",
    "cancellation_rate_pct",
    "valid_arrivals",
    "delayed_arrivals",
    "arrival_delay_rate_pct",
    "on_time_arrivals",
    "arrival_on_time_rate_pct",
    "average_delay_minutes",
)
REQUIRED_LIMITATION_CODES = {
    "historical_2013_data",
    "nyc_departures_only",
    "not_full_purchase_decision",
    "no_causal_claim",
}
BANNED_PROSE_TERMS = (
    "最佳航司",
    "最差航司",
    "一定会",
    "保证",
    "导致",
    "造成",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate an evidence-grounded AI carrier review."
    )
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--review", required=True, type=Path)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as file_handle:
        value = json.load(file_handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object.")
    return value


def build_evidence_catalog(evidence: dict[str, object]) -> dict[str, object]:
    catalog = {}
    carriers = evidence.get("carriers")
    if not isinstance(carriers, list):
        raise ValueError("Evidence must contain a carriers list.")

    for carrier_record in carriers:
        if not isinstance(carrier_record, dict):
            raise ValueError("Each carrier record must be an object.")
        carrier = carrier_record.get("carrier")
        if not isinstance(carrier, str):
            raise ValueError("Each carrier record must have a carrier code.")

        for field in METRIC_FIELDS:
            if field not in carrier_record:
                raise ValueError(f"Missing evidence field: {carrier}.{field}")
            catalog[f"{carrier}.{field}"] = carrier_record[field]

    return catalog


def expected_decision(evidence: dict[str, object]) -> str:
    carriers = evidence.get("carriers", [])
    eligible_count = sum(
        1
        for record in carriers
        if isinstance(record, dict) and record.get("recommendation_eligible") is True
    )
    return "explain" if eligible_count >= 2 else "refuse"


def validate_scope(
    evidence: dict[str, object], review: dict[str, object]
) -> None:
    filters = evidence.get("filters")
    scope = review.get("scope")
    if not isinstance(filters, dict) or not isinstance(scope, dict):
        raise ValueError("Evidence filters and review scope must be objects.")

    expected_scope = {
        "origin": filters.get("origin"),
        "destination": filters.get("destination"),
        "start_date": filters.get("start_date"),
        "end_date": filters.get("end_date"),
    }
    if scope != expected_scope:
        raise ValueError("Review scope does not exactly match evidence filters.")


def validate_prose(text: object, field_name: str) -> None:
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{field_name} must be non-empty text.")
    if re.search(r"\d", text):
        raise ValueError(
            f"{field_name} contains a handwritten number; cite evidence IDs instead."
        )
    for term in BANNED_PROSE_TERMS:
        if term in text:
            raise ValueError(f"{field_name} contains banned wording: {term}")


def validate_claims(
    review: dict[str, object],
    field_name: str,
    catalog: dict[str, object],
) -> None:
    claims = review.get(field_name)
    if not isinstance(claims, list) or not claims:
        raise ValueError(f"{field_name} must be a non-empty list.")

    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            raise ValueError(f"{field_name}[{index}] must be an object.")
        validate_prose(claim.get("statement"), f"{field_name}[{index}].statement")
        evidence_ids = claim.get("evidence_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            raise ValueError(f"{field_name}[{index}] needs evidence IDs.")
        unknown_ids = [value for value in evidence_ids if value not in catalog]
        if unknown_ids:
            raise ValueError(
                f"{field_name}[{index}] uses unknown evidence IDs: {unknown_ids}"
            )


def validate_ai_review(
    evidence: dict[str, object], review: dict[str, object]
) -> None:
    catalog = build_evidence_catalog(evidence)
    validate_scope(evidence, review)

    expected = expected_decision(evidence)
    actual = review.get("decision")
    if actual != expected:
        raise ValueError(f"Decision must be {expected!r}, got {actual!r}.")

    disclosure = review.get("generation_disclosure")
    if not isinstance(disclosure, dict):
        raise ValueError("generation_disclosure is required.")
    if not disclosure.get("generated_by") or not disclosure.get("generation_mode"):
        raise ValueError("AI generation disclosure is incomplete.")

    if expected == "refuse":
        if review.get("recommendation_strength") != "none":
            raise ValueError("A refused review must use recommendation_strength none.")
        if review.get("refusal_reason_code") != "fewer_than_two_eligible_carriers":
            raise ValueError("The refusal reason code is missing or incorrect.")
        validate_prose(review.get("summary"), "summary")
        return

    if review.get("recommendation_strength") != "cautious":
        raise ValueError("An explanatory review must remain cautious.")

    validate_prose(review.get("summary"), "summary")
    validate_claims(review, "tradeoffs", catalog)
    validate_claims(review, "consumer_guidance", catalog)

    limitation_codes = review.get("limitation_codes")
    if not isinstance(limitation_codes, list):
        raise ValueError("limitation_codes must be a list.")
    missing_codes = REQUIRED_LIMITATION_CODES - set(limitation_codes)
    if missing_codes:
        raise ValueError(f"Missing limitation codes: {sorted(missing_codes)}")


def main() -> None:
    args = parse_args()
    evidence = read_json(args.evidence)
    review = read_json(args.review)
    validate_ai_review(evidence, review)
    print("[PASS] AI review is grounded, scoped, cautious, and fully disclosed.")


if __name__ == "__main__":
    main()
