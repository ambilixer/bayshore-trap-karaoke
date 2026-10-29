#!/usr/bin/env python3
# Created by Andy Charlie W. (ambilixer) in collaboration with Claude (Anthropic).
"""
render_titles.py

Step 4. Renders the front and end title cards for a track from its metadata,
as two short silent videos that splice_titles.py joins to the karaoke video.

Usage:
    python render_titles.py <track_id> [--dry-run] [--no-grain] [--no-scanlines]
                                       [--ass-out PATH]

Reads, from the track's folder:
    <track_id>_meta.json          keys used: artist, title, album, year

Writes:
    <track_id>_titles_front.mp4
    <track_id>_titles_end.mp4

--dry-run prints the fit and layout report without rendering; --ass-out writes
the front card's subtitle file, for inspecting it in Aegisub.

The cards are set in Consolas, with the note glyph from Segoe UI Symbol; both
ship with Windows. The horizontal constants below were tuned against real
renders with those fonts, so a substitute font will not line up. CHANNEL_TAG is
not ASCII, so this file must stay UTF-8.

──────────────────────────────────────────────────────────────────────────────
DESIGN: "Readout"

A label/value stack on a flow layout. Each field is a BLOCK: a small lime
label, a 4px gap, then one or two lines of value. Blocks stack with a constant
BLOCK_GAP inside the REGION between the two hairline rules (y 196..878, inset
REGION_PAD). The TRACK block's vertical centre is pinned at TRACK_ANCHOR_Y on
every card, so the largest element, and the thumbnail crop, never shifts;
a wrapped title grows symmetrically around it.

Text is fitted by an ordered cascade, gentlest move first, each stage solved in
closed form because Consolas is monospace:

    1. negative \\fsp   down to -FSP_RATIO x fontsize   (no distortion)
    2. ScaleX          down to SX_MIN                  (condenses letterforms)
    3. fontsize        solved exactly
    4. wrap to 2 lines when 1..3 would push the size below WRAP_TRIGGER

Everything is budgeted on INK width, not advance width. Letter-spacing is
subtracted after every character INCLUDING the last, so the final glyph's ink
sits |fsp| beyond the advance box:

    ink = (n * fs * CHAR_ADV + (n - 1) * fsp) * ScaleX / 100

Budgeting on advance leaves that overhang outside the slot, which is enough
to push the URL or a long title past the rule.

Texture: static scanlines are drawn in the ASS layer as a single \\p1 shape
(they cost essentially nothing to encode because the pattern never changes
between frames). Animated grain is an ffmpeg filter and fades to zero before
the cut, so there is no pop at the splice into the karaoke segment.
──────────────────────────────────────────────────────────────────────────────
"""

import json
import os
import subprocess
import sys
import tempfile

from paths import get_track_path

# ── CONFIG ────────────────────────────────────────────────────────────────────

BG_COLOR        = "#090406"
FONT_FACE       = "Consolas"
SYMBOL_FACE     = "Segoe UI Symbol"
PLAY_RES_X      = 1920
PLAY_RES_Y      = 1080
FPS             = 60          # must match FPS in karaoke_render.py

FRONT_DURATION  = 5.1
BACK_DURATION   = 5.1

CHANNEL_TAG     = "ßayshore ŧrap ĸaraoke"
CHANNEL_URL     = "youtube.com/@bayshoretrap"
NOTE_GLYPH      = "\U0001D133"

# Consolas advance per \fs unit, as libass renders it. Measured by solving it
# back out of a real render across six elements at five sizes; every one landed
# on 0.4698, which also matches Aegisub's text_extents().
#
# NOTE the trap: Consolas' advance on the EM SQUARE is 0.5498, which is what a
# browser reports for a given CSS font-size. libass normalises \fs to the
# font's ascent+descent rather than the em square, so the same nominal "font
# size" produces a 17% narrower advance here. Measuring in a browser to get a
# number for this file gives the wrong answer: measure in a render.
CHAR_ADV        = 0.4698

# Left side bearing as a fraction of \fs, used to optically align ink edges
# rather than advance-box edges. Without it a large glyph appears indented
# relative to a small one: measured 11px on 'B' at fs176 against 1px on 'O' at
# fs54. It is glyph-dependent so it can never be exact; 0.05 pulls the spread
# from ~10px down to ~4px across the card.
OPTICAL_LSB     = 0.05

# Ascent as a fraction of \fs, used to sit the URL on the channel tag's
# baseline. Checked against a render: tag ink 921..955, URL ink 925..954, so
# the baselines agree.
CHAR_ASCENT     = 0.767

# ── Layout ────────────────────────────────────────────────────────────────────

X_LEFT          = 190
COL_W           = 1580
X_RIGHT         = X_LEFT + COL_W          # 1770; both rules end here

RULE_T_Y        = 196
RULE_B_Y        = 878
RULE_H          = 2
REGION_PAD      = 34                      # inset of the flow region from the rules
REGION_TOP      = RULE_T_Y + REGION_PAD
REGION_BOT      = RULE_B_Y - REGION_PAD

LABEL_FS        = 30
LABEL_FSP       = 10
LABEL_H         = 36                      # label line box, for stacking arithmetic
LABEL_GAP       = 4                       # label bottom -> value top
LINE_H          = 1.18                    # value line pitch, x fontsize
BLOCK_GAP       = 44
BLOCK_GAP_MIN   = 20

TRACK_ANCHOR_Y  = 570                     # pinned vertical centre of the track block
YEAR_GAP        = 60                      # clearance between album slot and year column

EYEBROW_TEXT    = "NOW PLAYING"           # front card only
EYEBROW_FS      = 28
EYEBROW_FSP     = 11
EYEBROW_Y       = 146

# ── Fit cascade ───────────────────────────────────────────────────────────────

MAX_LINES       = 2
WRAP_TRIGGER    = 0.72                    # wrap once one line would drop below this x ideal
FSP_RATIO       = 0.025                   # letter-spacing floor, as a fraction of fontsize
SX_MIN          = 92                      # ScaleX floor
LINE_GAIN       = 1.04                    # an extra line must buy at least this much size

FS_ARTIST       = 108
FS_TRACK        = 176
FS_ALBUM        = 54

# ── Channel row ───────────────────────────────────────────────────────────────

TAG_Y           = 891                     # ~20px under the lower rule
TAG_FS          = 46
TAG_FSP         = -4
URL_FS          = 38
URL_FSP         = -3
NOTE_FS         = 58
# The note glyph is Segoe UI Symbol, not Consolas, so none of the monospace
# arithmetic applies to it. Measured off two renders: placed at x=509 its ink
# began at 516, a left bearing of 7px at fs58 = 0.12 em. Its ink is also only
# ~20px wide at fs58, far narrower than the font size implies: raise NOTE_FS if
# it should carry more weight against the 46px tag. All three are hand-tuned;
# there is no formula to derive them.
# Tuned against renders: at GAP 26 / DY 6 the ink measured x 588..606,
# y 899..937 against a tag at x ..557, y 900..933, a 31px gap, and
# optically low because the glyph overhangs the tag's baseline by 4px while
# sitting 1px above its cap line. GAP 20 / DY 2 closes it to ~25px and lifts the
# glyph's centre 2.5px above the tag's to balance that overhang.
NOTE_LSB        = 0.12                    # left bearing, em
NOTE_GAP        = 20                      # ink-to-ink gap from the tag
NOTE_DY         = 2                       # nudge down from box-centred on the tag

# ── Entry-cue dots (front card only) ──────────────────────────────────────────
# Mirrors the lyric display's cue, scaled up for the card. Right-aligned at the
# rule's right end on the eyebrow row; extinguishes leftmost-first, the last one
# going out exactly as the card cuts to the karaoke segment.

DOT_COUNT       = 3
DOT_SIZE        = 20
DOT_GAP         = 20
CUE_MS          = 2000

# ── Timeline (ms) ─────────────────────────────────────────────────────────────
# (start, sweep duration) per element. Durations are multiplied by SWEEP_SCALE
# at use; start times are absolute. The reveal is a left-to-right clip wipe.

SEQ = {
    "rule_t":  (0,    250),
    "eyebrow": (125,  150),
    "lb_1":    (250,  100),
    "v_1":     (310,  210),
    "lb_2":    (530,  100),
    "v_2":     (590,  250),
    "lb_3":    (860,  100),
    "lb_4":    (860,  100),
    "v_3":     (920,  200),
    "v_4":     (920,  100),
    "rule_b":  (1100, 250),
    "tag":     (1200, 170),
    "note":    (1380, 150),
    "url":     (1475, 170),
}
# Each element's wipe runs for SWEEP_SCALE x its listed duration. The start
# times are untouched, so the cadence holds and only the sweeps slow down.
SWEEP_SCALE     = 2.0
REVEAL_END      = 1815                    # max(start + SWEEP_SCALE*dur) == url
DOTS_ON         = 3100                    # REVEAL_END + 1285ms hold
FADE_MS         = 250
FADE_START      = 4850                    # FRONT_DURATION*1000 - FADE_MS
# REVEAL_END, DOTS_ON and FADE_START are kept in step with SEQ by hand.
#
# Every element of the front card is fully opaque from REVEAL_END to FADE_START
# (1.815s to 4.85s). generate_yt.py grabs the thumbnail inside that window, at
# its THUMB_TIMESTAMP; if this timeline changes, check it still does.

# ── Texture ───────────────────────────────────────────────────────────────────

SCANLINE_PERIOD = 3                       # px; 1 dark row every N
SCANLINE_ALPHA  = "A8"                    # ASS alpha, 00 opaque .. FF clear
GRAIN_STRENGTH  = 8                       # ffmpeg noise alls=; 0 disables
GRAIN_FADE_MS   = 600                     # grain ramps to zero over the last N ms

# ── Colours ───────────────────────────────────────────────────────────────────

C_ARTIST        = "#f76e64"
C_TRACK         = "#b9de40"
C_ALBUM         = "#f9edc8"
C_YEAR          = "#d4dfda"
C_LABEL         = "#b9de40"
C_TAG           = "#b9de40"
C_URL           = "#f9edc8"
C_NOTE          = "#f76e64"
C_RULE          = "#b9de40"

A_LABEL         = "73"                    # 0.55 opacity
A_EYEBROW       = "80"                    # 0.50
A_RULE          = "B3"                    # 0.30


# ── COLOUR / ASS HELPERS ──────────────────────────────────────────────────────

def hex_to_ass(h):
    """Convert #RRGGBB to ASS &H00BBGGRR& format (opaque)."""
    h = h.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}&"


def ms_to_ass(ms):
    """Convert milliseconds to ASS time format H:MM:SS.cc"""
    total_cs = int(round(ms)) // 10
    cs       = total_cs % 100
    total_s  = total_cs // 100
    s        = total_s % 60
    total_m  = total_s // 60
    m        = total_m % 60
    h        = total_m // 60
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_header():
    """The [Script Info], [V4+ Styles] and [Events] headers for a card."""
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {PLAY_RES_X}\n"
        f"PlayResY: {PLAY_RES_Y}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{FONT_FACE},40,&H00FFFFFF&,&H000000FF&,"
        "&H00000000&,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )


def event(start_ms, end_ms, text, layer=0):
    """One Dialogue line in the Default style."""
    return (f"Dialogue: {layer},{ms_to_ass(start_ms)},{ms_to_ass(end_ms)},"
            f"Default,,0,0,0,,{text}\n")


def rect(w, h):
    """A \\p1 rectangle drawing with its origin at the top-left."""
    return f"m 0 0 l {int(round(w))} 0 {int(round(w))} {int(round(h))} 0 {int(round(h))}"


WIPE_PAD = 80   # the resting clip overshoots the text box, so a small error in
                # the width model can never leave a glyph permanently clipped

def cue(name):
    """(start, sweep duration) for a timeline element, with SWEEP_SCALE applied."""
    start, dur = SEQ[name]
    return start, dur * SWEEP_SCALE


def wipe(x, y, w, h, dur_ms):
    """Clip tags that reveal a box left to right."""
    x0, x1 = int(round(x)), int(round(x + w))
    y0, y1 = int(round(y - h * 0.4)), int(round(y + h * 1.4))
    return (f"\\clip({x0},{y0},{x0},{y1})"
            f"\\t(0,{int(dur_ms)},\\clip({x0},{y0},{x1 + WIPE_PAD},{y1}))")


# ── FIT CASCADE ───────────────────────────────────────────────────────────────

def ink_width(n_chars, fs, fsp, sx):
    """Rendered ink width of a monospace run of n_chars (spaces included)."""
    if n_chars <= 0:
        return 0.0
    return (n_chars * fs * CHAR_ADV + (n_chars - 1) * fsp) * sx / 100.0


def solve(lines, budget, ideal):
    """
    Ordered cascade against a per-line ink budget. Returns a dict with the
    gentlest settings that fit, or the best available if nothing does.
    Stage: 0 ideal, 1 letter-spacing, 2 condense, 3 size.
    """
    widest_n = max(len(l) for l in lines)
    fs, fsp, sx, stage = float(ideal), 0.0, 100.0, 0

    if ink_width(widest_n, fs, fsp, sx) <= budget:
        return {"fs": fs, "fsp": fsp, "sx": sx, "stage": 0}

    # 1: negative letter-spacing, down to the ratio floor
    stage = 1
    if widest_n > 1:
        need = (budget * 100.0 / sx - widest_n * fs * CHAR_ADV) / (widest_n - 1)
        fsp = max(-FSP_RATIO * fs, need)
    if ink_width(widest_n, fs, fsp, sx) <= budget:
        return {"fs": fs, "fsp": fsp, "sx": sx, "stage": 1}

    # 2: condense
    stage = 2
    denom = widest_n * fs * CHAR_ADV + (widest_n - 1) * fsp
    if denom > 0:
        sx = max(float(SX_MIN), budget * 100.0 / denom)
    if ink_width(widest_n, fs, fsp, sx) <= budget:
        return {"fs": fs, "fsp": fsp, "sx": sx, "stage": 2}

    # 3: size, solved exactly with fsp held at its ratio
    stage = 3
    denom = sx * (widest_n * CHAR_ADV - (widest_n - 1) * FSP_RATIO)
    fs = budget * 100.0 / denom if denom > 0 else fs
    fsp = -FSP_RATIO * fs
    return {"fs": fs, "fsp": fsp, "sx": sx, "stage": 3}


def split_into(text, n_lines, budget):
    """Break text into n_lines at word boundaries, minimising the longest line."""
    words = text.split()
    if n_lines <= 1 or len(words) < n_lines:
        return [" ".join(words)]

    best = None

    def walk(start, made, acc):
        nonlocal best
        if made == n_lines - 1:
            lines = acc + [" ".join(words[start:])]
            score = max(len(l) for l in lines)
            if best is None or score < best[0]:
                best = (score, lines)
            return
        for c in range(start + 1, len(words) - (n_lines - 2 - made)):
            walk(c, made + 1, acc + [" ".join(words[start:c])])

    walk(0, 0, [])
    return best[1] if best else [" ".join(words)]


def fit_field(text, ideal, budget):
    """Choose a line count and the settings that go with it."""
    text = (text or "").strip()
    if not text:
        return {"lines": [""], "fs": float(ideal), "fsp": 0.0, "sx": 100.0,
                "stage": 0, "widest": 0.0, "budget": budget}

    max_l = min(MAX_LINES, len(text.split()))
    trigger = ideal * WRAP_TRIGGER
    chosen = None

    for n in range(1, max_l + 1):
        lines = split_into(text, n, budget)
        r = solve(lines, budget, ideal)
        r["lines"] = lines
        if chosen is None:
            chosen = r
        elif r["fs"] > chosen["fs"] * LINE_GAIN:
            chosen = r
        if len(chosen["lines"]) == n and chosen["fs"] >= trigger:
            break

    chosen["widest"] = max(
        ink_width(len(l), chosen["fs"], chosen["fsp"], chosen["sx"])
        for l in chosen["lines"])
    chosen["budget"] = budget
    if len(chosen["lines"]) > 1:
        chosen["stage"] = 4
    return chosen


# ── VERTICAL FLOW ─────────────────────────────────────────────────────────────

def layout_card(artist, track_name, album, year):
    """
    Fit all four fields and resolve the vertical flow. Returns (fields, geo)
    where geo carries each block's label_y / value_y and the resolved gap.
    """
    # The year column right-aligns on the rule; the album slot stops short of it.
    year_label_x = X_RIGHT - ink_width(len("YEAR"), LABEL_FS, LABEL_FSP, 100)
    year_value_x = X_RIGHT - ink_width(len(year), FS_ALBUM, 0, 100)
    album_budget = min(year_label_x, year_value_x) - YEAR_GAP - X_LEFT

    fields = {
        "artist": fit_field(artist,     FS_ARTIST, COL_W),
        "track":  fit_field(track_name, FS_TRACK,  COL_W),
        "album":  fit_field(album,      FS_ALBUM,  album_budget),
    }

    order   = ["artist", "track", "album"]
    heights = [LABEL_H + LABEL_GAP + len(fields[k]["lines"]) * fields[k]["fs"] * LINE_H
               for k in order]
    sum_h   = sum(heights)
    region  = REGION_BOT - REGION_TOP
    gap     = float(BLOCK_GAP)
    scale   = 1.0

    if sum_h + 2 * gap > region:
        gap = max(float(BLOCK_GAP_MIN), (region - sum_h) / 2.0)
    if sum_h + 2 * gap > region:
        scale = max(0.5, (region - 2 * gap) / sum_h)

    group_h = sum_h * scale + 2 * gap

    # Track-anchored: pin the track block's vertical centre, then clamp to the
    # region. Clamping wins: a card that overflows the rules is worse than one
    # whose anchor slipped.
    track_h = heights[1] * scale
    top = TRACK_ANCHOR_Y - track_h / 2.0 - (heights[0] * scale + gap)
    clamped = max(float(REGION_TOP), min(top, REGION_BOT - group_h))
    anchor_held = abs(clamped - top) < 0.5
    top = clamped

    geo, y = {}, top
    for k, h0 in zip(order, heights):
        h = h0 * scale
        if scale != 1.0:
            fields[k]["fs"] *= scale
            fields[k]["fsp"] *= scale
            fields[k]["widest"] *= scale
        geo[k] = {"label_y": round(y),
                  "value_y": round(y + LABEL_H + LABEL_GAP),
                  "h": round(h)}
        y += h + gap

    geo["_meta"] = {"gap": round(gap), "scale": scale, "top": round(top),
                    "group_h": round(group_h), "region": region,
                    "anchor_held": anchor_held,
                    "year_label_x": year_label_x, "year_value_x": year_value_x,
                    "album_budget": album_budget}
    return fields, geo


# ── EVENT BUILDERS ────────────────────────────────────────────────────────────

def text_event(start, dur, text, x, y, fs, fsp, sx, colour, alpha,
               end_ms, right=False, italic=False, font=None, extra=""):
    """
    One wiped, fading run of text. x is the left edge, or the right edge when
    right=True. Both are corrected by OPTICAL_LSB so that the glyph ink, not the
    advance box, lands on the target; see the constant's note.
    """
    w = ink_width(len(text), fs, fsp, sx)
    x0 = (x - w + OPTICAL_LSB * fs) if right else (x - OPTICAL_LSB * fs)
    tags = (f"\\an7\\pos({int(round(x0))},{int(round(y))})"
            f"\\fs{fs:.1f}\\c{hex_to_ass(colour)}\\alpha&H{alpha}&"
            f"\\bord0\\shad0")
    if fsp:
        tags += f"\\fsp{fsp:.2f}"
    if sx != 100:
        tags += f"\\fscx{sx:.2f}"
    if italic:
        tags += "\\i1"
    if font:
        tags += f"\\fn{font}"
    tags += extra
    tags += wipe(x0, y, w, fs * LINE_H, dur)
    tags += f"\\fad(0,{FADE_MS})"
    return event(start, end_ms, "{" + tags + "}" + text)


def field_events(seq_label, seq_value, fld, geo, colour, end_ms, label,
                 italic=False):
    """Label + one or two value lines for a single block."""
    evts = []
    ls, ld = cue(seq_label)
    lw = ink_width(len(label), LABEL_FS, LABEL_FSP, 100)
    lx = X_LEFT - OPTICAL_LSB * LABEL_FS
    evts.append(event(ls, end_ms,
        "{" + f"\\an7\\pos({int(round(lx))},{geo['label_y']})\\fs{LABEL_FS}"
        f"\\c{hex_to_ass(C_LABEL)}\\alpha&H{A_LABEL}&\\bord0\\shad0"
        f"\\fsp{LABEL_FSP}" + wipe(lx, geo['label_y'], lw, LABEL_H, ld)
        + f"\\fad(0,{FADE_MS})" + "}" + label))

    vs, vd = cue(seq_value)
    for i, line in enumerate(fld["lines"]):
        y = geo["value_y"] + i * fld["fs"] * LINE_H
        evts.append(text_event(vs + i * 60, vd, line, X_LEFT, y,
                               fld["fs"], fld["fsp"], fld["sx"], colour, "00",
                               end_ms, italic=italic))
    return evts


def scanline_event(end_ms):
    """One static \\p1 shape: a 1px dark row every SCANLINE_PERIOD px."""
    parts = []
    for y in range(0, PLAY_RES_Y, SCANLINE_PERIOD):
        parts.append(f"m 0 {y} l {PLAY_RES_X} {y} {PLAY_RES_X} {y + 1} 0 {y + 1}")
    draw = " ".join(parts)
    return event(0, end_ms,
                 "{" + f"\\an7\\pos(0,0)\\p1\\fscx100\\fscy100\\bord0\\shad0"
                 f"\\c&H000000&\\alpha&H{SCANLINE_ALPHA}&" + "}" + draw,
                 layer=100)


def dot_events():
    """Entry-cue dots: right-aligned on the rule, extinguishing leftmost-first."""
    evts = []
    cluster = DOT_COUNT * DOT_SIZE + (DOT_COUNT - 1) * DOT_GAP
    x0 = X_RIGHT - cluster
    y = EYEBROW_Y + EYEBROW_FS * 1.2 / 2.0 - DOT_SIZE / 2.0
    draw = rect(DOT_SIZE, DOT_SIZE)
    for j in range(DOT_COUNT):
        x = x0 + j * (DOT_SIZE + DOT_GAP)
        out = DOTS_ON + (j + 1) * CUE_MS / DOT_COUNT
        evts.append(event(DOTS_ON, out,
                          "{" + f"\\an7\\pos({int(round(x))},{int(round(y))})"
                          f"\\p1\\fscx100\\fscy100\\bord0\\shad0"
                          f"\\c{hex_to_ass(C_TRACK)}" + "}" + draw, layer=21))
    return evts


def channel_events(end_ms):
    """
    Channel row: tag flush left, note glyph immediately after it, URL flush
    right against the rule. All three sit on the tag's baseline.
    """
    evts = []
    tag_w  = ink_width(len(CHANNEL_TAG), TAG_FS, TAG_FSP, 100)
    url_y  = TAG_Y + CHAR_ASCENT * (TAG_FS - URL_FS)
    note_y = TAG_Y + (TAG_FS - NOTE_FS) * 1.2 / 2.0 + NOTE_DY
    # The tag's ink ends OPTICAL_LSB in from its advance edge; the glyph's ink
    # starts NOTE_LSB into its own box. Place so the two inks sit NOTE_GAP apart.
    tag_ink_right = X_LEFT - OPTICAL_LSB * TAG_FS + tag_w
    note_x = tag_ink_right + NOTE_GAP - NOTE_LSB * NOTE_FS

    ts, td = cue("tag")
    evts.append(text_event(ts, td, CHANNEL_TAG, X_LEFT, TAG_Y, TAG_FS, TAG_FSP,
                           100, C_TAG, "00", end_ms))
    ns, nd = cue("note")
    evts.append(event(ns, end_ms,
        "{" + f"\\an7\\pos({int(round(note_x))},{int(round(note_y))})"
        f"\\fs{NOTE_FS}\\c{hex_to_ass(C_NOTE)}\\bord1\\blur2"
        f"\\3c{hex_to_ass(C_NOTE)}\\shad0\\b1\\i1\\fn{SYMBOL_FACE}"
        f"\\fad({int(nd)},{FADE_MS})" + "}" + NOTE_GLYPH))
    us, ud = cue("url")
    evts.append(text_event(us, ud, CHANNEL_URL, X_RIGHT, url_y, URL_FS, URL_FSP,
                           100, C_URL, "00", end_ms, right=True))
    return evts


def build_card_ass(artist, track_name, album, year, is_end=False, scanlines=True):
    """
    Build one card. The end card is identical to the front card except that it
    drops the NOW PLAYING eyebrow and the entry-cue dots: nothing follows it,
    so a countdown there would be a lie. Returns (ass_text, fields, geo).
    """
    end_ms = int(round((BACK_DURATION if is_end else FRONT_DURATION) * 1000))
    fields, geo = layout_card(artist, track_name, album, year)
    evts = []

    # rules
    for name, y in (("rule_t", RULE_T_Y), ("rule_b", RULE_B_Y)):
        st, d = cue(name)
        evts.append(event(st, end_ms,
            "{" + f"\\an7\\pos({X_LEFT},{y})\\p1\\fscx100\\fscy100\\bord0\\shad0"
            f"\\c{hex_to_ass(C_RULE)}\\alpha&H{A_RULE}&"
            + wipe(X_LEFT, y, COL_W, RULE_H, d)
            + f"\\fad(0,{FADE_MS})" + "}" + rect(COL_W, RULE_H)))

    # eyebrow, front card only
    if not is_end:
        st, d = cue("eyebrow")
        evts.append(text_event(st, d, EYEBROW_TEXT, X_LEFT, EYEBROW_Y,
                               EYEBROW_FS, EYEBROW_FSP, 100, C_LABEL,
                               A_EYEBROW, end_ms))

    # blocks
    evts += field_events("lb_1", "v_1", fields["artist"], geo["artist"],
                         C_ARTIST, end_ms, "ARTIST")
    evts += field_events("lb_2", "v_2", fields["track"], geo["track"],
                         C_TRACK, end_ms, "TRACK")
    evts += field_events("lb_3", "v_3", fields["album"], geo["album"],
                         C_ALBUM, end_ms, "ALBUM", italic=True)

    # year: right-aligned on the rule, riding the album block
    year_fs = min(FS_ALBUM, fields["album"]["fs"])
    yl_w = ink_width(len("YEAR"), LABEL_FS, LABEL_FSP, 100)
    yl_x = X_RIGHT - yl_w + OPTICAL_LSB * LABEL_FS
    st, d = cue("lb_4")
    evts.append(event(st, end_ms,
        "{" + f"\\an7\\pos({int(round(yl_x))},{geo['album']['label_y']})"
        f"\\fs{LABEL_FS}\\c{hex_to_ass(C_LABEL)}\\alpha&H{A_LABEL}&"
        f"\\bord0\\shad0\\fsp{LABEL_FSP}"
        + wipe(yl_x, geo['album']['label_y'], yl_w, LABEL_H, d)
        + f"\\fad(0,{FADE_MS})" + "}YEAR"))
    st, d = cue("v_4")
    evts.append(text_event(st, d, year, X_RIGHT, geo["album"]["value_y"],
                           year_fs, 0, 100, C_YEAR, "00", end_ms, right=True))

    # entry-cue dots, front card only
    if not is_end:
        evts += dot_events()

    evts += channel_events(end_ms)

    if scanlines:
        evts.append(scanline_event(end_ms))

    return ass_header() + "".join(evts), fields, geo


def build_front_ass(artist, track_name, album, year, scanlines=True):
    """The front card's subtitle text."""
    return build_card_ass(artist, track_name, album, year, False, scanlines)[0]


def build_back_ass(artist, track_name, album, year, scanlines=True):
    """The end card's subtitle text."""
    return build_card_ass(artist, track_name, album, year, True, scanlines)[0]


# ── RENDER ────────────────────────────────────────────────────────────────────

def render_title_card(ass_content, output_path, duration_s, grain=True):
    """Render ASS events over a solid canvas, with an optional grain layer."""
    tmp = tempfile.NamedTemporaryFile(suffix=".ass", mode="w",
                                      encoding="utf-8", delete=False)
    try:
        tmp.write(ass_content)
        tmp.close()
        # ffmpeg's filter parser treats ":" as an option separator and "\" as
        # an escape, so a Windows temp path needs forward slashes and an
        # escaped drive colon.
        ass_escaped = tmp.name.replace("\\", "/").replace(":", "\\:")

        if grain and GRAIN_STRENGTH > 0:
            # Grain ramps to zero over the last GRAIN_FADE_MS so there is no
            # texture pop at the splice into the (ungrained) karaoke segment.
            fade_end = duration_s
            fade_dur = GRAIN_FADE_MS / 1000.0
            w = f"max(0,min(1,({fade_end:.3f}-T)/{fade_dur:.3f}))"
            filt = (f"[0:v]ass='{ass_escaped}'[t];"
                    f"[t]split=2[gn][cl];"
                    f"[gn]noise=alls={GRAIN_STRENGTH}:allf=t+u[nz];"
                    f"[nz][cl]blend=all_expr='A*{w}+B*(1-{w})'[v]")
        else:
            filt = f"[0:v]ass='{ass_escaped}'[v]"

        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"color=c={BG_COLOR}:s={PLAY_RES_X}x{PLAY_RES_Y}:r={FPS}",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-filter_complex", filt,
            "-map", "[v]", "-map", "1:a",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "320k",
            "-t", str(duration_s),
            "-movflags", "+faststart",
            str(output_path),
        ]
        print(f"  Rendering: {output_path.name}")
        subprocess.run(cmd, check=True)
    finally:
        os.unlink(tmp.name)


# ── MAIN ──────────────────────────────────────────────────────────────────────

STAGE_NAME = ["ideal", "letter-spacing", "condense", "size reduced", "wrapped"]


def report(fields, geo):
    """Print how each field was fitted and where the blocks landed."""
    print("\n  Fit:")
    for k in ("artist", "track", "album"):
        f = fields[k]
        over = "  *** OVER SLOT" if f["widest"] > f["budget"] + 0.5 else ""
        print(f"    {k:7s} {len(f['lines'])} line(s)  fs {f['fs']:6.1f}  "
              f"fsp {f['fsp']:5.1f}  ScaleX {f['sx']:5.1f}  "
              f"{f['widest']:7.1f}/{f['budget']:.0f}px  "
              f"[{STAGE_NAME[f['stage']]}]{over}")
    m = geo["_meta"]
    print(f"\n  Flow: group {m['group_h']}px in {m['region']}px region, "
          f"top {m['top']}, gap {m['gap']}"
          + (f", scaled {m['scale']*100:.0f}%" if m["scale"] != 1.0 else "")
          + ("" if m["anchor_held"] else "  *** track anchor overridden"))
    for k in ("artist", "track", "album"):
        print(f"    {k:7s} label y {geo[k]['label_y']:4d}  "
              f"value y {geo[k]['value_y']:4d}  height {geo[k]['h']:4d}")


def main():
    """Command-line entry point. See the module docstring for usage."""
    args = sys.argv[1:]
    if not args:
        print("Usage: python render_titles.py <track_id> [--dry-run] "
              "[--no-grain] [--no-scanlines] [--ass-out PATH]")
        sys.exit(1)

    track_id  = args[0]
    dry_run   = "--dry-run" in args
    grain     = "--no-grain" not in args
    scanlines = "--no-scanlines" not in args
    ass_out   = None
    if "--ass-out" in args:
        i = args.index("--ass-out")
        if i + 1 < len(args):
            ass_out = args[i + 1]

    meta_path = get_track_path(track_id, "meta.json")
    if not meta_path.exists():
        print(f"ERROR: Meta file not found: {meta_path}")
        sys.exit(1)

    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    artist     = meta["artist"]
    track_name = meta["title"]
    album      = meta["album"]
    year       = str(meta["year"])

    front_out = get_track_path(track_id, "titles_front.mp4")
    end_out   = get_track_path(track_id, "titles_end.mp4")

    print(f"\nrender_titles: {track_id}")
    print(f"  Artist:    {artist}")
    print(f"  Track:     {track_name}")
    print(f"  Album:     {album} ({year})")
    print(f"  Cards:     {FRONT_DURATION}s front, {BACK_DURATION}s end, {FPS}fps")
    print(f"  Texture:   scanlines {'on' if scanlines else 'off'}, "
          f"grain {'alls=' + str(GRAIN_STRENGTH) if grain else 'off'}")

    front_ass, fields, geo = build_card_ass(artist, track_name, album, year,
                                            False, scanlines)
    back_ass = build_card_ass(artist, track_name, album, year, True, scanlines)[0]
    report(fields, geo)

    if ass_out:
        with open(ass_out, "w", encoding="utf-8") as f:
            f.write(front_ass)
        print(f"\n  Front ASS written to {ass_out}")

    if dry_run:
        print("\nDry run — exiting without rendering.")
        sys.exit(0)

    print("\n[1/2] Rendering front titles...")
    render_title_card(front_ass, front_out, FRONT_DURATION, grain)

    print("\n[2/2] Rendering end titles...")
    render_title_card(back_ass, end_out, BACK_DURATION, grain)

    print("\nDone.")
    print(f"  Front: {front_out}")
    print(f"  End:   {end_out}")
    print("\nNext step:")
    print(f"  python splice_titles.py {track_id}")


if __name__ == "__main__":
    main()
