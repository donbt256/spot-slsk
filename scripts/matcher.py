import os
import re
import unicodedata
from difflib import SequenceMatcher


AUDIO_EXTENSIONS = {
    ".mp3",
    ".flac",
    ".m4a",
    ".mp4",
    ".aac",
    ".ogg",
    ".opus",
    ".wav",
    ".aiff",
    ".aif",
    ".wma",
    ".alac",
}

ALTERNATE_TERMS = {
    "live",
    "acoustic",
    "instrumental",
    "karaoke",
    "remix",
    "demo",
    "radio edit",
    "edit",
    "sped up",
    "slowed",
    "slowed reverb",
    "slowed and reverb",
    "8d",
    "nightcore",
    "cover",
    "tribute",
    "re recorded",
    "re-recorded",
    "remaster",
    "remastered",
}


def normalize(value):
    value = value or ""

    value = unicodedata.normalize("NFKD", value)
    value = "".join(
        char for char in value
        if not unicodedata.combining(char)
    )

    value = value.lower()
    value = value.replace("&", " and ")

    value = value.replace("’", "'")
    value = value.replace("–", "-")
    value = value.replace("—", "-")

    value = re.sub(r"[_]+", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)

    return re.sub(r"\s+", " ", value).strip()


def similarity(a, b):
    a = normalize(a)
    b = normalize(b)

    if not a or not b:
        return 0.0

    if a == b:
        return 1.0

    return SequenceMatcher(None, a, b).ratio()


def contains_alternate_term(value):
    normalized = normalize(value)

    for term in ALTERNATE_TERMS:
        if normalize(term) in normalized:
            return True

    return False


def extract_track_number(filename):
    name = os.path.basename(filename or "")

    patterns = [
        r"^\s*(\d{1,3})\s*[-._ ]",
        r"^\s*(\d{1,2})\s*of\s*\d{1,3}\s*[-._ ]",
        r"^\s*(\d{1,2})\s*[-._ ]",
    ]

    for pattern in patterns:
        match = re.match(
            pattern,
            name,
            flags=re.IGNORECASE,
        )

        if match:
            return int(match.group(1))

    return None


def is_audio_file(filename):
    extension = os.path.splitext(
        filename or ""
    )[1].lower()

    return extension in AUDIO_EXTENSIONS


def filename_stem(filename):
    name = os.path.basename(filename or "")
    stem, _ = os.path.splitext(name)
    return stem.strip()


def extract_candidate_title(filename, artist=None):
    """
    Extract the most likely track title from a remote filename.

    Examples:

      06 Body.mp3
        -> Body

      Mother Mother - Body Of Years.flac
        -> Body Of Years

      13 - Until It Doesn't Hurt.mp3
        -> Until It Doesn't Hurt
    """

    title = filename_stem(filename)

    # Remove leading track number.
    title = re.sub(
        r"^\s*\d{1,3}\s*(?:[-._]|of\s+\d{1,3}\s*[-._])\s*",
        "",
        title,
        flags=re.IGNORECASE,
    )

    if artist:
        normalized_artist = normalize(artist)
        normalized_title = normalize(title)

        if normalized_title.startswith(
            normalized_artist + " "
        ):
            title = title[len(artist):].strip()

            title = re.sub(
                r"^\s*[-–—:|]\s*",
                "",
                title,
            )

    return title.strip()


def title_classification(
    target_title,
    candidate_filename,
    artist=None,
):
    candidate_title = extract_candidate_title(
        candidate_filename,
        artist=artist,
    )

    target_normalized = normalize(target_title)
    candidate_normalized = normalize(candidate_title)

    if not target_normalized or not candidate_normalized:
        return {
            "classification": "unknown",
            "similarity": 0.0,
            "candidate_title": candidate_title,
        }

    if candidate_normalized == target_normalized:
        return {
            "classification": "exact",
            "similarity": 1.0,
            "candidate_title": candidate_title,
        }

    # Check for obvious version/alternate suffixes.
    alternate = contains_alternate_term(
        candidate_title
    )

    candidate_without_alternate = candidate_normalized

    for term in ALTERNATE_TERMS:
        normalized_term = normalize(term)

        candidate_without_alternate = re.sub(
            rf"\b{re.escape(normalized_term)}\b",
            "",
            candidate_without_alternate,
        )

    candidate_without_alternate = re.sub(
        r"\s+",
        " ",
        candidate_without_alternate,
    ).strip()

    if candidate_without_alternate == target_normalized:
        return {
            "classification": "alternate",
            "similarity": 1.0,
            "candidate_title": candidate_title,
        }

    score = SequenceMatcher(
        None,
        target_normalized,
        candidate_normalized,
    ).ratio()

    if score >= 0.90:
        classification = "near_exact"
    elif score >= 0.72:
        classification = "similar"
    else:
        classification = "mismatch"

    if alternate and classification != "exact":
        classification = "alternate"

    return {
        "classification": classification,
        "similarity": score,
        "candidate_title": candidate_title,
    }


def score_candidate(track, candidate):
    spotify = track["spotify"]

    filename = candidate.get("filename") or ""
    artist = spotify.get("artist") or ""
    title = spotify.get("title") or ""
    album = spotify.get("album") or ""
    track_number = spotify.get("track_number")

    # Non-audio files should never compete with audio.
    if not is_audio_file(filename):
        return None

    candidate_title_info = title_classification(
        title,
        filename,
        artist=artist,
    )

    title_class = candidate_title_info["classification"]
    title_similarity = candidate_title_info["similarity"]

    # An actual title mismatch is not a valid match.
    #
    # This prevents:
    #   O My Heart -> 06 Body.mp3
    #   Inside -> album.nfo
    #
    # from winning because artist/album happen to match.
    if title_class == "mismatch":
        return None

    normalized_filename = normalize(filename)
    normalized_artist = normalize(artist)
    normalized_album = normalize(album)

    artist_score = similarity(
        artist,
        normalized_filename,
    )

    # Album is inferred primarily from directory/path.
    path_parts = re.split(
        r"[\\/]+",
        filename,
    )

    album_similarity = 0.0

    for part in path_parts[:-1]:
        part_score = similarity(
            album,
            part,
        )

        album_similarity = max(
            album_similarity,
            part_score,
        )

    if (
        normalized_album
        and normalized_album in normalized_filename
    ):
        album_similarity = max(
            album_similarity,
            1.0,
        )

    score = 0.0

    # Title is the strongest signal.
    if title_class == "exact":
        score += 60.0
    elif title_class == "near_exact":
        score += 48.0
    elif title_class == "similar":
        score += 30.0
    elif title_class == "alternate":
        score += 18.0

    score += title_similarity * 15.0

    # Artist.
    if normalized_artist in normalized_filename:
        score += 15.0
    else:
        score += artist_score * 10.0

    # Album/path.
    score += album_similarity * 15.0

    # Track number.
    candidate_track_number = extract_track_number(
        filename
    )

    if (
        track_number is not None
        and candidate_track_number is not None
    ):
        if candidate_track_number == track_number:
            score += 10.0
        else:
            score -= 5.0

    # Alternate versions are useful candidates, but should not beat
    # an exact normal-version match.
    if title_class == "alternate":
        score -= 15.0

    return {
        **candidate,
        "score": round(score, 2),
        "match": {
            "title": candidate_title_info,
            "audio": True,
            "track_number": candidate_track_number,
        },
    }


def rank_candidates(track, candidates):
    scored = []

    for candidate in candidates:
        result = score_candidate(
            track,
            candidate,
        )

        if result is not None:
            scored.append(result)

    scored.sort(
        key=lambda candidate: (
            candidate["score"],
            bool(
                candidate.get("peer", {}).get(
                    "has_free_upload_slot"
                )
            ),
            -(
                candidate.get("peer", {}).get(
                    "queue_length"
                )
                or 0
            ),
        ),
        reverse=True,
    )

    return scored