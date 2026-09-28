import base64
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests


STATE_FILE = Path("state/tracks.json")

GITHUB_API = "https://api.github.com"

# Keep normal repositories below 1 GiB.
REPO_TARGET_BYTES = 900 * 1024 * 1024

# Albums at or below this size are atomic.
ALBUM_ATOMIC_LIMIT_BYTES = 1 * 1024 * 1024 * 1024

LIBRARY_PREFIX = os.environ.get(
    "GITHUB_LIBRARY_PREFIX",
    "music-library-",
)

LIBRARY_START_NUMBER = int(
    os.environ.get(
        "GITHUB_LIBRARY_START",
        "1",
    )
)

REQUEST_TIMEOUT = 60


def log(message=""):
    print(message, flush=True)


def load_state():
    if not STATE_FILE.exists():
        raise SystemExit(
            f"Missing {STATE_FILE}"
        )

    with STATE_FILE.open(
        "r",
        encoding="utf-8",
    ) as handle:
        state = json.load(handle)

    log(
        f"Loaded {len(state.get('tracks', []))} tracks."
    )

    return state


def save_state(state):
    STATE_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = STATE_FILE.with_suffix(
        ".tmp"
    )

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

    temporary.replace(STATE_FILE)


def format_bytes(value):
    value = float(value or 0)

    units = [
        "B",
        "KiB",
        "MiB",
        "GiB",
    ]

    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"

        value /= 1024

    return f"{value:.1f} GiB"


def normalize_path(value):
    value = str(value or "")

    value = value.replace(
        "\\",
        "/",
    )

    value = re.sub(
        r"/+",
        "/",
        value,
    )

    value = value.strip("/")

    return value


def sanitize_component(value):
    value = str(value or "").strip()

    value = value.replace(
        "/",
        "_",
    )

    value = value.replace(
        "\\",
        "_",
    )

    value = value.replace(
        "\x00",
        "",
    )

    if value in {"", ".", ".."}:
        return "_"

    return value


def spotify(track):
    return track.get(
        "spotify",
        track,
    )


def track_id(track):
    data = spotify(track)

    return data.get("id") or (
        f"{data.get('artist', '')}\x1f"
        f"{data.get('album', '')}\x1f"
        f"{data.get('title', '')}"
    )


def album_key(track):
    data = spotify(track)

    artist = str(
        data.get("album_artist")
        or data.get("artist")
        or ""
    ).strip()

    album = str(
        data.get("album")
        or ""
    ).strip()

    if not artist or not album:
        return None

    return (
        artist.casefold(),
        album.casefold(),
    )


def is_downloaded(track):
    acquisition = track.get(
        "acquisition",
        {},
    )

    status = acquisition.get(
        "status"
    )

    return status in {
        "downloaded",
        "ready_to_publish",
    }


def already_published(track):
    acquisition = track.get(
        "acquisition",
        {}
    )

    return acquisition.get(
        "status"
    ) == "published"


def get_local_file(track):
    acquisition = track.get(
        "acquisition",
        {},
    )

    file_info = acquisition.get(
        "file"
    )

    if not isinstance(
        file_info,
        dict,
    ):
        return None

    path = (
        file_info.get("path")
        or file_info.get("local_path")
        or file_info.get("localPath")
    )

    if not path:
        return None

    path = Path(path)

    if not path.is_file():
        return None

    return path


def get_download_size(track):
    path = get_local_file(track)

    if path is None:
        return 0

    return path.stat().st_size


def album_groups(tracks):
    groups = {}

    for index, track in enumerate(tracks):
        if already_published(track):
            continue

        if not is_downloaded(track):
            continue

        key = album_key(track)

        if key is None:
            continue

        groups.setdefault(
            key,
            {
                "first_index": index,
                "tracks": [],
            },
        )

        groups[key]["tracks"].append(track)

    ordered = list(
        groups.values()
    )

    ordered.sort(
        key=lambda item: item[
            "first_index"
        ]
    )

    return ordered


def build_items(tracks):
    """
    Build the publishing queue in original track order.

    Albums are represented as one item when all of their
    currently publishable tracks belong to the same album.

    A one-track album remains an individual item.
    """

    groups = album_groups(
        tracks
    )

    track_to_group = {}

    for group in groups:
        for track in group["tracks"]:
            track_to_group[
                id(track)
            ] = group

    items = []
    consumed = set()

    for index, track in enumerate(
        tracks
    ):
        if id(track) in consumed:
            continue

        if already_published(track):
            continue

        if not is_downloaded(track):
            continue

        group = track_to_group.get(
            id(track)
        )

        if (
            group is not None
            and len(group["tracks"]) >= 2
        ):
            group_tracks = group[
                "tracks"
            ]

            for member in group_tracks:
                consumed.add(
                    id(member)
                )

            items.append(
                {
                    "type": "album",
                    "first_index": group[
                        "first_index"
                    ],
                    "tracks": group_tracks,
                }
            )

        else:
            consumed.add(
                id(track)
            )

            items.append(
                {
                    "type": "track",
                    "first_index": index,
                    "tracks": [track],
                }
            )

    items.sort(
        key=lambda item: item[
            "first_index"
        ]
    )

    return items


def item_size(item):
    return sum(
        get_download_size(track)
        for track in item["tracks"]
    )


def item_album_name(item):
    track = item["tracks"][0]
    data = spotify(track)

    return str(
        data.get("album")
        or "Unknown Album"
    )


def item_artist_name(item):
    track = item["tracks"][0]
    data = spotify(track)

    return str(
        data.get("album_artist")
        or data.get("artist")
        or "Unknown Artist"
    )


def is_atomic_album(item):
    if item["type"] != "album":
        return False

    return (
        item_size(item)
        <= ALBUM_ATOMIC_LIMIT_BYTES
    )


class GitHubClient:
    def __init__(self, token):
        if not token:
            raise SystemExit(
                "GIT_PAT is required."
            )

        self.session = requests.Session()

        self.session.headers.update(
            {
                "Authorization": (
                    f"Bearer {token}"
                ),
                "Accept": (
                    "application/vnd.github+json"
                ),
                "X-GitHub-Api-Version": (
                    "2022-11-28"
                ),
                "User-Agent": (
                    "spot-slsk-publisher"
                ),
            }
        )

        self.owner = self._get_owner()

    def request(
        self,
        method,
        path,
        **kwargs,
    ):
        url = (
            f"{GITHUB_API}"
            f"{path}"
        )

        response = self.session.request(
            method,
            url,
            timeout=REQUEST_TIMEOUT,
            **kwargs,
        )

        if response.status_code >= 400:
            try:
                detail = response.json()
            except Exception:
                detail = response.text

            raise RuntimeError(
                f"GitHub API {method} {path} "
                f"failed with "
                f"{response.status_code}: "
                f"{detail}"
            )

        if not response.content:
            return None

        return response.json()

    def _get_owner(self):
        configured = os.environ.get(
            "GITHUB_LIBRARY_OWNER"
        )

        if configured:
            return configured

        source_repo = os.environ.get(
            "GITHUB_REPO"
        )

        if source_repo and "/" in source_repo:
            return source_repo.split(
                "/",
                1,
            )[0]

        data = self.request(
            "GET",
            "/user",
        )

        login = data.get(
            "login"
        )

        if not login:
            raise RuntimeError(
                "Could not determine GitHub "
                "account owner."
            )

        return login

    def repo_exists(self, repo):
        path = (
            f"/repos/"
            f"{quote(self.owner, safe='')}/"
            f"{quote(repo, safe='')}"
        )

        response = self.session.get(
            f"{GITHUB_API}{path}",
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code == 404:
            return False

        if response.status_code >= 400:
            try:
                detail = response.json()
            except Exception:
                detail = response.text

            raise RuntimeError(
                f"Unable to check repository "
                f"{repo}: "
                f"{response.status_code}: "
                f"{detail}"
            )

        return True

    def create_repo(self, repo):
        log(
            f"Creating library repository "
            f"{self.owner}/{repo}"
        )

        data = self.request(
            "POST",
            "/user/repos",
            json={
                "name": repo,
                "description": (
                    "Music library generated "
                    "by spot-slsk"
                ),
                "private": True,
                "has_issues": False,
                "has_projects": False,
                "has_wiki": False,
                "has_discussions": False,
                "auto_init": True,
            },
        )

        return data

    def ensure_repo(self, repo):
        if self.repo_exists(repo):
            return

        self.create_repo(repo)

        # GitHub may need a short moment before
        # the repository is immediately accessible.
        for _ in range(10):
            if self.repo_exists(repo):
                return

            time.sleep(1)

        raise RuntimeError(
            f"Repository {repo} was created "
            f"but did not become available."
        )

    def list_tree(self, repo):
        repo_path = (
            f"/repos/"
            f"{quote(self.owner, safe='')}/"
            f"{quote(repo, safe='')}"
        )

        repository = self.request(
            "GET",
            repo_path,
        )

        default_branch = (
            repository.get(
                "default_branch"
            )
            or "main"
        )

        branch_path = (
            f"{repo_path}/git/refs/heads/"
            f"{quote(default_branch, safe='')}"
        )

        ref = self.request(
            "GET",
            branch_path,
        )

        commit_sha = (
            ref.get("object", {})
            .get("sha")
        )

        if not commit_sha:
            return {
                "branch": default_branch,
                "files": [],
                "bytes": 0,
            }

        commit = self.request(
            "GET",
            f"{repo_path}/git/commits/"
            f"{commit_sha}",
        )

        tree_sha = (
            commit.get("tree", {})
            .get("sha")
        )

        if not tree_sha:
            return {
                "branch": default_branch,
                "files": [],
                "bytes": 0,
            }

        tree = self.request(
            "GET",
            f"{repo_path}/git/trees/"
            f"{tree_sha}",
            params={
                "recursive": "1"
            },
        )

        files = []

        total_bytes = 0

        for entry in tree.get(
            "tree",
            [],
        ):
            if entry.get("type") != "blob":
                continue

            size = int(
                entry.get("size")
                or 0
            )

            files.append(
                {
                    "path": entry.get(
                        "path"
                    ),
                    "size": size,
                }
            )

            total_bytes += size

        return {
            "branch": default_branch,
            "files": files,
            "bytes": total_bytes,
        }

    def get_repo_state(self, repo):
        self.ensure_repo(repo)

        tree = self.list_tree(
            repo
        )

        return {
            "name": repo,
            "branch": tree[
                "branch"
            ],
            "bytes": tree[
                "bytes"
            ],
            "files": tree[
                "files"
            ],
        }

    def put_file(
        self,
        repo,
        path,
        local_file,
        branch,
        message,
    ):
        local_file = Path(
            local_file
        )

        encoded = base64.b64encode(
            local_file.read_bytes()
        ).decode("ascii")

        api_path = (
            f"/repos/"
            f"{quote(self.owner, safe='')}/"
            f"{quote(repo, safe='')}/"
            f"contents/"
            f"{quote(normalize_path(path), safe='/')}"
        )

        existing_sha = None

        response = self.session.get(
            f"{GITHUB_API}{api_path}",
            params={
                "ref": branch
            },
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code == 200:
            try:
                existing_sha = response.json().get(
                    "sha"
                )
            except Exception:
                existing_sha = None

        elif response.status_code != 404:
            try:
                detail = response.json()
            except Exception:
                detail = response.text

            raise RuntimeError(
                f"Unable to inspect "
                f"{repo}/{path}: "
                f"{response.status_code}: "
                f"{detail}"
            )

        payload = {
            "message": message,
            "content": encoded,
            "branch": branch,
        }

        if existing_sha:
            payload["sha"] = existing_sha

        self.request(
            "PUT",
            api_path,
            json=payload,
        )


def library_repo_number(repo):
    suffix = repo[
        len(LIBRARY_PREFIX):
    ]

    try:
        return int(suffix)
    except ValueError:
        return None


def discover_library_repos(client):
    repos = []

    number = LIBRARY_START_NUMBER

    while True:
        repo = (
            f"{LIBRARY_PREFIX}"
            f"{number:03d}"
        )

        if not client.repo_exists(repo):
            break

        repos.append(repo)
        number += 1

    return repos


def ensure_initial_repo(
    client,
    repos,
):
    if repos:
        return repos

    repo = (
        f"{LIBRARY_PREFIX}"
        f"{LIBRARY_START_NUMBER:03d}"
    )

    client.ensure_repo(
        repo
    )

    repos.append(repo)

    return repos


def refresh_repo_state(
    client,
    repo_states,
    repo,
):
    state = client.get_repo_state(
        repo
    )

    repo_states[repo] = state

    return state


def available_bytes(
    repo_state,
):
    return max(
        0,
        REPO_TARGET_BYTES
        - repo_state["bytes"],
    )


def next_repo_name(
    existing_repos,
):
    highest = (
        LIBRARY_START_NUMBER - 1
    )

    for repo in existing_repos:
        number = library_repo_number(
            repo
        )

        if number is not None:
            highest = max(
                highest,
                number,
            )

    return (
        f"{LIBRARY_PREFIX}"
        f"{highest + 1:03d}"
    )


def ensure_next_repo(
    client,
    repos,
    repo_states,
):
    repo = next_repo_name(
        repos
    )

    client.ensure_repo(
        repo
    )

    state = client.get_repo_state(
        repo
    )

    repos.append(repo)
    repo_states[repo] = state

    return repo


def relative_library_path(track):
    data = spotify(track)

    artist = sanitize_component(
        data.get("album_artist")
        or data.get("artist")
        or "Unknown Artist"
    )

    album = sanitize_component(
        data.get("album")
        or "Unknown Album"
    )

    title = sanitize_component(
        data.get("title")
        or "Unknown Track"
    )

    path = get_local_file(
        track
    )

    if path is None:
        raise RuntimeError(
            "Track has no local file."
        )

    extension = path.suffix

    if not extension:
        extension = ""

    track_number = data.get(
        "track_number"
    )

    if track_number is not None:
        try:
            track_number = int(
                track_number
            )

            filename = (
                f"{track_number:02d} - "
                f"{title}"
                f"{extension}"
            )

        except (
            TypeError,
            ValueError,
        ):
            filename = (
                f"{title}"
                f"{extension}"
            )
    else:
        filename = (
            f"{title}"
            f"{extension}"
        )

    return normalize_path(
        f"{artist}/"
        f"{album}/"
        f"{filename}"
    )


def mark_published(
    track,
    repo,
    path,
):
    acquisition = track.setdefault(
        "acquisition",
        {},
    )

    acquisition[
        "status"
    ] = "published"

    acquisition[
        "library"
    ] = {
        "repo": repo,
        "path": path,
    }


def set_pending_publish(
    track,
):
    acquisition = track.setdefault(
        "acquisition",
        {},
    )

    acquisition[
        "status"
    ] = "ready_to_publish"


def publish_item(
    client,
    item,
    repo,
    repo_state,
):
    tracks = item["tracks"]

    total_size = item_size(
        item
    )

    log(
        f"Publishing "
        f"{item['type']}: "
        f"{item_artist_name(item)} - "
        f"{item_album_name(item)}"
    )

    log(
        f"  Size: "
        f"{format_bytes(total_size)}"
    )

    log(
        f"  Repository: "
        f"{repo}"
    )

    uploaded = 0

    for track in tracks:
        local_file = get_local_file(
            track
        )

        if local_file is None:
            raise RuntimeError(
                "Downloaded file is missing "
                f"for {spotify(track).get('title')}"
            )

        path = relative_library_path(
            track
        )

        size = local_file.stat().st_size

        log(
            f"  Uploading: "
            f"{path} "
            f"({format_bytes(size)})"
        )

        client.put_file(
            repo=repo,
            path=path,
            local_file=local_file,
            branch=repo_state[
                "branch"
            ],
            message=(
                f"Add "
                f"{spotify(track).get('artist', 'track')} - "
                f"{spotify(track).get('title', 'track')}"
            ),
        )

        repo_state[
            "bytes"
        ] += size

        repo_state.setdefault(
            "files",
            [],
        ).append(
            {
                "path": path,
                "size": size,
            }
        )

        mark_published(
            track,
            repo,
            path,
        )

        uploaded += 1

    log(
        f"  Published {uploaded} "
        f"file(s)."
    )

    log(
        f"  Repository usage: "
        f"{format_bytes(repo_state['bytes'])} / "
        f"{format_bytes(REPO_TARGET_BYTES)}"
    )


def item_fits_atomically(
    item,
    repo_state,
):
    size = item_size(
        item
    )

    return (
        size
        <= available_bytes(
            repo_state
        )
    )


def split_large_album(
    item,
    client,
    repos,
    repo_states,
):
    """
    Albums over 1 GiB are the sole exception to
    album atomicity.

    Tracks are placed individually using the same
    first-fitting-repository strategy.
    """

    log(
        f"Large album exception: "
        f"{item_artist_name(item)} - "
        f"{item_album_name(item)} "
        f"({format_bytes(item_size(item))})"
    )

    for track in item["tracks"]:
        track_size = get_download_size(
            track
        )

        placed = False

        for repo in list(repos):
            repo_state = repo_states[
                repo
            ]

            if (
                track_size
                <= available_bytes(
                    repo_state
                )
            ):
                single_item = {
                    "type": "track",
                    "first_index": 0,
                    "tracks": [track],
                }

                publish_item(
                    client,
                    single_item,
                    repo,
                    repo_state,
                )

                placed = True
                break

        if not placed:
            repo = ensure_next_repo(
                client,
                repos,
                repo_states,
            )

            repo_state = repo_states[
                repo
            ]

            if track_size > REPO_TARGET_BYTES:
                raise RuntimeError(
                    "Individual track exceeds "
                    f"repository target: "
                    f"{spotify(track).get('title')} "
                    f"({format_bytes(track_size)})"
                )

            single_item = {
                "type": "track",
                "first_index": 0,
                "tracks": [track],
            }

            publish_item(
                client,
                single_item,
                repo,
                repo_state,
            )


def publish():
    token = os.environ.get(
        "GIT_PAT"
    )

    if not token:
        raise SystemExit(
            "Missing GIT_PAT."
        )

    state = load_state()

    tracks = state.get(
        "tracks",
        [],
    )

    items = build_items(
        tracks
    )

    if not items:
        log(
            "No downloaded tracks are "
            "ready for publishing."
        )
        return 0

    client = GitHubClient(
        token
    )

    log(
        f"GitHub library owner: "
        f"{client.owner}"
    )

    repos = discover_library_repos(
        client
    )

    repos = ensure_initial_repo(
        client,
        repos,
    )

    repo_states = {}

    log(
        "Scanning library repositories..."
    )

    for repo in repos:
        repo_states[repo] = (
            client.get_repo_state(
                repo
            )
        )

        log(
            f"  {repo}: "
            f"{format_bytes(repo_states[repo]['bytes'])} used, "
            f"{format_bytes(available_bytes(repo_states[repo]))} available"
        )

    log("")
    log(
        f"Publishing queue: "
        f"{len(items)} item(s)"
    )
    log(
        f"Normal repository target: "
        f"{format_bytes(REPO_TARGET_BYTES)}"
    )
    log(
        f"Atomic album limit: "
        f"{format_bytes(ALBUM_ATOMIC_LIMIT_BYTES)}"
    )
    log("")

    pending_atomic = []

    # First pass:
    #
    # Walk the original acquisition order.
    # For each item, check every existing repository.
    #
    # If an atomic album doesn't fit anywhere, defer it
    # rather than immediately creating a new repository.
    for item in items:
        size = item_size(
            item
        )

        if size <= 0:
            log(
                f"Skipping item with no local "
                f"file data: "
                f"{item_artist_name(item)} - "
                f"{item_album_name(item)}"
            )
            continue

        if (
            item["type"] == "album"
            and size
            > ALBUM_ATOMIC_LIMIT_BYTES
        ):
            split_large_album(
                item,
                client,
                repos,
                repo_states,
            )

            save_state(
                state
            )

            continue

        placed = False

        for repo in list(repos):
            repo_state = repo_states[
                repo
            ]

            if item_fits_atomically(
                item,
                repo_state,
            ):
                publish_item(
                    client,
                    item,
                    repo,
                    repo_state,
                )

                placed = True
                break

        if not placed:
            pending_atomic.append(
                item
            )

            log(
                f"Deferring item because "
                f"no current repository can "
                f"fit it: "
                f"{item_artist_name(item)} - "
                f"{item_album_name(item)} "
                f"({format_bytes(size)})"
            )

        save_state(
            state
        )

    # Second pass:
    #
    # Items that could not fit anywhere during
    # the first pass are now given new repositories.
    #
    # We still check every existing repository first,
    # because earlier placements may have changed
    # available capacity.
    for item in pending_atomic:
        size = item_size(
            item
        )

        placed = False

        for repo in list(repos):
            repo_state = repo_states[
                repo
            ]

            if item_fits_atomically(
                item,
                repo_state,
            ):
                publish_item(
                    client,
                    item,
                    repo,
                    repo_state,
                )

                placed = True
                break

        if placed:
            save_state(
                state
            )
            continue

        if size > REPO_TARGET_BYTES:
            # This should only be reachable for a
            # one-track item or an album that is
            # >900 MiB but <=1 GiB.
            #
            # Such an album is still atomic, so a
            # dedicated repository is allowed.
            log(
                f"Item exceeds the normal "
                f"repository target but is still "
                f"within the atomic album limit: "
                f"{format_bytes(size)}"
            )

        repo = ensure_next_repo(
            client,
            repos,
            repo_states,
        )

        repo_state = repo_states[
            repo
        ]

        if size > REPO_TARGET_BYTES:
            if (
                item["type"] == "album"
                and size
                <= ALBUM_ATOMIC_LIMIT_BYTES
            ):
                # The whole album goes into this
                # repository despite exceeding the
                # normal 900 MiB target.
                publish_item(
                    client,
                    item,
                    repo,
                    repo_state,
                )
            else:
                raise RuntimeError(
                    "Atomic item cannot fit "
                    "within repository policy: "
                    f"{item_artist_name(item)} - "
                    f"{item_album_name(item)} "
                    f"({format_bytes(size)})"
                )
        else:
            publish_item(
                client,
                item,
                repo,
                repo_state,
            )

        save_state(
            state
        )

    log("")
    log(
        "Publishing complete."
    )

    log("")
    log(
        "Final library usage:"
    )

    for repo in repos:
        repo_state = repo_states[
            repo
        ]

        log(
            f"  {repo}: "
            f"{format_bytes(repo_state['bytes'])} / "
            f"{format_bytes(REPO_TARGET_BYTES)}"
        )

    save_state(
        state
    )

    return 0


def main():
    try:
        return publish()

    except KeyboardInterrupt:
        log(
            "Publishing interrupted."
        )
        return 130

    except Exception as exc:
        log(
            f"ERROR: {exc}"
        )
        return 1


if __name__ == "__main__":
    sys.exit(
        main()
    )