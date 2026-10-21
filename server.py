# /// script
# requires-python = ">=3.10"
# dependencies = ["flask", "soco", "yt-dlp"]
# ///
"""Shared queue for a Sonos speaker. Host runs this; guests open http://<host-ip>:8000.

Pipeline: yt-dlp downloads the audio -> we serve the file over HTTP -> Sonos pulls it
via soco.play_uri. The host manages the speaker at http://localhost:8000.
"""
import html, json, os, random, re, socket, subprocess, threading, time, urllib.request, uuid
from collections import OrderedDict, deque
from urllib.parse import urlencode
from urllib.parse import urlparse
import soco
from flask import Flask, jsonify, request, send_from_directory
from yt_dlp import YoutubeDL

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", 8000))
# One data folder per port: two servers must never share a cache, or one deletes the other's songs
DATA = os.path.join(ROOT, "data", str(PORT))
CACHE = os.path.join(DATA, "cache")
STATE = os.path.join(DATA, "state.json")  # queue, speaker and autoplay survive restarts
os.makedirs(CACHE, exist_ok=True)

VID = re.compile(r"^[\w-]{11}$")
DUR = re.compile(r"^\d{1,3}(:\d{2}){1,2}$")
# m4a only (Sonos can't play webm/opus). YouTube's m4a is 48k (139), 128k (140), 256k (141, Premium only; falls back to 140)
QUALITY = {"low": "139/worstaudio[ext=m4a]", "standard": "140/bestaudio[ext=m4a]", "high": "141/140/bestaudio[ext=m4a]"}
quality = "standard"  # applies to songs added from now on
LINK_NO_SCHEME = re.compile(r"^(www\.|m\.|music\.)?(youtube\.com|youtu\.be|open\.spotify\.com)/", re.I)
ALLOWED_HOSTS = ("youtube.com", "youtu.be", "open.spotify.com")  # yt-dlp would fetch any URL otherwise

speaker = None  # chosen by the host in the web UI (or SONOS_IP at startup)
HOST = None     # our IP as seen by that speaker
lock = threading.RLock()
queue = []      # {id, title, status: loading|ready|error, vid, artist, dur, by, file}
current = None
started_at = 0.0
skipped = False          # a guest pressed Skip: the next STOPPED is expected, not a failure
progress = (0, 0)        # (position, duration) in seconds from the last poll that saw PLAYING
autoplay = True          # when the queue runs dry, queue a random song like the last one
auto_pending = False
last_vid = None          # last song that started playing: the seed for autoplay
recent = deque(maxlen=40)  # recently played video ids, so autoplay doesn't repeat itself
errors = deque(maxlen=50)  # newest first; shown in the log panel at the bottom of the page
snap = {"playing": False, "position": None, "duration": None, "volume": 0}  # refreshed by player_loop
app = Flask(__name__, static_folder=os.path.join(ROOT, "static"), static_url_path="/static")


def log_err(msg):
    print("error:", msg)
    errors.appendleft({"t": time.time(), "msg": str(msg)[:200]})


def save():
    """Write the queue to disk after every change (atomic: temp file + rename)."""
    try:
        with lock:
            data = {"queue": [i for i in queue if i["status"] in ("ready", "loading")], "current": current,
                    "autoplay": autoplay, "speaker_ip": speaker.ip_address if speaker else None,
                    "quality": quality, "last_vid": last_vid, "recent": list(recent)}
            with open(STATE + ".tmp", "w") as f:
                json.dump(data, f)
            os.replace(STATE + ".tmp", STATE)
    except Exception as e:
        log_err(f"could not save state.json: {e}")


def restore():
    """Reload the saved queue and re-attach to the song the speaker is still playing.

    The speaker keeps playing after the server restarts, so if it is still on the saved current song
    that song stays "now playing". Otherwise it goes back to the front of the queue.
    Unfinished downloads restart.
    """
    global autoplay, last_vid, current, quality
    try:
        with open(STATE) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return
    autoplay, last_vid = bool(d.get("autoplay", True)), d.get("last_vid")
    quality = d.get("quality") if d.get("quality") in QUALITY else quality
    recent.extend(d.get("recent") or [])
    on_disk = lambda it: it.get("file") and os.path.exists(os.path.join(CACHE, it["file"]))
    for it in d.get("queue", []):
        if not isinstance(it, dict):
            continue
        if it.get("status") == "ready" and on_disk(it):
            queue.append(it)
        elif it.get("status") == "loading" and it.get("q"):
            queue.append(it)
            threading.Thread(target=fetch, args=(it, it["q"]), daemon=True).start()
    if d.get("speaker_ip"):
        try:
            use_speaker(d["speaker_ip"])
        except Exception as e:
            log_err(f"saved speaker {d['speaker_ip']} unreachable, pick one in the UI: {e}")
    cur = d.get("current")
    if isinstance(cur, dict) and cur.get("status") == "ready" and on_disk(cur):
        if speaker_still_on(cur):
            current = cur
        else:
            queue.insert(0, cur)  # the speaker stopped meanwhile: play it again
    keep = {i["file"] for i in [*queue, current] if i and i.get("file")}
    for f in os.listdir(CACHE):  # downloads nothing refers to any more
        if f not in keep:
            os.remove(os.path.join(CACHE, f))
    save()


def speaker_still_on(item):
    """True if the speaker is playing or paused on this song's file."""
    try:
        state = speaker.get_current_transport_info()["current_transport_state"]
        uri = speaker.get_current_track_info().get("uri") or ""
        return state in ("PLAYING", "PAUSED_PLAYBACK", "TRANSITIONING") and item["file"] in uri
    except Exception:
        return False  # no speaker, or unreachable


class LocalPlayer:
    """The host computer's audio output (default, or a paired Bluetooth sink) through mpv.

    Mimics the few SoCo calls this file uses, so the rest of the server doesn't care which one it has.
    """
    def __init__(self, sink="", name="This computer"):
        self.sink, self.player_name, self.ip_address = sink, name, "local:" + sink
        self.sock = os.path.join(DATA, "mpv.sock")
        self.proc, self.uri, self._vol = None, "", 100

    def _alive(self):
        return self.proc is not None and self.proc.poll() is None

    def _cmd(self, *args):
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(2)
            s.connect(self.sock)
            s.sendall(json.dumps({"command": list(args)}).encode() + b"\n")
            for line in s.makefile():  # skip async events until our reply
                r = json.loads(line)
                if "error" in r:
                    if r["error"] != "success":
                        raise RuntimeError(f"mpv: {r['error']}")
                    return r.get("data")

    def play_uri(self, uri, title=""):
        self.stop()
        cmd = ["mpv", "--no-video", "--no-terminal", f"--input-ipc-server={self.sock}", f"--volume={self._vol}"]
        if self.sink:
            cmd.append(f"--audio-device=pulse/{self.sink}")
        self.proc, self.uri = subprocess.Popen(cmd + [uri]), uri
        for _ in range(50):  # wait for the control socket
            if os.path.exists(self.sock):
                break
            time.sleep(0.1)

    def stop(self):
        if self._alive():
            self.proc.terminate()
            self.proc.wait()
        self.proc = None

    def get_current_transport_info(self):
        if not self._alive():
            return {"current_transport_state": "STOPPED"}
        paused = self._cmd("get_property", "pause")
        return {"current_transport_state": "PAUSED_PLAYBACK" if paused else "PLAYING"}

    def get_current_track_info(self):
        hms = lambda n: f"{int(n) // 3600}:{int(n) // 60 % 60:02d}:{int(n) % 60:02d}"
        try:
            return {"uri": self.uri, "position": hms(self._cmd("get_property", "time-pos") or 0),
                    "duration": hms(self._cmd("get_property", "duration") or 0)}
        except (OSError, RuntimeError):
            return {"uri": self.uri, "position": "0:00:00", "duration": "0:00:00"}  # still loading

    def pause(self):
        self._cmd("set_property", "pause", True)

    def play(self):
        if self._alive():
            self._cmd("set_property", "pause", False)

    def seek(self, target):  # SoCo-style "H:MM:SS"
        self._cmd("seek", secs(target), "absolute")

    @property
    def volume(self):
        return self._vol

    @volume.setter
    def volume(self, v):
        self._vol = v
        if self._alive():
            self._cmd("set_property", "volume", v)


def sinks():
    out = subprocess.run(["pactl", "-f", "json", "list", "sinks"], capture_output=True, text=True, timeout=5)
    return [{"name": s["name"], "desc": s["description"]} for s in json.loads(out.stdout)]


def local_ip(target="192.0.2.1"):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect((target, 1400))  # UDP connect sends nothing; it just picks the outgoing interface
    return s.getsockname()[0]


def is_host():
    return request.remote_addr in ("127.0.0.1", "::1", local_ip())


def use_speaker(ip):
    global speaker, HOST
    if ip.startswith("local:"):  # "local:<pulse sink name>", empty sink = system default
        sink = ip[6:]
        desc = next((s["desc"] for s in sinks() if s["name"] == sink), None) if sink else "This computer"
        if desc is None:
            raise ValueError(f"no audio output named {sink}")
        sp, host = LocalPlayer(sink, desc), "127.0.0.1"
    else:
        sp = soco.SoCo(ip)
        sp.player_name  # fail fast if unreachable
        host = local_ip(ip)
    if isinstance(speaker, LocalPlayer) and speaker is not sp:
        speaker.stop()
    HOST, speaker = host, sp
    save()


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
            log_err(f"spotify lookup failed for {item['title'][:60]}: {e}")
            return item.update(status="error", error="spotify lookup failed")
        item["title"] = query
    opts = {"format": QUALITY[quality], "outtmpl": f"{CACHE}/{item['id']}.%(ext)s",
            "noplaylist": True, "quiet": True,
            "match_filter": lambda info, incomplete: "live" if info.get("is_live") else None}  # a live stream never finishes downloading
    if not query.startswith(("http://", "https://")):
        query = f"ytsearch1:{query}"
    try:
        with YoutubeDL(opts) as y:
            info = y.extract_info(query, download=True)
            info = info and (info["entries"][0] if "entries" in info else info)
            if info is None or info.get("is_live"):
                raise ValueError("live streams aren't supported")
            item["title"] = info["title"]
            item["vid"] = info["id"]
            item["artist"] = item["artist"] or info.get("artist") or info.get("channel") or info.get("uploader")
            d = info.get("duration")
            item["dur"] = item["dur"] or mmss(d)
            item["file"] = os.path.basename(y.prepare_filename(info))
            if not os.path.exists(os.path.join(CACHE, item["file"])):
                raise ValueError("download produced no audio file")
        item["status"] = "ready"
        save()
    except Exception as e:
        reason = re.sub(r"^ERROR:\s*(\[\w+\]\s*\S+:\s*)?", "", str(e))
        item.update(status="error", error=reason[:100])  # keep the title, show a short reason
        log_err(f"download failed: {item['title'][:60]}: {reason}")
        save()


def mmss(seconds):
    d = int(seconds or 0)
    return f"{d // 60}:{d % 60:02d}" if d else None


def autofill():
    """Queue a random song from the YouTube Music radio of the last played song."""
    global auto_pending
    try:
        seed = last_vid
        with YoutubeDL({"extract_flat": True, "quiet": True, "playlistend": 25}) as y:
            info = y.extract_info(f"https://music.youtube.com/watch?v={seed}&list=RD{seed}", download=False)
        picks = [e for e in info["entries"] if e.get("id") and e["id"] not in recent]
        if not picks:
            return
        e = random.choice(picks)
        item = {"id": uuid.uuid4().hex[:8], "title": e.get("title") or e["id"], "status": "loading", "vid": e["id"],
                "artist": e.get("uploader") or e.get("channel"), "dur": mmss(e.get("duration")), "by": "Autoplay"}
        item["q"] = f"https://music.youtube.com/watch?v={e['id']}"
        with lock:
            queue.append(item)
            save()
        fetch(item, item["q"])
    except Exception as ex:
        log_err(f"autoplay: {ex}")
    finally:
        auto_pending = False


def drop_file(item):
    if item and item.get("file"):
        try:
            os.remove(os.path.join(CACHE, item["file"]))
        except OSError:
            pass


def player_loop():
    global current, started_at, last_vid, auto_pending, skipped, progress
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
            if state == "PLAYING" and current:
                progress = (secs(snap["position"]), secs(snap["duration"]))
            # STOPPED only happens when a track ends (pause is PAUSED_PLAYBACK)
            if state == "STOPPED" or (current is None and state != "PLAYING"):
                if time.time() - started_at < 10:
                    continue  # grace: play_uri hasn't reached PLAYING yet
                pos, dur = progress
                if current and not skipped and not (dur and pos >= dur - 8):
                    # stopped long before the end: the speaker couldn't play it, don't mistake that for "finished"
                    log_err(f"speaker stopped '{current['title'][:50]}' early ({pos}s of {dur}s). Check the host firewall and that the file is reachable.")
                skipped, progress = False, (0, 0)
                with lock:
                    nxt = next((q for q in queue if q["status"] == "ready"), None)
                    while nxt and not os.path.exists(os.path.join(CACHE, nxt["file"])):
                        log_err(f"file for '{nxt['title'][:50]}' is missing, skipping it")
                        queue.remove(nxt)
                        nxt = next((q for q in queue if q["status"] == "ready"), None)
                    old = current
                    if nxt:
                        queue.remove(nxt)
                    current = nxt
                    save()
                drop_file(old)
                if not nxt and autoplay and last_vid and not auto_pending and not any(q["status"] == "loading" for q in queue):
                    auto_pending = True
                    threading.Thread(target=autofill, daemon=True).start()
                if nxt:
                    last_vid = nxt.get("vid") or last_vid
                    recent.append(nxt.get("vid"))
                    sp.play_uri(f"http://{HOST}:{PORT}/f/{nxt['file']}", title=nxt["title"])
                    started_at = time.time()
        except Exception as e:
            log_err(f"speaker: {e}")


def secs(t):
    return sum(int(x) * 60 ** i for i, x in enumerate(reversed((t or "0").split(":")))) if t and t[0].isdigit() else 0


# ---- lyrics (LRCLIB: free, no key, time-synced) ----
LRC = re.compile(r"^\[(\d+):(\d+(?:\.\d+)?)\]\s*(.*)$")
lyrics_cache = OrderedDict()  # item id -> result, newest last


def clean_title(t):
    inner = re.search(r"[「『](.+?)[」』]", t)  # "YOASOBI「アイドル」 Official Video" -> アイドル
    if inner:
        return inner.group(1).strip()
    t = re.sub(r"[\(\[（【].*?[\)\]）】]", "", t)  # (Official Video), [Remastered], ...
    return re.sub(r"\s+-\s+(Topic|Official.*)$", "", t, flags=re.I).strip() or t


def lrclib_search(**params):
    req = urllib.request.Request("https://lrclib.net/api/search?" + urlencode(params),
                                 headers={"User-Agent": "sonos-local-player (https://github.com/randspace0/sonos-local-player)"})
    for attempt in (1, 2):  # LRCLIB sometimes answers 503; one retry is enough
        try:
            return json.load(urllib.request.urlopen(req, timeout=8))
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1)


def parse_synced(lrc):
    out = []
    for line in (lrc or "").splitlines():
        m = LRC.match(line.strip())
        if m:
            out.append({"t": int(m.group(1)) * 60 + float(m.group(2)), "text": m.group(3)})
    return out


def find_lyrics(item):
    artist = re.split(r",| and | & | x |、", item.get("artist") or "")[0].strip()
    title, dur = clean_title(item["title"]), secs(item.get("dur"))
    best = None  # (score, hit); lower is better
    queries = [{"artist_name": artist, "track_name": title}] if artist else []
    queries += [{"q": f"{artist} {title}".strip()}, {"q": title}]
    for q in queries:
        hits = lrclib_search(**q)  # raises if LRCLIB is unreachable: that is not the same as "no lyrics"
        for h in hits:
            synced = parse_synced(h.get("syncedLyrics"))
            plain = h.get("plainLyrics") or ""
            if h.get("instrumental"):
                continue
            if len(synced) < 4 and len(plain.splitlines()) < 4:
                continue  # LRCLIB has junk entries ("probe"); a real song has more lines than this
            gap = abs((h.get("duration") or 0) - dur) if dur else 0
            if dur and gap > 8:
                continue  # a different recording or a different song
            score = gap + (0 if len(synced) >= 4 else 4)  # prefer a synced match over a slightly closer plain one
            if best is None or score < best[0]:
                best = (score, {"synced": synced if len(synced) >= 4 else None, "plain": plain or None})
        if best and best[0] <= 2:
            break  # good enough, don't query further
    return best[1] if best else None


@app.get("/api/lyrics")
def lyrics():
    id = request.args.get("id", "")
    item = next((i for i in [current, *queue] if i and i["id"] == id), None)
    if not item:
        return "no such song", 404
    if id not in lyrics_cache:
        try:
            found = find_lyrics(item)
        except Exception as e:
            log_err(f"lyrics lookup failed for '{item['title'][:40]}': {e}")
            return "couldn't reach the lyrics service", 502  # not cached, so a retry can succeed
        lyrics_cache[id] = found or {"synced": None, "plain": None}
        while len(lyrics_cache) > 50:
            lyrics_cache.popitem(last=False)
    return jsonify(id=id, **lyrics_cache[id])


def need_speaker():
    return None if speaker else ("no speaker: the host has to pick one first", 409)


# Every guest polls this; it only reads the snapshot, so extra phones add no Sonos traffic.
@app.get("/api/state")
def state():
    sp = speaker
    return jsonify(current=current, queue=queue, host=is_host(), autoplay=autoplay, quality=quality, errors=list(errors)[:30],
                   speaker=sp and {"name": sp.player_name, "ip": sp.ip_address}, **snap)


@app.get("/api/speakers")
def speakers():
    if not is_host():
        return "host only", 403
    found = soco.discover(timeout=5) or []  # blocks ~5s; threaded server so fine
    return jsonify([{"name": z.player_name, "ip": z.ip_address} for z in found])


@app.get("/api/sinks")
def audio_sinks():
    if not is_host():
        return "host only", 403
    try:
        return jsonify(sinks())
    except Exception as e:
        return f"can't list audio outputs: {e}", 500


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
    # YouTube Music's own search; one call per filter gives title, artist, duration.
    # ponytail: private API, may change; these params blobs are fixed constants. Songs first, then plain videos.
    out, seen = [], set()  # up to 8 songs + 8 videos
    for params in ("EgWKAQIIAWoKEAkQBRAKEAMQBA==", "EgWKAQIQAWoKEAkQBRAKEAMQBA=="):  # Songs, Videos
        body = {"context": {"client": {"clientName": "WEB_REMIX", "clientVersion": "1.20240401.01.00", "hl": "en"}},
                "query": q, "params": params}
        req = urllib.request.Request("https://music.youtube.com/youtubei/v1/search?prettyPrint=false",
                                     json.dumps(body).encode(),
                                     {"content-type": "application/json", "origin": "https://music.youtube.com",
                                      "User-Agent": "Mozilla/5.0"})
        try:
            data = json.load(urllib.request.urlopen(req, timeout=10))
        except Exception as e:
            log_err(f"search failed for '{q[:40]}': {e}")
            return f"search failed: {e}", 502
        part = []
        for r in walk(data):
            try:
                col = lambda i: [x["text"] for x in r["flexColumns"][i]["musicResponsiveListItemFlexColumnRenderer"]["text"]["runs"]]
                sub = "".join(col(1)).split(" • ")  # Artist • Album • 3:34  (videos: Channel • 1M views • 3:34)
                vid = r["playlistItemData"]["videoId"]
                if vid in seen:
                    continue
                seen.add(vid)
                part.append({"title": col(0)[0], "artist": sub[0], "dur": sub[-1], "vid": vid,
                            "url": f"https://music.youtube.com/watch?v={vid}"})
            except (KeyError, IndexError):
                continue  # not a song/video row
        out += part[:8]
    return jsonify(out)


@app.post("/api/add")
def add():
    j = request.get_json(silent=True) or {}
    q = str(j.get("q") or "").strip()[:300]
    if not q:
        return "empty", 400
    if LINK_NO_SCHEME.match(q):
        q = "https://" + q  # pasted without the scheme, e.g. "youtu.be/ID" or "www.youtube.com/watch?v=ID"
    if q.startswith(("http://", "https://")):
        host = (urlparse(q).hostname or "").lower()
        if not any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS):
            return "only YouTube and Spotify track links are supported", 400
    vid = str(j.get("vid") or "")
    vid = vid if VID.match(vid) else None  # client values are untrusted: validate before they reach other guests
    dur = str(j.get("dur") or "")
    item = {"id": uuid.uuid4().hex[:8], "title": q, "status": "loading", "vid": vid,
            "artist": str(j.get("artist") or "")[:100] or None, "dur": dur if DUR.match(dur) else None,
            "by": str(j.get("by") or "")[:30] or None, "q": q}
    with lock:
        if vid and any(i and i.get("vid") == vid for i in [*queue, current]):
            return "already in the queue", 409
        queue.append(item)
        save()
    threading.Thread(target=fetch, args=(item, q), daemon=True).start()
    return jsonify(item)


@app.post("/api/autoplay")
def set_autoplay():
    global autoplay
    autoplay = bool((request.get_json(silent=True) or {}).get("on"))
    save()
    return "", 204


@app.post("/api/log/clear")
def clear_log():
    errors.clear()
    return "", 204


@app.post("/api/remove/<id>")
def remove(id):
    with lock:
        gone = [q for q in queue if q["id"] == id]
        queue[:] = [q for q in queue if q["id"] != id]
        save()
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
        save()
    return "", 204


@app.post("/api/reorder")
def reorder():
    j = request.get_json(silent=True) or {}
    try:
        id, to = str(j["id"]), int(j["to"])
    except (KeyError, TypeError, ValueError):
        return "bad request", 400
    with lock:
        i = next((n for n, q in enumerate(queue) if q["id"] == id), None)
        if i is None:
            return "that song already started or was removed", 404
        queue.insert(max(0, min(to, len(queue) - 1)), queue.pop(i))  # `to` = final position
        save()
    return "", 204


@app.post("/api/skip")
def skip():
    global started_at
    if err := need_speaker():
        return err
    global skipped
    started_at, skipped = 0, True
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


@app.post("/api/seek")
def seek():
    if err := need_speaker():
        return err
    try:
        v = max(0, int((request.get_json(silent=True) or {})["v"]))
    except (KeyError, TypeError, ValueError):
        return "bad seek", 400
    speaker.seek(f"{v // 3600}:{v // 60 % 60:02d}:{v % 60:02d}")
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


@app.post("/api/quality")
def set_quality():
    global quality
    q = (request.get_json(silent=True) or {}).get("q")
    if q not in QUALITY:
        return "bad quality", 400
    quality = q
    save()
    return "", 204


@app.get("/f/<path:name>")
def files(name):
    return send_from_directory(CACHE, name)  # supports Range, which Sonos uses


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


if __name__ == "__main__":
    restore()
    if os.environ.get("SONOS_IP"):
        use_speaker(os.environ["SONOS_IP"])
    threading.Thread(target=player_loop, daemon=True).start()
    print(f"host:   http://localhost:{PORT}  (pick the speaker here)")
    print(f"guests: http://{local_ip()}:{PORT}")
    app.run(host="0.0.0.0", port=PORT, threaded=True)
