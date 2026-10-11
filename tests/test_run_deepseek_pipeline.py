from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.run_deepseek_pipeline import (
    REVIEW_CHECKS,
    execute_pipeline,
    load_deepseek_config,
)


def make_evidence(eligible_carriers: int = 2) -> dict[str, object]:
    carriers = []
    for index, code in enumerate(("AA", "DL")):
        carriers.append(
            {
                "carrier": code,
                "airline_name": code,
                "planned_flights": 100,
                "cancelled_flights": 2,
                "cancellation_rate_pct": 2.0,
                "valid_arrivals": 95,
                "delayed_arrivals": 20,
                "arrival_delay_rate_pct": 21.05,
                "on_time_arrivals": 75,
                "arrival_on_time_rate_pct": 78.95,
                "average_delay_minutes": 50.0,
                "sample_status": (
                    "sufficient" if index < eligible_carriers else "insufficient"
                ),
                "recommendation_eligible": index < eligible_carriers,
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


def make_candidate_review() -> dict[str, object]:
    return {
        "decision": "explain",
        "scope": {
            "origin": "JFK",
            "destination": "LAX",
            "start_date": "2013-01-01",
            "end_date": "2013-12-31",
        },
        "summary": "两家航司在取消和到达表现方面存在取舍。",
        "tradeoffs": [
            {
                "statement": "AA与DL的到达延误率可以在相同范围内比较。",
                "evidence_ids": [
                    "AA.arrival_delay_rate_pct",
                    "DL.arrival_delay_rate_pct",
                ],
            }
        ],
        "consumer_guidance": [
            {
                "statement": "消费者可按自己更重视的指标比较候选航司。",
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
    }


def make_reviewer_pass() -> dict[str, object]:
    return {
        "decision": "pass",
        "checks": {name: True for name in REVIEW_CHECKS},
        "issue_codes": [],
        "review_summary": "候选解释通过全部检查。",
    }


class DeepSeekPipelineTests(unittest.TestCase):
    def test_pre_call_gate_skips_all_model_calls(self) -> None:
        calls = []

        def unexpected_call(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("Model should not be called.")

        result = execute_pipeline(
            evidence=make_evidence(eligible_carriers=1),
            mode="api",
            model_name="deepseek-flash",
            generator_prompt="json generator",
            reviewer_prompt="json reviewer",
            model_call=unexpected_call,
        )

        self.assertEqual(calls, [])
        self.assertEqual(
            result["audit"]["pipeline_status"], "refused_by_pre_call_gate"
        )
        self.assertEqual(result["final_output"]["decision"], "refuse")

    def test_two_model_calls_can_produce_an_approved_result(self) -> None:
        stages = []

        def fake_call(stage, system_prompt, payload):
            stages.append(stage)
            content = (
                make_candidate_review() if stage == "generator" else make_reviewer_pass()
            )
            return {
                "content": content,
                "metadata": {"stage": stage, "total_tokens": 10},
            }

        result = execute_pipeline(
            evidence=make_evidence(),
            mode="api",
            model_name="deepseek-flash",
            generator_prompt="json generator",
            reviewer_prompt="json reviewer",
            model_call=fake_call,
        )

        self.assertEqual(stages, ["generator", "reviewer"])
        self.assertEqual(result["audit"]["pipeline_status"], "approved")
        self.assertEqual(result["audit"]["model_call_count"], 2)
        self.assertEqual(result["final_output"]["decision"], "explain")

    def test_invalid_generator_output_falls_back_before_review(self) -> None:
        stages = []

        def fake_call(stage, system_prompt, payload):
            stages.append(stage)
            candidate = make_candidate_review()
            candidate["tradeoffs"][0]["evidence_ids"] = ["AA.unknown_metric"]
            return {"content": candidate, "metadata": {"stage": stage}}

        result = execute_pipeline(
            evidence=make_evidence(),
            mode="api",
            model_name="deepseek-flash",
            generator_prompt="json generator",
            reviewer_prompt="json reviewer",
            model_call=fake_call,
        )

        self.assertEqual(stages, ["generator"])
        self.assertEqual(result["audit"]["pipeline_status"], "fallback")
        self.assertEqual(result["final_output"]["decision"], "fallback")

    def test_reviewer_revision_request_causes_fallback(self) -> None:
        stages = []

        def fake_call(stage, system_prompt, payload):
            stages.append(stage)
            if stage == "generator":
                content = make_candidate_review()
            else:
                content = make_reviewer_pass()
                content["decision"] = "revise"
                content["checks"]["consumer_guidance_is_clear"] = False
                content["issue_codes"] = ["unclear_consumer_guidance"]
                content["review_summary"] = "消费者建议需要修改。"
            return {"content": content, "metadata": {"stage": stage}}

        result = execute_pipeline(
            evidence=make_evidence(),
            mode="api",
            model_name="deepseek-flash",
            generator_prompt="json generator",
            reviewer_prompt="json reviewer",
            model_call=fake_call,
        )

        self.assertEqual(stages, ["generator", "reviewer"])
        self.assertEqual(result["audit"]["pipeline_status"], "fallback")
        self.assertEqual(result["final_output"]["reason_code"], "ai_reviewer_requested_revision")

    def test_env_file_loads_without_printing_or_committing_secret(self) -> None:
        writable_temp_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=writable_temp_root) as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "DEEPSEEK_API_KEY=test-secret\n"
                "DEEPSEEK_BASE_URL=https://api.deepseek.com\n"
                "DEEPSEEK_MODEL=deepseek-flash\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                config = load_deepseek_config(env_path)

        self.assertEqual(config["api_key"], "test-secret")
        self.assertEqual(config["model"], "deepseek-flash")


if __name__ == "__main__":
    unittest.main()
