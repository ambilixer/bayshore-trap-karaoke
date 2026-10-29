#!/usr/bin/env python3
# Created by Andy Charlie W. (ambilixer) in collaboration with Claude (Anthropic).
"""
run_whisperx.py

Step 2. Runs WhisperX on the vocal stem to get word timestamps, giving it the
track's lyrics as its initial prompt so the transcription leans towards the
real words. Run it with the karaoke virtual environment's Python;
run_pipeline.py does that for you.

Usage:
    python run_whisperx.py <track_id> [options]

    where <track_id> is the track identifier, e.g. jimi-hendrix_purple-haze.
    Files, in the track's folder:

        <track_id>_vox.wav      vocal stem (falls back to _original.wav)
        <track_id>_lyr.txt      lyrics, used to build the prompt
        <track_id>_whisp.json   WhisperX output

Options:
    --model         WhisperX model to use (default: large-v2)
    --language      Language code (default: en)
    --dry-run       Print the command without executing it

Example:
    python run_whisperx.py jimi-hendrix_purple-haze
    python run_whisperx.py jimi-hendrix_purple-haze --model medium
    python run_whisperx.py jimi-hendrix_purple-haze --dry-run
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from paths import get_track_path, get_track_dir

# The whisperx command installed beside the interpreter running this script,
# i.e. in the same virtual environment; failing that, whatever is on PATH.
_BESIDE = Path(sys.executable).parent / ("whisperx.exe" if os.name == "nt" else "whisperx")
WHISPERX_EXE = str(_BESIDE) if _BESIDE.exists() else shutil.which("whisperx")

# A section header line in _lyr.txt, e.g. [Chorus].
SECTION_RE = re.compile(r"^\[.+\]$")

# ── Configuration ─────────────────────────────────────────────────────────────

DEFAULT_MODEL    = "large-v2"
DEFAULT_LANGUAGE = "en"
ALIGN_MODEL      = "WAV2VEC2_ASR_LARGE_LV60K_960H"   # wav2vec2 model for word alignment


# ── Helpers ───────────────────────────────────────────────────────────────────

def build_prompt(lyrics_path: Path) -> str:
    """
    Build WhisperX's --initial_prompt from a lyrics file: every lead-vocal
    line, joined into one space-separated string.

    Left out: background-vocal lines (wrapped in parentheses), blank lines,
    # comment lines and [Section] headers. Asterisks are stripped, so a
    censored word reaches the prompt as its first letter (s*** becomes s);
    uncensored lyrics give a better prompt.
    """
    lines = []
    with open(lyrics_path, encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or SECTION_RE.match(line):
                continue
            if line.startswith("(") and line.endswith(")"):
                continue
            line = re.sub(r'\*+', '', line)
            lines.append(line)
    return " ".join(lines)


def parse_args(argv: list) -> tuple:
    """Minimal parser for the options in the module docstring."""
    if len(argv) < 2:
        print("Usage: python run_whisperx.py <track_id> [--model MODEL] [--language LANG] [--dry-run]")
        sys.exit(1)

    track_id = argv[1]
    model    = DEFAULT_MODEL
    language = DEFAULT_LANGUAGE
    dry_run  = False

    i = 2
    while i < len(argv):
        if argv[i] == "--model" and i + 1 < len(argv):
            model = argv[i + 1]
            i += 2
        elif argv[i] == "--language" and i + 1 < len(argv):
            language = argv[i + 1]
            i += 2
        elif argv[i] == "--dry-run":
            dry_run = True
            i += 1
        else:
            print(f"Unknown argument: {argv[i]}")
            sys.exit(1)

    return track_id, model, language, dry_run


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    track_id, model, language, dry_run = parse_args(sys.argv)

    track_dir  = get_track_dir(track_id)
    audio_path = get_track_path(track_id, "vox.wav")
    if not audio_path.exists():
        audio_path = get_track_path(track_id, "original.wav")
        if audio_path.exists():
            print(f"Note: No vocal stem found — using full track ({audio_path})")
    lyrics_path = get_track_path(track_id, "lyr.txt")
    whisp_out   = get_track_path(track_id, "whisp.json")

    if not audio_path.exists():
        print(f"ERROR: No audio file found. Expected: {get_track_path(track_id, 'vox.wav')}")
        sys.exit(1)
    if not lyrics_path.exists():
        print(f"ERROR: Lyrics file not found: {lyrics_path}")
        sys.exit(1)
    if not WHISPERX_EXE:
        print(f"ERROR: whisperx not found beside {sys.executable} or on PATH.")
        print("       Run this with the karaoke venv's python.")
        sys.exit(1)
    track_dir.mkdir(parents=True, exist_ok=True)

    prompt = build_prompt(lyrics_path)
    print(f"Initial prompt ({len(prompt.split())} words):")
    print(f"  {prompt[:120]}{'...' if len(prompt) > 120 else ''}\n")

    cmd = [
        WHISPERX_EXE, str(audio_path),
        "--model",       model,
        "--align_model", ALIGN_MODEL,
        "--output_format", "json",
        "--output_dir",  str(track_dir),
        "--language",    language,
        "--initial_prompt", prompt,
    ]

    print("Command:")
    print("  " + " ".join(f'"{c}"' if " " in c else c for c in cmd) + "\n")

    if dry_run:
        print("Dry run — exiting without executing.")
        sys.exit(0)

    print("Running WhisperX...\n")
    result = subprocess.run(cmd)
    if result.returncode == 0:
        # WhisperX names its output after the input file; rename it to the
        # track convention. replace(), not rename(): on Windows, rename() raises
        # FileExistsError when the target exists, which is every re-run of this
        # step. replace() overwrites on every platform.
        raw_out = track_dir / (audio_path.stem + ".json")
        if raw_out.exists():
            raw_out.replace(whisp_out)
            print(f"\n  Renamed output → {whisp_out}")
        else:
            print(f"\n  WARNING: Expected whisperx output not found at {raw_out}")
    sys.exit(result.returncode)
