#!/usr/bin/env python3
# Created by Andy Charlie W. (ambilixer) in collaboration with Claude (Anthropic).
"""
track_db.py

A JSON database of every track in the pipeline: how far each one has got,
and free-text notes.

It deliberately holds nothing that _meta.json or _yt.json already hold
(artist, title, genre, youtube_url and so on). Those files are the single
source of truth for track facts; a copy here would go stale the moment one
of them was edited, with no way to re-sync. This file records only what
exists nowhere else on disk.

Fields written automatically by run_pipeline.py:
    track_id, status, date_started, date_completed

Field set by hand, through the command line below:
    notes    free text

Usage:
    python track_db.py <track_id> --notes "some note"
    python track_db.py <track_id> --status <status>     (manual override)
    python track_db.py <track_id> --show
    python track_db.py --list

The database lives at the db_path set in config.toml ([paths]), by default
track_db.json beside the tracks folder.
"""

import argparse
import json
from datetime import date

from paths import DB_PATH

FIELDS = [
    "track_id", "status",
    "date_started", "date_completed",
    "notes",
]


# ── Core DB operations ───────────────────────────────────────────────────────

def load_db() -> dict:
    """The whole database as {track_id: record}, or {} if it does not exist yet."""
    if not DB_PATH.exists():
        return {}
    with open(DB_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_db(db: dict) -> None:
    """Write the whole database back, keys sorted so diffs stay readable."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False, sort_keys=True)


def upsert_track(track_id: str, **fields) -> dict:
    """
    Create or update a track record. Only overwrites fields explicitly
    passed with a non-None value; everything else is preserved as-is.
    date_started is set once, on first creation, and never overwritten.
    Unknown field names are ignored with a warning (typo guard).
    """
    db = load_db()
    is_new = track_id not in db
    record = db.get(track_id, {f: None for f in FIELDS})
    record["track_id"] = track_id

    if is_new:
        record["date_started"] = date.today().isoformat()

    for k, v in fields.items():
        if k not in FIELDS:
            print(f"  WARNING: track_db.upsert_track got unknown field '{k}' -- ignored")
            continue
        if v is not None:
            record[k] = v

    db[track_id] = record
    save_db(db)
    return record


def get_track(track_id: str) -> dict | None:
    """One track's record, or None if the track is not in the database."""
    return load_db().get(track_id)


# ── CLI ───────────────────────────────────────────────────────────────────────

def print_record(track_id: str, record: dict | None) -> None:
    """Print a record one field per line, or say there is none."""
    if record is None:
        print(f"No record for '{track_id}'.")
        return
    width = max(len(k) for k in FIELDS)
    for k in FIELDS:
        print(f"  {k.ljust(width)} : {record.get(k)}")


def main():
    """Command-line entry point. See the module docstring for usage."""
    parser = argparse.ArgumentParser(description="Manage the track database.")
    parser.add_argument("track_id", nargs="?", help="Track identifier")
    parser.add_argument("--notes", help="Free-text notes (overwrites existing notes)")
    parser.add_argument("--status", help="Manually override status")
    parser.add_argument("--show", action="store_true", help="Print the record for track_id")
    parser.add_argument("--list", action="store_true", help="List all tracks with their status")
    args = parser.parse_args()

    if args.list:
        db = load_db()
        if not db:
            print("Database is empty.")
            return
        width = max(len(t) for t in db)
        for track_id, record in sorted(db.items()):
            print(f"  {track_id.ljust(width)}  {record.get('status')}")
        return

    if not args.track_id:
        parser.error("track_id is required unless --list is given")

    if args.show:
        print_record(args.track_id, get_track(args.track_id))
        return

    updates = {}
    if args.notes is not None:
        updates["notes"] = args.notes
    if args.status:
        updates["status"] = args.status

    if not updates:
        parser.error("no fields given to update -- see --help")

    record = upsert_track(args.track_id, **updates)
    print(f"Updated '{args.track_id}':")
    print_record(args.track_id, record)


if __name__ == "__main__":
    main()
