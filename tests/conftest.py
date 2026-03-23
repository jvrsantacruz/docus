"""
Shared fixtures for docus tests.

The binary under test is controlled by the DOCUS_BIN environment variable:

    DOCUS_BIN=/usr/local/bin/docus pytest tests/test_functional.py
    DOCUS_BIN="python /path/to/other/docus.py" pytest tests/test_functional.py

When unset, defaults to running the local docus.py via the current interpreter.
"""

import json
import os
import stat
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Binary under test
# ---------------------------------------------------------------------------

_DEFAULT_CMD = [sys.executable, str(Path(__file__).parent.parent / "docus.py")]
DOCUS_CMD: list[str] = os.environ.get("DOCUS_BIN", "").split() or _DEFAULT_CMD


@pytest.fixture(scope="session")
def docus_bin() -> list[str]:
    """Command list to invoke docus. Override with DOCUS_BIN env var."""
    return DOCUS_CMD


# ---------------------------------------------------------------------------
# Fake CLI factory
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_cli_factory(tmp_path):
    """
    Returns a factory that writes a minimal fake CLI script to tmp_path.

    Usage::

        cli = fake_cli_factory({
            (): "root --help text",
            ("sub",): "sub --help text",
            ("sub", "deep"): "deep --help text",
        })
        # cli is the absolute path string to an executable script named "fakecli"

    The script responds to ``--help`` / ``-h`` by printing the mapped text and
    exiting 0.  Unknown command paths print nothing and exit 0 (matching most
    real CLIs).  The name defaults to "fakecli".
    """
    def _make(commands: dict[tuple[str, ...], str], name: str = "fakecli") -> str:
        mapping = {" ".join(k).strip(): v for k, v in commands.items()}
        script = tmp_path / name
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import sys, json\n"
            f"HELP = {json.dumps(mapping)}\n"
            'args = " ".join(a for a in sys.argv[1:] if a not in ("--help", "-h"))\n'
            'print(HELP.get(args.strip(), ""), end="")\n'
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return str(script)

    return _make


@pytest.fixture
def fake_cli(fake_cli_factory):
    """A standard fake CLI with two subcommands: alpha and beta."""
    return fake_cli_factory({
        (): (
            "fakecli — a fake tool\n\n"
            "Available Commands:\n"
            "  alpha  Alpha subcommand\n"
            "  beta   Beta subcommand\n"
        ),
        ("alpha",): "Usage: fakecli alpha\n\nThe alpha subcommand.\n",
        ("beta",):  "Usage: fakecli beta\n\nThe beta subcommand.\n",
    })
