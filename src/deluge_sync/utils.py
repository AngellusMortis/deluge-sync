"""Deluge utils."""


def sizeof_fmt(num: float, suffix: str = "B") -> str:
    """Format bytes to human readable string."""

    for unit in ("", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei", "Zi"):
        if abs(num) < 512.0:  # noqa: PLR2004
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"
