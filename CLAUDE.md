# Album Art Player

A self-hosted web app for browsing a local music library and playing albums through Sonos speakers or directly on the local device.

## Architecture

- **Backend**: Flask (`app.py`). All endpoints are in this single file.
- **Frontend**: One file — `templates/index.html` — with all CSS and JS inline. No build step.
- **Database**: SQLite (path set in `config.json`). Schema: `albums`, `tracks`, `settings` tables.
- **Sonos control**: `soco` library. Auto-discovery via `soco.discover()` with configured-IP fallback.
- **Audio tagging**: `mutagen` (MP3/ID3, FLAC, M4A).

## Config

Copy `config.json.example` to `config.json` and edit before first run:

```json
{
  "sonos_ip": "192.168.1.100",
  "music_directory": "/path/to/music",
  "database_path": "./sonos_albums.db",
  "port": 5100,
  "items_per_page": 1000
}
```

`sonos_ip` is optional. Leave it empty and the app uses `soco.discover()` on first run, then writes the found IP back to `config.json` automatically. Setting it skips the multicast discovery delay (~5s) on subsequent starts.

## Key flows

- **Library scan**: `python app.py scan [--full]`. Walks `music_directory`, reads tags via mutagen, populates SQLite. Incremental by default (skips unchanged folders via a signature hash); `--full` rebuilds everything.
- **Album model**: one folder = one album. Metadata derived by majority vote across all tracks (prefers `album_artist` tag so compilations show "Various Artists"). `consolidate_multidisc()` merges multi-disc folders by matching tags.
- **Cover art**: extracted from track tags (APIC/FLAC picture/M4A covr), falls back to `cover.jpg` / `folder.jpg` in the album directory. Stored as base64 in SQLite. Served lazily per album via `/api/albums/<id>/art`.
- **Sonos queue**: tracks resolved by searching the Sonos music library index (title + artist match). Queue operations always route to the group coordinator.
- **Local playback**: `/stream/<track_id>` serves audio with HTTP range support. The frontend uses `<audio>` + MediaSession API for lock screen / CarPlay controls.

## Running locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.json.example config.json   # then edit
python app.py scan
python app.py
```

Open `http://localhost:<port>` (default 5100).

## Deploy notes

The app uses `use_reloader=True` so a redeployed `app.py` restarts automatically. `templates/index.html` is picked up immediately without a restart (`TEMPLATES_AUTO_RELOAD=True`). The Dockerfile exists but the recommended deployment is a plain venv process.

## JS syntax check

```bash
sed -n '/<script>/,/<\/script>/p' templates/index.html | sed '1d;$d' | node --check
```
