# Changelog

Every release of bayshore-trap-karaoke, newest first. Each release has a git
tag (`v1.0.0`, ...). The Aegisub macro and the tap tool also carry their own
version stamps, listed under each release, so an installed copy can be matched
to the release it came from.

## [1.1.0] - 2026-10-31

Larger, tighter lyrics, and a style check that names any style that has
drifted.

### Changed

- **Lyric styles** (`aegisub/btk_styles.ass`): Main and BG go from size 80 to
  **92**, gain a **2px outline in `#522437`**, and get brighter unsung
  colours: Main `#888888`, BG `#7D706D`. Import them into Aegisub's style
  storage again (HOWTO, section 5).
- **Aegisub macro** `aegisub/btk_karaoke_v2.lua`, **v2.14**. Lyric lines are
  drawn with letter-spacing `\fsp-1.7`, which brings the gap between letters
  to 80% of what it was. Everything else on screen is scaled to match the new
  size (section labels, countdowns, the progress bar, the background-vocals
  separator, the cue dots), and the stack is laid out for it: rows 114px
  apart, the active row at y 476. The separator's label is drawn without the
  outline.
- **Line limit 48 to 44 characters**: in `whisperx_to_ass.py`,
  `score_timing.py`, the ruler at the top of a new `_lyr.txt`, and the
  pipeline's lyrics instructions.

### Added

- `check_styles.py` prints each style's colours and the contrast of its sweep,
  and compares every field of Main and BG with `aegisub/btk_styles.ass`,
  listing any that differ.
- `paths.py` warns about anything in `config.toml` it does not read, above
  all a setting placed under the wrong `[section]`, which used to be ignored
  without a word.

### Upgrading

- Videos already rendered are unaffected.
- A timing file split for the old 48-character limit can wrap at the new
  size: a line over 49 characters lands on top of the row below it. Split
  those in `_subs-timed.ass` before restyling; `score_timing.py --check`
  lists every line over 44.
- To restyle a finished track: open its `_subs-timed.ass`, apply the new
  styles, run the v2.14 macro once, save `_subs-final.ass`, and run the
  pipeline again from step 5 with `--overwrite`.

## [1.0.0] - 2026-10-29

First public release.

### Included

- **The pipeline, steps 0 to 7** (`scripts/`), run end to end by
  `run_pipeline.py` with a pause for the lyrics and one for the timing pass.
- **Aegisub macro** `aegisub/btk_karaoke_v2.lua`, **v2.13**, menu name
  "Karaoke Formatting v2".
- **Tap-timing tool** `tools/tap_timing/btk_tap.html`, **build 2026-10-28**.
- **Export gate** in `scripts/score_timing.py`: a tap export goes to Aegisub
  only once it reads `EXPORT GATE: READY`.
- `config.example.toml`, and requirements for the two virtual environments.

### Known limitations

- Windows only in practice: the display relies on Consolas and Segoe UI
  Symbol, and the pipeline has only been run there.
- The stem-separation presets and `--denoise` may not reach the Roformer half
  of the ensemble; this is unconfirmed. The Demucs half is unaffected.
- The syllable splitter leaves some two-syllable words whole (`lucky`,
  `crazy`). The timing is still right; flag the line with `S` in the tap tool
  and split it in Aegisub.
