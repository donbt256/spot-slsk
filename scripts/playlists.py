import hashlib
import json
import os
import posixpath
import re
import sys
from pathlib import Path

from publish import GitHubClient, discover_library_repos


STATE_FILE = Path("state/tracks.json")
PLAYLIST_DIR = "Playlists"


def load_state():
    with STATE_FILE.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def library_path(track):
    return (
        track.get("acquisition", {})
        .get("library", {})
        .get("path")
    )


def track_duration(track):
    value = track.get("spotify", {}).get("duration_ms")
    try:
        return max(0, int(round(float(value) / 1000.0)))
    except (TypeError, ValueError):
        return 0


def display_title(track):
    data = track.get("spotify", {})
    return (
        f"{data.get('artist') or 'Unknown Artist'} - "
        f"{data.get('title') or 'Unknown Track'}"
    )


def playlist_path(playlist_id):
    return f"{PLAYLIST_DIR}/{playlist_id}.m3u8"


def playlist_relative_path(track_path):
    # Playlist files live in Playlists/ inside music-library-001.
    # After the user combines all music-library-* repositories into one
    # local library folder, every Artist/Album/... path is rooted there.
    return posixpath.join("..", track_path)


def build_playlist_text(playlist, track_map):
    lines = ["#EXTM3U"]

    for track_id in playlist.get("tracks", []):
        track = track_map.get(track_id)
        if not track:
            raise RuntimeError(
                f"Playlist {playlist.get('id')} references unknown track {track_id}."
            )

        path = library_path(track)
        if not path:
            raise RuntimeError(
                f"Track {track_id} is not published yet."
            )

        lines.append(
            f"#EXTINF:{track_duration(track)},{display_title(track)}"
        )
        lines.append(playlist_relative_path(path))

    lines.append("")
    return "\n".join(lines)


def git_blob_sha(content):
    header = f"blob {len(content)}\0".encode("utf-8")
    return hashlib.sha1(header + content).hexdigest()


def process():
    state = load_state()
    playlists = state.get("playlists", [])

    token = os.environ.get("GIT_PAT")
    if not token:
        raise SystemExit("Missing GIT_PAT.")

    client = GitHubClient(token)
    repos = discover_library_repos(client)

    if not repos:
        print("No library repositories exist yet.")
        return 0

    # Keep generated playlist files in the first library repository. When
    # repositories are later combined into one local folder, Playlists/
    # sits alongside all Artist/Album directories.
    playlist_repo = repos[0]
    repo_state = client.get_repo_state(playlist_repo)
    branch = repo_state["branch"]

    track_map = {
        track.get("spotify", {}).get("id"): track
        for track in state.get("tracks", [])
        if track.get("spotify", {}).get("id")
    }

    # A playlist is only rewritten when every current entry has a published
    # library path. This prevents a transient acquisition failure from
    # destroying a previously valid playlist.
    desired = {}
    skipped = False
    skipped_paths = set()

    for playlist in playlists:
        playlist_id = playlist.get("id")
        if not playlist_id:
            continue

        track_ids = playlist.get("tracks", [])
        missing = [
            track_id
            for track_id in track_ids
            if not library_path(track_map.get(track_id, {}))
        ]

        if missing:
            skipped = True
            skipped_paths.add(playlist_path(playlist_id))
            print(
                f"Skipping playlist {playlist_id}: "
                f"{len(missing)} current track(s) are not published yet."
            )
            continue

        desired[playlist_path(playlist_id)] = (
            build_playlist_text(playlist, track_map).encode("utf-8")
        )

    existing = {
        entry.get("path"): entry
        for entry in repo_state.get("files", [])
        if str(entry.get("path", "")).startswith(f"{PLAYLIST_DIR}/")
        and str(entry.get("path", "")).endswith(".m3u8")
    }

    # Any generated playlist no longer present in urls.txt is removed.
    # Music itself is never removed.
    entries = []
    changes = []

    for path, content in desired.items():
        new_sha = git_blob_sha(content)
        old = existing.get(path)
        if old and old.get("sha") == new_sha:
            print(f"Playlist unchanged: {path}")
            continue

        blob_sha = client.create_blob(
            playlist_repo,
            content,
        )
        entries.append(
            {
                "path": path,
                "mode": "100644",
                "type": "blob",
                "sha": blob_sha,
            }
        )
        changes.append(path)

    desired_paths = set(desired)

    for path in existing:
        if path in desired_paths or path in skipped_paths:
            continue
        # A configured playlist that could not be rebuilt is preserved.
        # Only playlists removed from urls.txt are deleted.
        entries.append(
            {
                "path": path,
                "mode": "100644",
                "type": "blob",
                "sha": None,
            }
        )
        changes.append(path)
        print(f"Removing obsolete playlist: {path}")

    if entries:
        client.create_tree_commit(
            repo=playlist_repo,
            branch=branch,
            entries=entries,
            message="Update Spotify playlists",
        )
        print(
            f"Committed {len(changes)} playlist file change(s) "
            f"atomically."
        )
    elif not skipped:
        print("All Spotify playlists are up to date.")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(process())
    except KeyboardInterrupt:
        raise SystemExit(130)
