import json
import os
import subprocess
import sys
from pathlib import Path

from release import group_releases


STATE_PATH = Path("state/tracks.json")

SUCCESS_STATUSES = {
    "downloaded",
    "ready_to_publish",
    "published",
}


def load_state():
    with STATE_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_checkpoint(label):
    subprocess.run(
        ["git", "config", "user.name", "github-actions[bot]"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "config",
            "user.email",
            "41898282+github-actions[bot]@users.noreply.github.com",
        ],
        check=True,
    )

    subprocess.run(
        ["git", "add", str(STATE_PATH)],
        check=True,
    )

    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"]
    )

    if result.returncode != 0:
        subprocess.run(
            ["git", "commit", "-m", f"Checkpoint {label}"],
            check=True,
        )

        subprocess.run(
            ["git", "push", "origin", "HEAD:main"],
            check=True,
        )

        print(f"Checkpoint pushed: {label}", flush=True)
    else:
        print(f"No state changes to checkpoint: {label}", flush=True)


def run_stage(script, env):
    command = [sys.executable, script]

    print("", flush=True)
    print(f"=== Running {script} ===", flush=True)

    result = subprocess.run(
        command,
        env=env,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"{script} failed with exit code {result.returncode}"
        )


def track_id(track):
    return str(
        track.get("spotify", {}).get("id")
        or ""
    )


def main():
    state = load_state()
    all_tracks = state.get("tracks", [])

    releases = group_releases(all_tracks)

    print(
        f"Found {len(all_tracks)} tracks in "
        f"{len(releases)} release(s).",
        flush=True,
    )

    failed = False

    for index, release in enumerate(releases, start=1):
        label = release["label"]
        key = release["key"]
        ids = {
            track_id(track)
            for track in release["tracks"]
            if track_id(track)
        }

        current_state = load_state()
        current_tracks = [
            track
            for track in current_state.get("tracks", [])
            if track_id(track) in ids
        ]

        if not current_tracks:
            print(
                f"Skipping {label}: release tracks are no longer in state.",
                flush=True,
            )
            continue

        if all(
            track.get("acquisition", {}).get("status") == "published"
            for track in current_tracks
        ):
            print(
                f"[{index}/{len(releases)}] Already published: {label}",
                flush=True,
            )
            continue

        print("", flush=True)
        print(
            f"=== Release [{index}/{len(releases)}]: {label} ===",
            flush=True,
        )

        env = os.environ.copy()
        env["RELEASE_KEY"] = key

        try:
            run_stage("scripts/search.py", env)
            run_stage("scripts/llm_matcher.py", env)
            run_stage("scripts/download.py", env)

            current_state = load_state()
            current_tracks = [
                track
                for track in current_state.get("tracks", [])
                if track_id(track) in ids
            ]

            incomplete = [
                track
                for track in current_tracks
                if track.get("acquisition", {}).get("status")
                not in SUCCESS_STATUSES
            ]

            if incomplete:
                names = [
                    track.get("spotify", {}).get("title", "?")
                    for track in incomplete
                ]
                raise RuntimeError(
                    "Release is not fully downloaded: "
                    + ", ".join(names)
                )

            run_stage("scripts/publish.py", env)

            current_state = load_state()
            current_tracks = [
                track
                for track in current_state.get("tracks", [])
                if track_id(track) in ids
            ]

            unpublished = [
                track
                for track in current_tracks
                if track.get("acquisition", {}).get("status")
                != "published"
            ]

            if unpublished:
                names = [
                    track.get("spotify", {}).get("title", "?")
                    for track in unpublished
                ]
                raise RuntimeError(
                    "Release was not fully published: "
                    + ", ".join(names)
                )

            save_checkpoint(label)

            print(
                f"Release complete: {label}",
                flush=True,
            )

        except Exception as exc:
            failed = True
            print(
                f"RELEASE FAILED: {label}: {exc}",
                file=sys.stderr,
                flush=True,
            )

            try:
                save_checkpoint(label + " (failed)")
            except Exception as checkpoint_exc:
                print(
                    f"Checkpoint after failure also failed: {checkpoint_exc}",
                    file=sys.stderr,
                    flush=True,
                )

    if failed:
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
