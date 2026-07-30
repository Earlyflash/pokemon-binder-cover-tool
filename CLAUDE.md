# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file CLI tool (`binder_cover.py`) that generates print-ready, square
Pokémon TCG binder cover images (a "Pokedex"-style panel) as 3600x3600px PNGs.
Everything — font resolution, TCGdex lookup, QR generation, and pixel-level
drawing — lives in this one script; there is no package structure and it's
meant to be run directly. `tests/` holds a stdlib `unittest` suite (see
below) that's also run in CI via `.github/workflows/tests.yml`.

## Commands

Setup:
```
pip install -r requirements.txt
```
`qrcode[pil]` (in requirements.txt) is optional and only used by `--qr-url`;
the script degrades gracefully (prints a warning, skips the QR block) if it's
missing. There's also a `reportlab`-based QR fallback if `qrcode` isn't
installed but `reportlab` is.

Run the tool:
```
python binder_cover.py --set M5 --name "Abyss Eye" --out test_cover.png
```
`--set` (code or name) triggers a live lookup against TCGdex
(api.tcgdex.net) to fill in set code/name/name_jp/era/release date/total
cards/main-secret-rare split; any of those can still be overridden with
their own flag (`--name`, `--release-date`, etc.) when TCGdex has it wrong
or doesn't have the set yet. This means the tool needs internet access by
default. See README.md for the full example invocations (QR-code style vs.
completion-gauge style), the full flag table, and the "Sets with no English
release yet" caveats (TCGdex only indexes a set's English name once it has
an English release — search those by set code, not fan translation; applies
to Japan/China/Korea-exclusive sets alike, not just Japanese ones).

Debugging font selection: pass `-v/--verbose` to print which font file was
resolved for each text role (bundled vs. system match vs. not found).

Run tests:
```
python -m unittest discover -s tests -v
```
The suite mocks every TCGdex network call (via `unittest.mock.patch` on
`binder_cover._fetch_json`/`lookup_set_info`/`list_all_sets`) so it's fast
and deterministic in CI — it never hits the real API. `TestBuildCoverSmoke`
renders actual small (600px) images through the real two-pass layout engine
to catch layout regressions; there's no pixel-diffing, just size/mode and
no-exceptions checks. Font-resolution tests only cover the bundled fonts in
`fonts/`, since CI runners won't have the same system fonts a local machine
might. There is no separate lint/build command — for anything not covered
by the tests, verify by actually running the script and inspecting the
output PNG.

## Architecture

Everything is in `binder_cover.py`, organized into five sections (see the
`# ---- comment ----` banners in the file):

1. **Font resolution** (`resolve_font_path`, `get_font`, `_build_font_index`,
   `fit_font`). Fonts are looked up by abstract "role" (e.g.
   `black_display`, `bold_caps`, `medium`, `italic`, `japanese`), not by
   filename. For each role, `BUNDLED` maps to a font shipped in `fonts/`
   (Poppins/Lato weights); if that's missing, `ROLE_KEYWORDS` gives a list of
   filename substrings to search for across OS font directories (Windows
   Fonts, macOS System/Library Fonts, Linux `/usr/share/fonts` etc.), walked
   and cached once in `_FONT_INDEX_CACHE`. Japanese text relies entirely on
   whatever CJK font the OS provides (Noto/Meiryo/Yu Gothic/Hiragino/MS
   Gothic) — there's no bundled CJK font — and is silently skipped with a
   warning if none is found. `fit_font` shrinks a font's point size in a loop
   until the rendered text fits a target pixel width, which is how the tool
   avoids ever overflowing the panel with long set names/footers.

2. **Drawing primitives** (`draw_spaced`, `measure_spaced`, `centered`,
   `pokeball`, `make_qr_image`, `make_flag_badge`, `hex_to_rgb`). Low-level
   Pillow helpers: letter-spaced text (used everywhere for the all-caps
   display text), centered text, the Poke Ball icon drawn as concentric
   ellipses/pieslices, QR image generation with a `qrcode` → `reportlab` →
   skip fallback chain, and the `--lang-flag` corner badge (`en`/`jp`/`cn`/
   `kr`) — four simplified national flags built entirely from primitives
   (ellipses, pieslices, polygons via `_star_points`, rectangles), not image
   assets, so they scale cleanly with `--size` like everything else. It's a
   purely cosmetic label the user picks themselves (also sets the default
   `--footer` text) — unrelated to the TCGdex lookup, which finds Chinese/
   Korean sets on its own regardless of this flag; there's just no
   `--name-cn`/`--name-kr` display field the way `--name-jp` exists for
   Japanese.

3. **Set lookup** (`lookup_set_info`, `_find_set_id`, `_fetch_json`,
   `_format_release_date`, `_is_latin_text`, `list_all_sets`). Queries
   TCGdex's REST API (`api.tcgdex.net/v2/{lang}/sets...`) across five
   datasets — `TCGDEX_LANGS = ("ja", "en", "zh-cn", "zh-tw", "ko")` — by set
   code first, then by name, in that priority order (Japanese-first so an
   ambiguous English-name match can't shadow a Japan-exclusive set). Once a
   set is matched, its detail is fetched from all five languages
   *concurrently* (`concurrent.futures.ThreadPoolExecutor`, network-bound so
   threads are fine despite the GIL), and individual fields are extracted
   via `_first_field(detail, field)`, which prefers `TCGDEX_FIELD_PRIORITY =
   ("en", "ja", "zh-cn", "zh-tw", "ko")` — English first when more than one
   dataset has a field, since it's the most standardized, which is a
   *different* order than the search priority above. Returns a plain dict
   of whatever fields it found — callers merge it in only where the
   corresponding CLI flag wasn't explicitly given, so any field can be
   overridden by hand. Main Set/Secret Rares stat rows are derived from
   TCGdex's `cardCount` (`official` = main-set count, `total` = grand total
   including secret rares/alt arts). Prints a running commentary of what
   it's checking and found at each step (not gated behind `-v`); `-v` adds
   lower-level HTTP-failure detail on top. `_is_latin_text` guards the
   `era` field specifically, since it's drawn with a Latin-only italic font
   and a raw non-Latin (e.g. Japanese/Chinese/Korean) series name would
   render as tofu boxes. `list_all_sets` (triggered by `--list-sets`)
   concurrently fetches all five language datasets (no early-exit benefit
   to sequential here, since it always needs all of them), merges them by
   id, and prints every set found — a standalone path in `main()` that
   exits before any `--set`/cover-generation logic runs. `lookup_set_info`
   also stashes the raw `cards` list from whichever language's set-detail
   response had it first (by `TCGDEX_FIELD_PRIORITY` order, as `_cards`/
   `_cards_lang`) purely so `--rarity-chart` can reuse it without a second
   round trip.

4. **Rarity chart** (`fetch_rarity_counts`, `_sort_and_abbreviate_rarities`,
   `_abbreviate_rarity`, `RARITY_ORDER`/`RARITY_ABBREVIATIONS`). TCGdex only
   exposes a card's rarity via its individual card endpoint (not the bulk
   set listing), so `fetch_rarity_counts` fires one request per card through
   a `concurrent.futures.ThreadPoolExecutor` (network-bound, so threads are
   fine despite the GIL) and tolerates individual failures (excluded from
   the chart, reported as a count) rather than aborting the whole thing.
   Opt-in via `--rarity-chart` specifically because of that per-card cost —
   never fetched automatically. Rarity names come back as full English
   strings ("Common", "Double Rare"); `RARITY_ABBREVIATIONS` maps well-known
   ones to their standard short code, `_abbreviate_rarity` falls back to
   initials for anything unrecognized, and `_sort_and_abbreviate_rarities`
   orders the result by `RARITY_ORDER` (canonical common→rare tier order,
   not by count) and disambiguates any two distinct rarity names that would
   otherwise collide on the same fallback abbreviation.

5. **`build_cover(cfg)`** — the actual layout engine, driven by a single
   `argparse.Namespace` (`cfg`) of CLI flags. All positions/sizes are
   computed as fractions of `cfg.size` (the canvas is always square) so the
   whole design scales cleanly at any resolution. The key trick is the inner
   `run(y0, render)` closure: it's called once with `render=False` to
   measure the total content height (nothing is drawn, only `y` is
   accumulated), then again with `render=True` at a `start_y` computed to
   vertically center that content inside the panel. Any change to the
   layout (adding a block, changing a font size) must keep the two passes
   consistent — every branch that advances `y` must do so identically in
   both passes, and drawing should only happen inside `if render:` guards.

   Within `run()`, the layout goes: header → era subheading → rule → big
   set-code → set name (+ optional Japanese name) → rule → release
   date / total cards two-column grid → rule → left column (up to 2
   `--stat` rows) alongside a right column that is *either* a QR code block
   (`--qr-url`) *or* a circular completion gauge (`--completion`) *or* a
   rarity distribution chart (`--rarity-chart`) *or* blank — in that
   priority order if more than one is given → rule → footer with Poke Ball
   icon. The `--lang-flag` badge (if given) is drawn separately, straight
   onto the background before `run()` is called — like the accent tab, it
   sits in the fixed margin/inset strip outside the centered content flow,
   so it doesn't participate in the two-pass height measurement.

`main()` parses args, defaults `--out` to `<SET_CODE>_<name>_<EN|JP>_cover.png`
(`JP` if a Japanese name ends up on the cover, `EN` otherwise), and saves the PNG.

## Conventions specific to this codebase

- All user-facing text drawn on the cover is uppercased and rendered with
  `draw_spaced`/`measure_spaced` for consistent letter-spacing — don't use
  plain `draw.text` for label/header/footer strings.
- Sizes, margins, and offsets in `build_cover` are almost all expressed as
  `int(W * <fraction>)` rather than hardcoded pixels, to keep the design
  proportional across `--size` values. Follow this pattern for any new
  layout constants instead of hardcoding pixels at the 3600px scale.
- Fonts are always resolved through `get_font`/`fit_font` by role, never
  loaded directly by path, so the bundled-font / system-fallback / dry-run
  behavior stays consistent.
