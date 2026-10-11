from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "outputs" / "carrier_comparison.json"
MIN_VALID_ARRIVALS = 30
DELAY_THRESHOLD_MINUTES = 15


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare historical airline operating stability on one route "
            "and one date range."
        )
    )
    parser.add_argument("--origin", required=True, help="Origin airport code.")
    parser.add_argument("--dest", required=True, help="Destination airport code.")
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD.")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD.")
    parser.add_argument(
        "--carriers",
        nargs="*",
        help="Optional carrier codes, for example: AA DL UA.",
    )
    parser.add_argument(
        "--min-valid-arrivals",
        type=int,
        default=MIN_VALID_ARRIVALS,
        help="Minimum valid arrival samples required for recommendation eligibility.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Path for the structured JSON evidence file.",
    )
    return parser.parse_args()


def validate_code(value: str, field_name: str) -> str:
    normalized = value.strip().upper()
    if len(normalized) != 3 or not normalized.isalpha():
        raise ValueError(f"{field_name} must be a three-letter airport code.")
    return normalized


def validate_carriers(values: list[str] | None) -> list[str] | None:
    if not values:
        return None

    normalized = []
    for value in values:
        code = value.strip().upper()
        if len(code) != 2 or not code.isalnum():
            raise ValueError(
                "Each carrier must be a two-character nycflights13 carrier code."
            )
        normalized.append(code)

    return sorted(set(normalized))


def load_source_data(
    raw_data_dir: Path = RAW_DATA_DIR,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    flights_path = raw_data_dir / "flights.csv"
    airlines_path = raw_data_dir / "airlines.csv"

    if not flights_path.exists() or not airlines_path.exists():
        raise FileNotFoundError(
            "Required CSV files are missing. Run src/fetch_data.py first."
        )

    flights = pd.read_csv(
        flights_path,
        usecols=[
            "year",
            "month",
            "day",
            "dep_time",
            "arr_delay",
            "carrier",
            "origin",
            "dest",
        ],
    )
    airlines = pd.read_csv(airlines_path, usecols=["carrier", "name"])
    flights["flight_date"] = pd.to_datetime(
        flights[["year", "month", "day"]], errors="raise"
    )
    return flights, airlines


def filter_comparison_scope(
    flights: pd.DataFrame,
    origin: str,
    dest: str,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    carriers: list[str] | None = None,
) -> pd.DataFrame:
    if start_date > end_date:
        raise ValueError("start-date must be on or before end-date.")

    mask = (
        flights["origin"].eq(origin)
        & flights["dest"].eq(dest)
        & flights["flight_date"].between(start_date, end_date, inclusive="both")
    )
    if carriers:
        mask &= flights["carrier"].isin(carriers)

    scoped = flights.loc[mask].copy()
    if scoped.empty:
        raise ValueError("No flights match the requested comparison scope.")

    return scoped


def calculate_carrier_metrics(
    scoped_flights: pd.DataFrame,
    airlines: pd.DataFrame,
    min_valid_arrivals: int = MIN_VALID_ARRIVALS,
) -> list[dict[str, object]]:
    if min_valid_arrivals < 1:
        raise ValueError("min-valid-arrivals must be at least 1.")

    airline_names = airlines.set_index("carrier")["name"].to_dict()
    results = []

    for carrier, group in scoped_flights.groupby("carrier", sort=True):
        planned_flights = int(len(group))
        cancelled_flights = int(group["dep_time"].isna().sum())
        valid_arrivals = int(group["arr_delay"].notna().sum())
        delayed_mask = group["arr_delay"].gt(DELAY_THRESHOLD_MINUTES)
        on_time_mask = group["arr_delay"].le(DELAY_THRESHOLD_MINUTES)
        delayed_arrivals = int(delayed_mask.sum())
        on_time_arrivals = int(on_time_mask.sum())

        if valid_arrivals:
            delay_rate = delayed_arrivals / valid_arrivals * 100
            on_time_rate = on_time_arrivals / valid_arrivals * 100
        else:
            delay_rate = None
            on_time_rate = None

        if delayed_arrivals:
            average_delay = float(group.loc[delayed_mask, "arr_delay"].mean())
        else:
            average_delay = None

        sample_is_sufficient = valid_arrivals >= min_valid_arrivals
        results.append(
            {
                "carrier": str(carrier),
                "airline_name": str(airline_names.get(carrier, "Unknown carrier")),
                "planned_flights": planned_flights,
                "cancelled_flights": cancelled_flights,
                "cancellation_rate_pct": round(
                    cancelled_flights / planned_flights * 100, 2
                ),
                "valid_arrivals": valid_arrivals,
                "delayed_arrivals": delayed_arrivals,
                "arrival_delay_rate_pct": (
                    round(delay_rate, 2) if delay_rate is not None else None
                ),
                "on_time_arrivals": on_time_arrivals,
                "arrival_on_time_rate_pct": (
                    round(on_time_rate, 2) if on_time_rate is not None else None
                ),
                "average_delay_minutes": (
                    round(average_delay, 2) if average_delay is not None else None
                ),
                "sample_status": (
                    "sufficient" if sample_is_sufficient else "insufficient"
                ),
                "recommendation_eligible": sample_is_sufficient,
            }
        )

    return results


def build_evidence(
    origin: str,
    dest: str,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    requested_carriers: list[str] | None,
    min_valid_arrivals: int,
    metrics: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "analysis_type": "controlled_route_carrier_comparison",
        "filters": {
            "origin": origin,
            "destination": dest,
            "start_date": start_date.strftime("%Y-%m-%d"),
            "end_date": end_date.strftime("%Y-%m-%d"),
            "requested_carriers": requested_carriers,
        },
        "metric_contract": {
            "delay_threshold_minutes": DELAY_THRESHOLD_MINUTES,
            "minimum_valid_arrivals": min_valid_arrivals,
            "cancellation_proxy": "dep_time is missing",
            "valid_arrival_rule": "arr_delay is not missing",
            "average_delay_rule": "mean arr_delay where arr_delay > 15",
            "rates_unit": "percent",
        },
        "comparison_note": (
            "All carriers use the same origin, destination, and date range. "
            "No composite stability score or absolute winner is calculated."
        ),
        "carriers": metrics,
    }


def print_summary(metrics: list[dict[str, object]]) -> None:
    columns = [
        "carrier",
        "airline_name",
        "planned_flights",
        "cancellation_rate_pct",
        "valid_arrivals",
        "arrival_delay_rate_pct",
        "arrival_on_time_rate_pct",
        "average_delay_minutes",
        "sample_status",
    ]
    print(pd.DataFrame(metrics)[columns].to_string(index=False))


def main() -> None:
    args = parse_args()
    origin = validate_code(args.origin, "origin")
    dest = validate_code(args.dest, "dest")
    carriers = validate_carriers(args.carriers)
    start_date = pd.to_datetime(args.start_date, format="%Y-%m-%d", errors="raise")
    end_date = pd.to_datetime(args.end_date, format="%Y-%m-%d", errors="raise")

    flights, airlines = load_source_data()
    scoped = filter_comparison_scope(
        flights,
        origin,
        dest,
        start_date,
        end_date,
        carriers,
    )
    metrics = calculate_carrier_metrics(
        scoped,
        airlines,
        min_valid_arrivals=args.min_valid_arrivals,
    )
    evidence = build_evidence(
        origin,
        dest,
        start_date,
        end_date,
        carriers,
        args.min_valid_arrivals,
        metrics,
    )

    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print_summary(metrics)
    print(f"\n[PASS] Evidence written to: {output_path}")


if __name__ == "__main__":
    main()
