#!/usr/bin/env python3
"""
Pokemon TCG Binder Cover Generator
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
import datetime
import json
import os
import platform
import sys
import urllib.error
import urllib.request

from PIL import Image, ImageDraw, ImageFont, ImageFilter

TCGDEX_BASE = "https://api.tcgdex.net/v2"

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
        f_jp, jp_ok = get_font("japanese", 116, fd, verbose=cfg.verbose)
        if not jp_ok:
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
        left_bottom = row2_top
        if len(stats) == 1:
            label, value = stats[0]
            mid = row2_top + int(W * 0.075)
            if render:
                draw.text((content_x0, mid - lbl2_font.size - 10), label.upper(), font=lbl2_font, fill=GRAY, anchor="la")
                draw.text((content_x0, mid), str(value), font=val2_font, fill=INK, anchor="la")
            left_bottom = mid + int(val2_font.size * 1.3)
        elif len(stats) == 2:
            (l1, v1), (l2, v2) = stats
            if render:
                draw.text((content_x0, row2_top), l1.upper(), font=lbl2_font, fill=GRAY, anchor="la")
                draw.text((content_x0, row2_top + int(lbl2_font.size * 1.96)), str(v1), font=val2_font, fill=INK, anchor="la")
                second_y = row2_top + int(W * 0.0806)
                draw.text((content_x0, second_y), l2.upper(), font=lbl2_font, fill=GRAY, anchor="la")
                draw.text((content_x0, second_y + int(lbl2_font.size * 1.96)), str(v2), font=val2_font, fill=INK, anchor="la")
            left_bottom = row2_top + int(W * 0.0806) + int(lbl2_font.size * 1.96) + int(val2_font.size * 1.3)

        # ---- right column: QR / gauge / blank ----
        gcx = cx + (content_x1 - cx) * 0.62
        right_bottom = row2_top

        if qr_img is not None:
            if render:
                draw_spaced(draw, gcx, row2_top, cfg.qr_caption.upper(), qr_caption_font, INK, 6)
            box_top = row2_top + int(qr_caption_font.size * 2.1)
            qr_pad = int(qr_size * 0.047)
            box_half = qr_size / 2 + qr_pad
            if render:
                box = [gcx - box_half, box_top, gcx + box_half, box_top + qr_size + 2 * qr_pad]
                draw.rounded_rectangle(box, radius=20, fill=(255, 255, 255), outline=CARD_BORDER, width=5)
                img.paste(qr_img, (int(gcx - qr_size / 2), int(box_top + qr_pad)))
            right_bottom = box_top + qr_size + 2 * qr_pad
            if cfg.qr_subcaption:
                sub_y = right_bottom + int(W * 0.0111)
                if render:
                    draw_spaced(draw, gcx, sub_y, cfg.qr_subcaption.upper(), qr_subcaption_font, GRAY, 6)
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
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
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
    """Search TCGdex (Japanese sets first, then English/international) for a set
    matching `query` by id or by name. Returns the matched set id, or None."""
    needle = query.strip().lower()
    for lang in ("ja", "en"):
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
            return None
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
              "Japan-exclusive set that hasn't released in English yet, TCGdex only has "
              "its Japanese name -- search by the set CODE instead (e.g. \"M5\"), not an "
              "English fan translation. Otherwise it may just be too new to be indexed "
              "yet. Either way, fill in the gaps with --set-code/--name/--release-date/"
              "--total-cards/etc.")
        return info

    print(f"[lookup] Fetching English and Japanese detail for set '{set_id}'...")
    en = _fetch_json(f"{TCGDEX_BASE}/en/sets/{set_id}", verbose) or {}
    ja = _fetch_json(f"{TCGDEX_BASE}/ja/sets/{set_id}", verbose) or {}
    if en:
        print("[lookup] English detail found.")
    else:
        print(f"[lookup] No English-dataset entry for '{set_id}' (expected for "
              "Japan-exclusive sets not yet released in English).")
    if ja:
        print("[lookup] Japanese detail found.")

    info["set_code"] = str(en.get("id") or ja.get("id") or set_id).upper()
    if en.get("name"):
        info["name"] = en["name"]
    jp_name = ja.get("name")
    if jp_name and jp_name != info.get("name"):
        info["name_jp"] = jp_name
    if "name" not in info and jp_name:
        # No English release on TCGdex yet -- flag it so the caller can decide whether
        # it's actually a problem (i.e. the user didn't already supply --name themselves).
        info["_name_unavailable_jp"] = jp_name

    serie = en.get("serie") or ja.get("serie") or {}
    if isinstance(serie, dict) and serie.get("name") and _is_latin_text(serie["name"]):
        info["era"] = serie["name"]

    release_raw = en.get("releaseDate") or ja.get("releaseDate")
    if release_raw:
        info["release_date"] = _format_release_date(release_raw)

    card_count = en.get("cardCount") or ja.get("cardCount") or {}
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
    """Print every set TCGdex knows about (id, English name if any, Japanese name if
    any), merged across both datasets and sorted by id. Used by --list-sets."""
    combined = {}
    for lang in ("ja", "en"):
        print(f"[lookup] Fetching the {lang} set list from TCGdex...")
        listing = _fetch_json(f"{TCGDEX_BASE}/{lang}/sets", verbose)
        if not listing:
            print(f"[warning] Couldn't fetch TCGdex's {lang} set list.")
            continue
        print(f"[lookup] Got {len(listing)} sets from the {lang} dataset.")
        for s in listing:
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
        shown = " / ".join(n for n in (names.get("en"), names.get("ja")) if n)
        print(f"{sid:<10} {shown or '(no name)'}")


# --------------------------------------------------------------------- CLI --

def parse_stat(value):
    parts = value
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("--stat needs exactly two values: LABEL VALUE")
    return tuple(parts)


def build_arg_parser():
    p = argparse.ArgumentParser(
        description="Generate a print-ready Pokemon TCG binder cover (Pokedex-style panel).",
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
    p.add_argument("--game-title", default="Pokemon Card Game")
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
    p.add_argument("--footer", default="Japanese Master Set")
    p.add_argument("--accent", default="C42A22", help="Hex accent color (no #), default a Pokemon red")
    p.add_argument("--bg-color", default="FFFFFF", help="Hex color (no #) for the area outside the panel, "
                                                          "default white -- keeps printing ink-cheap")
    p.add_argument("--accent-tab", dest="accent_tab", action=argparse.BooleanOptionalAction,
                    default=True, help="Show/hide the small red accent tab on the right edge")
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

    if not had_name and looked_up.get("_name_unavailable_jp"):
        print(f"[note] '{args.set_query}' has no English release on TCGdex yet -- only "
              f"its Japanese name ({looked_up['_name_unavailable_jp']}) is available. "
              f"Pass --name yourself, e.g. --name \"{args.set_query}\", for the English "
              "display text.")

    missing = [f for f in ("set_code", "name", "release_date", "total_cards") if not getattr(args, f)]
    if missing:
        flags = ", ".join(f"--{f.replace('_', '-')}" for f in missing)
        parser.error(f"couldn't determine {flags} from --set \"{args.set_query}\" -- "
                     f"pass them directly to fill in the gaps")
    args.total_cards = str(args.total_cards)

    if not args.out:
        safe_name = "".join(c if c.isalnum() else "_" for c in args.name).strip("_")
        lang_tag = "JP" if args.name_jp else "EN"
        args.out = f"{args.set_code}_{safe_name}_{lang_tag}_cover.png"

    img = build_cover(args)
    img.save(args.out, "PNG")
    print(f"Saved {args.out}  ({args.size}x{args.size}px)")


if __name__ == "__main__":
    sys.exit(main())
