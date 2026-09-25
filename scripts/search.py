import json
import os
import sys
from pathlib import Path

from matcher import rank_candidates
from soulseek import (
    SoulseekClient,
    flatten_responses,
)


ROOT = Path(__file__).resolve().parent.parent
TRACKS_FILE = ROOT / "state" / "tracks.json"


def load_state():
    with TRACKS_FILE.open(
        encoding="utf-8"
    ) as handle:
        return json.load(handle)


def save_state(data):
    temporary = TRACKS_FILE.with_suffix(
        ".tmp"
    )

    temporary.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    temporary.replace(TRACKS_FILE)


def build_search_query(track):
    spotify = track["spotify"]

    artist = spotify.get("artist") or ""
    title = spotify.get("title") or ""

    return f"{artist} {title}".strip()


def main():
    if not TRACKS_FILE.exists():
        raise FileNotFoundError(
            f"Missing {TRACKS_FILE}"
        )

    state = load_state()

    client = SoulseekClient(
        base_url=os.environ.get(
            "SLSKD_URL",
            "http://127.0.0.1:5030",
        ),
        api_key=os.environ.get(
            "SLSKD_API_KEY"
        ),
    )

    tracks = state.get("tracks", [])

    print(f"Loaded {len(tracks)} tracks.")

    searched = 0

    for index, track in enumerate(
        tracks,
        start=1,
    ):
        spotify = track["spotify"]

        acquisition = track.setdefault(
            "acquisition",
            {},
        )

        status = acquisition.get(
            "status",
            "pending",
        )

        if status not in {
            "pending",
            "searching",
            "search_failed",
        }:
            print(
                f"[{index}/{len(tracks)}] "
                f"Skipping "
                f"{spotify['artist']} - "
                f"{spotify['title']} "
                f"(status={status})"
            )
            continue

        query = build_search_query(track)

        print()
        print(
            f"[{index}/{len(tracks)}] "
            f"Searching: {query}"
        )

        acquisition["status"] = "searching"
        acquisition["attempts"] = (
            acquisition.get("attempts", 0) + 1
        )

        save_state(state)

        try:
            search_id = client.search(
                query=query,
                timeout_ms=15000,
                file_limit=10000,
                response_limit=100,
            )

            print(f"  Search ID: {search_id}")

            result = client.wait_for_search(
                search_id,
                timeout_seconds=30,
            )

            candidates = flatten_responses(
                result
            )

            print(
                f"  Raw candidates: "
                f"{len(candidates)}"
            )

            ranked = rank_candidates(
                track,
                candidates,
            )

            top_candidates = ranked[:20]

            acquisition["status"] = (
                "matched"
                if top_candidates
                else "search_failed"
            )

            acquisition["match"] = {
                "query": query,
                "search_id": search_id,
                "candidate_count": len(ranked),
                "candidates": top_candidates,
            }

            if top_candidates:
                best = top_candidates[0]

                print(
                    f"  Best: "
                    f"{best.get('filename')} "
                    f"from "
                    f"{best.get('username')} "
                    f"(score="
                    f"{best['match']['score']})"
                )
            else:
                print("  No candidates found.")

            searched += 1

            client.delete_search(search_id)

        except Exception as exc:
            acquisition["status"] = "search_failed"

            acquisition["match"] = {
                "query": query,
                "error": str(exc),
            }

            print(
                f"  ERROR: {exc}",
                file=sys.stderr,
            )

        save_state(state)

    print()
    print(f"Searched {searched} tracks.")

    save_state(state)


if __name__ == "__main__":
    main()
