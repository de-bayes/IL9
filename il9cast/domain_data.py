"""Static domain data loaded from JSON files."""
import json
import os

_DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')


def _load_json(name):
    path = os.path.join(_DATA_DIR, name)
    with open(path, encoding='utf-8') as f:
        return json.load(f)


CANDIDATE_PROFILES = _load_json('candidates.json')
FINAL_VOTE_SHARES = _load_json('final_results.json')
FEC_CANDIDATES = _load_json('fec.json')


def fetch_all_fec_data():
    """Return FEC summary data for all tracked candidates."""
    return FEC_CANDIDATES
