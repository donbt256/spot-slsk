import json
import os
import sys
import time
from pathlib import Path

from soulseek import SoulseekClient


STATE_PATH = Path("state/tracks.json")


def load_state():
    with STATE_PATH.open(
        "r",
        encoding="utf-8",
    ) as handle:
        return json.load(handle)


def save_state(state):
    temporary = STATE_PATH.with_suffix(".tmp")

    with temporary.open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            state,
            handle,
            indent=2,
            ensure_ascii=False,
        )
        handle.write("\n")

    temporary.replace(STATE_PATH)


def main():
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

    pending = []

    for track in tracks:
        acquisition = track.setdefault(
            "acquisition",
            {},
        )

        match = acquisition.get("match")

        if not match:
            continue

        username = match.get("username")
        filename = match.get("filename")

        if not username or not filename:
            continue

        pending.append({
            "track": track,
            "username": username,
            "filename": filename,
            "size": match.get("size"),
        })

    print(
        f"Found {len(pending)} matched tracks to download.",
        flush=True,
    )

    if not pending:
        print("Nothing to download.")
        return

    # Group downloads by Soulseek username because slskd's download
    # endpoint accepts one username per request.
    grouped = {}

    for item in pending:
        grouped.setdefault(
            item["username"],
            [],
        ).append(item)

    queued = 0

    for username, items in grouped.items():
        files = [
            {
                "filename": item["filename"],
                "size": item["size"],
            }
            for item in items
        ]

        print(
            f"Queueing {len(files)} file(s) from {username}...",
            flush=True,
        )

        try:
            client.enqueue_downloads(
                username,
                files,
            )

            for item in items:
                acquisition = item["track"]["acquisition"]

                acquisition["status"] = "downloading"

                acquisition["download"] = {
                    "username": username,
                    "filename": item["filename"],
                    "size": item["size"],
                }

                queued += 1

            save_state(state)

        except Exception as exc:
            print(
                f"ERROR queueing downloads from "
                f"{username}: {exc}",
                file=sys.stderr,
                flush=True,
            )

    print(
        f"Queued {queued} download(s).",
        flush=True,
    )

    if queued == 0:
        raise RuntimeError(
            "No downloads were successfully queued."
        )

    save_state(state)

    print(
        "Downloads are now queued concurrently in slskd.",
        flush=True,
    )


if __name__ == "__main__":
    main()