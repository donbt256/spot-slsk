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

    album = str(
        data.get("album")
        or ""
    ).strip()

    return artist, album


def release_key(track):
    artist, album = release_identity(track)

    raw = f"{artist}\x1f{album}".encode(
        "utf-8",
        errors="replace",
    )

    return hashlib.sha1(raw).hexdigest()[:16]


def release_label(track):
    artist, album = release_identity(track)

    return f"{artist} - {album}"


def group_releases(tracks):
    groups = {}

    for track in tracks:
        key = release_key(track)

        if key not in groups:
            groups[key] = {
                "key": key,
                "label": release_label(track),
                "tracks": [],
            }

        groups[key]["tracks"].append(track)

    return list(groups.values())
