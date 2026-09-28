import json
from pathlib import Path

from spotify import resolve_urls


ROOT = Path(__file__).resolve().parent.parent
URLS_FILE = ROOT / "urls.txt"
STATE_DIR = ROOT / "state"
TRACKS_FILE = STATE_DIR / "tracks.json"


def load_urls():
    if not URLS_FILE.exists():
        raise FileNotFoundError(f"Missing {URLS_FILE}")
    return URLS_FILE.read_text(encoding="utf-8").splitlines()


def load_existing_state():
    if not TRACKS_FILE.exists():
        return {}

    try:
        data = json.loads(TRACKS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in {TRACKS_FILE}: {exc}"
        ) from exc

    return {
        track.get("spotify", {}).get("id"): track
        for track in data.get("tracks", [])
        if track.get("spotify", {}).get("id")
    }


def default_acquisition():
    return {
        "status": "pending",
        "attempts": 0,
        "match": None,
        "file": None,
        "library": None,
    }


def build_track_state(spotify_track, existing=None, sources=None):
    if existing:
        acquisition = existing.get(
            "acquisition",
            default_acquisition(),
        )
        enrichment = existing.get(
            "enrichment",
            {"artwork": None, "lyrics": None},
        )
    else:
        acquisition = default_acquisition()
        enrichment = {"artwork": None, "lyrics": None}

    state = {
        "spotify": spotify_track,
        "sources": sources or {
            "album_ids": [],
            "playlist_ids": [],
            "track_ids": [],
        },
        "acquisition": acquisition,
        "enrichment": enrichment,
    }

    if existing and existing.get("playlist_sources"):
        state["playlist_sources"] = existing["playlist_sources"]

    return state


def merge_sources(existing, current):
    merged = {
        "album_ids": [],
        "playlist_ids": [],
        "track_ids": [],
    }

    for source in (current or {},):
        for key in merged:
            for value in source.get(key, []):
                if value not in merged[key]:
                    merged[key].append(value)

    return merged


def main():
    urls = load_urls()
    print(f"Found {len(urls)} input lines.")

    existing_tracks = load_existing_state()
    resolved = resolve_urls(urls)

    current_tracks = resolved["tracks"]
    current_sources = resolved["track_sources"]

    # Preserve previously acquired library tracks even if they are no
    # longer present in urls.txt. This is required for playlist removal:
    # removing a track from a playlist must not delete the music.
    tracks_by_id = dict(existing_tracks)

    for spotify_track in current_tracks:
        track_id = spotify_track["id"]
        existing = existing_tracks.get(track_id)
        state = build_track_state(
            spotify_track,
            existing=existing,
            sources=current_sources.get(track_id),
        )

        if existing:
            acquisition = state["acquisition"]
            old_library = acquisition.get("library")
            if old_library:
                state["acquisition"]["library"] = old_library

        tracks_by_id[track_id] = state

    # Mark the currently configured source set. Existing published tracks
    # remain in state but are no longer considered active acquisition work.
    current_track_ids = set(current_sources)
    for track_id, track in tracks_by_id.items():
        track["active_source"] = track_id in current_track_ids

    tracks = list(tracks_by_id.values())

    STATE_DIR.mkdir(parents=True, exist_ok=True)

    output = {
        "version": 3,
        "playlists": resolved["playlists"],
        "tracks": tracks,
    }

    TRACKS_FILE.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    preserved = len(existing_tracks)
    new = len(tracks) - preserved

    print()
    print(f"Resolved {len(current_tracks)} current unique tracks.")
    print(f"Preserved state for {preserved} existing tracks.")
    print(f"Created state for {new} new tracks.")
    print(f"Configured {len(resolved['playlists'])} playlist(s).")
    print(f"Wrote {TRACKS_FILE}")


if __name__ == "__main__":
    main()
