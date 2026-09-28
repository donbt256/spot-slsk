import hashlib
import os
import re
import unicodedata
from difflib import SequenceMatcher


MATCHER_VERSION = 3

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
    "live session",
    "live sessions",
    "acoustic",
    "acoustic version",
    "instrumental",
    "karaoke",
    "remix",
    "demo",
    "piano demo",
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
    "anniversary edition",
}

COMMON_SHORT_TITLES = {
    "body",
    "life",
    "inside",
    "two",
    "seven",
    "one",
    "home",
    "love",
    "time",
    "fire",
    "mother",
    "girl",
    "boy",
    "stay",
    "dream",
    "breathe",
    "weep",
}


def normalize(value):
    value = value or ""

    value = unicodedata.normalize(
        "NFKD",
        value,
    )

    value = "".join(
        char
        for char in value
        if not unicodedata.combining(char)
    )

    value = value.lower()

    value = value.replace("&", " and ")
    value = value.replace("’", "'")
    value = value.replace("–", "-")
    value = value.replace("—", "-")

    value = re.sub(
        r"[_]+",
        " ",
        value,
    )

    value = re.sub(
        r"[^a-z0-9]+",
        " ",
        value,
    )

    return re.sub(
        r"\s+",
        " ",
        value,
    ).strip()


def similarity(a, b):
    a = normalize(a)
    b = normalize(b)

    if not a or not b:
        return 0.0

    if a == b:
        return 1.0

    return SequenceMatcher(
        None,
        a,
        b,
    ).ratio()


def candidate_id(candidate):
    value = "|".join(
        [
            str(candidate.get("username") or ""),
            str(candidate.get("filename") or ""),
            str(candidate.get("size") or ""),
            str(candidate.get("extension") or ""),
        ]
    )

    digest = hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()

    return f"c_{digest[:16]}"


def is_audio_file(filename):
    extension = os.path.splitext(
        filename or ""
    )[1].lower()

    return extension in AUDIO_EXTENSIONS


def filename_stem(filename):
    name = os.path.basename(
        filename or ""
    )

    stem, _ = os.path.splitext(name)

    return stem.strip()


def extract_track_number(filename):
    name = os.path.basename(
        filename or ""
    )

    patterns = [
        r"^\s*(\d{1,3})\s*[-._ ]",
        r"^\s*(\d{1,2})\s*of\s*\d{1,3}\s*[-._ ]",
    ]

    for pattern in patterns:
        match = re.match(
            pattern,
            name,
            flags=re.IGNORECASE,
        )

        if match:
            return int(
                match.group(1)
            )

    return None


def extract_candidate_title(
    filename,
    artist=None,
):
    title = filename_stem(filename)

    # Remove a leading track number.
    title = re.sub(
        r"^\s*\d{1,3}\s*(?:[-._]|of\s+\d{1,3}\s*[-._])\s*",
        "",
        title,
        flags=re.IGNORECASE,
    )

    # Remove a leading artist name when it is clearly
    # separated from the title.
    if artist:
        normalized_artist = normalize(
            artist
        )

        normalized_title = normalize(
            title
        )

        if normalized_title.startswith(
            normalized_artist + " "
        ):
            title = title[
                len(artist):
            ].strip()

            title = re.sub(
                r"^\s*[-–—:|]\s*",
                "",
                title,
            )

    return title.strip()


def detect_alternate_terms(value):
    normalized = normalize(value)

    found = []

    for term in ALTERNATE_TERMS:
        normalized_term = normalize(term)

        if re.search(
            rf"\b{re.escape(normalized_term)}\b",
            normalized,
        ):
            found.append(term)

    return sorted(
        set(found)
    )


def title_classification(
    target_title,
    candidate_filename,
    artist=None,
):
    candidate_title = extract_candidate_title(
        candidate_filename,
        artist=artist,
    )

    target_normalized = normalize(
        target_title
    )

    candidate_normalized = normalize(
        candidate_title
    )

    if (
        not target_normalized
        or not candidate_normalized
    ):
        return {
            "classification": "unknown",
            "similarity": 0.0,
            "candidate_title": candidate_title,
            "alternate_terms": [],
        }

    alternate_terms = detect_alternate_terms(
        candidate_title
    )

    # Remove alternate/version terms for comparison.
    candidate_without_alternate = (
        candidate_normalized
    )

    for term in alternate_terms:
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

    if candidate_normalized == target_normalized:
        classification = "exact"
        score = 1.0

    elif (
        candidate_without_alternate
        == target_normalized
        and alternate_terms
    ):
        classification = "alternate"
        score = 1.0

    else:
        score = SequenceMatcher(
            None,
            target_normalized,
            candidate_normalized,
        ).ratio()

        if score >= 0.92:
            classification = "near_exact"
        elif score >= 0.72:
            classification = "similar"
        else:
            classification = "mismatch"

        if (
            alternate_terms
            and classification != "exact"
        ):
            classification = "alternate"

    return {
        "classification": classification,
        "similarity": round(score, 4),
        "candidate_title": candidate_title,
        "alternate_terms": alternate_terms,
    }


def path_similarity(
    target,
    filename,
):
    parts = re.split(
        r"[\\/]+",
        filename or "",
    )

    best = 0.0

    for part in parts:
        score = similarity(
            target,
            part,
        )

        best = max(
            best,
            score,
        )

    return best


def path_contains(
    value,
    filename,
):
    normalized_value = normalize(
        value
    )

    normalized_filename = normalize(
        filename
    )

    if not normalized_value:
        return False

    return normalized_value in normalized_filename


def score_candidate(
    track,
    candidate,
):
    spotify = track["spotify"]

    filename = candidate.get(
        "filename"
    ) or ""

    artist = spotify.get(
        "artist"
    ) or ""

    album = spotify.get(
        "album"
    ) or ""

    title = spotify.get(
        "title"
    ) or ""

    track_number = spotify.get(
        "track_number"
    )

    if not is_audio_file(filename):
        return None

    title_info = title_classification(
        title,
        filename,
        artist=artist,
    )

    title_class = title_info[
        "classification"
    ]

    title_similarity = title_info[
        "similarity"
    ]

    # A clear title mismatch is never a valid candidate.
    if title_class in {
        "mismatch",
        "unknown",
    }:
        return None

    artist_similarity = path_similarity(
        artist,
        filename,
    )

    album_similarity = path_similarity(
        album,
        filename,
    )

    artist_exact = path_contains(
        artist,
        filename,
    )

    album_exact = path_contains(
        album,
        filename,
    )

    candidate_track_number = (
        extract_track_number(filename)
    )

    score = 0.0

    # Title: 50 points.
    if title_class == "exact":
        score += 50.0

    elif title_class == "near_exact":
        score += 43.0

    elif title_class == "similar":
        score += 30.0

    elif title_class == "alternate":
        score += 45.0

    score += title_similarity * 5.0

    # Artist: 25 points.
    if artist_exact:
        score += 25.0

    elif artist_similarity >= 0.90:
        score += 20.0

    elif artist_similarity >= 0.75:
        score += 12.0

    # Album: 15 points.
    if album_exact:
        score += 15.0

    elif album_similarity >= 0.90:
        score += 12.0

    elif album_similarity >= 0.75:
        score += 7.0

    # Track number: 10 points.
    if (
        track_number is not None
        and candidate_track_number is not None
    ):
        if candidate_track_number == track_number:
            score += 10.0
        else:
            score -= 5.0

    alternate_terms = title_info[
        "alternate_terms"
    ]

    # Explicit alternate versions should not be
    # automatically accepted.
    if alternate_terms:
        score -= 15.0

    score = max(
        0.0,
        min(
            100.0,
            score,
        ),
    )

    normalized_title = normalize(
        title
    )

    is_short_common_title = (
        normalized_title
        in COMMON_SHORT_TITLES
    )

    # Short/common titles require stronger evidence.
    strong_identity = (
        title_class in {
            "exact",
            "near_exact",
        }
        and artist_exact
        and (
            album_exact
            or album_similarity >= 0.90
        )
    )

    if is_short_common_title:
        if not strong_identity:
            decision = "llm"
        elif alternate_terms:
            decision = "llm"
        elif score >= 90:
            decision = "accept"
        else:
            decision = "llm"

    elif title_class == "alternate":
        decision = "llm"

    elif score >= 90:
        decision = "accept"

    elif score >= 60:
        decision = "llm"

    else:
        decision = "reject"

    result = {
        **candidate,
        "candidate_id": candidate_id(
            candidate
        ),
        "score": round(score, 2),
        "decision": decision,
        "match": {
            "matcher_version": MATCHER_VERSION,
            "title": title_info,
            "artist_similarity": round(
                artist_similarity,
                4,
            ),
            "artist_exact": artist_exact,
            "album_similarity": round(
                album_similarity,
                4,
            ),
            "album_exact": album_exact,
            "track_number":
                candidate_track_number,
            "target_track_number":
                track_number,
            "alternate_terms":
                alternate_terms,
            "short_common_title":
                is_short_common_title,
            "strong_identity":
                strong_identity,
        },
    }

    return result


def rank_candidates(
    track,
    candidates,
):
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
            candidate["match"][
                "artist_exact"
            ],
            candidate["match"][
                "album_exact"
            ],
            bool(
                candidate.get(
                    "peer",
                    {},
                ).get(
                    "has_free_upload_slot"
                )
            ),
            -(
                candidate.get(
                    "peer",
                    {},
                ).get(
                    "queue_length"
                )
                or 0
            ),
        ),
        reverse=True,
    )

    return scored


def classify_candidates(
    track,
    candidates,
):
    ranked = rank_candidates(
        track,
        candidates,
    )

    accepted = [
        candidate
        for candidate in ranked
        if candidate["decision"]
        == "accept"
    ]

    llm_candidates = [
        candidate
        for candidate in ranked
        if candidate["decision"]
        == "llm"
    ]

    rejected = [
        candidate
        for candidate in ranked
        if candidate["decision"]
        == "reject"
    ]

    return {
        "matcher_version": MATCHER_VERSION,
        "all_ranked": ranked,
        "accepted": accepted,
        "llm_candidates": llm_candidates,
        "rejected": rejected,
    }