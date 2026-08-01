#!/usr/bin/env python3
"""
Sonos Album Player - Backend Service
Run with: python app.py
"""

from flask import Flask, render_template, jsonify, request, send_from_directory, Response
from flask_cors import CORS
import sqlite3
import json
import os
import sys
from pathlib import Path
import base64
import time
import threading
from mutagen import File
from mutagen.id3 import ID3, APIC
from mutagen.flac import FLAC, Picture
from mutagen.mp4 import MP4
import soco
from soco.exceptions import SoCoException
import logging

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load configuration
CONFIG_FILE = 'config.json'
config = {}

def load_config():
    global config
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
    else:
        config = {
            "sonos_ip": "192.168.1.100",
            "music_directory": "/volume1/music",
            "database_path": "./sonos_albums.db",
            "port": 5000,
            "items_per_page": 50
        }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        logger.info(f"Created default config file: {CONFIG_FILE}")
    return config

# Database functions
def init_db():
    """Initialize the SQLite database"""
    conn = sqlite3.connect(config['database_path'])
    c = conn.cursor()

    # WAL lets the page keep reading while a background rescan writes,
    # so the incremental scan never blocks album/art requests.
    c.execute('PRAGMA journal_mode=WAL')

    c.execute('''CREATE TABLE IF NOT EXISTS albums
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  title TEXT NOT NULL,
                  artist TEXT,
                  genre TEXT,
                  year INTEGER,
                  path TEXT UNIQUE NOT NULL,
                  cover_art TEXT,
                  track_count INTEGER DEFAULT 0,
                  scan_sig TEXT)''')

    # Migrate existing databases that predate the scan_sig column
    try:
        c.execute('ALTER TABLE albums ADD COLUMN scan_sig TEXT')
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    c.execute('''CREATE TABLE IF NOT EXISTS tracks
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  album_id INTEGER,
                  track_number INTEGER,
                  title TEXT,
                  path TEXT UNIQUE NOT NULL,
                  duration INTEGER,
                  FOREIGN KEY (album_id) REFERENCES albums(id))''')
    
    c.execute('''CREATE INDEX IF NOT EXISTS idx_album_artist ON albums(artist)''')
    c.execute('''CREATE INDEX IF NOT EXISTS idx_album_title ON albums(title)''')
    c.execute('''CREATE INDEX IF NOT EXISTS idx_album_genre ON albums(genre)''')

    c.execute('''CREATE TABLE IF NOT EXISTS settings
                 (key TEXT PRIMARY KEY, value TEXT)''')

    conn.commit()
    conn.close()

def get_cover_art(file_path):
    """Extract cover art from audio file and return as base64"""
    try:
        audio = File(file_path)
        if audio is None:
            return None
        
        # MP3
        if isinstance(audio, ID3) or hasattr(audio, 'tags'):
            for tag in audio.tags.values() if hasattr(audio, 'tags') and audio.tags else []:
                if isinstance(tag, APIC):
                    return base64.b64encode(tag.data).decode('utf-8')
        
        # FLAC
        if isinstance(audio, FLAC):
            if audio.pictures:
                return base64.b64encode(audio.pictures[0].data).decode('utf-8')
        
        # M4A/MP4
        if isinstance(audio, MP4):
            if 'covr' in audio.tags:
                return base64.b64encode(audio.tags['covr'][0]).decode('utf-8')
        
    except Exception as e:
        logger.error(f"Error extracting cover art from {file_path}: {e}")

    return None

def get_folder_art(album_dir):
    """Fall back to a cover image sitting alongside the tracks (cover.jpg etc.)."""
    import glob
    preferred = ('cover', 'folder', 'front', 'albumart', 'album')
    img_ext = ('.jpg', '.jpeg', '.png')
    candidates = []
    try:
        for f in glob.glob(os.path.join(str(album_dir), '*')):
            ext = os.path.splitext(f)[1].lower()
            if ext in img_ext:
                stem = os.path.splitext(os.path.basename(f))[0].lower()
                # Preferred names first, then any image
                rank = preferred.index(stem) if stem in preferred else len(preferred)
                candidates.append((rank, f))
        for _rank, f in sorted(candidates):
            with open(f, 'rb') as fh:
                return base64.b64encode(fh.read()).decode('utf-8')
    except Exception as e:
        logger.warning(f"Error reading folder art in {album_dir}: {e}")
    return None

def _norm(s):
    """Lowercase alphanumeric-only form, for fuzzy artist/album matching."""
    return ''.join(ch for ch in (s or '').lower() if ch.isalnum())

def _names_match(query, candidate):
    """True if two names correspond (either contains the other, normalized)."""
    q, c = _norm(query), _norm(candidate)
    if not q or not c:
        return False
    return q in c or c in q

def fetch_art_online(artist, album):
    """Look up album art via the iTunes Search API. Returns base64 JPEG or None.

    Only accepts a result whose artist AND album name actually correspond to the
    query, so a fuzzy near-miss never yields the wrong cover.
    """
    if not artist or not album:
        return None
    if _norm(artist) in ('', 'unknownartist', 'unknown') or _norm(album).startswith('unknown'):
        return None
    try:
        import urllib.request, urllib.parse, json as _json
        term = urllib.parse.quote(f"{artist} {album}")
        url = f"https://itunes.apple.com/search?term={term}&entity=album&limit=5"
        req = urllib.request.Request(url, headers={'User-Agent': 'sonos-album-player/1.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read().decode('utf-8'))
        for r in (data.get('results') or []):
            if _names_match(artist, r.get('artistName')) and \
               _names_match(album, r.get('collectionName')):
                art_url = r.get('artworkUrl100') or ''
                if not art_url:
                    continue
                # iTunes serves a small thumb by default; request a larger render
                art_url = art_url.replace('100x100bb', '600x600bb').replace('100x100', '600x600')
                with urllib.request.urlopen(
                        urllib.request.Request(art_url, headers={'User-Agent': 'sonos-album-player/1.0'}),
                        timeout=10) as img_resp:
                    img = img_resp.read()
                if img[:2] == b'\xff\xd8':  # sane JPEG
                    logger.info(f"Fetched online art for {artist} - {album}")
                    return base64.b64encode(img).decode('utf-8')
    except Exception as e:
        logger.warning(f"Online art lookup failed for {artist} - {album}: {e}")
    return None

def compute_album_signature(files):
    """Build a change-detection signature for an album folder.

    Combines the file count with the newest file modification time, so the
    signature changes when tracks are added, removed, or re-tagged in place.
    Only stats files (no content reads), so it stays fast over network shares.
    """
    latest_mtime = 0.0
    for f in files:
        try:
            mtime = f.stat().st_mtime
            if mtime > latest_mtime:
                latest_mtime = mtime
        except OSError:
            pass
    return f"{len(files)}:{latest_mtime:.6f}"

def scan_music_library(full=False):
    """Scan music directory and populate database.

    By default this is incremental: albums whose folder signature is unchanged
    since the last scan are skipped without reading tags or cover art. Pass
    full=True to force a complete rebuild from scratch.
    """
    music_dir = Path(config['music_directory'])
    if not music_dir.exists():
        logger.error(f"Music directory not found: {music_dir}")
        return {'error': 'music directory not found'}

    conn = sqlite3.connect(config['database_path'], timeout=30)
    c = conn.cursor()
    # Wait rather than error if a page read briefly holds the DB
    c.execute('PRAGMA busy_timeout=30000')

    if full:
        # Clear existing data for a fresh rebuild
        logger.info("Clearing existing database (full rebuild)...")
        c.execute('DELETE FROM tracks')
        c.execute('DELETE FROM albums')
        conn.commit()

    audio_extensions = {'.mp3', '.flac', '.m4a', '.ogg', '.wav', '.aac'}
    
    # Directories to exclude
    excluded_dirs = {
        '#recycle', '@eaDir', '.DS_Store', 'Thumbs.db', 
        '@Transcode', '#snapshot', '.@__thumb', '@tmp',
        '.AppleDouble', '.TemporaryItems', '.Trashes'
    }
    
    # Group files by directory (assuming one album per directory)
    albums = {}
    
    logger.info("Scanning music library...")
    for file_path in music_dir.rglob('*'):
        # Skip excluded directories
        if any(excluded in file_path.parts for excluded in excluded_dirs):
            continue
            
        if file_path.suffix.lower() in audio_extensions:
            album_dir = file_path.parent
            if album_dir not in albums:
                albums[album_dir] = []
            albums[album_dir].append(file_path)
    
    logger.info(f"Found {len(albums)} potential albums")

    # Load existing album ids/signatures so we can skip unchanged folders
    existing = {}
    for row in c.execute('SELECT path, id, scan_sig FROM albums'):
        existing[row[0]] = (row[1], row[2])
    seen_paths = set()
    skipped = 0
    added = 0
    updated = 0

    processed = 0
    for album_dir, files in albums.items():
        processed += 1
        album_path = str(album_dir)
        seen_paths.add(album_path)

        # Skip folders whose signature is unchanged since the last scan
        signature = compute_album_signature(files)
        prior = existing.get(album_path)
        if not full and prior and prior[1] == signature:
            skipped += 1
            continue

        try:
            # Get metadata from first file
            first_file = File(str(files[0]))
            if first_file is None:
                continue
            
            # Debug: print what we're getting
            logger.debug(f"File type: {type(first_file)}")
            logger.debug(f"File path: {files[0]}")
            
            # Extract metadata with better approach
            album_title = None
            artist = None
            genre = None
            year = None
            
            # Try to get tags based on file type
            if hasattr(first_file, 'tags') and first_file.tags:
                logger.debug(f"Available tags: {list(first_file.tags.keys())}")
                
                # For ID3 tags (MP3)
                if hasattr(first_file.tags, 'getall'):
                    album_vals = first_file.tags.getall('TALB')
                    if album_vals:
                        album_title = str(album_vals[0])
                    
                    artist_vals = first_file.tags.getall('TPE1') or first_file.tags.getall('TPE2')
                    if artist_vals:
                        artist = str(artist_vals[0])
                    
                    genre_vals = first_file.tags.getall('TCON')
                    if genre_vals:
                        genre = str(genre_vals[0])
                    
                    year_vals = first_file.tags.getall('TDRC')
                    if year_vals:
                        try:
                            year = int(str(year_vals[0])[:4])
                        except:
                            pass
                
                # For Vorbis Comments (FLAC, OGG) and MP4
                else:
                    # Try dictionary-style access
                    for album_key in ['album', 'ALBUM', 'Album', '\xa9alb']:
                        if album_key in first_file.tags:
                            val = first_file.tags[album_key]
                            album_title = str(val[0]) if isinstance(val, list) else str(val)
                            break
                    
                    for artist_key in ['artist', 'ARTIST', 'Artist', 'albumartist', 'ALBUMARTIST', '\xa9ART', 'aART']:
                        if artist_key in first_file.tags:
                            val = first_file.tags[artist_key]
                            artist = str(val[0]) if isinstance(val, list) else str(val)
                            break
                    
                    for genre_key in ['genre', 'GENRE', 'Genre', '\xa9gen']:
                        if genre_key in first_file.tags:
                            val = first_file.tags[genre_key]
                            genre = str(val[0]) if isinstance(val, list) else str(val)
                            break
                    
                    for year_key in ['date', 'DATE', 'Date', 'year', 'YEAR', '\xa9day']:
                        if year_key in first_file.tags:
                            val = first_file.tags[year_key]
                            year_str = str(val[0]) if isinstance(val, list) else str(val)
                            try:
                                year = int(year_str[:4])
                            except:
                                pass
                            break
            
            # Try info attribute as fallback
            if not album_title and hasattr(first_file, 'info'):
                if hasattr(first_file.info, 'album'):
                    album_title = first_file.info.album
            
            # Fallbacks
            if not album_title:
                album_title = album_dir.name
            if not artist:
                artist = 'Unknown Artist'
            if not genre:
                genre = 'Unknown'
            
            logger.info(f"[{processed}/{len(albums)}] Indexing: {artist} - {album_title}")
            
            # Cover art: embedded first, then a folder image, then online lookup
            cover_art = get_cover_art(str(files[0]))
            if not cover_art:
                cover_art = get_folder_art(album_dir)
            if not cover_art and config.get('fetch_art_online', True):
                cover_art = fetch_art_online(artist, album_title)

            # Insert or update the album row (prior is preloaded above)
            if prior:
                album_id = prior[0]
                updated += 1
                c.execute('''UPDATE albums SET title=?, artist=?, genre=?, year=?,
                            cover_art=?, track_count=?, scan_sig=? WHERE id=?''',
                         (album_title, artist, genre, year, cover_art, len(files), signature, album_id))
                # Drop stale tracks so removed/renamed files don't linger
                c.execute('DELETE FROM tracks WHERE album_id=?', (album_id,))
            else:
                added += 1
                c.execute('''INSERT INTO albums (title, artist, genre, year, path, cover_art, track_count, scan_sig)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                         (album_title, artist, genre, year, str(album_dir), cover_art, len(files), signature))
                album_id = c.lastrowid

            # Add tracks
            for track_file in files:
                track_audio = File(str(track_file))
                if track_audio:
                    track_title = track_file.name
                    track_num = 0
                    
                    if hasattr(track_audio, 'tags') and track_audio.tags:
                        # Get track title
                        if hasattr(track_audio.tags, 'getall'):
                            title_vals = track_audio.tags.getall('TIT2')
                            if title_vals:
                                track_title = str(title_vals[0])
                            
                            num_vals = track_audio.tags.getall('TRCK')
                            if num_vals:
                                track_num_str = str(num_vals[0])
                                try:
                                    track_num = int(track_num_str.split('/')[0])
                                except:
                                    pass
                        else:
                            for title_key in ['title', 'TITLE', 'Title', '\xa9nam']:
                                if title_key in track_audio.tags:
                                    val = track_audio.tags[title_key]
                                    track_title = str(val[0]) if isinstance(val, list) else str(val)
                                    break
                            
                            for num_key in ['tracknumber', 'TRACKNUMBER', 'trkn']:
                                if num_key in track_audio.tags:
                                    val = track_audio.tags[num_key]
                                    track_num_val = val[0] if isinstance(val, list) else val
                                    # Handle tuple format from MP4
                                    if isinstance(track_num_val, tuple):
                                        track_num = track_num_val[0]
                                    else:
                                        try:
                                            track_num_str = str(track_num_val)
                                            track_num = int(track_num_str.split('/')[0])
                                        except:
                                            pass
                                    break
                    
                    c.execute('''INSERT OR REPLACE INTO tracks (album_id, track_number, title, path)
                                VALUES (?, ?, ?, ?)''',
                             (album_id, track_num, track_title, str(track_file)))
            
            conn.commit()
            
        except Exception as e:
            logger.error(f"Error processing album {album_dir}: {e}")
            continue

    # Prune albums (and their tracks) whose folders no longer exist on disk
    removed = 0
    for path, (album_id, _sig) in existing.items():
        if path not in seen_paths:
            c.execute('DELETE FROM tracks WHERE album_id=?', (album_id,))
            c.execute('DELETE FROM albums WHERE id=?', (album_id,))
            removed += 1
    conn.commit()

    conn.close()
    logger.info(
        f"Music library scan complete "
        f"({added} added, {updated} updated, {skipped} unchanged, {removed} removed)"
    )
    return {
        'added': added,
        'updated': updated,
        'skipped': skipped,
        'removed': removed,
        'total_albums': len(albums),
    }

# ---- Background rescan (triggered from the browser on page load) ----
_scan_lock = threading.Lock()
_scan_state = {
    'running': False,
    'last_started': 0.0,
    'last_finished': 0.0,
    'last_result': None,
}

def _persist_last_scan(ts):
    """Record the last scan time so the cooldown survives app restarts."""
    try:
        conn = sqlite3.connect(config['database_path'], timeout=30)
        conn.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)',
                     ('last_scan_time', str(ts)))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Could not persist last_scan_time: {e}")

def _last_scan_time():
    """Most recent scan time, preferring the persisted value across restarts."""
    if _scan_state['last_finished']:
        return _scan_state['last_finished']
    try:
        conn = sqlite3.connect(config['database_path'], timeout=30)
        row = conn.execute("SELECT value FROM settings WHERE key='last_scan_time'").fetchone()
        conn.close()
        if row:
            return float(row[0])
    except Exception:
        pass
    return 0.0

def _run_scan_bg():
    try:
        result = scan_music_library(full=False)
        _scan_state['last_result'] = result
    except Exception as e:
        logger.error(f"Background rescan failed: {e}")
        _scan_state['last_result'] = {'error': str(e)}
    finally:
        finished = time.time()
        _scan_state['last_finished'] = finished
        _scan_state['running'] = False
        _persist_last_scan(finished)

# Sonos control functions
def get_coordinator(device):
    """Return the group coordinator for a device — required for queue operations."""
    try:
        coordinator = device.group.coordinator
        if coordinator.ip_address != device.ip_address:
            logger.info(f"Routing to group coordinator: {coordinator.ip_address} (selected: {device.ip_address})")
        return coordinator
    except Exception:
        return device

def get_sonos_controller():
    """Get Sonos controller, trying configured IP first then falling back to discovery."""
    sonos_ip = config.get('sonos_ip', '')
    if sonos_ip:
        try:
            device = soco.SoCo(sonos_ip)
            device.get_speaker_info()  # verify reachable
            return get_coordinator(device)
        except Exception:
            logger.warning(f"Configured Sonos IP {sonos_ip} unreachable, attempting discovery...")

    # Fall back to network discovery
    try:
        devices = soco.discover(timeout=5)
        if not devices:
            logger.error("No Sonos devices found on network")
            return None
        device = next(iter(devices))
        logger.info(f"Discovered Sonos device at {device.ip_address}")
        config['sonos_ip'] = device.ip_address
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        return get_coordinator(device)
    except Exception as e:
        logger.error(f"Sonos discovery failed: {e}")
        return None

# API Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/manifest.json')
def manifest():
    return send_from_directory('static', 'manifest.json', mimetype='application/manifest+json')

@app.route('/sw.js')
def service_worker():
    res = send_from_directory('static', 'sw.js', mimetype='application/javascript')
    res.headers['Service-Worker-Allowed'] = '/'
    res.headers['Cache-Control'] = 'no-cache'
    return res

@app.route('/api/speakers')
def get_speakers():
    """Discover all Sonos groups on the network, returning one entry per group."""
    try:
        devices = soco.discover(timeout=5) or set()
    except Exception as e:
        logger.error(f"Speaker discovery failed: {e}")
        return jsonify([])

    configured_ip = config.get('sonos_ip', '')
    seen_coordinators = set()
    groups = []

    for device in devices:
        try:
            group = device.group
            coordinator = group.coordinator
            coord_ip = coordinator.ip_address
            if coord_ip in seen_coordinators:
                continue
            seen_coordinators.add(coord_ip)

            members_raw = []
            for member in group.members:
                try:
                    members_raw.append({'ip': member.ip_address, 'name': member.player_name})
                except Exception:
                    members_raw.append({'ip': member.ip_address, 'name': member.ip_address})

            member_ips = {m['ip'] for m in members_raw}

            # Deduplicate by name — stereo pairs appear as two devices with identical names
            seen_names = {}
            for m in members_raw:
                if m['name'] not in seen_names or m['ip'] == coord_ip:
                    seen_names[m['name']] = m
            members = sorted(seen_names.values(), key=lambda m: m['name'])

            # Build label with coordinator first, then other rooms
            if len(members) == 1:
                label = members[0]['name']
            else:
                coord_name = next((m['name'] for m in members if m['ip'] == coord_ip), members[0]['name'])
                other_names = sorted(m['name'] for m in members if m['ip'] != coord_ip)
                label = coord_name + ' + ' + ' + '.join(other_names)

            groups.append({
                'coordinator_ip': coord_ip,
                'label': label,
                'members': members,
                'selected': configured_ip in member_ips,
            })
        except Exception:
            pass

    groups.sort(key=lambda g: g['label'])
    return jsonify(groups)

@app.route('/api/speaker/select', methods=['POST'])
def select_speaker():
    """Persist the chosen speaker IP"""
    data = request.get_json()
    ip = (data or {}).get('ip', '').strip()
    if not ip:
        return jsonify({'error': 'ip required'}), 400
    config['sonos_ip'] = ip
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)
    logger.info(f"Speaker changed to {ip}")
    return jsonify({'success': True})

@app.route('/api/albums')
def get_albums():
    """Get all albums with optional sorting"""
    sort_by = request.args.get('sort', 'title')  # title, artist, genre
    page = int(request.args.get('page', 1))
    per_page = config.get('items_per_page', 50)
    
    valid_sorts = {'title': 'title', 'artist': 'artist', 'genre': 'genre'}
    sort_column = valid_sorts.get(sort_by, 'title')
    
    conn = sqlite3.connect(config['database_path'])
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    offset = (page - 1) * per_page
    c.execute(f'''SELECT id, title, artist, genre, year, track_count
                  FROM albums ORDER BY {sort_column} COLLATE NOCASE
                  LIMIT ? OFFSET ?''', (per_page, offset))
    
    albums = [dict(row) for row in c.fetchall()]
    
    c.execute('SELECT COUNT(*) FROM albums')
    total = c.fetchone()[0]
    
    conn.close()
    
    return jsonify({
        'albums': albums,
        'total': total,
        'page': page,
        'per_page': per_page,
        'has_more': offset + per_page < total
    })

@app.route('/api/rescan', methods=['POST'])
def trigger_rescan():
    """Kick off an incremental scan in the background and return immediately.

    Guarded by a lock (one scan at a time across tabs/refreshes) and a cooldown
    so repeated page loads don't re-scan constantly. Pass ?force=1 to bypass the
    cooldown.
    """
    now = time.time()
    cooldown = config.get('rescan_cooldown_seconds', 600)
    force = request.args.get('force') == '1'

    with _scan_lock:
        if _scan_state['running']:
            return jsonify({'status': 'running'})
        since = now - _last_scan_time()
        if not force and since < cooldown:
            return jsonify({'status': 'skipped', 'reason': 'cooldown',
                            'seconds_ago': int(since)})
        _scan_state['running'] = True
        _scan_state['last_started'] = now

    threading.Thread(target=_run_scan_bg, daemon=True).start()
    return jsonify({'status': 'started'})

@app.route('/api/rescan/status')
def rescan_status():
    """Report whether a scan is running and the summary of the last one."""
    return jsonify({
        'running': _scan_state['running'],
        'last_finished': _scan_state['last_finished'],
        'last_result': _scan_state['last_result'],
    })

@app.route('/api/albums/<int:album_id>/art')
def get_album_art(album_id):
    conn = sqlite3.connect(config['database_path'])
    c = conn.cursor()
    c.execute('SELECT cover_art FROM albums WHERE id = ?', (album_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'id': album_id, 'cover_art': row[0]})

@app.route('/api/albums/<int:album_id>/tracks')
def get_album_tracks(album_id):
    """Get all tracks for an album"""
    conn = sqlite3.connect(config['database_path'])
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    c.execute('''SELECT * FROM tracks WHERE album_id = ? 
                 ORDER BY track_number''', (album_id,))
    tracks = [dict(row) for row in c.fetchall()]
    
    conn.close()
    return jsonify(tracks)

@app.route('/api/queue/add/<int:album_id>', methods=['POST'])
def add_to_queue(album_id):
    """Add album to Sonos queue using music library search"""
    sonos = get_sonos_controller()
    if not sonos:
        return jsonify({'error': 'Cannot connect to Sonos'}), 500
    
    conn = sqlite3.connect(config['database_path'])
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # Get album info
    c.execute('''SELECT title, artist FROM albums WHERE id = ?''', (album_id,))
    album_info = c.fetchone()
    
    if not album_info:
        conn.close()
        return jsonify({'error': 'Album not found'}), 404
    
    album_title = album_info['title']
    artist = album_info['artist']
    conn.close()
    
    try:
        # Search for album in Sonos music library
        logger.info(f"Searching Sonos library for: {artist} - {album_title}")
        results = sonos.music_library.get_albums(search_term=album_title)
        
        logger.info(f"Found {len(results)} results")
        
        # Find the best match
        album_item = None
        for result in results:
            logger.info(f"  Result: {result.creator} - {result.title}")
            # Try to match both title and artist
            if album_title.lower() in result.title.lower():
                if artist and artist.lower() in result.creator.lower():
                    album_item = result
                    logger.info(f"  ✓ Matched on title and artist")
                    break
                elif not album_item:  # Keep first title match as fallback
                    album_item = result
                    logger.info(f"  ~ Partial match on title only")
        
        if not album_item:
            logger.warning(f"Album not found in Sonos library: {artist} - {album_title}")
            return jsonify({'error': 'Album not found in Sonos library', 
                          'album': album_title,
                          'artist': artist,
                          'suggestion': 'Make sure Sonos has indexed this music'}), 404
        
        # Add all tracks from the album to queue
        logger.info(f"Adding album to queue: {album_item.item_id}")
        
        # Browse the album to get its tracks
        tracks = sonos.music_library.browse(album_item)
        
        tracks_added = 0
        for track in tracks:
            logger.info(f"  Adding track: {track.title}")
            sonos.add_to_queue(track)
            tracks_added += 1
        
        logger.info(f"Added {tracks_added} tracks from '{album_title}' to queue")
        
        # Get current queue length to help debug
        queue_info = sonos.get_queue()
        logger.info(f"Queue now has {len(queue_info)} items")
        
        return jsonify({'success': True, 'tracks_added': tracks_added, 'queue_length': len(queue_info)})
        
    except Exception as e:
        logger.error(f"Error adding album to queue: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@app.route('/api/queue/clear', methods=['POST'])
def clear_queue():
    """Clear Sonos queue"""
    sonos = get_sonos_controller()
    if not sonos:
        return jsonify({'error': 'Cannot connect to Sonos'}), 500
    
    try:
        sonos.clear_queue()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error clearing queue: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/queue/play', methods=['POST'])
def play_queue():
    """Stop current source and play from queue"""
    sonos = get_sonos_controller()
    if not sonos:
        return jsonify({'error': 'Cannot connect to Sonos'}), 500
    
    try:
        # Get the queue
        queue = sonos.get_queue()
        
        if len(queue) == 0:
            return jsonify({'error': 'Queue is empty'}), 400
        
        # Stop current playback
        sonos.stop()
        
        # Play from queue (track 0 is the first track)
        sonos.play_from_queue(0)
        
        logger.info(f"Started playing queue with {len(queue)} tracks")
        return jsonify({'success': True, 'queue_length': len(queue)})
    except Exception as e:
        logger.error(f"Error playing queue: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/player/play', methods=['POST'])
def play():
    sonos = get_sonos_controller()
    if not sonos:
        return jsonify({'error': 'Cannot connect to Sonos'}), 500
    try:
        sonos.play()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/player/pause', methods=['POST'])
def pause():
    sonos = get_sonos_controller()
    if not sonos:
        return jsonify({'error': 'Cannot connect to Sonos'}), 500
    try:
        sonos.pause()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/player/next', methods=['POST'])
def next_track():
    sonos = get_sonos_controller()
    if not sonos:
        return jsonify({'error': 'Cannot connect to Sonos'}), 500
    try:
        sonos.next()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/player/previous', methods=['POST'])
def previous_track():
    sonos = get_sonos_controller()
    if not sonos:
        return jsonify({'error': 'Cannot connect to Sonos'}), 500
    try:
        sonos.previous()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/queue/state', methods=['GET', 'POST'])
def queue_state():
    """Persist the UI queue as an ordered list of album IDs"""
    conn = sqlite3.connect(config['database_path'])
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    queue_key = f"queue_{config.get('sonos_ip', 'default')}"

    if request.method == 'POST':
        album_ids = (request.get_json() or {}).get('album_ids', [])
        c.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)',
                  (queue_key, json.dumps(album_ids)))
        conn.commit()
        conn.close()
        return jsonify({'success': True})

    # GET — return full album objects in saved order
    c.execute('SELECT value FROM settings WHERE key = ?', (queue_key,))
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify([])

    album_ids = json.loads(row['value'])
    if not album_ids:
        conn.close()
        return jsonify([])

    placeholders = ','.join('?' * len(album_ids))
    c.execute(f'SELECT id, title, artist, genre, year, cover_art, track_count '
              f'FROM albums WHERE id IN ({placeholders})', album_ids)
    by_id = {r['id']: dict(r) for r in c.fetchall()}
    conn.close()

    # Return in the saved order
    return jsonify([by_id[aid] for aid in album_ids if aid in by_id])

@app.route('/api/player/volume', methods=['GET', 'POST'])
def player_volume():
    sonos = get_sonos_controller()
    if not sonos:
        return jsonify({'error': 'Cannot connect to Sonos'}), 500
    try:
        if request.method == 'POST':
            vol = int(request.get_json().get('volume', 0))
            sonos.volume = max(0, min(100, vol))
        return jsonify({'volume': sonos.volume})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/player/status')
def player_status():
    """Get current player status"""
    sonos = get_sonos_controller()
    if not sonos:
        return jsonify({'error': 'Cannot connect to Sonos'}), 500
    
    try:
        transport_info = sonos.get_current_transport_info()
        track_info = sonos.get_current_track_info()
        
        return jsonify({
            'state': transport_info['current_transport_state'],
            'track': track_info.get('title', ''),
            'artist': track_info.get('artist', ''),
            'album': track_info.get('album', '')
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def backfill_art():
    """Fill in cover art for albums that currently have none, without rescanning.

    Tries a folder image, then an online lookup. Only updates rows we can fill,
    so it is safe to re-run.
    """
    conn = sqlite3.connect(config['database_path'], timeout=30)
    conn.execute('PRAGMA busy_timeout=30000')
    c = conn.cursor()
    rows = c.execute('SELECT id, artist, title, path FROM albums WHERE cover_art IS NULL').fetchall()
    logger.info(f"Backfilling art for {len(rows)} album(s) with no cover")
    filled = 0
    for album_id, artist, title, path in rows:
        art = get_folder_art(path)
        if not art and config.get('fetch_art_online', True):
            art = fetch_art_online(artist, title)
            time.sleep(1)  # be polite to the iTunes API
        if art:
            c.execute('UPDATE albums SET cover_art=? WHERE id=?', (art, album_id))
            conn.commit()
            filled += 1
            logger.info(f"  filled: {artist} - {title}")
        else:
            logger.info(f"  no art found: {artist} - {title}")
    conn.close()
    logger.info(f"Backfill complete: {filled}/{len(rows)} filled")
    return {'filled': filled, 'total': len(rows)}

if __name__ == '__main__':
    config = load_config()

    # Always run init so new tables/indexes are created if missing
    init_db()

    # Check if we should scan library (command line argument)
    if len(sys.argv) > 1 and sys.argv[1] == 'scan':
        full = '--full' in sys.argv[2:]
        logger.info(
            "Starting %s music library scan...",
            "full" if full else "incremental",
        )
        scan_music_library(full=full)
        logger.info("Scan complete. Exiting.")
        sys.exit(0)

    # Backfill missing cover art for albums that have none
    if len(sys.argv) > 1 and sys.argv[1] == 'backfill-art':
        backfill_art()
        logger.info("Backfill complete. Exiting.")
        sys.exit(0)

    port = config.get('port', 5000)
    logger.info(f"Starting Sonos Album Player on port {port}")
    logger.info(f"Run 'python app.py scan' to index your music library")
    
    app.run(host='0.0.0.0', port=port, debug=True, threaded=True)