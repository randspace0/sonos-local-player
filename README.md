# sonos-local-player

A shared music queue for a Sonos speaker. One machine on your Wi-Fi runs a small
web server; everyone else opens a web page, searches YouTube Music and adds songs.
No Sonos app, no Android phone, no cloud account.

```
phone/laptop (web page) -> this server -> yt-dlp downloads AAC -> Sonos pulls it over HTTP
```

The server talks to the speaker over the LAN with [SoCo](https://github.com/SoCo/SoCo)
(Sonos UPnP, port 1400) and gets audio with [yt-dlp](https://github.com/yt-dlp/yt-dlp).

## Run

Needs [uv](https://docs.astral.sh/uv/) (no ffmpeg). Dependencies are
declared inside `server.py`, so there is nothing to install:

```bash
uv run server.py            # PORT=9000 to change the port, SONOS_IP=192.168.x.x to skip scanning
```

1. Open **http://localhost:8000** on the host machine, press **Scan**, choose your speaker, press **Take over**.
2. Share the `guests:` URL the server prints (e.g. `http://192.168.1.20:8000`).
3. Guests search, tap **Add**, and use play/pause, skip and volume.

Allow inbound TCP on the port in the host firewall: the speaker downloads each track from it.

## Features

- Debounced YouTube Music search with thumbnail, artist and duration
- Paste a YouTube link, or a Spotify **track** link (matched to the same song on YouTube; Spotify itself is DRM-protected)
- Queue with "play next", move up, remove, and who added each song
- Play/pause, skip, volume (hold the +/- buttons to repeat), live elapsed time
- Only the host (localhost) can scan for or take over a speaker; guests cannot

## Limits

- No authentication: anyone on your Wi-Fi can queue, skip and change volume. Don't expose the port to the internet.
- Only YouTube and Spotify links are accepted (so guests can't make the host fetch arbitrary URLs).
- Search uses YouTube Music's private web API and can break when YouTube changes it.
- Spotify playlists and albums are not supported.
- Downloading from YouTube may violate its terms of service; use it for music you're entitled to play, at your own risk.
- State is in memory. Restarting the server clears the queue and the chosen speaker.

## Layout

- `server.py`: Flask API, yt-dlp downloads, SoCo playback loop
- `static/index.html`: the whole UI, no build step
- `static/pico.min.css`: [Pico CSS](https://picocss.com) 2.1.1 (MIT), vendored so the page works without a CDN

MIT licensed.
