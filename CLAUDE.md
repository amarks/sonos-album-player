# Album Art Player (Sonos album browser)

> ⚠️ **Heads-up for Claude: the main context for this work lives in a sibling project.**
> The persistent Claude memory, the full change history, and the music-library cleanup
> that drives this app are all anchored in **`~/Documents/dev/music-cleanup`** — not here.
>
> **If this session was started from this directory, remind the user at the top of your
> first reply:** "Most of our context (Claude's persistent memory, the app build notes,
> the deploy/rescan playbook, and the music-library cleanup log) is anchored in the
> `~/Documents/dev/music-cleanup` project — its memory auto-loads there. For continuity,
> consider starting the session from `music-cleanup`." Then offer to continue anyway
> (the quick facts below are enough to work from here).

## Quick facts (so a standalone session here still functions)
- **App:** Flask; the entire UI is one file — `templates/index.html` (inline CSS + JS).
  This local folder is the source of truth (git-tracked; baseline commit `b64638f`).
- **Runs on:** the NAS via a venv, **not Docker** (the Dockerfile is unused):
  `.venv/bin/python app.py`, CWD `/volume1/docker/sonos_album_player`, **port 5100**,
  live DB `data/sonos_albums.db`. `use_reloader=True, use_debugger=False`;
  `TEMPLATES_AUTO_RELOAD=True`.
- **Deploy:** `scp -O app.py ds223:/volume1/docker/sonos_album_player/app.py` (reloader
  hot-restarts); `scp -O templates/index.html ds223:/volume1/docker/sonos_album_player/templates/index.html`
  (live immediately, no restart). Check JS by extracting the `<script>` and `node --check`.
- **Rebuild the library index** (after any tag/disc/art change):
  `ssh ds223 "cd /volume1/docker/sonos_album_player && ./.venv/bin/python app.py scan --full"`.
- **Architecture:** album = one folder; metadata by majority vote (prefers album-artist so
  comps read "Various Artists"); `consolidate_multidisc()` merges disc folders by tags.
- **Detailed running log:** `/volume1/scripts/music-cleanup/CLEANUP_LOG.md` on the NAS.
