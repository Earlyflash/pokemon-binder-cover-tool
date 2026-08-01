import argparse
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import binder_cover as bc  # noqa: E402


# ---------------------------------------------------------------- helpers --

def fetch_side_effect(ja_listing=None, en_listing=None, details=None, listings=None):
    """Build a stand-in for binder_cover._fetch_json that never touches the
    network. `details` maps (lang, set_id) -> detail dict (or None for 404).
    `listings` maps lang -> set listing, for languages beyond ja/en (any dataset
    not given here -- e.g. zh-cn/zh-tw/ko when not explicitly under test --
    simulates an unreachable dataset, same as a real network failure)."""
    all_listings = dict(listings or {})
    all_listings.setdefault("ja", ja_listing if ja_listing is not None else [])
    all_listings.setdefault("en", en_listing if en_listing is not None else [])
    details = details or {}

    def _fake_fetch(url, verbose=False):
        for lang, listing in all_listings.items():
            if url == f"{bc.TCGDEX_BASE}/{lang}/sets":
                return listing
        for (lang, set_id), detail in details.items():
            if url == f"{bc.TCGDEX_BASE}/{lang}/sets/{set_id}":
                return detail
        return None

    return _fake_fetch


# -------------------------------------------------------------- pure fns --

class TestHexToRgb(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(bc.hex_to_rgb("C42A22"), (196, 42, 34))

    def test_strips_hash_prefix(self):
        self.assertEqual(bc.hex_to_rgb("#FFFFFF"), (255, 255, 255))

    def test_black(self):
        self.assertEqual(bc.hex_to_rgb("000000"), (0, 0, 0))


class TestFormatReleaseDate(unittest.TestCase):
    def test_iso_dash_format(self):
        self.assertEqual(bc._format_release_date("2026-05-22"), "22 MAY 2026")

    def test_slash_format(self):
        self.assertEqual(bc._format_release_date("2026/08/01"), "1 AUG 2026")

    def test_single_digit_day_has_no_leading_zero(self):
        self.assertEqual(bc._format_release_date("2026-01-05"), "5 JAN 2026")

    def test_unparseable_falls_back_to_uppercased_raw(self):
        self.assertEqual(bc._format_release_date("not a date"), "NOT A DATE")


class TestFetchJson(unittest.TestCase):
    """_fetch_json runs inside ThreadPoolExecutor workers elsewhere in the file, so
    every plausible network failure mode must degrade to None, not raise -- an
    uncaught exception here would surface as a raw crash via future.result()."""

    def _assert_degrades_to_none(self, exc):
        with patch("urllib.request.urlopen", side_effect=exc):
            self.assertIsNone(bc._fetch_json("https://example.com/x"))

    def test_url_error_returns_none(self):
        import urllib.error
        self._assert_degrades_to_none(urllib.error.URLError("no route"))

    def test_socket_timeout_returns_none(self):
        self._assert_degrades_to_none(TimeoutError("timed out"))

    def test_connection_reset_returns_none(self):
        self._assert_degrades_to_none(ConnectionResetError("connection reset"))

    def test_incomplete_http_read_returns_none(self):
        import http.client
        self._assert_degrades_to_none(http.client.IncompleteRead(b""))

    def test_malformed_json_returns_none(self):
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.read.return_value = b"not json"
        resp.__enter__.return_value = resp
        with patch("urllib.request.urlopen", return_value=resp):
            self.assertIsNone(bc._fetch_json("https://example.com/x"))


class TestIsLatinText(unittest.TestCase):
    def test_ascii_is_latin(self):
        self.assertTrue(bc._is_latin_text("Mega Series"))

    def test_japanese_is_not_latin(self):
        self.assertFalse(bc._is_latin_text("ポケモンカードゲーム MEGA"))

    def test_latin1_accented_is_latin(self):
        self.assertTrue(bc._is_latin_text("Café"))


# --------------------------------------------------------- fonts/drawing --

class TestFontResolution(unittest.TestCase):
    def test_bundled_font_resolves(self):
        path, ok = bc.resolve_font_path("bold_caps")
        self.assertTrue(ok)
        self.assertTrue(os.path.isfile(path))

    def test_fit_font_shrinks_to_fit_or_hits_min_size(self):
        text = "A VERY VERY VERY LONG SET NAME INDEED"
        min_size = 20
        font = bc.fit_font("black_display", text, start_size=400, max_width=500,
                            min_size=min_size, extra_font_dir=None)
        width = sum(bc.text_w(font, c) for c in text)
        self.assertTrue(width <= 500 or font.size == min_size)
        self.assertLessEqual(font.size, 400)


class TestMeasureAndDrawSpaced(unittest.TestCase):
    def test_measure_matches_draw_return_value(self):
        from PIL import Image, ImageDraw
        font, _ = bc.get_font("medium", 40)
        img = Image.new("RGB", (10, 10))
        draw = ImageDraw.Draw(img)
        measured = bc.measure_spaced(font, "HELLO WORLD", 5)
        drawn_total = bc.draw_spaced(draw, 100, 0, "HELLO WORLD", font, (0, 0, 0), 5)
        self.assertEqual(measured, drawn_total)


# ------------------------------------------------------------- QR images --

class TestMakeQrImage(unittest.TestCase):
    def test_returns_image_of_requested_size(self):
        try:
            import qrcode  # noqa: F401
        except ImportError:
            self.skipTest("qrcode not installed")
        img = bc.make_qr_image("https://example.com/abyss-eye", 100)
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (100, 100))


# --------------------------------------------------------------- lookup --

class TestFindSetId(unittest.TestCase):
    JA_LISTING = [{"id": "M5", "name": "アビスアイ"},
                  {"id": "M2A", "name": "メガブレイブ"}]
    EN_LISTING = [{"id": "base1", "name": "Base Set"},
                  {"id": "sv1", "name": "Scarlet & Violet"}]

    @patch("binder_cover._fetch_json")
    def test_exact_id_match_in_japanese_dataset(self, mock_fetch):
        mock_fetch.side_effect = fetch_side_effect(self.JA_LISTING, self.EN_LISTING)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(bc._find_set_id("M5"), "M5")

    @patch("binder_cover._fetch_json")
    def test_id_match_is_case_insensitive(self, mock_fetch):
        mock_fetch.side_effect = fetch_side_effect(self.JA_LISTING, self.EN_LISTING)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(bc._find_set_id("m5"), "M5")

    @patch("binder_cover._fetch_json")
    def test_name_match_falls_back_to_english_dataset(self, mock_fetch):
        mock_fetch.side_effect = fetch_side_effect(self.JA_LISTING, self.EN_LISTING)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(bc._find_set_id("Base Set"), "base1")

    @patch("binder_cover._fetch_json")
    def test_no_match_returns_none(self, mock_fetch):
        mock_fetch.side_effect = fetch_side_effect(self.JA_LISTING, self.EN_LISTING)
        with redirect_stdout(io.StringIO()):
            self.assertIsNone(bc._find_set_id("Totally Made Up Set"))

    @patch("binder_cover._fetch_json")
    def test_ambiguous_name_match_returns_none_and_warns(self, mock_fetch):
        ja_listing = [{"id": "a1", "name": "Ancient Origins"},
                      {"id": "a2", "name": "Ancient Origins Reprint"}]
        mock_fetch.side_effect = fetch_side_effect(ja_listing, [])
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = bc._find_set_id("Ancient")
        self.assertIsNone(result)
        self.assertIn("Multiple TCGdex sets", buf.getvalue())

    @patch("binder_cover._fetch_json")
    def test_falls_through_to_korean_and_chinese_datasets(self, mock_fetch):
        # Not in ja/en/zh-cn at all -- only zh-tw and ko have it.
        mock_fetch.side_effect = fetch_side_effect(
            ja_listing=[], en_listing=[],
            listings={"zh-cn": [], "zh-tw": [{"id": "SC2b", "name": "測試"}], "ko": []},
        )
        with redirect_stdout(io.StringIO()):
            self.assertEqual(bc._find_set_id("SC2b"), "SC2b")

    @patch("binder_cover._fetch_json")
    def test_korean_only_name_match(self, mock_fetch):
        mock_fetch.side_effect = fetch_side_effect(
            ja_listing=[], en_listing=[],
            listings={"zh-cn": [], "zh-tw": [], "ko": [{"id": "SM1M", "name": "테스트"}]},
        )
        with redirect_stdout(io.StringIO()):
            self.assertEqual(bc._find_set_id("테스트"), "SM1M")


class TestLookupSetInfo(unittest.TestCase):
    @patch("binder_cover._fetch_json")
    def test_japan_exclusive_set_merges_expected_fields(self, mock_fetch):
        ja_listing = [{"id": "M5", "name": "アビスアイ"}]
        ja_detail = {
            "id": "M5",
            "name": "アビスアイ",
            "releaseDate": "2026-05-22",
            "serie": {"id": "M", "name": "ポケモンカードゲーム MEGA"},
            "cardCount": {"official": 81, "total": 118},
            "cards": [{"id": "M5-001"}, {"id": "M5-002"}],
        }
        mock_fetch.side_effect = fetch_side_effect(
            ja_listing=ja_listing, en_listing=[],
            details={("ja", "M5"): ja_detail, ("en", "M5"): None},
        )
        with redirect_stdout(io.StringIO()):
            info = bc.lookup_set_info("M5")

        self.assertEqual(info["set_code"], "M5")
        self.assertNotIn("name", info)  # no English release on TCGdex
        self.assertEqual(info["name_jp"], "アビスアイ")
        self.assertEqual(info["_name_unavailable_local"], ("Japanese", "アビスアイ"))
        self.assertEqual(info["release_date"], "22 MAY 2026")
        self.assertNotIn("era", info)  # non-Latin series name must not be auto-filled
        self.assertEqual(info["total_cards"], 118)
        self.assertEqual(info["stats"], [("Main Set", 81), ("Secret Rares", 37)])
        self.assertEqual(info["_cards"], ja_detail["cards"])
        self.assertEqual(info["_cards_lang"], "ja")

    @patch("binder_cover._fetch_json")
    def test_english_set_gets_latin_era_and_no_split_when_no_secret_rares(self, mock_fetch):
        en_listing = [{"id": "base1", "name": "Base Set"}]
        en_detail = {
            "id": "base1",
            "name": "Base Set",
            "releaseDate": "1999-01-09",
            "serie": {"id": "base", "name": "Base"},
            "cardCount": {"official": 102, "total": 102},
        }
        mock_fetch.side_effect = fetch_side_effect(
            ja_listing=[], en_listing=en_listing,
            details={("en", "base1"): en_detail, ("ja", "base1"): None},
        )
        with redirect_stdout(io.StringIO()):
            info = bc.lookup_set_info("base1")

        self.assertEqual(info["name"], "Base Set")
        self.assertEqual(info["era"], "Base")
        self.assertEqual(info["total_cards"], 102)
        self.assertNotIn("stats", info)  # official == total, nothing to split

    @patch("binder_cover._fetch_json")
    def test_set_not_found_returns_empty_dict(self, mock_fetch):
        mock_fetch.side_effect = fetch_side_effect(ja_listing=[], en_listing=[])
        with redirect_stdout(io.StringIO()):
            info = bc.lookup_set_info("Nothing Like This Exists")
        self.assertEqual(info, {})

    @patch("binder_cover._fetch_json")
    def test_traditional_chinese_exclusive_set_falls_back_correctly(self, mock_fetch):
        # No en/ja/zh-cn/ko detail at all -- only zh-tw, mirroring a real set (SC2b)
        # that TCGdex only has under Traditional Chinese.
        zh_tw_listing = [{"id": "SC2b", "name": "測試"}]
        zh_tw_detail = {
            "id": "SC2b", "name": "測試", "releaseDate": "2020-08-21",
            "cardCount": {"official": 144, "total": 144},
        }
        mock_fetch.side_effect = fetch_side_effect(
            ja_listing=[], en_listing=[],
            listings={"zh-cn": [], "zh-tw": zh_tw_listing, "ko": []},
            details={("zh-tw", "SC2b"): zh_tw_detail, ("en", "SC2b"): None,
                      ("ja", "SC2b"): None, ("zh-cn", "SC2b"): None, ("ko", "SC2b"): None},
        )
        with redirect_stdout(io.StringIO()):
            info = bc.lookup_set_info("SC2b")

        self.assertEqual(info["set_code"], "SC2B")
        self.assertNotIn("name", info)
        self.assertEqual(info["_name_unavailable_local"], ("Traditional Chinese", "測試"))
        self.assertEqual(info["release_date"], "21 AUG 2020")
        self.assertEqual(info["total_cards"], 144)
        self.assertNotIn("stats", info)  # official == total

    @patch("binder_cover._fetch_json")
    def test_english_field_priority_beats_search_order_for_extraction(self, mock_fetch):
        # Matched via the ja-first search order, but once matched, English data (when
        # present) is still preferred for era/release/cardCount extraction.
        ja_listing = [{"id": "M5", "name": "アビスアイ"}]
        ja_detail = {"id": "M5", "name": "アビスアイ", "releaseDate": "2026-01-01",
                     "cardCount": {"official": 1, "total": 1}}
        en_detail = {"id": "M5", "name": "Abyss Eye", "releaseDate": "2026-05-22",
                     "serie": {"name": "Mega Series"}, "cardCount": {"official": 81, "total": 118}}
        mock_fetch.side_effect = fetch_side_effect(
            ja_listing=ja_listing, en_listing=[],
            details={("ja", "M5"): ja_detail, ("en", "M5"): en_detail},
        )
        with redirect_stdout(io.StringIO()):
            info = bc.lookup_set_info("M5")

        self.assertEqual(info["name"], "Abyss Eye")
        self.assertEqual(info["release_date"], "22 MAY 2026")
        self.assertEqual(info["era"], "Mega Series")
        self.assertEqual(info["total_cards"], 118)


class TestListAllSets(unittest.TestCase):
    @patch("binder_cover._fetch_json")
    def test_merges_across_languages_and_prints_count(self, mock_fetch):
        mock_fetch.side_effect = fetch_side_effect(
            ja_listing=[{"id": "M5", "name": "アビスアイ"}],
            en_listing=[{"id": "base1", "name": "Base Set"}],
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            bc.list_all_sets()
        output = buf.getvalue()
        self.assertIn("2 sets total", output)
        self.assertIn("base1", output)
        self.assertIn("Base Set", output)
        self.assertIn("M5", output)

    @patch("binder_cover._fetch_json")
    def test_merges_chinese_and_korean_datasets_too(self, mock_fetch):
        mock_fetch.side_effect = fetch_side_effect(
            ja_listing=[], en_listing=[],
            listings={
                "zh-cn": [{"id": "SC1", "name": "简体测试"}],
                "zh-tw": [{"id": "SC2b", "name": "測試"}],
                "ko": [{"id": "SM1M", "name": "테스트"}],
            },
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            bc.list_all_sets()
        output = buf.getvalue()
        self.assertIn("3 sets total", output)
        self.assertIn("SC1", output)
        self.assertIn("SC2b", output)
        self.assertIn("SM1M", output)

    @patch("binder_cover._fetch_json")
    def test_total_failure_warns_instead_of_crashing(self, mock_fetch):
        mock_fetch.return_value = None
        buf = io.StringIO()
        with redirect_stdout(buf):
            bc.list_all_sets()
        self.assertIn("Couldn't reach TCGdex", buf.getvalue())


# --------------------------------------------------------------- rarity --

class TestAbbreviateRarity(unittest.TestCase):
    def test_known_rarities_use_the_standard_short_code(self):
        self.assertEqual(bc._abbreviate_rarity("Common"), "C")
        self.assertEqual(bc._abbreviate_rarity("Double Rare"), "RR")
        self.assertEqual(bc._abbreviate_rarity("Art Rare"), "AR")
        self.assertEqual(bc._abbreviate_rarity("Ultra Rare"), "UR")

    def test_unrecognized_rarity_falls_back_to_initials(self):
        self.assertEqual(bc._abbreviate_rarity("Mega Hyper Rare"), "MHR")

    def test_is_case_insensitive(self):
        self.assertEqual(bc._abbreviate_rarity("COMMON"), "C")


class TestSortAndAbbreviateRarities(unittest.TestCase):
    def test_orders_by_canonical_rarity_tier_not_by_count(self):
        counts = {"Ultra Rare": 18, "Common": 38, "Double Rare": 8, "Uncommon": 27}
        result = bc._sort_and_abbreviate_rarities(counts)
        self.assertEqual(result, [("C", 38), ("U", 27), ("RR", 8), ("UR", 18)])

    def test_unrecognized_rarities_sort_after_known_ones_alphabetically(self):
        counts = {"Common": 10, "Zebra Rare": 1, "Alpha Rare": 2}
        result = bc._sort_and_abbreviate_rarities(counts)
        labels_in_order = [label for label, _ in result]
        self.assertEqual(labels_in_order[0], "C")
        self.assertEqual(set(labels_in_order[1:]), {"AR", "ZR"})

    def test_colliding_abbreviations_are_disambiguated(self):
        # Two distinct, unrecognized rarity names that would both abbreviate to "MR"
        counts = {"Mega Rare": 3, "Mystic Rare": 5}
        result = bc._sort_and_abbreviate_rarities(counts)
        labels = [label for label, _ in result]
        self.assertEqual(len(labels), len(set(labels)))  # no duplicate labels


class TestFetchRarityCounts(unittest.TestCase):
    @patch("binder_cover._fetch_json")
    def test_counts_and_abbreviates_successful_fetches(self, mock_fetch):
        cards = [{"id": f"M5-{i}"} for i in range(1, 6)]
        rarity_by_id = {
            "M5-1": {"rarity": "Common"}, "M5-2": {"rarity": "Common"},
            "M5-3": {"rarity": "Uncommon"}, "M5-4": {"rarity": "Rare"},
            "M5-5": {"rarity": "Rare"},
        }
        mock_fetch.side_effect = lambda url, verbose=False: rarity_by_id.get(url.rsplit("/", 1)[-1])
        with redirect_stdout(io.StringIO()):
            result = bc.fetch_rarity_counts(cards, "ja")
        self.assertEqual(result, [("C", 2), ("U", 1), ("R", 2)])

    @patch("binder_cover._fetch_json")
    def test_failed_fetches_are_excluded_and_reported(self, mock_fetch):
        cards = [{"id": "M5-1"}, {"id": "M5-2"}]

        def side_effect(url, verbose=False):
            return {"rarity": "Common"} if url.endswith("M5-1") else None

        mock_fetch.side_effect = side_effect
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = bc.fetch_rarity_counts(cards, "ja")
        self.assertEqual(result, [("C", 1)])
        self.assertIn("Couldn't determine rarity for 1/2 cards", buf.getvalue())

    def test_no_cards_returns_empty_list(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(bc.fetch_rarity_counts([], "en"), [])


# -------------------------------------------------------------- CLI/main --

class TestArgParser(unittest.TestCase):
    def test_set_is_optional_at_the_parser_level(self):
        # argparse can't express "required unless --list-sets"; main() enforces that
        # itself (see TestMainCli), so the parser alone should just accept no args.
        parser = bc.build_arg_parser()
        args = parser.parse_args([])
        self.assertIsNone(args.set_query)
        self.assertFalse(args.list_sets)

    def test_list_sets_alone_parses_fine(self):
        parser = bc.build_arg_parser()
        args = parser.parse_args(["--list-sets"])
        self.assertTrue(args.list_sets)
        self.assertIsNone(args.set_query)

    def test_stat_flag_appends_pairs(self):
        parser = bc.build_arg_parser()
        args = parser.parse_args(["--set", "M5", "--stat", "Main Set", "81",
                                   "--stat", "Secret Rares", "37"])
        self.assertEqual(args.stat, [["Main Set", "81"], ["Secret Rares", "37"]])

    def test_accent_tab_default_and_negation(self):
        parser = bc.build_arg_parser()
        self.assertTrue(parser.parse_args(["--set", "M5"]).accent_tab)
        self.assertFalse(parser.parse_args(["--set", "M5", "--no-accent-tab"]).accent_tab)

    def test_paper_defaults_to_square_and_size_defaults_to_none(self):
        parser = bc.build_arg_parser()
        args = parser.parse_args(["--set", "M5"])
        self.assertEqual(args.paper, "square")
        self.assertIsNone(args.size)


class TestMainCli(unittest.TestCase):
    def test_neither_set_nor_list_sets_exits_with_error(self):
        buf_err = io.StringIO()
        with self.assertRaises(SystemExit):
            with redirect_stdout(io.StringIO()), redirect_stderr(buf_err):
                bc.main([])
        self.assertIn("--set is required", buf_err.getvalue())

    @patch("binder_cover.lookup_set_info")
    def test_missing_required_fields_after_lookup_exits_with_error(self, mock_lookup):
        mock_lookup.return_value = {}
        buf_err = io.StringIO()
        with self.assertRaises(SystemExit):
            with redirect_stdout(io.StringIO()), redirect_stderr(buf_err):
                bc.main(["--set", "Nonexistent Set"])
        self.assertIn("couldn't determine", buf_err.getvalue())

    @patch("binder_cover.lookup_set_info")
    def test_full_run_writes_png_with_jp_filename_tag(self, mock_lookup):
        mock_lookup.return_value = {
            "set_code": "M5", "name_jp": "アビスアイ",
            "release_date": "22 MAY 2026", "total_cards": 118,
            "stats": [("Main Set", 81), ("Secret Rares", 37)],
        }
        with tempfile.TemporaryDirectory() as tmp:
            prev_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with redirect_stdout(io.StringIO()):
                    bc.main(["--set", "M5", "--name", "Abyss Eye", "--size", "300"])
                self.assertTrue(os.path.isfile("M5_Abyss_Eye_JP_cover.png"))
            finally:
                os.chdir(prev_cwd)

    @patch("binder_cover.lookup_set_info")
    def test_full_run_writes_png_with_en_filename_tag_when_no_japanese_name(self, mock_lookup):
        mock_lookup.return_value = {
            "set_code": "BASE1", "name": "Base Set",
            "release_date": "9 JAN 1999", "total_cards": 102,
        }
        with tempfile.TemporaryDirectory() as tmp:
            prev_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with redirect_stdout(io.StringIO()):
                    bc.main(["--set", "base1", "--size", "300"])
                self.assertTrue(os.path.isfile("BASE1_Base_Set_EN_cover.png"))
            finally:
                os.chdir(prev_cwd)

    @patch("binder_cover.fetch_rarity_counts")
    @patch("binder_cover.lookup_set_info")
    def test_rarity_chart_flag_fetches_using_stashed_cards(self, mock_lookup, mock_fetch_rarity):
        mock_lookup.return_value = {
            "set_code": "M5", "name_jp": "アビスアイ",
            "release_date": "22 MAY 2026", "total_cards": 118,
            "_cards": [{"id": "M5-001"}], "_cards_lang": "ja",
        }
        mock_fetch_rarity.return_value = [("C", 1)]
        with tempfile.TemporaryDirectory() as tmp:
            prev_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with redirect_stdout(io.StringIO()):
                    bc.main(["--set", "M5", "--name", "Abyss Eye", "--rarity-chart", "--size", "300"])
            finally:
                os.chdir(prev_cwd)
        mock_fetch_rarity.assert_called_once_with([{"id": "M5-001"}], "ja", verbose=False)

    @patch("binder_cover.fetch_rarity_counts")
    @patch("binder_cover.lookup_set_info")
    def test_rarity_chart_flag_without_a_card_list_warns_and_skips(self, mock_lookup, mock_fetch_rarity):
        mock_lookup.return_value = {
            "set_code": "M5", "name": "Abyss Eye",
            "release_date": "22 MAY 2026", "total_cards": 118,
        }
        with tempfile.TemporaryDirectory() as tmp:
            prev_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    bc.main(["--set", "M5", "--rarity-chart", "--size", "300"])
            finally:
                os.chdir(prev_cwd)
        mock_fetch_rarity.assert_not_called()
        self.assertIn("skipping the rarity chart", buf.getvalue())

    @patch("binder_cover.build_cover")
    @patch("binder_cover.lookup_set_info")
    def test_default_footer_says_japanese_when_japanese_name_shown(self, mock_lookup, mock_build):
        mock_lookup.return_value = {
            "set_code": "M5", "name_jp": "アビスアイ",
            "release_date": "22 MAY 2026", "total_cards": 118,
        }
        mock_build.return_value = Image.new("RGB", (10, 10))
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()):
                bc.main(["--set", "M5", "--name", "Abyss Eye", "--out", os.path.join(tmp, "x.png")])
        self.assertEqual(mock_build.call_args[0][0].footer, "Japanese Master Set")

    @patch("binder_cover.build_cover")
    @patch("binder_cover.lookup_set_info")
    def test_default_footer_says_english_when_no_japanese_name(self, mock_lookup, mock_build):
        mock_lookup.return_value = {
            "set_code": "BASE1", "name": "Base Set",
            "release_date": "9 JAN 1999", "total_cards": 102,
        }
        mock_build.return_value = Image.new("RGB", (10, 10))
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()):
                bc.main(["--set", "base1", "--out", os.path.join(tmp, "x.png")])
        self.assertEqual(mock_build.call_args[0][0].footer, "English Master Set")

    @patch("binder_cover.build_cover")
    @patch("binder_cover.lookup_set_info")
    def test_lang_flag_overrides_default_footer_language(self, mock_lookup, mock_build):
        mock_lookup.return_value = {
            "set_code": "BASE1", "name": "Base Set",
            "release_date": "9 JAN 1999", "total_cards": 102,
        }
        mock_build.return_value = Image.new("RGB", (10, 10))
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()):
                bc.main(["--set", "base1", "--lang-flag", "kr", "--out", os.path.join(tmp, "x.png")])
        self.assertEqual(mock_build.call_args[0][0].footer, "Korean Master Set")

    @patch("binder_cover.build_cover")
    @patch("binder_cover.lookup_set_info")
    def test_explicit_footer_is_not_replaced_by_the_language_default(self, mock_lookup, mock_build):
        mock_lookup.return_value = {
            "set_code": "M5", "name_jp": "アビスアイ",
            "release_date": "22 MAY 2026", "total_cards": 118,
        }
        mock_build.return_value = Image.new("RGB", (10, 10))
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()):
                bc.main(["--set", "M5", "--name", "Abyss Eye", "--footer", "My Own Footer",
                         "--out", os.path.join(tmp, "x.png")])
        self.assertEqual(mock_build.call_args[0][0].footer, "My Own Footer")

    @patch("binder_cover.list_all_sets")
    def test_list_sets_flag_bypasses_set_requirement(self, mock_list_all_sets):
        bc.main(["--list-sets"])
        mock_list_all_sets.assert_called_once()

    @patch("binder_cover.lookup_set_info")
    def test_set2_looks_up_second_set_and_fills_dual_fields(self, mock_lookup):
        mock_lookup.side_effect = [
            {"set_code": "M3", "name_jp": "ムニキスゼロ",
             "release_date": "23 JAN 2026", "total_cards": 117},
            {"set_code": "M4", "name_jp": "ニンジャスピナー",
             "release_date": "13 MAR 2026", "total_cards": 120},
        ]
        captured = {}
        real_build_cover = bc.build_cover

        def spy_build_cover(cfg):
            captured["cfg"] = cfg
            return real_build_cover(cfg)

        with tempfile.TemporaryDirectory() as tmp:
            with patch("binder_cover.build_cover", side_effect=spy_build_cover):
                with redirect_stdout(io.StringIO()):
                    bc.main(["--set", "M3", "--name", "Nihil Zero",
                             "--set2", "M4", "--name2", "Ninja Spinner",
                             "--out", os.path.join(tmp, "x.png"), "--size", "300"])

        self.assertEqual(mock_lookup.call_args_list[1].args[0], "M4")
        cfg = captured["cfg"]
        self.assertEqual(cfg.set_code2, "M4")
        self.assertEqual(cfg.name2, "Ninja Spinner")
        self.assertEqual(cfg.name_jp2, "ニンジャスピナー")
        self.assertEqual(cfg.release_date2, "13 MAR 2026")
        self.assertEqual(cfg.total_cards2, "120")

    @patch("binder_cover.lookup_set_info")
    def test_set2_missing_fields_exits_with_error(self, mock_lookup):
        mock_lookup.side_effect = [
            {"set_code": "M3", "name": "Nihil Zero",
             "release_date": "23 JAN 2026", "total_cards": 117},
            {},
        ]
        buf_err = io.StringIO()
        with self.assertRaises(SystemExit):
            with redirect_stdout(io.StringIO()), redirect_stderr(buf_err):
                bc.main(["--set", "M3", "--set2", "Nonexistent Set2"])
        self.assertIn("couldn't determine", buf_err.getvalue())
        self.assertIn("--set2", buf_err.getvalue())

    @patch("binder_cover.lookup_set_info")
    def test_set2_uses_combined_default_output_filename(self, mock_lookup):
        mock_lookup.side_effect = [
            {"set_code": "M3", "name": "Nihil Zero",
             "release_date": "23 JAN 2026", "total_cards": 117},
            {"set_code": "M4", "name": "Ninja Spinner",
             "release_date": "13 MAR 2026", "total_cards": 120},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            prev_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with redirect_stdout(io.StringIO()):
                    bc.main(["--set", "M3", "--set2", "M4", "--size", "300"])
                self.assertTrue(os.path.isfile("M3_M4_dual_cover.png"))
            finally:
                os.chdir(prev_cwd)

    @patch("binder_cover.fetch_rarity_counts")
    @patch("binder_cover.lookup_set_info")
    def test_rarity_chart_flag_with_set2_fetches_both_sets(self, mock_lookup, mock_fetch_rarity):
        mock_lookup.side_effect = [
            {"set_code": "M3", "name": "Nihil Zero", "release_date": "23 JAN 2026",
             "total_cards": 117, "_cards": [{"id": "M3-001"}], "_cards_lang": "ja"},
            {"set_code": "M4", "name": "Ninja Spinner", "release_date": "13 MAR 2026",
             "total_cards": 120, "_cards": [{"id": "M4-001"}], "_cards_lang": "ja"},
        ]
        mock_fetch_rarity.side_effect = [[("C", 1)], [("U", 1)]]
        captured = {}
        real_build_cover = bc.build_cover

        def spy_build_cover(cfg):
            captured["cfg"] = cfg
            return real_build_cover(cfg)

        with tempfile.TemporaryDirectory() as tmp:
            with patch("binder_cover.build_cover", side_effect=spy_build_cover):
                with redirect_stdout(io.StringIO()):
                    bc.main(["--set", "M3", "--set2", "M4", "--rarity-chart",
                             "--out", os.path.join(tmp, "x.png"), "--size", "300"])

        self.assertEqual(mock_fetch_rarity.call_args_list[0].args, ([{"id": "M3-001"}], "ja"))
        self.assertEqual(mock_fetch_rarity.call_args_list[1].args, ([{"id": "M4-001"}], "ja"))
        self.assertEqual(captured["cfg"].rarity_counts, [("C", 1)])
        self.assertEqual(captured["cfg"].rarity_counts2, [("U", 1)])

    @patch("binder_cover.fetch_rarity_counts")
    @patch("binder_cover.lookup_set_info")
    def test_rarity_chart_flag_with_set2_and_no_card_list_warns_and_skips_second(
            self, mock_lookup, mock_fetch_rarity):
        mock_lookup.side_effect = [
            {"set_code": "M3", "name": "Nihil Zero", "release_date": "23 JAN 2026",
             "total_cards": 117, "_cards": [{"id": "M3-001"}], "_cards_lang": "ja"},
            {"set_code": "M4", "name": "Ninja Spinner", "release_date": "13 MAR 2026",
             "total_cards": 120},
        ]
        mock_fetch_rarity.return_value = [("C", 1)]
        with tempfile.TemporaryDirectory() as tmp:
            buf = io.StringIO()
            with redirect_stdout(buf):
                bc.main(["--set", "M3", "--set2", "M4", "--rarity-chart",
                         "--out", os.path.join(tmp, "x.png"), "--size", "300"])
        mock_fetch_rarity.assert_called_once()
        self.assertIn("skipping the second rarity chart", buf.getvalue())

    @patch("binder_cover.lookup_set_info")
    def test_without_set2_dual_fields_stay_unset(self, mock_lookup):
        mock_lookup.return_value = {
            "set_code": "BASE1", "name": "Base Set",
            "release_date": "9 JAN 1999", "total_cards": 102,
        }
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()):
                bc.main(["--set", "base1", "--out", os.path.join(tmp, "x.png")])
        self.assertEqual(mock_lookup.call_count, 1)

    @patch("binder_cover.lookup_set_info")
    def test_explicit_overrides_are_not_replaced_by_lookup(self, mock_lookup):
        mock_lookup.return_value = {
            "set_code": "M5", "name": "Should Not Be Used",
            "release_date": "1 JAN 2000", "total_cards": 1,
        }
        with tempfile.TemporaryDirectory() as tmp:
            prev_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with redirect_stdout(io.StringIO()):
                    bc.main(["--set", "M5", "--name", "My Own Name",
                             "--release-date", "22 MAY 2026", "--total-cards", "118",
                             "--size", "300"])
                self.assertTrue(os.path.isfile("M5_My_Own_Name_EN_cover.png"))
            finally:
                os.chdir(prev_cwd)

    @patch("binder_cover.build_cover")
    @patch("binder_cover.lookup_set_info")
    def test_paper_a5_defaults_size_to_1748_when_not_given(self, mock_lookup, mock_build):
        mock_lookup.return_value = {
            "set_code": "M5", "name": "Abyss Eye",
            "release_date": "22 MAY 2026", "total_cards": 118,
        }
        mock_build.return_value = Image.new("RGB", (10, 10))
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()):
                bc.main(["--set", "M5", "--paper", "a5", "--out", os.path.join(tmp, "x.png")])
        self.assertEqual(mock_build.call_args[0][0].size, 1748)

    @patch("binder_cover.build_cover")
    @patch("binder_cover.lookup_set_info")
    def test_paper_a5_landscape_also_defaults_size_to_1748(self, mock_lookup, mock_build):
        mock_lookup.return_value = {
            "set_code": "M5", "name": "Abyss Eye",
            "release_date": "22 MAY 2026", "total_cards": 118,
        }
        mock_build.return_value = Image.new("RGB", (10, 10))
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()):
                bc.main(["--set", "M5", "--paper", "a5-landscape", "--out", os.path.join(tmp, "x.png")])
        self.assertEqual(mock_build.call_args[0][0].size, 1748)

    @patch("binder_cover.build_cover")
    @patch("binder_cover.lookup_set_info")
    def test_paper_square_still_defaults_size_to_3600(self, mock_lookup, mock_build):
        mock_lookup.return_value = {
            "set_code": "M5", "name": "Abyss Eye",
            "release_date": "22 MAY 2026", "total_cards": 118,
        }
        mock_build.return_value = Image.new("RGB", (10, 10))
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()):
                bc.main(["--set", "M5", "--out", os.path.join(tmp, "x.png")])
        self.assertEqual(mock_build.call_args[0][0].size, 3600)

    @patch("binder_cover.build_cover")
    @patch("binder_cover.lookup_set_info")
    def test_explicit_size_overrides_the_paper_default(self, mock_lookup, mock_build):
        mock_lookup.return_value = {
            "set_code": "M5", "name": "Abyss Eye",
            "release_date": "22 MAY 2026", "total_cards": 118,
        }
        mock_build.return_value = Image.new("RGB", (10, 10))
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()):
                bc.main(["--set", "M5", "--paper", "a5", "--size", "900",
                         "--out", os.path.join(tmp, "x.png")])
        self.assertEqual(mock_build.call_args[0][0].size, 900)


# ---------------------------------------------------------- build_cover --

class TestBuildCoverSmoke(unittest.TestCase):
    def _base_cfg(self, **overrides):
        cfg = argparse.Namespace(
            set_code="M5", name="ABYSS EYE", name_jp="", game_title="Pokémon Card Game",
            era="", release_date="22 MAY 2026", total_cards="118",
            stats=[("Main Set", 81), ("Secret Rares", 37)],
            qr_url="", qr_caption="Scan To Track", qr_subcaption="",
            completion="", rarity_counts=[], footer="Japanese Master Set", accent="C42A22",
            bg_color="FFFFFF", accent_tab=True, lang_flag=None, size=600, font_dir=None, verbose=False,
        )
        for k, v in overrides.items():
            setattr(cfg, k, v)
        return cfg

    def test_renders_expected_size_and_mode(self):
        img = bc.build_cover(self._base_cfg())
        self.assertEqual(img.size, (600, 600))
        self.assertEqual(img.mode, "RGB")

    def test_renders_with_japanese_name(self):
        # Whether a CJK font is actually available is environment-dependent (no CJK
        # font is bundled), so this exercises either the real dual-name draw path or
        # the "no CJK font found, skip it" warning path -- either way, no exceptions.
        img = bc.build_cover(self._base_cfg(name_jp="アビスアイ"))
        self.assertEqual(img.size, (600, 600))

    def test_renders_with_era_subheading(self):
        img = bc.build_cover(self._base_cfg(era="Mega Series"))
        self.assertEqual(img.size, (600, 600))

    def test_renders_with_qr_code(self):
        img = bc.build_cover(self._base_cfg(qr_url="https://example.com/abyss-eye"))
        self.assertEqual(img.size, (600, 600))

    def test_renders_with_completion_gauge(self):
        img = bc.build_cover(self._base_cfg(completion="187/187"))
        self.assertEqual(img.size, (600, 600))

    def test_renders_with_no_stats_and_blank_right_column(self):
        img = bc.build_cover(self._base_cfg(stats=[], qr_url="", completion=""))
        self.assertEqual(img.size, (600, 600))

    def test_renders_with_single_stat_row(self):
        img = bc.build_cover(self._base_cfg(stats=[("Secret Rares", 37)]))
        self.assertEqual(img.size, (600, 600))

    def test_renders_at_a_different_canvas_size(self):
        img = bc.build_cover(self._base_cfg(size=900))
        self.assertEqual(img.size, (900, 900))

    def test_renders_with_rarity_chart(self):
        cfg = self._base_cfg(rarity_counts=[
            ("C", 38), ("U", 27), ("R", 8), ("RR", 8), ("UR", 18), ("MHR", 1),
        ])
        img = bc.build_cover(cfg)
        self.assertEqual(img.size, (600, 600))

    def test_qr_code_takes_priority_over_rarity_chart(self):
        # Placement decision: rarity chart only fills the right column when neither
        # --qr-url nor --completion is given.
        cfg = self._base_cfg(qr_url="https://example.com/abyss-eye",
                              rarity_counts=[("C", 38), ("U", 27)])
        img = bc.build_cover(cfg)
        self.assertEqual(img.size, (600, 600))

    def test_landscape_shows_qr_code_and_rarity_chart_side_by_side(self):
        # Landscape's wider right column has room for both instead of picking
        # one -- only kicks in when W > H (checked here via paper=a5-landscape).
        cfg = self._base_cfg(paper="a5-landscape",
                              qr_url="https://example.com/abyss-eye",
                              rarity_counts=[("C", 38), ("U", 27), ("R", 8)])
        img = bc.build_cover(cfg)
        self.assertEqual(img.width, round(600 * bc.A5_RATIO))
        self.assertEqual(img.height, 600)

    def test_a5_portrait_still_prioritizes_qr_over_rarity(self):
        # Portrait A5 isn't any wider than square, so the combined layout
        # shouldn't kick in there -- same priority as square.
        cfg = self._base_cfg(paper="a5",
                              qr_url="https://example.com/abyss-eye",
                              rarity_counts=[("C", 38), ("U", 27)])
        img = bc.build_cover(cfg)
        self.assertEqual(img.width, 600)
        self.assertEqual(img.height, round(600 * bc.A5_RATIO))

    def test_renders_with_each_lang_flag(self):
        for lang in ("en", "jp", "cn", "kr"):
            with self.subTest(lang=lang):
                img = bc.build_cover(self._base_cfg(lang_flag=lang))
                self.assertEqual(img.size, (600, 600))

    def test_renders_dual_set_cover(self):
        cfg = self._base_cfg(
            set_code2="M4", name2="NINJA SPINNER", name_jp2="",
            release_date2="13 MAR 2026", total_cards2="120",
        )
        img = bc.build_cover(cfg)
        self.assertEqual(img.size, (600, 600))
        self.assertEqual(img.mode, "RGB")

    def test_renders_dual_set_cover_with_japanese_names(self):
        # Same environment-dependent CJK caveat as test_renders_with_japanese_name.
        cfg = self._base_cfg(
            name_jp="ムニキスゼロ",
            set_code2="M4", name2="NINJA SPINNER", name_jp2="ニンジャスピナー",
            release_date2="13 MAR 2026", total_cards2="120",
        )
        img = bc.build_cover(cfg)
        self.assertEqual(img.size, (600, 600))

    def test_dual_set_cover_ignores_single_set_only_blocks(self):
        # era/--stat/QR/gauge/rarity-chart are single-set-only concerns -- passing
        # them alongside set_code2 should be harmlessly ignored, not raise.
        cfg = self._base_cfg(
            era="Mega Series", qr_url="https://example.com/x", rarity_counts=[("C", 1)],
            set_code2="M4", name2="NINJA SPINNER", name_jp2="",
            release_date2="13 MAR 2026", total_cards2="120",
        )
        img = bc.build_cover(cfg)
        self.assertEqual(img.size, (600, 600))

    def test_dual_set_cover_at_a_different_canvas_size(self):
        cfg = self._base_cfg(
            set_code2="M4", name2="NINJA SPINNER", name_jp2="",
            release_date2="13 MAR 2026", total_cards2="120", size=900,
        )
        img = bc.build_cover(cfg)
        self.assertEqual(img.size, (900, 900))

    def test_dual_set_cover_with_rarity_breakdown_in_both_columns(self):
        cfg = self._base_cfg(
            set_code2="M4", name2="NINJA SPINNER", name_jp2="",
            release_date2="13 MAR 2026", total_cards2="120",
            rarity_counts=[("C", 38), ("U", 26), ("R", 8)],
            rarity_counts2=[("C", 38), ("U", 29), ("R", 8), ("RR", 8), ("UR", 18)],
        )
        img = bc.build_cover(cfg)
        self.assertEqual(img.size, (600, 600))

    def test_dual_set_cover_with_rarity_breakdown_in_only_one_column(self):
        # The second set's card-rarity fetch can fail independently of the first's,
        # so each column's chart has to render fine on its own.
        cfg = self._base_cfg(
            set_code2="M4", name2="NINJA SPINNER", name_jp2="",
            release_date2="13 MAR 2026", total_cards2="120",
            rarity_counts=[("C", 38), ("U", 26)],
            rarity_counts2=[],
        )
        img = bc.build_cover(cfg)
        self.assertEqual(img.size, (600, 600))

    def test_dual_set_cover_with_qr_code_in_both_columns(self):
        cfg = self._base_cfg(
            set_code2="M4", name2="NINJA SPINNER", name_jp2="",
            release_date2="13 MAR 2026", total_cards2="120",
            qr_url="https://example.com/m3", qr_url2="https://example.com/m4",
        )
        img = bc.build_cover(cfg)
        self.assertEqual(img.size, (600, 600))

    def test_dual_set_cover_with_qr_code_in_one_column_and_rarity_in_the_other(self):
        cfg = self._base_cfg(
            set_code2="M4", name2="NINJA SPINNER", name_jp2="",
            release_date2="13 MAR 2026", total_cards2="120",
            qr_url="https://example.com/m3",
            rarity_counts2=[("C", 38), ("U", 29), ("R", 8)],
        )
        img = bc.build_cover(cfg)
        self.assertEqual(img.size, (600, 600))

    def test_dual_set_cover_qr_takes_priority_over_rarity_per_column(self):
        # Same QR > rarity priority as the single-set cover, decided independently
        # per column -- outside of landscape, a column never stacks both blocks
        # (see test_dual_set_cover_landscape_stacks_qr_and_rarity_per_column).
        cfg = self._base_cfg(
            set_code2="M4", name2="NINJA SPINNER", name_jp2="",
            release_date2="13 MAR 2026", total_cards2="120",
            qr_url="https://example.com/m3", rarity_counts=[("C", 38)],
            qr_url2="https://example.com/m4", rarity_counts2=[("C", 38)],
        )
        img = bc.build_cover(cfg)
        self.assertEqual(img.size, (600, 600))

    def test_dual_set_cover_landscape_stacks_qr_and_rarity_per_column(self):
        # Landscape's wider columns have room to stack QR + rarity chart in the
        # same column instead of picking one, independently per set.
        cfg = self._base_cfg(
            paper="a5-landscape",
            set_code2="M4", name2="NINJA SPINNER", name_jp2="",
            release_date2="13 MAR 2026", total_cards2="120",
            qr_url="https://example.com/m3", rarity_counts=[("C", 38), ("U", 26)],
            qr_url2="https://example.com/m4", rarity_counts2=[("C", 38), ("U", 29)],
        )
        img = bc.build_cover(cfg)
        self.assertEqual(img.width, round(600 * bc.A5_RATIO))
        self.assertEqual(img.height, 600)

    def test_dual_set_cover_landscape_one_column_qr_other_rarity_only(self):
        # A column with only a QR code (no rarity for that set) or only a rarity
        # chart (no QR for that set) still renders fine in landscape.
        cfg = self._base_cfg(
            paper="a5-landscape",
            set_code2="M4", name2="NINJA SPINNER", name_jp2="",
            release_date2="13 MAR 2026", total_cards2="120",
            qr_url="https://example.com/m3", rarity_counts=[],
            qr_url2="", rarity_counts2=[("C", 38), ("U", 29)],
        )
        img = bc.build_cover(cfg)
        self.assertEqual(img.width, round(600 * bc.A5_RATIO))
        self.assertEqual(img.height, 600)

    def test_paper_a5_produces_a_taller_portrait_canvas(self):
        cfg = self._base_cfg(paper="a5")
        img = bc.build_cover(cfg)
        self.assertEqual(img.width, 600)
        self.assertEqual(img.height, round(600 * bc.A5_RATIO))
        self.assertGreater(img.height, img.width)

    def test_paper_a5_landscape_produces_a_wider_canvas(self):
        # Reuses the exact a5 portrait geometry internally and rotates the
        # finished image 90 degrees, so width/height simply swap versus a5.
        cfg = self._base_cfg(paper="a5-landscape")
        img = bc.build_cover(cfg)
        self.assertEqual(img.width, round(600 * bc.A5_RATIO))
        self.assertEqual(img.height, 600)
        self.assertGreater(img.width, img.height)

    def test_paper_square_stays_square_when_set_explicitly(self):
        cfg = self._base_cfg(paper="square")
        img = bc.build_cover(cfg)
        self.assertEqual(img.size, (600, 600))

    def test_paper_a5_with_two_stat_rows_does_not_overflow_the_panel(self):
        # Regression check for the bug where fixed-size get_font(...) calls (not
        # auto-scaled by fit_font's shrink-to-width) drifted out of sync with the
        # W-scaled panel/margins at a canvas far from the 3600 default, pushing
        # the second --stat row (and the footer) below the card entirely.
        cfg = self._base_cfg(paper="a5", size=1748,
                              stats=[("Main Set", 80), ("Secret Rares", 37)])
        img = bc.build_cover(cfg)
        self.assertEqual(img.size, (1748, round(1748 * bc.A5_RATIO)))

    def test_paper_a5_dual_set_cover(self):
        cfg = self._base_cfg(
            paper="a5", set_code2="M4", name2="NINJA SPINNER", name_jp2="",
            release_date2="13 MAR 2026", total_cards2="120",
        )
        img = bc.build_cover(cfg)
        self.assertEqual(img.size, (600, round(600 * bc.A5_RATIO)))


class TestMakeFlagBadge(unittest.TestCase):
    def test_returns_image_of_requested_size_for_every_language(self):
        for lang in ("en", "jp", "cn", "kr"):
            with self.subTest(lang=lang):
                img = bc.make_flag_badge(80, 53, lang)
                self.assertEqual(img.size, (80, 53))
                self.assertEqual(img.mode, "RGB")


if __name__ == "__main__":
    unittest.main()
