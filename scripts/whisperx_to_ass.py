#!/usr/bin/env python3
# Created by Andy Charlie W. (ambilixer) in collaboration with Claude (Anthropic).
"""
whisperx_to_ass.py

Step 3. Lines the track's lyrics up with WhisperX's word timestamps and writes
the first karaoke subtitle file, with a \\kf tag on every syllable. Run it
with the karaoke virtual environment's Python, which has pyphen;
run_pipeline.py does that for you.

Usage:
    python whisperx_to_ass.py <track_id>

    where <track_id> is the track identifier, e.g. jimi-hendrix_purple-haze.
    Files, in the track's folder:

        <track_id>_lyr.txt      lyrics
        <track_id>_whisp.json   WhisperX output, from step 2
        <track_id>_subs.ass     output: the tap tool's input

Lyrics format:
    - One lyric line per line. Keep each within LONG_LINE_WARN characters (the
      ruler initialize_track.py puts at the top of the file marks it); a
      longer line is split in two here, at the word nearest its middle.
    - Background/hype vocals wrapped in parentheses: (Yeah, let's go)
    - Section headers in square brackets, Genius style ([Verse 1], [Chorus],
      [Bridge]), are KEPT. Each header labels the next Main line: the label is
      written into that line's Actor (Name) field, and btk_karaoke_v2.lua reads
      it to insert a section gap and label into the scrolling stack. Headers
      are not lyric lines and are never rendered as text themselves. Anything
      after a colon in a header is dropped ([Chorus: Artist] becomes Chorus).
    - Lines starting with # are comments and are ignored.

Timing model:
    A syllable's on-screen onset is the line start plus the sum of the \\kf
    durations before it, so a duration is the distance to the NEXT onset, not
    the length of the sung sound. Emitting sung lengths instead discards every
    gap between words, and each line walks earlier as it goes, by about 50ms a
    word. Durations here run onset to onset, with a word's trailing gap carried
    by its last syllable, which is also how Aegisub's karaoke mode and the tap
    tool build them. On a track WhisperX aligns well, that puts the median word
    onset within 40 to 50ms of hand timing.

Lines with too few words matched are marked LOWCONF in the Effect field. Their
timing is interpolated from the lines around them, so expect to move them.
"""

import json
import re
import sys
from difflib import SequenceMatcher

from paths import get_track_path

import pyphen


# ── Configuration ─────────────────────────────────────────────────────────────

# Padding added to the end timestamp of each line (seconds)
LINE_END_PAD = 0.1

# A line is flagged if fewer than this fraction of its words matched WhisperX
FLAG_THRESHOLD_MAIN = 0.4
FLAG_THRESHOLD_BG   = 0.25  # Lower bar for BG: WhisperX often misses these

# Flagged lines are written as Dialogue carrying this marker in the Effect
# field, not as Comment events. The Lua macro skips Comment events, so a
# flagged line that was fixed but never switched back to Dialogue would vanish
# from the render. The marker is advisory: score_timing.py counts it, and the
# macro blanks the Effect field on every event it emits, so it never renders.
LOWCONF_EFFECT = "LOWCONF"

# Fallback word duration used when interpolation has no anchors at all (seconds)
FALLBACK_WORD_DUR = 0.15

# Ceiling on a word's duration when its timing was interpolated rather than
# matched (seconds). Without it, a line WhisperX missed entirely is stretched
# across the whole gap between the anchors either side of it: one word alone
# in a 20s gap becomes a 20s line. Beyond looking broken, an over-long line
# distorts the gaps the Lua macro measures from line end to line start, which
# is what BREAK_THRESHOLD tests, so a stretched line can swallow a real
# instrumental break. A run that would
# exceed the cap is laid out at the cap and centred in the gap: the true
# position is unknown, and the middle bounds the worst case.
INTERP_MAX_WORD_DUR = 0.6

# Lines longer than this are split in two (and warned about). Derived from the
# Main style, Consolas bold fs92 at ScaleX 90, drawn by the macro with \fsp
# -1.7: about 37.4 px per character. At 44 characters a cued line's entry-cue
# dots stay on frame (45 would still fit inside ~110px side margins). libass
# wraps at 49, and a wrapped line lands on top of its neighbour because the
# macro places every row with \pos. Mirrored in check_styles.py and
# score_timing.py, and marked by the ruler initialize_track.py seeds.
LONG_LINE_WARN = 44

# Syllabifier language
SYLLABLE_LANG = 'en_US'

# pyphen is a hyphenation dictionary, not a syllabifier, and its defaults refuse
# to break off the first or last two characters of a word, so "open", "every"
# and "ago" come back unsplit.
#
# left=1, right=1 gets those and also produces junk: "freaks" comes out as
# "f|reak|s". right=1 is simply wrong, since a single trailing consonant is
# never a syllable. left=1 is right only when the first character is a vowel,
# which is the case it is there for. So: ask for left=1, right=2, then drop any
# break after the first character of a consonant-initial word. See split_word().
HYPHENATOR = pyphen.Pyphen(lang=SYLLABLE_LANG, left=1, right=2)

# A leading one-character syllable is kept only for these. Not "y": at the
# start of a word it is a consonant ("you", "your", "yeah"), and including it
# produces "Y|ou" and "y|ou're".
LEADING_VOWELS = set("aeiouAEIOU")
# The vowel test for the no-vowel merge, where y IS a vowel ("ly", "my").
VOWELS = set("aeiouyAEIOUY")
# Never split inside these, which is what produced "re|al|ly". Costs the odd
# under-split ("create" stays whole), which is harmless; a wrong split cannot
# be tapped.
VOWEL_DIGRAPHS = {"ea", "ee", "oo", "ou", "oa", "ai"}

# Section header pattern: a whole line of the form [Anything]. Genius also
# emits "[Chorus: Artist]"; the part after the colon is dropped.
SECTION_RE = re.compile(r"^\[(.+)\]$")


# ── ASS header ────────────────────────────────────────────────────────────────
# Placeholder styles. The real Main and BG styles are applied in Aegisub's
# Style Manager after the timing pass, and replace these.

ASS_HEADER = """\
[Script Info]
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,Arial,72,&H00FFFFFF,&H0000FFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,2,0,0,0,1
Style: BG,Arial,52,&H00FFFFFF,&H0000FFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,2,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def normalize(word: str) -> str:
    """Lowercase and strip punctuation for matching purposes."""
    return re.sub(r"[^\w']", "", word).lower()

def strip_tags(text: str) -> str:
    """Strip ASS override tags for character length measurement."""
    return re.sub(r"\{[^}]*\}", "", text).strip()

def seconds_to_ass(t: float) -> str:
    """Convert seconds to ASS timestamp format H:MM:SS.cc"""
    cs_total = int(round(max(0.0, t) * 100))   # round to centiseconds first, so
    h, rem = divmod(cs_total, 360000)          # 59.996s carries to 1:00.00
    m, rem = divmod(rem, 6000)
    s, cs  = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


# ── Parsing ───────────────────────────────────────────────────────────────────

def parse_lyrics(path: str) -> list[list]:
    """
    Returns a list of [style, raw_words, section] entries.
      - Lines wrapped in () → style 'BG'
      - All other non-empty lines → style 'Main'
      - [Section] headers are consumed and attached to the NEXT Main line as its
        `section` label (None on every other line). A header followed only by BG
        lines carries over until a Main line arrives.
      - Lines starting with # are ignored (initialize_track.py seeds three).
      - A header's label stops at the first colon.
    """
    lines = []
    pending_section = None
    with open(path, encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            m = SECTION_RE.match(line)
            if m:
                pending_section = m.group(1).split(":")[0].strip()
                continue
            if line.startswith("(") and line.endswith(")"):
                style = "BG"
                text  = line[1:-1].strip()
            else:
                style = "Main"
                text  = line
            words = text.split()
            if words:
                section = None
                if style == "Main" and pending_section:
                    section = pending_section
                    pending_section = None
                lines.append([style, words, section])
    if pending_section:
        print(f"  WARNING: section header [{pending_section}] at end of file has no line after it — dropped")
    return lines


def parse_whisperx(path: str) -> list[dict]:
    """
    Returns a flat list of word dicts: {word, norm, start, end, score}
    Skips any word entry that lacks start/end (WhisperX occasionally omits these).
    """
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)

    words = []
    for seg in data.get("segments", []):
        for w in seg.get("words", []):
            if "start" not in w or "end" not in w:
                continue
            words.append({
                "word":  w["word"],
                "norm":  normalize(w["word"]),
                "start": float(w["start"]),
                "end":   float(w["end"]),
                "score": float(w.get("score", 0.0)),
            })
    return words


# ── Alignment ─────────────────────────────────────────────────────────────────

def interpolate_all(aligned: list, flat_index: list) -> list:
    """
    Fill in every word that got no timestamp from the nearest matched anchors,
    searching ACROSS line boundaries rather than only inside the line.

    Searching across lines matters: a line where nothing matched has no
    anchors of its own, and would otherwise be laid out from 0:00. Neighbouring
    lines are anchors too; a run placed between them lands in roughly the right
    bar, which is a nudge rather than a rebuild. Those lines are still flagged.

    A run is spread evenly across the gap unless that would give each word more
    than INTERP_MAX_WORD_DUR, in which case it is laid out at that rate and
    centred in the gap instead of stretched across it.

    flat_index is the (line_idx, word_idx) sequence in lyric order.
    """
    n = len(flat_index)
    i = 0
    while i < n:
        li, wi = flat_index[i]
        if aligned[li][1][wi][3]:      # matched
            i += 1
            continue

        gap_start = i
        gap_end   = i
        while gap_end < n and not aligned[flat_index[gap_end][0]][1][flat_index[gap_end][1]][3]:
            gap_end += 1
        gap_count = gap_end - gap_start

        if gap_start > 0:
            pl, pw   = flat_index[gap_start - 1]
            prev_end = aligned[pl][1][pw][2]
        else:
            prev_end = None
        if gap_end < n:
            nl, nw     = flat_index[gap_end]
            next_start = aligned[nl][1][nw][1]
        else:
            next_start = None

        if prev_end is None and next_start is None:
            # Nothing matched anywhere in the track: lay out sequentially from 0
            cursor = 0.0
            for k in range(gap_count):
                li, wi = flat_index[gap_start + k]
                raw = aligned[li][1][wi][0]
                aligned[li][1][wi] = (raw, cursor, cursor + FALLBACK_WORD_DUR, False)
                cursor += FALLBACK_WORD_DUR
        else:
            if prev_end is None:
                prev_end   = max(0.0, next_start - gap_count * FALLBACK_WORD_DUR)
            if next_start is None:
                next_start = prev_end + gap_count * FALLBACK_WORD_DUR
            slot   = (next_start - prev_end) / gap_count
            cursor = prev_end
            if slot > INTERP_MAX_WORD_DUR:
                slot   = INTERP_MAX_WORD_DUR
                cursor = prev_end + ((next_start - prev_end) - gap_count * slot) / 2
            for k in range(gap_count):
                li, wi = flat_index[gap_start + k]
                raw = aligned[li][1][wi][0]
                aligned[li][1][wi] = (raw, cursor + k * slot, cursor + (k + 1) * slot, False)

        i = gap_end

    return aligned


def align(lyric_lines: list, wx_words: list) -> list:
    """
    Sequence-align all lyric words (flattened) against WhisperX words,
    transfer timestamps, interpolate gaps, and flag low-confidence lines.

    Returns list of [style, word_data, flagged, section] where:
        word_data = [(raw_word, start, end, matched), ...]
        flagged   = bool
        section   = str | None  (label for the first line of a section)
    """
    # Build flat lyric word list: (line_idx, word_idx, normalized)
    flat_lyric = []
    for li, (style, words, _section) in enumerate(lyric_lines):
        for wi, w in enumerate(words):
            flat_lyric.append((li, wi, normalize(w)))

    wx_norms  = [w["norm"] for w in wx_words]
    ly_norms  = [t[2]      for t in flat_lyric]

    # Sequence alignment
    sm     = SequenceMatcher(None, ly_norms, wx_norms, autojunk=False)
    blocks = sm.get_matching_blocks()

    # Map: lyric flat index → wx index
    ly_to_wx = {}
    for block in blocks:
        for offset in range(block.size):
            ly_to_wx[block.a + offset] = block.b + offset

    # Initialise result structure
    result = []
    for style, words, section in lyric_lines:
        word_data = [(w, None, None, False) for w in words]
        result.append([style, word_data, False, section])

    # Fill matched timestamps
    for flat_idx, (li, wi, _) in enumerate(flat_lyric):
        if flat_idx in ly_to_wx:
            wx = wx_words[ly_to_wx[flat_idx]]
            raw = result[li][1][wi][0]
            result[li][1][wi] = (raw, wx["start"], wx["end"], True)

    # Flag BEFORE interpolating: interpolation fabricates timestamps, so the
    # match count has to be taken from the real ones.
    for li, (style, _words, _section) in enumerate(lyric_lines):
        matched_count = sum(1 for _, _, _, m in result[li][1] if m)
        total         = len(result[li][1])
        threshold     = FLAG_THRESHOLD_BG if style == "BG" else FLAG_THRESHOLD_MAIN
        if total > 0 and (matched_count / total) < threshold:
            result[li][2] = True

    result = interpolate_all(result, [(li, wi) for li, wi, _ in flat_lyric])

    return result



# ── BG pre-placement ──────────────────────────────────────────────────────────

BG_FALLBACK_DUR  = 2.0   # seconds allocated per BG line at start/end of song
BG_START_OFFSET  = -0.2  # shift placed BG lines earlier, against WhisperX's timestamp overshoot

def get_line_bounds(word_data: list) -> tuple[float, float] | None:
    """Return (start, end) of a line from its word data, or None if no timestamps."""
    times = [(s, e) for (_, s, e, _) in word_data if s is not None and e is not None]
    if not times:
        return None
    return times[0][0], times[-1][1]


def assign_line_timestamps(word_data: list, start: float, end: float) -> list:
    """Distribute a time range equally across all words in a line."""
    n = len(word_data)
    if n == 0:
        return word_data
    slot = (end - start) / n
    return [(raw, start + i * slot, start + (i + 1) * slot, matched)
            for i, (raw, _, _, matched) in enumerate(word_data)]


def preplace_bg_lines(aligned: list) -> list:
    """
    Assign rough timestamps to runs of BG lines that WhisperX missed entirely.

    Rules:
      - A run where ANY word matched WhisperX is left alone, so real alignment
        data always wins over an even division of the gap.
      - Normal (between two Main lines): divide the available time equally
      - BG line(s) at the start: iterate backwards from first Main line start,
        allocating BG_FALLBACK_DUR seconds per line
      - BG line(s) at the end: iterate forwards from last Main line end,
        allocating BG_FALLBACK_DUR seconds per line
      - A run this function places is unflagged: its timing is fabricated but
        it is ready to nudge, which is the same bar the rest of the file is at.
      - BG_START_OFFSET shifts the whole line, so it applies to both bounds;
        applied to the start alone it would stretch the line, not move it.
    """
    n = len(aligned)

    # Collect Main line bounds indexed by position
    main_bounds = {}
    for i, entry in enumerate(aligned):
        style, word_data = entry[0], entry[1]
        if style == "Main":
            bounds = get_line_bounds(word_data)
            if bounds:
                main_bounds[i] = bounds

    if not main_bounds:
        return aligned  # nothing to anchor to

    # Find runs of BG lines and assign timestamps
    i = 0
    while i < n:
        style = aligned[i][0]
        if style != "BG":
            i += 1
            continue

        # Collect this run of consecutive BG lines
        run_start = i
        while i < n and aligned[i][0] == "BG":
            i += 1
        run_end = i  # exclusive
        run_len = run_end - run_start

        # Anything in this run that WhisperX actually matched stays as it is
        if any(matched
               for li in range(run_start, run_end)
               for (_, _, _, matched) in aligned[li][1]):
            continue

        # Find surrounding Main anchors
        prev_main = max((k for k in main_bounds if k < run_start), default=None)
        next_main = min((k for k in main_bounds if k >= run_end),  default=None)

        if prev_main is None and next_main is None:
            # No anchors at all: leave as-is
            continue

        elif prev_main is None:
            # BG lines at the start: iterate backwards from first Main start
            anchor = main_bounds[next_main][0]
            for j, li in enumerate(reversed(range(run_start, run_end))):
                end   = anchor - j * BG_FALLBACK_DUR
                start = end - BG_FALLBACK_DUR
                aligned[li][1] = assign_line_timestamps(aligned[li][1], max(0.0, start + BG_START_OFFSET), max(0.0, end + BG_START_OFFSET))
                aligned[li][2] = False  # unflag: pre-placed, ready to nudge

        elif next_main is None:
            # BG lines at the end: iterate forwards from last Main end
            anchor = main_bounds[prev_main][1]
            for j, li in enumerate(range(run_start, run_end)):
                start = anchor + j * BG_FALLBACK_DUR
                end   = start + BG_FALLBACK_DUR
                aligned[li][1] = assign_line_timestamps(aligned[li][1], max(0.0, start + BG_START_OFFSET), max(0.0, end + BG_START_OFFSET))
                aligned[li][2] = False

        else:
            # BG lines between two Main lines: divide available time equally
            seg_start = main_bounds[prev_main][1]
            seg_end   = main_bounds[next_main][0]
            slot      = (seg_end - seg_start) / run_len
            for j, li in enumerate(range(run_start, run_end)):
                start = seg_start + j * slot
                end   = seg_start + (j + 1) * slot
                aligned[li][1] = assign_line_timestamps(aligned[li][1], max(0.0, start + BG_START_OFFSET), max(0.0, end + BG_START_OFFSET))
                aligned[li][2] = False

    return aligned

# ── split long lines ────────────────────────────────────────────────────────────

def split_long_lines(aligned: list) -> list:
    """
    Split any line exceeding LONG_LINE_WARN characters at the word boundary
    closest to the midpoint. Timing is preserved per-word across the split.
    The section label, if any, stays on the first half.

    A line is split at most once, so one over twice the limit leaves two halves
    that are both still too long; the entry point warns about those.
    """
    result = []
    for style, word_data, flagged, section in aligned:
        plain = ' '.join(w[0] for w in word_data)
        if len(plain) <= LONG_LINE_WARN or len(word_data) <= 1:
            result.append([style, word_data, flagged, section])
            continue

        # Find word boundary closest to character midpoint
        mid_char  = len(plain) // 2
        cumulative = 0
        best_split = len(word_data) // 2
        best_dist  = float('inf')
        for i in range(1, len(word_data)):
            cumulative += len(word_data[i - 1][0]) + 1
            dist = abs(cumulative - mid_char)
            if dist < best_dist:
                best_dist  = dist
                best_split = i

        result.append([style, word_data[:best_split], flagged, section])
        result.append([style, word_data[best_split:], flagged, None])

    return result

# ── ASS output ────────────────────────────────────────────────────────────────

def split_word(word: str) -> list[str]:
    """
    Hyphenation points for one word, minus the ones that are not syllables.

    Dropped: a break after the first character unless that character is a
    vowel, which stops "freaks" becoming "f|reak|s" while still allowing
    "o|pen" and "a|go"; any break inside a VOWEL_DIGRAPHS pair; and, through
    right=2 on the hyphenator, any break that would split off a lone trailing
    consonant. Any fragment left with no vowel is merged into its neighbour.
    """
    if len(word) < 2:
        return [word]
    keep = [p for p in HYPHENATOR.positions(word)
            if (p > 1 or word[0] in LEADING_VOWELS) and len(word) - p >= 2
            and word[p - 1:p + 1].lower() not in VOWEL_DIGRAPHS]
    syllables, prev = [], 0
    for p in keep:
        if p > prev:
            syllables.append(word[prev:p])
            prev = p
    syllables.append(word[prev:])
    syllables = [s for s in syllables if s]

    # A syllable with no vowel in it is not a syllable. Merge any such fragment
    # into its neighbour. This is the backstop that catches whatever the
    # hyphenation patterns offer that the rules above did not anticipate.
    merged = []
    for s in syllables:
        if merged and not any(c in VOWELS for c in s):
            merged[-1] += s
        elif merged and not any(c in VOWELS for c in merged[-1]):
            merged[-1] += s
        else:
            merged.append(s)
    return merged or [word]


def syllabify(core: str) -> list[str]:
    """Split a word core into syllables, keeping any internal hyphens."""
    parts = core.split('-')
    syllables = []
    for pi, part in enumerate(parts):
        if part == "":
            # A trailing or doubled hyphen ("Wo-", "weird-") splits to an empty
            # part. As a syllable it would hold nothing but the word's trailing
            # space: a stray {\kf1} in the file, an extra invisible syllable to
            # tap, and a plain-text mismatch ("Wo- ah" for "Wo-ah"). So it emits
            # nothing, and the hyphen stays on the previous syllable.
            continue
        part_syls = split_word(part)
        if pi < len(parts) - 1:
            part_syls[-1] = part_syls[-1] + '-'
        syllables.extend(part_syls)
    return syllables or [core]


def build_ass(aligned: list, output_path: str) -> None:
    """
    Write the ASS file.

    Karaoke timing: a \\kf duration is the distance from one syllable's onset to
    the next one's, so each word runs to the NEXT word's onset and carries its
    trailing gap on its last syllable. Within the sung part of a word,
    syllables share the time in proportion to their length in characters. That
    is a crude proxy, and the reason syllable timing is corrected by hand (tap
    tool, then Aegisub), but the word onsets are exactly the ones WhisperX
    found.

    Durations are emitted as the difference of two rounded absolute offsets, so
    per-syllable rounding cannot accumulate along a line.

    A section label is written into the Actor (Name) field of the section's
    first Main line. Low-confidence lines stay Dialogue and are marked in the
    Effect field with LOWCONF_EFFECT.
    """
    dialogue_lines = []
    flagged_lines  = []

    for li, (style, word_data, flagged, section) in enumerate(aligned):
        timestamps = [(s, e) for (_, s, e, _) in word_data if s is not None and e is not None]
        if not timestamps:
            print(f"  SKIP line {li+1}: no timestamps at all (check lyrics/audio alignment)")
            continue

        line_start = timestamps[0][0]

        # Absolute onset of every syllable, plus the line's final boundary
        bounds, texts = [], []
        for wi, (raw, start, end, _matched) in enumerate(word_data):
            if start is None:
                continue
            word_end  = end if end is not None else start + FALLBACK_WORD_DUR
            nxt_start = None
            for (_, s2, _, _) in word_data[wi + 1:]:
                if s2 is not None:
                    nxt_start = s2
                    break
            # onset-to-onset; never run past the next word, never run backwards
            stop     = nxt_start if (nxt_start is not None and nxt_start > start) else word_end
            sung_end = min(word_end, stop) if word_end > start else stop

            # Strip leading/trailing punctuation for syllabification
            stripped  = raw.strip()
            lead      = len(stripped) - len(stripped.lstrip("\"'("))
            trail     = len(stripped) - len(stripped.rstrip("\"'),.!?;:"))
            core      = stripped[lead:len(stripped) - trail] if trail else stripped[lead:]
            lead_str  = stripped[:lead]
            trail_str = stripped[len(stripped) - trail:] if trail else ""

            syllables = syllabify(core)
            if not syllables or not any(syllables):
                syllables = [stripped]
                lead_str  = ""
                trail_str = ""

            syl_lens  = [max(1, len(s)) for s in syllables]
            total_len = sum(syl_lens)

            for idx, syl in enumerate(syllables):
                is_last = idx == len(syllables) - 1
                bounds.append(start if idx == 0
                              else start + (sung_end - start) * sum(syl_lens[:idx]) / total_len)
                prefix = lead_str  if idx == 0 else ""
                suffix = trail_str if is_last  else ""
                texts.append(f"{prefix}{syl}{suffix}" + (" " if is_last else ""))

        last_end = max(e for (_s, e) in timestamps)
        line_end = max(last_end, bounds[-1] + 0.01)
        bounds.append(line_end)

        # Differences of rounded offsets: no cumulative rounding drift
        offsets = [int(round((b - line_start) * 100)) for b in bounds]
        ktext = "".join(
            "{\\kf%d}%s" % (max(1, offsets[i + 1] - offsets[i]), txt)
            for i, txt in enumerate(texts)
        ).rstrip()

        plain_len = len(strip_tags(ktext))
        if plain_len > LONG_LINE_WARN:
            print(f"  WARNING: line {li+1} is {plain_len} chars (>{LONG_LINE_WARN}) — may wrap in video: {ktext[:60]}...")

        layer  = 0
        actor  = (section or "").replace(",", "")   # a comma would split the field
        effect = LOWCONF_EFFECT if flagged else ""

        line = (f"Dialogue: {layer},"
                f"{seconds_to_ass(line_start)},"
                f"{seconds_to_ass(line_end + LINE_END_PAD)},"
                f"{style},{actor},0,0,0,{effect},"
                f"{ktext}\n")

        dialogue_lines.append(line)
        if flagged:
            flagged_lines.append(li + 1)

    with open(output_path, "w", encoding="utf-8-sig") as f:
        f.write(ASS_HEADER)
        f.writelines(dialogue_lines)

    print(f"\nDone → {output_path}")
    print(f"  Total lines:   {len(aligned)}")
    print(f"  Written:       {len(dialogue_lines)}")
    if flagged_lines:
        print(f"  Flagged (low confidence, Effect={LOWCONF_EFFECT}): lines {flagged_lines}")
        print("  → In Aegisub: sort or filter on the Effect column to find them. Their timing is")
        print("    interpolated from neighbouring lines, so expect to move them, not build them.")
    else:
        print("  No lines flagged.")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python whisperx_to_ass.py <track_id>")
        print("  e.g. python whisperx_to_ass.py jimi-hendrix_purple-haze")
        print("  Reads:  tracks/<track_id>/<track_id>_lyr.txt")
        print("          tracks/<track_id>/<track_id>_whisp.json")
        print("  Writes: tracks/<track_id>/<track_id>_subs.ass")
        sys.exit(1)

    track_id        = sys.argv[1]
    lyrics_path     = str(get_track_path(track_id, "lyr.txt"))
    wx_json_path    = str(get_track_path(track_id, "whisp.json"))
    output_ass_path = str(get_track_path(track_id, "subs.ass"))

    print("Parsing WhisperX output...")
    wx_words = parse_whisperx(wx_json_path)
    print(f"  {len(wx_words)} words with timestamps")

    print("Parsing lyrics...")
    lyric_lines = parse_lyrics(lyrics_path)
    main_count = sum(1 for s, _, _ in lyric_lines if s == "Main")
    bg_count   = sum(1 for s, _, _ in lyric_lines if s == "BG")
    sections   = [sec for _, _, sec in lyric_lines if sec]
    print(f"  {len(lyric_lines)} lines ({main_count} Main, {bg_count} BG)")
    if sections:
        print(f"  {len(sections)} sections: {', '.join(sections)}")
    else:
        print("  No [Section] headers found — the v2 display will show no section gaps.")

    print("Aligning...")
    aligned = align(lyric_lines, wx_words)

    print("Pre-placing BG lines...")
    aligned = preplace_bg_lines(aligned)

    print("Splitting long lines...")
    aligned = split_long_lines(aligned)
    long_count = sum(1 for entry in aligned if len(' '.join(w[0] for w in entry[1])) > LONG_LINE_WARN)
    if long_count:
        print(f"  WARNING: {long_count} lines still exceed {LONG_LINE_WARN} chars after splitting — may need manual split")
    else:
        print("  All lines within limit.")

    print("Writing ASS...")
    build_ass(aligned, output_ass_path)
