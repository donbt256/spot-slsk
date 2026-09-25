import json
import os
import sys
import time
from pathlib import Path

from soulseek import SoulseekClient


STATE_FILE = Path("state/tracks.json")
DOWNLOAD_ROOT = Path(
    os.environ.get("SOULSEEK_DOWNLOAD_DIR", "/home/runner/music-downloads")
)

POLL_SECONDS = 5
TIMEOUT_SECONDS = 60 * 60


def load_state():
    with STATE_FILE.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")

    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    temporary.replace(STATE_FILE)


def selected_matches(state):
    matches = []

    for track in state.get("tracks", []):
        acquisition = track.get("acquisition", {})

        if acquisition.get("status") != "matched":
            continue

        match = acquisition.get("match")

        if not match:
            continue

        username = match.get("username")
        filename = match.get("filename")

        if not username or not filename:
            continue

        matches.append((track, match))

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


def find_downloaded_file(username, remote_filename):
    remote_name = Path(str(remote_filename).replace("\\", "/")).name

    exact_matches = []

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
    state = load_state()

    client = SoulseekClient(
        base_url=os.environ.get(
            "SLSKD_URL",
            "http://127.0.0.1:5030",
        ),
        api_key=os.environ.get("SLSKD_API_KEY"),
    )

    matches = selected_matches(state)

    if not matches:
        print("No matched tracks need downloading.")
        return 0

    print(f"Queueing {len(matches)} downloads...")

    queued = []

    for track, match in matches:
        username = match["username"]
        filename = match["filename"]
        size = match.get("size")

        title = track["spotify"]["title"]

        print(f"Queueing: {title}")
        print(f"  User: {username}")
        print(f"  File: {filename}")

        try:
            client.enqueue_download(
                username=username,
                filename=filename,
                size=size,
            )

            track["acquisition"]["status"] = "downloading"
            queued.append((track, match))

        except Exception as exc:
            print(f"  FAILED: {exc}")

            track["acquisition"]["status"] = "download_failed"
            track["acquisition"]["download_error"] = str(exc)

    save_state(state)

    if not queued:
        print("No downloads were successfully queued.")
        return 1

    print(f"Queued {len(queued)} downloads.")
    print("Waiting for Soulseek transfers...")

    deadline = time.monotonic() + TIMEOUT_SECONDS

    wanted = {
        transfer_key(match["username"], match["filename"]): (track, match)
        for track, match in queued
    }

    completed = set()

    while time.monotonic() < deadline:
        try:
            data = client.get_downloads()
        except Exception as exc:
            print(f"Unable to read download status: {exc}")
            time.sleep(POLL_SECONDS)
            continue

        transfers = extract_transfers(data)

        for transfer in transfers:
            username = transfer_username(transfer)
            filename = transfer_filename(transfer)

            if not username or not filename:
                continue

            key = transfer_key(username, filename)

            if key not in wanted or key in completed:
                continue

            current_state = transfer_state(transfer).lower()

            print(
                f"Transfer: {filename} "
                f"[{current_state or 'unknown'}]"
            )

            track, match = wanted[key]

            if "succeeded" in current_state:
                path = find_downloaded_file(username, filename)

                if path is None:
                    continue

                track["acquisition"]["status"] = "downloaded"
                track["acquisition"]["file"] = {
                    "path": str(path),
                    "filename": path.name,
                    "size": path.stat().st_size,
                }

                completed.add(key)
                save_state(state)

                print(f"  Downloaded: {path}")

            elif any(
                failure in current_state
                for failure in (
                    "rejected",
                    "timedout",
                    "errored",
                    "failed",
                )
            ):
                track["acquisition"]["status"] = "download_failed"
                track["acquisition"]["download_error"] = (
                    current_state
                )

                completed.add(key)
                save_state(state)

                print(f"  Download failed: {current_state}")

        if len(completed) == len(wanted):
            break

        print(
            f"Progress: {len(completed)}/{len(wanted)} complete"
        )

        time.sleep(POLL_SECONDS)

    unresolved = len(wanted) - len(completed)

    if unresolved:
        print(
            f"{unresolved} download(s) did not finish before timeout."
        )

        for track, match in queued:
            key = transfer_key(
                match["username"],
                match["filename"],
            )

            if key not in completed:
                track["acquisition"]["status"] = "download_timeout"

        save_state(state)

        return 1

    print("All queued downloads finished.")

    return 0


if __name__ == "__main__":
    sys.exit(main())