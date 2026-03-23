# docus — CLI Documentation Extractor

[![CI](https://github.com/jvrsantacruz/docus/actions/workflows/ci.yml/badge.svg)](https://github.com/jvrsantacruz/docus/actions/workflows/ci.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

Generates a single comprehensive Markdown document from any CLI tool by recursively invoking `--help` on every discovered (sub)command. All invocations run in parallel; identical paragraphs across commands are deduplicated with a reference link.

## Why docus?

CLI tools rarely have a single comprehensive reference. Documentation is spread across dozens of subcommand help pages you have to navigate interactively. This creates friction when you want to understand a new tool, search across all its subcommands for a specific flag, or feed its full documentation into an LLM as context.

docus crawls the entire `--help` tree in parallel and produces one Markdown file you can commit, search, or paste into an LLM context window — run it once, reference it everywhere.

## Install

**From source:**

```bash
git clone https://github.com/jvrsantacruz/docus
cd docus
uv sync
uv run docus git
```

**One-shot with uvx (no install required):**

```bash
uvx --from git+https://github.com/jvrsantacruz/docus docus git
```

**Homebrew:**

```bash
brew tap jvrsantacruz/tap
brew install docus
```

**pip / uv tool** (once published to PyPI):

```bash
pip install docus
uv tool install docus
```

## Usage

```
docus [OPTIONS] COMMAND [SUBCOMMAND ...]

Options:
  -o, --output FILE    Write to FILE (default: <command>.md)
  -d, --max-depth N    Max subcommand recursion depth (default: 5)
  -w, --workers N      Parallel worker threads (default: 20)
  -t, --timeout SEC    Per-command timeout in seconds (default: 10)
  --stdout             Print to stdout instead of writing a file
  --like TERM          Filter output to paragraphs relevant to TERM
  -v, --verbose        Show each command being explored on stderr
```

## Examples

```bash
# Document git — writes git.md
docus git

# Start the tree at a subcommand entry point
docus git commit

# Custom output file
docus --output atlas.md atlas

# Stream to stdout, limit depth
docus --stdout --max-depth 3 docker

# Pipe to a pager
docus -v kubectl | less

# Filter to a topic — keeps only sections mentioning "patch"
docus --stdout --like patch git

# Feed directly to an LLM
docus --stdout gh | llm -s "explain the gh CLI"
```

## Output format

The generated file is valid CommonMark / GitHub-Flavored Markdown:

1. Title heading with the command name
2. Index table mapping every command to its one-line description, with anchor links
3. One section per command, containing verbatim `--help` output in a fenced code block

Headings follow: `##` root, `###` depth-1, `####` depth-2, capped at `h6`.

Paragraphs longer than 80 characters that appear verbatim in multiple commands are included in full only on the first occurrence; subsequent occurrences are replaced with `[[ duplicate content — see \`<command>\` ]]`.

## Supported CLI formats

| Format | Example |
|--------|---------|
| Cobra (Go) | `Available Commands:` section |
| Click (Python) | `Commands:` section |
| git column-aligned | Three-space-indented table |
| npm/yarn | `where <command> is one of: …` |

## Development

```bash
make lint    # ruff
make test    # pytest + coverage
```

Requirements: Python 3.11+. No external dependencies.

## License

[GPL-3.0-or-later](LICENSE) — Copyright (C) 2026 Javier Santacruz
