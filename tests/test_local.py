"""LocalPlayer drives a real mpv: play, pause, position, volume, stop.

Run: uv run --with flask --with soco --with yt-dlp python tests/test_local.py
"""
import os, shutil, subprocess, sys, time
os.environ["PORT"] = "8898"  # its own data/8898 folder
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import server

wav = os.path.join(server.CACHE, "t.wav")
subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=20", wav], check=True)
p = server.LocalPlayer("")
assert p.get_current_transport_info()["current_transport_state"] == "STOPPED"
p.play_uri(wav)
time.sleep(1.5)
assert p.get_current_transport_info()["current_transport_state"] == "PLAYING"
assert p.get_current_track_info()["duration"] == "0:00:20", p.get_current_track_info()
p.pause()
assert p.get_current_transport_info()["current_transport_state"] == "PAUSED_PLAYBACK"
server.speaker = p
assert server.app.test_client().post("/api/seek", json={"v": 12}).status_code == 204
assert server.snap["position"] == "0:00:12", server.snap  # guests poll the snapshot, so seek must update it
time.sleep(0.5)
assert server.secs(p.get_current_track_info()["position"]) >= 11, p.get_current_track_info()  # mpv truncates 11.99s
p.play(); p.volume = 30
assert p._cmd("get_property", "volume") == 30
p.stop()
assert p.get_current_transport_info()["current_transport_state"] == "STOPPED"
shutil.rmtree(server.DATA)
print("local player: ok")
