#!/usr/bin/env python3
# Created by Andy Charlie W. (ambilixer) in collaboration with Claude (Anthropic).
"""
karaoke_render.py

Step 5. Renders the karaoke video: the instrumental stem, with the finished
subtitles burned in by libass, over a background.

Usage:
    python karaoke_render.py <track_id> [--dry-run] [--bg-color #RRGGBB]

    where <track_id> is the track identifier, e.g. jimi-hendrix_purple-haze.
    Files, in the track's folder:

        <track_id>_inst.wav         instrumental audio (required)
        <track_id>_subs-final.ass   subtitles after the Aegisub macro (required)
        <track_id>_bgvid.mp4        video background (optional)
        <track_id>_bgimg.png        still image background (optional, used as-is)
        <track_id>_kar.mp4          output

    Background priority: bgvid > bgimg > --bg-color > BG_COLOR.

The render runs at FPS, which must match FPS in render_titles.py:
splice_titles.py joins the title cards and this video without converting
frame rates, and a mismatch gives a variable-frame-rate seam.
"""


import argparse
import subprocess
import sys
from paths import get_track_path

# ── CONFIG ────────────────────────────────────────────────────────────────────

BG_COLOR       = "#090406"   # near-black canvas when there is no background file
FPS            = 60          # must match FPS in render_titles.py

# ── HELPERS ────────────────────────────────────────────────────────────────────

def run_ffmpeg(cmd):
    """Print an ffmpeg command, then run it, raising if it fails."""
    print("\n🚀 Running FFmpeg command:\n")
    print(" ".join(cmd))
    print("\n")
    subprocess.run(cmd, check=True, stderr=None)

def escape_ass_path(path: str) -> str:
    """Escape a path for use inside an FFmpeg -vf ass= filter on Windows.

    The filter parser treats a colon as an option separator and a backslash as
    an escape, so a Windows path has to be written with forward slashes and an
    escaped drive colon (C\\:/...). ffmpeg accepts forward slashes on Windows.
    """
    return path.replace('\\', '/').replace(':', '\\:').replace(' ', '\\ ')

def get_audio_duration(audio_path):
    """Length of an audio file in seconds, read with ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return float(result.stdout.strip())


def build_command(video, video_is_image, audio, subtitles, output, bg_color=None, duration=None):
    """
    Build the ffmpeg command for the karaoke render.

    video is a background video or image path, or None for a plain canvas
    (video_is_image says which). bg_color sets the canvas colour and falls
    back to BG_COLOR. duration is the audio length in seconds, needed only for
    an image background. audio, subtitles and output are file paths.
    """

    # Background input
    if video and video_is_image:
        input_video = ["-loop", "1", "-framerate", str(FPS), "-i", video]
        video_filter_input = "[0:v]"
    elif video:
        # A background video is conformed to FPS so the subtitle timing and the
        # splice both see a constant rate.
        input_video = ["-i", video]
        video_filter_input = f"[0:v]fps={FPS},"
    else:
        # No background file: generate a solid canvas.
        color = bg_color if bg_color else BG_COLOR
        input_video = [
            "-f", "lavfi",
            "-i", f"color=c={color}:s=1920x1080:r={FPS}"
        ]
        video_filter_input = "[0:v]"

    if audio:
        input_audio = ["-i", audio]
    else:
        input_audio = []

    # Subtitles are drawn by libass, through ffmpeg's ass filter.
    vf = f"{video_filter_input}ass='{escape_ass_path(subtitles)}'"

    # A looped image never ends on its own, so it is cut to the audio's length.
    # The other backgrounds stop with the audio through -shortest.
    if video and video_is_image:
        duration_flag = ["-t", str(duration)]
    else:
        duration_flag = ["-shortest"]

    cmd = [
        "ffmpeg",
        *input_video,
        *input_audio,
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "320k",
        *duration_flag,
        "-movflags", "+faststart",
        output
    ]

    return cmd

def main():
    """Command-line entry point. See the module docstring for usage."""
    parser = argparse.ArgumentParser(description='Render karaoke MP4 from component parts.')
    parser.add_argument('track_id', help='Track identifier, e.g. jimi-hendrix_purple-haze')
    parser.add_argument('--dry-run', action='store_true', help='Print command without executing')
    parser.add_argument('--bg-color', help='Background color in hex format (e.g., #FF0000) for canvas fallback')
    args = parser.parse_args()

    track_id = args.track_id
    dry_run = args.dry_run
    bg_color = args.bg_color

    if bg_color and not (bg_color.startswith('#') and len(bg_color) == 7 and
                         all(c in '0123456789ABCDEFabcdef' for c in bg_color[1:])):
        print(f"ERROR: Invalid hex color format: {bg_color}. Expected format: #RRGGBB")
        sys.exit(1)

    audio_path = get_track_path(track_id, "inst.wav")
    subs_path  = get_track_path(track_id, "subs-final.ass")
    output     = get_track_path(track_id, "kar.mp4")

    bgvid = get_track_path(track_id, "bgvid.mp4")
    bgimg = get_track_path(track_id, "bgimg.png")

    if bgvid.exists():
        video          = str(bgvid)
        video_is_image = False
        print(f"  Background: video ({bgvid.name})")
    elif bgimg.exists():
        video          = str(bgimg)
        video_is_image = True
        print(f"  Background: image ({bgimg.name})")
    else:
        video          = None
        video_is_image = False
        if bg_color:
            print(f"  Background: canvas with color {bg_color}")
        else:
            print("  Background: none (black canvas fallback)")

    if not audio_path.exists():
        print(f"ERROR: Audio file not found: {audio_path}")
        sys.exit(1)
    if not subs_path.exists():
        print(f"ERROR: Subtitle file not found: {subs_path}")
        sys.exit(1)

    print(f"\nkaraoke_render: {track_id}")
    print(f"  Audio:  {audio_path}")
    print(f"  Subs:   {subs_path}")
    print(f"  Output: {output}")
    print(f"  FPS:    {FPS}")

    if dry_run:
        print("\nDry run — exiting without executing.")
        sys.exit(0)

    duration = get_audio_duration(audio_path) if video_is_image else None
    cmd = build_command(video, video_is_image, str(audio_path), str(subs_path), str(output), bg_color, duration)
    run_ffmpeg(cmd)
    print(f"\nDone. Output: {output}")

if __name__ == "__main__":
    main()
