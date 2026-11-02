"""Regular YouTube search and pasted-video previews, without network or downloads.

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


class SearchTests(unittest.TestCase):
    def search(self, videos, failure=None):
        with patch.object(server, "YoutubeDL") as downloader:
            extract = downloader.return_value.__enter__.return_value.extract_info
            extract.return_value = {"entries": videos}
            extract.side_effect = failure
            return server.app.test_client().get("/api/search?q=space+lecture")

    def test_regular_video_metadata_and_deduplication(self):
        response = self.search([
            None,
            {"id": "playlist", "title": "Not a video"},
            {"id": "live1234567", "title": "Live", "live_status": "is_live"},
            {"id": "video123456", "title": "Space lecture", "channel": "Science", "duration": 125},
            {"id": "video123456", "title": "Duplicate lecture"},
            {"id": "unknown1234", "title": "Untimed video", "uploader": "Creator"},
        ])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), [
            {"title": "Space lecture", "artist": "Science", "dur": "2:05", "vid": "video123456",
             "url": "https://www.youtube.com/watch?v=video123456"},
            {"title": "Untimed video", "artist": "Creator", "dur": "", "vid": "unknown1234",
             "url": "https://www.youtube.com/watch?v=unknown1234"},
        ])

    def test_empty_search_results(self):
        self.assertEqual(self.search([]).get_json(), [])

    def test_video_search_failure_is_reported(self):
        response = self.search([], RuntimeError("video service unavailable"))
        self.assertEqual((response.status_code, response.get_data(as_text=True)),
                         (502, "search failed: video service unavailable"))

    def test_pasted_video_links_return_one_preview(self):
        for link in [
            "https://www.youtube.com/watch?v=video123456&list=ignored",
            "https://youtu.be/video123456?t=42",
            "youtube.com/watch?v=video123456",
            "https://music.youtube.com/watch?v=video123456",
            "https://m.youtube.com/shorts/video123456",
            "https://www.youtube.com/embed/video123456",
            "https://www.youtube.com/live/video123456",
        ]:
            with self.subTest(link=link), patch.object(server.urllib.request, "urlopen", return_value=io.BytesIO(
                    json.dumps({"title": "Space lecture", "author_name": "Science"}).encode())):
                response = server.app.test_client().get("/api/search", query_string={"q": link})
                self.assertEqual(response.get_json(), [{
                    "title": "Space lecture", "artist": "Science", "dur": "", "vid": "video123456",
                    "url": "https://www.youtube.com/watch?v=video123456",
                }])

    def test_invalid_video_links_are_rejected_before_network_access(self):
        for link in ["https://example.com/watch?v=video123456", "https://www.youtube.com/playlist?list=abc",
                     "https://youtube.com.evil.test/watch?v=video123456", "https://youtu.be/invalid"]:
            with self.subTest(link=link), patch.object(server.urllib.request, "urlopen") as network:
                response = server.app.test_client().get("/api/search", query_string={"q": link})
                self.assertEqual((response.status_code, response.get_data(as_text=True)),
                                 (400, "enter a YouTube video URL"))
                network.assert_not_called()

    def test_unavailable_preview_reports_an_error(self):
        with patch.object(server.urllib.request, "urlopen", side_effect=OSError("video unavailable")):
            response = server.app.test_client().get("/api/search", query_string={
                "q": "https://youtu.be/video123456"})
        self.assertEqual((response.status_code, response.get_data(as_text=True)),
                         (502, "couldn't load this YouTube video"))


if __name__ == "__main__":
    unittest.main()
