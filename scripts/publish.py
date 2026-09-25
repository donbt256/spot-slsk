import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


STATE_FILE = Path("state/tracks.json")

OWNER = os.environ.get("GITHUB_OWNER", "donbt256")
REPO_PREFIX = os.environ.get("MUSIC_REPO_PREFIX", "music-library-")

MAX_REPO_BYTES = 900 * 1024 * 1024
TARGET_REPO_BYTES = 890 * 1024 * 1024

WORK_ROOT = Path(
    os.environ.get(
        "MUSIC_LIBRARY_WORKDIR",
        "/home/runner/music-library-work",
    )
)


def run(command, cwd=None):
    print("$", " ".join(str(x) for x in command))

    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        text=True,
    )


def load_state():
    with STATE_FILE.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_state(state):
    temporary = STATE_FILE.with_suffix(".tmp")

    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    temporary.replace(STATE_FILE)


def safe_component(value):
    value = str(value or "").strip()

    value = value.replace("/", " - ")
    value = value.replace("\\", " - ")
    value = value.replace("\0", "")

    value = re.sub(r"[\x00-\x1f]", "", value)
    value = re.sub(r"\s+", " ", value)

    value = value.rstrip(". ")

    if not value:
        return "Unknown"

    return value


def repo_number(name):
    match = re.fullmatch(
        re.escape(REPO_PREFIX) + r"(\d+)",
        name,
        re.IGNORECASE,
    )

    if not match:
        return None

    return int(match.group(1))


def git_url(repo):
    token = os.environ["GIT_PAT"]

    return (
        f"https://x-access-token:{token}"
        f"@github.com/{OWNER}/{repo}.git"
    )


def discover_repositories():
    result = subprocess.run(
        [
            "git",
            "ls-remote",
            "--heads",
            f"https://github.com/{OWNER}/{REPO_PREFIX}001.git",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # We cannot enumerate repositories with git itself, so use the
    # GitHub REST API through curl.
    token = os.environ["GIT_PAT"]

    response = subprocess.run(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "-L",
            "-H",
            "Accept: application/vnd.github+json",
            "-H",
            f"Authorization: Bearer {token}",
            "-H",
            "X-GitHub-Api-Version: 2022-11-28",
            f"https://api.github.com/user/repos?per_page=100",
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    repositories = json.loads(response.stdout)

    numbers = []

    for repository in repositories:
        name = repository.get("name", "")
        number = repo_number(name)

        if number is not None:
            numbers.append(number)

    return sorted(numbers)


def repo_size(repo_dir):
    total = 0

    for path in repo_dir.rglob("*"):
        if not path.is_file():
            continue

        if ".git" in path.parts:
            continue

        total += path.stat().st_size

    return total


def clone_repo(repo):
    if WORK_ROOT.exists():
        shutil.rmtree(WORK_ROOT)

    WORK_ROOT.parent.mkdir(parents=True, exist_ok=True)

    run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            git_url(repo),
            str(WORK_ROOT),
        ]
    )

    return WORK_ROOT


def ensure_git_identity(repo_dir):
    run(
        ["git", "config", "user.name", "github-actions"],
        cwd=repo_dir,
    )

    run(
        [
            "git",
            "config",
            "user.email",
            "41898282+github-actions[bot]@users.noreply.github.com",
        ],
        cwd=repo_dir,
    )


def select_repo():
    numbers = discover_repositories()

    if not numbers:
        raise RuntimeError(
            "No music-library repositories were found."
        )

    for number in reversed(numbers):
        repo = f"{REPO_PREFIX}{number:03d}"

        print(f"Checking {repo}...")

        repo_dir = clone_repo(repo)
        size = repo_size(repo_dir)

        print(
            f"{repo}: "
            f"{size / (1024 * 1024):.2f} MiB"
        )

        if size < TARGET_REPO_BYTES:
            return repo, repo_dir

    next_number = max(numbers) + 1
    repo = f"{REPO_PREFIX}{next_number:03d}"

    raise RuntimeError(
        f"All existing repositories are at or above "
        f"{TARGET_REPO_BYTES} bytes. "
        f"Create {OWNER}/{repo} before publishing."
    )


def get_downloaded_tracks(state):
    result = []

    for track in state.get("tracks", []):
        acquisition = track.get("acquisition", {})

        if acquisition.get("status") != "downloaded":
            continue

        file_info = acquisition.get("file")

        if not file_info:
            continue

        path = Path(file_info.get("path", ""))

        if not path.is_file():
            print(
                f"Skipping missing file: {path}"
            )
            continue

        result.append((track, path))

    return result


def destination_for(track, source):
    spotify = track["spotify"]

    artist = safe_component(
        spotify.get("album_artist")
        or spotify.get("artist")
        or "Unknown Artist"
    )

    album = safe_component(
        spotify.get("album")
        or "Unknown Album"
    )

    title = safe_component(
        spotify.get("title")
        or source.stem
    )

    extension = source.suffix

    if not extension:
        extension = ".bin"

    filename = f"{title}{extension}"

    return WORK_ROOT / artist / album / filename


def copy_track(track, source):
    destination = destination_for(track, source)

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    shutil.copy2(source, destination)

    return destination


def push(repo_dir, repo):
    run(
        ["git", "add", "--all"],
        cwd=repo_dir,
    )

    status = subprocess.run(
        [
            "git",
            "status",
            "--porcelain",
        ],
        cwd=repo_dir,
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    )

    if not status.stdout.strip():
        print("Nothing new to commit.")
        return False

    run(
        [
            "git",
            "commit",
            "-m",
            "Add downloaded music",
        ],
        cwd=repo_dir,
    )

    run(
        [
            "git",
            "push",
            "origin",
            "HEAD:main",
        ],
        cwd=repo_dir,
    )

    print(f"Pushed {repo}.")

    return True


def main():
    if not os.environ.get("GIT_PAT"):
        raise RuntimeError(
            "GIT_PAT is not set."
        )

    state = load_state()

    tracks = get_downloaded_tracks(state)

    if not tracks:
        print("No downloaded tracks are ready for publishing.")
        return 0

    repo, repo_dir = select_repo()

    ensure_git_identity(repo_dir)

    published = 0

    for track, source in tracks:
        destination = copy_track(track, source)

        relative = destination.relative_to(repo_dir)

        track["acquisition"]["status"] = "published"
        track["acquisition"]["library"] = {
            "repository": repo,
            "path": str(relative),
        }

        published += 1

        print(
            f"Published candidate: "
            f"{source.name} -> {relative}"
        )

    save_state(state)

    if not push(repo_dir, repo):
        print("No library changes were pushed.")
        return 0

    print(
        f"Published {published} track(s) to {repo}."
    )

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)