from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
import urllib.request
from pathlib import Path

import pyreadr


PACKAGE_VERSION = "1.0.2"
SOURCE_URL = (
    "https://cran.r-project.org/src/contrib/"
    f"nycflights13_{PACKAGE_VERSION}.tar.gz"
)
EXPECTED_ARCHIVE_SHA256 = (
    "0e87c5a4e285f16750e91c75aeba33b1e4682cdabf4a3effe5a1de7398394a1d"
)

EXPECTED_SCHEMAS = {
    "airlines": ["carrier", "name"],
    "airports": [
        "faa", "name", "lat", "lon", "alt", "tz", "dst", "tzone"
    ],
    "flights": [
        "year", "month", "day", "dep_time", "sched_dep_time",
        "dep_delay", "arr_time", "sched_arr_time", "arr_delay",
        "carrier", "flight", "tailnum", "origin", "dest",
        "air_time", "distance", "hour", "minute", "time_hour"
    ],
}

EXPECTED_ROWS = {
    "airlines": 16,
    "airports": 1458,
    "flights": 336776,
}

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
ARCHIVE_PATH = RAW_DIR / f"nycflights13_{PACKAGE_VERSION}.tar.gz"
MANIFEST_PATH = DATA_DIR / "manifest.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def download_archive() -> str:
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    if not ARCHIVE_PATH.exists():
        temporary_path = ARCHIVE_PATH.with_name(
            ARCHIVE_PATH.name + ".part"
        )
        request = urllib.request.Request(
            SOURCE_URL,
            headers={"User-Agent": "flight-ops-ai-review/0.1"},
        )

        with urllib.request.urlopen(request, timeout=120) as response:
            with temporary_path.open("wb") as output_file:
                shutil.copyfileobj(response, output_file)

        temporary_path.replace(ARCHIVE_PATH)

    actual_hash = sha256_file(ARCHIVE_PATH)

    if actual_hash != EXPECTED_ARCHIVE_SHA256:
        raise RuntimeError(
            "Archive SHA-256 mismatch. "
            f"Expected {EXPECTED_ARCHIVE_SHA256}, got {actual_hash}."
        )

    return actual_hash


def extract_and_convert_tables() -> dict[str, dict[str, object]]:
    table_records = {}

    with tarfile.open(ARCHIVE_PATH, "r:gz") as archive:
        for table_name, expected_columns in EXPECTED_SCHEMAS.items():
            member_name = f"nycflights13/data/{table_name}.rda"
            member = archive.getmember(member_name)
            extracted_file = archive.extractfile(member)

            if extracted_file is None:
                raise RuntimeError(f"Could not read {member_name}.")

            rda_path = RAW_DIR / f"{table_name}.rda"

            with extracted_file, rda_path.open("wb") as output_file:
                shutil.copyfileobj(extracted_file, output_file)

            objects = pyreadr.read_r(str(rda_path))

            if table_name in objects:
                frame = objects[table_name]
            elif len(objects) == 1:
                frame = next(iter(objects.values()))
            else:
                raise RuntimeError(
                    f"Unexpected objects in {rda_path.name}."
                )

            actual_columns = [str(column) for column in frame.columns]

            if actual_columns != expected_columns:
                raise RuntimeError(
                    f"Schema mismatch for {table_name}: "
                    f"{actual_columns}"
                )

            if len(frame) != EXPECTED_ROWS[table_name]:
                raise RuntimeError(
                    f"Row-count mismatch for {table_name}: "
                    f"{len(frame)}"
                )

            csv_path = RAW_DIR / f"{table_name}.csv"
            frame.to_csv(csv_path, index=False, lineterminator="\n")

            table_records[table_name] = {
                "rows": len(frame),
                "columns": actual_columns,
                "csv_sha256": sha256_file(csv_path),
            }

            print(f"[PASS] {table_name}: {len(frame):,} rows")

    return table_records


def main() -> None:
    archive_hash = download_archive()
    print(f"[PASS] archive SHA-256: {archive_hash}")

    tables = extract_and_convert_tables()

    manifest = {
        "dataset": "nycflights13",
        "package_version": PACKAGE_VERSION,
        "source_url": SOURCE_URL,
        "license": "CC0",
        "archive_sha256": archive_hash,
        "tables": tables,
    }

    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"[PASS] manifest written: {MANIFEST_PATH}")


if __name__ == "__main__":
    main()