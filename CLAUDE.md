# docus — Claude instructions

## Commands

```bash
make lint   # ruff, vulture, ty, xenon
make test   # unit + functional suites
uv run pytest -m <marker>   # run a single feature's tests
```

## Tests

Tests are split into two files:

- `tests/test_unit.py` — tests that import docus internals directly
- `tests/test_functional.py` — tests that invoke the binary via subprocess

When writing any new test, mark it with the pytest marker that matches the
feature it covers. The registered markers are:

| Marker | Feature |
|--------|---------|
| `file_output` | Document a CLI tool to a Markdown file |
| `stdout` | Print documentation to stdout without writing a file |
| `entry_point` | Start the command tree at a subcommand entry point |
| `depth_limit` | Limit subcommand recursion depth |
| `like_filter` | Filter output to sections relevant to a search term |
| `deduplication` | Deduplicate repeated paragraphs across commands |

In functional tests, apply the marker as a decorator:

```python
@pytest.mark.file_output
def test_something(docus_bin, ...):
    ...
```

In unit tests, apply it as a class variable:

```python
class TestSomething(unittest.TestCase):
    pytestmark = pytest.mark.file_output
```

For classes that span two features, use a list (annotated with `ClassVar` to satisfy ruff):

```python
class TestSomething(unittest.TestCase):
    pytestmark: typing.ClassVar = [pytest.mark.entry_point, pytest.mark.depth_limit]
```
