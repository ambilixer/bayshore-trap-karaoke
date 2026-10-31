#!/usr/bin/env python3
# Created by Andy Charlie W. (ambilixer) in collaboration with Claude (Anthropic).
"""
score_timing.py

Measures karaoke timing in an .ass file: either against a hand-timed reference
(how much manual work the machine output would still need) or on its own (a
structural sanity check, ending in the export gate below).

Usage:
    python score_timing.py <track_id>                    # compare _subs.ass to _subs-timed.ass
    python score_timing.py <track_id> --check            # sanity-check _subs.ass alone
    python score_timing.py <track_id> --check --test subs-final.ass
    python score_timing.py <track_id> --test subs_new.ass --ref subs-timed.ass
    python score_timing.py <track_id> --test subs-tap.ass    # the tap tool's export
    python score_timing.py --files new.ass ref.ass       # any two files, no track layout

Why word onsets and not lines: a syllable's on-screen onset is the line start
plus the sum of the \\kf durations before it, so the onset is what the viewer
sees and what the manual pass in Aegisub is actually fixing. Every syllable of
a word is collapsed to that word's onset, because the two files rarely split
syllables the same way, and because word onsets are what any aligner can be
compared on.

Matching: the test and reference files are aligned as two word sequences
(difflib), so different line splits between them do not matter. A matched pair
more than MAX_PAIR_GAP seconds apart is treated as a mis-match of a repeated
lyric rather than a timing error, and dropped.

THE EXPORT GATE. Onset accuracy is not the same thing as a usable file. A
timing file can score well on onsets while placing a whole verse twice,
leaving another verse empty, keeping lines parked on a placeholder timestamp,
or ending most of its lines early. None of that is an onset error, so none of
it shows in the statistics. The check therefore ends with a verdict, and a
file headed for Aegisub, a tap export above all, must read READY:
    - no placeholder lines (whole \\k sum under 0.2s: WhisperX's parked lines)
    - no lines under MIN_LINE_DUR
    - no line starting more than ORDER_TOL before the line above it
    - every Comment / 0:00 / zero-length / empty-syllable check still clean
With a reference, compare() adds coverage (every reference line has a timed
counterpart within MAX_PAIR_GAP) and line length against the reference.
The machine's own _subs.ass will fail the gate on most tracks. That is correct:
it is the input to hand work, not something to feed onward.

Run the gate on the tap export, not on a finished _subs-timed.ass: the END
marker in a timing master is a Comment line, which the check counts as a hard
problem.

Reading the output:
    median |err|   the number to watch. Under ~50ms is "nudge the odd edge";
                   150ms+ is "retime the line".
    <=100ms        share of words that need no real work.
    by position    a median that walks steadily negative across the line means
                   durations are being emitted shorter than the onset-to-onset
                   distance, so the line drifts early. Flat is correct.
    line length    median test-minus-reference line duration. Near zero is
                   correct; clearly negative means lines end early.

What to expect from step 3's _subs.ass against a hand-timed reference: on a
track WhisperX aligns well, a median of 40 to 50ms with roughly 75 to 80% of
words within 100ms. A track whose lyrics repeat heavily can sit at 300ms or far
more, because WhisperX matches under half of its words. That is an alignment
problem, not a conversion one, and no change to the conversion will fix it.
"""

import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

try:
    from paths import get_track_path
except ImportError:      # usable outside the pipeline with --files
    get_track_path = None


# ── Configuration ─────────────────────────────────────────────────────────────

MAX_PAIR_GAP   = 5.0    # seconds; beyond this a "match" is a repeated lyric
LONG_LINE_WARN = 44     # keep in step with whisperx_to_ass.py
KSUM_TOLERANCE = 0.25   # seconds of disagreement between line length and \k sum
LONG_SYL_WARN  = 8.0    # seconds; a single syllable longer than this is suspect
PLACEHOLDER_KSUM = 0.2  # seconds; a whole line's \k sum under this is a parked line
MIN_LINE_DUR   = 0.2    # seconds; a sung line shorter than this is a defect
ORDER_TOL      = 0.5    # seconds a line may start before the one above it (BG leads)
COVER_MIN      = 0.5    # share of a reference line's words that must be matched

# A Dialogue or Comment line: Layer, Start, End, Style, Name (actor), the three
# margins (skipped), Effect, Text.
EVENT_RE = re.compile(
    r"^(Dialogue|Comment):\s*(\d+),([^,]+),([^,]+),([^,]*),([^,]*),"
    r"[^,]*,[^,]*,[^,]*,([^,]*),(.*)$"
)
TAG_RE = re.compile(r"\{([^}]*)\}")
K_RE   = re.compile(r"\\[kK][fo]?(\d+)")     # \k, \K, \kf, \ko: duration in centiseconds


# ── Parsing ───────────────────────────────────────────────────────────────────

def ass_to_seconds(stamp: str) -> float:
    """An .ass timestamp (H:MM:SS.cc) in seconds."""
    h, m, s = stamp.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def parse_events(path) -> list[dict]:
    """
    Parse the [Events] block into per-line dicts with per-word onsets.

    Each word gets the absolute onset of the syllable its first character falls
    in. A line with no \\k tags at all has one known onset, the line start.
    """
    events = []
    with open(path, encoding="utf-8-sig") as f:
        for raw in f:
            m = EVENT_RE.match(raw.rstrip("\r\n"))
            if not m:
                continue
            kind, _layer, start, end, style, actor, effect, text = m.groups()
            st, en = ass_to_seconds(start), ass_to_seconds(end)

            # Split the text into (duration_cs | None, text) syllables
            syllables, pos, cur, buf = [], 0, None, ""
            for tag in TAG_RE.finditer(text):
                buf += text[pos:tag.start()]
                k = K_RE.search(tag.group(1))
                if k:
                    if cur is not None or buf:
                        syllables.append((cur, buf))
                    buf, cur = "", int(k.group(1))
                pos = tag.end()
            syllables.append((cur, buf + text[pos:]))
            has_k = any(d is not None for d, _ in syllables)

            # Absolute onset per character
            chars, t = [], st
            for d, s in syllables:
                dur = (d or 0) / 100.0
                for ch in s:
                    chars.append((ch, t, t + dur))
                t += dur

            words, cw = [], None
            for ch, on, off in chars:
                if ch.isspace():
                    if cw:
                        words.append(cw)
                        cw = None
                    continue
                if cw is None:
                    cw = {"txt": ch, "on": on, "off": off}
                else:
                    cw["txt"] += ch
                    cw["off"] = off
            if cw:
                words.append(cw)

            if not has_k:
                for w in words:
                    w["on"] = None
                if words:
                    words[0]["on"] = st

            line_no = len(events)
            for i, w in enumerate(words):
                w["norm"]  = re.sub(r"[^\w']", "", w["txt"]).lower()
                w["pos"]   = i
                w["style"] = style
                w["line"]  = line_no

            events.append({
                "kind": kind, "start": st, "end": en, "style": style,
                "actor": actor, "effect": effect, "words": words,
                "has_k": has_k,
                "ksum": sum((d or 0) for d, _ in syllables) / 100.0,
                "syls": [(d, s) for d, s in syllables if d is not None],
                "plain": re.sub(r"\{[^}]*\}", "", text).strip(),
            })
    return events


# ── Statistics ────────────────────────────────────────────────────────────────

def median(xs):
    """Median of a list, or NaN for an empty one."""
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return float("nan")
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def percentile(xs, p):
    """The p-th percentile, interpolating linearly between neighbours."""
    xs = sorted(xs)
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def share(xs, limit):
    """Percentage of values at or under limit."""
    return 100.0 * sum(1 for x in xs if x <= limit) / len(xs) if xs else float("nan")


def summarise(label, errs):
    """Print one row of onset-error statistics for a list of signed errors."""
    if not errs:
        print(f"  {label:22s} no matched words")
        return
    a = [abs(e) for e in errs]
    print(f"  {label:22s} n={len(a):5d}  median {median(a)*1000:5.0f}ms  "
          f"p75 {percentile(a,75)*1000:5.0f}  p90 {percentile(a,90)*1000:6.0f}  "
          f"<=50ms {share(a,.05):3.0f}%  <=100ms {share(a,.1):3.0f}%  <=250ms {share(a,.25):3.0f}%  "
          f"signed median {median(errs)*1000:+5.0f}ms")


# ── Compare ───────────────────────────────────────────────────────────────────

def compare(test_path, ref_path):
    """Score test_path's word onsets against ref_path's and print the report.

    Returns (pairs, uncovered): the matched word pairs with their signed error,
    and the indices of reference lines left without a timed counterpart.
    """
    test, ref = parse_events(test_path), parse_events(ref_path)
    tw = [w for e in test for w in e["words"]]
    rw = [w for e in ref  for w in e["words"]]

    sm = SequenceMatcher(None, [w["norm"] for w in tw], [w["norm"] for w in rw], autojunk=False)
    pairs = []
    for block in sm.get_matching_blocks():
        for k in range(block.size):
            t, r = tw[block.a + k], rw[block.b + k]
            if t["on"] is None or r["on"] is None:
                continue
            if abs(t["on"] - r["on"]) > MAX_PAIR_GAP:
                continue          # repeated lyric matched to the wrong instance
            pairs.append((t, r, t["on"] - r["on"]))

    print(f"\nTEST {Path(test_path).name}   vs   REF {Path(ref_path).name}")
    print(f"  words: test {len(tw)}, reference {len(rw)}, matched with timing {len(pairs)} "
          f"({100.0*len(pairs)/len(rw):.0f}% of the reference)")

    errs = [e for _, _, e in pairs]
    summarise("onset error, all", errs)
    summarise("  Main lines", [e for t, r, e in pairs if r["style"] == "Main"])
    summarise("  BG lines",   [e for t, r, e in pairs if r["style"] == "BG"])
    summarise("  first word in line", [e for t, _, e in pairs if t["pos"] == 0])
    summarise("  later words",       [e for t, _, e in pairs if t["pos"] > 0])

    print("  signed error by word position in the test line (median ms):")
    for p in range(0, 9):
        sel = [e for t, _, e in pairs if (t["pos"] == p if p < 8 else t["pos"] >= 8)]
        if sel:
            print(f"    pos {p}{'+' if p == 8 else ' '}: {median(sel)*1000:+6.0f}   n={len(sel)}")

    # Coverage: a reference line is covered when most of its words found a
    # timed counterpart. An uncovered line is a hole in the render, for
    # instance a verse that was never tapped while another was placed twice.
    # Onset statistics cannot show that, because unmatched words simply drop
    # out of them.
    matched_per_line = {}
    line_pairs = {}
    for t, r, _ in pairs:
        matched_per_line[r["line"]] = matched_per_line.get(r["line"], 0) + 1
        line_pairs.setdefault(r["line"], {}).setdefault(t["line"], 0)
        line_pairs[r["line"]][t["line"]] += 1
    uncovered = [i for i, e in enumerate(ref)
                 if e["words"] and matched_per_line.get(i, 0) < COVER_MIN * len(e["words"])]
    print(f"  reference lines not covered          {len(uncovered):4d} of {len(ref)}")
    for i in uncovered[:8]:
        print(f"      {ref[i]['start']:8.2f}s  {ref[i]['plain'][:48]}")

    # Line length against the reference, for lines paired by majority vote.
    ddur = []
    for ri, tl in line_pairs.items():
        ti = max(tl, key=tl.get)
        ddur.append((test[ti]["end"] - test[ti]["start"]) - (ref[ri]["end"] - ref[ri]["start"]))
    if ddur:
        short = sum(1 for d in ddur if d < -0.1)
        print(f"  line length, test minus reference    median {median(ddur)*1000:+5.0f}ms   "
              f"{short} of {len(ddur)} lines shorter by >100ms")

    worst = sorted(pairs, key=lambda x: -abs(x[2]))[:8]
    if worst:
        print("  worst matched words:")
        for t, r, e in worst:
            print(f"    {e*1000:+7.0f}ms  {t['txt'][:20]:20s} test {t['on']:8.2f}s  ref {r['on']:8.2f}s")
    return pairs, uncovered


# ── Check a single file ───────────────────────────────────────────────────────

def check(path):
    """Run every structural check on one file, print the report and the export
    gate verdict. Returns (events, gate), gate being the problem count (0 = READY).
    """
    events = parse_events(path)
    print(f"\nCHECK {Path(path).name}: {len(events)} events")

    comments = [e for e in events if e["kind"] == "Comment"]
    zeros    = [e for e in events if e["start"] < 0.01]
    lowconf  = [e for e in events if e["effect"].strip()]
    nokara   = [e for e in events if not e["has_k"] and len(e["words"]) > 1]
    longln   = [e for e in events if len(e["plain"]) > LONG_LINE_WARN]
    zerodur  = [e for e in events if e["end"] - e["start"] <= 0.01]
    ksum_bad = [e for e in events
                if e["has_k"] and abs((e["end"] - e["start"]) - e["ksum"]) > KSUM_TOLERANCE]
    longsyl  = [(e, d) for e in events for d, _ in e["syls"] if d / 100.0 > LONG_SYL_WARN]
    emptysyl = [e for e in events for d, s in e["syls"] if not s.strip()]
    parked   = [e for e in events if e["has_k"] and e["ksum"] < PLACEHOLDER_KSUM]
    # A placeholder line is also short. Count it once, as a placeholder, so the
    # problem total is not inflated.
    shortln  = [e for e in events if 0.01 < e["end"] - e["start"] < MIN_LINE_DUR
                and not (e["has_k"] and e["ksum"] < PLACEHOLDER_KSUM)]
    disorder = [cur for prev, cur in zip(events, events[1:])
                if cur["start"] < prev["start"] - ORDER_TOL]

    def report(label, items, fmt=lambda e: f"{e['start']:8.2f}s  {e['plain'][:48]}"):
        print(f"  {label:36s} {len(items):4d}")
        for it in items[:6]:
            print(f"      {fmt(it)}")

    report("Comment events", comments)
    report("events starting at 0:00", zeros)
    report("events with an Effect marker", lowconf)
    report("multi-word lines with no \\k tags", nokara)
    report(f"lines over {LONG_LINE_WARN} chars", longln)
    report("zero-length events", zerodur)
    report(f"line length vs \\k sum off by >{KSUM_TOLERANCE}s", ksum_bad,
           fmt=lambda e: f"{e['start']:8.2f}s  line {e['end']-e['start']:6.2f}s  "
                         f"\\k {e['ksum']:6.2f}s  {e['plain'][:36]}")
    report(f"syllables over {LONG_SYL_WARN}s", longsyl,
           fmt=lambda p: f"{p[0]['start']:8.2f}s  {p[1]/100.0:6.2f}s  {p[0]['plain'][:36]}")
    report("empty syllables", emptysyl)
    report(f"placeholder lines (\\k sum <{PLACEHOLDER_KSUM}s)", parked)
    report(f"lines under {MIN_LINE_DUR}s", shortln,
           fmt=lambda e: f"{e['start']:8.2f}s  {e['end']-e['start']:5.2f}s  {e['plain'][:40]}")
    report(f"lines out of order (>{ORDER_TOL}s early)", disorder)

    bad = len(comments) + len(zeros) + len(zerodur) + len(emptysyl)
    gate = bad + len(parked) + len(shortln) + len(disorder)
    print(f"  --> {'clean' if bad == 0 else str(bad) + ' hard problems'}; "
          f"{len(ksum_bad)} timing disagreements, {len(lowconf)} flagged lines")
    print(f"  --> EXPORT GATE: {'READY' if gate == 0 else 'NOT READY (' + str(gate) + ' problems above)'}")
    return events, gate


# ── Entry point ───────────────────────────────────────────────────────────────

def resolve(track_id, suffix):
    """A track file's path, or exit if paths.py could not be imported."""
    if get_track_path is None:
        print("ERROR: paths.py not importable — use --files instead")
        sys.exit(1)
    return str(get_track_path(track_id, suffix))


if __name__ == "__main__":
    argv = sys.argv[1:]
    if not argv:
        # Print the Usage section of the module docstring.
        print(__doc__.split("Usage:")[1].split("Why word")[0].strip())
        sys.exit(1)

    do_check = "--check" in argv
    argv = [a for a in argv if a != "--check"]

    if argv and argv[0] == "--files":
        paths = argv[1:]
        if not paths:
            print("ERROR: --files needs at least one .ass path")
            sys.exit(1)
        if do_check or len(paths) == 1:
            for p in paths:
                check(p)
        else:
            check(paths[0])
            compare(paths[0], paths[1])
        sys.exit(0)

    track_id = argv[0]
    test_sfx = "subs.ass"
    ref_sfx  = "subs-timed.ass"
    i = 1
    while i < len(argv):
        if argv[i] == "--test" and i + 1 < len(argv):
            test_sfx = argv[i + 1]; i += 2
        elif argv[i] == "--ref" and i + 1 < len(argv):
            ref_sfx = argv[i + 1]; i += 2
        else:
            print(f"Unknown argument: {argv[i]}")
            sys.exit(1)

    test_path = resolve(track_id, test_sfx)
    if not Path(test_path).exists():
        print(f"ERROR: {test_path} not found")
        sys.exit(1)

    check(test_path)

    if not do_check:
        ref_path = resolve(track_id, ref_sfx)
        if not Path(ref_path).exists():
            print(f"\nNo reference at {ref_path} — nothing to compare against. "
                  f"Run with --check to skip this.")
            sys.exit(0)
        compare(test_path, ref_path)
