# Sonos Album Player — Project Status

## What's Running

- **Local dev**: `http://localhost:5100` — `.venv/bin/python app.py` from project root
- **NAS production**: `http://ds223:5100` — process at `/volume1/docker/sonos_album_player/`, started with `nohup .venv/bin/python app.py &`
- **NAS Python**: Python 3.8 via `.venv` (local dev uses Python 3.14 — no incompatibilities found)
- **NAS config**: `config.json` at `/volume1/docker/sonos_album_player/config.json` — sonos_ip, music_directory, database_path. Do NOT overwrite this when deploying.

## Deploy Procedure

```bash
rsync -av app.py alan@ds223:/volume1/docker/sonos_album_player/
rsync -av templates/index.html alan@ds223:/volume1/docker/sonos_album_player/templates/
# If static assets changed:
rsync -av static/ alan@ds223:/volume1/docker/sonos_album_player/static/

# Restart (only needed for app.py changes — templates reload automatically):
ssh alan@ds223 "fuser -k 5100/tcp 2>/dev/null; sleep 2; cd /volume1/docker/sonos_album_player && nohup .venv/bin/python app.py > /tmp/sonos-player.log 2>&1 &"
```

## Features Built

- **Sonos auto-discovery**: `get_sonos_controller()` tries configured IP first, falls back to `soco.discover()`, caches discovered IP back to `config.json`
- **Group-first speaker picker**: `/api/speakers` returns one entry per Sonos group (not per device). Groups are keyed by coordinator IP. Stereo pairs are deduplicated by name. Multi-room groups show member names as a subtitle (e.g. "Kitchen Ceiling + Living Room + Portable"). Single-room speakers show just their name.
- **Coordinator routing**: `get_coordinator(device)` always routes queue operations to the group coordinator, preventing the "add_to_queue can only be called on coordinator" error.
- **Dark UI redesign**: Full dark theme, larger album cards (175px min), hover `+` button to add to queue
- **Now-playing bar**: Album art thumbnail, track/artist display, volume slider with editable number input (0–100)
- **Queue persistence**: Saved to SQLite `settings` table as JSON, keyed per coordinator IP (`queue_{sonos_ip}`). Restored on page load. Switching speakers loads that speaker's queue.
- **Lazy cover art**: Initial albums payload is ~144KB (metadata only). Art fetched per-album via `/api/albums/<id>/art` as cards scroll into view using `IntersectionObserver`
- **Cover art MIME detection**: `artMime()` in JS detects PNG/GIF/WebP/JPEG from base64 prefix, fixing broken images for non-JPEG artwork
- **Mobile / PWA**: Responsive layout (queue slides in as overlay on mobile), safe-area insets, manifest.json, service worker, apple-touch-icon
- **Default sort**: Artist (alphabetical); Albums sort available as secondary option

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/albums` | All albums (no cover_art), sorted by `sort` param (artist/title/genre) |
| GET | `/api/albums/<id>/art` | Cover art (base64) for one album |
| GET | `/api/albums/<id>/tracks` | Track listing |
| GET/POST | `/api/queue/state` | Persist UI queue as album ID list (per coordinator IP) |
| POST | `/api/queue/add/<id>` | Search Sonos library and enqueue album |
| POST | `/api/queue/clear` | Clear Sonos queue |
| POST | `/api/queue/play` | Play from queue position 0 |
| GET | `/api/speakers` | Discover all Sonos groups; returns `[{coordinator_ip, label, members, selected}]` |
| POST | `/api/speaker/select` | Set active speaker by coordinator IP, persist to config.json |
| GET/POST | `/api/player/volume` | Get or set volume (0–100) |
| GET | `/api/player/status` | Transport state, current track/artist/album |
| POST | `/api/player/play\|pause\|next\|previous` | Playback controls |
| GET | `/manifest.json` | PWA manifest |
| GET | `/sw.js` | Service worker |

## Sonos Topology Notes

- Kitchen Ceiling is the group coordinator for a group that currently includes Living Room and Portable
- Porch and TV are stereo pairs (two physical devices each, same name — deduplicated in the picker)
- Garden (192.168.4.41) appears during discovery but may be offline/unreachable
- Pi-hole local DNS at 192.168.4.240 resolves `ds223` — required for Sonos speakers to stream from the NAS

## Known Issues / Limitations

- **No auth**: App is open to anyone on the local network
- **No incremental scan**: `python app.py scan` wipes and rebuilds the entire DB
- **Cover art MIME type**: Served correctly in the browser via JS detection, but the `/api/albums/<id>/art` endpoint always returns `image/jpeg` in the HTTP Content-Type header (doesn't matter since the data is embedded in JSON)
- **Service worker won't register on HTTP**: iOS SW requires HTTPS; all PWA features still work except offline shell. Fix: expose via Tailscale HTTPS
- **Queue UI vs Sonos queue can diverge**: If you add to queue then switch speakers, the Sonos queue on the new speaker may differ from the UI queue
- **`removeFromQueue` is slow**: Clears Sonos queue and re-adds all remaining albums serially

## Potential Next Steps

- Add HTTPS via Tailscale for full PWA / SW support
- Rescan button in UI (currently requires SSH + `python app.py scan`)
- Shuffle queue
- Recently played
- Multiple zone support / group management (join/unjoin speakers from the UI)
- Auth (basic HTTP auth via Flask or reverse proxy)
- Incremental library scan (hash-based, skip unchanged directories)
- Show currently-playing album highlighted in the grid
