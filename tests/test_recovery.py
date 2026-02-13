import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('IL9_SKIP_STARTUP_TASKS', '1')
os.environ.setdefault('IL9_DISABLE_SCHEDULER', '1')

import app


class RecoveryAndImportTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)

    def _write_csv(self, rows):
        path = self.root / 'input.csv'
        with path.open('w', encoding='utf-8') as f:
            f.write('timestamp,candidate,probability,hasKalshi\n')
            for r in rows:
                f.write(','.join(r) + '\n')
        return path

    def _write_jsonl(self, name, snapshots):
        path = self.root / name
        with path.open('w', encoding='utf-8') as f:
            for s in snapshots:
                f.write(json.dumps(s) + '\n')
        return path

    def test_recover_csv_only(self):
        csv_path = self._write_csv([
            ('2026-01-01T00:00:00Z', 'Alice', '10.0', 'true'),
            ('2026-01-01T00:03:00Z', 'Alice', '11.0', 'true'),
        ])
        output = self.root / 'out.jsonl'
        stats = app.recover_snapshots_from_csv_and_current(
            csv_path=str(csv_path),
            current_path=str(self.root / 'missing.jsonl'),
            output_path=str(output),
            dry_run=False,
            csv_only=True,
        )
        self.assertEqual(stats['merged_total'], 2)
        loaded = app.read_snapshots_jsonl(str(output))
        self.assertEqual(len(loaded), 2)

    def test_recover_merge_with_bridge_and_dedupe(self):
        csv_path = self._write_csv([
            ('2026-01-01T00:00:00Z', 'Alice', '10.0', 'true'),
            ('2026-01-01T00:03:00Z', 'Alice', '11.0', 'true'),
            ('2026-01-01T00:03:00Z', 'Alice', '11.0', 'true'),
        ])
        current_path = self._write_jsonl('current.jsonl', [
            {'timestamp': '2026-01-01T00:09:00Z', 'candidates': [{'name': 'Alice', 'probability': 15.0, 'hasKalshi': True}]}
        ])
        output = self.root / 'merged.jsonl'
        stats = app.recover_snapshots_from_csv_and_current(
            csv_path=str(csv_path),
            current_path=str(current_path),
            output_path=str(output),
            bridge_interval_minutes=3,
            max_bridge_hours=1,
            dry_run=False,
        )
        self.assertEqual(stats['bridge_created'], 1)
        self.assertEqual(stats['merged_total'], 4)
        loaded = app.read_snapshots_jsonl(str(output))
        self.assertEqual(len(loaded), 4)

    def test_empty_invalid_csv_handling(self):
        empty_csv = self.root / 'empty.csv'
        empty_csv.write_text('timestamp,candidate,probability,hasKalshi\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            app.recover_snapshots_from_csv_and_current(
                csv_path=str(empty_csv),
                current_path=str(self.root / 'missing.jsonl'),
                output_path=str(self.root / 'out.jsonl'),
                dry_run=True,
                csv_only=True,
            )

    def test_import_repo_csv_only_when_output_empty(self):
        csv_path = self._write_csv([
            ('2026-01-01T00:00:00Z', 'Alice', '10.0', 'true'),
        ])
        output = self.root / 'imported.jsonl'
        result = app.import_repo_csv_to_volume_if_needed(str(csv_path), str(output))
        self.assertTrue(result['imported'])
        existing = app.read_snapshots_jsonl(str(output))
        self.assertEqual(len(existing), 1)

        result_again = app.import_repo_csv_to_volume_if_needed(str(csv_path), str(output))
        self.assertFalse(result_again['imported'])
        self.assertEqual(result_again['reason'], 'output_has_data')

    def test_backfill_repo_csv_history_when_output_starts_later(self):
        csv_path = self._write_csv([
            ('2026-01-01T00:00:00Z', 'Alice', '10.0', 'true'),
            ('2026-01-01T00:03:00Z', 'Alice', '11.0', 'true'),
        ])
        output = self._write_jsonl('existing.jsonl', [
            {'timestamp': '2026-01-01T00:09:00Z', 'candidates': [{'name': 'Alice', 'probability': 12.0, 'hasKalshi': True}]}
        ])

        result = app.backfill_repo_csv_history_if_needed(str(csv_path), str(output), bridge_interval_minutes=3, max_bridge_hours=1)
        self.assertTrue(result['backfilled'])
        merged = app.read_snapshots_jsonl(str(output))
        self.assertGreaterEqual(len(merged), 3)
        self.assertEqual(merged[0]['timestamp'], '2026-01-01T00:00:00Z')

    def test_backfill_skips_when_existing_already_starts_earlier(self):
        csv_path = self._write_csv([
            ('2026-01-01T00:00:00Z', 'Alice', '10.0', 'true'),
        ])
        output = self._write_jsonl('existing.jsonl', [
            {'timestamp': '2025-12-31T23:57:00Z', 'candidates': [{'name': 'Alice', 'probability': 9.0, 'hasKalshi': True}]}
        ])

        result = app.backfill_repo_csv_history_if_needed(str(csv_path), str(output))
        self.assertFalse(result['backfilled'])
        self.assertEqual(result['reason'], 'already_covers_csv_start')


class EndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app.app.test_client()

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.data_path = str(Path(self.tmpdir.name) / 'snapshots.jsonl')
        self.original_data_path = app.HISTORICAL_DATA_PATH
        self.original_cache = dict(app._chart_cache)
        app.HISTORICAL_DATA_PATH = self.data_path
        app._chart_cache = {'data': None, 'time': 0, 'key': None}

    def tearDown(self):
        app.HISTORICAL_DATA_PATH = self.original_data_path
        app._chart_cache = self.original_cache

    def test_chart_returns_payload_shape(self):
        with open(self.data_path, 'w', encoding='utf-8') as f:
            f.write(json.dumps({'timestamp': '2026-01-01T00:00:00Z', 'candidates': [{'name': 'Alice', 'probability': 10.0, 'hasKalshi': True}]}) + '\n')
        resp = self.client.get('/api/snapshots/chart?period=all')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertIn('snapshots', body)
        self.assertIn('gaps', body)
        self.assertGreater(len(body['snapshots']), 0)

    def test_csv_export_header(self):
        with open(self.data_path, 'w', encoding='utf-8') as f:
            f.write(json.dumps({'timestamp': '2026-01-01T00:00:00Z', 'candidates': [{'name': 'Alice, A', 'probability': 'x', 'hasKalshi': True}]}) + '\n')
        resp = self.client.get('/api/download/snapshots/csv')
        self.assertEqual(resp.status_code, 200)
        text = resp.data.decode('utf-8')
        self.assertTrue(text.startswith('timestamp,candidate,probability,hasKalshi'))

    def test_imported_csv_data_drives_chart_payload(self):
        csv_path = str(Path(self.tmpdir.name) / 'history.csv')
        with open(csv_path, 'w', encoding='utf-8') as f:
            f.write('timestamp,candidate,probability,hasKalshi\n')
            f.write('2026-01-01T00:00:00Z,Alice,10.0,true\n')
            f.write('2026-01-01T00:03:00Z,Alice,11.0,true\n')

        result = app.import_repo_csv_to_volume_if_needed(csv_path=csv_path, output_path=self.data_path)
        self.assertTrue(result.get('imported'))

        resp = self.client.get('/api/snapshots/chart?period=all')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertIn('snapshots', body)
        self.assertIn('gaps', body)
        self.assertGreaterEqual(len(body['snapshots']), 2)


if __name__ == '__main__':
    unittest.main()
