"""Deluge Sync CLI."""

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Annotated

from cyclopts import App, CycloptsError, Parameter
from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

from deluge_sync.client import DelugeClient, State, Torrent
from deluge_sync.utils import sizeof_fmt

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
PARAM_TIMEOUT = Annotated[
    int,
    Parameter(
        ("--deluge-timeout"),
        env_var="DELUGE_SYNC_TIMEOUT",
    ),
]
PARAM_RETRIES = Annotated[
    int,
    Parameter(
        ("--deluge-retries"),
        env_var="DELUGE_SYNC_RETRIES",
    ),
]
PARAM_HOST = Annotated[
    str | None,
    Parameter(
        ("--deluge-host"),
        env_var="DELUGE_SYNC_HOST",
    ),
]
PARAM_VERIFY = Annotated[
    bool,
    Parameter(
        ("--deluge-verify"),
        env_var="DELUGE_SYNC_VERIFY",
    ),
]
PARAM_STATE = Annotated[
    State | None,
    Parameter(
        ("-s", "--state"),
        env_var="DELUGE_SYNC_STATE",
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

PARAM_PATH_MAP = Annotated[
    list[str] | None,
    Parameter(
        ("-m", "--path-map"),
        env_var="DELUGE_SYNC_PATH_MAP",
    ),
]


class TrackerRule(BaseModel):
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


DEFAULT_RULES = [
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
        host="myanonamouse.net",
        priority=10,
        min_time=timedelta(days=3, hours=12),
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


def _get_default_rules(ctx: Context) -> dict[str, list[TrackerRule]]:
    rules: dict[str, list[TrackerRule]] = {}

    console = Console()
    _print(console, "Loading default rules...", quiet=ctx.quiet)
    for rule in DEFAULT_RULES:
        tracker_rules = rules.get(rule.host, [])
        tracker_rules.append(rule)
        rules[rule.host] = tracker_rules

    return rules


def _get_env_rules(ctx: Context) -> dict[str, list[TrackerRule]] | None:
    env_rules = os.environ.get("DELUGE_SYNC_RULES")
    if not env_rules:
        return None

    console = Console()
    _print(console, "Loading rules from ENV...", quiet=ctx.quiet)
    rules: dict[str, list[TrackerRule]] = {}
    for rule_json in json.loads(env_rules):
        rule = TrackerRule(**rule_json)
        tracker_rules = rules.get(rule.host, [])
        tracker_rules.append(rule)
        rules[rule.host] = tracker_rules

    return rules


def _compile_rules(ctx: Context) -> dict[str, list[TrackerRule]]:
    rules = _get_env_rules(ctx) or _get_default_rules(ctx)

    for host, tracker_rules in rules.items():
        sorted_rules = sorted(tracker_rules, key=lambda r: r.priority)
        rules[host] = sorted_rules

    console = Console()
    _print(console, f"Loaded rules for {len(rules)} trackers", quiet=ctx.quiet)
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
def main(  # noqa: PLR0913
    *tokens: Annotated[str, Parameter(show=False, allow_leading_hyphen=True)],
    deluge_url: PARAM_URL,
    deluge_password: PARAM_PASSWORD,
    deluge_timeout: PARAM_TIMEOUT = 10,
    deluge_retries: PARAM_RETRIES = 3,
    deluge_host: PARAM_HOST = None,
    deluge_verify: PARAM_VERIFY = True,
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

    deluge_timeout: int
        Deluge connect timeout.

    deluge_retries: int
        Deluge auth retry count.

    deluge_host: str
        Deluge host for host header.

    deluge_verify: bool
        Verify SSL cert for HTTPS requests.

    quiet: bool
        Surpress most output.

    """

    client = DelugeClient(
        host=deluge_url,
        password=deluge_password,
        timeout=deluge_timeout,
        host_header=deluge_host,
        verify=deluge_verify,
    )
    console = Console(soft_wrap=False)

    try:
        _print(
            console,
            (
                f"Connecting to deluge ({deluge_url} "
                f"timeout={deluge_timeout} retries={deluge_retries} "
                f"host={deluge_host} verify={deluge_verify})"
            ),
            quiet=quiet,
        )
        client.connect()

        _print(console, "Logging to deluge", quiet=quiet)
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
    state: PARAM_STATE = None,
    labels: PARAM_LABELS = None,
    exclude_labels: PARAM_EXCLUDE_LABELS = None,
) -> int:
    """
    Deluge List Torrents.

    Parameters
    ----------
    state: State | None
        torrent state to filter for.

    labels: list[str]
        labels for filtering.

    exclude_labels: list[str]
        labels for filtering.

    """

    ctx = get_context()

    console = Console()

    extra = ""
    if labels:
        extra = f" (label={','.join(labels)})"
    _print(console, f"Getting list of seeding torrents{extra}...", quiet=ctx.quiet)
    torrents = ctx.client.get_torrents(
        state=state, labels=labels, exclude_labels=exclude_labels
    )
    if not torrents:
        console.print("No torrents found")
        return 1

    table = Table(title="Torrents", row_styles=["", "dim"])
    table.add_column("ID")
    table.add_column("Name", style="cyan")
    table.add_column("State")
    table.add_column("Progress")
    table.add_column("Label", style="green")
    table.add_column("Tracker", style="yellow")
    table.add_column("Tracker Status")
    table.add_column("Added")
    table.add_column("Seeding Time")
    table.add_column("Path")

    for torrent in torrents.values():
        done = sizeof_fmt(torrent.total_done)
        wanted = sizeof_fmt(torrent.total_wanted)

        table.add_row(
            torrent.id,
            torrent.name,
            torrent.state,
            f"{done} / {wanted} {torrent.progress:.0f}%",
            torrent.label,
            torrent.tracker_host,
            torrent.tracker_status,
            torrent.time_added.isoformat(),
            str(torrent.seeding_time),
            str(torrent.download_location),
        )

    console.print(table)

    return 0


def _convert_to_dict(path_list: list[str]) -> dict[str, Path]:
    if len(path_list) == 1:
        path_list = path_list[0].split(",")

    path_map = {}
    for item in path_list:
        key, value = item.split("=")
        path_map[key] = Path(value)

    return path_map


def _print(console: Console, msg: str, *, quiet: bool, dry_run: bool = False) -> None:
    if quiet:
        return

    dry = ""
    if dry_run:
        dry = " (dry)"
    msg = msg.format(dry=dry)

    console.print(msg)


def _print_label_text(
    console: Console, ctx: Context, labels: list[str] | None, exclude: list[str] | None
) -> None:
    extra = ""
    if labels:
        extra = f"label={','.join(labels)}"
    if exclude:
        if extra:
            extra += ","
        extra += f"exclude={','.join(exclude)}"

    if extra:
        extra = f" ({extra})"
    _print(console, f"Getting list of seeding torrents{extra}...", quiet=ctx.quiet)


@app.command()
def sync(
    *,
    labels: PARAM_LABELS = None,
    exclude_labels: PARAM_EXCLUDE_LABELS = None,
    default_seed_time: PARAM_DEFAULT_SEED = timedelta(minutes=90),
    path_list: PARAM_PATH_MAP = None,
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

    path_list: list[str]
        Map of tracker (key) to download folder (value). Will move to path.

    dry_run: bool
        Do not actually delete any torrents.

    """

    if labels and len(labels) == 1:
        labels = labels[0].split(",")
    if exclude_labels and len(exclude_labels) == 1:
        exclude_labels = exclude_labels[0].split(",")

    ctx = get_context()
    console = Console()
    rules = _compile_rules(ctx)
    path_map = _convert_to_dict(path_list or [])
    _print(console, f"Loaded path maps for {len(path_map)} trackers", quiet=ctx.quiet)

    _print_label_text(console, ctx, labels, exclude_labels)
    torrents = ctx.client.get_torrents(
        state=State.SEEDING, labels=labels, exclude_labels=exclude_labels
    )
    to_remove = []
    for torrent in torrents.values():
        if _check_torrent(
            torrent, rules.get(torrent.tracker_host, []), default_seed_time
        ):
            to_remove.append(torrent.id)
            continue

        expected_path = path_map.get(torrent.tracker_host)
        if expected_path and torrent.download_location != expected_path:
            _print(
                console,
                f"\tMoving torrent{{dry}}: {torrent}",
                quiet=ctx.quiet,
                dry_run=dry_run,
            )
            if not dry_run:
                ctx.client.move_torrent(torrent.id, str(expected_path))

    _print(console, f"Torrents to delete: {len(to_remove)}", quiet=ctx.quiet)
    for tid in to_remove:
        _print(
            console,
            f"\tRemoving torrent{{dry}}: {torrents[tid]}",
            quiet=ctx.quiet,
            dry_run=dry_run,
        )

        try:
            if not dry_run:
                ctx.client.remove_torrent(tid)
        except Exception:  # noqa: BLE001
            console.print("[red]Failed to remove torrent[/red]")

    return 0
