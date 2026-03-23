#!/usr/bin/env python3
"""
docus - CLI Documentation Extractor

Generates a single comprehensive Markdown document from a CLI tool by
recursively invoking --help on every discovered (sub)command.  All
invocations run in parallel, and paragraphs that appear verbatim in
more than one command are deduplicated with a reference link.
"""

import argparse
import concurrent.futures
import difflib
import hashlib
import re
import subprocess
import sys
import textwrap
import threading
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class CommandNode:
    """A node in the command tree, holding its path and discovered help."""

    path: list[str]
    depth: int = 0
    help_text: str = ""
    description: str = ""
    subcommands: list["CommandNode"] = field(default_factory=list)
    error: str | None = None

    @property
    def full_command(self) -> str:
        return " ".join(self.path)

    @property
    def anchor(self) -> str:
        """GitHub-compatible heading anchor for this command."""
        return re.sub(r"\s+", "-", re.sub(r"[^a-z0-9\s-]", "", self.full_command.lower()).strip())


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------


def run_help(
    command_path: list[str], timeout: int = 10
) -> tuple[str, str | None]:
    """Run *command_path* with --help (then -h as fallback).

    Returns ``(output, error_message)``.  Output merges stdout and stderr
    because many CLIs print help to stderr on a non-zero exit code.
    """
    for flag in ("--help", "-h"):
        try:
            result = subprocess.run(
                [*command_path, flag],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            combined = (result.stdout + result.stderr).strip()
            if combined:
                return combined, None
        except subprocess.TimeoutExpired:
            return "", f"timed out: {' '.join(command_path)}"
        except FileNotFoundError:
            return "", f"command not found: {command_path[0]}"
        except OSError as exc:
            return "", str(exc)
    return "", f"no help output: {' '.join(command_path)}"


# ---------------------------------------------------------------------------
# Subcommand extraction
# ---------------------------------------------------------------------------

# Section headers that introduce a list of subcommands (Cobra, Click, etc.)
_SECTION_HDR = re.compile(
    r"^[ \t]*"
    r"(?:(?:available|management|basic|advanced|server|other|additional|"
    r"low-level|ancillary|main|remote|alias)\s+)?"
    r"commands?"
    r"(?:\s*\([^)]*\))?"   # optional qualifier like "(Beginner)"
    r"[ \t]*:[ \t]*$",
    re.IGNORECASE,
)

# A subcommand line inside such a section: 1-8 leading spaces + word + space
_CMD_IN_SECTION = re.compile(
    r"^[ \t]{1,8}([a-z][a-z0-9_:-]*)(?:[ \t]|$)", re.IGNORECASE
)

# git-style column-aligned layout: exactly 3 spaces + word + 3+ spaces
_GIT_CMD = re.compile(r"^   ([a-z][a-z0-9_-]+)[ \t]{3,}\S")

# npm/yarn "where <command> is one of: cmd1, cmd2, …"
_WHERE_HDR = re.compile(r"where\s+<\w+>\s+is\s+one\s+of:\s*", re.IGNORECASE)

# Words that must never be treated as subcommands
_BLACKLIST = frozenset(
    {
        "or", "and", "the", "a", "an", "is", "are", "be", "in", "of",
        "to", "for", "on", "at", "by", "with", "as", "if", "then",
        "else", "not", "no", "yes",
    }
)


def extract_subcommands(help_text: str) -> list[str]:
    """Return subcommand names found in *help_text*, preserving order."""
    results: list[str] = []
    lines = help_text.splitlines()

    # --- Strategy 1: named "Commands:" section (Cobra, Click, …) ---
    in_section = False
    for line in lines:
        if _SECTION_HDR.match(line):
            in_section = True
            continue
        if in_section:
            if not line.strip():
                # A blank line ends the section unless the next non-empty
                # line is still indented (some CLIs leave a blank gap).
                in_section = False
                continue
            m = _CMD_IN_SECTION.match(line)
            if m:
                name = m.group(1).lower()
                if name not in _BLACKLIST:
                    results.append(name)

    if results:
        return _dedupe(results)

    # --- Strategy 2: git column-aligned ---
    for line in lines:
        m = _GIT_CMD.match(line)
        if m:
            name = m.group(1).lower()
            if name not in _BLACKLIST:
                results.append(name)

    if results:
        return _dedupe(results)

    # --- Strategy 3: npm "where <cmd> is one of: …" ---
    full = " ".join(ln.strip() for ln in lines)
    m = _WHERE_HDR.search(full)
    if m:
        rest = full[m.end():]
        for tok in re.split(r"[,\s]+", rest):
            tok = tok.strip().rstrip(",.")
            if re.match(r"^[a-z][a-z0-9_-]*$", tok) and tok not in _BLACKLIST:
                results.append(tok)
        return _dedupe(results)

    return []


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ---------------------------------------------------------------------------
# Description extraction
# ---------------------------------------------------------------------------

_SKIP_PREFIXES = (
    "usage", "synopsis", "options", "flags", "commands",
    "example", "see also", "version", "#", "-", "[",
)


def extract_description(help_text: str) -> str:
    """Return a one-line human-readable description from *help_text*."""
    for line in help_text.splitlines():
        stripped = line.strip()
        if not stripped or len(stripped) < 5:
            continue
        low = stripped.lower()
        if any(low.startswith(kw) for kw in _SKIP_PREFIXES):
            continue
        # Skip lines that look like option flags
        if re.match(r"^-{1,2}[a-z]", stripped, re.IGNORECASE):
            continue
        return stripped
    return ""


# ---------------------------------------------------------------------------
# Recursive extractor (BFS + ThreadPoolExecutor)
# ---------------------------------------------------------------------------


class DocusExtractor:
    """Explore a CLI command tree, collecting ``--help`` for every node."""

    def __init__(
        self,
        max_depth: int = 5,
        max_workers: int = 20,
        timeout: int = 10,
        verbose: bool = False,
    ) -> None:
        self.max_depth = max_depth
        self.max_workers = max_workers
        self.timeout = timeout
        self.verbose = verbose
        self._visited: set[str] = set()
        self._lock = threading.Lock()

    def explore(self, command: list[str] | str) -> CommandNode:
        """BFS over the command tree with per-level parallelism.

        *command* is either a list of argv tokens (e.g. ``['git', 'commit']``)
        or a plain string (split on whitespace for backwards compatibility).

        Returns the root :class:`CommandNode` with ``subcommands``
        populated recursively.
        """
        if isinstance(command, str):
            command = command.split()
        key = " ".join(command)
        self._visited = {key}
        root = CommandNode(path=command, depth=0)
        queue: list[CommandNode] = [root]

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_workers
        ) as executor:
            while queue:
                future_map = {
                    executor.submit(self._process, node): node
                    for node in queue
                }
                queue = []
                for future in concurrent.futures.as_completed(future_map):
                    node = future_map[future]
                    try:
                        children = future.result()
                        queue.extend(children)
                    except Exception as exc:
                        node.error = str(exc)

        return root

    def _process(self, node: CommandNode) -> list[CommandNode]:
        """Fetch ``--help`` for *node* and return new child nodes."""
        if self.verbose:
            print(f"  {node.full_command} --help", file=sys.stderr)

        output, error = run_help(node.path, self.timeout)
        node.help_text = output
        if error and not output:
            node.error = error
        node.description = extract_description(output)

        if not output or node.depth >= self.max_depth:
            return []

        children: list[CommandNode] = []
        for name in extract_subcommands(output):
            child_key = f"{node.full_command} {name}"
            with self._lock:
                if child_key not in self._visited:
                    self._visited.add(child_key)
                    child = CommandNode(
                        path=[*node.path, name], depth=node.depth + 1
                    )
                    node.subcommands.append(child)
                    children.append(child)

        return children


# ---------------------------------------------------------------------------
# Paragraph deduplication helpers
# ---------------------------------------------------------------------------


def _split_paragraphs(text: str) -> list[str]:
    """Split *text* on blank lines, discarding empty paragraphs."""
    return [p for p in re.split(r"\n{2,}", text) if p.strip()]


def _para_hash(text: str) -> str:
    return hashlib.md5(text.strip().encode()).hexdigest()


# ---------------------------------------------------------------------------
# Relevance filter
# ---------------------------------------------------------------------------


class RelevanceFilter:
    """Score paragraphs for relevance to a search term.

    Three additive signals:
      - exact word-boundary match: 3.0
      - stem/morphological variant:  2.0
      - difflib character similarity: ratio × 1.0

    A paragraph is relevant if its total score >= threshold (default 1.5).
    """

    _DEFAULT_THRESHOLD = 1.5

    def __init__(self, term: str, threshold: float = _DEFAULT_THRESHOLD) -> None:
        self.term = term.lower()
        self.threshold = threshold
        self._stems = self._build_stems(self.term)
        self._exact_re = re.compile(
            r"\b" + re.escape(self.term) + r"\b", re.IGNORECASE
        )
        self._stem_res = [
            re.compile(r"\b" + re.escape(s) + r"\b", re.IGNORECASE)
            for s in self._stems
            if s != self.term
        ]

    @staticmethod
    def _build_stems(term: str) -> list[str]:
        stems = [term]
        stems.append(term + "s")
        stems.append(term + "es")
        stems.append(term + "ing")
        stems.append(term + "ed")
        stems.append(term + "er")
        # If term ends in 'e', drop it before adding -ing/-ed
        if term.endswith("e"):
            base = term[:-1]
            stems.append(base + "ing")
            stems.append(base + "ed")
        return list(dict.fromkeys(stems))  # preserve order, dedupe

    def score(self, text: str) -> float:
        if self._exact_re.search(text):
            return 3.0
        total = 0.0
        for stem_re in self._stem_res:
            if stem_re.search(text):
                total += 2.0
                break  # count stem signal once
        words = re.findall(r"[a-zA-Z]{4,}", text)
        for word in words:
            ratio = difflib.SequenceMatcher(
                None, self.term, word.lower()
            ).ratio()
            if ratio >= 0.75:
                total += ratio
                break  # count difflib signal once
        return total

    def is_relevant(self, text: str) -> bool:
        return self.score(text) >= self.threshold


# ---------------------------------------------------------------------------
# Markdown generator
# ---------------------------------------------------------------------------


class MarkdownGenerator:
    """Turn a :class:`CommandNode` tree into a single Markdown document."""

    # Minimum paragraph length (chars) to be a deduplication candidate
    _MIN_PARA_LEN = 80

    def __init__(self, root: CommandNode, relevance: RelevanceFilter | None = None) -> None:
        self.root = root
        self.relevance = relevance
        # hash -> command that first contains this paragraph
        self._first_seen: dict[str, str] = {}
        # hashes that appear in 2+ different commands
        self._dup_hashes: set[str] = set()

    # --- public --------------------------------------------------------

    def generate(self) -> str:
        nodes = list(self._walk(self.root))
        if self.relevance is not None:
            nodes = self._filter_nodes(nodes)
        self._scan_duplicates(nodes)
        parts = [
            self._header(),
            self._index(nodes),
            *[self._section(node) for node in nodes],
        ]
        return "\n".join(parts)

    # --- relevance filtering -------------------------------------------

    def _filter_nodes(self, nodes: list[CommandNode]) -> list[CommandNode]:
        """Keep root always; keep other nodes if any paragraph is relevant."""
        kept = []
        for node in nodes:
            if node.depth == 0:
                kept.append(node)
                continue
            paras = _split_paragraphs(node.help_text)
            if any(self.relevance.is_relevant(p) for p in paras):
                kept.append(node)
        return kept

    # --- traversal -----------------------------------------------------

    def _walk(self, node: CommandNode):
        yield node
        for child in node.subcommands:
            yield from self._walk(child)

    # --- duplication scan ----------------------------------------------

    def _scan_duplicates(self, nodes: list[CommandNode]) -> None:
        seen: dict[str, str] = {}
        for node in nodes:
            for para in _split_paragraphs(node.help_text):
                if len(para.strip()) < self._MIN_PARA_LEN:
                    continue
                h = _para_hash(para)
                if h in seen:
                    self._dup_hashes.add(h)
                    self._first_seen[h] = seen[h]
                else:
                    seen[h] = node.full_command
                    self._first_seen[h] = node.full_command

    # --- document sections ---------------------------------------------

    def _header(self) -> str:
        return (
            f"# {self.root.full_command}\n\n"
            f"> Generated by **docus** — CLI Documentation Extractor\n"
        )

    def _index(self, nodes: list[CommandNode]) -> str:
        rows = [
            "\n## Index\n",
            "| Command | Description |",
            "| ------- | ----------- |",
        ]
        for node in nodes:
            desc = (node.description or "").replace("|", "\\|")
            if len(desc) > 90:
                desc = desc[:87] + "…"
            rows.append(
                f"| [`{node.full_command}`](#{node.anchor}) | {desc} |"
            )
        rows.append("")
        return "\n".join(rows)

    def _section(self, node: CommandNode) -> str:
        level = min(node.depth + 2, 6)   # h2 root, h3 depth-1, …
        heading = "#" * level
        lines = [f"\n{heading} `{node.full_command}`\n"]

        if node.error and not node.help_text:
            lines.append(f"> **Error:** {node.error}\n")
            return "\n".join(lines)

        if not node.help_text:
            lines.append("*No documentation available.*\n")
            return "\n".join(lines)

        body = self._deduplicated_body(node)
        # Escape any accidental triple-backtick sequences in help text
        body = body.replace("```", "`` `")
        lines.append(f"```\n{body}\n```\n")
        return "\n".join(lines)

    def _deduplicated_body(self, node: CommandNode) -> str:
        """Return help text with duplicate paragraphs replaced by references."""
        paras = _split_paragraphs(node.help_text)

        # When relevance filter is active, keep only relevant paragraphs
        # (plus always the first paragraph of the section for context).
        if self.relevance is not None:
            relevant_indices = [
                i for i, p in enumerate(paras) if self.relevance.is_relevant(p)
            ]
            if relevant_indices:
                indices_to_keep = set(relevant_indices)
                indices_to_keep.add(0)
                paras = [p for i, p in enumerate(paras) if i in indices_to_keep]

        out: list[str] = []
        for para in paras:
            if len(para.strip()) >= self._MIN_PARA_LEN:
                h = _para_hash(para)
                if h in self._dup_hashes:
                    first = self._first_seen.get(h, "")
                    if first != node.full_command:
                        out.append(
                            f"[[ duplicate content — see `{first}` ]]"
                        )
                        continue
            out.append(para)
        return "\n\n".join(out)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="docus",
        description="Generate unified Markdown documentation from a CLI command.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            examples:
              docus git
              docus git commit
              docus --output atlas.md atlas
              docus --stdout --max-depth 3 docker
              docus -v kubectl | less
            """
        ),
    )
    p.add_argument(
        "command",
        nargs="+",
        metavar="WORD",
        help="CLI command to document, optionally including a subcommand entry "
             "point (e.g. 'git commit' starts the tree at git-commit)",
    )
    p.add_argument(
        "-o", "--output",
        metavar="FILE",
        help="write output to FILE (default: <command>.md)",
    )
    p.add_argument(
        "-d", "--max-depth",
        type=int, default=5, metavar="N",
        help="maximum subcommand recursion depth (default: 5)",
    )
    p.add_argument(
        "-w", "--workers",
        type=int, default=20, metavar="N",
        help="maximum parallel worker threads (default: 20)",
    )
    p.add_argument(
        "-t", "--timeout",
        type=int, default=10, metavar="SEC",
        help="per-command timeout in seconds (default: 10)",
    )
    p.add_argument(
        "--stdout",
        action="store_true",
        help="print to stdout instead of writing a file",
    )
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="show each command being explored on stderr",
    )
    p.add_argument(
        "--like",
        metavar="TERM",
        default=None,
        help="filter output to paragraphs relevant to TERM",
    )
    return p


def _count_nodes(node: CommandNode) -> int:
    return 1 + sum(_count_nodes(c) for c in node.subcommands)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    command: list[str] = args.command  # nargs='+' gives a list
    output_path = args.output or f"{'-'.join(command)}.md"

    print(f"docus: exploring '{' '.join(command)}' …", file=sys.stderr)

    extractor = DocusExtractor(
        max_depth=args.max_depth,
        max_workers=args.workers,
        timeout=args.timeout,
        verbose=args.verbose,
    )
    root = extractor.explore(command)

    if not root.help_text and root.error:
        print(f"docus: error — {root.error}", file=sys.stderr)
        return 1

    n = _count_nodes(root)
    print(
        f"docus: {n} command(s) discovered, generating documentation …",
        file=sys.stderr,
    )

    relevance = RelevanceFilter(args.like) if args.like else None
    markdown = MarkdownGenerator(root, relevance=relevance).generate()

    if args.stdout:
        sys.stdout.write(markdown)
    else:
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(markdown)
        print(f"docus: wrote {output_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
