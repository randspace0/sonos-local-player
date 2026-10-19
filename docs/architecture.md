# How it works

```
guest browser ──HTTP──▶ server.py (Flask) ──UPnP/SOAP :1400──▶ Sonos speaker
                            │                                      │
                            ├─ yt-dlp: download AAC to data/<port>/cache │
                            └─ GET /f/<file>  ◀── speaker pulls ───┘
```

Everything is one process: `server.py`. The page in `static/index.html` is plain HTML and JS with no build step.

## Playing a song

1. A guest taps **Add**. `POST /api/add` validates the input and appends an item with `status: "loading"`, then starts a download thread.
2. The thread runs yt-dlp (`-f 140/bestaudio[ext=m4a]`, AAC in an m4a file, the format Sonos plays) and writes `cache/<id>.m4a`. The item becomes `ready`, or `error` with a reason.
3. `player_loop` runs every 2 seconds. When the speaker's transport state is `STOPPED` (a track ended) or nothing has started yet, it takes the first `ready` item, sets it as `current`, and calls `play_uri("http://<host-ip>:<port>/f/<file>")`. The speaker then downloads the file from this server. Pausing gives `PAUSED_PLAYBACK`, so a pause is never mistaken for a track ending.
4. The finished song's file is deleted.

A stop is only treated as "finished" if the last poll saw the song within 8 seconds of its end, or a guest pressed Skip. Anything else is logged as `speaker stopped '<song>' early`, because it usually means the speaker couldn't fetch or play the file. A queued file that has gone missing from disk is logged and skipped without calling the speaker.

The loop also refreshes a snapshot (playing, position, duration, volume). `GET /api/state` only reads that snapshot, so any number of phones polling every 2 seconds adds no traffic to the speaker.

## Autoplay

When the queue is empty and the speaker is idle, `autofill()` fetches the YouTube Music radio for the last played song (`watch?v=<id>&list=RD<id>`), drops anything in the last 40 played, picks one at random and queues it as "Autoplay". It only starts after at least one song has played, and the switch next to "Up next" turns it off.

## Lyrics

When the Lyrics panel is open, the page asks `GET /api/lyrics?id=<song id>`. The server cleans the title (`YOASOBI「アイドル」 Official Video` becomes `アイドル`, `(Official Video)` and `[Remastered]` are dropped), searches [LRCLIB](https://lrclib.net) by artist and title, and keeps the best hit whose length is within 8 seconds of the song. It prefers time-synced lyrics, ignores entries with fewer than 4 lines (LRCLIB has junk ones), and caches the answer per song. If LRCLIB can't be reached it returns `502` and does not cache, so Try again works. The page shows the current line in bold and scrolls it into view from the speaker's position; scrolling the box by hand pauses auto-scroll for 5 seconds. Lyrics text is only ever rendered with `textContent`.

## Spotify links

Spotify audio is DRM-protected, so it can't be streamed. For a `open.spotify.com/.../track/...` link the server reads the page's `og:title` and `og:description` to get the artist and title, then downloads the best YouTube match for that text. It can pick a different version of the song.

## Who can do what

- **Host** (requests from `localhost` or the machine's own LAN IP): scan for speakers and take one over.
- **Everyone**: search, add, remove, reorder, skip, pause, volume, autoplay switch, clear the error log.

There is no authentication. Treat the port as LAN-only.

## Input validation

`vid`, `artist`, `dur` and `by` come from the browser and are shown to other guests, so the server validates them (`vid` must match `^[\w-]{11}$`, `dur` must look like `3:34`, strings are length-limited) and the page renders with `textContent` only. Pasted links must be on `youtube.com`, `youtu.be` or `open.spotify.com`; otherwise yt-dlp would fetch any URL on your network for a guest.

## Persistence

`data/<port>/state.json` holds the queue, the song that was playing, the speaker IP, the autoplay switch and the recently-played list. It is rewritten (temp file, then rename) after every change. `cache/` holds the audio files.

On startup `restore()`:
- puts the song that was playing back at the front of the queue (it restarts from 0:00),
- re-queues songs whose file still exists, and restarts downloads that were unfinished,
- deletes files in `data/<port>/cache/` that nothing refers to,
- reconnects to the saved speaker, or logs an error if it is unreachable.

## Files

| Path | What |
|---|---|
| `server.py` | API, downloads, playback loop, persistence |
| `static/index.html` | The whole UI |
| `static/pico.min.css` | Pico CSS 2.1.1 (MIT), vendored |
| `data/<port>/` | `state.json` and `cache/`, one folder per port so two servers never share files; git-ignored |
