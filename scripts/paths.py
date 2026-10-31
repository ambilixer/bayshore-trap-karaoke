# Created by Andy Charlie W. (ambilixer) in collaboration with Claude (Anthropic).
"""
paths.py

Shared path and environment settings, imported by every script.

Settings come from config.toml in the repository root (the folder above
scripts/). Copy config.example.toml there to start. The file and every
setting in it are optional: anything not set falls back to the defaults
below, which put tracks/ inside the repository and track_db.json beside it.

Windows paths in config.toml need single quotes or forward slashes. Inside
double quotes a backslash starts an escape sequence and the file will not
load.

Anything in config.toml that is not one of the settings below is reported as
a warning, most usefully a setting under the wrong [section], which would
otherwise be ignored without a word.
"""

import sys
import tomllib
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_DIR    = SCRIPTS_DIR.parent
CONFIG_PATH = REPO_DIR / "config.toml"


def _load_config() -> dict:
    """Read config.toml, or return {} if there is none.

    Invalid TOML exits with a readable message instead of a traceback. In
    practice the cause is almost always a Windows path in double quotes.
    """
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        sys.exit(f"ERROR: {CONFIG_PATH} is not valid TOML: {e}\n"
                 f"  Windows paths need single quotes or forward slashes.")


_CONFIG = _load_config()

# Every setting config.toml can hold, by section.
_KNOWN = {
    "paths":  ("tracks_dir", "db_path"),
    "python": ("stems", "karaoke"),
    "models": ("model_dir",),
}


def _check_config() -> None:
    """Warn about anything in config.toml that this file does not read.

    TOML files every key under the [section] above it, so tracks_dir written
    below [models] is read as a models setting, and the default tracks folder
    is used instead. Warnings go to stderr and do not stop the run. Every
    script imports this file, so each step of a pipeline run repeats them.
    """
    home = {key: section for section, keys in _KNOWN.items() for key in keys}

    def warn(msg: str) -> None:
        print(f"WARNING: {CONFIG_PATH}: {msg}", file=sys.stderr)

    for section, body in _CONFIG.items():
        if not isinstance(body, dict):
            # A key above the first [section] header.
            where = f"; it belongs under [{home[section]}]" if section in home else ""
            warn(f"{section} is not under any [section]{where}. Ignored.")
            continue
        if section not in _KNOWN:
            warn(f"unknown section [{section}]. Everything in it is ignored.")
        for key in body:
            if section in _KNOWN and key in _KNOWN[section]:
                continue
            if key in home:
                warn(f"{key} is under [{section}] but belongs under [{home[key]}]. "
                     f"Ignored; the default is used.")
            elif section in _KNOWN:
                warn(f"unknown setting {key} under [{section}]. Ignored.")


_check_config()


def _path(section: str, key: str, default: Path) -> Path:
    """A path setting. Relative paths are taken from the repository root."""
    value = _CONFIG.get(section, {}).get(key)
    if value in (None, ""):
        return default
    if not isinstance(value, str):
        sys.exit(f"ERROR: [{section}] {key} in {CONFIG_PATH} must be a quoted path")
    p = Path(value).expanduser()
    return p if p.is_absolute() else REPO_DIR / p


TRACKS_DIR     = _path("paths", "tracks_dir", REPO_DIR / "tracks")
DB_PATH        = _path("paths", "db_path", TRACKS_DIR.parent / "track_db.json")

# Interpreters for the two virtual environments: stems for step 1, karaoke for
# steps 2 and 3. Unset, they fall back to whichever Python is running.
PYTHON_STEMS   = str(_path("python", "stems", Path(sys.executable)))
PYTHON_KARAOKE = str(_path("python", "karaoke", Path(sys.executable)))

# Where audio-separator downloads the Roformer model on first use.
MODEL_DIR      = _path("models", "model_dir", Path.home() / ".cache" / "audio-separator")


def get_track_dir(track_id: str) -> Path:
    """The folder that holds every file for one track."""
    return TRACKS_DIR / track_id


def get_track_path(track_id: str, suffix: str) -> Path:
    """One track file, named <track_id>_<suffix>.

    e.g. get_track_path('jimi-hendrix_purple-haze', 'original.wav')
    """
    return get_track_dir(track_id) / f"{track_id}_{suffix}"
