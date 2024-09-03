"""Deluge Sync CLI."""

import re
import sys
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from cyclopts import App, CycloptsError, Parameter
from rich.console import Console
from rich.table import Table

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
PARAM_LABELS = Annotated[
    list[str] | None,
    Parameter(
        ("-l", "--label"),
        env_var="DELUGE_SYNC_LABELS",
    ),
]
PARAM_EXCLUDE_LABELS = Annotated[
    list[str] | None,
    Parameter(
        ("-e", "--exclude-label"),
        env_var="DELUGE_SYNC_EXCLUDE_LABELS",
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
    name_search: re.Pattern[str] | None = None


@dataclass
class Context:
    """CLI Context."""

    client: DelugeClient
    quiet: bool


@dataclass
class _State:
    """CLI state."""

    context: Context | None = None


_STATE = _State()
NO_CONTEXT_ERROR = "No CLI context"


def get_context() -> Context:
    """Get CLI Context."""

    if _STATE.context is None:
        raise CycloptsError(msg=NO_CONTEXT_ERROR)

    return _STATE.context


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
    rules: dict[str, list[TrackerRule]] = {}
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


@app.meta.default
def main(
    *tokens: Annotated[str, Parameter(show=False, allow_leading_hyphen=True)],
    deluge_url: PARAM_URL,
    deluge_password: PARAM_PASSWORD,
    quiet: FLAG_QUIET = False,
) -> None:
    """
    Deluge entrypoint.

    Parameters
    ----------
    tokens: str
        CLI args.

    deluge_url: str
        Deluge Web base URL.

    deluge_password: str
        Deluge Web password.

    quiet: bool
        Surpress all output.

    """

    client = DelugeClient(host=deluge_url, password=deluge_password)
    console = Console()

    try:
        if not quiet:
            console.print(f"Logging in to deluge ({deluge_url} )")
        client.auth()
    except Exception:  # noqa: BLE001
        client.close()
        console.print_exception()
        sys.exit(1)

    _STATE.context = Context(client=client, quiet=quiet)
    try:
        sys.exit(app(tokens))
    finally:
        client.close()


@app.command()
def query(
    *,
    labels: PARAM_LABELS = None,
    exclude_labels: PARAM_EXCLUDE_LABELS = None,
) -> int:
    """
    Deluge List Torrents.

    Parameters
    ----------
    labels: list[str]
        labels for filtering.

    exclude_labels: list[str]
        labels for filtering.

    """

    ctx = get_context()

    console = Console()

    if not ctx.quiet:
        extra = ""
        if labels:
            extra = f" (label={','.join(labels)})"
        console.print(f"Getting list of seeding torrents{extra}...")

    torrents = ctx.client.get_torrents(
        state=State.SEEDING, labels=labels, exclude_labels=exclude_labels
    )
    if not torrents:
        console.print("No torrents found")
        return 1

    table = Table(title="Torrents", row_styles=["dim", ""])
    table.add_column("ID")
    table.add_column("Name", style="cyan")
    table.add_column("State")
    table.add_column("Label", style="green")
    table.add_column("Tracker", style="yellow")
    table.add_column("Added")
    table.add_column("Wanted")
    table.add_column("Seeding Time")

    for torrent in torrents.values():
        table.add_row(
            torrent.id,
            torrent.name,
            torrent.state,
            torrent.label,
            torrent.tracker_host,
            torrent.time_added.isoformat(),
            str(torrent.total_wanted),
            str(torrent.seeding_time),
        )

    console.print(table)

    return 0


@app.command()
def sync(
    *,
    labels: PARAM_LABELS = None,
    exclude_labels: PARAM_EXCLUDE_LABELS = None,
    default_seed_time: PARAM_DEFAULT_SEED = timedelta(minutes=90),
    dry_run: FLAG_DRY = False,
) -> int:
    """
    Deluge Sync.

    Parameters
    ----------
    labels: list[str]
        Seeding label for filtering.

    exclude_labels: list[str]
        labels for filtering.

    default_seed_time: timedelta
        Default seedtime for torrents without rules.

    dry_run: bool
        Do not actually delete any torrents.

    """

    ctx = get_context()

    console = Console()
    rules = _compile_rules()

    if not ctx.quiet:
        extra = ""
        if labels:
            extra = f" (label={','.join(labels)})"
        console.print(f"Getting list of seeding torrents{extra}...")

    torrents = ctx.client.get_torrents(
        state=State.SEEDING, labels=labels, exclude_labels=exclude_labels
    )
    to_remove = [
        t.id
        for t in torrents.values()
        if _check_torrent(t, rules.get(t.tracker_host, []), default_seed_time)
    ]

    if not ctx.quiet:
        console.print(f"Torrents to delete: {len(to_remove)}")
    for tid in to_remove:
        if not ctx.quiet:
            extra = ""
            if dry_run:
                extra = " (dry)"
            console.print(f"\tRemoving torrent{extra}: {torrents[tid]}")
        try:
            if not dry_run:
                ctx.client.remove_torrent(tid)
        except Exception:  # noqa: BLE001
            console.print("[red]Failed to remove torrent[/red]")

    return 0
