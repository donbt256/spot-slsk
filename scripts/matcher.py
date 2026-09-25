import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import PurePath


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
    "slowed + reverb",
    "slowed and reverb",
    "8d",
    "nightcore",
    "cover",
    "tribute",
    "re-recorded",
    "re recorded",
}


def normalize(text):
    if not text:
        return ""

    text = unicodedata.normalize(
        "NFKD",
        str(text),
    )

    text = "".join(
        character
        for character in text
        if not unicodedata.combining(character)
    )

    text = text.lower()
    text = text.replace("&", " and ")

    text = re.sub(
        r"[\[\]{}()_,.!?;:'\"`~]",
        " ",
        text,
    )

    text = re.sub(
        r"[-–—_/\\]+",
        " ",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def similarity(left, right):
    left = normalize(left)
    right = normalize(right)

    if not left or not right:
        return 0.0

    if left == right:
        return 1.0

    return SequenceMatcher(
        None,
        left,
        right,
    ).ratio()


def filename_without_extension(filename):
    if not filename:
        return ""

    return PurePath(filename).stem


def contains_alternate_term(filename):
    normalized = normalize(
        filename_without_extension(filename)
    )

    for term in ALTERNATE_TERMS:
        if term in normalized:
            return term

    return None


def extract_track_number(filename):
    if not filename:
        return None

    name = filename_without_extension(filename)

    patterns = [
        r"^\s*(\d{1,2})\s*[-.)]",
        r"^\s*(\d{1,2})\s+",
        r"\btrack\s*(\d{1,2})\b",
        r"\btrk\s*(\d{1,2})\b",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            name,
            flags=re.IGNORECASE,
        )

        if match:
            number = int(match.group(1))

            if 1 <= number <= 99:
                return number

    return None


def score_candidate(track, candidate):
    spotify = track["spotify"]

    filename = candidate.get("filename") or ""
    stem = filename_without_extension(filename)

    artist = spotify.get("artist") or ""
    title = spotify.get("title") or ""
    album = spotify.get("album") or ""

    artist_score = similarity(artist, stem)
    title_score = similarity(title, stem)
    album_score = similarity(album, stem)

    normalized_filename = normalize(stem)
    normalized_artist = normalize(artist)
    normalized_title = normalize(title)
    normalized_album = normalize(album)

    artist_exact = (
        normalized_artist
        and normalized_artist in normalized_filename
    )

    title_exact = (
        normalized_title
        and normalized_title in normalized_filename
    )

    album_exact = (
        normalized_album
        and normalized_album in normalized_filename
    )

    score = (
        artist_score * 25
        + title_score * 40
        + album_score * 20
    )

    if artist_exact:
        score += 5

    if title_exact:
        score += 5

    track_number = extract_track_number(filename)

    expected_track_number = spotify.get(
        "track_number"
    )

    track_number_match = (
        track_number is not None
        and expected_track_number is not None
        and track_number == expected_track_number
    )

    if track_number_match:
        score += 15

    alternate_term = contains_alternate_term(
        filename
    )

    if alternate_term:
        if alternate_term not in {
            "remastered",
            "remaster",
        }:
            score -= 35
        else:
            score -= 5

    score = max(
        0.0,
        min(100.0, score),
    )

    return {
        "score": round(score, 2),
        "signals": {
            "artist_similarity": round(
                artist_score,
                4,
            ),
            "title_similarity": round(
                title_score,
                4,
            ),
            "album_similarity": round(
                album_score,
                4,
            ),
            "artist_exact": bool(
                artist_exact
            ),
            "title_exact": bool(
                title_exact
            ),
            "album_exact": bool(
                album_exact
            ),
            "filename_track_number": track_number,
            "expected_track_number": expected_track_number,
            "track_number_match": bool(
                track_number_match
            ),
            "alternate_term": alternate_term,
        },
    }


def rank_candidates(track, candidates):
    ranked = []

    for candidate in candidates:
        result = score_candidate(
            track,
            candidate,
        )

        ranked.append({
            **candidate,
            "match": result,
        })

    ranked.sort(
        key=lambda item: (
            item["match"]["score"],
            bool(
                item["peer"].get(
                    "has_free_upload_slot"
                )
            ),
            -(
                item["peer"].get(
                    "queue_length"
                )
                or 999999
            ),
        ),
        reverse=True,
    )

    return ranked
