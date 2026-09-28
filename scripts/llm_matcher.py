import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI


ROOT = Path(__file__).resolve().parent.parent
TRACKS_FILE = ROOT / "state" / "tracks.json"

MODEL = os.environ.get(
    "OPENAI_MATCH_MODEL",
    "gpt-5-nano",
)

LLM_VERSION = 1

AUTO_ACCEPT_CONFIDENCE = 85


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


def build_prompt(track, candidates):
    spotify = track[
        "spotify"
    ]

    target = {
        "artist": spotify.get(
            "artist"
        ),
        "artists": spotify.get(
            "artists"
        ),
        "album": spotify.get(
            "album"
        ),
        "album_artist":
            spotify.get(
                "album_artist"
            ),
        "title": spotify.get(
            "title"
        ),
        "track_number":
            spotify.get(
                "track_number"
            ),
        "disc_number":
            spotify.get(
                "disc_number"
            ),
        "duration_ms":
            spotify.get(
                "duration_ms"
            ),
        "release_date":
            spotify.get(
                "release_date"
            ),
        "isrc":
            spotify.get(
                "isrc"
            ),
    }

    compact_candidates = []

    for candidate in candidates:
        compact_candidates.append(
            {
                "candidate_id":
                    candidate.get(
                        "candidate_id"
                    ),
                "username":
                    candidate.get(
                        "username"
                    ),
                "filename":
                    candidate.get(
                        "filename"
                    ),
                "extension":
                    candidate.get(
                        "extension"
                    ),
                "size":
                    candidate.get(
                        "size"
                    ),
                "peer":
                    candidate.get(
                        "peer"
                    ),
                "deterministic_score":
                    candidate.get(
                        "score"
                    ),
                "deterministic_match":
                    candidate.get(
                        "match"
                    ),
            }
        )

    return f"""
You are selecting a music file from Soulseek search results.

Determine which candidate most likely represents the exact target
recording described by the Spotify metadata.

Important rules:

- Artist identity matters.
- Track title identity matters.
- Album identity matters.
- Track and disc numbers are useful evidence.
- Live recordings are not the same as studio recordings unless the
  target itself indicates a live recording.
- Acoustic versions are not the same as the normal recording.
- Demos are not the same as the normal recording.
- Piano demos are not the same as the normal recording.
- Remixes are not the same as the normal recording.
- Remasters may represent the same composition, but distinguish them
  from the target when the evidence indicates a materially different
  release.
- A directory containing the target artist is evidence, but do not
  assume the track is correct merely because the artist matches.
- A filename containing the target title is not sufficient by itself.
- Do not invent information that is not present in the supplied data.
- If no candidate is sufficiently convincing, choose reject.
- Return exactly one candidate_id when accepting or rejecting based on
  the candidate set.
- Confidence must represent confidence in the selected decision.

TARGET:
{json.dumps(
    target,
    ensure_ascii=False,
    indent=2,
)}

CANDIDATES:
{json.dumps(
    compact_candidates,
    ensure_ascii=False,
    indent=2,
)}
""".strip()


def screen_candidates(
    client,
    track,
    candidates,
):
    schema = {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": [
                    "accept",
                    "reject",
                ],
            },
            "candidate_id": {
                "type": [
                    "string",
                    "null",
                ],
            },
            "confidence": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
            },
            "version_classification": {
                "type": "string",
                "enum": [
                    "normal",
                    "live",
                    "acoustic",
                    "demo",
                    "remix",
                    "remaster",
                    "other_alternate",
                    "unknown",
                ],
            },
            "reason": {
                "type": "string",
            },
        },
        "required": [
            "decision",
            "candidate_id",
            "confidence",
            "version_classification",
            "reason",
        ],
        "additionalProperties": False,
    }

    response = client.responses.create(
        model=MODEL,
        input=[
            {
                "role": "system",
                "content": (
                    "You are a conservative music-file "
                    "identity classifier."
                ),
            },
            {
                "role": "user",
                "content": build_prompt(
                    track,
                    candidates,
                ),
            },
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "music_match",
                "strict": True,
                "schema": schema,
            }
        },
        max_output_tokens=300,
    )

    parsed = json.loads(
        response.output_text
    )

    return parsed


def main():
    if not TRACKS_FILE.exists():
        raise FileNotFoundError(
            f"Missing {TRACKS_FILE}"
        )

    api_key = os.environ.get(
        "OPENAI_API_KEY"
    )

    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set."
        )

    client = OpenAI(
        api_key=api_key
    )

    state = load_state()

    tracks = state.get(
        "tracks",
        [],
    )

    screened = 0
    skipped = 0

    print(
        f"Loaded {len(tracks)} tracks."
    )

    for index, track in enumerate(
        tracks,
        start=1,
    ):
        spotify = track[
            "spotify"
        ]

        matching = track.setdefault(
            "matching",
            {},
        )

        deterministic = matching.get(
            "deterministic",
            {},
        )

        llm_candidates = (
            deterministic.get(
                "llm_candidates",
                [],
            )
        )

        # If deterministic matching already found an
        # unambiguous candidate, there is no reason to call
        # the LLM.
        if not llm_candidates:
            if deterministic.get(
                "accepted"
            ):
                skipped += 1

                print(
                    f"[{index}/{len(tracks)}] "
                    f"Skipping LLM: "
                    f"{spotify['artist']} - "
                    f"{spotify['title']} "
                    f"(deterministic match)"
                )

                continue

            skipped += 1

            print(
                f"[{index}/{len(tracks)}] "
                f"Skipping LLM: "
                f"{spotify['artist']} - "
                f"{spotify['title']} "
                f"(no candidates)"
            )

            continue

        # Don't pay for the same decision twice.
        existing_llm = matching.get(
            "llm"
        )

        if (
            existing_llm
            and existing_llm.get(
                "status"
            )
            == "complete"
        ):
            skipped += 1

            print(
                f"[{index}/{len(tracks)}] "
                f"Skipping LLM: "
                f"{spotify['artist']} - "
                f"{spotify['title']} "
                f"(already screened)"
            )

            continue

        # Only send the strongest ambiguous candidates.
        candidates = sorted(
            llm_candidates,
            key=lambda candidate: (
                candidate.get(
                    "score",
                    0,
                ),
            ),
            reverse=True,
        )[:10]

        print()
        print(
            f"[{index}/{len(tracks)}] "
            f"LLM screening: "
            f"{spotify['artist']} - "
            f"{spotify['title']}"
        )

        print(
            f"  Candidates sent: "
            f"{len(candidates)}"
        )

        try:
            result = screen_candidates(
                client,
                track,
                candidates,
            )

            candidate_map = {
                candidate[
                    "candidate_id"
                ]: candidate
                for candidate in candidates
            }

            selected_id = result.get(
                "candidate_id"
            )

            selected = (
                candidate_map.get(
                    selected_id
                )
                if selected_id
                else None
            )

            confidence = int(
                result.get(
                    "confidence",
                    0,
                )
            )

            decision = result.get(
                "decision"
            )

            # Never accept a low-confidence LLM answer.
            if (
                decision == "accept"
                and selected
                and confidence
                >= AUTO_ACCEPT_CONFIDENCE
            ):
                final_status = "accepted"
            elif (
                decision == "reject"
                or not selected
            ):
                final_status = "rejected"
            else:
                final_status = "needs_review"

            matching[
                "llm"
            ] = {
                "status": "complete",
                "version":
                    LLM_VERSION,
                "model": MODEL,
                "screened_at": now(),
                "decision":
                    final_status,
                "candidate_id":
                    selected_id,
                "confidence":
                    confidence,
                "version_classification":
                    result.get(
                        "version_classification"
                    ),
                "reason":
                    result.get(
                        "reason"
                    ),
                "candidates_considered":
                    [
                        candidate[
                            "candidate_id"
                        ]
                        for candidate in candidates
                    ],
            }

            acquisition = track.setdefault(
                "acquisition",
                {},
            )

            if (
                final_status
                == "accepted"
            ):
                # Put the selected candidate first so the existing
                # downloader can consume it.
                ordered = [
                    selected
                ]

                ordered.extend(
                    candidate
                    for candidate in candidates
                    if candidate[
                        "candidate_id"
                    ]
                    != selected_id
                )

                acquisition[
                    "status"
                ] = "matched"

                acquisition[
                    "match"
                ] = {
                    "query":
                        track.get(
                            "search",
                            {},
                        ).get(
                            "queries",
                            [{}],
                        )[-1].get(
                            "query",
                            "",
                        ),
                    "candidate_count":
                        len(
                            candidates
                        ),
                    "candidates":
                        ordered,
                    "selection_source":
                        "llm",
                    "selected_candidate_id":
                        selected_id,
                }

                print(
                    f"  ACCEPT: "
                    f"{selected.get('filename')} "
                    f"(confidence="
                    f"{confidence})"
                )

            elif (
                final_status
                == "needs_review"
            ):
                acquisition[
                    "status"
                ] = "needs_review"

                acquisition[
                    "match"
                ] = {
                    "candidates":
                        candidates,
                    "selection_source":
                        "llm_review",
                    "selected_candidate_id":
                        selected_id,
                }

                print(
                    f"  REVIEW: "
                    f"{selected.get('filename') if selected else 'none'} "
                    f"(confidence="
                    f"{confidence})"
                )

            else:
                acquisition[
                    "status"
                ] = "search_failed"

                acquisition[
                    "match"
                ] = {
                    "candidates": [],
                    "selection_source":
                        "llm_rejected",
                    "selected_candidate_id":
                        selected_id,
                }

                print(
                    f"  REJECT: "
                    f"confidence="
                    f"{confidence}"
                )

            screened += 1

        except Exception as exc:
            matching[
                "llm"
            ] = {
                "status": "failed",
                "version":
                    LLM_VERSION,
                "model": MODEL,
                "error": str(exc),
                "failed_at": now(),
            }

            print(
                f"  LLM ERROR: {exc}",
                file=sys.stderr,
            )

        save_state(state)

    print()
    print(
        f"LLM screened: {screened}"
    )
    print(
        f"LLM skipped: {skipped}"
    )

    save_state(state)


if __name__ == "__main__":
    main()