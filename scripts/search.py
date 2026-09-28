import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from matcher import (
    MATCHER_VERSION,
    classify_candidates,
)
from soulseek import (
    SoulseekClient,
    flatten_responses,
)


ROOT = Path(__file__).resolve().parent.parent
TRACKS_FILE = ROOT / "state" / "tracks.json"


def now():
    return datetime.now(
        timezone.utc
    ).isoformat()


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

    temporary.replace(
        TRACKS_FILE
    )


def build_search_query(track):
    spotify = track["spotify"]

    artist = spotify.get(
        "artist"
    ) or ""

    title = spotify.get(
        "title"
    ) or ""

    return f"{artist} {title}".strip()


def stable_candidate_id(candidate):
    value = "|".join(
        [
            str(
                candidate.get(
                    "username"
                )
                or ""
            ),
            str(
                candidate.get(
                    "filename"
                )
                or ""
            ),
            str(
                candidate.get(
                    "size"
                )
                or ""
            ),
            str(
                candidate.get(
                    "extension"
                )
                or ""
            ),
        ]
    )

    return (
        "c_"
        + hashlib.sha256(
            value.encode(
                "utf-8"
            )
        ).hexdigest()[:16]
    )


def normalize_raw_candidate(
    candidate
):
    result = dict(candidate)

    result["candidate_id"] = (
        stable_candidate_id(
            candidate
        )
    )

    return result


def ensure_track_state(track):
    track.setdefault(
        "search",
        {},
    )

    track.setdefault(
        "matching",
        {},
    )

    track.setdefault(
        "acquisition",
        {},
    )

    track.setdefault(
        "enrichment",
        {},
    )

    track.setdefault(
        "library",
        {},
    )


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

    tracks = state.get(
        "tracks",
        [],
    )

    print(
        f"Loaded {len(tracks)} tracks."
    )

    searched = 0

    for index, track in enumerate(
        tracks,
        start=1,
    ):
        ensure_track_state(
            track
        )

        spotify = track[
            "spotify"
        ]

        acquisition = track[
            "acquisition"
        ]

        status = acquisition.get(
            "status",
            "pending",
        )

        # Already successfully acquired/published tracks
        # do not need another search.
        if status in {
            "downloaded",
            "ready_to_publish",
            "published",
        }:
            print(
                f"[{index}/{len(tracks)}] "
                f"Skipping "
                f"{spotify['artist']} - "
                f"{spotify['title']} "
                f"(status={status})"
            )
            continue

        query = build_search_query(
            track
        )

        print()
        print(
            f"[{index}/{len(tracks)}] "
            f"Searching: {query}"
        )

        search_state = track[
            "search"
        ]

        search_state.setdefault(
            "attempts",
            0,
        )

        search_state[
            "attempts"
        ] += 1

        search_state[
            "status"
        ] = "searching"

        save_state(state)

        try:
            search_id = client.search(
                query=query,
                timeout_ms=15000,
                file_limit=10000,
                response_limit=100,
            )

            print(
                f"  Search ID: {search_id}"
            )

            result = client.wait_for_search(
                search_id,
                timeout_seconds=30,
            )

            raw_candidates = (
                flatten_responses(
                    result
                )
            )

            candidates = [
                normalize_raw_candidate(
                    candidate
                )
                for candidate in raw_candidates
            ]

            print(
                f"  Raw candidates: "
                f"{len(candidates)}"
            )

            # Persist the complete search result.
            search_state[
                "status"
            ] = "complete"

            search_state[
                "completed_at"
            ] = now()

            search_state[
                "queries"
            ] = search_state.get(
                "queries",
                [],
            )

            search_state[
                "queries"
            ].append(
                {
                    "query": query,
                    "search_id": search_id,
                    "status": "complete",
                    "candidate_count":
                        len(candidates),
                    "searched_at": now(),
                }
            )

            search_state[
                "candidates"
            ] = candidates

            # Run deterministic matching.
            classification = (
                classify_candidates(
                    track,
                    candidates,
                )
            )

            matching = track[
                "matching"
            ]

            matching[
                "status"
            ] = "complete"

            matching[
                "matcher_version"
            ] = MATCHER_VERSION

            matching[
                "deterministic"
            ] = {
                "accepted":
                    classification[
                        "accepted"
                    ],
                "llm_candidates":
                    classification[
                        "llm_candidates"
                    ],
                "rejected_count":
                    len(
                        classification[
                            "rejected"
                        ]
                    ),
                "ranked_count":
                    len(
                        classification[
                            "all_ranked"
                        ]
                    ),
            }

            # Keep the deterministic accepted candidates in the
            # legacy acquisition location for compatibility.
            accepted = (
                classification[
                    "accepted"
                ]
            )

            llm_candidates = (
                classification[
                    "llm_candidates"
                ]
            )

            if accepted:
                acquisition[
                    "status"
                ] = "matched"

                acquisition[
                    "match"
                ] = {
                    "query": query,
                    "search_id":
                        search_id,
                    "candidate_count":
                        len(
                            classification[
                                "all_ranked"
                            ]
                        ),
                    "candidates":
                        accepted,
                    "selection_source":
                        "deterministic",
                }

            elif llm_candidates:
                # Do not download these yet. The LLM stage will
                # select from them.
                acquisition[
                    "status"
                ] = "llm_screening"

                acquisition[
                    "match"
                ] = {
                    "query": query,
                    "search_id":
                        search_id,
                    "candidate_count":
                        len(
                            classification[
                                "all_ranked"
                            ]
                        ),
                    "candidates":
                        llm_candidates,
                    "selection_source":
                        "llm_pending",
                }

            else:
                acquisition[
                    "status"
                ] = "search_failed"

                acquisition[
                    "match"
                ] = {
                    "query": query,
                    "search_id":
                        search_id,
                    "candidate_count":
                        len(
                            classification[
                                "all_ranked"
                            ]
                        ),
                    "candidates": [],
                    "selection_source":
                        "none",
                }

            if accepted:
                best = accepted[0]

                print(
                    f"  Deterministic best: "
                    f"{best.get('filename')} "
                    f"from "
                    f"{best.get('username')} "
                    f"(score="
                    f"{best['score']})"
                )

            elif llm_candidates:
                print(
                    f"  LLM screening: "
                    f"{len(llm_candidates)} "
                    f"candidate(s)"
                )

                for candidate in (
                    llm_candidates[:5]
                ):
                    print(
                        f"    "
                        f"{candidate.get('filename')} "
                        f"from "
                        f"{candidate.get('username')} "
                        f"(score="
                        f"{candidate['score']})"
                    )

            else:
                print(
                    "  No viable candidates."
                )

            searched += 1

            client.delete_search(
                search_id
            )

        except Exception as exc:
            search_state[
                "status"
            ] = "failed"

            search_state[
                "error"
            ] = str(exc)

            acquisition[
                "status"
            ] = "search_failed"

            acquisition[
                "match"
            ] = {
                "query": query,
                "error": str(exc),
                "candidates": [],
            }

            print(
                f"  ERROR: {exc}",
                file=sys.stderr,
            )

        save_state(state)

    print()
    print(
        f"Searched {searched} tracks."
    )

    save_state(state)


if __name__ == "__main__":
    main()