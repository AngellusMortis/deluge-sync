"""Deluge Sync CLI."""

import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from cyclopts import App, Parameter
from rich.console import Console

from deluge_sync.client import DelugeClient, State, Torrent

app = App(
    help="""
    Deluge Sync CLI.

    Python app to manage Deluge.
""",
)

FLAG_QUIET = Annotated[bool, Parameter(("-q", "--quiet"), env_var="DELUGE_SYNC_QUIET")]
FLAG_DRY = Annotated[
    bool, Parameter(("-d", "--dry-run"), env_var="DELUGE_SYNC_DRY_RUN")
]
PARAM_URL = Annotated[
    str,
    Parameter(
        ("-u", "--deluge-url"),
        env_var="DELUGE_SYNC_URL",
    ),
]
PARAM_PASSWORD = Annotated[
    str,
    Parameter(
        ("-p", "--deluge-password"),
        env_var="DELUGE_SYNC_PASSWORD",
    ),
]
PARAM_SEED_LABEL = Annotated[
    str | None,
    Parameter(
        ("-l", "--seed-label"),
        env_var="DELUGE_SYNC_SEED_LABEL",
    ),
]

PARAM_DEFAULT_SEED = Annotated[
    timedelta,
    Parameter(
        ("-t", "--seed-time"),
        env_var="DELUGE_SYNC_SEED_TIME",
    ),
]


@dataclass
class TrackerRule:
    """Tracker rules."""

    host: str
    priority: int
    min_time: timedelta
    name_search: re.Pattern | None = None


RULES = [
    TrackerRule(
        host="landof.tv",
        priority=1,
        min_time=timedelta(days=1, hours=2),
        name_search=re.compile(r"(?i)S[0-9][0-9]E[0-9][0-9]"),
    ),
    TrackerRule(
        host="landof.tv",
        priority=10,
        min_time=timedelta(days=5, hours=12),
    ),
    TrackerRule(
        host="torrentbytes.net",
        priority=10,
        min_time=timedelta(days=3),
    ),
    TrackerRule(
        host="torrentbytes.net",
        priority=10,
        min_time=timedelta(days=3),
    ),
    TrackerRule(
        host="tleechreload.org",
        priority=10,
        min_time=timedelta(days=10, hours=12),
    ),
    TrackerRule(
        host="torrentleech.org",
        priority=10,
        min_time=timedelta(days=10, hours=12),
    ),
    TrackerRule(
        host="rptscene.xyz",
        priority=10,
        min_time=timedelta(days=1, hours=2),
    ),
]


def _compile_rules() -> dict[str, list[TrackerRule]]:
    rules = {}
    for rule in RULES:
        tracker_rules = rules.get(rule.host, [])
        tracker_rules.append(rule)
        rules[rule.host] = tracker_rules

    for host, tracker_rules in rules.items():
        sorted_rules = sorted(tracker_rules, key=lambda r: r.priority)
        rules[host] = sorted_rules

    return rules


def _check_torrent(
    torrent: Torrent, rules: list[TrackerRule], default_seed_time: timedelta
) -> bool:
    if not rules:
        return torrent.seeding_time > default_seed_time

    for rule in rules:
        # name does not match, skip rule
        if rule.name_search and not rule.name_search.search(torrent.name):
            continue

        if torrent.seeding_time > rule.min_time:
            return True

    return False


@app.default()
def main(
    *,
    deluge_url: PARAM_URL,
    deluge_password: PARAM_PASSWORD,
    seed_label: PARAM_SEED_LABEL = None,
    default_seed_time: PARAM_DEFAULT_SEED = timedelta(seconds=5400),
    quiet: FLAG_QUIET = False,
    dry_run: FLAG_DRY = False,
) -> int:
    """
    Deluge Sync.

    Parameters
    ----------
    deluge_url: str
        Deluge Web base URL.

    deluge_password: str
        Deluge Web password.

    seed_label: str
        Seeding label for filtering.

    quiet: bool
        Surpress all output.

    default_seed_time: timedelta
        Default seedtime for torrents without rules.

    """

    client = DelugeClient(host=deluge_url, password=deluge_password)
    console = Console()

    rules = _compile_rules()

    try:
        if not quiet:
            console.print("Logging in")
        client.auth()

        if not quiet:
            extra = ""
            if seed_label:
                extra = f" (label={seed_label})"
            console.print(f"Getting list of seeding torrents{extra}...")

        torrents = client.get_torrents(state=State.SEEDING, label=seed_label)

        to_remove = [
            t.id
            for t in torrents.values()
            if _check_torrent(t, rules.get(t.tracker_host, []), default_seed_time)
        ]

        for tid in to_remove:
            if not quiet:
                extra = ""
                if dry_run:
                    extra = " (dry)"
                console.print(f"Removing torrent{extra}: {torrents[tid]}")
            try:
                if not dry_run:
                    client.remove_torrent(tid)
            except Exception:  # noqa: BLE001
                console.print("[red]Failed to remove torrent[/red]")
    except Exception:  # noqa: BLE001
        client.close()
        console.print_exception()
        return 1

    client.close()
    return 0
