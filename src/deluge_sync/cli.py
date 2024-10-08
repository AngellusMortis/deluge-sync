"""Deluge Sync CLI."""

import ast
import datetime
import json
import os
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated

from cyclopts import App, CycloptsError, Parameter
from pydantic import BaseModel, ConfigDict
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

_GIBIBYTE = Decimal(1024) ** 3
COUNT_UNDER = (
    "Tracker ({tracker}) count ({count}) is less than {keep_count}, keeping all"
)
COUNT_OVER = (
    "Tracker ({tracker}) is over limit over limit ({keep_count}), {check} to check"
)
SIZE_OVER = (
    "Tracker ({tracker}) download size (at least {total_size:.2f} GiB) is over limit "
    "{keep_size:.2f} GiB, {to_add} to check"
)
SIZE_UNDER = (
    "Tracker ({tracker}) download size ({total_size:.2f} GiB) is less "
    "than {keep_size:.2f} GiB, keeping all"
)

FLAG_QUIET = Annotated[bool, Parameter(("-q", "--quiet"), env_var="DELUGE_SYNC_QUIET")]
FLAG_DRY = Annotated[
    bool, Parameter(("-d", "--dry-run"), env_var="DELUGE_SYNC_DRY_RUN")
]
FLAG_REMOVE = Annotated[bool, Parameter(("--remove"), env_var="DELUGE_SYNC_REMOVE")]
FLAG_MOVE = Annotated[bool, Parameter(("--move"), env_var="DELUGE_SYNC_MOVE")]
FLAG_RELABEL = Annotated[bool, Parameter(("--relabel"), env_var="DELUGE_SYNC_RELABEL")]
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

PARAM_SEED_BUFFER = Annotated[
    float,
    Parameter(
        ("--buffer-time"),
        env_var="DEFAULT_SYNC_SEED_BUFFER",
    ),
]

PARAM_PATH_MAP = Annotated[
    list[str] | None,
    Parameter(
        ("-m", "--path-map"),
        env_var="DELUGE_SYNC_PATH_MAP",
    ),
]

PARAM_LABEL_REMAP = Annotated[
    list[str] | None,
    Parameter(
        ("--label-remap"),
        env_var="DELUGE_SYNC_LABEL_REMAP",
    ),
]

PARAM_HOST_ALIAS = Annotated[
    list[str] | None,
    Parameter(
        ("--host-alias"),
        env_var="DELUGE_SYNC_HOST_ALIAS_MAP",
    ),
]

INVALID_FORMULA = "Invalid formula"
INVALID_RESULT = "Formula result must be a timedelta"
_AST_WHITELIST = (
    ast.Expression,
    ast.Call,
    ast.BinOp,
    ast.Attribute,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.operator,
    ast.keyword,
)


def _parse(formula: str) -> timedelta:
    tree = ast.parse(formula, mode="eval")
    valid = all(isinstance(node, _AST_WHITELIST) for node in ast.walk(tree))
    if not valid:
        raise CycloptsError(msg=INVALID_FORMULA)

    result = eval(  # noqa: S307
        compile(tree, filename="", mode="eval"),
        {"__builtins__": None, "datetime": datetime, "timedelta": timedelta},
        {},
    )

    if not isinstance(result, timedelta):
        raise CycloptsError(msg=INVALID_RESULT)

    return result


class TrackerRule(BaseModel):
    """Tracker rules."""

    model_config = ConfigDict(ser_json_timedelta="iso8601")

    host: str
    priority: int
    min_time: timedelta
    min_formula: str | None = None
    name_search: re.Pattern[str] | None = None
    keep_count: int | None = None
    keep_size: int | None = None

    def required_seed_time(self, torrent: Torrent, buffer: float) -> timedelta:
        """Return required seed time combining min_time and min_formula."""

        if self.min_formula is None:
            return self.min_time * buffer

        size = float(Decimal(torrent.total_wanted) / _GIBIBYTE)
        formula = self.min_formula.format(
            min=self.min_time,
            size=size,
            buffer=buffer,
        )

        return _parse(formula)


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
INVALID_PRIORITY = "Priority must be 1 or greater"
INVALID_KEEP = "Keep count and keep size are mutually exclusive"


def get_context() -> Context:
    """Get CLI Context."""

    if _STATE.context is None:
        raise CycloptsError(msg=NO_CONTEXT_ERROR)

    return _STATE.context


DEFAULT_SEED_BUFFER = 1.1

DEFAULT_RULES = [
    TrackerRule(
        host="avistaz.to",
        priority=10,
        min_time=timedelta(days=3),
        min_formula="({min!r}+(timedelta(hours=2)*{size!r})) * {buffer}",
    ),
    TrackerRule(
        host="blutopia.cc",
        priority=10,
        min_time=timedelta(days=10),
        keep_size=10440,
    ),
    TrackerRule(
        host="exoticaz.to",
        priority=10,
        min_time=timedelta(days=3),
        min_formula="({min!r}+(timedelta(hours=2)*{size!r})) * {buffer}",
    ),
    TrackerRule(
        host="flacsfor.me",
        priority=10,
        min_time=timedelta(days=7),
    ),
    TrackerRule(
        host="landof.tv",
        priority=1,
        min_time=timedelta(days=1),
        name_search=re.compile(r"(?i)S[0-9][0-9]E[0-9][0-9]"),
    ),
    TrackerRule(
        host="landof.tv",
        priority=10,
        min_time=timedelta(days=5),
    ),
    TrackerRule(
        host="myanonamouse.net",
        priority=10,
        min_time=timedelta(days=3),
    ),
    TrackerRule(
        host="opsfet.ch",
        priority=10,
        min_time=timedelta(days=7),
    ),
    TrackerRule(
        host="seedpool.org",
        priority=10,
        min_time=timedelta(days=10),
    ),
    TrackerRule(
        host="torrentbytes.net",
        priority=10,
        min_time=timedelta(days=3),
    ),
    TrackerRule(
        host="torrentleech.org",
        priority=10,
        min_time=timedelta(days=10),
        keep_count=100,
    ),
    TrackerRule(
        host="rptscene.xyz",
        priority=10,
        min_time=timedelta(days=1),
    ),
]

DEFAULT_HOST_ALIAS = ["tleechreload.org=torrentleech.org"]


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


def _compile_rules(ctx: Context, *, remove: bool) -> dict[str, list[TrackerRule]]:
    rules = _get_env_rules(ctx) or _get_default_rules(ctx)

    for host, tracker_rules in rules.items():
        sorted_rules = sorted(tracker_rules, key=lambda r: r.priority)
        if sorted_rules and sorted_rules[0].priority < 1:
            raise CycloptsError(msg=INVALID_PRIORITY)

        if len(sorted_rules) > 1:
            priority_zero_rule = sorted_rules[0].model_copy()
            for rule in sorted_rules:
                if rule.keep_count:
                    priority_zero_rule.keep_count = rule.keep_count
            if priority_zero_rule.keep_count or priority_zero_rule.keep_size:
                if priority_zero_rule.keep_count and priority_zero_rule.keep_size:
                    raise CycloptsError(msg=INVALID_KEEP)
                sorted_rules.insert(0, priority_zero_rule)

        rules[host] = sorted_rules

    console = Console()
    _print(
        console,
        f"Loaded rules for {len(rules)} trackers (remove: {remove})",
        quiet=ctx.quiet,
    )
    return rules


def _torrents_by_tracker(torrents: Iterable[Torrent]) -> dict[str, list[Torrent]]:
    torrents_by_tracker: dict[str, list[Torrent]] = {}
    for torrent in torrents:
        host_torrents = torrents_by_tracker.get(torrent.tracker_alias, [])
        host_torrents.append(torrent)
        torrents_by_tracker[torrent.tracker_alias] = host_torrents

    return torrents_by_tracker


def _get_under_size(
    torrents: list[Torrent], keep_size: int
) -> tuple[Decimal, list[Torrent] | None]:
    total_size = Decimal(0)
    to_add: list[Torrent] | None = None
    for index, torrent in enumerate(torrents):
        total_size += Decimal(torrent.total_wanted) / _GIBIBYTE
        if total_size > keep_size:
            to_add = torrents[index + 1 :]
            break

    return total_size, to_add


def _filter_out_keep(
    console: Console,
    ctx: Context,
    torrents: Iterable[Torrent],
    rules: dict[str, list[TrackerRule]],
) -> set[str]:
    torrents_by_tracker = _torrents_by_tracker(torrents)

    to_check: list[Torrent] = []
    for host, tracker_torrents in torrents_by_tracker.items():
        sorted_torrents = sorted(
            tracker_torrents, key=lambda x: x.total_wanted, reverse=True
        )
        tracker_rules = rules.get(host, [])
        if not tracker_rules:
            to_check += sorted_torrents
            continue

        rule_zero = tracker_rules[0]
        if not rule_zero.keep_count and not rule_zero.keep_size:
            to_check += sorted_torrents
            continue

        if rule_zero.keep_count:
            count = len(sorted_torrents)
            if count < rule_zero.keep_count:
                _print(
                    console,
                    COUNT_UNDER.format(
                        tracker=rule_zero.host,
                        count=count,
                        keep_count=rule_zero.keep_count,
                    ),
                    quiet=ctx.quiet,
                )
            else:
                _print(
                    console,
                    COUNT_OVER.format(
                        tracker=rule_zero.host,
                        check=count - rule_zero.keep_count,
                        keep_count=rule_zero.keep_count,
                    ),
                    quiet=ctx.quiet,
                )
                to_check += sorted_torrents[rule_zero.keep_count :]
        elif rule_zero.keep_size:
            total_size, to_add = _get_under_size(sorted_torrents, rule_zero.keep_size)
            if to_add:
                to_check += to_add
                _print(
                    console,
                    SIZE_OVER.format(
                        tracker=rule_zero.host,
                        total_size=total_size,
                        keep_size=rule_zero.keep_size,
                        to_add=len(to_add),
                    ),
                    quiet=ctx.quiet,
                )
            else:
                _print(
                    console,
                    SIZE_UNDER.format(
                        tracker=rule_zero.host,
                        total_size=total_size,
                        keep_size=rule_zero.keep_size,
                    ),
                    quiet=ctx.quiet,
                )

    return {t.id for t in to_check}


def _check_torrent(
    torrent: Torrent,
    rules: list[TrackerRule],
    default_seed_time: timedelta,
    buffer: float,
) -> bool:
    if not rules:
        return torrent.seeding_time > default_seed_time

    for rule in rules:
        # name does not match, skip rule
        if rule.name_search and not rule.name_search.search(torrent.name):
            continue

        if torrent.seeding_time > rule.required_seed_time(torrent, buffer):
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

    if tokens and tokens[0] == "rules":
        quiet = True
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
    _print(console, f"Getting list of torrents{extra}...", quiet=ctx.quiet)
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
            torrent.tracker_alias,
            torrent.tracker_status,
            torrent.time_added.isoformat(),
            str(torrent.seeding_time),
            str(torrent.download_location),
        )

    console.print(table)

    return 0


def _convert_to_dict(items: list[str]) -> dict[str, str]:
    if len(items) == 1:
        items = items[0].split(",")

    item_map = {}
    for item in items:
        key, value = item.split("=")
        item_map[key] = value

    return item_map


def _convert_to_dict_path(items: list[str]) -> dict[str, Path]:
    item_map = _convert_to_dict(items)
    path_map = {}
    for key, value in item_map.items():
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


def _remove_torrents(
    ctx: Context, torrents: dict[str, Torrent], to_remove: list[str], *, dry_run: bool
) -> None:
    console = Console()

    _print(console, f"Torrents to delete: {len(to_remove)}", quiet=ctx.quiet)
    for tid in to_remove:
        escaped = str(torrents[tid]).replace("{", "{{").replace("}", "}}")
        _print(
            console,
            f"\tRemoving torrent{{dry}}: {escaped}",
            quiet=ctx.quiet,
            dry_run=dry_run,
        )

        try:
            if not dry_run:
                ctx.client.remove_torrent(tid)
        except Exception:  # noqa: BLE001
            console.print("[red]Failed to remove torrent[/red]")


@app.command()
def rules() -> int:
    """
    Dump sync rules.

    Returns json of the currently configured rules for the sync command.
    """

    ctx = get_context()
    console = Console()
    dump_rules = []
    rules = _get_env_rules(ctx) or _get_default_rules(ctx)
    for tracker_rules in rules.values():
        for tracker_rule in tracker_rules:
            rule = json.loads(tracker_rule.model_dump_json())
            if rule["min_formula"] is None:
                del rule["min_formula"]
            if rule["name_search"] is None:
                del rule["name_search"]
            if rule["keep_count"] is None:
                del rule["keep_count"]
            if rule["keep_size"] is None:
                del rule["keep_size"]
            dump_rules.append(rule)

    console.print_json(json.dumps(dump_rules))
    return 0


@app.command()
def sync(  # noqa: PLR0913
    *,
    labels: PARAM_LABELS = None,
    exclude_labels: PARAM_EXCLUDE_LABELS = None,
    default_seed_time: PARAM_DEFAULT_SEED = timedelta(minutes=90),
    seed_buffer: PARAM_SEED_BUFFER = DEFAULT_SEED_BUFFER,
    path_list: PARAM_PATH_MAP = None,
    label_remap_list: PARAM_LABEL_REMAP = None,
    host_aliases_list: PARAM_HOST_ALIAS = None,
    remove: FLAG_REMOVE = True,
    move: FLAG_MOVE = True,
    relabel: FLAG_RELABEL = True,
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

    seed_buffer: float
        Multiplier to apply to min_time for tracker rules to add extra buffer time.

    path_list: list[str]
        Map of tracker (key) to download folder (value). Will move to path.

    label_remap_list: list[str]
        Map of tracker (key) to label (value). Will change label if has tracker.

    host_aliases_list: list[str]
        Map of tracker alias (key) to tracker (value).

    remove: bool
        Automatically remove torrents past the min seed time.

    move: bool
        Automatically move torrents to correct paths.

    relabel: bool
        Automatically relabel torrents for specific trackers to new label.

    dry_run: bool
        Do not actually delete any torrents.

    """

    if labels and len(labels) == 1:
        labels = labels[0].split(",")
    if exclude_labels and len(exclude_labels) == 1:
        exclude_labels = exclude_labels[0].split(",")

    ctx = get_context()
    console = Console()
    rules = _compile_rules(ctx, remove=remove)
    path_map = _convert_to_dict_path(path_list or [])
    label_remap = _convert_to_dict(label_remap_list or [])
    host_aliases = _convert_to_dict(host_aliases_list or DEFAULT_HOST_ALIAS)
    _print(
        console,
        f"Loaded path maps for {len(path_map)} trackers (move: {move})",
        quiet=ctx.quiet,
    )
    _print(
        console,
        f"Loaded alias maps for {len(host_aliases)} trackers",
        quiet=ctx.quiet,
    )

    _print_label_text(console, ctx, labels, exclude_labels)
    torrents = ctx.client.get_torrents(
        state=State.SEEDING,
        labels=labels,
        exclude_labels=exclude_labels,
        aliases=host_aliases,
    )
    if not torrents:
        _print(console, "No torrents to process", quiet=ctx.quiet)
        return 0

    _print(console, f"{len(torrents)} torrent(s) to process", quiet=ctx.quiet)

    can_remove = _filter_out_keep(console, ctx, torrents.values(), rules)
    to_remove = []
    for torrent in torrents.values():
        changed_label = False
        escaped = str(torrent).replace("{", "{{").replace("}", "}}")

        if (
            relabel
            and (new_label := label_remap.get(torrent.tracker_alias)) is not None
            and torrent.label != new_label
        ):
            _print(
                console,
                f"\tChanging label of torrent to {new_label}{{dry}}: {escaped}",
                quiet=ctx.quiet,
                dry_run=dry_run,
            )
            if not dry_run:
                ctx.client.change_label_torrent(torrent.id, new_label)
            torrent.label = new_label
            changed_label = True

        if (
            remove
            and not changed_label
            and torrent.id in can_remove
            and _check_torrent(
                torrent,
                rules.get(torrent.tracker_alias, []),
                default_seed_time,
                seed_buffer,
            )
        ):
            to_remove.append(torrent.id)
            continue

        expected_path = path_map.get(torrent.tracker_alias)
        if move and expected_path and torrent.download_location != expected_path:
            _print(
                console,
                f"\tMoving torrent{{dry}}: {escaped}",
                quiet=ctx.quiet,
                dry_run=dry_run,
            )
            if not dry_run:
                ctx.client.move_torrent(torrent.id, str(expected_path))

    _remove_torrents(ctx, torrents, to_remove, dry_run=dry_run)
    return 0
