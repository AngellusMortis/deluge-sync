"""Deluge Sync CLI entrypoint."""

from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[assignment]

from deluge_sync.cli import app


def _main() -> None:
    if load_dotenv is not None:
        env_file = Path().cwd() / ".env"
        if env_file.exists():
            load_dotenv(dotenv_path=env_file)
        else:
            load_dotenv()
    app.meta()


if __name__ == "__main__":
    _main()
