# Contributing

Thanks for wanting to help. This is a small personal project — the bar is "does it work and does the code stay simple." Fixes and features are both welcome.

## Setup

See the [Setup section of the README](README.md#setup). In brief: clone, venv, `pip install -r requirements.txt`, copy `config.json.example` to `config.json`, run `python3 app.py scan`, then `python3 app.py`.

Flask's reloader picks up edits to `app.py` and `templates/index.html` automatically — no restart needed for most changes.

## Code layout

- `app.py` — all Flask routes and the library scanner in one file
- `templates/index.html` — the entire frontend (HTML + CSS + JS inline, no build step)
- `static/` — PWA icons, manifest, service worker
- `CLAUDE.md` — architecture notes worth skimming before diving in

## Testing

There's no automated test suite. Verify changes by exercising the affected feature in the browser. If you touch the frontend, this quick syntax check catches typos:

```bash
sed -n '/<script>/,/<\/script>/p' templates/index.html | sed '1d;$d' | node --check
```

## Submitting a PR

1. Fork and branch off `main`
2. Keep commits scoped — one logical change per commit
3. Commit message: short subject with a categorical prefix (`UI:`, `Queue:`, `Fix ...`, `README:`), then a body explaining the *why* if it's non-obvious
4. Open a PR against `main`

## What's out of scope

- Anything requiring a build step or bundler on the frontend — the "edit one file, refresh browser" workflow is intentional
- Cloud services, sign-ups, analytics — this is designed to run entirely on your LAN
- Alternate music sources (Spotify, Tidal, etc.) — the project scope is local library files
