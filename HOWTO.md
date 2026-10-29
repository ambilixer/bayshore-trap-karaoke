# How to use bayshore-trap-karaoke

This is the full walkthrough: setting up once, then making a track from start to finish. The [README](README.md) has the short version.

## The whole process at a glance

![Flow chart of making a track. You start a track and input its details; step 0 sets it up; you paste the lyrics; steps 1 to 3 make a first draft; you tap the timing in the tap tool; the export gate checks it, sending it back if not ready; you finish in Aegisub; steps 4 to 7 render and assemble the video; you watch it and upload.](docs/images/pipeline-flow.svg)

`run_pipeline.py` runs every automatic step in order and stops for each manual one. When you've done that part, press Enter in its window and it carries on.

---

## 1. What you need

| | What | Check it with |
|---|---|---|
| **Computer** | Windows, with an NVIDIA GPU. The display relies on Consolas and Segoe UI Symbol, which ship with Windows. | |
| **Python** | **3.11** for the two virtual environments, and **3.11 or later** to run the pipeline itself. | `py -3.11 --version` |
| **ffmpeg** | A build with libass, on your PATH. The gyan.dev "full" build works. | `ffmpeg -filters \| findstr ass` should list `ass` and `subtitles` |
| **Aegisub** | 3.4 (installer or portable). | |
| **yt-dlp** | Installed into the Python that runs the pipeline, with its `[default]` extra: `py -m pip install -U "yt-dlp[default]"`. | `py -m yt_dlp --version` |
| **Deno** | yt-dlp needs a JavaScript runtime to download from YouTube: `winget install DenoLand.Deno`. | `deno --version` |

## 2. Getting the code

Clone the repository with GitHub Desktop (**File > Clone repository**), or download it as a ZIP from GitHub and unpack it. Keep it **outside** any synced folder such as Google Drive or OneDrive: sync clients can damage a git repository.

```
scripts/                Python pipeline, one script per step, plus run_pipeline.py
aegisub/                btk_karaoke_v2.lua (the macro) and btk_styles.ass (the lyric styles)
tools/tap_timing/       btk_tap.html, the tap-timing tool
config.example.toml     settings template
docs/                   the flow chart above, as source (pipeline-flow.mmd) and image
requirements-*.txt      packages for the two virtual environments
```

Your track folders can live anywhere, synced folders included; `config.toml` says where (section 4).

## 3. The two virtual environments

Stem separation and WhisperX each need their own PyTorch, so each gets its own environment. The paths below are examples; use any folder outside the repository.

**For each environment, install PyTorch first, then the requirements file.** Done the other way round, pip installs a CPU-only PyTorch and everything runs many times slower.

```powershell
# Stems (step 1)
py -3.11 -m venv C:\venvs\stems
C:\venvs\stems\Scripts\python.exe -m pip install torch==2.11.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cu128
C:\venvs\stems\Scripts\python.exe -m pip install -r requirements-stems.txt

# Karaoke (steps 2 and 3)
py -3.11 -m venv C:\venvs\karaoke
C:\venvs\karaoke\Scripts\python.exe -m pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
C:\venvs\karaoke\Scripts\python.exe -m pip install -r requirements-karaoke.txt
```

Then check each one sees the GPU:

```powershell
C:\venvs\stems\Scripts\python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available())"
C:\venvs\karaoke\Scripts\python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

Both should end in `True`. If either prints `False` or a version without `+cu128`, reinstall its PyTorch with the command above.

The first stems run downloads the separation model (several hundred MB), and the first WhisperX run downloads its own models.

## 4. Settings: `config.toml`

Copy `config.example.toml` to `config.toml` in the same folder and edit the copy. `config.toml` is ignored by git, so your paths never end up in the repository. Every setting is optional.

| Setting | What it is | If you leave it out |
|---|---|---|
| `[paths] tracks_dir` | The folder holding one subfolder per track | `tracks\` inside the repository |
| `[paths] db_path` | The pipeline's progress log, `track_db.json` | beside the tracks folder |
| `[python] stems` | The stems environment's `python.exe` | the Python running the pipeline, which will fail at step 1 |
| `[python] karaoke` | The karaoke environment's `python.exe` | the same, failing at step 2 |
| `[models] model_dir` | Where the separation model is kept | `.cache\audio-separator` in your user folder |

A typical file:

```toml
[paths]
tracks_dir = 'D:\karaoke\tracks'

[python]
stems   = 'C:\venvs\stems\Scripts\python.exe'
karaoke = 'C:\venvs\karaoke\Scripts\python.exe'
```

**Use single quotes around Windows paths** (or forward slashes). Inside double quotes a backslash starts an escape sequence and the file won't load; the pipeline stops and says so.

## 5. Aegisub setup

### The macro

Copy **only** `aegisub\btk_karaoke_v2.lua` into Aegisub's autoload folder:

- installed Aegisub: `%APPDATA%\Aegisub\automation\autoload`
- portable Aegisub: `automation\autoload` inside its folder

Aegisub loads every `.lua` file it finds there, in subfolders too. Don't copy the repository's folders in, and don't keep old copies of the macro beside the new one: you'd get two entries in the menu with no way to tell which is which. Restart Aegisub; the **Automation** menu should now list **Karaoke Formatting v2**.

### The lyric styles

The look of the lyrics (font, size, colours, shadow, position) lives in two Aegisub styles, **Main** and **BG**, and the macro's layout is built around their exact values. `aegisub\btk_styles.ass` holds both. Put them into Aegisub's style storage once, and every track can pick them up from there:

1. In Aegisub, open `aegisub\btk_styles.ass` (**File > Open Subtitles**).
2. Open **Subtitle > Styles Manager**. At the top, next to the catalog list, click **New** and name the catalog, for example `btk`.
3. In the **Current script** list on the right, select both Main and BG and click **Copy to storage**.

For each track later, you copy them back the other way (section 6.4).

These are the values in the template, for reference. `check_styles.py` prints the ones in each finished track so you can spot a difference:

| | Main | BG |
|---|---|---|
| Font | Consolas, bold, size 80 | Consolas, bold, size 80 |
| Scale X / Y, spacing | 90% / 100%, spacing 1 | 90% / 100%, spacing 1 |
| Primary colour (sung) | `#B9DE40` lime | `#F76E64` coral |
| Secondary colour (unsung) | `#6E7A76` | `#695C59` |
| Outline / shadow | 0 / 2.5, shadow black at alpha `AA` | the same |
| Alignment, margins (L / R / V) | 5, 40 / 40 / 0 | 2, 40 / 40 / 260 |

If you change a style, note that ASS stores colours as `&HAABBGGRR`: blue and red are swapped compared with the `#RRGGBB` you would type elsewhere. And mind the size: the layout and the 48-character line limit both depend on it.

---

## 6. Making a track

Open PowerShell in the repository's `scripts` folder and start the pipeline with a **track ID** and the YouTube link:

```powershell
cd scripts
py run_pipeline.py artist_track-name https://youtube.com/watch?v=...
```

The track ID becomes the folder name and the start of every file name. Use letters, digits, hyphens and underscores, by convention `<artist>_<track-name>`.

### 6.1 Step 0: set up the track

It asks for the track's details (title, artist, album, year, BPM, and who requested it, if anyone), then the listing details (genre, subgenre, mood words, extra tags). Then it downloads the audio as `_original.wav` and creates an almost empty `_lyr.txt`. The details end up on the title cards and in the YouTube copy, so spell them the way you want them shown.

### 6.2 Paste the lyrics

The pipeline pauses. Open `<track>_lyr.txt` in the track folder and paste the lyrics below the three lines starting with `#`. They're instructions for you, and every script ignores them.

- **One line per on-screen line.** The third `#` line is a ruler: its `|` marks 48 characters. Split anything longer by hand, at a natural break; a longer line gets split automatically, at the word nearest its middle.
- **Background vocals** go on their own line, in parentheses: `(yeah, okay)`. They're shown in their own zone below the main lyrics.
- **Keep section headers** like `[Verse 1]`, `[Chorus]`, `[Bridge]` on their own lines. They become the section labels on screen. Everything after a colon is dropped, so write `[Final Chorus]`, not `[Chorus: Final]`.
- **Check the words against what is actually sung.** The tap tool expects one tap per syllable, so a missing or extra word costs you a tap and throws the rest of that line out.
- **Lyrics copied from Genius** can contain invisible lookalike letters (a Cyrillic `е` in place of an `e`). They look identical on screen but confuse the syllable splitter and the alignment. If a word behaves oddly later, retype it.

Save the file (as UTF-8, which is Notepad's default) and press Enter.

### 6.3 Steps 1 to 3, then the tap tool

These run by themselves: vocals are separated from the instrumental (`_vox.wav`, `_inst.wav`), WhisperX lines the lyrics up with the vocals, and the result becomes `_subs.ass`, a first draft with a timing for every syllable. The pipeline then pauses again.

The draft is good on some tracks and poor on others, especially where lyrics repeat. Either way, you now time it properly by tapping along:

1. Open `tools\tap_timing\btk_tap.html` in your browser.
2. Load `<track>_subs.ass` (**subs**) and `<track>_original.wav` (**audio**).
3. Press **space** to play, and tap each syllable as it is sung, alternating **J** and **K**. The current line is in the middle, with the syllable you're on underlined.
4. When the last note of a line ends, you can tap once more to mark it. Without that end tap, a line ends **hold** seconds after its last syllable: around 0.3 suits dense rap, around 1.0 slow, held vocals. Set it in the header.
5. Press **Enter** to keep your taps and move on.
6. When every line is done, click **export .ass**. The file downloads as `<track>_subs-tap.ass`; move it into the track folder.

| Key | Does |
|---|---|
| space | play / pause |
| J / K | tap (alternate them) |
| Enter | keep your taps, next line |
| N | keep the draft's timing for this line, next line |
| S | the syllable split is wrong: before tapping, switches the line to one tap per word; after tapping, just flags it |
| Backspace | undo the last tap |
| R | redo this line |
| P / O | previous line / restart this line |
| - / = | back / forward 5 seconds |
| 1 / 2 / 3 | 0.5x / 0.75x / 1x speed |
| F | follow the audio on / off |
| Esc | unpin, so the tool follows the audio again |

Things worth knowing:

- **Coral lines** (a coral `--:--` in place of a time) are ones the draft got badly wrong. Their stored time is meaningless, so the tool never jumps to it or skips them. **Tap every coral line**; the header counts how many are left.
- **Auto-offset** (on by default) measures how far behind the beat you tap, once you've tapped 30 or so syllables, and corrects for it on export. Leave it on.
- **Flagged lines** (`S`) are marked `SYLFIX` for fixing in Aegisub. Their timing is still used.
- **Use the same headphones for all timing and checking.** Different headsets add different delays, so a line that looks in sync on one can look late on another.

### 6.4 The export check, then Aegisub

Before going further, check the export. In PowerShell, in `scripts`:

```powershell
py score_timing.py artist_track-name --check --test subs-tap.ass
```

It must end with **`EXPORT GATE: READY`**. If it says NOT READY, the lines it lists still need work: usually coral lines left untapped, or a line accepted half-tapped. Go back to the tap tool, fix them, and export again.

Then, in Aegisub:

1. **Open `<track>_subs-tap.ass`.**
2. **Fix the flagged lines.** Find each line with `SYLFIX` in its Effect field (**Edit > Find**, searching the Effect field). Correct its syllable split in Aegisub's karaoke mode, then delete the `SYLFIX`.
3. **Shift everything if needed.** If every line sits slightly early or late, select all lines and use **Timing > Shift Times**.
4. **Place the END marker** if the song ends on a long instrumental. Add a line where the music ends, tick its **Comment** box and type `END` in its Effect field. A tail of 10 seconds or more then gets a countdown and progress bar instead of a plain fade.
5. **Save as `<track>_subs-timed.ass`** (**File > Save Subtitles As**). This is your finished timing, and the file to go back to later.
6. **Apply the styles.** Open **Subtitle > Styles Manager**, choose your catalog, select Main and BG in **Storage**, click **Copy to current script**, and confirm replacing the existing ones.
7. **Run Automation > Karaoke Formatting v2, once.** Its log lists the sections, breaks and outro it found, and any warnings.
8. **Save as `<track>_subs-final.ass`.**

Back in the pipeline window, press Enter.

> **Run the macro only on a `_subs-timed.ass`.** The lines it creates look like lyrics to a second run, so it refuses to run on its own output. And never edit a `_subs-final.ass`: change `_subs-timed.ass` and run the macro again.

### 6.5 Steps 4 to 7

These run by themselves: the title cards, a style check, the karaoke video, the final splice, and the YouTube copy and thumbnail. The style check (`check_styles.py`) only reports and never stops the pipeline. Its warning that a stacked pair of BG lines can reach under YouTube's control bar is expected.

When it finishes, the track folder holds `_final.mp4`, `_yt_copy.txt` (the title, description and tags, ready to paste into YouTube Studio) and `_thumb.png`. **Watch the video before uploading**; the pipeline never uploads anything.

The splice step takes options, passed to `run_pipeline.py` like this:

```powershell
py run_pipeline.py artist_track-name --from-step 6 --bleed 2 --overwrite
```

| Option | Does |
|---|---|
| `--bleed S` | Lets the instrumental run under both title cards by S seconds, instead of cutting in and out with them |
| `--bleed-in S` / `--bleed-out S` | The same for one card only |
| `--lead-in S` | Holds the first frame, silent, for S seconds before the music, for songs whose first line comes too soon. Can't be combined with `--bleed-in` |
| `--trim-end-at S` | Cuts the karaoke video off at S seconds |
| `--overwrite` | Allows replacing an existing `_final.mp4`; without it the step refuses |

Two working values: `--bleed-in 2.2` puts the music under way just before the front card's countdown dots light. On a track that opens with a long instrumental countdown, use `--bleed-in 0`, or the countdown is first seen part-way through. To find a trim point, list every silence in the karaoke video and pick the last one:

```powershell
ffmpeg -i <track>_kar.mp4 -af silencedetect=n=-30dB:d=1 -f null -
```

---

## 7. A track's files

Every file sits in the track's folder as `<track_id>_<suffix>`.

| File | What it is | Safe to regenerate? |
|---|---|---|
| `_meta.json`, `_yt.json` | the details from step 0 | edit by hand, then run `py check_meta.py <track_id>` |
| `_lyr.txt` | the lyrics | yours |
| `_original.wav` | downloaded audio | step 0 |
| `_vox.wav`, `_inst.wav` | vocal and instrumental stems | step 1 |
| `_whisp.json` | WhisperX output | step 2 |
| `_subs.ass` | the first draft | step 3 (overwrites it) |
| `_subs-tap.ass` | the tap tool's export | re-export |
| **`_subs-timed.ass`** | **your finished timing** | **no: this is the master. Back it up.** |
| `_subs-final.ass` | the macro's output, what renders | rerun the macro on `_subs-timed.ass` |
| `_titles_front.mp4`, `_titles_end.mp4` | title cards | step 4 |
| `_kar.mp4` | karaoke video | step 5 |
| `_final.mp4` | the finished video | step 6 |
| `_yt_copy.txt`, `_thumb.png` | listing copy, thumbnail | step 7 |

## 8. Going back and changing things

`--from-step N` runs step N **and every step after it**. To rerun one step alone, run its script from `scripts`. Steps 1 to 3 need their environment's Python, for example `C:\venvs\karaoke\Scripts\python.exe whisperx_to_ass.py <track_id>`.

| You changed | Redo |
|---|---|
| the track's details | `--from-step 4 --overwrite` |
| a style, or the macro | in Aegisub: open `_subs-timed.ass`, apply the styles, run the macro, save `_subs-final.ass`; then `--from-step 5 --overwrite` |
| some timing | edit `_subs-timed.ass`, rerun the macro, `--from-step 5 --overwrite` |
| a splice option | `--from-step 6 --overwrite` with the new option |

`--overwrite` is there because each of these runs step 6 again, which otherwise refuses to replace an existing `_final.mp4`.

## 9. Making it yours

The title cards and the YouTube copy carry this channel's identity. Change them for your own:

- `scripts\render_titles.py`: `CHANNEL_TAG` and `CHANNEL_URL`, the channel row on every card. `CHANNEL_TAG` uses non-ASCII letters, so keep the file saved as UTF-8.
- `scripts\generate_yt.py`: `CHANNEL_NAME`, `CHANNEL_URL`, `INSTAGRAM_HANDLE`, `INSTAGRAM_URL`, `CTA`, `DISCLAIMER`, `CHANNEL_MONO` and the `CHANNEL_BOILERPLATE` blurb.

The colours of the cards are constants at the top of `render_titles.py`; the lyric colours are in the styles (section 5).

## 10. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Step 0: download fails with `403` | yt-dlp is out of date, or has no JavaScript runtime. `py -m pip install -U "yt-dlp[default]"`, and install Deno. |
| Step 0: `No module named yt_dlp` | yt-dlp isn't installed for the Python running the pipeline. Install it with that Python's `-m pip`. |
| Step 1 takes 20 minutes or more | The stems environment has a CPU-only PyTorch. Reinstall it with the command in section 3, and check for `True`. |
| `No module named pyphen` or `whisperx not found` | A step 2 or 3 script was run with the wrong Python. Check `[python]` in `config.toml`, or run it with the karaoke environment's `python.exe`. |
| `config.toml is not valid TOML` | A Windows path in double quotes. Use single quotes. |
| `Unknown argument: --from-step=6` | `--from-step`, `--preset` and `--bg-color` need a space: `--from-step 6`. |
| The macro isn't in the Automation menu, or appears twice | It isn't in the autoload folder, or there's a second copy there. Keep exactly one `btk_karaoke_v2.lua`, and restart Aegisub. |
| The macro stops with `ABORT ... this file is macro OUTPUT` | It was run on a `_subs-final.ass`. Open `_subs-timed.ass` and run it there. |
| A lyric line sits on top of the next one | The line is longer than about 52 characters and wrapped. Split it in `_subs-timed.ass` and rerun the macro. |
| The tap tool shows the wrong number of syllables for a word | Check the word in `_lyr.txt` first, then use `S` and fix the split in Aegisub. |
| Step 6 refuses: `output already exists` | Add `--overwrite`. |
| The style check warns about the BG stack and the control bar | Expected. A stacked pair of BG lines is rare, and the overlap is accepted. |
