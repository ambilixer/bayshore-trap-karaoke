#!/usr/bin/env python3
# Created by Andy Charlie W. (ambilixer) in collaboration with Claude (Anthropic).
"""
check_styles.py

Prints the Main/BG styles actually embedded in a track's subtitle file, plus the
layout geometry derived from them, and warns when a derived value has crossed a
known threshold. Advisory only: run_pipeline.py runs it between steps 4 and 5
and never stops on its result.

Usage:
    python check_styles.py <track_id> [--subs PATH]

Reads <track_id>_subs-final.ass in the track's folder, or the file given with
--subs.

Why this exists
---------------
The Main and BG styles live in Aegisub's Style Manager, outside every script and
outside version control. They are stamped into each .ass when styles are applied,
so every track carries a frozen snapshot of whatever the styles were that day.

A style changed in the Style Manager changes nothing else, but several
constants in the macro and in whisperx_to_ass.py are derived from the style
values, above all the font size. A change that goes unnoticed silently
invalidates them. This script makes the change visible in the log the first
time it renders, rather than the next time someone recomputes by hand.

Mirrored constants
------------------
The values under MIRRORED below are copies of constants that live in
btk_karaoke_v2.lua and whisperx_to_ass.py. They have to be kept in sync by hand.
That is a smaller version of the same problem this script exists to catch, but
it is bounded: these change rarely, and a mismatch here produces a wrong warning
rather than a wrong render.
"""

import argparse
import math
import sys

from paths import get_track_path


# ── MIRRORED CONSTANTS: keep in sync with the files named ─────────────────────

# btk_karaoke_v2.lua
Y_ACTIVE             = 486
ROW_PITCH            = 100
LINES_ABOVE          = 3
LINES_BELOW          = 3
SECTION_GAP          = 70
Y_BG                 = 880
BG_SLOT_SPACING_MULT = 1.3
BG_STACK_SHIFT       = 2
BG_MAX_SLOTS         = 2
DOT_COUNT            = 3
DOT_SIZE             = 14
DOT_GAP              = 14
DOT_MARGIN           = 46

# whisperx_to_ass.py
LONG_LINE_WARN = 48

# Font metrics, frame and thresholds
# Consolas advance in px per point of \fs, measured from libass renders. libass
# sizes \fs to the font's ascent plus descent, not to the em square a browser
# uses, so a figure measured in a browser (about 0.55) does not apply here.
CONSOLAS_ADVANCE = 0.4698
FRAME_W          = 1920
FRAME_H          = 1080
# A design margin: how far in from each edge a full-length line should stop.
# It is not where lines wrap. libass wraps at the style's MarginL/MarginR
# (40 each, so at 1840px), even for lines placed with \pos.
SIDE_MARGIN      = 110
WHITESPACE_FLOOR = 10       # px between stacked BG glyph boxes before collisions
ROW_GAP_FLOOR    = 20       # px between adjacent stack rows' glyph boxes
BG_CLEAR_FLOOR   = 20       # px between the bottom stack row and the BG glyph box
BG_HIDE_ROWS     = 1        # stack rows the macro fades out while BG lines show

# YouTube frame safety: the player's overlays cover the frame edges while a
# viewer interacts with it.
SAFE_TOP    = 80            # px; YouTube's title gradient covers roughly this much
# px; below this the control/progress bar can cover content. At BG fontsize 80 a
# stacked pair of BG lines reaches about y=1022, so the warning below prints on
# every track with a BG style. Stacked pairs are rare, and that overlap is an
# accepted trade-off for the larger BG size.
SAFE_BOTTOM = 980


# ── PARSING ───────────────────────────────────────────────────────────────────

def parse_styles(path):
    """
    Return {style_name: {field: value}} from the [V4+ Styles] block.

    Fields are mapped by the block's own Format: line rather than by position, so
    a reordered header can't silently shift every value. Style lines never contain
    commas inside a field, unlike Dialogue lines, so a plain split is safe here.
    The file is read as utf-8-sig, so a byte-order mark from Notepad is harmless.
    """
    styles     = {}
    fmt_fields = None
    in_block   = False

    with open(path, encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()

            if line.startswith("["):
                in_block = line.lower().startswith("[v4+ styles")
                continue
            if not in_block or not line:
                continue

            if line.startswith("Format:"):
                fmt_fields = [x.strip() for x in line[len("Format:"):].split(",")]
            elif line.startswith("Style:"):
                if fmt_fields is None:
                    continue
                values = [x.strip() for x in line[len("Style:"):].split(",")]
                if len(values) != len(fmt_fields):
                    print(f"  WARNING: style line has {len(values)} fields, "
                          f"Format declares {len(fmt_fields)} — skipping: {values[:1]}")
                    continue
                row = dict(zip(fmt_fields, values))
                styles[row.get("Name", "?")] = row

    return styles


def num(row, field, default=0.0):
    """A style field as a float, or default if it is missing or not a number."""
    try:
        return float(row.get(field, default))
    except (TypeError, ValueError):
        return default


# ── GEOMETRY ──────────────────────────────────────────────────────────────────

def px_per_char(fontsize, fsp, scale_x):
    """Horizontal pixels per character: (advance + spacing), scaled by ScaleX.

    Exact for a monospace face, which is why the lyrics use Consolas.
    """
    return (CONSOLAS_ADVANCE * fontsize + fsp) * (scale_x / 100.0)


def describe(name, row):
    """Print one style's font, size, scale and spacing, and return them."""
    fs      = num(row, "Fontsize")
    scale_x = num(row, "ScaleX", 100)
    scale_y = num(row, "ScaleY", 100)
    fsp     = num(row, "Spacing")
    font    = row.get("Fontname", "?")
    print(f"  {name:<5} {font:<12} fs {fs:<6g} "
          f"ScaleX {scale_x:<6g} ScaleY {scale_y:<6g} Spacing {fsp:g}")
    return fs, scale_x, scale_y, fsp, font


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    """Command-line entry point. See the module docstring for usage."""
    parser = argparse.ArgumentParser(
        description="Print the styles embedded in a track's .ass and the geometry they imply."
    )
    parser.add_argument("track_id", help="Track identifier")
    parser.add_argument("--subs", default=None, metavar="PATH",
                        help="Read this .ass instead of the track's _subs-final.ass")
    args = parser.parse_args()

    subs_path = args.subs or get_track_path(args.track_id, "subs-final.ass")

    try:
        styles = parse_styles(subs_path)
    except FileNotFoundError:
        print(f"  Could not read a subtitle file for {args.track_id}: {subs_path}")
        sys.exit(1)

    print(f"\n  Styles in {getattr(subs_path, 'name', subs_path)}:\n")

    missing = [s for s in ("Main", "BG") if s not in styles]
    if missing:
        print(f"  WARNING: no {' or '.join(missing)} style in this file. "
              f"Found: {', '.join(styles) or '(none)'}")
    for name in ("Main", "BG"):
        if name in styles:
            describe(name, styles[name])
    for name in styles:
        if name not in ("Main", "BG"):
            describe(name, styles[name])

    if "Main" not in styles:
        sys.exit(0)

    m_fs   = num(styles["Main"], "Fontsize")
    m_sx   = num(styles["Main"], "ScaleX", 100)
    m_sy   = num(styles["Main"], "ScaleY", 100)
    m_fsp  = num(styles["Main"], "Spacing")
    m_font = styles["Main"].get("Fontname", "?")

    if m_font.lower() != "consolas":
        print(f"\n  WARNING: advance arithmetic below assumes Consolas (monospace). "
              f"Main is {m_font} — treat every derived figure as indicative only.")

    adv     = px_per_char(m_fs, m_fsp, m_sx)
    glyph_h = m_fs * (m_sy / 100.0)

    print(f"\n  Derived geometry (v2 display, no upscale):\n")
    print(f"    Main lines         : fs {m_fs:g}  ({adv:.2f} px/char), glyph box {glyph_h:.0f}px")

    fits_edge   = int(FRAME_W // adv)
    fits_margin = int((FRAME_W - 2 * SIDE_MARGIN) // adv)
    print(f"    Chars per line     : {fits_edge} edge-to-edge, "
          f"{fits_margin} with {SIDE_MARGIN}px margins")
    print(f"      LONG_LINE_WARN   : {LONG_LINE_WARN}"
          f"  ({LONG_LINE_WARN * adv:.0f}px for {LONG_LINE_WARN} chars)")

    if LONG_LINE_WARN > fits_edge:
        over = LONG_LINE_WARN * adv - FRAME_W
        print(f"      *** LONG_LINE_WARN is TOO HIGH — a {LONG_LINE_WARN}-char line "
              f"overflows the frame by {over:.0f}px.")
        print(f"          Set it to {fits_margin} (margins kept) or {fits_edge} "
              f"(edge-to-edge) in whisperx_to_ass.py.")
    elif LONG_LINE_WARN < fits_margin - 4:
        print(f"      Note: LONG_LINE_WARN is conservative — {fits_margin} would "
              f"still fit inside the margins.")

    # The entry-cue dots sit left of the line; a full-width line pushes them off-frame.
    dots_w   = DOT_COUNT * DOT_SIZE + (DOT_COUNT - 1) * DOT_GAP + DOT_MARGIN
    max_with_dots = int((FRAME_W - 2 * dots_w) // adv)
    print(f"    Entry-cue dots     : need {dots_w}px left of the line → "
          f"{max_with_dots} chars keep them on-frame")

    # Stack rows
    row_gap = ROW_PITCH - glyph_h
    top_row = Y_ACTIVE - LINES_ABOVE * ROW_PITCH
    bot_row = Y_ACTIVE + LINES_BELOW * ROW_PITCH
    print(f"    Stack rows         : pitch {ROW_PITCH}px, {row_gap:.0f}px between glyph boxes; "
          f"rows span y={top_row:.0f}..{bot_row:.0f} (active {Y_ACTIVE})")
    if row_gap < ROW_GAP_FLOOR:
        print(f"      *** Rows are too tight — raise ROW_PITCH or lower Main fontsize.")
    if top_row - glyph_h / 2 < 0:
        print(f"      *** Top row leaves the frame — lower LINES_ABOVE or raise Y_ACTIVE.")
    if top_row - glyph_h / 2 < SAFE_TOP:
        print(f"      *** Top stack row (y={top_row - glyph_h / 2:.0f}) sits under the "
              f"player's title gradient — keep it below y={SAFE_TOP}.")

    if "BG" in styles:
        b_fs  = num(styles["BG"], "Fontsize")
        b_sx  = num(styles["BG"], "ScaleX", 100)
        b_sy  = num(styles["BG"], "ScaleY", 100)
        b_fsp = num(styles["BG"], "Spacing")

        # Two BG lines sung at once stack in two slots. The macro moves them
        # BG_STACK_SHIFT px towards each other, so the effective pitch is less.
        pitch      = math.floor(b_fs * BG_SLOT_SPACING_MULT)
        effective  = pitch - (2 * BG_STACK_SHIFT)
        bg_glyph_h = b_fs * (b_sy / 100.0)
        whitespace = effective - bg_glyph_h

        print(f"    BG lines           : fs {b_fs:g}  "
              f"({px_per_char(b_fs, b_fsp, b_sx):.2f} px/char) at y={Y_BG}")
        print(f"    BG stacked rows    : pitch {pitch}px, effective {effective}px "
              f"after 2x{BG_STACK_SHIFT}px shift, glyph box {bg_glyph_h:.1f}px")
        print(f"      Whitespace       : {whitespace:.1f}px  (floor ~{WHITESPACE_FLOOR}px)")

        if whitespace < WHITESPACE_FLOOR:
            need = math.ceil((bg_glyph_h + WHITESPACE_FLOOR - pitch) / -2)
            print(f"      *** Stacked BG rows are too close — descenders and ascenders "
                  f"will collide.")
            print(f"          Reduce BG_STACK_SHIFT to {max(0, need)} or lower, or "
                  f"raise BG_SLOT_SPACING_MULT.")

        # Clearance between the bottom stack row and the BG row
        bg_top     = Y_BG + BG_STACK_SHIFT - bg_glyph_h / 2   # stacked slot 1 is the higher case
        stack_bot  = Y_ACTIVE + (LINES_BELOW - BG_HIDE_ROWS) * ROW_PITCH + glyph_h / 2   # bottom row hidden while BG shows
        clearance  = bg_top - stack_bot
        print(f"    Stack → BG clear.  : {clearance:.0f}px  (floor ~{BG_CLEAR_FLOOR}px)")
        if clearance < BG_CLEAR_FLOOR:
            print(f"      *** Bottom stack row runs into the BG line — raise Y_BG, "
                  f"lower LINES_BELOW, or reduce ROW_PITCH.")
        bg_bottom = Y_BG + (BG_MAX_SLOTS - 1) * pitch - BG_STACK_SHIFT + bg_glyph_h / 2
        if bg_bottom > FRAME_H:
            print(f"      *** A stacked BG pair leaves the frame by {bg_bottom - FRAME_H:.0f}px.")
        if bg_bottom > SAFE_BOTTOM:
            print(f"      *** BG stack bottom (y={bg_bottom:.0f}) sits under the "
                  f"player's control bar — keep it above y={SAFE_BOTTOM}.")

    print(f"\n  Compare against the agreed style values in HOWTO.md. If they "
          f"disagree, one of the two has drifted: settle it before rendering.\n")


if __name__ == "__main__":
    main()
