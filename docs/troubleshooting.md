# Troubleshooting

The **Error log** at the bottom of the page lists recent failures with timestamps. **Clear log** empties it. The server also prints each one to the terminal prefixed with `error:`.

## Setup

| Symptom | Cause and fix |
|---|---|
| Scan finds nothing | Speaker discovery uses multicast, which many routers block between Wi-Fi and wired devices, or between VLANs. Find the speaker's IP in your router and start with `SONOS_IP=192.168.x.x uv run server.py`. |
| "can't reach speaker" | Wrong IP, or the PC isn't on the same network as the speaker. Test with `curl http://<ip>:1400/xml/device_description.xml`. |
| Phones can't open the guest URL | Use the `guests:` address the server prints, not `localhost`. Guest Wi-Fi and "client isolation" settings stop devices from reaching each other. |
| Song is added but never plays | The speaker downloads files from this PC. Allow inbound TCP on the port (default 8000) in the host firewall, e.g. `sudo ufw allow 8000/tcp`. |
| Guests see "Waiting for the host to pick a speaker" | Open `http://localhost:8000` on the host, press the speaker chip, Scan, Take over. The choice is saved, so this is needed once. |

## Downloads

| Symptom | Cause and fix |
|---|---|
| "live streams aren't supported" | Live streams never finish downloading. Queue a normal video. |
| "Requested format is not available" or random download failures | YouTube changed something. Update yt-dlp: `uv run --upgrade server.py`. yt-dlp may also warn that no JavaScript runtime was found; installing [deno](https://deno.com) lets it extract more formats. |
| "Sign in to confirm you're not a bot" | YouTube is rate-limiting your IP. Wait, or try a different network. |
| Spotify link added the wrong version | Spotify links are matched by artist and title on YouTube. Search by hand and pick the right one. |
| Spotify playlist or album link is rejected | Only track links are supported. |

## Page

| Symptom | Cause and fix |
|---|---|
| Search says "Search failed" | The YouTube Music web API changed or is unreachable. The Error log has the reason; this is the most likely thing to break over time. |
| No thumbnails | They load from YouTube's image server, so the phone needs internet access. A music-note placeholder is shown instead. |
| Volume bar jumps back | The page ignores server volume for about 1.5 seconds after you change it; if it keeps reverting, someone else is changing it. |

## Songs skip by themselves

The player treats "the speaker stopped" as "the song finished" and moves on. If the speaker can't play a file, that looks the same, so the queue can drain quickly. The Error log now says `speaker stopped '<song>' early (Ns of Ms)` or `file for '<song>' is missing` when this happens. Usual causes: the host firewall blocks the port (see Setup), the PC's IP changed since the speaker was chosen (take over the speaker again), or two servers were sharing one `cache/` folder (each port now has its own `data/<port>/`).

## Lyrics

| Symptom | Cause and fix |
|---|---|
| "No lyrics found for this song" | LRCLIB has no match within 8 seconds of the song's length. Covers, live versions and remixes often have none. |
| "Couldn't reach the lyrics service" | LRCLIB is down or rate-limiting. Press Try again. |
| Highlighted line is slightly early or late | The synced timing comes from LRCLIB for a recording that can differ by a second or two from the YouTube version. |

## Data

- To start clean, stop the server, delete `data/<port>/` (e.g. `data/8000/`), and start again.
- If the server was killed while a song was playing, that song returns at the front of the queue and starts from the beginning.
- Port already in use: `PORT=9000 uv run server.py`.
