# HTTP API

JSON over HTTP, no authentication. Errors are a plain-text body with a 4xx/5xx status. "Host only" endpoints return `403 host only` unless the request comes from `localhost` or the machine's own LAN IP.

## Read

### `GET /api/state`
The page polls this every 2 seconds.

```json
{
  "current": { "id": "9bfbada7", "title": "…", "artist": "…", "dur": "3:34", "vid": "m9SMT5ipbxk", "by": "Dina", "status": "ready" },
  "queue": [ { "id": "…", "title": "…", "status": "loading|ready|error", "error": "only when status is error" } ],
  "speaker": { "name": "Living Room", "ip": "192.168.1.30" },
  "playing": true,
  "position": "0:01:23",
  "duration": "0:03:34",
  "volume": 35,
  "autoplay": true,
  "host": false,
  "errors": [ { "t": 1792386510.8, "msg": "download failed: …" } ]
}
```

`speaker` is `null` until the host picks one. `position`/`duration` are `null` when nothing is playing. `errors` is newest first, at most 30.

### `GET /api/search?q=<text>`
Searches YouTube Music (songs only). Returns up to 10 `{ "title", "artist", "dur", "vid", "url" }`. `502` if YouTube can't be reached.

### `GET /api/speakers` (host only)
Scans the network for about 5 seconds. Returns `[ { "name", "ip" } ]`.

### `GET /api/sinks` (host only)
Audio outputs of the host computer (`pactl`): `[ { "name", "desc" } ]`. Pass `local:<name>` (or `local:` for the default output) as `ip` to `POST /api/speaker` to play on the host computer through mpv.

### `GET /api/lyrics?id=<song id>`
Lyrics for a song that is playing or queued: `{ "id", "synced": [ { "t": seconds, "text" } ] | null, "plain": "text" | null }`. Both `null` means LRCLIB has nothing for it. `404` for an unknown id, `502` if LRCLIB can't be reached (not cached).

### `GET /f/<file>`
Serves a downloaded audio file (supports `Range`). This is what the speaker fetches.

## Queue

### `POST /api/add`
`{ "q": "<search text or link>", "vid"?, "artist"?, "dur"?, "by"? }`

Returns the new item. `400` for empty text or a link outside YouTube/Spotify, `409 already in the queue` if `vid` is already queued or playing. Metadata fields are optional hints; invalid values are dropped.

### `POST /api/remove/<id>`
Removes a queued song and deletes its file. `204`.

### `POST /api/move/<id>/<how>`
`how` is `up` (one place earlier) or `top` (play next). `204`, or `404`.

### `POST /api/reorder`
`{ "id": "<id>", "to": <index> }` moves the song so it ends up at position `to` (0-based, clamped). `204`, `404` if it already started or was removed, `400` for a bad body.

## Playback

All of these return `409 no speaker: …` until the host has picked a speaker.

| Endpoint | Body | Effect |
|---|---|---|
| `POST /api/pause` | none | Toggles play/pause |
| `POST /api/skip` | none | Stops the current song; the next one starts within ~2 seconds |
| `POST /api/seek` | `{ "v": seconds }` | Jump to that position in the current song (clamped at 0) |
| `POST /api/volume` | `{ "v": 0-100 }` | Sets volume (clamped) |

## Settings and maintenance

| Endpoint | Body | Effect |
|---|---|---|
| `POST /api/speaker` (host only) | `{ "ip": "192.168.1.30" }` | Take over that speaker; `400` if unreachable |
| `POST /api/autoplay` | `{ "on": true }` | Turn autoplay on or off |
| `POST /api/quality` | `{ "q": "low", "standard" or "high" }` | Download quality for songs added afterwards (m4a bitrate). `state` returns it as `quality` |
| `POST /api/log/clear` | none | Empties the server's error log |

## Item fields

| Field | Meaning |
|---|---|
| `id` | 8-character id, used in the other endpoints |
| `title`, `artist`, `dur` | Display text (`dur` is `m:ss`) |
| `vid` | YouTube video id (thumbnail: `https://i.ytimg.com/vi/<vid>/mqdefault.jpg`) |
| `by` | Name the guest typed, or `Autoplay` |
| `status` | `loading`, `ready`, or `error` |
| `error` | Short reason, only when `status` is `error` |
| `q` | The original text or link (used to retry after a restart) |
| `file` | File name in `data/<port>/cache/`, once downloaded |
