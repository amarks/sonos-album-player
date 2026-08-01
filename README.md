# Sonos Album Player

A web-based interface for playing albums on Sonos with drag-and-drop queue management.

## Features

- Browse your music library with album covers
- Sort by Albums, Artists, or Genre
- Drag-and-drop albums to build a queue
- Full Sonos playback controls
- Apple-inspired clean design
- Persistent album database with cover art

## Installation on Synology NAS

### Prerequisites

1. SSH access to your Synology NAS
2. Python 3.8+ installed on your NAS
3. Your Sonos controller's IP address
4. Music files stored on your NAS

### Setup Steps

1. **SSH into your NAS:**
   ```bash
   ssh admin@your-nas-ip
   ```

2. **Create project directory:**
   ```bash
   mkdir -p /volume1/sonos-player
   cd /volume1/sonos-player
   ```

3. **Upload files:**
   - `app.py` - Main backend application
   - `requirements.txt` - Python dependencies
   - Create `templates/` folder and upload `index.html`

4. **Install dependencies:**
   ```bash
   pip3 install -r requirements.txt
   ```

5. **Configure the application:**
   Edit `config.json` (will be created on first run):
   ```json
   {
     "sonos_ip": "192.168.1.100",
     "music_directory": "/volume1/music",
     "database_path": "./sonos_albums.db",
     "port": 5000,
     "items_per_page": 50
   }
   ```

6. **Scan your music library:**
   ```bash
   python3 app.py scan
   ```
   This will create the database and index all albums. May take 10-30 minutes depending on library size.

7. **Start the service:**
   ```bash
   python3 app.py
   ```

## Running as a Service on Synology

### Option 1: Using Task Scheduler (GUI)

1. Open **Control Panel** → **Task Scheduler**
2. Create → **Triggered Task** → **User-defined script**
3. General tab:
   - Task: "Sonos Player"
   - User: Your admin user
4. Task Settings tab:
   - Run command: `python3 /volume1/sonos-player/app.py`
5. Schedule: Boot-up

### Option 2: Using systemd (Advanced)

Create `/etc/systemd/system/sonos-player.service`:

```ini
[Unit]
Description=Sonos Album Player
After=network.target

[Service]
Type=simple
User=admin
WorkingDirectory=/volume1/sonos-player
ExecStart=/usr/bin/python3 /volume1/sonos-player/app.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable sonos-player
sudo systemctl start sonos-player
```

## Usage

1. **Access the interface:**
   Open browser to `http://your-nas-ip:5000`

2. **Browse albums:**
   - Click "Albums", "Artists", or "Genre" to sort
   - Scroll through your collection

3. **Build queue:**
   - Drag album covers from the main grid to the Queue panel on the left
   - Albums will be added to Sonos queue in order

4. **Manage queue:**
   - Drag albums out of the queue to remove them
   - Click "Clear" to empty the entire queue

5. **Control playback:**
   - ⏮ = Previous album (skip back ~20 tracks)
   - ⏪ = Previous track
   - ▶/⏸ = Play/Pause
   - ⏩ = Next track
   - ⏭ = Next album (skip forward ~20 tracks)

## Configuration Details

### config.json Parameters

- **sonos_ip**: IP address of your Sonos controller (required)
- **music_directory**: Path to your music folder on NAS
- **database_path**: Where to store the SQLite database
- **port**: Web server port (default: 5000)
- **items_per_page**: Number of albums to load per page

### Finding Your Sonos IP

1. Open Sonos app
2. Settings → System → About My System
3. Look for IP address of any speaker

## Maintenance

### Re-scan Library

When you add new albums:
```bash
cd /volume1/sonos-player
python3 app.py scan
```

### View Logs

```bash
tail -f /volume1/sonos-player/sonos.log
```

## Troubleshooting

### Cannot connect to Sonos
- Verify Sonos IP in config.json
- Ensure NAS and Sonos are on same network
- Try pinging the Sonos: `ping 192.168.1.100`

### Albums not showing
- Run rescan: `python3 app.py scan`
- Check music_directory path is correct
- Verify NAS has read access to music files

### Cover art not loading
- Ensure music files have embedded album art
- Supported formats: MP3, FLAC, M4A with embedded images

### Music won't play
- Sonos needs network access to music files
- Check that music_directory is accessible via SMB/NFS
- May need to configure Sonos Music Library settings

## Architecture Notes

### Queue Management
The app sends commands directly to Sonos rather than maintaining a separate queue state. When you drag albums to the queue:

1. Album is added to local queue display (instant feedback)
2. API call adds all tracks to Sonos queue
3. Sonos manages actual playback order

This approach means:
- Queue persists across app restarts (stored in Sonos)
- Multiple clients can control the same queue
- No sync issues between app and Sonos state

### File Access
Music files must be accessible to Sonos via your network shares. The app reads metadata for indexing but Sonos plays files directly from the NAS.

## Future Enhancements

Potential improvements:
- Search/filter albums
- Playlist support
- Multiple Sonos zones
- Album shuffle
- Recently played
- Favorites/bookmarks
- Lazy loading for large libraries (currently loads 50 at a time)
- Album grouping by artist/genre folders

## Additional Config Options

Add to config.json:
- **cache_covers**: Pre-load cover art for faster browsing
- **enable_transcoding**: Convert formats on-the-fly
- **sonos_username/password**: For secured Sonos systems
- **theme**: Light/dark mode preference

## License

MIT License - Free to use and modify