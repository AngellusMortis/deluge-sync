"""Deluge Client."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from urllib.parse import urljoin

import httpx
from pydantic import BaseModel

ERROR_NOT_CONNECTED = "Not Connected"


class ClientError(Exception):
    """Unexpected Deluge error."""


class State(StrEnum):
    """State for torrent."""

    ALLOCATING = "Allocating"
    CHECKING = "Checking"
    DOWNLOADING = "Downloading"
    ERROR = "Error"
    PAUSED = "Paused"
    QUEUED = "Queued"
    SEEDING = "Seeding"


class Torrent(BaseModel):
    """Torrent model."""

    id: str
    tracker_host: str
    time_added: datetime
    state: State
    name: str
    label: str
    seeding_time: timedelta
    download_location: Path
    progress: float
    total_done: int
    total_wanted: int

    def __str__(self) -> str:
        """Torrent str."""

        return f"{self.name} - {self.state} - {self.label} - {self.tracker_host}"


@dataclass
class DelugeClient:
    """Deluge API wrapper."""

    host: str
    password: str
    timeout: int = 10
    host_header: str | None = None
    verify: bool = True

    _session: httpx.Client | None = None

    @property
    def json_api(self) -> str:
        """Deluge JSON API."""

        return urljoin(self.host, "json")

    @property
    def session(self) -> httpx.Client:
        """Get HTTP session."""

        if self._session is None:
            headers = None
            if self.host_header:
                headers = {"Host": self.host_header}
            self._session = httpx.Client(
                timeout=self.timeout, headers=headers, verify=self.verify
            )
            self.auth()

        return self._session

    def close(self) -> None:
        """Close session."""

        if self._session is not None:
            self._session.close()
            self._session = None

    def auth(self) -> None:
        """Authenticate with Deluge."""

        data = {"method": "auth.login", "params": [self.password], "id": "13"}

        response = self.session.post(self.json_api, json=data)
        response.raise_for_status()

    def get_torrents(
        self,
        *,
        state: State | None = None,
        labels: list[str] | None = None,
        exclude_labels: list[str] | None = None,
    ) -> dict[str, Torrent]:
        """Get list of torrent from Deluge."""

        excluded = set(exclude_labels) if exclude_labels else set()
        fields = [
            "name",
            "state",
            "time_added",
            "tracker_host",
            "seeding_time",
            "label",
            "download_location",
            "progress",
            "total_done",
            "total_wanted",
        ]
        query: dict[str, str | list[str]] = {}
        if state:
            query["state"] = state.value
        if labels:
            query["label"] = labels

        data = {
            "method": "web.update_ui",
            "params": [
                fields,
                query,
            ],
            "id": 22,
        }

        response = self.session.post(self.json_api, json=data)
        response.raise_for_status()
        json_data = response.json()

        if (result := json_data.get("result")) is None:
            raise ClientError(json_data["error"]["message"])

        if not result.get("connected"):
            raise ClientError(ERROR_NOT_CONNECTED)

        return_data = {}
        torrent_data = result.get("torrents", {})
        for key, values in torrent_data.items():
            if values["label"] in excluded:
                continue
            return_data[key] = Torrent(id=key, **values)

        return return_data

    def remove_torrent(self, torrent_id: str) -> None:
        """Remove torrent."""

        data = {
            "method": "core.remove_torrent",
            "params": [torrent_id, "true"],
            "id": "2030",
        }
        response = self.session.post(self.json_api, json=data)
        response.raise_for_status()

    def move_torrent(self, torrent_id: str, path: str) -> None:
        """Move torrent."""

        data = {
            "method": "core.move_storage",
            "params": [[torrent_id], path],
            "id": "112",
        }
        response = self.session.post(self.json_api, json=data)
        response.raise_for_status()

    def change_label_torrent(self, torrent_id: str, label: str) -> None:
        """Change label on torrent."""

        data = {
            "method": "label.set_torrent",
            "params": [torrent_id, label],
            "id": "9641",
        }
        response = self.session.post(self.json_api, json=data)
        response.raise_for_status()
