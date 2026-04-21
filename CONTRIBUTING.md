# Contributing to pester

Thanks for your interest in contributing to pester!

## Development Setup

```bash
# Clone the repo
git clone https://github.com/stansolo93/pester.git
cd pester

# Install with all extras + dev tools
pip install -e ".[all,dev]"

# Verify
pester --version
```

## Running Tests

```bash
make test          # Fast tests only (skip @slow, @search, @mcp)
make test-all      # All tests including slow + search
make lint          # Ruff check + format check
make format        # Auto-format with ruff
```

Test markers:
- `@pytest.mark.slow` — long-running tests
- `@pytest.mark.search` — requires `[search]` extra (ChromaDB + ONNX)
- `@pytest.mark.mcp` — requires `[mcp]` extra
- `@pytest.mark.daemon` — requires `[daemon]` extra (watchdog)
- `@pytest.mark.llm` — requires `[llm]` extra (OpenAI and/or Anthropic SDKs)

## Code Style

- **Formatter/linter:** [Ruff](https://docs.astral.sh/ruff/) (configured in `pyproject.toml`)
- **Line length:** 100 characters
- **Target:** Python 3.11+
- **Imports:** Use `from __future__ import annotations` in every module

### Optional extras

Optional extras use the centralized factory in `core/extras.py`:

```python
from pester.core.extras import make_optional_check

HAS_SEARCH, require_search = make_optional_check("chromadb", "search")
```

Commands call `require_search()` at the top. It raises `SystemExit` with a `pip install` hint if the extra is missing.

## Adding CLI Commands

1. Create `src/pester/cli/cmd_yourcommand.py`
2. Register in `src/pester/cli/main.py`:
   ```python
   from pester.cli.cmd_yourcommand import yourcommand
   cli.add_command(yourcommand)
   ```

See `CLAUDE.md` for the full contributor guide, module dependency order, and architecture details.

## Pull Request Process

1. Create a feature branch from `main`
2. Make your changes
3. Run `make test && make lint`
4. Submit a PR with a clear description of what changed and why

## Vault Discovery

3-tier lookup: `--vault` flag, `$PESTER_VAULT` env var, or walk up from CWD for `pester.yaml`.

## Conduct

Be respectful. The maintainer reserves the right to remove content and block users.
