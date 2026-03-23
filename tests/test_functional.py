"""
Functional tests — invoke the docus binary as a subprocess and inspect its
public contract: exit codes, stdout/stderr content, and generated files.

These tests never import docus internals.  They can be run against any
compliant implementation by setting DOCUS_BIN:

    DOCUS_BIN=/usr/local/bin/docus         pytest tests/test_functional.py -v
    DOCUS_BIN="python /other/docus.py"     pytest tests/test_functional.py -v
"""

import os
import subprocess

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GLOBAL_FLAGS = (
    "Global Flags:\n"
    "  --config PATH   Path to config file (default $HOME/.config)\n"
    "  --debug         Enable debug output to stderr\n"
    "  --verbose       Verbose logging (implies --debug)\n"
    "  --timeout INT   Timeout in seconds (default 30)\n"
)


def _run(docus_bin, args, cwd=None, env=None):
    return subprocess.run(
        [*docus_bin, *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
    )


# ---------------------------------------------------------------------------
# Output structure
# ---------------------------------------------------------------------------

def test_generates_heading_and_index(docus_bin, fake_cli, tmp_path):
    result = _run(docus_bin, ["--output", str(tmp_path / "out.md"), fake_cli])
    assert result.returncode == 0
    content = (tmp_path / "out.md").read_text()
    assert "fakecli" in content
    assert "## Index" in content


def test_index_contains_all_discovered_commands(docus_bin, fake_cli):
    result = _run(docus_bin, ["--stdout", fake_cli])
    assert result.returncode == 0
    assert "alpha" in result.stdout
    assert "beta" in result.stdout


def test_each_command_has_its_own_section(docus_bin, fake_cli):
    result = _run(docus_bin, ["--stdout", fake_cli])
    assert result.returncode == 0
    assert "alpha subcommand" in result.stdout.lower()
    assert "beta subcommand" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Output modes
# ---------------------------------------------------------------------------

def test_stdout_flag_writes_markdown_to_stdout(docus_bin, fake_cli):
    result = _run(docus_bin, ["--stdout", fake_cli])
    assert result.returncode == 0
    assert len(result.stdout) > 0
    assert "fakecli" in result.stdout
    assert result.stderr != ""  # progress written to stderr


def test_stdout_flag_writes_no_file(docus_bin, fake_cli, tmp_path):
    _run(docus_bin, ["--stdout", fake_cli], cwd=tmp_path)
    assert not any(p.suffix == ".md" for p in tmp_path.iterdir())


def test_output_flag_writes_to_specified_path(docus_bin, fake_cli, tmp_path):
    out = tmp_path / "custom.md"
    result = _run(docus_bin, ["--output", str(out), fake_cli])
    assert result.returncode == 0
    assert out.exists()


def test_default_output_filename_derived_from_command(docus_bin, fake_cli_factory, tmp_path):
    fake_cli_factory({(): "root help\n"}, name="fakecli")
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}"}
    result = _run(docus_bin, ["fakecli"], cwd=tmp_path, env=env)
    assert result.returncode == 0
    assert (tmp_path / "fakecli.md").exists()


def test_multi_word_command_filename_uses_dashes(docus_bin, fake_cli_factory, tmp_path):
    fake_cli_factory({
        (): "root help\n\nAvailable Commands:\n  sub  Sub\n",
        ("sub",): "sub help\n",
    }, name="mycli")
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}"}
    result = _run(docus_bin, ["mycli", "sub"], cwd=tmp_path, env=env)
    assert result.returncode == 0
    assert (tmp_path / "mycli-sub.md").exists()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_exit_nonzero_on_missing_command(docus_bin):
    result = _run(docus_bin, ["--stdout", "nonexistent_command_xyz_docus"])
    assert result.returncode != 0


def test_no_output_file_created_on_error(docus_bin, tmp_path):
    _run(docus_bin, ["nonexistent_command_xyz_docus"], cwd=tmp_path)
    assert not any(p.suffix == ".md" for p in tmp_path.iterdir())


# ---------------------------------------------------------------------------
# Subcommand entry point
# ---------------------------------------------------------------------------

def test_subcommand_entry_point_roots_at_given_command(docus_bin, fake_cli_factory, tmp_path):
    fake_cli_factory({
        (): "root\n\nAvailable Commands:\n  config  Config\n",
        ("config",): "config help\n\nAvailable Commands:\n  get  Get\n  set  Set\n",
        ("config", "get"): "get a value\n",
        ("config", "set"): "set a value\n",
    }, name="mycli")
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}"}
    result = _run(docus_bin, ["--stdout", "mycli", "config"], cwd=tmp_path, env=env)
    assert result.returncode == 0
    assert "config" in result.stdout
    assert "get" in result.stdout
    assert "set" in result.stdout


def test_subcommand_entry_point_excludes_parent(docus_bin, fake_cli_factory, tmp_path):
    fake_cli_factory({
        (): "root help\n\nAvailable Commands:\n  sub  Sub\n",
        ("sub",): "sub help\n\nAvailable Commands:\n  child  Child\n",
        ("sub", "child"): "child help\n",
    }, name="mycli")
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}"}
    result = _run(docus_bin, ["--stdout", "mycli", "sub"], cwd=tmp_path, env=env)
    assert result.returncode == 0
    # root heading should be "mycli sub", not a separate "mycli" section
    lines = result.stdout.splitlines()
    h1_lines = [ln for ln in lines if ln.startswith("# ")]
    assert len(h1_lines) == 1
    assert "sub" in h1_lines[0]


# ---------------------------------------------------------------------------
# Depth limiting
# ---------------------------------------------------------------------------

def test_max_depth_limits_exploration(docus_bin, fake_cli_factory, tmp_path):
    fake_cli_factory({
        (): "root\n\nAvailable Commands:\n  alpha  Alpha\n",
        ("alpha",): "alpha help\n\nAvailable Commands:\n  beta  Beta\n",
        ("alpha", "beta"): "beta help\n\nAvailable Commands:\n  deep  Deep\n",
        ("alpha", "beta", "deep"): "deep leaf\n",
    }, name="mycli")
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}"}
    result = _run(docus_bin, ["--stdout", "--max-depth", "1", "mycli"], cwd=tmp_path, env=env)
    assert result.returncode == 0
    assert "alpha help" in result.stdout     # depth 1 — present
    assert "beta help" not in result.stdout  # depth 2 — absent


# ---------------------------------------------------------------------------
# --like filter
# ---------------------------------------------------------------------------

_PATCH_SECTION = (
    "  -p, --patch\n"
    "        Interactively choose hunks of patch between the index and the\n"
    "        work tree and add them to the index. This gives the user a\n"
    "        chance to review the difference before adding modified contents\n"
    "        to the index.\n"
)
_UNRELATED_SECTION = (
    "  -n, --dry-run\n"
    "        Do not actually add the files, just show what would happen.\n"
    "        This option is useful when checking before committing.\n"
)


def test_like_filter_keeps_relevant_sections(docus_bin, fake_cli_factory):
    cli = fake_cli_factory({
        (): "root\n\nAvailable Commands:\n  add  Add\n  log  Log\n",
        ("add",): f"add help\n\n{_PATCH_SECTION}",
        ("log",): f"log help\n\n{_UNRELATED_SECTION}",
    })
    result = _run(docus_bin, ["--stdout", "--like", "patch", cli])
    assert result.returncode == 0
    assert "add" in result.stdout


def test_like_filter_excludes_irrelevant_sections(docus_bin, fake_cli_factory):
    cli = fake_cli_factory({
        (): "root\n\nAvailable Commands:\n  add  Add\n  log  Log\n",
        ("add",): f"add help\n\n{_PATCH_SECTION}",
        ("log",): f"log help\n\n{_UNRELATED_SECTION}",
    })
    result = _run(docus_bin, ["--stdout", "--like", "patch", cli])
    assert result.returncode == 0
    assert "dry-run" not in result.stdout


def test_like_filter_produces_shorter_output_than_full(docus_bin, fake_cli_factory):
    cli = fake_cli_factory({
        (): "root\n\nAvailable Commands:\n  add  Add\n  log  Log\n",
        ("add",): f"add help\n\n{_PATCH_SECTION}",
        ("log",): f"log help\n\n{_UNRELATED_SECTION}",
    })
    full = _run(docus_bin, ["--stdout", cli])
    filtered = _run(docus_bin, ["--stdout", "--like", "patch", cli])
    assert len(filtered.stdout) < len(full.stdout)


# ---------------------------------------------------------------------------
# Verbose flag
# ---------------------------------------------------------------------------

def test_verbose_writes_progress_to_stderr(docus_bin, fake_cli):
    result = _run(docus_bin, ["--stdout", "--verbose", fake_cli])
    assert result.returncode == 0
    assert "--help" in result.stderr


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def test_deduplication_replaces_repeated_content(docus_bin, fake_cli_factory):
    cli = fake_cli_factory({
        (): "root\n\nAvailable Commands:\n  alpha  Alpha\n  beta   Beta\n",
        ("alpha",): f"Alpha help.\n\n{_GLOBAL_FLAGS}",
        ("beta",):  f"Beta help.\n\n{_GLOBAL_FLAGS}",
    })
    result = _run(docus_bin, ["--stdout", cli])
    assert result.returncode == 0
    assert "duplicate content" in result.stdout
    assert result.stdout.count("--config PATH") == 1
