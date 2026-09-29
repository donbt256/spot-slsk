import base64
import os
import re

import requests
import time


SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_URL = "https://api.spotify.com/v1"


class SpotifyClient:
    def __init__(self):
        self._next_request_at = 0.0
        client_id = os.environ["SPOTIFY_CLIENT_ID"]
        client_secret = os.environ["SPOTIFY_CLIENT_SECRET"]

        credentials = f"{client_id}:{client_secret}".encode()
        encoded = base64.b64encode(credentials).decode()

        response = self._request_with_retry(
            "POST",
            SPOTIFY_TOKEN_URL,
            headers={
                "Authorization": f"Basic {encoded}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
        )
        response.raise_for_status()
        self.token = response.json()["access_token"]

    def _request_with_retry(self, method, url, **kwargs):
        last_response = None
        for attempt in range(1, 6):
            wait = self._next_request_at - time.monotonic()
            if wait > 0:
                time.sleep(wait)

            try:
                response = requests.request(
                    method,
                    url,
                    timeout=30,
                    **kwargs,
                )
            except requests.RequestException:
                if attempt >= 5:
                    raise
                time.sleep(2 ** (attempt - 1))
                continue

            last_response = response
            if response.status_code not in {429, 500, 502, 503, 504}:
                response.raise_for_status()
                return response

            if attempt >= 5:
                response.raise_for_status()

            retry_after = response.headers.get("Retry-After")
            try:
                delay = max(1, int(float(retry_after)))
            except (TypeError, ValueError):
                delay = 2 ** (attempt - 1)

            self._next_request_at = max(
                self._next_request_at,
                time.monotonic() + delay,
            )

            print(
                f"Spotify returned HTTP {response.status_code}; "
                f"retrying in {delay}s...",
                flush=True,
            )
            time.sleep(delay)

        if last_response is not None:
            last_response.raise_for_status()
        raise RuntimeError("Spotify request failed.")

    def request(self, endpoint):
        response = self._request_with_retry(
            "GET",
            f"{SPOTIFY_API_URL}{endpoint}",
            headers={"Authorization": f"Bearer {self.token}"},
        )
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
        return self.normalize_track(
            self.request(f"/tracks/{track_id}")
        )

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
                    self.normalize_track(track, album_override=album)
                )
            if not page.get("next"):
                break
            offset += len(page["items"])

        return tracks

    def get_playlist(self, playlist_id):
        playlist = self.request(
            f"/playlists/{playlist_id}"
        )

        playlist_info = {
            "id": playlist_id,
            "name": playlist.get("name") or playlist_id,
            "url": playlist.get("external_urls", {}).get("spotify"),
            "snapshot_id": playlist.get("snapshot_id"),
            "description": playlist.get("description"),
            "public": playlist.get("public"),
            "collaborative": playlist.get("collaborative"),
            "owner": (
                playlist.get("owner", {}).get("display_name")
                or playlist.get("owner", {}).get("id")
            ),
            "image": (
                {
                    "url": playlist["images"][0]["url"],
                    "width": playlist["images"][0].get("width"),
                    "height": playlist["images"][0].get("height"),
                }
                if playlist.get("images")
                else None
            ),
            "tracks": [],
        }

        tracks = []
        offset = 0

        while True:
            page = self.request(
                f"/playlists/{playlist_id}/items"
                f"?limit=50&offset={offset}"
                f"&fields=items(item(id,name,artists,album,duration_ms,"
                f"external_ids,external_urls,track_number,disc_number,type)),"
                f"next,total"
            )

            for item in page.get("items", []):
                track = item.get("item")
                if not track or track.get("type") != "track":
                    continue

                normalized = self.normalize_track(track)
                tracks.append(normalized)
                playlist_info["tracks"].append(normalized["id"])

            if not page.get("next"):
                break
            offset += len(page.get("items", []))

        return playlist_info, tracks

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
            "url": track.get("external_urls", {}).get("spotify"),
            "artist": artists[0] if artists else None,
            "artists": artists,
            "album": album.get("name"),
            "album_artist": (
                album_artists[0] if album_artists else None
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
            "isrc": track.get("external_ids", {}).get("isrc"),
            "album_art": (
                {
                    "url": images[0]["url"],
                    "width": images[0].get("width"),
                    "height": images[0].get("height"),
                }
                if images
                else None
            ),
        }


def resolve_urls(urls, cache=None):
    client = SpotifyClient()
    tracks = {}
    track_sources = {}
    playlists = {}
    cache = cache if isinstance(cache, dict) else {}
    cache.setdefault("tracks", {})
    cache.setdefault("albums", {})
    cache.setdefault("playlists", {})

    for raw_url in urls:
        url = raw_url.strip()
        if not url or url.startswith("#"):
            continue
        url = url.split("?", 1)[0]
        kind, spotify_id = client.parse_url(url)
        print(f"Resolving {kind}: {spotify_id}", flush=True)

        if kind == "track":
            cached = cache["tracks"].get(spotify_id)
            if cached:
                print("  Using cached Spotify track.", flush=True)
                resolved = [cached]
            else:
                resolved = [client.get_track(spotify_id)]
                cache["tracks"][spotify_id] = resolved[0]
            source = {"track_ids": [spotify_id]}
        elif kind == "album":
            cached = cache["albums"].get(spotify_id)
            if cached:
                print(f"  Using cached Spotify album ({len(cached)} tracks).", flush=True)
                resolved = cached
            else:
                resolved = client.get_album(spotify_id)
                cache["albums"][spotify_id] = resolved
                for track in resolved:
                    cache["tracks"][track["id"]] = track
            source = {"album_ids": [spotify_id]}
        elif kind == "playlist":
            cached = cache["playlists"].get(spotify_id)
            if cached:
                print("  Using cached Spotify playlist.", flush=True)
                playlist = cached["playlist"]
                resolved = cached["tracks"]
            else:
                playlist, resolved = client.get_playlist(spotify_id)
                cache["playlists"][spotify_id] = {"playlist": playlist, "tracks": resolved}
                for track in resolved:
                    cache["tracks"][track["id"]] = track
            playlists[spotify_id] = playlist
            source = {"playlist_ids": [spotify_id]}
        else:
            raise ValueError(f"Unsupported Spotify type: {kind}")

        for track in resolved:
            track_id = track["id"]
            tracks[track_id] = track
            entry = track_sources.setdefault(track_id, {"album_ids": [], "playlist_ids": [], "track_ids": []})
            for key, values in source.items():
                for value in values:
                    if value not in entry[key]:
                        entry[key].append(value)

    return {"tracks": list(tracks.values()), "track_sources": track_sources, "playlists": list(playlists.values()), "cache": cache}
