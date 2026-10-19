# /// script
# requires-python = ">=3.10"
# dependencies = ["flask", "soco", "yt-dlp"]
# ///
"""Shared queue for a Sonos speaker. Host runs this; guests open http://<host-ip>:8000.

Pipeline: yt-dlp downloads the audio -> we serve the file over HTTP -> Sonos pulls it
via soco.play_uri. The host manages the speaker at http://localhost:8000.
"""
import html, json, os, re, socket, threading, time, urllib.request, uuid
from urllib.parse import urlparse
import soco
from flask import Flask, jsonify, request, send_from_directory
from yt_dlp import YoutubeDL

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", 8000))
CACHE = os.path.join(ROOT, "cache")
os.makedirs(CACHE, exist_ok=True)
for f in os.listdir(CACHE):  # the queue lives in memory, so old downloads are orphans
    os.remove(os.path.join(CACHE, f))

VID = re.compile(r"^[\w-]{11}$")
DUR = re.compile(r"^\d{1,3}(:\d{2}){1,2}$")
ALLOWED_HOSTS = ("youtube.com", "youtu.be", "open.spotify.com")  # yt-dlp would fetch any URL otherwise

speaker = None  # chosen by the host in the web UI (or SONOS_IP at startup)
HOST = None     # our IP as seen by that speaker
lock = threading.Lock()
queue = []      # {id, title, status: loading|ready|error, vid, artist, dur, by, file}
current = None
started_at = 0.0
snap = {"playing": False, "position": None, "duration": None, "volume": 0}  # refreshed by player_loop
app = Flask(__name__, static_folder=os.path.join(ROOT, "static"), static_url_path="/static")


def local_ip(target="192.0.2.1"):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect((target, 1400))  # UDP connect sends nothing; it just picks the outgoing interface
    return s.getsockname()[0]


def is_host():
    return request.remote_addr in ("127.0.0.1", "::1", local_ip())


def use_speaker(ip):
    global speaker, HOST
    sp = soco.SoCo(ip)
    sp.player_name  # fail fast if unreachable
    HOST = local_ip(ip)
    speaker = sp


def spotify_query(url):
    """Spotify can't be streamed (DRM), so read the track's artist/title and find it on YouTube."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    page = urllib.request.urlopen(req, timeout=10).read().decode()
    og = lambda k: html.unescape(re.search(f'property="og:{k}" content="([^"]*)"', page).group(1))
    return f"{og('description').split(' · ')[0]} {og('title')}"  # "Artist · Album · Song · Year"


def fetch(item, query):
    if re.match(r"https://open\.spotify\.com/(intl-\w+/)?track/", query):
        try:
            query = spotify_query(query)
        except Exception as e:
            return item.update(status="error", title=f"spotify lookup failed: {str(e)[:60]}")
        item["title"] = query
    # AAC/m4a: the Sonos-friendly format (webm/opus is not supported)
    opts = {"format": "140/bestaudio[ext=m4a]", "outtmpl": f"{CACHE}/{item['id']}.%(ext)s",
            "noplaylist": True, "quiet": True}
    if not query.startswith(("http://", "https://")):
        query = f"ytsearch1:{query}"
    try:
        with YoutubeDL(opts) as y:
            info = y.extract_info(query, download=True)
            info = info["entries"][0] if "entries" in info else info
            item["title"] = info["title"]
            item["vid"] = info["id"]
            item["artist"] = item["artist"] or info.get("artist") or info.get("channel") or info.get("uploader")
            d = int(info.get("duration") or 0)
            item["dur"] = item["dur"] or (f"{d // 60}:{d % 60:02d}" if d else None)
            item["file"] = os.path.basename(y.prepare_filename(info))
        item["status"] = "ready"
    except Exception as e:
        item.update(status="error", title=f"failed: {str(e)[:80]}")


def drop_file(item):
    if item and item.get("file"):
        try:
            os.remove(os.path.join(CACHE, item["file"]))
        except OSError:
            pass


def player_loop():
    global current, started_at
    while True:
        time.sleep(2)
        sp = speaker
        if not sp:
            continue
        try:
            state = sp.get_current_transport_info()["current_transport_state"]
            snap.update(playing=state == "PLAYING", volume=sp.volume, position=None, duration=None)
            if current:
                t = sp.get_current_track_info()  # real position/duration as "H:MM:SS"
                snap.update(position=t.get("position"), duration=t.get("duration"))
            # STOPPED only happens when a track ends (pause is PAUSED_PLAYBACK)
            if state == "STOPPED" or (current is None and state != "PLAYING"):
                if time.time() - started_at < 10:
                    continue  # grace: play_uri hasn't reached PLAYING yet
                with lock:
                    nxt = next((q for q in queue if q["status"] == "ready"), None)
                    old = current
                    if nxt:
                        queue.remove(nxt)
                    current = nxt
                drop_file(old)
                if nxt:
                    sp.play_uri(f"http://{HOST}:{PORT}/f/{nxt['file']}", title=nxt["title"])
                    started_at = time.time()
        except Exception as e:
            print("player:", e)


def need_speaker():
    return None if speaker else ("no speaker: the host has to pick one first", 409)


# Every guest polls this; it only reads the snapshot, so extra phones add no Sonos traffic.
@app.get("/api/state")
def state():
    sp = speaker
    return jsonify(current=current, queue=queue, host=is_host(),
                   speaker=sp and {"name": sp.player_name, "ip": sp.ip_address}, **snap)


@app.get("/api/speakers")
def speakers():
    if not is_host():
        return "host only", 403
    found = soco.discover(timeout=5) or []  # blocks ~5s; threaded server so fine
    return jsonify([{"name": z.player_name, "ip": z.ip_address} for z in found])


@app.post("/api/speaker")
def pick_speaker():
    if not is_host():
        return "host only", 403
    try:
        use_speaker((request.get_json(silent=True) or {})["ip"])
    except Exception as e:
        return f"can't reach speaker: {e}", 400
    return "", 204


def walk(o):
    if isinstance(o, dict):
        if "musicResponsiveListItemRenderer" in o:
            yield o["musicResponsiveListItemRenderer"]
        for v in o.values():
            yield from walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v)


@app.get("/api/search")
def search():
    q = request.args.get("q", "").strip()[:200]
    if not q:
        return jsonify([])
    # YouTube Music's own search (songs filter); one call gives title, artist, duration.
    # ponytail: private API, may change; this "Songs" params blob is a fixed constant.
    body = {"context": {"client": {"clientName": "WEB_REMIX", "clientVersion": "1.20240401.01.00", "hl": "en"}},
            "query": q, "params": "EgWKAQIIAWoKEAkQBRAKEAMQBA=="}
    req = urllib.request.Request("https://music.youtube.com/youtubei/v1/search?prettyPrint=false",
                                 json.dumps(body).encode(),
                                 {"content-type": "application/json", "origin": "https://music.youtube.com",
                                  "User-Agent": "Mozilla/5.0"})
    try:
        data = json.load(urllib.request.urlopen(req, timeout=10))
    except Exception as e:
        return f"search failed: {e}", 502
    out = []
    for r in walk(data):
        try:
            col = lambda i: [x["text"] for x in r["flexColumns"][i]["musicResponsiveListItemFlexColumnRenderer"]["text"]["runs"]]
            sub = "".join(col(1)).split(" • ")  # Artist • Album • 3:34
            vid = r["playlistItemData"]["videoId"]
            out.append({"title": col(0)[0], "artist": sub[0], "dur": sub[-1], "vid": vid,
                        "url": f"https://music.youtube.com/watch?v={vid}"})
        except (KeyError, IndexError):
            continue  # not a song row
    return jsonify(out[:10])


@app.post("/api/add")
def add():
    j = request.get_json(silent=True) or {}
    q = str(j.get("q") or "").strip()[:300]
    if not q:
        return "empty", 400
    if q.startswith(("http://", "https://")):
        host = (urlparse(q).hostname or "").lower()
        if not any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS):
            return "only YouTube and Spotify track links are supported", 400
    vid = str(j.get("vid") or "")
    vid = vid if VID.match(vid) else None  # client values are untrusted: validate before they reach other guests
    dur = str(j.get("dur") or "")
    item = {"id": uuid.uuid4().hex[:8], "title": q, "status": "loading", "vid": vid,
            "artist": str(j.get("artist") or "")[:100] or None, "dur": dur if DUR.match(dur) else None,
            "by": str(j.get("by") or "")[:30] or None}
    with lock:
        if vid and any(i and i.get("vid") == vid for i in [*queue, current]):
            return "already in the queue", 409
        queue.append(item)
    threading.Thread(target=fetch, args=(item, q), daemon=True).start()
    return jsonify(item)


@app.post("/api/remove/<id>")
def remove(id):
    with lock:
        gone = [q for q in queue if q["id"] == id]
        queue[:] = [q for q in queue if q["id"] != id]
    for g in gone:
        drop_file(g)
    return "", 204


@app.post("/api/move/<id>/<how>")
def move(id, how):
    with lock:
        i = next((n for n, q in enumerate(queue) if q["id"] == id), None)
        if i is None or how not in ("up", "top"):
            return "not found", 404
        queue.insert(0 if how == "top" else max(i - 1, 0), queue.pop(i))
    return "", 204


@app.post("/api/skip")
def skip():
    global started_at
    if err := need_speaker():
        return err
    started_at = 0
    speaker.stop()  # loop sees STOPPED and advances
    return "", 204


@app.post("/api/pause")
def pause():
    if err := need_speaker():
        return err
    if speaker.get_current_transport_info()["current_transport_state"] == "PLAYING":
        speaker.pause()
    else:
        speaker.play()
    return "", 204


@app.post("/api/volume")
def volume():
    if err := need_speaker():
        return err
    try:
        v = max(0, min(100, int((request.get_json(silent=True) or {})["v"])))
    except (KeyError, TypeError, ValueError):
        return "bad volume", 400
    speaker.volume = v
    snap["volume"] = v
    return "", 204


@app.get("/f/<path:name>")
def files(name):
    return send_from_directory(CACHE, name)  # supports Range, which Sonos uses


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


if __name__ == "__main__":
    if os.environ.get("SONOS_IP"):
        use_speaker(os.environ["SONOS_IP"])
    threading.Thread(target=player_loop, daemon=True).start()
    print(f"host:   http://localhost:{PORT}  (pick the speaker here)")
    print(f"guests: http://{local_ip()}:{PORT}")
    app.run(host="0.0.0.0", port=PORT, threaded=True)
