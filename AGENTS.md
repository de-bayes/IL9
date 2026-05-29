# AGENTS.md

## Cursor Cloud specific instructions

### Overview

IL9Cast is a single Flask application (`app.py`) serving an archived Illinois 9th District primary forecast dashboard. No external databases or APIs are required at runtime — all data is served from local files in `data/`.

### Running the dev server

```bash
python3 app.py
```

Server starts on port 8000. Use `python3` (not `python`) as the latter may not exist.

The `EMAIL_SECRET_SALT` warning on startup is expected and non-blocking in development.

### Running tests

```bash
python3 -m unittest tests.test_recovery -v
```

Two tests (`test_recover_merge_with_bridge_and_dedupe`, `test_import_repo_csv_only_when_output_empty`) have pre-existing failures unrelated to environment setup.

### Linting

```bash
flake8 app.py --max-line-length=120
```

Pre-existing style issues (mostly line length) exist in the codebase.

### Key caveats

- The data file `data/historical_snapshots.jsonl.gz` must be decompressed before the app can read it if `data/historical_snapshots.jsonl` doesn't exist. The app handles seeding from `data/seed_snapshots.json` automatically.
- Set `IL9_SKIP_STARTUP_TASKS=1` and `IL9_DISABLE_SCHEDULER=1` env vars when running tests to skip data initialization and background scheduling.
- No `python` binary exists in the Cloud VM — always use `python3`.
- The `~/.local/bin` directory (where pip installs scripts) may need to be added to PATH: `export PATH="$HOME/.local/bin:$PATH"`.
