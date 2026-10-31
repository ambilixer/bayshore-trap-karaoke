#!/usr/bin/env python3
# Created by Andy Charlie W. (ambilixer) in collaboration with Claude (Anthropic).
"""
run_pipeline.py

Runs the whole pipeline for one track, step by step, pausing for the two
manual stages: filling in the lyrics, and the timing pass (tap tool, then
Aegisub). Steps 1 to 3 run under the virtual-environment interpreters set in
config.toml; every other step runs under the Python running this script.
Each step records its progress in the track database (track_db.py).

Usage:
    python run_pipeline.py <track_id> <youtube_url> [options]

    <youtube_url> is needed only when starting from step 0.

Options:
    --from-step N                Resume pipeline from step N (0-7)
    --preset normal|aggressive   Stem separation preset (default: normal)
    --denoise                    Enable Roformer denoising
    --trim-start                 Auto-trim leading silence from karaoke video
    --trim-end                   Auto-trim trailing silence from karaoke video
    --trim-end-at <seconds>      Manually trim karaoke video end at timestamp
    --bleed <seconds>            Run the instrumental under both title cards
    --bleed-in <seconds>         Override the front-card bleed only
    --bleed-out <seconds>        Override the end-card bleed only
    --lead-in <seconds>          Hold the karaoke's first frame, silent, before
                                 the music (for tracks whose first lyric comes
                                 too soon); cannot combine with a bleed-in
    --overwrite                  Let step 6 replace an existing _final.mp4
    --bg-color #RRGGBB           Background color for karaoke canvas

--from-step N runs step N and every step after it, not step N alone. To re-run
a single step, run its script directly.

Pipeline-level flags are routed to whichever step script owns them: the trim,
bleed, lead-in and overwrite flags above are forwarded verbatim to
splice_titles.py (step 6), --preset and --denoise to run_stems.py (step 1),
--bg-color to karaoke_render.py (step 5). The trim, bleed and lead-in flags
accept both `--flag <value>` and `--flag=<value>`; --preset, --bg-color and
--from-step are space-separated only.

Nothing is trimmed unless you ask for it. Pass --trim-end-at, or --trim-end
for silence detection, or neither to keep the full render.

Pipeline steps:
    [0] initialize_track.py  create the track folder, download audio, write metadata
    *** PAUSE: fill in _lyr.txt ***
    [1] run_stems.py         stem separation (stems venv)
    [2] run_whisperx.py      transcription and alignment (karaoke venv)
    [3] whisperx_to_ass.py   first .ass, with per-syllable karaoke tags (karaoke venv)
    *** PAUSE: tap tool, export check, Aegisub ***
    [4] render_titles.py     title card videos
        check_styles.py      print the styles embedded in _subs-final.ass (advisory)
    [5] karaoke_render.py    karaoke video
    [6] splice_titles.py     title cards and karaoke video joined
    [7] generate_yt.py       YouTube listing copy and thumbnail

Resume examples:
    python run_pipeline.py <track_id> <youtube_url> --from-step 1
    python run_pipeline.py <track_id> <youtube_url> --from-step 4 --trim-end-at=140.5
    python run_pipeline.py <track_id> <youtube_url> --from-step 6 --bleed 1.5 --overwrite
    python run_pipeline.py <track_id> <youtube_url> --from-step 6 --lead-in 2 --bleed-out 1.5 --overwrite
"""

import subprocess
import sys
from datetime import date
from pathlib import Path

import track_db
from paths import PYTHON_KARAOKE, PYTHON_STEMS

# ── VENV CONFIG ───────────────────────────────────────────────────────────────
# The stems and karaoke interpreters come from config.toml (see paths.py).

PYTHON_DEFAULT = sys.executable

# ── SCRIPT DIRECTORY ──────────────────────────────────────────────────────────

SCRIPTS_DIR = Path(__file__).parent.resolve()

# ── HELPERS ───────────────────────────────────────────────────────────────────

def run(python, script, args, step_label, step_num, from_step, track_id=None, db_status=None):
    """Run one step's script under the given Python, unless the step comes
    before from_step.

    On success the track's status becomes db_status. On failure it becomes
    failed_step_N and the pipeline exits with the script's exit code.
    """
    if step_num < from_step:
        print(f"  Skipping: {step_label}")
        return
    print(f"\n{'='*60}")
    print(f"  {step_label}")
    print(f"{'='*60}")
    cmd = [python, str(SCRIPTS_DIR / script)] + args
    print(f"  Command: {' '.join(cmd)}\n")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        if track_id:
            track_db.upsert_track(track_id, status=f"failed_step_{step_num}")
        print(f"\nERROR: {step_label} failed (exit code {result.returncode}).")
        print(f"Fix the issue and re-run with --from-step {step_num}")
        sys.exit(result.returncode)
    if track_id and db_status:
        track_db.upsert_track(track_id, status=db_status)

def pause(header, instructions, step_num, from_step, track_id=None, db_status=None):
    """Print a manual step's instructions and wait for Enter.

    Skipped, like run(), when step_num comes before from_step. A pause carries
    the number of the step it follows.
    """
    if step_num < from_step:
        print(f"  Skipping pause: {header}")
        return
    print(f"\n{'='*60}")
    print(f"  *** MANUAL STEP: {header} ***")
    print(f"{'='*60}")
    print(instructions)
    input()
    if track_id and db_status:
        track_db.upsert_track(track_id, status=db_status)

# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    """Command-line entry point. See the module docstring for usage."""
    # youtube_url is only required for step 0. A second argument that starts
    # with -- is an option, not a URL.
    has_url = len(sys.argv) >= 3 and not sys.argv[2].startswith("--")

    if not has_url and len(sys.argv) < 2:
        print("Usage: python run_pipeline.py <track_id> <youtube_url> [options]")
        sys.exit(1)

    track_id    = sys.argv[1]
    youtube_url = sys.argv[2] if has_url else None
    rest        = sys.argv[3:] if has_url else sys.argv[2:]
    # Pipeline-level flags
    preset     = "normal"
    denoise    = False
    bg_color   = None
    overwrite  = False
    trim_args  = []
    bleed_args = []    # bleed and lead-in flags, forwarded to splice_titles.py as given
    from_step  = 0

    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg == "--preset" and i + 1 < len(rest):
            preset = rest[i + 1]
            i += 2
        elif arg == "--denoise":
            denoise = True
            i += 1
        elif arg == "--overwrite":
            overwrite = True
            i += 1
        elif arg == "--bg-color" and i + 1 < len(rest):
            bg_color = rest[i + 1]
            i += 2
        elif arg in ("--trim-start", "--trim-end"):
            trim_args.append(arg)
            i += 1
        elif arg == "--trim-end-at" and i + 1 < len(rest):
            trim_args.append(f"--trim-end-at={rest[i + 1]}")
            i += 2
        elif arg.startswith("--trim-end-at="):
            trim_args.append(arg)
            i += 1
        elif arg in ("--bleed", "--bleed-in", "--bleed-out", "--lead-in") and i + 1 < len(rest):
            bleed_args += [arg, rest[i + 1]]
            i += 2
        elif arg.startswith(("--bleed=", "--bleed-in=", "--bleed-out=", "--lead-in=")):
            bleed_args.append(arg)
            i += 1
        elif arg == "--from-step" and i + 1 < len(rest):
            from_step = int(rest[i + 1])
            i += 2
        else:
            print(f"Unknown argument: {arg}")
            sys.exit(1)

    if from_step > 0:
        print(f"  Resuming from step: {from_step}")
    print(f"\n{'='*60}")
    print(f"  bayshore trap karaoke -- pipeline")
    print(f"  Track: {track_id}")
    print(f"{'='*60}")
    print(f"  Stems preset:  {preset}")
    print(f"  Denoise:       {denoise}")
    print(f"  BG color:      {bg_color or 'default'}")
    print(f"  Trim flags:    {' '.join(trim_args) or 'none — karaoke runs full length'}")
    print(f"  Bleed/lead-in: {' '.join(bleed_args) or 'none'}")
    print(f"  Overwrite:     {overwrite}")
    print(f"  Python, stems:   {PYTHON_STEMS}")
    print(f"  Python, karaoke: {PYTHON_KARAOKE}")

    # Step 0: Initialize track
    if from_step == 0 and youtube_url is None:
        print("ERROR: <youtube_url> is required when starting from step 0.")
        sys.exit(1)
    run(PYTHON_DEFAULT, "initialize_track.py", [track_id, youtube_url or ""],
        "[0/7] Initialize track (initialize_track.py)", 0, from_step,
        track_id=track_id, db_status="step0_initialized")

    # Pause: lyrics. Shown only when starting from step 0.
    pause("Add lyrics", f"""
      Fill in the lyrics file before continuing:
        tracks/{track_id}/{track_id}_lyr.txt

      - Main vocal lines: plain text, one line per subtitle line
      - Keep every line within 44 characters: the ruler at the top of the
        file marks the limit
      - BG/backing vocal lines: wrapped in parentheses (like this)
      - Keep Genius-style section headers: [Verse 1], [Chorus], [Bridge] ...
        they become the section labels in the video

      Press Enter when _lyr.txt is complete...""", 0, from_step,
      track_id=track_id, db_status="lyrics_added")

    # Step 1: Stem separation
    stems_args = [track_id, "--preset", preset]
    if denoise:
        stems_args.append("--denoise")
    run(PYTHON_STEMS, "run_stems.py", stems_args,
        "[1/7] Stem separation (run_stems.py)", 1, from_step,
        track_id=track_id, db_status="step1_stems_done")

    # Step 2: WhisperX transcription
    run(PYTHON_KARAOKE, "run_whisperx.py", [track_id],
        "[2/7] WhisperX transcription (run_whisperx.py)", 2, from_step,
        track_id=track_id, db_status="step2_whisperx_done")

    # Step 3: Generate initial .ass
    run(PYTHON_KARAOKE, "whisperx_to_ass.py", [track_id],
        "[3/7] Generate subtitle file (whisperx_to_ass.py)", 3, from_step,
        track_id=track_id, db_status="step3_subs_generated")

    # Pause: the timing pass. Shown when starting from step 3 or earlier.
    pause("Aegisub", f"""
      The generated subtitle file is ready:
        tracks/{track_id}/{track_id}_subs.ass

        1. Tap tool: load _subs.ass and _original.wav, tap every line,
           export {track_id}_subs-tap.ass
        2. Gate check, which must end with EXPORT GATE: READY:
             python score_timing.py {track_id} --check --test subs-tap.ass
        3. Aegisub: open _subs-tap.ass, fix SYLFIX lines (search the Effect
           field), shift all times if there is a constant lag
        4. Add the END marker: a Comment line with Effect END where the
           music ends
        5. Save as: {track_id}_subs-timed.ass   (timing master: pre-styles, pre-macro)
        6. Apply the Main and BG styles (Style Manager)
        7. Run the "Karaoke Formatting v2" macro ONCE
        8. Save as: {track_id}_subs-final.ass

      Press Enter when _subs-final.ass is saved...""", 3, from_step,
      track_id=track_id, db_status="aegisub_complete")

    # Step 4: Render title cards
    run(PYTHON_DEFAULT, "render_titles.py", [track_id],
        "[4/7] Render title cards (render_titles.py)", 4, from_step,
        track_id=track_id, db_status="step4_titles_rendered")

    # Style snapshot, advisory only. Prints the styles actually embedded in
    # _subs-final.ass, which is what renders, and the geometry derived from
    # them, so a style changed in Aegisub shows up the first time it renders.
    # It never blocks the pipeline: a non-zero exit here is deliberately ignored.
    if from_step <= 5:
        print(f"\n{'='*60}")
        print(f"  [style check] check_styles.py")
        print(f"{'='*60}")
        subprocess.run([PYTHON_DEFAULT, str(SCRIPTS_DIR / "check_styles.py"), track_id])

    # Step 5: Render karaoke video
    render_args = [track_id]
    if bg_color:
        render_args += ["--bg-color", bg_color]
    run(PYTHON_DEFAULT, "karaoke_render.py", render_args,
        "[5/7] Render karaoke video (karaoke_render.py)", 5, from_step,
        track_id=track_id, db_status="step5_karaoke_rendered")

    # Step 6: Splice titles
    # --bleed-out is measured back from whatever end point the trim flags settle
    # on. With no trim flag the karaoke runs to its full length.
    splice_args = [track_id] + trim_args + bleed_args
    if overwrite:
        splice_args.append("--overwrite")
    run(PYTHON_DEFAULT, "splice_titles.py", splice_args,
        "[6/7] Splice title cards (splice_titles.py)", 6, from_step,
        track_id=track_id, db_status="step6_spliced")

    # Step 7: Generate YouTube copy
    run(PYTHON_DEFAULT, "generate_yt.py", [track_id],
        "[7/7] Generate YouTube listing (generate_yt.py)", 7, from_step,
        track_id=track_id, db_status="step7_yt_copy_generated")

    # Pipeline finished: mark the track completed. That means _final.mp4 and
    # _yt_copy.txt exist, not that the video has been uploaded.
    if from_step <= 7:
        track_db.upsert_track(track_id, status="completed",
                               date_completed=date.today().isoformat())

    print(f"\n{'='*60}")
    print(f"  Pipeline complete: {track_id}")
    print(f"{'='*60}")
    print(f"\n  Final video:   tracks/{track_id}/{track_id}_final.mp4")
    print(f"  YouTube copy:  tracks/{track_id}/{track_id}_yt_copy.txt")
    print(f"\n  Ready to upload.")

if __name__ == "__main__":
    main()
