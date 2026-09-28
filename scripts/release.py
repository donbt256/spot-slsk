import hashlib


def spotify(track):
    return track.get("spotify", track)


def release_identity(track):
    data = spotify(track)
    artist = str(
        data.get("album_artist")
        or data.get("artist")
        or ""
    ).strip()
    album = str(data.get("album") or "").strip()
    return artist, album


def release_key(track):
    sources = track.get("sources", {})
    album_ids = sources.get("album_ids", [])

    if album_ids:
        # Spotify album ID is the primary release identity. Artist/album
        # metadata remains in the key only as a guard against malformed
        # state containing a mismatched album ID.
        artist, album = release_identity(track)
        raw = f"album:{album_ids[0]}\x1f{artist}\x1f{album}".encode(
            "utf-8", errors="replace"
        )
    else:
        # Playlist and direct-track inputs are individual acquisitions.
        # This prevents several playlist tracks from the same album from
        # accidentally becoming an album download.
        track_id = spotify(track).get("id") or ""
        raw = f"track:{track_id}".encode("utf-8", errors="replace")

    return hashlib.sha1(raw).hexdigest()[:16]


def release_label(track):
    data = spotify(track)
    sources = track.get("sources", {})
    if sources.get("album_ids"):
        artist, album = release_identity(track)
        return f"{artist} - {album}"

    return (
        f"{data.get('artist') or 'Unknown Artist'} - "
        f"{data.get('title') or 'Unknown Track'}"
    )


def group_releases(tracks):
    groups = {}

    for track in tracks:
        if not track.get("active_source", True):
            continue

        key = release_key(track)

        if key not in groups:
            groups[key] = {
                "key": key,
                "label": release_label(track),
                "tracks": [],
            }

        groups[key]["tracks"].append(track)

    return list(groups.values())
