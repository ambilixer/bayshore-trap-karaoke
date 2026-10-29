#!/usr/bin/env python3
# Created by Andy Charlie W. (ambilixer) in collaboration with Claude (Anthropic).
"""
generate_yt.py

Step 7. Writes the YouTube listing copy (title, description, tags) for a
finished video, and grabs a thumbnail from its front title card.

Usage:
    python generate_yt.py <track_id> [--dry-run] [--no-thumb]

Reads, from the track's folder:
    <track_id>_meta.json          track facts (title, artist, album, year, bpm)
    <track_id>_yt.json            listing metadata (genre, subgenre, mood, extra tags)
    <track_id>_titles_front.mp4   front title card, the thumbnail source

Writes:
    <track_id>_yt_copy.txt        the listing, ready to paste into YouTube Studio
    <track_id>_thumb.png          the thumbnail

Expected _yt.json structure:
    {
        "genre":             "indie hip-hop",
        "subgenre":          "trap",
        "mood":              ["hazy", "braggadocious"],
        "tempo_descriptor":  "mid-tempo",
        "extra_tags":        []
    }

The front title card is required unless --no-thumb is given. If it is
missing, the final video never rendered or is incomplete, so there is
nothing to upload and nothing is written.
"""

import json
import subprocess
import sys
from pathlib import Path

from paths import get_track_path

# ── CHANNEL CONSTANTS ─────────────────────────────────────────────────────────
# The channel's own copy, repeated in every listing. If the channel's About
# text changes, change these to match.

CHANNEL_NAME     = "bayshore trap karaoke"
CHANNEL_URL      = "youtube.com/@bayshoretrap"
INSTAGRAM_HANDLE = "@bayshoretrap"
INSTAGRAM_URL    = "instagram.com/bayshoretrap"

# "(at)" rather than "@" is deliberate: YouTube auto-linkifies @handles in
# descriptions to YouTube channels, which would link to this channel while the
# sentence points at Instagram.
CTA              = "requests are welcome asf ♥‿♥, dm (at)bayshoretrap on instagram."

DISCLAIMER       = (
    "all videos are shared for entertainment and i claim no ownership. "
    "if you are the copyright holder and request removal, the video will "
    "be promptly deleted."
)
CHANNEL_MONO     = "ßŧĸ.𝄳"
SEP              = "-" * 3

CHANNEL_BOILERPLATE = (
    f"bayshore trap is a karaoke channel focused on indie rock, indie hip-hop, "
    f"shoegaze, trap, drill, witch house, rage, wave, experimental, indie pop, "
    f"and more. we make high-quality timed lyrics for the tracks that other "
    f"karaoke channels don't cover.\n"
    f"{SEP}\n"
    f"{CTA}"
)

# ── THUMBNAIL CONSTANTS ───────────────────────────────────────────────────────

# Seconds into <track_id>_titles_front.mp4 to grab the thumbnail frame.
#
# It must fall inside the stretch where every element of the front card is
# fully opaque: roughly 1.8 s to 4.85 s, after the last fade-in and before the
# first fade-out (see the card timeline in render_titles.py). The card is the
# same on every video, so this never needs to vary per track. If the card's
# timeline changes, check this still falls inside the window: nothing else
# will flag it.
THUMB_TIMESTAMP = 3.5

# ── TITLE ─────────────────────────────────────────────────────────────────────

def build_title(artist: str, title: str) -> str:
    """
    Format: Artist - Track Name (karaoke with lyrics)
    """
    return f"{artist} - {title} (karaoke with lyrics)"

# ── DESCRIPTION ───────────────────────────────────────────────────────────────

def build_description(meta: dict, yt: dict) -> str:
    """The description: a track info block, then the channel boilerplate.

    Genre, tempo and mood lines are left out when the field is empty.
    """
    artist           = meta["artist"]
    title            = meta["title"]
    album            = meta["album"]
    year             = str(meta["year"])
    genre            = yt.get("genre", "")
    subgenre         = yt.get("subgenre", "")
    mood             = yt.get("mood", [])

    # Genre string: "indie hip-hop / trap" or just "indie hip-hop"
    genre_str = f"{genre} / {subgenre}" if subgenre else genre

    # Mood string: "hazy, braggadocious"
    mood_str = ", ".join(mood) if mood else ""

    info_lines = [
        f"ARTIST:  {artist}",
        f"TRACK:   {title}",
        f"ALBUM:   {album} ({year})",
    ]
    if genre_str:
        info_lines.append(f"GENRE:   {genre_str}")
    bpm = meta.get("bpm")
    if bpm:
        info_lines.append(f"TEMPO:   {bpm} BPM")
    if mood_str:
        info_lines.append(f"MOOD:    {mood_str}")
    info_block = "\n".join(info_lines)

    sections = [
        info_block,
        SEP,
        CHANNEL_BOILERPLATE,
    ]

    return "\n".join(sections)

# ── TAGS ──────────────────────────────────────────────────────────────────────

def build_tags(meta: dict, yt: dict) -> list:
    """The tag list: fixed karaoke tags, artist and title combinations, genre,
    mood and any extra tags, with case-insensitive duplicates removed.
    """
    artist    = meta["artist"]
    title     = meta["title"]
    genre     = yt.get("genre", "")
    subgenre  = yt.get("subgenre", "")
    mood      = yt.get("mood", [])
    extra     = yt.get("extra_tags", [])

    tags = [
        "karaoke",
        "lyrics",
        "karaoke with lyrics",
        "lyrics video",
        artist,
        title,
        f"{artist} karaoke",
        f"{title} karaoke",
        f"{artist} {title}",
        CHANNEL_NAME,
    ]

    if genre:
        tags.append(genre)
        tags.append(f"{genre} karaoke")
    if subgenre:
        tags.append(subgenre)
        tags.append(f"{subgenre} karaoke")

    tags.extend(mood)
    tags.extend(extra)

    # Deduplicate, keeping the first occurrence of each.
    seen = set()
    deduped = []
    for t in tags:
        tl = t.lower().strip()
        if tl and tl not in seen:
            seen.add(tl)
            deduped.append(t)

    return deduped

# ── THUMBNAIL ─────────────────────────────────────────────────────────────────

def render_thumbnail(front_path: Path, out_path: Path,
                     timestamp: float = THUMB_TIMESTAMP) -> None:
    """
    Grab a single frame from the front title card as a PNG thumbnail.

    Uses output-side seeking (-ss after -i) rather than input-side. Input
    seeking on H.264 is keyframe-dependent; output seeking decodes forward and
    is exact. The card is only about 5 s long, so the extra decode costs
    nothing worth measuring.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", str(front_path),
        "-ss", str(timestamp),
        "-frames:v", "1",
        "-update", "1",
        str(out_path),
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        print("ERROR: ffmpeg not found on PATH.")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"ERROR: ffmpeg failed extracting the thumbnail frame.")
        print(f"  Source:    {front_path}")
        print(f"  Timestamp: {timestamp}s")
        if e.stderr:
            print("  ffmpeg stderr (last 15 lines):")
            for line in e.stderr.strip().splitlines()[-15:]:
                print(f"    {line}")
        sys.exit(1)

    # ffmpeg exits 0 having written nothing if the seek lands past the end of
    # the clip, so check=True above cannot catch it. Check the output instead.
    if not out_path.exists() or out_path.stat().st_size == 0:
        print(f"ERROR: ffmpeg reported success but wrote no thumbnail.")
        print(f"  Is {timestamp}s past the end of {front_path.name}?")
        sys.exit(1)

# ── OUTPUT ────────────────────────────────────────────────────────────────────

def build_listing(meta: dict, yt: dict) -> str:
    """The whole _yt_copy.txt: title, description, tags, disclaimer and
    monogram, separated by SEP lines so each block is easy to copy.
    """
    title       = build_title(meta["artist"], meta["title"])
    description = build_description(meta, yt)
    tags        = build_tags(meta, yt)
    tags_str    = ", ".join(tags)

    sections = [
        title,
        SEP,
        description,
        SEP,
        "TAGS:",
        tags_str,
        SEP,
        DISCLAIMER,
        SEP,
        CHANNEL_MONO,
        "",
    ]
    return "\n".join(sections)

# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    """Command-line entry point. See the module docstring for usage."""
    if len(sys.argv) < 2:
        print("Usage: python generate_yt.py <track_id> [--dry-run] [--no-thumb]")
        sys.exit(1)

    track_id  = sys.argv[1]
    dry_run   = "--dry-run" in sys.argv
    no_thumb  = "--no-thumb" in sys.argv

    meta_path  = get_track_path(track_id, "meta.json")
    yt_path    = get_track_path(track_id, "yt.json")
    front_path = get_track_path(track_id, "titles_front.mp4")
    out_path   = get_track_path(track_id, "yt_copy.txt")
    thumb_path = get_track_path(track_id, "thumb.png")

    if not meta_path.exists():
        print(f"ERROR: meta.json not found: {meta_path}")
        sys.exit(1)
    if not yt_path.exists():
        print(f"ERROR: yt.json not found: {yt_path}")
        print(f"  Create it at: {yt_path}")
        print('  Required fields: genre, subgenre, mood (list), tempo_descriptor, extra_tags (list)')
        sys.exit(1)

    # The front title card gates everything. Its absence means the final video
    # never rendered or is incomplete. There is nothing to upload, so write no
    # listing copy that implies otherwise.
    if not no_thumb and not front_path.exists():
        print(f"ERROR: front title card not found: {front_path}")
        print("  The final video is missing or incomplete — nothing to upload.")
        print(f"  Run step 4 first:  python render_titles.py {track_id}")
        print("  (Use --no-thumb to write listing copy anyway.)")
        sys.exit(1)

    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    with open(yt_path, encoding="utf-8") as f:
        yt = json.load(f)

    listing = build_listing(meta, yt)

    print(f"\ngenerate_yt: {track_id}")
    print(f"  Artist: {meta['artist']}")
    print(f"  Title:  {meta['title']}")

    if dry_run:
        print("\n" + listing)
        print(f"\nWould write thumbnail: {thumb_path}"
              if not no_thumb else "\nThumbnail skipped (--no-thumb).")
        print("Dry run — no files written.")
        sys.exit(0)

    # Thumbnail first: if it fails we exit non-zero, and having written no
    # listing copy keeps the track's on-disk state unambiguous.
    if not no_thumb:
        print(f"\n  Extracting thumbnail at {THUMB_TIMESTAMP}s...")
        render_thumbnail(front_path, thumb_path)
        print(f"  Thumb:  {thumb_path}")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(listing)

    print(f"  Output: {out_path}")
    print("\nDone. Open the file and copy each section into YouTube Studio.")

if __name__ == "__main__":
    main()
