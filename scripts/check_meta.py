#!/usr/bin/env python3
# Created by Andy Charlie W. (ambilixer) in collaboration with Claude (Anthropic).
"""
check_meta.py

Validates every track's <track_id>_meta.json: UTF-8 encoding, JSON syntax,
the keys the pipeline reads, and the types of year and bpm. Run it after
editing a meta file by hand. Nothing reads _meta.json until the title cards
(step 4), so a broken file otherwise surfaces only after the slow steps have
run.

Usage:
    python check_meta.py            # check all tracks
    python check_meta.py <track_id> # check one track

Reads every _meta.json under the tracks folder (config.toml, [paths]
tracks_dir). Exit code 0 if all files pass, 1 if any fail.
"""

import json
import sys

from paths import TRACKS_DIR

# Keys that render_titles.py and generate_yt.py read. A missing one raises
# KeyError partway through the pipeline.
REQUIRED = ["title", "artist", "album", "year"]
# Keys that may be present. Anything else is reported as unrecognized, which
# is how a misspelled key gets caught.
OPTIONAL = ["bpm", "request_source", "youtube_url"]


def check_one(path):
    """Return a list of problem strings. Empty list means the file is fine."""
    problems = []

    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        return [f"not valid UTF-8: {e}"]
    except OSError as e:
        return [f"could not read: {e}"]

    try:
        meta = json.loads(raw)
    except json.JSONDecodeError as e:
        # Show the reported line and the one before it. The parser reports the
        # start of the next token, so the real defect (usually a missing comma)
        # is often at the end of the previous line.
        lines = raw.splitlines()
        ctx = []
        for n in (e.lineno - 1, e.lineno):
            if 1 <= n <= len(lines):
                ctx.append(f"      line {n}: {lines[n - 1]}")
        return [f"invalid JSON: {e}"] + ctx

    if not isinstance(meta, dict):
        return [f"top level is {type(meta).__name__}, expected an object"]

    for key in REQUIRED:
        if key not in meta:
            problems.append(f"missing required key: {key!r}")
        elif meta[key] in (None, ""):
            problems.append(f"required key {key!r} is empty")

    if "year" in meta:
        year = str(meta["year"])
        if not (year.isdigit() and len(year) == 4):
            problems.append(f"year {meta['year']!r} is not a 4-digit number")

    # bool is a subclass of int in Python, so True would otherwise pass.
    if "bpm" in meta and meta["bpm"] is not None:
        if not isinstance(meta["bpm"], int) or isinstance(meta["bpm"], bool):
            problems.append(f"bpm {meta['bpm']!r} is not an integer")
        elif meta["bpm"] <= 0:
            problems.append(f"bpm {meta['bpm']!r} is not positive")

    unknown = set(meta) - set(REQUIRED) - set(OPTIONAL)
    if unknown:
        problems.append(f"unrecognized key(s): {', '.join(sorted(unknown))}")

    return problems


def main():
    """Check one track or all of them, print a line per file, exit 1 on any failure."""
    base = TRACKS_DIR
    if not base.exists():
        print(f"ERROR: tracks directory not found: {base}")
        sys.exit(1)

    if len(sys.argv) > 1:
        paths = [base / sys.argv[1] / f"{sys.argv[1]}_meta.json"]
        if not paths[0].exists():
            print(f"ERROR: no meta file at {paths[0]}")
            sys.exit(1)
    else:
        paths = sorted(base.glob("*/*_meta.json"))

    if not paths:
        print(f"No _meta.json files found under {base}")
        sys.exit(1)

    failed = 0
    for path in paths:
        problems = check_one(path)
        if problems:
            failed += 1
            print(f"FAIL  {path.parent.name}")
            for p in problems:
                print(f"        {p}")
        else:
            print(f"ok    {path.parent.name}")

    print()
    if failed:
        print(f"{failed} of {len(paths)} meta file(s) have problems.")
        sys.exit(1)

    print(f"All {len(paths)} meta file(s) valid.")
    sys.exit(0)


if __name__ == "__main__":
    main()
