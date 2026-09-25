import base64
import os
import re

import requests


SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_URL = "https://api.spotify.com/v1"


class SpotifyClient:
    def __init__(self):
        client_id = os.environ["SPOTIFY_CLIENT_ID"]
        client_secret = os.environ["SPOTIFY_CLIENT_SECRET"]

        credentials = f"{client_id}:{client_secret}".encode()
        encoded = base64.b64encode(credentials).decode()

        response = requests.post(
            SPOTIFY_TOKEN_URL,
            headers={
                "Authorization": f"Basic {encoded}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "client_credentials",
            },
            timeout=30,
        )

        response.raise_for_status()

        self.token = response.json()["access_token"]

    def request(self, endpoint):
        response = requests.get(
            f"{SPOTIFY_API_URL}{endpoint}",
            headers={
                "Authorization": f"Bearer {self.token}",
            },
            timeout=30,
        )

        response.raise_for_status()
        return response.json()

    @staticmethod
    def parse_url(url):
        match = re.search(
            r"open\.spotify\.com/(track|album|playlist)/([A-Za-z0-9]+)",
            url,
        )

        if not match:
            raise ValueError(f"Unsupported Spotify URL: {url}")

        return match.group(1), match.group(2)

    def get_track(self, track_id):
        data = self.request(f"/tracks/{track_id}")

        return self.normalize_track(data)

    def get_album(self, album_id):
        album = self.request(f"/albums/{album_id}")

        tracks = []
        offset = 0

        while True:
            page = self.request(
                f"/albums/{album_id}/tracks?limit=50&offset={offset}"
            )

            for track in page["items"]:
                tracks.append(
                    self.normalize_track(
                        track,
                        album_override=album,
                    )
                )

            if not page["next"]:
                break

            offset += len(page["items"])

        return tracks

    def get_playlist(self, playlist_id):
        tracks = []
        offset = 0

        while True:
            page = self.request(
                f"/playlists/{playlist_id}/tracks"
                f"?limit=100&offset={offset}"
                f"&fields=items(track(id,name,artists,album,"
                f"duration_ms,external_ids,external_urls,"
                f"track_number,disc_number))"
            )

            for item in page["items"]:
                track = item.get("track")

                if not track:
                    continue

                tracks.append(self.normalize_track(track))

            if not page["next"]:
                break

            offset += len(page["items"])

        return tracks

    def normalize_track(self, track, album_override=None):
        album = album_override or track["album"]

        artists = [
            artist["name"]
            for artist in track.get("artists", [])
        ]

        album_artists = [
            artist["name"]
            for artist in album.get("artists", [])
        ]

        images = album.get("images", [])

        return {
            "id": track["id"],
            "url": track.get("external_urls", {}).get(
                "spotify"
            ),
            "artist": artists[0] if artists else None,
            "artists": artists,
            "album": album.get("name"),
            "album_artist": (
                album_artists[0]
                if album_artists
                else None
            ),
            "album_artists": album_artists,
            "title": track.get("name"),
            "track_number": track.get("track_number"),
            "disc_number": track.get("disc_number"),
            "total_tracks": album.get("total_tracks"),
            "duration_ms": track.get("duration_ms"),
            "release_date": album.get("release_date"),
            "release_date_precision": album.get(
                "release_date_precision"
            ),
            "isrc": (
                track.get("external_ids", {})
                .get("isrc")
            ),
            "album_art": (
                {
                    "url": images[0]["url"],
                    "width": images[0]["width"],
                    "height": images[0]["height"],
                }
                if images
                else None
            ),
        }


def resolve_urls(urls):
    client = SpotifyClient()
    tracks = {}

    for raw_url in urls:
        url = raw_url.strip()

        if not url or url.startswith("#"):
            continue

        url = url.split("?", 1)[0]

        kind, spotify_id = client.parse_url(url)

        print(f"Resolving {kind}: {spotify_id}")

        if kind == "track":
            resolved = [client.get_track(spotify_id)]

        elif kind == "album":
            resolved = client.get_album(spotify_id)

        elif kind == "playlist":
            resolved = client.get_playlist(spotify_id)

        else:
            raise ValueError(
                f"Unsupported Spotify type: {kind}"
            )

        for track in resolved:
            tracks[track["id"]] = track

    return list(tracks.values())

