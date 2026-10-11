from __future__ import annotations

import unittest

import pandas as pd

from src.compare_carriers import (
    calculate_carrier_metrics,
    filter_comparison_scope,
)


class CarrierComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.flights = pd.DataFrame(
            {
                "flight_date": pd.to_datetime(
                    [
                        "2013-01-01",
                        "2013-01-02",
                        "2013-01-03",
                        "2013-01-01",
                        "2013-01-02",
                    ]
                ),
                "origin": ["JFK", "JFK", "JFK", "JFK", "JFK"],
                "dest": ["LAX", "LAX", "LAX", "LAX", "LAX"],
                "carrier": ["AA", "AA", "AA", "DL", "DL"],
                "dep_time": [100.0, 200.0, float("nan"), 300.0, 400.0],
                "arr_delay": [20.0, 10.0, float("nan"), 16.0, -5.0],
            }
        )
        self.airlines = pd.DataFrame(
            {
                "carrier": ["AA", "DL"],
                "name": ["American Airlines Inc.", "Delta Air Lines Inc."],
            }
        )

    def test_metric_contract_is_applied(self) -> None:
        metrics = calculate_carrier_metrics(
            self.flights,
            self.airlines,
            min_valid_arrivals=2,
        )
        aa = metrics[0]

        self.assertEqual(aa["carrier"], "AA")
        self.assertEqual(aa["planned_flights"], 3)
        self.assertEqual(aa["cancelled_flights"], 1)
        self.assertEqual(aa["cancellation_rate_pct"], 33.33)
        self.assertEqual(aa["valid_arrivals"], 2)
        self.assertEqual(aa["delayed_arrivals"], 1)
        self.assertEqual(aa["arrival_delay_rate_pct"], 50.0)
        self.assertEqual(aa["on_time_arrivals"], 1)
        self.assertEqual(aa["arrival_on_time_rate_pct"], 50.0)
        self.assertEqual(aa["average_delay_minutes"], 20.0)
        self.assertEqual(aa["sample_status"], "sufficient")
        self.assertTrue(aa["recommendation_eligible"])

    def test_sample_rule_blocks_strong_recommendation(self) -> None:
        metrics = calculate_carrier_metrics(
            self.flights,
            self.airlines,
            min_valid_arrivals=3,
        )

        for carrier in metrics:
            self.assertEqual(carrier["sample_status"], "insufficient")
            self.assertFalse(carrier["recommendation_eligible"])

    def test_scope_uses_same_route_and_date_range(self) -> None:
        scoped = filter_comparison_scope(
            self.flights,
            origin="JFK",
            dest="LAX",
            start_date=pd.Timestamp("2013-01-02"),
            end_date=pd.Timestamp("2013-01-03"),
            carriers=["AA"],
        )

        self.assertEqual(len(scoped), 2)
        self.assertEqual(set(scoped["carrier"]), {"AA"})
        self.assertTrue(scoped["flight_date"].min() >= pd.Timestamp("2013-01-02"))


if __name__ == "__main__":
    unittest.main()
