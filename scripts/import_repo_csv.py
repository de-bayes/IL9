#!/usr/bin/env python3
"""One-shot command to import repository CSV into the configured snapshot JSONL file."""

import argparse
import json
import os


def main():
    parser = argparse.ArgumentParser(description='Import repository CSV snapshot history into JSONL if output is missing/empty.')
    parser.add_argument('--csv-path', default=None, help='Path to source CSV (default: repo il9cast_historical_data.csv).')
    parser.add_argument('--output-path', default=None, help='Path to target JSONL (default: resolved HISTORICAL_DATA_PATH).')
    args = parser.parse_args()

    os.environ.setdefault('IL9_SKIP_STARTUP_TASKS', '1')
    os.environ.setdefault('IL9_DISABLE_SCHEDULER', '1')

    import app as il9_app

    result = il9_app.import_repo_csv_to_volume_if_needed(
        csv_path=args.csv_path or il9_app.REPO_CSV_PATH,
        output_path=args.output_path or il9_app.HISTORICAL_DATA_PATH,
    )
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
