#!/usr/bin/env python3
"""Import repository CSV into the configured snapshot JSONL (recovery/bootstrap)."""

import argparse
import json
import os
import sys


def main():
    parser = argparse.ArgumentParser(
        description='Import il9cast_historical_data.csv into JSONL when volume is empty or recovery is needed.'
    )
    parser.add_argument('--csv-path', default=None, help='Source CSV (default: repo il9cast_historical_data.csv).')
    parser.add_argument('--output-path', default=None, help='Target JSONL (default: resolved HISTORICAL_DATA_PATH).')
    args = parser.parse_args()

    os.environ.setdefault('IL9_SKIP_STARTUP_TASKS', '1')

    try:
        import app as il9_app

        result = il9_app.import_repo_csv_to_volume_if_needed(
            csv_path=args.csv_path or il9_app.REPO_CSV_PATH,
            output_path=args.output_path or il9_app.HISTORICAL_DATA_PATH,
        )
        print(json.dumps(result, indent=2))

        if result.get('imported') is False and result.get('reason') in (
            'csv_missing', 'csv_empty',
        ):
            sys.exit(2)
        if result.get('imported') is True:
            sys.exit(0)
        sys.exit(0)
    except Exception as e:
        print(json.dumps({'error': str(e)}, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
