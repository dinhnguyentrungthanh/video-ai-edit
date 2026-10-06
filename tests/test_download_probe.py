import unittest

from biliflow.download_probe import (
    GENERIC_MIN_DURATION_SECONDS,
    choose,
    classify_error,
    entries_from_info,
    format_duration,
    is_generic,
)


def video(title="Clip", duration=600.0, *, video_id="v1", live_status="not_live", drm=False,
          formats=None, playlist_index=None, **extra):
    info = {"id": video_id, "title": title, "duration": duration, "live_status": live_status,
            "requested_formats": formats if formats is not None else [
                {"format_id": "137", "vcodec": "avc1.640028", "acodec": "none", "height": 1080,
                 "filesize": 90_000_000, "has_drm": drm},
                {"format_id": "140", "vcodec": "none", "acodec": "mp4a.40.2", "filesize": 10_000_000,
                 "has_drm": drm},
            ], **extra}
    if playlist_index is not None:
        info["playlist_index"] = playlist_index
    return info


def page(*entries):
    return {"_type": "playlist", "id": "page", "title": "Page",
            "entries": [dict(entry, playlist_index=index) for index, entry in enumerate(entries, start=1)]}


class EntryTests(unittest.TestCase):
    def test_single_video_estimate_sums_the_requested_formats(self):
        entry, = entries_from_info(video())
        self.assertEqual(entry.index, 0)
        self.assertEqual(entry.estimated_bytes, 100_000_000)
        self.assertEqual(entry.expected_files, 2)
        self.assertEqual((entry.video_codec, entry.audio_codec, entry.height), ("avc1.640028", "mp4a.40.2", 1080))

    def test_estimate_falls_back_to_approx_then_bitrate(self):
        approx, = entries_from_info(video(formats=[], filesize_approx=5_000))
        self.assertEqual(approx.estimated_bytes, 5_000)
        self.assertEqual(approx.expected_files, 1)
        bitrate, = entries_from_info(video(duration=100.0, formats=[{"tbr": 800.0}]))
        self.assertEqual(bitrate.estimated_bytes, 10_000_000)
        unknown, = entries_from_info(video(duration=None, formats=[{}]))
        self.assertIsNone(unknown.estimated_bytes)

    def test_playlist_entries_keep_their_playlist_index(self):
        entries = entries_from_info(page(video("Ad", 30, video_id="a"), video("Film", 5400, video_id="f")))
        self.assertEqual([(entry.index, entry.title) for entry in entries], [(1, "Ad"), (2, "Film")])

    def test_missing_entries_are_skipped(self):
        info = page(video("Film", 5400))
        info["entries"].append(None)
        self.assertEqual(len(entries_from_info(info)), 1)


def site(info):
    """A page read by a site's own yt-dlp reader."""
    return dict(info, extractor_key="Youtube")


def generic(info):
    """A page read by yt-dlp's generic reader (no reader for this site)."""
    return dict(info, extractor_key="Generic")


class ChooseTests(unittest.TestCase):
    def test_a_single_video_is_ready_at_any_length(self):
        for duration in (600.0, 12.0, None):
            with self.subTest(duration=duration):
                result = choose(site(video(duration=duration)))
                self.assertEqual((result.kind, result.entry.index), ("READY", 0))

    def test_the_reader_rule_follows_the_extractor_name(self):
        self.assertTrue(is_generic({"extractor_key": "Generic"}))
        self.assertTrue(is_generic({"extractor": "generic"}))
        self.assertFalse(is_generic({"extractor_key": "Youtube", "extractor": "youtube"}))
        self.assertFalse(is_generic({}))

    def test_live_upcoming_and_post_live_are_refused(self):
        for status, code in (("is_live", "LIVE"), ("is_upcoming", "UPCOMING"), ("post_live", "POST_LIVE")):
            with self.subTest(status=status):
                result = choose(site(video(live_status=status)))
                self.assertEqual((result.kind, result.code), ("FAILED", code))
        self.assertEqual(choose(site(video(live_status=None, is_live=True))).code, "LIVE")

    def test_drm_formats_are_refused(self):
        result = choose(site(video(drm=True)))
        self.assertEqual((result.kind, result.code), ("FAILED", "DRM"))

    def test_a_site_reader_refuses_a_link_with_several_videos(self):
        result = choose(site(page(video("A", 600), video("B", 700))))
        self.assertEqual((result.kind, result.code), ("FAILED", "MULTIPLE_ENTRIES"))
        self.assertIn("danh sách phát", result.message)

    def test_ads_are_dropped_and_the_film_is_chosen(self):
        info = page(video("Ad 1", 30, video_id="a1"), video("Ad 2", 15, video_id="a2"),
                    video("Ad 3", 65, video_id="a3"), video("Film", 5400, video_id="f"))
        result = choose(generic(info))
        self.assertEqual(result.kind, "READY")
        self.assertEqual((result.entry.index, result.entry.title), (4, "Film"))

    def test_the_longest_wins_only_when_twice_the_next(self):
        clear = choose(generic(page(video("Trailer", 700), video("Film", 5400))))
        self.assertEqual((clear.kind, clear.entry.title), ("READY", "Film"))
        close = choose(generic(page(video("Part 1", 2700), video("Part 2", 3000))))
        self.assertEqual(close.kind, "NEEDS_CHOICE")
        self.assertEqual([entry.title for entry in close.entries], ["Part 1", "Part 2"])

    def test_unknown_durations_need_a_choice(self):
        result = choose(generic(page(video("A", None), video("B", 5400))))
        self.assertEqual(result.kind, "NEEDS_CHOICE")

    def test_a_generic_page_with_short_videos_only_fails_with_their_lengths(self):
        result = choose(generic(page(video("Ad 1", 30), video("Ad 2", 15), video("Ad 3", 65))))
        self.assertEqual((result.kind, result.code), ("FAILED", "ONLY_SHORT_ENTRIES"))
        self.assertIn("3 video ngắn", result.message)
        self.assertIn("0:30", result.message)
        self.assertIn("1:05", result.message)
        self.assertIn("10 phút", result.message)
        single = choose(generic(video("Ad", 45)))
        self.assertEqual(single.code, "ONLY_SHORT_ENTRIES")
        self.assertIn("1 video ngắn (0:45)", single.message)
        self.assertEqual(choose(generic(video("Direct file", None))).kind, "READY")
        self.assertEqual(GENERIC_MIN_DURATION_SECONDS, 600)

    def test_drm_or_live_entries_are_not_candidates(self):
        result = choose(generic(page(video("Ad", 30), video("Film", 5400, drm=True))))
        self.assertEqual((result.kind, result.code), ("FAILED", "ONLY_SHORT_ENTRIES"))
        self.assertEqual(choose(generic(page(video("Film", 5400, drm=True)))).code, "DRM")

    def test_a_page_without_videos_is_not_supported_yet(self):
        result = choose(generic({"_type": "playlist", "entries": []}))
        self.assertEqual(result.code, "NO_ENTRIES")
        self.assertIn("Trang này chưa được hỗ trợ", result.message)

    def test_an_empty_playlist_on_a_site_reader_is_not_called_unsupported(self):
        result = choose(site({"_type": "playlist", "entries": []}))
        self.assertEqual(result.code, "NO_VIDEOS")
        self.assertNotIn("chưa được hỗ trợ", result.message)

    def test_titles_from_the_page_are_bounded(self):
        entry, = entries_from_info(video("x" * 5000))
        self.assertEqual(len(entry.title), 300)


class ErrorTests(unittest.TestCase):
    def test_known_messages_get_their_codes(self):
        cases = {
            "ERROR: [generic] This video is DRM protected": "DRM",
            "ERROR: Sign in to confirm your age. This video may be inappropriate for some users.":
                "AGE_RESTRICTED",
            "ERROR: Sign in to confirm you’re not a bot.": "BOT_CHECK",
            "ERROR: This video is private": "LOGIN_REQUIRED",
            "ERROR: Join this channel to get access to members-only content": "LOGIN_REQUIRED",
            "ERROR: Unsupported URL: https://x.example/": "UNSUPPORTED",
            "ERROR: This live event will begin in 3 hours.": "UPCOMING",
            "ERROR: The uploader has not made this video available in your country": "GEO_BLOCKED",
            "ERROR: Video unavailable": "UNAVAILABLE",
            "ERROR: Requested format is not available": "NO_FORMAT",
            "ERROR: [Errno 28] No space left on device": "DISK_FULL",
            "ERROR: Unable to download webpage: <urlopen error timed out>": "NETWORK",
        }
        for text, code in cases.items():
            with self.subTest(code=code):
                self.assertEqual(classify_error(text, stage="probe")[0], code)

    def test_an_unsupported_page_reads_as_not_supported_yet(self):
        code, message = classify_error("ERROR: Unsupported URL: https://phim.example/phim/1", stage="probe")
        self.assertEqual(code, "UNSUPPORTED")
        self.assertTrue(message.startswith("Trang này chưa được hỗ trợ"), message)

    def test_words_of_the_link_never_decide_the_code(self):
        for path in ("login/phim-1", "account/1", "premium/tap-2", "subscribe/x", "cookies/a", "drm/b", "private"):
            with self.subTest(path=path):
                code, _ = classify_error(f"ERROR: [generic] Unsupported URL: https://site.example/{path}",
                                         stage="probe")
                self.assertEqual(code, "UNSUPPORTED")

    def test_links_and_addresses_never_reach_the_message(self):
        cases = ("ERROR: Unsupported URL: http://127.0.0.1:8765/api/x",
                 "ERROR: [generic] Unable to download webpage: <urlopen error [WinError 10061] refused> "
                 "(caused by http://192.168.1.1/admin)",
                 "ERROR: [generic] connect to [fe80::1%12]:8080 refused",
                 "ERROR: [generic] connection to 10.0.0.5:22 timed out")
        for text in cases:
            with self.subTest(text=text[:40]):
                _, message = classify_error(text, stage="probe")
                for leaked in ("127.0.0.1", "8765", "192.168.1.1", "fe80", "10.0.0.5", "http://"):
                    self.assertNotIn(leaked, message)

    def test_unknown_errors_keep_the_last_error_line(self):
        code, message = classify_error("noise\nERROR: something odd happened\n", stage="download")
        self.assertEqual(code, "DOWNLOAD_FAILED")
        self.assertIn("something odd happened", message)
        self.assertEqual(classify_error("", stage="probe")[0], "PROBE_FAILED")


class FormatTests(unittest.TestCase):
    def test_durations(self):
        self.assertEqual(format_duration(65), "1:05")
        self.assertEqual(format_duration(5400), "1:30:00")
        self.assertEqual(format_duration(None), "?")


if __name__ == "__main__":
    unittest.main()
