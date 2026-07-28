import argparse
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import binder_cover as bc  # noqa: E402


# ---------------------------------------------------------------- helpers --

def fetch_side_effect(ja_listing=None, en_listing=None, details=None):
    """Build a stand-in for binder_cover._fetch_json that never touches the
    network. `details` maps (lang, set_id) -> detail dict (or None for 404)."""
    details = details or {}

    def _fake_fetch(url, verbose=False):
        if url == f"{bc.TCGDEX_BASE}/ja/sets":
            return ja_listing if ja_listing is not None else []
        if url == f"{bc.TCGDEX_BASE}/en/sets":
            return en_listing if en_listing is not None else []
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


class TestIsLatinText(unittest.TestCase):
    def test_ascii_is_latin(self):
        self.assertTrue(bc._is_latin_text("Mega Series"))

    def test_japanese_is_not_latin(self):
        self.assertFalse(bc._is_latin_text("ポケモンカードゲーム MEGA"))

    def test_latin1_accented_is_latin(self):
        self.assertTrue(bc._is_latin_text("Café"))


class TestParseStat(unittest.TestCase):
    def test_valid_pair(self):
        self.assertEqual(bc.parse_stat(["Secret Rares", "37"]), ("Secret Rares", "37"))

    def test_wrong_length_raises(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            bc.parse_stat(["only one"])


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
        self.assertEqual(info["_name_unavailable_jp"], "アビスアイ")
        self.assertEqual(info["release_date"], "22 MAY 2026")
        self.assertNotIn("era", info)  # non-Latin series name must not be auto-filled
        self.assertEqual(info["total_cards"], 118)
        self.assertEqual(info["stats"], [("Main Set", 81), ("Secret Rares", 37)])

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
    def test_total_failure_warns_instead_of_crashing(self, mock_fetch):
        mock_fetch.return_value = None
        buf = io.StringIO()
        with redirect_stdout(buf):
            bc.list_all_sets()
        self.assertIn("Couldn't reach TCGdex", buf.getvalue())


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

    @patch("binder_cover.list_all_sets")
    def test_list_sets_flag_bypasses_set_requirement(self, mock_list_all_sets):
        bc.main(["--list-sets"])
        mock_list_all_sets.assert_called_once()

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


# ---------------------------------------------------------- build_cover --

class TestBuildCoverSmoke(unittest.TestCase):
    def _base_cfg(self, **overrides):
        cfg = argparse.Namespace(
            set_code="M5", name="ABYSS EYE", name_jp="", game_title="Pokémon Card Game",
            era="", release_date="22 MAY 2026", total_cards="118",
            stats=[("Main Set", 81), ("Secret Rares", 37)],
            qr_url="", qr_caption="Scan To Track", qr_subcaption="",
            completion="", footer="Japanese Master Set", accent="C42A22",
            bg_color="FFFFFF", accent_tab=True, size=600, font_dir=None, verbose=False,
        )
        for k, v in overrides.items():
            setattr(cfg, k, v)
        return cfg

    def test_renders_expected_size_and_mode(self):
        img = bc.build_cover(self._base_cfg())
        self.assertEqual(img.size, (600, 600))
        self.assertEqual(img.mode, "RGB")

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


if __name__ == "__main__":
    unittest.main()
