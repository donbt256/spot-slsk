import time
import uuid

import requests


class SoulseekClient:
    def __init__(
        self,
        base_url="http://127.0.0.1:5030",
        api_key=None,
    ):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

        if api_key:
            self.session.headers.update({
                "X-API-Key": api_key,
            })

        self.session.headers.update({
            "Content-Type": "application/json",
        })

    def _url(self, path):
        return f"{self.base_url}{path}"

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query,
        timeout_ms=15000,
        file_limit=10000,
        response_limit=100,
    ):
        search_id = str(uuid.uuid4())

        payload = {
            "id": search_id,
            "searchText": query,
            "searchTimeout": timeout_ms,
            "fileLimit": file_limit,
            "responseLimit": response_limit,
            "filterResponses": True,
            "maximumPeerQueueLength": 100,
            "minimumResponseFileCount": 1,
        }

        response = self.session.post(
            self._url("/api/v0/searches"),
            json=payload,
            timeout=30,
        )

        response.raise_for_status()

        return search_id

    def get_search(
        self,
        search_id,
        include_responses=True,
    ):
        response = self.session.get(
            self._url(
                f"/api/v0/searches/{search_id}"
            ),
            params={
                "includeResponses": str(
                    include_responses
                ).lower()
            },
            timeout=30,
        )

        response.raise_for_status()

        return response.json()

    def wait_for_search(
        self,
        search_id,
        timeout_seconds=30,
    ):
        deadline = time.monotonic() + timeout_seconds

        while True:
            data = self.get_search(
                search_id,
                include_responses=True,
            )

            if (
                data.get("isComplete")
                or data.get("state") in {
                    "Completed",
                    "Complete",
                    "Cancelled",
                    "Errored",
                    "Failed",
                }
            ):
                return data

            if time.monotonic() >= deadline:
                return data

            time.sleep(1)

    def delete_search(self, search_id):
        response = self.session.delete(
            self._url(
                f"/api/v0/searches/{search_id}"
            ),
            timeout=30,
        )

        if response.status_code not in {
            200,
            204,
            404,
        }:
            response.raise_for_status()

    # ------------------------------------------------------------------
    # Downloads
    # ------------------------------------------------------------------

    def enqueue_download(
        self,
        username,
        filename,
        size=None,
    ):
        payload = [
            {
                "filename": filename,
                **(
                    {"size": size}
                    if size is not None
                    else {}
                ),
            }
        ]

        response = self.session.post(
            self._url(
                f"/api/v0/transfers/downloads/"
                f"{username}"
            ),
            json=payload,
            timeout=30,
        )

        response.raise_for_status()

        if response.content:
            return response.json()

        return None

    def enqueue_downloads(
        self,
        username,
        files,
    ):
        payload = []

        for file_info in files:
            item = {
                "filename": file_info["filename"],
            }

            if file_info.get("size") is not None:
                item["size"] = file_info["size"]

            payload.append(item)

        if not payload:
            return None

        response = self.session.post(
            self._url(
                f"/api/v0/transfers/downloads/"
                f"{username}"
            ),
            json=payload,
            timeout=30,
        )

        response.raise_for_status()

        if response.content:
            return response.json()

        return None

    def get_downloads(
        self,
        include_removed=False,
    ):
        response = self.session.get(
            self._url(
                "/api/v0/transfers/downloads"
            ),
            params={
                "includeRemoved": str(
                    include_removed
                ).lower()
            },
            timeout=30,
        )

        response.raise_for_status()

        return response.json()


def flatten_responses(search_data):
    candidates = []

    for response in search_data.get(
        "responses",
        [],
    ):
        username = response.get("username")

        peer_info = {
            "username": username,
            "has_free_upload_slot": response.get(
                "hasFreeUploadSlot"
            ),
            "upload_speed": response.get(
                "uploadSpeed"
            ),
            "queue_length": response.get(
                "queueLength"
            ),
        }

        for file_info in response.get(
            "files",
            [],
        ):
            candidates.append({
                "username": username,
                "filename": file_info.get(
                    "filename"
                ),
                "size": file_info.get("size"),
                "extension": file_info.get(
                    "extension"
                ),
                "peer": peer_info,
            })

    return candidates