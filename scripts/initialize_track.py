#!/usr/bin/env python3
# Created by Andy Charlie W. (ambilixer) in collaboration with Claude (Anthropic).
"""
initialize_track.py

Step 0. Sets up a new track:
  1. Creates the track's folder
  2. Asks for the track's details (title, artist, album, year, BPM, who
     requested it) and writes <track_id>_meta.json
  3. Asks for the listing details (genre, subgenre, mood, extra tags) and
     writes <track_id>_yt.json
  4. Creates <track_id>_lyr.txt, seeded with a short guide and a ruler that
     marks the 44-character line limit
  5. Downloads the audio from YouTube with yt-dlp as <track_id>_original.wav
  6. Prints the audio's duration, sample rate, channels, bitrate and file size

Usage:
    python initialize_track.py <track_id> <youtube_url>

    <track_id>     letters, digits, hyphens and underscores only; by
                   convention <artist>_<track-name>
    <youtube_url>  the video to take the audio from

    The track folder is created inside the tracks folder set in config.toml
    (see paths.py).

If the track's _meta.json or _original.wav already exists, it asks before
overwriting. If YouTube refuses the download with a 403, yt-dlp is usually
out of date or has no JavaScript runtime: install "yt-dlp[default]" for this
Python, and Deno.

Example:
    python initialize_track.py jimi-hendrix_purple-haze https://youtube.com/watch?v=xxx
    python initialize_track.py beatles_hey-jude https://youtu.be/xxx
"""

import json
import os
import sys
import subprocess
import wave
from pathlib import Path

from paths import TRACKS_DIR

# Seeded at the top of every new _lyr.txt. The | sits at column 44, the longest
# line that fits on screen once split (LONG_LINE_WARN in whisperx_to_ass.py);
# # lines are ignored by every reader. The guide lines stay inside the ruler.
LYRICS_TEMPLATE = (
    "# One line per subtitle line, none past |\n"
    "# Backing (like this). Headers [Verse 1]\n"
    "#.......10........20........30........40...|\n"
)


def prompt_metadata():
    """
    Ask for the track's details, re-asking until each answer is valid.

    Returns a dict with title, artist, album, year (a 4-digit string), bpm (a
    positive integer) and request_source (None for your own pick).
    """
    print("\n=== Enter Track Metadata ===")

    title = input("Title: ").strip()
    while not title:
        print("Title cannot be empty.")
        title = input("Title: ").strip()

    artist = input("Artist: ").strip()
    while not artist:
        print("Artist cannot be empty.")
        artist = input("Artist: ").strip()

    album = input("Album: ").strip()
    while not album:
        print("Album cannot be empty.")
        album = input("Album: ").strip()

    year = input("Year: ").strip()
    while not year or not year.isdigit() or len(year) != 4:
        print("Year must be a 4-digit number.")
        year = input("Year: ").strip()

    bpm = input("BPM: ").strip()
    while True:
        try:
            bpm_int = int(bpm)
            if bpm_int <= 0:
                print("BPM must be a positive integer.")
                bpm = input("BPM: ").strip()
                continue
            break
        except ValueError:
            print("BPM must be an integer.")
            bpm = input("BPM: ").strip()

    request_source = input(
        "Request source (blank if your own pick, otherwise platform + handle, "
        "e.g. 'Instagram @user2'): "
    ).strip()
    request_source = request_source if request_source else None

    return {
        "title": title,
        "artist": artist,
        "album": album,
        "year": year,
        "bpm": bpm_int,
        "request_source": request_source
    }

def prompt_yt_metadata():
    """Ask for the listing details: genre (required), subgenre, mood and extra tags."""
    print("\n=== Enter YouTube Metadata ===")

    genre = input("Primary genre (e.g. indie hip-hop): ").strip()
    while not genre:
        print("Genre cannot be empty.")
        genre = input("Primary genre: ").strip()

    subgenre = input("Subgenre (optional, press Enter to skip): ").strip()

    mood_input = input("Mood descriptors, comma-separated (e.g. hazy, braggadocious): ").strip()
    mood = [m.strip() for m in mood_input.split(",") if m.strip()] if mood_input else []

    extra_input = input("Extra tags, comma-separated (optional): ").strip()
    extra_tags = [t.strip() for t in extra_input.split(",") if t.strip()] if extra_input else []

    return {
        "genre":            genre,
        "subgenre":         subgenre,
        "mood":             mood,
        "extra_tags":       extra_tags,
    }

def check_existing_files(track_dir, track_id):
    """
    If the track's _meta.json or _original.wav already exists, list them and
    ask whether to overwrite. Returns True when it is safe to go on: nothing
    exists yet, or the overwrite was confirmed.
    """
    meta_file = track_dir / f"{track_id}_meta.json"
    wav_file = track_dir / f"{track_id}_original.wav"

    existing_files = []
    if meta_file.exists():
        existing_files.append(meta_file.name)
    if wav_file.exists():
        existing_files.append(wav_file.name)

    if not existing_files:
        return True

    print(f"\n⚠️  The following files already exist in {track_dir}:")
    for f in existing_files:
        print(f"  - {f}")

    response = input("Overwrite existing files? (y/N): ").strip().lower()
    return response == 'y' or response == 'yes'


def download_audio(track_dir, track_id, youtube_url):
    """
    Download the video's best audio stream with yt-dlp and convert it to
    <track_id>_original.wav. Returns True on success. On failure, prints
    yt-dlp's error and, for the two common causes, the command that fixes it.
    """
    output_path = track_dir / f"{track_id}_original.wav"

    # yt-dlp runs as a module of this interpreter, so the copy that runs is
    # the one installed (and upgraded) for this Python, not whichever yt-dlp
    # happens to be first on PATH.
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "-f", "ba",           # best audio format
        "-x",                 # extract audio
        "--audio-format", "wav",
        "--output", str(output_path),
        youtube_url
    ]

    print(f"\n=== Downloading Audio ===")
    print(f"Command: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False
        )

        if result.returncode != 0:
            print(f"❌ Download failed with error:")
            print(result.stderr)
            # YouTube needs a JavaScript challenge solved before it hands out
            # audio. yt-dlp does that only with a JS runtime (Deno) and the
            # solver scripts that the [default] extra installs; without them
            # the request is refused with 403.
            if "No module named yt_dlp" in result.stderr:
                print(f'Install it for this Python: "{sys.executable}" -m pip install -U "yt-dlp[default]"')
            elif "403" in result.stderr:
                print('A 403 usually means a stale yt-dlp or no JavaScript runtime. Update with\n'
                      f'"{sys.executable}" -m pip install -U "yt-dlp[default]" and install Deno.')
            return False

        print("✅ Download completed successfully")
        return True

    except Exception as e:
        print(f"❌ Unexpected error during download: {e}")
        return False


def get_wav_metadata(wav_path):
    """
    Read a WAV file's header with the standard wave module.

    Returns a dict with duration_seconds, sample_rate, channels, sample_width,
    bitrate and file_size_bytes, or None if the file cannot be read.
    """
    try:
        with wave.open(str(wav_path), 'rb') as wav_file:
            n_channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            frame_rate = wav_file.getframerate()
            n_frames = wav_file.getnframes()

            duration_seconds = n_frames / float(frame_rate)

            # Uncompressed, so the bitrate is just rate x bytes x channels x 8.
            bitrate = frame_rate * sample_width * n_channels * 8

            file_size = wav_path.stat().st_size

            return {
                "duration_seconds": duration_seconds,
                "sample_rate": frame_rate,
                "channels": n_channels,
                "sample_width": sample_width,
                "bitrate": bitrate,
                "file_size_bytes": file_size
            }
    except Exception as e:
        print(f"⚠️  Could not read WAV metadata: {e}")
        return None


def format_duration(seconds):
    """Format duration in seconds to MM:SS or HH:MM:SS."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}:{secs:02d}"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours}:{minutes:02d}:{secs:02d}"


def format_bitrate(bitrate):
    """Format bitrate in bits per second to human-readable string."""
    if bitrate >= 1_000_000:
        return f"{bitrate / 1_000_000:.1f} Mbps"
    elif bitrate >= 1_000:
        return f"{bitrate / 1_000:.0f} kbps"
    else:
        return f"{bitrate} bps"


def format_file_size(bytes_size):
    """Format file size in bytes to human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.1f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.1f} TB"


def main():
    """Command-line entry point. See the module docstring for usage."""
    if len(sys.argv) != 3:
        print("❌ Error: Invalid number of arguments")
        print(__doc__)
        sys.exit(1)

    track_id = sys.argv[1]
    youtube_url = sys.argv[2]

    # The track_id becomes a folder name and the prefix of every file name.
    if not track_id or not all(c.isalnum() or c in '-_' for c in track_id):
        print("❌ Error: track_id must contain only alphanumeric characters, hyphens, and underscores")
        sys.exit(1)

    track_dir = TRACKS_DIR / track_id

    print(f"\n=== Initializing Track: {track_id} ===")
    print(f"Track directory: {track_dir}")

    try:
        track_dir.mkdir(parents=True, exist_ok=True)
        print(f"✅ Directory created: {track_dir}")
    except Exception as e:
        print(f"❌ Failed to create directory: {e}")
        sys.exit(1)

    if not check_existing_files(track_dir, track_id):
        print("❌ Operation cancelled by user.")
        sys.exit(0)

    metadata = prompt_metadata()
    metadata["youtube_url"] = youtube_url

    # Written as explicit UTF-8: a bare open() on Windows uses cp1252, and the
    # first accented letter in an artist or title would then break the reader.
    meta_path = track_dir / f"{track_id}_meta.json"
    try:
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=4, ensure_ascii=False)
        print(f"✅ Metadata saved: {meta_path}")
    except Exception as e:
        print(f"❌ Failed to save metadata: {e}")
        # The folder is left in place; nothing has been downloaded yet.
        sys.exit(1)

    yt_metadata = prompt_yt_metadata()
    yt_path = track_dir / f"{track_id}_yt.json"
    try:
        with open(yt_path, 'w', encoding='utf-8') as f:
            json.dump(yt_metadata, f, indent=4, ensure_ascii=False)
        print(f"  YouTube metadata saved: {yt_path}")
    except Exception as e:
        print(f"  WARNING: Failed to save YouTube metadata: {e}")

    # A lyrics file that already exists is never overwritten.
    lyr_path = track_dir / f"{track_id}_lyr.txt"
    if not lyr_path.exists():
        lyr_path.write_text(LYRICS_TEMPLATE, encoding="utf-8")
        print(f"  Created empty lyrics file: {lyr_path}")
        print(f"  *** Fill in lyrics before running run_whisperx.py ***")

    if not download_audio(track_dir, track_id, youtube_url):
        print("❌ Download failed. Cleaning up...")
        # Remove the folder only if the meta file is all it holds. Since
        # _yt.json and _lyr.txt are written before the download, in practice
        # the folder is always left in place, with the files typed so far, so
        # a re-run after fixing yt-dlp can keep them.
        try:
            if track_dir.exists():
                contents = list(track_dir.iterdir())
                if len(contents) == 1 and contents[0].name == f"{track_id}_meta.json":
                    track_dir.rmdir()
                    print(f"✅ Cleaned up empty directory: {track_dir}")
                else:
                    print(f"⚠️  Partial files may remain in: {track_dir}")
        except Exception as e:
            print(f"⚠️  Could not clean up directory: {e}")
        sys.exit(1)

    wav_path = track_dir / f"{track_id}_original.wav"
    if wav_path.exists():
        audio_info = get_wav_metadata(wav_path)
        if audio_info:
            print("\n=== Audio File Information ===")
            print(f"Duration:     {format_duration(audio_info['duration_seconds'])}")
            print(f"Sample Rate:  {audio_info['sample_rate']} Hz")
            print(f"Channels:     {audio_info['channels']}")
            print(f"Bitrate:      {format_bitrate(audio_info['bitrate'])}")
            print(f"File size:    {format_file_size(audio_info['file_size_bytes'])}")
        else:
            print("⚠️  Audio metadata could not be read.")
    else:
        print("⚠️  Audio file not found after download.")
        sys.exit(1)

    print("\n=== ✅ Track Initialization Complete ===")
    print(f"Track ID:   {track_id}")
    print(f"Directory:  {track_dir}")
    print(f"Metadata:   {meta_path}")
    print(f"Audio:      {wav_path}")
    print(f"Artist:     {metadata['artist']}")
    print(f"Title:      {metadata['title']}")
    print(f"Album:      {metadata['album']}")
    print(f"Year:       {metadata['year']}")
    print(f"BPM:        {metadata['bpm']}")
    print(f"Request:    {metadata['request_source'] or 'own pick'}")
    print(f"YouTube:    {metadata['youtube_url']}")

    sys.exit(0)


if __name__ == "__main__":
    main()
