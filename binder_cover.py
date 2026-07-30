#!/usr/bin/env python3
"""
Pokémon TCG Binder Cover Generator
-----------------------------------
Generates a print-ready square binder-cover image (Pokedex-style panel,
matching the "M2A Mega Brave" / "M5 Abyss Eye" covers) for any set. Set
details (code, name, Japanese name, era, release date, total cards) are
looked up automatically from TCGdex (api.tcgdex.net) -- you just pass the
set name or code, plus a QR URL if you want one.

Usage examples:

  python binder_cover.py --set M2A --completion 187/187 \\
      --out M2A_Mega_Brave_cover.png

  python binder_cover.py --set "Abyss Eye" \\
      --stat "Main Set" 81 --stat "Secret Rares" 37 \\
      --qr-url "https://rarecandy.com/pokemon/sets/abyss-eye?username=Earlyflash" \\
      --out M5_Abyss_Eye_cover.png

Any looked-up field can be overridden individually (--name, --name-jp,
--era, --release-date, --total-cards, --set-code) if TCGdex has it wrong
or doesn't have the set yet.

Run `python binder_cover.py --help` for the full flag list.

Requires:  pip install pillow
Optional:  pip install "qrcode[pil]"   (for the --qr-url option)
"""
import argparse
import concurrent.futures
import datetime
import http.client
import json
import math
import os
import platform
import sys
import urllib.error
import urllib.request

from PIL import Image, ImageDraw, ImageFont, ImageFilter

TCGDEX_BASE = "https://api.tcgdex.net/v2"
# Search/lookup order: ja and en first since that's the tool's original/most common
# use case (Japan-exclusive and English-market sets), then both Chinese datasets
# (zh-cn/zh-tw are genuinely different TCGdex datasets with different sets, not just
# a script variant of the same one) and Korean.
TCGDEX_LANGS = ("ja", "en", "zh-cn", "zh-tw", "ko")
TCGDEX_LANG_LABELS = {"ja": "Japanese", "en": "English", "zh-cn": "Simplified Chinese",
                       "zh-tw": "Traditional Chinese", "ko": "Korean"}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BUNDLED_FONTS = os.path.join(SCRIPT_DIR, "fonts")

BUNDLED = {
    "black_display": "Lato-Black.ttf",
    "bold_display":  "Lato-Bold.ttf",
    "bold_caps":     "Poppins-Bold.ttf",
    "medium":        "Poppins-Medium.ttf",
    "regular":       "Poppins-Regular.ttf",
    "italic":        "Poppins-Italic.ttf",
}

ROLE_KEYWORDS = {
    "black_display": ["lato-black", "poppins-black", "archivoblack", "anton-regular",
                       "montserrat-black", "ariblk", "arial black", "arialbd", "segoeuib"],
    "bold_display":  ["lato-bold", "poppins-bold", "montserrat-bold", "arialbd",
                       "segoeuib", "dejavusans-bold"],
    "bold_caps":     ["poppins-bold", "montserrat-bold", "arialbd", "segoeuib",
                       "dejavusans-bold"],
    "medium":        ["poppins-medium", "poppins-regular", "montserrat-medium",
                       "segoeui", "arial", "dejavusans"],
    "regular":       ["poppins-regular", "segoeui", "arial", "dejavusans"],
    "italic":        ["poppins-italic", "montserrat-italic", "ariali",
                       "arial italic", "segoeuii", "dejavusans-oblique"],
    "japanese":      ["notosanscjk", "noto sans cjk", "notosansjp", "meiryo",
                       "yugoth", "msgothic", "ms gothic", "hiragino", "msjh"],
}

_FONT_INDEX_CACHE = None


def _candidate_font_dirs(extra_dir=None):
    dirs = []
    if extra_dir:
        dirs.append(extra_dir)
    system = platform.system()
    if system == "Windows":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        dirs.append(os.path.join(windir, "Fonts"))
    elif system == "Darwin":
        dirs += ["/System/Library/Fonts", "/System/Library/Fonts/Supplemental",
                 "/Library/Fonts", os.path.expanduser("~/Library/Fonts")]
    else:
        dirs += ["/usr/share/fonts", "/usr/local/share/fonts",
                 os.path.expanduser("~/.fonts"), os.path.expanduser("~/.local/share/fonts")]
    return [d for d in dirs if d and os.path.isdir(d)]


def _build_font_index(extra_dir=None):
    global _FONT_INDEX_CACHE
    if _FONT_INDEX_CACHE is not None:
        return _FONT_INDEX_CACHE
    index = []
    for d in _candidate_font_dirs(extra_dir):
        for root, _dirs, files in os.walk(d):
            for fn in files:
                if fn.lower().endswith((".ttf", ".otf", ".ttc")):
                    index.append(os.path.join(root, fn))
    _FONT_INDEX_CACHE = index
    return index


def resolve_font_path(role, extra_font_dir=None, verbose=False):
    """Return (path_or_None, is_real_font)."""
    bundled_name = BUNDLED.get(role)
    if bundled_name:
        p = os.path.join(BUNDLED_FONTS, bundled_name)
        if os.path.isfile(p):
            if verbose:
                print(f"[font] {role} -> bundled {bundled_name}")
            return p, True

    idx = _build_font_index(extra_font_dir)
    lower_idx = [(p, os.path.basename(p).lower()) for p in idx]
    avoid_bold = role not in ("black_display", "bold_display", "bold_caps")
    for kw in ROLE_KEYWORDS.get(role, []):
        matches = [(path, base) for path, base in lower_idx if kw in base]
        if avoid_bold:
            non_bold = [m for m in matches if "bold" not in m[1]]
            if non_bold:
                matches = non_bold
        if matches:
            path = matches[0][0]
            if verbose:
                print(f"[font] {role} -> system match {path}")
            return path, True

    if verbose:
        print(f"[font] {role} -> NOT FOUND (falling back to a default bitmap font)")
    return None, False


def get_font(role, size, extra_font_dir=None, verbose=False):
    path, ok = resolve_font_path(role, extra_font_dir, verbose)
    if path:
        try:
            return ImageFont.truetype(path, size, index=0), True
        except Exception:
            pass
    try:
        return ImageFont.load_default(size=size), False
    except TypeError:
        return ImageFont.load_default(), False


# ---------------------------------------------------------------- drawing --

def text_w(font, s):
    b = font.getbbox(s)
    return b[2] - b[0]


def fit_font(role, text, start_size, max_width, min_size, extra_font_dir, spacing=0, verbose=False):
    """Shrink font size until `text` (with optional per-char `spacing`) fits max_width."""
    size = start_size
    while size > min_size:
        font, _ = get_font(role, size, extra_font_dir, verbose=False)
        widths = [text_w(font, c) for c in text]
        total = sum(widths) + spacing * max(0, len(text) - 1)
        if total <= max_width:
            return font
        size -= 4
    font, _ = get_font(role, min_size, extra_font_dir, verbose=False)
    return font


def draw_spaced(draw, cx, y, text, font, fill, spacing):
    widths = [text_w(font, c) if c != " " else font.getbbox("A")[2] * 0.45 for c in text]
    total = sum(widths) + spacing * (len(text) - 1)
    x = cx - total / 2
    for c, w in zip(text, widths):
        draw.text((x, y), c, font=font, fill=fill, anchor="la")
        x += w + spacing
    return total


def measure_spaced(font, text, spacing):
    widths = [text_w(font, c) if c != " " else font.getbbox("A")[2] * 0.45 for c in text]
    return sum(widths) + spacing * (len(text) - 1)


def centered(draw, cx, cy, text, font, fill):
    draw.text((cx, cy), text, font=font, fill=fill, anchor="mm")


def pokeball(draw, cxp, cyp, r, ink, red, bg):
    draw.ellipse([cxp - r, cyp - r, cxp + r, cyp + r], fill=ink)
    draw.pieslice([cxp - r, cyp - r, cxp + r, cyp + r], 180, 360, fill=red)
    band_h = max(3, int(r * 0.16))
    draw.rectangle([cxp - r, cyp - band_h // 2, cxp + r, cyp + band_h // 2], fill=ink)
    inner_r = int(r * 0.34)
    draw.ellipse([cxp - inner_r, cyp - inner_r, cxp + inner_r, cyp + inner_r], fill=ink)
    core_r = int(r * 0.20)
    draw.ellipse([cxp - core_r, cyp - core_r, cxp + core_r, cyp + core_r], fill=bg)


def _star_points(cx, cy, r, rotation=-90, inner_ratio=0.4):
    """Vertices of a 5-pointed star, `rotation` degrees from one point facing east."""
    pts = []
    for i in range(10):
        ang = math.radians(rotation + i * 36)
        rad = r if i % 2 == 0 else r * inner_ratio
        pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    return pts


LANG_FLAG_NAMES = {"en": "English", "jp": "Japanese", "cn": "Chinese", "kr": "Korean"}


def make_flag_badge(w, h, lang):
    """A small stylized national flag for the --lang-flag corner badge.
    Simplified for legibility at icon size (a few dozen px) -- not a
    heraldically exact reproduction of any flag."""
    img = Image.new("RGB", (w, h), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    cx, cy = w / 2, h / 2

    if lang == "jp":
        r = min(w, h) * 0.32
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(188, 0, 45))

    elif lang == "cn":
        draw.rectangle([0, 0, w, h], fill=(222, 41, 16))
        big_r = min(w, h) * 0.19
        draw.polygon(_star_points(w * 0.27, h * 0.3, big_r), fill=(255, 222, 0))
        small_r = big_r * 0.32
        for dx, dy, rot in [(0.46, 0.10, -70), (0.55, 0.23, -35),
                             (0.55, 0.40, 15), (0.47, 0.53, 55)]:
            draw.polygon(_star_points(w * dx, h * dy, small_r, rotation=rot), fill=(255, 222, 0))

    elif lang == "kr":
        r = min(w, h) * 0.28
        red, blue = (205, 46, 44), (0, 43, 127)
        draw.pieslice([cx - r, cy - r, cx + r, cy + r], -45, 135, fill=red)
        draw.pieslice([cx - r, cy - r, cx + r, cy + r], 135, 315, fill=blue)
        dot_r = r * 0.26
        dx = r * 0.5 * math.cos(math.radians(-45))
        dy = r * 0.5 * math.sin(math.radians(-45))
        draw.ellipse([cx + dx - dot_r, cy + dy - dot_r, cx + dx + dot_r, cy + dy + dot_r], fill=blue)
        draw.ellipse([cx - dx - dot_r, cy - dy - dot_r, cx - dx + dot_r, cy - dy + dot_r], fill=red)
        # simplified trigram bars in each corner (not the exact per-corner patterns)
        bar_w, bar_h, bar_gap = w * 0.11, h * 0.03, h * 0.045
        for bx, by, pattern in [(w * 0.14, h * 0.16, (1, 1, 1)), (w * 0.86, h * 0.16, (1, 0, 1)),
                                 (w * 0.14, h * 0.84, (1, 0, 1)), (w * 0.86, h * 0.84, (1, 1, 1))]:
            for i, solid in enumerate(pattern):
                yy = by + (i - 1) * bar_gap
                if solid:
                    draw.rectangle([bx - bar_w / 2, yy - bar_h / 2, bx + bar_w / 2, yy + bar_h / 2],
                                    fill=(20, 20, 20))
                else:
                    gap = bar_w * 0.22
                    draw.rectangle([bx - bar_w / 2, yy - bar_h / 2, bx - gap / 2, yy + bar_h / 2],
                                    fill=(20, 20, 20))
                    draw.rectangle([bx + gap / 2, yy - bar_h / 2, bx + bar_w / 2, yy + bar_h / 2],
                                    fill=(20, 20, 20))

    else:  # "en"
        draw.rectangle([0, 0, w, h], fill=(1, 33, 105))
        diag_w = max(2, int(min(w, h) * 0.16))
        draw.line([(0, 0), (w, h)], fill=(255, 255, 255), width=diag_w)
        draw.line([(0, h), (w, 0)], fill=(255, 255, 255), width=diag_w)
        diag_w2 = max(1, int(diag_w * 0.4))
        draw.line([(0, 0), (w, h)], fill=(200, 16, 46), width=diag_w2)
        draw.line([(0, h), (w, 0)], fill=(200, 16, 46), width=diag_w2)
        cross_w = min(w, h) * 0.32
        draw.rectangle([0, cy - cross_w / 2, w, cy + cross_w / 2], fill=(255, 255, 255))
        draw.rectangle([cx - cross_w / 2, 0, cx + cross_w / 2, h], fill=(255, 255, 255))
        cross_w2 = cross_w * 0.5
        draw.rectangle([0, cy - cross_w2 / 2, w, cy + cross_w2 / 2], fill=(200, 16, 46))
        draw.rectangle([cx - cross_w2 / 2, 0, cx + cross_w2 / 2, h], fill=(200, 16, 46))

    return img


def make_qr_image(url, box_size, verbose=False):
    """Try `qrcode` first, then `reportlab`. Returns a PIL Image or None."""
    try:
        import qrcode
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
        qr.add_data(url)
        qr.make(fit=True)
        im = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        return im.resize((box_size, box_size), Image.LANCZOS)
    except ImportError:
        pass
    except Exception as e:
        if verbose:
            print(f"[qr] qrcode library failed: {e}")

    try:
        from reportlab.graphics.barcode.qr import QrCodeWidget
        from reportlab.graphics.shapes import Drawing
        from reportlab.graphics import renderPM

        qrw = QrCodeWidget(url)
        qrw.barLevel = "M"
        b = qrw.getBounds()
        w, h = b[2] - b[0], b[3] - b[1]
        raw_size = 1000
        d = Drawing(raw_size, raw_size, transform=[raw_size / w, 0, 0, raw_size / h, 0, 0])
        d.add(qrw)
        tmp_path = os.path.join(SCRIPT_DIR, "_qr_tmp.png")
        renderPM.drawToFile(d, tmp_path, fmt="PNG", bg=0xFFFFFF)
        raw = Image.open(tmp_path).convert("RGB")
        margin = int(raw_size * 0.08)
        framed = Image.new("RGB", (raw_size + margin * 2, raw_size + margin * 2), (255, 255, 255))
        framed.paste(raw, (margin, margin))
        try:
            os.remove(tmp_path)
        except OSError:
            pass  # best-effort cleanup; harmless if it lingers
        return framed.resize((box_size, box_size), Image.LANCZOS)
    except ImportError:
        pass
    except Exception as e:
        if verbose:
            print(f"[qr] reportlab fallback failed: {e}")

    print("[warning] Neither 'qrcode' nor 'reportlab' is installed -- skipping the QR "
          "code block. Install one with:  pip install qrcode[pil]")
    return None


# ------------------------------------------------------------------ build --

def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def build_cover(cfg):
    W = H = cfg.size
    margin = int(W * 0.0833)          # 300 / 3600
    radius = int(W * 0.0178)          # 64 / 3600
    inset = int(W * 0.0094)           # 34 / 3600
    pad = int(W * 0.055)              # inner content padding

    BG_COLOR = hex_to_rgb(cfg.bg_color)
    CARD_BG = (240, 233, 217)
    CARD_BORDER = (35, 33, 30)
    INK = (30, 28, 25)
    GRAY = (120, 113, 100)
    LIGHT_RULE = (198, 190, 170)
    RED = hex_to_rgb(cfg.accent)

    px0, py0, px1, py1 = margin, margin, W - margin, H - margin
    content_x0 = px0 + pad
    content_x1 = px1 - pad
    cx = (px0 + px1) // 2
    content_w = content_x1 - content_x0

    # ---- fonts (resolved once so dry-run + real-run stay identical) ----
    fd = cfg.font_dir
    f_header = fit_font("bold_caps", cfg.game_title.upper(), 92, content_w, 34, fd, spacing=14)
    f_era = None
    if cfg.era:
        f_era = fit_font("italic", cfg.era.upper(), 60, content_w, 26, fd, spacing=10)
    f_setcode = fit_font("black_display", cfg.set_code.upper(), 610, content_w, 160, fd, spacing=0)
    f_name = fit_font("black_display", cfg.name.upper(), 198, content_w, 60, fd, spacing=10)
    f_jp, jp_ok = (None, False)
    if cfg.name_jp:
        _, jp_ok = get_font("japanese", 116, fd, verbose=cfg.verbose)
        if jp_ok:
            f_jp = fit_font("japanese", cfg.name_jp, 116, content_w, 40, fd)
        else:
            print("[warning] No Japanese-capable font found on this system -- the "
                  "Japanese set name will be skipped. Install a CJK font (Windows/Mac "
                  "already ship one; on Linux try 'fonts-noto-cjk') or pass --font-dir.")
    lbl_font = get_font("medium", 50, fd)[0]
    val_font = fit_font("bold_display", cfg.release_date.upper(), 102, content_w * 0.42, 40, fd)
    val_font_cards = fit_font("bold_display", str(cfg.total_cards), 102, content_w * 0.42, 40, fd)
    lbl2_font = get_font("medium", 47, fd)[0]
    val2_font = get_font("bold_display", 87, fd)[0]
    footer_font = get_font("bold_caps", 71, fd)[0]
    qr_caption_font = get_font("bold_caps", 50, fd)[0]
    qr_subcaption_font = get_font("medium", 34, fd)[0]
    gauge_pct_font = get_font("black_display", 145, fd)[0]
    gauge_caption_font = get_font("medium", 40, fd)[0]
    rarity_caption_font = get_font("medium", 50, fd)[0]

    stats = cfg.stats[:2]

    # completion parsing
    completion = None
    if cfg.qr_url and cfg.completion:
        print("[note] Both --qr-url and --completion were given; showing the QR code "
              "and ignoring --completion.")
    if cfg.completion and not cfg.qr_url:
        raw = cfg.completion.replace(" ", "")
        if "/" in raw:
            collected, total = raw.split("/", 1)
            try:
                pct = round(100 * float(collected) / float(total))
            except (ValueError, ZeroDivisionError):
                pct = 0
            completion = {"collected": collected, "total": total, "pct": pct}
        else:
            try:
                pct = round(float(raw.rstrip("%")))
            except ValueError:
                pct = 0
            completion = {"collected": None, "total": None, "pct": pct}

    qr_img = None
    qr_size = int(W * 0.1194)  # 430/3600
    if cfg.qr_url:
        qr_img = make_qr_image(cfg.qr_url, qr_size, verbose=cfg.verbose)

    # Rarity chart rows are sized to fit within the same total height as the QR code
    # box, however many rarity tiers there are -- so it never pushes the rule below
    # it further down than a QR code or completion gauge would.
    rarity_chart_h = qr_size + 2 * int(qr_size * 0.047)
    num_rarity_rows = len(cfg.rarity_counts)
    rarity_row_h = rarity_chart_h / num_rarity_rows if num_rarity_rows else rarity_chart_h
    rarity_label_font = get_font("bold_caps", int(min(38, max(16, rarity_row_h * 0.5))), fd)[0]
    rarity_count_font = get_font("medium", int(min(34, max(14, rarity_row_h * 0.45))), fd)[0]

    # ---------------------------------------------------------- background --
    img = Image.new("RGB", (W, H), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # drop shadow + panel
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    off = int(W * 0.0139)
    sd.rounded_rectangle([px0, py0 + off, px1, py1 + off], radius=radius, fill=(0, 0, 0, 160))
    shadow = shadow.filter(ImageFilter.GaussianBlur(int(W * 0.0167)))
    img = Image.alpha_composite(img.convert("RGBA"), shadow).convert("RGB")
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle([px0, py0, px1, py1], radius=radius, fill=CARD_BG)
    draw.rounded_rectangle([px0, py0, px1, py1], radius=radius, outline=CARD_BORDER, width=6)
    draw.rounded_rectangle([px0 + inset, py0 + inset, px1 - inset, py1 - inset],
                            radius=max(1, radius - 24), outline=(180, 171, 150), width=3)

    if cfg.accent_tab:
        tab_w = int(W * 0.0056)
        tab_y0 = py0 + int(H * 0.0833)
        tab_y1 = py0 + int(H * 0.2111)
        draw.rectangle([px1 - inset - tab_w, tab_y0, px1 - inset, tab_y1], fill=RED)

    if cfg.lang_flag:
        badge_w = int(W * 0.06)
        badge_h = int(badge_w * 0.66)
        badge_x0 = px0 + inset + int(W * 0.018)
        badge_y0 = py0 + inset + int(H * 0.018)
        badge = make_flag_badge(badge_w, badge_h, cfg.lang_flag)
        img.paste(badge, (badge_x0, badge_y0))
        draw.rectangle([badge_x0, badge_y0, badge_x0 + badge_w, badge_y0 + badge_h],
                        outline=CARD_BORDER, width=max(2, int(min(badge_w, badge_h) * 0.05)))

    # ------------------------------------------------------- layout blocks --
    # Two-pass: pass 1 (render=False) only measures the total content height so
    # we can center everything vertically inside the panel; pass 2 draws it.

    def run(y0, render):
        y = y0

        if render:
            draw_spaced(draw, cx, y, cfg.game_title.upper(), f_header, INK, 14)
        y += int(f_header.size * 1.43)

        if f_era:
            if render:
                draw_spaced(draw, cx, y, cfg.era.upper(), f_era, GRAY, 10)
            y += int(f_era.size * 2.2)

        if render:
            draw.line([(content_x0, y), (content_x1, y)], fill=LIGHT_RULE, width=3)
        y += int(W * 0.036)

        if render:
            centered(draw, cx, y + int(f_setcode.size * 0.40), cfg.set_code.upper(), f_setcode, INK)
        y += int(f_setcode.size * 0.92)

        if render:
            draw_spaced(draw, cx, y, cfg.name.upper(), f_name, RED, 10)
        y += int(f_name.size * 1.26)

        if cfg.name_jp and jp_ok:
            if render:
                centered(draw, cx, y + int(f_jp.size * 0.40), cfg.name_jp, f_jp, (90, 84, 74))
            y += int(f_jp.size * 1.28)

        if render:
            draw.line([(content_x0, y), (content_x1, y)], fill=LIGHT_RULE, width=3)
        y += int(W * 0.029)

        grid_top = y
        row1_val_y = grid_top + int(lbl_font.size * 2.36)

        c1x = content_x0 + (cx - content_x0) * 0.5
        c2x = cx + (content_x1 - cx) * 0.5

        if render:
            draw_spaced(draw, c1x, grid_top, "RELEASE DATE", lbl_font, GRAY, 5)
            draw_spaced(draw, c2x, grid_top, "TOTAL CARDS", lbl_font, GRAY, 5)
            centered(draw, c1x, row1_val_y + val_font.size * 0.57, cfg.release_date.upper(), val_font, INK)
            centered(draw, c2x, row1_val_y + val_font_cards.size * 0.57, str(cfg.total_cards), val_font_cards, INK)

        row1_bottom = row1_val_y + int(max(val_font.size, val_font_cards.size) * 1.42)
        if render:
            draw.line([(cx, grid_top - 10), (cx, row1_bottom)], fill=LIGHT_RULE, width=3)
            draw.line([(content_x0, row1_bottom), (content_x1, row1_bottom)], fill=LIGHT_RULE, width=3)

        y = row1_bottom + int(W * 0.0256)
        row2_top = y

        # ---- left column: up to 2 stats ----
        # Label/value text is fully user-controlled (--stat), so it's fit to the
        # available column width just like every other headline text -- lbl2_font/
        # val2_font stay the sizing basis for vertical rhythm either way.
        stat_max_w = cx - content_x0 - int(W * 0.02)
        left_bottom = row2_top
        if len(stats) == 1:
            label, value = stats[0]
            mid = row2_top + int(W * 0.075)
            f_lbl = fit_font("medium", label.upper(), lbl2_font.size, stat_max_w, 20, fd)
            f_val = fit_font("bold_display", str(value), val2_font.size, stat_max_w, 30, fd)
            if render:
                draw.text((content_x0, mid - lbl2_font.size - 10), label.upper(), font=f_lbl, fill=GRAY, anchor="la")
                draw.text((content_x0, mid), str(value), font=f_val, fill=INK, anchor="la")
            left_bottom = mid + int(val2_font.size * 1.3)
        elif len(stats) == 2:
            (l1, v1), (l2, v2) = stats
            f_lbl1 = fit_font("medium", l1.upper(), lbl2_font.size, stat_max_w, 20, fd)
            f_val1 = fit_font("bold_display", str(v1), val2_font.size, stat_max_w, 30, fd)
            f_lbl2 = fit_font("medium", l2.upper(), lbl2_font.size, stat_max_w, 20, fd)
            f_val2 = fit_font("bold_display", str(v2), val2_font.size, stat_max_w, 30, fd)
            if render:
                draw.text((content_x0, row2_top), l1.upper(), font=f_lbl1, fill=GRAY, anchor="la")
                draw.text((content_x0, row2_top + int(lbl2_font.size * 1.96)), str(v1), font=f_val1, fill=INK, anchor="la")
                second_y = row2_top + int(W * 0.0806)
                draw.text((content_x0, second_y), l2.upper(), font=f_lbl2, fill=GRAY, anchor="la")
                draw.text((content_x0, second_y + int(lbl2_font.size * 1.96)), str(v2), font=f_val2, fill=INK, anchor="la")
            left_bottom = row2_top + int(W * 0.0806) + int(lbl2_font.size * 1.96) + int(val2_font.size * 1.3)

        # ---- right column: QR / gauge / blank ----
        gcx = cx + (content_x1 - cx) * 0.62
        right_bottom = row2_top

        qr_col_max_w = content_x1 - cx
        if qr_img is not None:
            qr_caption_text = cfg.qr_caption.upper()
            f_qr_caption = fit_font("bold_caps", qr_caption_text, qr_caption_font.size,
                                     qr_col_max_w, 26, fd, spacing=6)
            if render:
                draw_spaced(draw, gcx, row2_top, qr_caption_text, f_qr_caption, INK, 6)
            box_top = row2_top + int(qr_caption_font.size * 2.1)
            qr_pad = int(qr_size * 0.047)
            box_half = qr_size / 2 + qr_pad
            if render:
                box = [gcx - box_half, box_top, gcx + box_half, box_top + qr_size + 2 * qr_pad]
                draw.rounded_rectangle(box, radius=20, fill=(255, 255, 255), outline=CARD_BORDER, width=5)
                img.paste(qr_img, (int(gcx - qr_size / 2), int(box_top + qr_pad)))
            right_bottom = box_top + qr_size + 2 * qr_pad
            if cfg.qr_subcaption:
                qr_subcaption_text = cfg.qr_subcaption.upper()
                f_qr_subcaption = fit_font("medium", qr_subcaption_text, qr_subcaption_font.size,
                                            qr_col_max_w, 18, fd, spacing=6)
                sub_y = right_bottom + int(W * 0.0111)
                if render:
                    draw_spaced(draw, gcx, sub_y, qr_subcaption_text, f_qr_subcaption, GRAY, 6)
                right_bottom = sub_y + int(qr_subcaption_font.size * 1.3)

        elif completion is not None:
            gauge_r = int(W * 0.0697)
            gcy = row2_top + gauge_r + int(W * 0.011)
            bbox = [gcx - gauge_r, gcy - gauge_r, gcx + gauge_r, gcy + gauge_r]
            thickness = int(gauge_r * 0.18)
            pct = max(0, min(100, completion["pct"]))
            if render:
                draw.arc(bbox, 0, 360, fill=(222, 213, 193), width=thickness)
                draw.arc(bbox, -90, -90 + 360 * (pct / 100.0), fill=RED, width=thickness)
                centered(draw, gcx, gcy - gauge_pct_font.size * 0.28, f"{pct}%", gauge_pct_font, INK)
                draw_spaced(draw, gcx, gcy + gauge_pct_font.size * 0.5, "COMPLETE", gauge_caption_font, GRAY, 6)
            right_bottom = gcy + gauge_r
            if completion["collected"] is not None:
                cap_y = right_bottom + int(W * 0.02)
                if render:
                    centered(draw, gcx, cap_y, f"{completion['collected']} / {completion['total']}", val2_font, INK)
                right_bottom = cap_y + int(val2_font.size * 0.8)

        elif cfg.rarity_counts:
            if render:
                draw_spaced(draw, gcx, row2_top, "RARITY BREAKDOWN", rarity_caption_font, GRAY, 5)
            rows_top = row2_top + int(rarity_caption_font.size * 2.1)

            block_half_w = int(W * 0.165)
            max_label_w = max(text_w(rarity_label_font, label) for label, _ in cfg.rarity_counts)
            count_col_w = int(W * 0.035)
            gap = int(W * 0.012)
            label_x1 = gcx - block_half_w + max_label_w
            bar_x0 = label_x1 + gap
            bar_x1 = gcx + block_half_w - count_col_w - gap
            count_x0 = bar_x1 + gap
            bar_max_w = max(1, bar_x1 - bar_x0)

            bar_h = max(3, int(rarity_row_h * 0.4))
            max_count = max(c for _, c in cfg.rarity_counts)

            if render:
                row_y = rows_top
                for label, count in cfg.rarity_counts:
                    draw.text((label_x1, row_y), label, font=rarity_label_font, fill=INK, anchor="ra")
                    bar_w = max(6, int(bar_max_w * count / max_count))
                    bar_top = row_y + (rarity_row_h - bar_h) / 2
                    draw.rounded_rectangle(
                        [bar_x0, bar_top, bar_x0 + bar_w, bar_top + bar_h],
                        radius=bar_h // 2, fill=RED)
                    draw.text((count_x0, row_y), str(count), font=rarity_count_font, fill=GRAY, anchor="la")
                    row_y += rarity_row_h
            # Fixed regardless of row count, so this block is always exactly as tall
            # as the QR code box -- see rarity_chart_h above.
            right_bottom = rows_top + rarity_chart_h

        y = max(left_bottom, right_bottom) + int(W * 0.0125)
        if render:
            draw.line([(content_x0, y), (content_x1, y)], fill=LIGHT_RULE, width=3)
        y += int(W * 0.026)

        # ---- footer ----
        if cfg.footer:
            label_text = cfg.footer.upper()
            f_footer = fit_font("bold_caps", label_text, footer_font.size, content_w * 0.82, 30, fd, spacing=12)
            lbl_w = measure_spaced(f_footer, label_text, 12)
            ball_r = int(W * 0.0125)
            gap = int(W * 0.0111)
            total_w = ball_r * 2 + gap + lbl_w
            start_x = cx - total_w / 2
            ball_cx = start_x + ball_r
            ball_cy = y + f_footer.size * 0.5
            if render:
                pokeball(draw, int(ball_cx), int(ball_cy), ball_r, INK, RED, CARD_BG)
                draw_spaced(draw, start_x + ball_r * 2 + gap + lbl_w / 2, y, label_text, f_footer, INK, 12)
            y += int(f_footer.size * 1.3)

        return y

    panel_h = py1 - py0
    total_h = run(0, render=False)
    start_y = py0 + max(int(H * 0.02), (panel_h - total_h) // 2)
    run(start_y, render=True)

    return img


# ---------------------------------------------------------- set lookup --

def _fetch_json(url, verbose=False):
    req = urllib.request.Request(url, headers={"User-Agent": "pokemon-binder-cover-tool/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    # OSError covers urllib.error.URLError (a subclass) as well as raw socket-level
    # failures (timeouts, connection resets) that urlopen doesn't always wrap in
    # URLError; http.client.HTTPException/UnicodeDecodeError cover malformed or
    # truncated responses. Runs inside ThreadPoolExecutor workers elsewhere in this
    # file, so anything not caught here surfaces as a raw crash via future.result()
    # instead of the graceful "[warning] ..." degradation the rest of the tool has.
    except (OSError, http.client.HTTPException, json.JSONDecodeError, UnicodeDecodeError) as e:
        if verbose:
            print(f"[lookup] GET {url} failed: {e}")
        return None


def _is_latin_text(s):
    """The era subheading is drawn with a Latin-only italic font, so a non-Latin
    (e.g. Japanese) series name would render as tofu boxes -- only auto-fill --era
    when the source text is actually renderable by that font."""
    try:
        s.encode("latin-1")
        return True
    except UnicodeEncodeError:
        return False


def _format_release_date(raw):
    raw = str(raw)
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            dt = datetime.datetime.strptime(raw, fmt)
            return f"{dt.day} {dt.strftime('%b').upper()} {dt.year}"
        except ValueError:
            continue
    return raw.upper()


def _find_set_id(query, verbose=False):
    """Search TCGdex (in TCGDEX_LANGS priority order) for a set matching `query` by
    id or by name. Returns the matched set id, or None."""
    needle = query.strip().lower()
    for lang in TCGDEX_LANGS:
        print(f"[lookup] Checking TCGdex's {lang} set list for \"{query}\"...")
        listing = _fetch_json(f"{TCGDEX_BASE}/{lang}/sets", verbose)
        if not listing:
            print(f"[lookup] Couldn't reach/parse TCGdex's {lang} set list.")
            continue
        print(f"[lookup] Got {len(listing)} sets from the {lang} dataset.")
        for s in listing:
            if str(s.get("id", "")).lower() == needle:
                print(f"[lookup] Matched by set code in the {lang} dataset: {s['id']}")
                return s["id"]
        matches = [s for s in listing if needle in str(s.get("name", "")).lower()]
        if len(matches) == 1:
            print(f"[lookup] Matched by name in the {lang} dataset: "
                  f"{matches[0]['id']} ({matches[0]['name']})")
            return matches[0]["id"]
        if len(matches) > 1:
            shown = ", ".join(f'{m["id"]} ({m["name"]})' for m in matches[:8])
            print(f"[warning] Multiple TCGdex sets ({lang}) match \"{query}\": {shown}. "
                  "Try a more specific --set value, or fill in the gaps with --set-code/"
                  "--name/etc.")
            # Ambiguous in this language doesn't mean ambiguous everywhere -- keep
            # checking the remaining languages for an exact/unique match instead of
            # giving up here.
    return None


# Field-extraction preference once a set is matched: English data is the most
# standardized/reliable when available, so it's preferred over the others -- distinct
# from TCGDEX_LANGS, which is the *search* order (Japanese-first, to avoid an
# ambiguous English-name match shadowing a Japan-exclusive set).
TCGDEX_FIELD_PRIORITY = ("en", "ja", "zh-cn", "zh-tw", "ko")


def _first_field(detail, field, langs=TCGDEX_FIELD_PRIORITY):
    """First truthy `field` found across `detail` (a {lang: set-detail dict}), in
    `langs` priority order."""
    for lang in langs:
        value = detail.get(lang, {}).get(field)
        if value:
            return value
    return None


def lookup_set_info(query, verbose=False):
    """Look up set metadata on TCGdex (api.tcgdex.net) by set code or name.
    Returns a dict with whatever of set_code/name/name_jp/era/release_date/
    total_cards it could find -- callers should treat this as defaults only,
    since CLI flags may still override individual fields."""
    print(f"[lookup] Looking up \"{query}\" on TCGdex (api.tcgdex.net)...")
    info = {}
    set_id = _find_set_id(query, verbose)
    if not set_id:
        print(f"[warning] Could not find a TCGdex set matching \"{query}\". If this is a "
              "set that hasn't released in English yet (Japan/China/Korea-exclusive), "
              "TCGdex only has its local name -- search by the set CODE instead (e.g. "
              "\"M5\"), not an English fan translation. Otherwise it may just be too new "
              "to be indexed yet. Either way, fill in the gaps with --set-code/--name/"
              "--release-date/--total-cards/etc.")
        return info

    print(f"[lookup] Fetching detail for set '{set_id}' across TCGdex's "
          f"{', '.join(TCGDEX_LANGS)} datasets...")
    detail = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(TCGDEX_LANGS)) as pool:
        futures = {pool.submit(_fetch_json, f"{TCGDEX_BASE}/{lang}/sets/{set_id}", verbose): lang
                   for lang in TCGDEX_LANGS}
        for future in concurrent.futures.as_completed(futures):
            detail[futures[future]] = future.result() or {}

    en = detail.get("en", {})
    ja = detail.get("ja", {})
    found_in = [TCGDEX_LANG_LABELS[lang] for lang in TCGDEX_LANGS if detail.get(lang)]
    if en:
        print("[lookup] English detail found.")
    else:
        print(f"[lookup] No English-dataset entry for '{set_id}' (expected for a set "
              "that hasn't released in English yet).")
    print(f"[lookup] Detail found in: {', '.join(found_in) if found_in else '(none)'}")

    info["set_code"] = str(_first_field(detail, "id") or set_id).upper()
    if en.get("name"):
        info["name"] = en["name"]
    jp_name = ja.get("name")
    if jp_name and jp_name != info.get("name"):
        info["name_jp"] = jp_name
    if "name" not in info:
        # No English release on TCGdex yet -- flag whichever local name we did find, so
        # the caller can decide whether it's actually a problem (i.e. the user didn't
        # already supply --name themselves).
        for lang in TCGDEX_FIELD_PRIORITY[1:]:  # skip "en", already known absent here
            local_name = detail.get(lang, {}).get("name")
            if local_name:
                info["_name_unavailable_local"] = (TCGDEX_LANG_LABELS[lang], local_name)
                break

    # Stashed for --rarity-chart, which needs the set's card list to fetch rarity
    # per-card -- reusing what we already fetched here instead of a redundant round trip.
    for lang in TCGDEX_FIELD_PRIORITY:
        if detail.get(lang, {}).get("cards"):
            info["_cards"], info["_cards_lang"] = detail[lang]["cards"], lang
            break

    serie = _first_field(detail, "serie") or {}
    if isinstance(serie, dict) and serie.get("name") and _is_latin_text(serie["name"]):
        info["era"] = serie["name"]

    release_raw = _first_field(detail, "releaseDate")
    if release_raw:
        info["release_date"] = _format_release_date(release_raw)

    card_count = _first_field(detail, "cardCount")
    if isinstance(card_count, dict):
        # `total` includes secret rares/alt arts; `official` is just the printed/main-set
        # count -- --total-cards on this tool has always meant the grand total (e.g. 118
        # for M5: 81 main-set + 37 secret rares), so prefer `total` over `official`.
        total = card_count.get("total") or card_count.get("official")
        official = card_count.get("official")
        if total:
            info["total_cards"] = total
        if official and total and total > official:
            info["stats"] = [("Main Set", official), ("Secret Rares", total - official)]

    summary = [f"set code {info['set_code']}"]
    summary.append(f"name {info['name']!r}" if info.get("name") else "no English name")
    if info.get("name_jp"):
        summary.append(f"Japanese name {info['name_jp']!r}")
    if info.get("release_date"):
        summary.append(f"release date {info['release_date']}")
    if info.get("total_cards"):
        card_txt = f"{info['total_cards']} total cards"
        if info.get("stats"):
            card_txt += f" ({info['stats'][0][1]} main set + {info['stats'][1][1]} secret rares)"
        summary.append(card_txt)
    if info.get("era"):
        summary.append(f"era {info['era']!r}")
    print(f"[lookup] Found: {', '.join(summary)}.")

    return info


def list_all_sets(verbose=False):
    """Print every set TCGdex knows about (id + name in whichever of TCGDEX_LANGS have
    it), merged across all datasets and sorted by id. Used by --list-sets. Fetched
    concurrently since (unlike a single lookup) every dataset is needed regardless."""
    print(f"[lookup] Fetching set lists from TCGdex's {', '.join(TCGDEX_LANGS)} "
          "datasets...")
    listings = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(TCGDEX_LANGS)) as pool:
        futures = {pool.submit(_fetch_json, f"{TCGDEX_BASE}/{lang}/sets", verbose): lang
                   for lang in TCGDEX_LANGS}
        for future in concurrent.futures.as_completed(futures):
            lang = futures[future]
            listing = future.result()
            if listing:
                print(f"[lookup] Got {len(listing)} sets from the {lang} dataset.")
                listings[lang] = listing
            else:
                print(f"[warning] Couldn't fetch TCGdex's {lang} set list.")

    combined = {}
    for lang in TCGDEX_LANGS:
        for s in listings.get(lang, []):
            sid = s.get("id")
            if not sid:
                continue
            combined.setdefault(sid, {})[lang] = s.get("name", "")

    if not combined:
        print("[warning] Couldn't reach TCGdex at all -- check your internet connection.")
        return

    print(f"\n{len(combined)} sets total:\n")
    for sid in sorted(combined, key=str.lower):
        names = combined[sid]
        shown = " / ".join(names[lang] for lang in TCGDEX_LANGS if names.get(lang))
        print(f"{sid:<10} {shown or '(no name)'}")


# Canonical low-to-high rarity order, and their standard short codes. Rarity names
# not in this table (newer terminology TCGdex hasn't been mapped for yet) fall back
# to an initials-based abbreviation in _abbreviate_rarity -- best-effort, since there's
# no API field for the "official" short code, only the full English name.
RARITY_ORDER = [
    "common", "uncommon", "rare", "rare holo", "double rare", "art rare",
    "super rare", "shiny rare", "shiny ultra rare", "illustration rare",
    "special illustration rare", "special art rare", "ultra rare", "hyper rare",
    "radiant rare", "amazing rare", "rare ace", "ace spec rare", "rare prime",
    "rare break", "promo",
]
RARITY_ABBREVIATIONS = {
    "common": "C", "uncommon": "U", "rare": "R", "rare holo": "RH",
    "double rare": "RR", "art rare": "AR", "super rare": "SR",
    "illustration rare": "IR", "special illustration rare": "SAR",
    "special art rare": "SAR", "shiny rare": "S", "shiny ultra rare": "SUR",
    "ultra rare": "UR", "hyper rare": "HR", "radiant rare": "RA",
    "amazing rare": "AMZ", "rare ace": "ACE", "ace spec rare": "ACE",
    "rare prime": "PRIME", "rare break": "BREAK", "promo": "PR",
}


def _abbreviate_rarity(name):
    key = name.strip().lower()
    if key in RARITY_ABBREVIATIONS:
        return RARITY_ABBREVIATIONS[key]
    words = [w for w in name.replace("-", " ").split() if w]
    return "".join(w[0].upper() for w in words) if words else "?"


def _sort_and_abbreviate_rarities(counts):
    """counts: {full rarity name: count}. Returns [(short_label, count), ...] in
    canonical low-to-high rarity order, unrecognized rarities sorted alphabetically
    after the recognized ones."""
    def sort_key(name):
        key = name.strip().lower()
        try:
            return (0, RARITY_ORDER.index(key))
        except ValueError:
            return (1, key)

    used = {}
    result = []
    for name in sorted(counts, key=sort_key):
        label = _abbreviate_rarity(name)
        # Guard against two distinct rarity names abbreviating to the same label
        # (most likely for unrecognized/newer terminology) -- would otherwise show
        # as one misleadingly-merged bar.
        if label in used and used[label] != name:
            n = 2
            while f"{label}{n}" in used:
                n += 1
            label = f"{label}{n}"
        used[label] = name
        result.append((label, counts[name]))
    return result


def fetch_rarity_counts(cards, lang, verbose=False):
    """Fetch each card's rarity (one API call per card -- TCGdex doesn't expose it in
    the bulk set listing) and return [(short_label, count), ...] for --rarity-chart.
    Runs concurrently since a full set can be 100-200+ cards; failed/unreachable
    cards are silently excluded (reported as a count) rather than aborting."""
    ids = [c["id"] for c in cards if c.get("id")]
    if not ids:
        return []

    print(f"[rarity] Fetching rarity for {len(ids)} cards from TCGdex "
          "(this can take a little while)...")

    def fetch_one(card_id):
        detail = _fetch_json(f"{TCGDEX_BASE}/{lang}/cards/{card_id}", verbose)
        return detail.get("rarity") if detail else None

    rarities = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(fetch_one, cid) for cid in ids]
        for done, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            rarities.append(future.result())
            if done % 20 == 0 or done == len(ids):
                print(f"[rarity] ...{done}/{len(ids)} cards checked")

    failed = sum(1 for r in rarities if not r)
    if failed:
        print(f"[warning] Couldn't determine rarity for {failed}/{len(ids)} cards -- "
              "excluded from the chart.")

    counts = {}
    for r in rarities:
        if r:
            counts[r] = counts.get(r, 0) + 1

    result = _sort_and_abbreviate_rarities(counts)
    print(f"[rarity] Breakdown: {', '.join(f'{label} x{count}' for label, count in result)}")
    return result


# --------------------------------------------------------------------- CLI --

def build_arg_parser():
    p = argparse.ArgumentParser(
        description="Generate a print-ready Pokémon TCG binder cover (Pokedex-style panel).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--set", dest="set_query", default=None,
                    help='Set to look up on TCGdex, by code or name, e.g. "M5" or "Abyss Eye". '
                         "Required unless --list-sets is given.")
    p.add_argument("--list-sets", action="store_true",
                    help="List every set code/name TCGdex knows about, then exit without "
                         "generating a cover")
    p.add_argument("--qr-url", default="", help="If set, shows a QR code linking here instead of a completion gauge")

    p.add_argument("--set-code", default=None, help='Override the looked-up set code, e.g. "M5"')
    p.add_argument("--name", default=None, help='Override the looked-up English set name, e.g. "Abyss Eye"')
    p.add_argument("--name-jp", default=None, help='Override the looked-up Japanese set name, e.g. "\u30a2\u30d3\u30b9\u30a2\u30a4"')
    p.add_argument("--game-title", default="Pokémon Card Game")
    p.add_argument("--era", default=None, help="Override the looked-up era/series subheading, "
                                                 'e.g. "Mega Series" or "Scarlet & Violet Era"')
    p.add_argument("--release-date", default=None, help='Override the looked-up release date, e.g. "22 MAY 2026"')
    p.add_argument("--total-cards", default=None, help="Override the looked-up total card count")
    p.add_argument("--stat", nargs=2, action="append", default=[], metavar=("LABEL", "VALUE"),
                    help="Up to two stat rows, e.g. --stat \"Secret Rares\" 37 (repeatable, max 2) -- "
                         "overrides the looked-up Main Set / Secret Rares rows if you pass this yourself")
    p.add_argument("--qr-caption", default="Scan To Track")
    p.add_argument("--qr-subcaption", default="")
    p.add_argument("--completion", default="", help='"collected/total" e.g. "187/187", or a bare percent like "100". '
                                                       "Ignored if --qr-url is given.")
    p.add_argument("--rarity-chart", action="store_true",
                    help="Show a Common/Uncommon/Rare/etc. distribution chart in place of the "
                         "QR code or completion gauge (only shown if neither is given). Fetches "
                         "every card's rarity from TCGdex individually, so this is slower -- "
                         "opt-in rather than automatic.")
    p.add_argument("--footer", default=None,
                    help="Bottom banner text next to the Poke Ball icon. Defaults to "
                         "\"<Language> Master Set\", where <Language> comes from --lang-flag "
                         "if given, otherwise Japanese if the cover shows a Japanese name and "
                         "English if not.")
    p.add_argument("--accent", default="C42A22", help="Hex accent color (no #), default a Pokémon red")
    p.add_argument("--bg-color", default="FFFFFF", help="Hex color (no #) for the area outside the panel, "
                                                          "default white -- keeps printing ink-cheap")
    p.add_argument("--accent-tab", dest="accent_tab", action=argparse.BooleanOptionalAction,
                    default=True, help="Show/hide the small red accent tab on the right edge")
    p.add_argument("--lang-flag", dest="lang_flag", default=None, type=str.lower,
                    choices=sorted(LANG_FLAG_NAMES), metavar="{en,jp,cn,kr}",
                    help="Show a small language flag badge in the top-left corner: en (English), "
                         "jp (Japanese), cn (Chinese), or kr (Korean). Off by default; purely a "
                         "cosmetic label you choose yourself, independent of --name/--name-jp.")
    p.add_argument("--size", type=int, default=3600, help="Canvas size in pixels (square). Default 3600 (=12in @300dpi).")
    p.add_argument("--font-dir", default=None, help="Extra directory to search for fonts first")
    p.add_argument("-o", "--out", default=None,
                    help="Output PNG path (default: <SET_CODE>_<name>_<EN|JP>_cover.png -- "
                         "JP if a Japanese name is shown on the cover, EN otherwise)")
    p.add_argument("-v", "--verbose", action="store_true", help="Print which fonts were resolved")
    return p


def main(argv=None):
    try:
        # Lookup results can contain Japanese text (set/era names); some Windows consoles
        # use a legacy codepage (e.g. cp1252) that can't encode it and would otherwise
        # crash on print() instead of just printing '?' for the unsupported characters.
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.list_sets:
        list_all_sets(verbose=args.verbose)
        return
    if not args.set_query:
        parser.error("--set is required (or use --list-sets to browse available sets)")

    args.stats = [tuple(s) for s in args.stat]

    had_name = args.name is not None
    looked_up = lookup_set_info(args.set_query, verbose=args.verbose)
    for field in ("set_code", "name", "name_jp", "era", "release_date", "total_cards"):
        if getattr(args, field) is None:
            setattr(args, field, looked_up.get(field))
    if not args.stats and looked_up.get("stats"):
        args.stats = looked_up["stats"]
    args.name_jp = args.name_jp or ""
    args.era = args.era or ""
    lang_tag = "JP" if args.name_jp else "EN"

    if args.footer is None:
        if args.lang_flag:
            args.footer = f"{LANG_FLAG_NAMES[args.lang_flag]} Master Set"
        else:
            args.footer = "Japanese Master Set" if lang_tag == "JP" else "English Master Set"

    if not had_name and looked_up.get("_name_unavailable_local"):
        lang_label, local_name = looked_up["_name_unavailable_local"]
        print(f"[note] '{args.set_query}' has no English release on TCGdex yet -- only "
              f"its {lang_label} name ({local_name}) is available. "
              f"Pass --name yourself, e.g. --name \"{args.set_query}\", for the English "
              "display text.")

    missing = [f for f in ("set_code", "name", "release_date", "total_cards") if not getattr(args, f)]
    if missing:
        flags = ", ".join(f"--{f.replace('_', '-')}" for f in missing)
        parser.error(f"couldn't determine {flags} from --set \"{args.set_query}\" -- "
                     f"pass them directly to fill in the gaps")
    args.total_cards = str(args.total_cards)

    args.rarity_counts = []
    if args.rarity_chart:
        cards = looked_up.get("_cards")
        if not cards:
            print("[warning] --rarity-chart needs a successful --set lookup with a card "
                  "list, which isn't available here -- skipping the rarity chart.")
        else:
            args.rarity_counts = fetch_rarity_counts(
                cards, looked_up["_cards_lang"], verbose=args.verbose)

    if not args.out:
        safe_name = "".join(c if c.isalnum() else "_" for c in args.name).strip("_")
        args.out = f"{args.set_code}_{safe_name}_{lang_tag}_cover.png"

    img = build_cover(args)
    img.save(args.out, "PNG")
    print(f"Saved {args.out}  ({args.size}x{args.size}px)")


if __name__ == "__main__":
    sys.exit(main())
