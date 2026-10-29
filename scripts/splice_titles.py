#!/usr/bin/env python3
# Created by Andy Charlie W. (ambilixer) in collaboration with Claude (Anthropic).
"""
splice_titles.py

Step 6. Joins the front title card, the karaoke video and the end title card
into the final video, with optional trimming, bleed and lead-in (all described
below, and in the BLEED and LEAD-IN notes in the code).

Usage:
    python splice_titles.py <track_id> [options]

Options:
    --trim-start            Auto-trim leading silence from the karaoke video
    --trim-end              Auto-trim trailing silence from the karaoke video
    --trim-end-at SECONDS   Manually trim the karaoke video end at this timestamp
    --bleed SECONDS         Instrumental runs under the tail of the front card and
                            under the head of the end card by this many seconds
    --bleed-in SECONDS      Override the front-card bleed only
    --bleed-out SECONDS     Override the end-card bleed only
    --lead-in SECONDS       Freeze the karaoke's first frame for this long, silent,
                            before the music starts (for tracks whose first lyric
                            arrives too soon for the singer to get ready)
    --overwrite             Replace an existing _final.mp4 (refuses without this)
    --dry-run               Print the FFmpeg command without executing

Expects, in the track's folder:
    <track_id>_kar.mp4            karaoke video, from karaoke_render.py
    <track_id>_titles_front.mp4   front title card, from render_titles.py
    <track_id>_titles_end.mp4     end title card, from render_titles.py

Output:
    <track_id>_final.mp4

Nothing is trimmed unless asked. --trim-end's silence detection is not
reliable enough to run unattended; the dependable way to find a trim point is
to list every silence in the karaoke video and read it off:

    ffmpeg -i <track_id>_kar.mp4 -af silencedetect=n=-30dB:d=1 -f null -

then pass it with --trim-end-at.

The segments are joined without converting frame rates, so the karaoke video
and the title cards must share one (FPS in karaoke_render.py and
render_titles.py).
"""


import argparse
import re
import subprocess
import sys

from paths import get_track_path


# ── SILENCE TRIMMING ──────────────────────────────────────────────────────────

SILENCE_THRESHOLD_DB = -30    # dB ceiling for silence detection
SILENCE_MIN_DUR      = 1.0    # minimum continuous seconds to count as silence
SILENCE_MAX_TRIM     = 10.0   # safety cap: never trim more than this
SILENCE_TRIM_BUFFER  = 0.3    # seconds kept after the detected silence start; raise if the cut sounds abrupt


# ── BLEED ─────────────────────────────────────────────────────────────────────
#
# Bleed lets the instrumental run underneath the title cards instead of cutting
# in and out with them.
#
# The karaoke audio and video always stay locked to each other. The bleed is
# achieved by leaving the title card ON SCREEN LONGER, never by sliding the
# audio out from under its own picture (which would desync the lyrics). With
# BI = bleed-in, BO = bleed-out, F = front card duration, K = karaoke duration:
#
#   video:  [ front card, full F ][ karaoke, BI..(K-BO) ][ end card, full ]
#   audio:  [ front card, 0..F-BI ][ karaoke, 0..K      ][ end card, BO.. ]
#
# so the karaoke audio starts BI seconds before the front card leaves the
# screen, and keeps playing for BO seconds after the end card arrives. Both
# tracks stay the same total length; only the segment boundaries differ, which
# is why video and audio are concatenated separately below.
#
# The cost is that the first BI and last BO seconds of the karaoke PICTURE are
# never seen: they play under the cards. Keep bleed values inside the
# instrumental head/tail; anything large enough to swallow a sung line will
# hide that line.
#
# Note that --bleed-in is only audible if the karaoke audio actually starts on
# music. If the render opens with silence and --trim-start was not passed, the
# bleed window is just more silence.
#
# A track that opens on a long instrumental break shows a countdown from its
# first frame. Bleed-in hides the first BI seconds of picture, so the countdown
# would appear already part way through. Use --bleed-in 0 on those tracks: there
# is no musical entry to smooth anyway.

MIN_VISIBLE_KAR = 1.0    # seconds of karaoke picture that must survive the bleed


# ── LEAD-IN ───────────────────────────────────────────────────────────────────
#
# Some tracks open on a lyric within a second or two, leaving the singer no
# time between the front card lifting and the first line. --lead-in L holds
# the karaoke's FIRST FRAME on screen for L seconds with no audio before the
# music starts. That frame is the lyric stack at rest (line 1 in the active
# row, the next lines below), which is exactly what a singer wants to look at while
# getting ready. Nothing in the karaoke picture or audio is shifted relative
# to each other; a silent, frozen L seconds is inserted in front of both.
#
#   video:  [ front card ][ kar frame 0, held L ][ karaoke ... ][ end card ]
#   audio:  [ front card ][ silence, L          ][ karaoke ... ][ end card ]
#
# The pad is applied AFTER the trim, so --trim-end-at values are unchanged.
# --bleed-in is refused alongside it: the card would be covering a frozen,
# silent frame, which achieves nothing. --bleed-out is unaffected.
#
# Implemented with tpad (start_mode=clone) on the video chain and adelay on the
# audio chain, both inside the existing per-stream filter graphs.


# ── PROBING ───────────────────────────────────────────────────────────────────

def ffprobe_field(path, stream_type, field):
    """One stream field from ffprobe as a string, e.g. ("a:0", "sample_rate")."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", stream_type,
        "-show_entries", f"stream={field}",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout.strip()


def has_audio_stream(path):
    """True if the file has an audio stream."""
    return ffprobe_field(path, "a:0", "codec_type") == "audio"


def get_audio_params(path):
    """(sample_rate, channels) of the first audio stream, as strings, defaulting to 48000 and 2."""
    sr = ffprobe_field(path, "a:0", "sample_rate") or "48000"
    ch = ffprobe_field(path, "a:0", "channels") or "2"
    return sr, ch


def get_duration(path):
    """
    Duration in seconds. Prefers the video stream, falling back to the container,
    because stream=duration reports N/A on some MP4 muxes. Returns None if neither
    can be read.
    """
    value = ffprobe_field(path, "v:0", "duration")
    if not value or value == "N/A":
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path)
        ]
        value = subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ── TRIM DETECTION ────────────────────────────────────────────────────────────

def detect_trim_points(path, trim_start, trim_end):
    """
    Find trim points from ffmpeg's silencedetect, printing every silence found.

    Returns (start_offset, end_offset). start_offset is where a silence at the
    very start of the file ends (0.0 if there is none); end_offset is the start
    of a silence that runs to the end of the file, plus SILENCE_TRIM_BUFFER, or
    None. Neither trims more than SILENCE_MAX_TRIM seconds.
    """
    if not (trim_start or trim_end):
        return 0.0, None

    cmd = [
        "ffmpeg", "-i", str(path),
        "-af", f"silencedetect=noise={SILENCE_THRESHOLD_DB}dB:duration={SILENCE_MIN_DUR}",
        "-f", "null", "-"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    stderr = result.stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", stderr)]
    ends   = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", stderr)]
    dur    = get_duration(path)

    print(f"\n  [silencedetect raw results]")
    print(f"  Video duration : {dur:.3f}s" if dur else "  Video duration : unknown")
    if not starts:
        print(f"  No silence intervals detected above threshold "
              f"({SILENCE_THRESHOLD_DB}dB / {SILENCE_MIN_DUR}s min)")
    else:
        for i, s in enumerate(starts):
            e = ends[i] if i < len(ends) else None
            if e:
                print(f"  Silence {i+1}: {s:.3f}s → {e:.3f}s  (duration: {e-s:.3f}s)")
            else:
                print(f"  Silence {i+1}: {s:.3f}s → end of file")
    print()

    start_offset = 0.0
    end_offset   = None

    if trim_start and starts and starts[0] < 0.05:
        amt          = min(ends[0] if ends else SILENCE_MAX_TRIM, SILENCE_MAX_TRIM)
        start_offset = amt
        print(f"  Trim start: {amt:.3f}s")

    if trim_end and starts:
        last_start = starts[-1]
        if dur and (dur - last_start) <= SILENCE_MAX_TRIM:
            amt        = dur - last_start
            end_offset = last_start + SILENCE_TRIM_BUFFER
            print(f"  Trim end: {amt:.3f}s (from {last_start:.3f}s to {dur:.3f}s)")
        else:
            print(f"  Trim end: no end-adjacent silence found")
    elif trim_end:
        print(f"  Trim end: no silence detected at threshold {SILENCE_THRESHOLD_DB}dB")

    return start_offset, end_offset


# ── BLEED RESOLUTION ──────────────────────────────────────────────────────────

def resolve_bleed(bleed, bleed_in, bleed_out):
    """--bleed sets both ends; --bleed-in / --bleed-out override individually."""
    both = bleed if bleed is not None else 0.0
    return (both if bleed_in  is None else bleed_in,
            both if bleed_out is None else bleed_out)


def validate_bleed(bleed_in, bleed_out, front_dur, end_dur, video_start, video_end,
                   lead_in=0.0):
    """Return a list of human-readable problems; empty means good to go."""
    errors = []

    if bleed_in < 0 or bleed_out < 0:
        errors.append(f"Bleed values must be >= 0 (got in={bleed_in}, out={bleed_out}).")

    if lead_in < 0:
        errors.append(f"--lead-in must be >= 0 (got {lead_in}).")

    if lead_in > 0 and bleed_in > 0:
        errors.append(f"--lead-in ({lead_in:.3f}s) and --bleed-in ({bleed_in:.3f}s) "
                      f"cannot be combined: the card would cover a frozen, silent "
                      f"frame. Pass --bleed-in 0 to keep --bleed-out.")

    if bleed_in > 0:
        if front_dur is None:
            errors.append("Could not read the front title card duration; cannot apply --bleed-in.")
        elif bleed_in >= front_dur:
            errors.append(f"--bleed-in ({bleed_in:.3f}s) must be shorter than the "
                          f"front title card ({front_dur:.3f}s).")

    if bleed_out > 0:
        if end_dur is None:
            errors.append("Could not read the end title card duration; cannot apply --bleed-out.")
        elif bleed_out >= end_dur:
            errors.append(f"--bleed-out ({bleed_out:.3f}s) must be shorter than the "
                          f"end title card ({end_dur:.3f}s).")

    if video_end is not None:
        visible = video_end - video_start
        if visible < MIN_VISIBLE_KAR:
            errors.append(f"Bleed leaves only {visible:.3f}s of karaoke picture visible "
                          f"(minimum {MIN_VISIBLE_KAR:.1f}s). Reduce the bleed or the trim.")

    return errors


# ── COMMAND ───────────────────────────────────────────────────────────────────

def fmt(value):
    """A time in seconds, to the millisecond, for an ffmpeg filter argument."""
    return f"{value:.3f}"


def build_splice_command(front_path, kar_path, end_path, output_path,
                         audio_start=0.0, audio_end=None,
                         video_start=0.0, video_end=None,
                         front_audio_end=None, end_audio_start=None,
                         lead_in=0.0):
    """
    Build the ffmpeg command that joins front card, karaoke and end card.

    Video and audio are concatenated as two separate 3-segment chains because
    bleed makes the segment boundaries differ between them (see the BLEED note
    above). With no bleed the two chains have identical boundaries and the
    result is the same as a single interleaved concat. ffmpeg's concat filter
    does not pad mismatched segments, so a joined chain with differing video and
    audio boundaries would drift out of sync rather than be extended.

    --lead-in pads the karaoke segment at its head: the video chain clones the
    first (post-trim) frame for lead_in seconds, the audio chain delays by the
    same amount, so the two stay locked and the pad is silent.

    Note the -y: overwriting is guarded by an explicit existence check in main()
    plus --overwrite, so ffmpeg must never be left sitting on its own interactive
    "File exists. Overwrite?" prompt, which under run_pipeline.py looks like a hang.
    """
    kar_v_trim = f"trim=start={fmt(video_start)}"
    if video_end is not None:
        kar_v_trim += f":end={fmt(video_end)}"

    kar_a_trim = f"atrim=start={fmt(audio_start)}"
    if audio_end is not None:
        kar_a_trim += f":end={fmt(audio_end)}"

    kar_v_pad = ""
    kar_a_pad = ""
    if lead_in > 0:
        kar_v_pad = f",tpad=start_duration={fmt(lead_in)}:start_mode=clone"
        kar_a_pad = f",adelay={int(round(lead_in * 1000))}:all=1"

    front_a = "[0:a]"
    if front_audio_end is not None:
        front_a += f"atrim=end={fmt(front_audio_end)},"

    end_a = "[2:a]"
    if end_audio_start is not None:
        end_a += f"atrim=start={fmt(end_audio_start)},"

    inputs = ["-i", str(front_path), "-i", str(kar_path), "-i", str(end_path)]

    filters = [
        "[0:v]setpts=PTS-STARTPTS[fv]",
        f"[1:v]{kar_v_trim},setpts=PTS-STARTPTS{kar_v_pad}[kv]",
        "[2:v]setpts=PTS-STARTPTS[ev]",
        f"{front_a}asetpts=PTS-STARTPTS[fa]",
        f"[1:a]{kar_a_trim},asetpts=PTS-STARTPTS{kar_a_pad}[ka]",
        f"{end_a}asetpts=PTS-STARTPTS[ea]",
        "[fv][kv][ev]concat=n=3:v=1:a=0[outv]",
        "[fa][ka][ea]concat=n=3:v=0:a=1[outa]",
    ]

    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(filters),
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "320k",
        "-movflags", "+faststart",
        str(output_path)
    ]
    return cmd


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    """Command-line entry point. See the module docstring for usage."""
    parser = argparse.ArgumentParser(
        description="Splice title cards around the rendered karaoke video."
    )
    parser.add_argument("track_id",
                        help="Track identifier, e.g. jimi-hendrix_purple-haze")
    parser.add_argument("--trim-start", action="store_true",
                        help="Auto-trim leading silence from the karaoke video")
    parser.add_argument("--trim-end", action="store_true",
                        help="Auto-trim trailing silence from the karaoke video")
    parser.add_argument("--trim-end-at", type=float, default=None, metavar="SECONDS",
                        help="Manually trim the karaoke video end at this timestamp")
    parser.add_argument("--bleed", type=float, default=None, metavar="SECONDS",
                        help="Run the instrumental under both title cards by this many seconds")
    parser.add_argument("--bleed-in", type=float, default=None, metavar="SECONDS",
                        help="Override the front-card bleed only")
    parser.add_argument("--bleed-out", type=float, default=None, metavar="SECONDS",
                        help="Override the end-card bleed only")
    parser.add_argument("--lead-in", type=float, default=0.0, metavar="SECONDS",
                        help="Hold the karaoke's first frame, silent, for this long before the music")
    parser.add_argument("--overwrite", action="store_true",
                        help="Replace an existing _final.mp4 instead of refusing")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the FFmpeg command without executing")
    args = parser.parse_args()

    track_id = args.track_id

    front_path  = get_track_path(track_id, "titles_front.mp4")
    end_path    = get_track_path(track_id, "titles_end.mp4")
    kar_path    = get_track_path(track_id, "kar.mp4")
    output_path = get_track_path(track_id, "final.mp4")

    for label, path in [
        ("Front titles",   front_path),
        ("End titles",     end_path),
        ("Karaoke render", kar_path)
    ]:
        if not path.exists():
            print(f"ERROR: {label} not found: {path}")
            sys.exit(1)

    # Refuse to clobber a finished render unless asked. The alternative, letting
    # ffmpeg prompt, blocks forever behind run_pipeline.py's output and reads as
    # a hang. Checked before the probing so a re-run fails in under a second.
    if output_path.exists() and not args.overwrite and not args.dry_run:
        print(f"ERROR: output already exists: {output_path}")
        print("       Pass --overwrite to replace it, or delete it first.")
        sys.exit(1)

    front_dur = get_duration(front_path)
    end_dur   = get_duration(end_path)
    kar_dur   = get_duration(kar_path)

    start_offset, end_offset = detect_trim_points(kar_path, args.trim_start, args.trim_end)

    # Audio keeps the trim points exactly as specified; bleed only moves the
    # video boundaries in from them.
    audio_start = start_offset
    audio_end   = args.trim_end_at if args.trim_end_at is not None else end_offset

    bleed_in, bleed_out = resolve_bleed(args.bleed, args.bleed_in, args.bleed_out)
    lead_in = args.lead_in

    video_start = audio_start + bleed_in
    if bleed_out > 0:
        base_end = audio_end if audio_end is not None else kar_dur
        if base_end is None:
            print("ERROR: --bleed-out needs a karaoke end point, but no trim was given "
                  "and the karaoke duration could not be read.")
            sys.exit(1)
        video_end = base_end - bleed_out
    else:
        video_end = audio_end

    errors = validate_bleed(bleed_in, bleed_out, front_dur, end_dur,
                            video_start, video_end if video_end is not None else kar_dur,
                            lead_in=lead_in)
    if errors:
        for err in errors:
            print(f"ERROR: {err}")
        sys.exit(1)

    front_audio_end = (front_dur - bleed_in) if bleed_in  > 0 else None
    end_audio_start = bleed_out              if bleed_out > 0 else None

    # ── Report ────────────────────────────────────────────────────────────────
    eff_video_end = video_end if video_end is not None else kar_dur
    eff_audio_end = audio_end if audio_end is not None else kar_dur

    print(f"\nsplice_titles: {track_id}")
    print(f"  Front card : {front_dur:.3f}s" if front_dur else "  Front card : unknown")
    print(f"  End card   : {end_dur:.3f}s"   if end_dur   else "  End card   : unknown")
    print(f"  Karaoke    : {kar_dur:.3f}s"   if kar_dur   else "  Karaoke    : unknown")

    if bleed_in or bleed_out:
        print(f"  Bleed      : in {bleed_in:.3f}s / out {bleed_out:.3f}s")
    else:
        print(f"  Bleed      : none")

    if lead_in > 0:
        print(f"  Lead-in    : {lead_in:.3f}s frozen first frame, silent")

    if audio_end is None:
        print(f"  Trim end   : none — karaoke runs to its full length")

    if output_path.exists():
        print(f"  Overwriting: {output_path.name}")

    if eff_video_end is not None:
        print(f"  Karaoke picture : {video_start:.3f}s → {eff_video_end:.3f}s "
              f"({eff_video_end - video_start:.3f}s visible)")
    if eff_audio_end is not None:
        print(f"  Karaoke audio   : {audio_start:.3f}s → {eff_audio_end:.3f}s "
              f"({eff_audio_end - audio_start:.3f}s audible)")
    if None not in (front_dur, end_dur, eff_video_end):
        total = front_dur + lead_in + (eff_video_end - video_start) + end_dur
        print(f"  Final duration  : {total:.3f}s")

    cmd = build_splice_command(
        front_path, kar_path, end_path, output_path,
        audio_start=audio_start, audio_end=audio_end,
        video_start=video_start, video_end=video_end,
        front_audio_end=front_audio_end, end_audio_start=end_audio_start,
        lead_in=lead_in
    )

    if args.dry_run:
        print("\n" + " ".join(cmd))
        print("\nDry run — exiting without executing.")
        sys.exit(0)

    print("\n🚀 Running FFmpeg command:\n")
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)
    print(f"\nDone. Output: {output_path}")


if __name__ == "__main__":
    main()
