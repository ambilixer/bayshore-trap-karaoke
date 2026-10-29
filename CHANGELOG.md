# Changelog
 
Every release of bayshore-trap-karaoke, newest first. Each release has a git
tag (`v1.0.0`, ...). The Aegisub macro and the tap tool also carry their own
version stamps, listed under each release, so an installed copy can be matched
to the release it came from.
 
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
