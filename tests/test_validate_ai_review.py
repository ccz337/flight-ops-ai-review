from __future__ import annotations

import copy
import unittest

from src.validate_ai_review import validate_ai_review


def make_evidence(eligible_carriers: int = 2) -> dict[str, object]:
    carriers = []
    for index, code in enumerate(("AA", "DL")):
        eligible = index < eligible_carriers
        carriers.append(
            {
                "carrier": code,
                "planned_flights": 100,
                "cancelled_flights": 2,
                "cancellation_rate_pct": 2.0,
                "valid_arrivals": 95,
                "delayed_arrivals": 20,
                "arrival_delay_rate_pct": 21.05,
                "on_time_arrivals": 75,
                "arrival_on_time_rate_pct": 78.95,
                "average_delay_minutes": 50.0,
                "recommendation_eligible": eligible,
            }
        )
    return {
        "filters": {
            "origin": "JFK",
            "destination": "LAX",
            "start_date": "2013-01-01",
            "end_date": "2013-12-31",
        },
        "carriers": carriers,
    }


def make_valid_review() -> dict[str, object]:
    return {
        "decision": "explain",
        "scope": {
            "origin": "JFK",
            "destination": "LAX",
            "start_date": "2013-01-01",
            "end_date": "2013-12-31",
        },
        "summary": "各航司在取消、到达准点和延误时长方面存在不同取舍。",
        "tradeoffs": [
            {
                "statement": "两家航司的到达延误率可以直接比较。",
                "evidence_ids": [
                    "AA.arrival_delay_rate_pct",
                    "DL.arrival_delay_rate_pct",
                ],
            }
        ],
        "consumer_guidance": [
            {
                "statement": "消费者可结合自己更重视的指标查看候选航司。",
                "evidence_ids": [
                    "AA.cancellation_rate_pct",
                    "DL.cancellation_rate_pct",
                ],
            }
        ],
        "limitation_codes": [
            "historical_2013_data",
            "nyc_departures_only",
            "not_full_purchase_decision",
            "no_causal_claim",
        ],
        "recommendation_strength": "cautious",
        "generation_disclosure": {
            "generated_by": "AI assistant",
            "generation_mode": "development_assisted_sample",
        },
    }


class AiReviewValidationTests(unittest.TestCase):
    def test_valid_grounded_review_passes(self) -> None:
        validate_ai_review(make_evidence(), make_valid_review())

    def test_unknown_evidence_id_is_rejected(self) -> None:
        review = make_valid_review()
        review["tradeoffs"][0]["evidence_ids"] = ["AA.unknown_metric"]
        with self.assertRaisesRegex(ValueError, "unknown evidence IDs"):
            validate_ai_review(make_evidence(), review)

    def test_handwritten_number_in_ai_prose_is_rejected(self) -> None:
        review = make_valid_review()
        review["summary"] = "某航司的准点率是80%。"
        with self.assertRaisesRegex(ValueError, "handwritten number"):
            validate_ai_review(make_evidence(), review)

    def test_insufficient_comparable_carriers_requires_refusal(self) -> None:
        evidence = make_evidence(eligible_carriers=1)
        review = {
            "decision": "refuse",
            "scope": copy.deepcopy(evidence["filters"]),
            "summary": "可比较的有效航司不足，当前不提供航司优劣判断。",
            "recommendation_strength": "none",
            "refusal_reason_code": "fewer_than_two_eligible_carriers",
            "generation_disclosure": {
                "generated_by": "AI assistant",
                "generation_mode": "development_assisted_sample",
            },
        }
        validate_ai_review(evidence, review)


if __name__ == "__main__":
    unittest.main()
