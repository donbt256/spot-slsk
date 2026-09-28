import base64
import json
import os
import posixpath
import re
import sys
from pathlib import Path

from publish import GitHubClient, discover_library_repos


STATE_FILE = Path("state/tracks.json")


def load_state():
    with STATE_FILE.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def sanitize_filename(value):
    value = str(value or "").strip()
    value = re.sub(r"[\\/\x00]", "_", value)
    value = re.sub(r"[^A-Za-z0-9._ -]+", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value or "playlist"


def library_path(track):
    return (
        track.get("acquisition", {})
        .get("library", {})
        .get("path")
    )


def track_duration(track):
    value = track.get("spotify", {}).get("duration_ms")
    try:
        return float(value) / 1000.0
    except (TypeError, ValueError):
        return 0.0


def display_title(track):
    data = track.get("spotify", {})
    return (
        f"{data.get('artist') or 'Unknown Artist'} - "
        f"{data.get('title') or 'Unknown Track'}"
    )


def relative_library_path(
    playlist_repo,
    track_repo,
    track_path,
):
    if track_repo == playlist_repo:
        return track_path

    return posixpath.join(
        "..",
        track_repo,
        track_path,
    )


def existing_content(client, repo, path, branch):
    api_path = (
        f"/repos/{client.owner}/{repo}/contents/{path}"
    )

    response = client.session.get(
        f"https://api.github.com{api_path}",
        params={"ref": branch},
        timeout=60,
    )

    if response.status_code == 404:
        return None

    if response.status_code >= 400:
        try:
            detail = response.json()
        except Exception:
            detail = response.text
        raise RuntimeError(
            f"Unable to read {repo}/{path}: "
            f"{response.status_code}: {detail}"
        )

    data = response.json()
    encoded = data.get("content", "").replace("\n", "")
    return base64.b64decode(encoded).decode("utf-8")


def build_playlist_text(
    playlist,
    track_map,
    repo_for_track,
    playlist_repo,
):
    lines = ["#EXTM3U"]

    for track_id in playlist.get("tracks", []):
        track = track_map.get(track_id)
        if not track:
            continue

        path = library_path(track)
        track_repo = repo_for_track.get(track_id)

        if not path or not track_repo:
            # A playlist is only updated once every current entry
            # is available in the library.
            raise RuntimeError(
                f"Track {track_id} is not published yet."
            )

        relative = relative_library_path(
            playlist_repo,
            track_repo,
            path,
        )

        lines.append(
            f"#EXTINF:{track_duration(track):.3f},{display_title(track)}"
        )
        lines.append(relative)

    lines.append("")
    return "\n".join(lines)


def process():
    state = load_state()
    playlists = state.get("playlists", [])

    if not playlists:
        print("No configured Spotify playlists.")
        return 0

    token = os.environ.get("GIT_PAT")
    if not token:
        raise SystemExit("Missing GIT_PAT.")

    client = GitHubClient(token)
    repos = discover_library_repos(client)

    if not repos:
        print("No library repositories exist yet.")
        return 0

    repo_states = {
        repo: client.get_repo_state(repo)
        for repo in repos
    }

    # Keep all generated playlists in the first library repository.
    # Cross-repository tracks use ../music-library-NNN/... relative paths,
    # assuming the library repositories are cloned as siblings.
    playlist_repo = repos[0]
    playlist_branch = repo_states[playlist_repo]["branch"]

    track_map = {
        track.get("spotify", {}).get("id"): track
        for track in state.get("tracks", [])
        if track.get("spotify", {}).get("id")
    }

    repo_for_track = {}
    for repo, repo_state in repo_states.items():
        paths = {
            entry.get("path")
            for entry in repo_state.get("files", [])
        }
        for track_id, track in track_map.items():
            path = library_path(track)
            if path in paths:
                repo_for_track[track_id] = repo

    for playlist in playlists:
        playlist_id = playlist.get("id")
        if not playlist_id:
            continue

        track_ids = playlist.get("tracks", [])
        if any(
            track_id not in repo_for_track
            for track_id in track_ids
        ):
            missing = [
                track_id
                for track_id in track_ids
                if track_id not in repo_for_track
            ]
            print(
                f"Skipping playlist {playlist_id}: "
                f"{len(missing)} current track(s) are not published yet."
            )
            continue

        content = build_playlist_text(
            playlist,
            track_map,
            repo_for_track,
            playlist_repo,
        )
        path = f"Playlists/{playlist_id}.m3u8"

        old = existing_content(
            client,
            playlist_repo,
            path,
            playlist_branch,
        )

        if old == content:
            print(
                f"Playlist unchanged: "
                f"{playlist.get('name', playlist_id)}"
            )
            continue

        print(
            f"Updating playlist: "
            f"{playlist.get('name', playlist_id)} "
            f"({len(track_ids)} tracks)"
        )

        client.put_bytes(
            repo=playlist_repo,
            path=path,
            content=content.encode("utf-8"),
            branch=playlist_branch,
            message=(
                f"Update playlist: "
                f"{playlist.get('name', playlist_id)}"
            ),
        )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(process())
    except KeyboardInterrupt:
        raise SystemExit(130)
