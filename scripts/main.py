import json
from pathlib import Path

from spotify import resolve_urls


ROOT = Path(__file__).resolve().parent.parent
URLS_FILE = ROOT / "urls.txt"
STATE_DIR = ROOT / "state"
TRACKS_FILE = STATE_DIR / "tracks.json"


def load_urls():
    if not URLS_FILE.exists():
        raise FileNotFoundError(
            f"Missing {URLS_FILE}"
        )

    return URLS_FILE.read_text(
        encoding="utf-8"
    ).splitlines()


def load_existing_state():
    if not TRACKS_FILE.exists():
        return {}

    try:
        data = json.loads(
            TRACKS_FILE.read_text(
                encoding="utf-8"
            )
        )
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in {TRACKS_FILE}: {exc}"
        ) from exc

    existing = {}

    for track in data.get("tracks", []):
        spotify = track.get("spotify", {})
        track_id = spotify.get("id")

        if track_id:
            existing[track_id] = track

    return existing


def build_track_state(
    spotify_track,
    existing=None,
):
    if existing:
        acquisition = existing.get(
            "acquisition",
            {
                "status": "pending",
                "attempts": 0,
                "match": None,
                "file": None,
                "library": None,
            },
        )

        enrichment = existing.get(
            "enrichment",
            {
                "artwork": None,
                "lyrics": None,
            },
        )
    else:
        acquisition = {
            "status": "pending",
            "attempts": 0,
            "match": None,
            "file": None,
            "library": None,
        }

        enrichment = {
            "artwork": None,
            "lyrics": None,
        }

    return {
        "spotify": spotify_track,
        "acquisition": acquisition,
        "enrichment": enrichment,
    }


def main():
    urls = load_urls()

    print(f"Found {len(urls)} input lines.")

    existing_tracks = load_existing_state()

    if existing_tracks:
        print(
            f"Loaded {len(existing_tracks)} existing tracks "
            "from state."
        )

    spotify_tracks = resolve_urls(urls)

    tracks = []

    preserved = 0
    new = 0

    for spotify_track in spotify_tracks:
        track_id = spotify_track["id"]

        existing = existing_tracks.get(track_id)

        if existing:
            preserved += 1
        else:
            new += 1

        tracks.append(
            build_track_state(
                spotify_track,
                existing=existing,
            )
        )

    STATE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    output = {
        "version": 2,
        "tracks": tracks,
    }

    TRACKS_FILE.write_text(
        json.dumps(
            output,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print()
    print(
        f"Resolved {len(tracks)} unique tracks."
    )
    print(
        f"Preserved state for {preserved} existing tracks."
    )
    print(
        f"Created state for {new} new tracks."
    )
    print(f"Wrote {TRACKS_FILE}")


if __name__ == "__main__":
    main()
