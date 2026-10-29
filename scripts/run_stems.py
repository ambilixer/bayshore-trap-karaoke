#!/usr/bin/env python3
# Created by Andy Charlie W. (ambilixer) in collaboration with Claude (Anthropic).
"""
run_stems.py

Step 1. Separates a full track into vocal and instrumental stems with an
ensemble of two models, averaged sample by sample:
  - MelBand Roformer Inst v2  (via audio-separator)
  - htdemucs_ft               (via demucs)

Run it with the stems virtual environment's Python; run_pipeline.py does
that for you. Both models run on the GPU when PyTorch has CUDA. Check that it
does: audio-separator can install a CPU-only PyTorch build without saying so,
and the Roformer pass then takes over 20 minutes instead of under 3.

Usage:
    python run_stems.py <track_id> [options]

    where <track_id> is the track identifier, e.g. jimi-hendrix_purple-haze.
    Files, in the track's folder:

        <track_id>_original.wav   full track (input)
        <track_id>_vox.wav        vocal stem (output)
        <track_id>_inst.wav       instrumental stem (output)

Options:
    --preset normal|aggressive   Separation aggressiveness (default: normal)
    --denoise                    Enable denoising on Roformer output (default: off)
    --dry-run                    Print config without running

A note on the presets: they were set to approximate a separation made in UVR
(Ultimate Vocal Remover), whose "segment size" and "overlap" settings mean
different things for MDX-Net and Roformer models. Treat them as best-effort
mappings, and judge any change by listening.

Example:
    python run_stems.py jimi-hendrix_purple-haze
    python run_stems.py jimi-hendrix_purple-haze --preset aggressive --denoise
    python run_stems.py jimi-hendrix_purple-haze --dry-run
"""

import sys
import tempfile
from pathlib import Path

from paths import MODEL_DIR, get_track_path, get_track_dir

import numpy as np
import soundfile as sf
import torch


# ── Model ─────────────────────────────────────────────────────────────────────
# audio-separator downloads this into MODEL_DIR (config.toml, [models]) the
# first time it is used, and reuses the file after that.

ROFORMER_MODEL = "melband_roformer_inst_v2.ckpt"


# ── Presets ───────────────────────────────────────────────────────────────────
# Roformer (audio-separator):
#   chunk_size   processing window in samples (44100 = 1 second at 44.1 kHz).
#                UVR's "segment size 512" corresponds roughly to 8 * 44100.
#                Larger means more context, slower, possibly better quality.
#   overlap      fraction of each chunk shared with the next, 0.0 to 1.0.
#                Higher means smoother transitions and a slower run. Same
#                meaning as UVR's overlap.
#   batch_size   leave at 1 unless there is VRAM to spare.
#
# Demucs:
#   shifts       number of randomly shifted passes averaged together
#                (test-time augmentation). Higher means better quality, slower.
#                Same as UVR's "Shifts".
#   overlap      overlap between Demucs segments, 0.0 to 1.0. Same as UVR's.

PRESETS = {
    "normal": {
        "roformer": {
            "chunk_size": 352800,   # about 8 s at 44.1 kHz (UVR segment size 512, roughly)
            "overlap":    0.5,      # UVR overlap 0.5
            "batch_size": 1,
        },
        "demucs": {
            "shifts":  4,           # UVR Shifts 4
            "overlap": 0.25,        # UVR Overlap 0.25
        },
    },
    "aggressive": {
        "roformer": {
            "chunk_size": 352800,   # unchanged: a larger window does not help much
            "overlap":    0.75,     # smoother, slower
            "batch_size": 1,
        },
        "demucs": {
            "shifts":  8,           # twice the passes
            "overlap": 0.5,
        },
    },
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_wav(path: Path) -> tuple[np.ndarray, int]:
    """Load a WAV file as a float32 numpy array (samples, channels)."""
    audio, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return audio, sr


def save_wav(path: Path, audio: np.ndarray, sr: int) -> None:
    """Save a float32 numpy array as a 32-bit float WAV."""
    sf.write(str(path), audio, sr, subtype="FLOAT")
    print(f"  Saved → {path}")


def match_length(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Trim the longer of two arrays so both have the same length along axis 0."""
    la, lb = len(a), len(b)
    if la == lb:
        return a, b
    target = min(la, lb)
    return a[:target], b[:target]


def average_stems(stem_a: np.ndarray, stem_b: np.ndarray) -> np.ndarray:
    """Average two stems sample by sample, in float64."""
    a, b = match_length(stem_a, stem_b)
    return (a.astype(np.float64) + b.astype(np.float64)) / 2.0


def parse_args(argv: list) -> tuple:
    """Minimal parser for the options in the module docstring."""
    if len(argv) < 2:
        print("Usage: python run_stems.py <track_id> [--preset normal|aggressive] [--denoise] [--dry-run]")
        sys.exit(1)

    track_id = argv[1]
    preset  = "normal"
    denoise = False
    dry_run = False

    i = 2
    while i < len(argv):
        if argv[i] == "--preset" and i + 1 < len(argv):
            preset = argv[i + 1]
            if preset not in PRESETS:
                print(f"ERROR: Unknown preset '{preset}'. Choose: {list(PRESETS)}")
                sys.exit(1)
            i += 2
        elif argv[i] == "--denoise":
            denoise = True
            i += 1
        elif argv[i] == "--dry-run":
            dry_run = True
            i += 1
        else:
            print(f"Unknown argument: {argv[i]}")
            sys.exit(1)

    return track_id, preset, denoise, dry_run


# ── Roformer separation (audio-separator) ─────────────────────────────────────

def run_roformer(audio_path: Path, tmp_dir: Path, params: dict, denoise: bool) -> tuple[Path, Path]:
    """
    Run MelBand Roformer Inst v2 through audio-separator.
    Returns (vocals_path, instrumental_path), both inside tmp_dir.
    """
    from audio_separator.separator import Separator

    print("  Initialising audio-separator...")
    sep = Separator(
        output_dir=str(tmp_dir / "roformer"),
        model_file_dir=str(MODEL_DIR),
        output_format="WAV",
        output_bitrate=None,
        normalization_threshold=0.9,
        # Unconfirmed: audio-separator may read Roformer settings from
        # mdxc_params rather than mdx_params. If so, these are ignored and the
        # Roformer half of the ensemble runs at audio-separator's defaults.
        # Check against the installed version before relying on the presets
        # or --denoise.
        mdx_params={
            "segment_size":    params["chunk_size"] // 441,  # in 441-sample units, i.e. 10 ms at 44.1 kHz
            "overlap":         params["overlap"],
            "batch_size":      params["batch_size"],
            "enable_denoise":  denoise,
        },
    )

    print(f"  Loading model: {ROFORMER_MODEL} (downloads on first use)")
    sep.load_model(model_filename=ROFORMER_MODEL)

    print(f"  Separating (chunk={params['chunk_size']}, overlap={params['overlap']})...")
    output_files = sep.separate(str(audio_path))

    # audio-separator names its outputs like <stem>_(Vocals)_<model>.wav.
    roformer_out = tmp_dir / "roformer"
    vox_files  = list(roformer_out.glob("*(Vocals)*.wav"))
    inst_files = list(roformer_out.glob("*(Instrumental)*.wav"))

    if not vox_files or not inst_files:
        print(f"  WARNING: Could not find expected output files in {roformer_out}")
        print(f"  Files found: {list(roformer_out.glob('*.wav'))}")
        sys.exit(1)

    return vox_files[0], inst_files[0]


# ── Demucs separation ─────────────────────────────────────────────────────────

def run_demucs(audio_path: Path, params: dict) -> tuple[np.ndarray, np.ndarray, int]:
    """
    Run htdemucs_ft through its Python API. Returns (vocals, instrumental,
    sample_rate), the stems as (samples, channels) numpy arrays.

    Audio goes in and out through soundfile rather than torchaudio, whose
    load and save depend on torchcodec in recent versions.
    """
    from demucs.pretrained import get_model
    from demucs.apply import apply_model
    import scipy.signal

    print(f"  Loading htdemucs_ft model...")
    model = get_model('htdemucs_ft')
    model.eval()
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    model.to(device)
    print(f"  Using device: {device}")

    audio, sr = sf.read(str(audio_path), dtype='float32', always_2d=True)
    # audio shape: (samples, channels)

    # Resample to the model's own rate if needed (44.1 kHz for htdemucs_ft).
    model_sr = model.samplerate
    if sr != model_sr:
        print(f"  Resampling from {sr}Hz to {model_sr}Hz...")
        num_samples = int(len(audio) * model_sr / sr)
        audio = scipy.signal.resample(audio, num_samples, axis=0)
        sr = model_sr

    # The model wants a stereo (channels, samples) tensor: duplicate a mono
    # channel, drop anything past the second.
    audio_t = torch.tensor(audio.T, dtype=torch.float32)  # (channels, samples)
    if audio_t.shape[0] == 1:
        audio_t = audio_t.repeat(2, 1)
    elif audio_t.shape[0] > 2:
        audio_t = audio_t[:2]

    print(f"  Separating (shifts={params['shifts']}, overlap={params['overlap']})...")
    with torch.no_grad():
        sources = apply_model(
            model,
            audio_t[None].to(device),   # add a batch dimension
            shifts=params['shifts'],
            overlap=params['overlap'],
            split=True,
            progress=True,
        )
    # sources shape: (batch=1, num_sources, channels, samples)
    sources = sources[0].cpu()  # (num_sources, channels, samples)

    # The instrumental is every source except vocals (drums, bass, other), summed.
    vocal_idx = model.sources.index('vocals')
    vox_np    = sources[vocal_idx].numpy().T                          # (samples, channels)
    inst_np   = sum(sources[i].numpy().T
                    for i in range(len(model.sources))
                    if i != vocal_idx)                                 # (samples, channels)

    return vox_np, inst_np, sr


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    track_id, preset, denoise, dry_run = parse_args(sys.argv)

    audio_path  = get_track_path(track_id, "original.wav")
    vox_out     = get_track_path(track_id, "vox.wav")
    inst_out    = get_track_path(track_id, "inst.wav")
    get_track_dir(track_id).mkdir(parents=True, exist_ok=True)

    if not audio_path.exists():
        print(f"ERROR: Input file not found: {audio_path}")
        sys.exit(1)
    params = PRESETS[preset]

    print(f"\nrun_stems: {track_id}")
    print(f"  Input:   {audio_path}")
    print(f"  Preset:  {preset}")
    print(f"  Denoise: {denoise}")
    print(f"  Roformer params: {params['roformer']}")
    print(f"  Demucs params:   {params['demucs']}")
    print(f"  Model dir: {MODEL_DIR}")

    if dry_run:
        print("\nDry run — exiting without executing.")
        sys.exit(0)

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="run_stems_") as tmp:
        tmp_dir = Path(tmp)
        print(f"\n[1/4] Running Roformer (MelBand Roformer Inst v2)...")
        rf_vox, rf_inst = run_roformer(audio_path, tmp_dir, params["roformer"], denoise)

        print(f"\n[2/4] Running Demucs (htdemucs_ft)...")
        dm_vox_audio, dm_inst_audio, dm_sr = run_demucs(audio_path, params["demucs"])

        print(f"\n[3/4] Averaging stems (Avg/Avg ensemble)...")
        rf_vox_audio,  rf_sr  = load_wav(rf_vox)
        rf_inst_audio, _      = load_wav(rf_inst)

        if rf_sr != dm_sr:
            print(f"WARNING: Sample rate mismatch — Roformer {rf_sr}Hz vs Demucs {dm_sr}Hz")
            print("         Stems may not average correctly. Check your audio pipeline.")

        vox_avg  = average_stems(rf_vox_audio,  dm_vox_audio)
        inst_avg = average_stems(rf_inst_audio, dm_inst_audio)

        print(f"\n[4/4] Writing outputs...")
        save_wav(vox_out,  vox_avg,  rf_sr)
        save_wav(inst_out, inst_avg, rf_sr)

    print(f"\nDone.")
    print(f"  Vocal stem:        {vox_out}")
    print(f"  Instrumental stem: {inst_out}")
    print(f"\nNext steps:")
    print(f"  python run_whisperx.py {track_id}")
    print(f"  python whisperx_to_ass.py {track_id}")
