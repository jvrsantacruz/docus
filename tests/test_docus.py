"""
Tests for docus.py — organised by feature area.

Run:
    make test
    uv run pytest tests/test_docus.py -v
"""

import concurrent.futures
import io
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import docus
from docus import CommandNode, DocusExtractor, MarkdownGenerator, RelevanceFilter, extract_subcommands

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _proc(stdout="", stderr=""):
    m = MagicMock()
    m.stdout, m.stderr = stdout, stderr
    return m


def _responses(mapping: dict[str, str]):
    """Return a subprocess.run side_effect that looks up output by command string."""
    def _run(cmd, **_):
        return _proc(stdout=mapping.get(" ".join(cmd), ""))
    return _run


ROOT_WITH_SUBS = (
    "mycli — a tool\n\n"
    "Usage:\n  mycli [command]\n\n"
    "Available Commands:\n"
    "  alpha  Alpha subcommand\n"
    "  beta   Beta subcommand\n\n"
    "Flags:\n  -h, --help  help\n"
)
ALPHA_HELP = "Usage: mycli alpha [flags]\n\nThe alpha subcommand.\n"
BETA_HELP  = "Usage: mycli beta [flags]\n\nThe beta subcommand.\n"

GLOBAL_FLAGS = (
    "Global Flags:\n"
    "  --config string   Path to config file (default $HOME/.config)\n"
    "  --debug           Enable debug output to stderr\n"
    "  --verbose         Verbose logging (implies --debug)\n"
    "  --timeout int     Timeout in seconds (default 30)\n"
)


def _tree(children: dict[str, str] | None = None) -> CommandNode:
    """Build a CommandNode tree from plain strings, no subprocess needed."""
    root = CommandNode(path=["mycli"], depth=0, help_text=ROOT_WITH_SUBS)
    for name, text in (children or {}).items():
        child = CommandNode(path=["mycli", name], depth=1, help_text=text)
        root.subcommands.append(child)
    return root


# ---------------------------------------------------------------------------
# Feature: subcommand detection
# ---------------------------------------------------------------------------

class TestSubcommandDetection(unittest.TestCase):

    def test_cobra_style(self):
        text = (
            "Usage:\n  mycli [command]\n\n"
            "Available Commands:\n"
            "  init    Initialise\n"
            "  run     Run it\n"
            "  config  Configure\n\n"
            "Flags:\n  -h, --help  help\n"
        )
        self.assertEqual(extract_subcommands(text), ["init", "run", "config"])

    def test_git_column_style(self):
        text = (
            "start a working area\n"
            "   clone             Clone a repository\n"
            "   init              Create an empty repo\n\n"
            "work on the current change\n"
            "   add               Add file contents\n"
        )
        result = extract_subcommands(text)
        self.assertEqual(result, ["clone", "init", "add"])

    def test_npm_comma_style(self):
        text = (
            "Usage: npm <command>\n\n"
            "where <command> is one of:\n"
            "    install, uninstall, publish\n"
        )
        result = extract_subcommands(text)
        self.assertIn("install", result)
        self.assertIn("publish", result)

    def test_no_subcommands_returns_empty(self):
        self.assertEqual(extract_subcommands("Usage: echo [string]\n\nPrint text.\n"), [])


# ---------------------------------------------------------------------------
# Feature: recursive exploration
# ---------------------------------------------------------------------------

class TestExploration(unittest.TestCase):

    def _x(self, **kw):
        return DocusExtractor(
            max_depth=kw.get("max_depth", 5),
            max_workers=kw.get("max_workers", 4),
            timeout=kw.get("timeout", 5),
        )

    @patch("subprocess.run")
    def test_discovers_subcommands(self, mock_run):
        mock_run.side_effect = _responses({
            "mycli --help": ROOT_WITH_SUBS,
            "mycli alpha --help": ALPHA_HELP,
            "mycli beta --help": BETA_HELP,
        })
        root = self._x().explore(["mycli"])
        names = {c.full_command for c in root.subcommands}
        self.assertIn("mycli alpha", names)
        self.assertIn("mycli beta", names)

    @patch("subprocess.run")
    def test_respects_max_depth(self, mock_run):
        mock_run.side_effect = _responses({
            "deep --help": "Usage: deep\n\nAvailable Commands:\n  one  One\n",
            "deep one --help": "Usage: deep one\n\nAvailable Commands:\n  two  Two\n",
            "deep one two --help": "Usage: deep one two\n\nAvailable Commands:\n  three  Three\n",
            "deep one two three --help": "Usage: deep one two three\n\nLeaf.\n",
        })
        root = self._x(max_depth=2).explore(["deep"])

        def all_names(node):
            yield node.full_command
            for c in node.subcommands:
                yield from all_names(c)

        cmds = set(all_names(root))
        self.assertIn("deep one two", cmds)
        self.assertNotIn("deep one two three", cmds)

    @patch("subprocess.run")
    def test_command_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        root = self._x().explore(["notexist"])
        self.assertEqual(root.help_text, "")
        self.assertIsNotNone(root.error)

    @patch("subprocess.run")
    def test_parallel_faster_than_sequential(self, mock_run):
        delay = 0.05

        def slow(cmd, **_):
            time.sleep(delay)
            key = " ".join(cmd)
            if key == "mycli --help":
                return _proc(stdout=(
                    "Root\n\nAvailable Commands:\n"
                    "  a  A\n  b  B\n  c  C\n  d  D\n  e  E\n"
                ))
            return _proc(stdout=f"Help for {key}")

        mock_run.side_effect = slow
        t0 = time.monotonic()
        self._x(max_workers=10).explore(["mycli"])
        # 6 calls × delay sequential = 0.30 s; parallel should be much less
        self.assertLess(time.monotonic() - t0, 6 * delay * 0.8 + 0.3)


# ---------------------------------------------------------------------------
# Feature: document output
# ---------------------------------------------------------------------------

class TestOutput(unittest.TestCase):

    @patch("subprocess.run")
    def test_writes_file_with_heading_and_index(self, mock_run):
        import tempfile
        mock_run.return_value = _proc(stdout="Usage: mycli\n\nA tool.\n")
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "mycli.md"
            self.assertEqual(docus.main(["--output", str(out), "mycli"]), 0)
            with open(out) as f:
                content = f.read()
            self.assertIn("# mycli", content)
            self.assertIn("## Index", content)

    @patch("subprocess.run")
    def test_custom_output_path(self, mock_run):
        import tempfile
        mock_run.return_value = _proc(stdout="Usage: mycli\n\nA tool.\n")
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "custom.md"
            docus.main(["--output", str(out), "mycli"])
            self.assertTrue(out.exists())

    @patch("subprocess.run")
    def test_stdout_flag(self, mock_run):
        mock_run.return_value = _proc(stdout="Usage: mycli\n\nA tool.\n")
        buf = io.StringIO()
        sys.stdout, old = buf, sys.stdout
        try:
            ret = docus.main(["--stdout", "mycli"])
        finally:
            sys.stdout = old
        self.assertEqual(ret, 0)
        self.assertIn("# mycli", buf.getvalue())

    @patch("subprocess.run")
    def test_exits_nonzero_on_command_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        self.assertEqual(docus.main(["nonexistent"]), 1)


# ---------------------------------------------------------------------------
# Feature: document structure
# ---------------------------------------------------------------------------

class TestDocumentStructure(unittest.TestCase):

    def test_index_lists_all_commands(self):
        root = _tree({"alpha": ALPHA_HELP, "beta": BETA_HELP})
        md = MarkdownGenerator(root).generate()
        self.assertIn("`mycli`", md)
        self.assertIn("`mycli alpha`", md)
        self.assertIn("`mycli beta`", md)

    def test_help_text_appears_in_output(self):
        root = CommandNode(path=["mycli"], depth=0, help_text="some distinctive help text")
        md = MarkdownGenerator(root).generate()
        self.assertIn("some distinctive help text", md)

    def test_no_help_text_noted(self):
        root = _tree()
        root.subcommands = [CommandNode(path=["mycli", "silent"], depth=1, help_text="")]
        md = MarkdownGenerator(root).generate()
        self.assertIn("No documentation available", md)

    def test_error_noted_in_section(self):
        root = CommandNode(path=["mycli"], depth=0, help_text="", error="command not found")
        md = MarkdownGenerator(root).generate()
        self.assertIn("Error", md)
        self.assertIn("command not found", md)


# ---------------------------------------------------------------------------
# Feature: deduplication
# ---------------------------------------------------------------------------

class TestDeduplication(unittest.TestCase):

    def test_shared_paragraphs_appear_once_with_reference(self):
        root = _tree({
            "alpha": f"Alpha subcommand.\n\n{GLOBAL_FLAGS}",
            "beta":  f"Beta subcommand.\n\n{GLOBAL_FLAGS}",
        })
        md = MarkdownGenerator(root).generate()
        # Full content appears exactly once
        self.assertEqual(md.count("--config string"), 1)
        # The other occurrence is a reference, not a repeat
        self.assertIn("duplicate content", md)


# ---------------------------------------------------------------------------
# Feature: subcommand entry point
# ---------------------------------------------------------------------------

class TestSubcommandEntryPoint(unittest.TestCase):

    def _x(self):
        return DocusExtractor(max_depth=3, max_workers=4, timeout=5)

    @patch("subprocess.run")
    def test_roots_tree_at_subcommand(self, mock_run):
        mock_run.side_effect = _responses({
            "mycli config --help": (
                "Manage config.\n\nAvailable Commands:\n  get  Get\n  set  Set\n"
            ),
            "mycli config get --help": "Get a value.\n",
            "mycli config set --help": "Set a value.\n",
        })
        root = self._x().explore(["mycli", "config"])
        self.assertEqual(root.full_command, "mycli config")
        child_names = {c.full_command for c in root.subcommands}
        self.assertIn("mycli config get", child_names)
        self.assertIn("mycli config set", child_names)

    @patch("subprocess.run")
    def test_parent_not_in_output(self, mock_run):
        mock_run.side_effect = _responses({
            "mycli config --help": "Manage config.\n\nAvailable Commands:\n  get  Get\n",
            "mycli config get --help": "Get a value.\n",
        })
        root = self._x().explore(["mycli", "config"])
        md = MarkdownGenerator(root).generate()
        self.assertIn("# mycli config", md)
        self.assertNotIn("# mycli\n", md)

    @patch("subprocess.run")
    def test_filename_uses_dashes(self, mock_run):
        import tempfile
        mock_run.return_value = _proc(stdout="Usage: git commit\n\nRecord changes.\n")
        with tempfile.TemporaryDirectory() as d:
            orig = Path.cwd()
            os.chdir(d)
            try:
                docus.main(["git", "commit"])
                self.assertTrue(Path("git-commit.md").exists())
            finally:
                os.chdir(orig)

    @patch("subprocess.run")
    def test_depth_relative_to_entry_point(self, mock_run):
        mock_run.side_effect = _responses({
            "mycli sub --help": "Sub.\n\nAvailable Commands:\n  child  Child\n",
            "mycli sub child --help": "Child.\n\nAvailable Commands:\n  leaf  Leaf\n",
            "mycli sub child leaf --help": "Leaf node.\n",
        })
        root = DocusExtractor(max_depth=1, max_workers=4, timeout=5).explore(["mycli", "sub"])

        def all_names(node):
            yield node.full_command
            for c in node.subcommands:
                yield from all_names(c)

        cmds = set(all_names(root))
        self.assertIn("mycli sub child", cmds)   # depth=1, within limit
        self.assertNotIn("mycli sub child leaf", cmds)  # depth=2, beyond limit


# ---------------------------------------------------------------------------
# Feature: relevance filter scoring
# ---------------------------------------------------------------------------

class TestRelevanceFilter(unittest.TestCase):

    def test_exact_match_passes(self):
        f = RelevanceFilter("patch")
        self.assertTrue(f.is_relevant("Use git patch to apply changes."))

    def test_exact_match_case_insensitive(self):
        f = RelevanceFilter("patch")
        self.assertTrue(f.is_relevant("Apply a PATCH to the working tree."))

    def test_stem_variant_patches_passes(self):
        f = RelevanceFilter("patch")
        self.assertTrue(f.is_relevant("Applying patches to the repository."))

    def test_stem_variant_patching_passes(self):
        f = RelevanceFilter("patch")
        self.assertTrue(f.is_relevant("Patching files in the working directory."))

    def test_unrelated_paragraph_fails(self):
        f = RelevanceFilter("patch")
        self.assertFalse(f.is_relevant("Show the commit log with author and date."))

    def test_custom_threshold_respected(self):
        f = RelevanceFilter("patch", threshold=4.0)
        # exact match scores 3.0, below new threshold
        self.assertFalse(f.is_relevant("Apply a patch to the tree."))

    def test_short_unrelated_words_ignored(self):
        f = RelevanceFilter("patch")
        # words shorter than 4 chars are skipped in difflib check
        self.assertFalse(f.is_relevant("run add rm set git log ref tag"))


# ---------------------------------------------------------------------------
# Feature: --like filter in MarkdownGenerator
# ---------------------------------------------------------------------------

_PATCH_PARA = (
    "  -p, --patch\n"
    "        Interactively choose hunks of patch between the index and the\n"
    "        work tree and add them to the index. This gives the user a\n"
    "        chance to review the difference before adding modified contents\n"
    "        to the index.\n"
)
_UNRELATED_PARA = (
    "  -n, --dry-run\n"
    "        Don't actually add the file(s), just show if they exist and/or\n"
    "        will be ignored.\n"
)
_ROOT_PARA = "mycli — a tool for managing things in the repository environment.\n"


class TestLikeFilter(unittest.TestCase):

    def _root_with_sub(self, sub_help: str) -> CommandNode:
        root = CommandNode(path=["mycli"], depth=0, help_text=_ROOT_PARA)
        child = CommandNode(path=["mycli", "add"], depth=1, help_text=sub_help)
        root.subcommands.append(child)
        return root

    def test_relevant_paragraph_included(self):
        root = self._root_with_sub(_PATCH_PARA)
        rf = RelevanceFilter("patch")
        md = MarkdownGenerator(root, relevance=rf).generate()
        self.assertIn("patch", md.lower())

    def test_unrelated_paragraph_excluded(self):
        # sub only has unrelated content → node filtered out
        root = self._root_with_sub(_UNRELATED_PARA)
        rf = RelevanceFilter("patch")
        md = MarkdownGenerator(root, relevance=rf).generate()
        self.assertNotIn("`mycli add`", md)

    def test_root_section_always_present(self):
        root = self._root_with_sub(_UNRELATED_PARA)
        rf = RelevanceFilter("patch")
        md = MarkdownGenerator(root, relevance=rf).generate()
        self.assertIn("# mycli", md)

    def test_index_contains_only_relevant_nodes(self):
        root = CommandNode(path=["mycli"], depth=0, help_text=_ROOT_PARA)
        root.subcommands.append(
            CommandNode(path=["mycli", "add"], depth=1, help_text=_PATCH_PARA)
        )
        root.subcommands.append(
            CommandNode(path=["mycli", "log"], depth=1, help_text=_UNRELATED_PARA)
        )
        rf = RelevanceFilter("patch")
        md = MarkdownGenerator(root, relevance=rf).generate()
        self.assertIn("`mycli add`", md)
        self.assertNotIn("`mycli log`", md)

    def test_no_like_gives_full_output(self):
        root = CommandNode(path=["mycli"], depth=0, help_text=_ROOT_PARA)
        root.subcommands.append(
            CommandNode(path=["mycli", "add"], depth=1, help_text=_PATCH_PARA)
        )
        root.subcommands.append(
            CommandNode(path=["mycli", "log"], depth=1, help_text=_UNRELATED_PARA)
        )
        md_full = MarkdownGenerator(root).generate()
        md_like = MarkdownGenerator(root, relevance=RelevanceFilter("patch")).generate()
        # Full output has more content than filtered
        self.assertGreater(len(md_full), len(md_like))
        # Full output contains both subcommands
        self.assertIn("`mycli log`", md_full)


# ---------------------------------------------------------------------------
# Parallel runner
# ---------------------------------------------------------------------------

_TEST_CLASSES = [
    TestSubcommandDetection,
    TestExploration,
    TestOutput,
    TestDocumentStructure,
    TestDeduplication,
    TestSubcommandEntryPoint,
    TestRelevanceFilter,
    TestLikeFilter,
]

if __name__ == "__main__":
    this_file = str(Path(__file__).resolve())
    class_names = {cls.__name__ for cls in _TEST_CLASSES}

    # Subprocess dispatch: run a single named class
    if len(sys.argv) == 2 and sys.argv[1] in class_names:
        unittest.main(defaultTest=sys.argv[1], argv=[sys.argv[0]], verbosity=2)
        sys.exit()

    # Parallel mode: one subprocess per test class, coordinated via threads
    def _run(name):
        r = subprocess.run(
            [sys.executable, this_file, name],
            capture_output=True, text=True,
        )
        return name, r.returncode, r.stdout + r.stderr

    failures = 0
    with concurrent.futures.ThreadPoolExecutor() as ex:
        for _name, rc, out in ex.map(_run, [c.__name__ for c in _TEST_CLASSES]):
            print(out, end="")
            if rc != 0:
                failures += 1

    sys.exit(min(failures, 1))
