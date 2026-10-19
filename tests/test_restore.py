"""Restart behaviour: the song the speaker is still playing must stay "now playing".

Run: uv run --with flask --with soco --with yt-dlp python tests/test_restore.py
"""
import json, os, shutil, sys
os.environ["PORT"] = "8899"  # its own data/8899 folder, so a real server's files are never touched
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import server

class Fake:
    ip_address, player_name = "10.0.0.5", "Fake"
    def __init__(self, state, uri): self.state, self.uri = state, uri
    def get_current_transport_info(self): return {"current_transport_state": self.state}
    def get_current_track_info(self): return {"uri": self.uri}

def run(fake):
    server.queue.clear(); server.current = None; server.speaker = None
    for f in os.listdir(server.CACHE): os.remove(os.path.join(server.CACHE, f))
    for n in ("a.m4a", "b.m4a"): open(os.path.join(server.CACHE, n), "w").write("x")
    cur = {"id": "cur", "title": "Playing", "status": "ready", "file": "a.m4a", "vid": "v1"}
    nxt = {"id": "nxt", "title": "Next", "status": "ready", "file": "b.m4a", "vid": "v2"}
    json.dump({"queue": [nxt], "current": cur, "autoplay": True, "speaker_ip": "10.0.0.5"}, open(server.STATE, "w"))
    server.use_speaker = lambda ip: setattr(server, "speaker", fake)
    server.restore()
    return (server.current or {}).get("id"), [q["id"] for q in server.queue]

# speaker still playing our file -> stays "now playing", not duplicated in the queue
assert run(Fake("PLAYING", "http://10.0.0.9:8000/f/a.m4a")) == ("cur", ["nxt"])
# paused on it -> still current
assert run(Fake("PAUSED_PLAYBACK", "http://10.0.0.9:8000/f/a.m4a")) == ("cur", ["nxt"])
# speaker stopped meanwhile -> back at the front of the queue
assert run(Fake("STOPPED", "http://10.0.0.9:8000/f/a.m4a")) == (None, ["cur", "nxt"])
# speaker is playing something else (Sonos app) -> don't claim it
assert run(Fake("PLAYING", "x-sonosapi-stream:other")) == (None, ["cur", "nxt"])
# speaker unreachable -> front of the queue
class Down(Fake):
    def get_current_transport_info(self): raise OSError("down")
assert run(Down("", "")) == (None, ["cur", "nxt"])
shutil.rmtree(server.DATA)
print("restore: all 5 cases ok")
