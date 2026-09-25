import json
from pathlib import Path

from spotify import resolve_urls


ROOT = Path(__file__).resolve().parent.parent
URLS_FILE = ROOT / "urls.txt"
STATE_DIR = ROOT / "state"
TRACKS_FILE = STATE_DIR / "tracks.json"


def load_urls():
    if not URLS_FILE.exists():
        raise FileNotFoundError(
            f"Missing {URLS_FILE}"
        )

    return URLS_FILE.read_text(
        encoding="utf-8"
    ).splitlines()


def main():
    urls = load_urls()

    print(f"Found {len(urls)} input lines.")

    tracks = resolve_urls(urls)

    STATE_DIR.mkdir(parents=True, exist_ok=True)

    output = {
        "version": 1,
        "tracks": tracks,
    }

    TRACKS_FILE.write_text(
        json.dumps(
            output,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print()
    print(f"Resolved {len(tracks)} unique tracks.")
    print(f"Wrote {TRACKS_FILE}")


if __name__ == "__main__":
    main()
