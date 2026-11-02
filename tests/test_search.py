"""Mixed music/video search without network or downloads.

Run: uv run --with flask --with soco --with yt-dlp python tests/test_search.py
"""
import io
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
with patch("os.makedirs"):
    import server


def music_row(vid, title):
    def column(text):
        return {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": text}]}}}
    return {"musicResponsiveListItemRenderer": {
        "flexColumns": [column(title), column("Artist • Album • 3:34")],
        "playlistItemData": {"videoId": vid},
    }}


class SearchTests(unittest.TestCase):
    def search(self, music, videos, failure=None):
        with patch.object(server.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(music).encode())), \
                patch.object(server, "YoutubeDL") as downloader:
            extract = downloader.return_value.__enter__.return_value.extract_info
            extract.return_value = {"entries": videos}
            extract.side_effect = failure
            return server.app.test_client().get("/api/search?q=space+lecture")

    def test_regular_video_metadata_and_music_deduplication(self):
        response = self.search([music_row("abcdefghijk", "Song")], [
            {"id": "abcdefghijk", "title": "Same song"},
            None,
            {"id": "playlist", "title": "Not a video"},
            {"id": "live1234567", "title": "Live", "live_status": "is_live"},
            {"id": "video123456", "title": "Space lecture", "channel": "Science", "duration": 125},
            {"id": "video123456", "title": "Duplicate lecture"},
            {"id": "unknown1234", "title": "Untimed video", "uploader": "Creator"},
        ])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), [
            {"title": "Song", "artist": "Artist", "dur": "3:34", "vid": "abcdefghijk",
             "url": "https://music.youtube.com/watch?v=abcdefghijk"},
            {"title": "Space lecture", "artist": "Science", "dur": "2:05", "vid": "video123456",
             "url": "https://www.youtube.com/watch?v=video123456"},
            {"title": "Untimed video", "artist": "Creator", "dur": "", "vid": "unknown1234",
             "url": "https://www.youtube.com/watch?v=unknown1234"},
        ])

    def test_empty_search_results(self):
        self.assertEqual(self.search([], []).get_json(), [])

    def test_video_search_failure_is_reported(self):
        response = self.search([], [], RuntimeError("video service unavailable"))
        self.assertEqual((response.status_code, response.get_data(as_text=True)),
                         (502, "search failed: video service unavailable"))


if __name__ == "__main__":
    unittest.main()
