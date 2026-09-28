import json
import os
import sys
import time
from pathlib import Path

from soulseek import SoulseekClient


STATE_FILE = Path("state/tracks.json")
DOWNLOAD_ROOT = Path(
    os.environ.get(
        "SOULSEEK_DOWNLOAD_DIR",
        "/home/runner/music-downloads",
    )
)

POLL_SECONDS = 5
TIMEOUT_SECONDS = 60 * 60


def log(message=""):
    print(message, flush=True)


def load_state():
    log("Loading acquisition state...")
    with STATE_FILE.open("r", encoding="utf-8") as handle:
        state = json.load(handle)

    log(
        f"Loaded {len(state.get('tracks', []))} tracks."
    )

    return state


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")

    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(
            state,
            handle,
            indent=2,
            ensure_ascii=False,
        )
        handle.write("\n")

    temporary.replace(STATE_FILE)


def selected_matches(state):
    matches = []

    for track in state.get("tracks", []):
        acquisition = track.get("acquisition", {})

        if acquisition.get("status") != "matched":
            continue

        match = acquisition.get("match")

        if not isinstance(match, dict):
            continue

        candidates = match.get("candidates")

        if not isinstance(candidates, list) or not candidates:
            continue

        selected = candidates[0]

        if not isinstance(selected, dict):
            continue

        username = selected.get("username")
        filename = selected.get("filename")

        if not username or not filename:
            continue

        matches.append((track, selected))

    return matches


def transfer_key(username, filename):
    return (
        str(username or "").lower(),
        str(filename or "").lower(),
    )


def extract_transfers(data):
    transfers = []

    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return transfers

    for username, entries in data.items():
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict):
                    item = dict(entry)
                    item.setdefault("username", username)
                    transfers.append(item)

        elif isinstance(entries, dict):
            for entry in entries.values():
                if isinstance(entry, dict):
                    item = dict(entry)
                    item.setdefault("username", username)
                    transfers.append(item)

    return transfers


def transfer_filename(transfer):
    return (
        transfer.get("filename")
        or transfer.get("fileName")
        or transfer.get("remoteFilename")
    )


def transfer_username(transfer):
    return (
        transfer.get("username")
        or transfer.get("userName")
    )


def transfer_state(transfer):
    state = transfer.get("state")

    if isinstance(state, dict):
        return str(
            state.get("description")
            or state.get("state")
            or state.get("name")
            or ""
        )

    return str(state or "")


def transfer_progress(transfer):
    total = (
        transfer.get("size")
        or transfer.get("fileSize")
        or transfer.get("totalBytes")
        or transfer.get("totalSize")
    )

    downloaded = (
        transfer.get("bytesTransferred")
        or transfer.get("bytesDownloaded")
        or transfer.get("transferred")
        or transfer.get("downloaded")
        or 0
    )

    speed = (
        transfer.get("averageSpeed")
        or transfer.get("speed")
        or transfer.get("downloadSpeed")
        or 0
    )

    try:
        total = int(total or 0)
    except (TypeError, ValueError):
        total = 0

    try:
        downloaded = int(downloaded or 0)
    except (TypeError, ValueError):
        downloaded = 0

    try:
        speed = float(speed or 0)
    except (TypeError, ValueError):
        speed = 0

    return total, downloaded, speed


def format_bytes(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "?"

    units = (
        "B",
        "KiB",
        "MiB",
        "GiB",
        "TiB",
    )

    for unit in units:
        if abs(value) < 1024:
            return f"{value:.1f} {unit}"

        value /= 1024

    return f"{value:.1f} PiB"


def format_speed(value):
    if not value:
        return "?"

    return f"{format_bytes(value)}/s"


def find_downloaded_file(username, remote_filename):
    remote_name = Path(
        str(remote_filename).replace("\\", "/")
    ).name

    exact_matches = []

    if not DOWNLOAD_ROOT.exists():
        return None

    for path in DOWNLOAD_ROOT.rglob("*"):
        if not path.is_file():
            continue

        if path.name != remote_name:
            continue

        exact_matches.append(path)

    if not exact_matches:
        return None

    if len(exact_matches) == 1:
        return exact_matches[0]

    username_text = str(username).lower()

    for path in exact_matches:
        if username_text in str(path).lower():
            return path

    return exact_matches[0]


def main():
    log("Starting download stage.")

    state = load_state()

    log("Connecting to slskd...")

    client = SoulseekClient(
        base_url=os.environ.get(
            "SLSKD_URL",
            "http://127.0.0.1:5030",
        ),
        api_key=os.environ.get("SLSKD_API_KEY"),
    )

    log("Selecting matched tracks...")

    matches = selected_matches(state)

    log(
        f"Found {len(matches)} matched track(s) "
        "ready for download."
    )

    if not matches:
        log("No matched tracks need downloading.")
        return 0

    log("")
    log(f"Queueing {len(matches)} downloads...")
    log("")

    queued = []

    for index, (track, match) in enumerate(
        matches,
        start=1,
    ):
        username = match["username"]
        filename = match["filename"]
        size = match.get("size")

        title = track["spotify"]["title"]

        log(
            f"[{index}/{len(matches)}] "
            f"Queueing: {title}"
        )
        log(f"  User: {username}")
        log(f"  File: {filename}")

        if size is not None:
            log(
                f"  Size: {format_bytes(size)}"
            )

        try:
            client.enqueue_download(
                username=username,
                filename=filename,
                size=size,
            )

            track["acquisition"]["status"] = (
                "downloading"
            )

            queued.append((track, match))

            log("  Queued.")

        except Exception as exc:
            log(f"  FAILED: {exc}")

            track["acquisition"]["status"] = (
                "download_failed"
            )

            track["acquisition"][
                "download_error"
            ] = str(exc)

    save_state(state)

    if not queued:
        log("No downloads were successfully queued.")
        return 1

    log("")
    log(
        f"Queued {len(queued)} downloads."
    )
    log("Waiting for Soulseek transfers...")
    log("")

    deadline = (
        time.monotonic()
        + TIMEOUT_SECONDS
    )

    wanted = {
        transfer_key(
            match["username"],
            match["filename"],
        ): (track, match)
        for track, match in queued
    }

    completed = set()
    failed = set()
    last_progress = {}

    while time.monotonic() < deadline:
        try:
            data = client.get_downloads()

        except Exception as exc:
            log(
                f"Unable to read download status: "
                f"{exc}"
            )
            time.sleep(POLL_SECONDS)
            continue

        transfers = extract_transfers(data)

        active_keys = set()

        for transfer in transfers:
            username = transfer_username(transfer)
            filename = transfer_filename(transfer)

            if not username or not filename:
                continue

            key = transfer_key(
                username,
                filename,
            )

            if key not in wanted:
                continue

            if key in completed or key in failed:
                continue

            active_keys.add(key)

            current_state = transfer_state(
                transfer
            ).lower()

            total, downloaded, speed = (
                transfer_progress(transfer)
            )

            progress_key = (
                current_state,
                total,
                downloaded,
                int(speed),
            )

            if (
                last_progress.get(key)
                != progress_key
            ):
                if total:
                    percent = (
                        downloaded / total * 100
                        if total
                        else 0
                    )

                    log(
                        f"Transfer: {filename}"
                    )
                    log(
                        f"  State: "
                        f"{current_state or 'unknown'}"
                    )
                    log(
                        f"  Progress: "
                        f"{format_bytes(downloaded)} / "
                        f"{format_bytes(total)} "
                        f"({percent:.1f}%)"
                    )
                    log(
                        f"  Speed: "
                        f"{format_speed(speed)}"
                    )

                else:
                    log(
                        f"Transfer: {filename}"
                    )
                    log(
                        f"  State: "
                        f"{current_state or 'unknown'}"
                    )
                    log(
                        f"  Progress: "
                        f"{format_bytes(downloaded)}"
                    )
                    log(
                        f"  Speed: "
                        f"{format_speed(speed)}"
                    )

                last_progress[key] = progress_key

            track, match = wanted[key]

            if "succeeded" in current_state:
                path = find_downloaded_file(
                    username,
                    filename,
                )

                if path is None:
                    log(
                        "  Transfer succeeded, "
                        "but the downloaded file "
                        "was not found yet."
                    )
                    continue

                track["acquisition"]["status"] = (
                    "downloaded"
                )

                track["acquisition"]["file"] = {
                    "path": str(path),
                    "filename": path.name,
                    "size": path.stat().st_size,
                }

                completed.add(key)
                save_state(state)

                log(
                    f"  Downloaded: {path}"
                )

            elif any(
                failure in current_state
                for failure in (
                    "rejected",
                    "timedout",
                    "errored",
                    "failed",
                )
            ):
                track["acquisition"]["status"] = (
                    "download_failed"
                )

                track["acquisition"][
                    "download_error"
                ] = current_state

                failed.add(key)
                save_state(state)

                log(
                    f"  Download failed: "
                    f"{current_state}"
                )

        finished = (
            len(completed)
            + len(failed)
        )

        log(
            f"Overall progress: "
            f"{finished}/{len(wanted)} finished "
            f"({len(completed)} downloaded, "
            f"{len(failed)} failed)"
        )

        if finished == len(wanted):
            break

        time.sleep(POLL_SECONDS)

    unresolved = (
        len(wanted)
        - len(completed)
        - len(failed)
    )

    if unresolved:
        log("")
        log(
            f"{unresolved} download(s) did not "
            "finish before timeout."
        )

        for track, match in queued:
            key = transfer_key(
                match["username"],
                match["filename"],
            )

            if (
                key not in completed
                and key not in failed
            ):
                track["acquisition"]["status"] = (
                    "download_timeout"
                )

        save_state(state)

        return 1

    log("")
    log(
        f"Download stage finished: "
        f"{len(completed)} downloaded, "
        f"{len(failed)} failed."
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())