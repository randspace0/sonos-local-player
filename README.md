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
- Queue you can reorder by dragging the handle (or with the Next / up buttons), remove songs, and see who added each one
- Autoplay: when the queue runs dry, a random song from the last song's YouTube Music radio is queued (toggle in the UI)
- Lyrics for the current song from [LRCLIB](https://lrclib.net), highlighted line by line when they are time-synced
- Play/pause, skip, volume (hold the +/- buttons to repeat), live elapsed time
- Error log at the bottom of the page (download, search and speaker failures, with timestamps) and a Clear log button
- Only the host (localhost) can scan for or take over a speaker; guests cannot

## Limits

- No authentication: anyone on your Wi-Fi can queue, skip and change volume. Don't expose the port to the internet.
- Only YouTube and Spotify links are accepted (so guests can't make the host fetch arbitrary URLs).
- Search uses YouTube Music's private web API and can break when YouTube changes it.
- Lyrics depend on LRCLIB having the song; matching is by artist, title and length, so rare songs and some remixes won't have any.
- Spotify playlists and albums are not supported.
- Downloading from YouTube may violate its terms of service; use it for music you're entitled to play, at your own risk.
- Live streams are rejected (they never finish downloading).
- The queue, chosen speaker and autoplay setting are saved under `data/<port>/` (`state.json` and `cache/`) and restored on restart; the song that was playing starts again from the beginning.

## Docs

- [How it works](docs/architecture.md): playback pipeline, autoplay, persistence, who can do what
- [HTTP API](docs/api.md): every endpoint and field
- [Troubleshooting](docs/troubleshooting.md): speaker not found, downloads failing, firewall

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `8000` | Port the web server listens on |
| `SONOS_IP` | none | Use this speaker at startup instead of scanning in the UI |

## Layout

- `server.py`: Flask API, yt-dlp downloads, SoCo playback loop
- `static/index.html`: the whole UI, no build step
- `static/pico.min.css`: [Pico CSS](https://picocss.com) 2.1.1 (MIT), vendored so the page works without a CDN

MIT licensed.
