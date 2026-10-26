# Contributing to Qanat

Qanat is MIT-licensed and open to pull requests. This guide covers local setup and where to
change things.

**The rules a change must not break are in [CLAUDE.md](CLAUDE.md)**, each one naming the test that
holds it. Read that before changing behaviour; read this for where things live.

## Local development

```bash
git clone https://github.com/fidetolabs/qanat.git && cd qanat
uv sync --extra dev

qanat init demo && cd demo
uv run qanat run
uv run qanat mcp            # serve this project to an agent over stdio
uv run qanat serve          # scheduler + MCP over HTTP at http://127.0.0.1:8421/mcp
```

Or with pip: `pip install -e ".[dev]"`.

Run tests:

```bash
uv run pytest                        # about nine seconds
uv run ruff check src tests audit
```

Across cores it is about three seconds:

```bash
uv run pytest -q -n auto
```

CI passes `--dist loadgroup` as well, and keeps it on purpose. Nothing needs it today — no test
file has carried an `xdist_group` mark since the Postgres store went — but a file that shares one
resource has to stay on one worker, and the flag costs nothing while no file does.

The batteries under `audit/` are opt-in — slower, and one of them calls a live public API:

```bash
uv run pytest audit/batteries -m audit
```

### Testing against both stores

A table is either a directory of parquet files or a single DuckDB file, and the suite runs against
either one. The default is the DuckDB file. Run the whole thing against parquet with:

```bash
QANAT_TEST_STORE=parquet uv run pytest -q -n auto
```

**The point is equivalence**: the same tests, the same assertions, a different storage kind
underneath. The same count passes both ways. Worth doing for anything touching `store.py` or
`reading.py`.

## Architecture

| Layer | Path | Role |
| --- | --- | --- |
| Project file | `qanat.yaml` | Stages, sources, steps, retention, store URL |
| Contract | `src/qanat/project.py` | Validates the stage rules |
| Runner | `src/qanat/runner.py` | Executes sources and steps |
| Scheduler | `src/qanat/scheduler.py` | Cron + retention cleanup |
| Store | `src/qanat/store.py` | DuckDB engine over a directory of parquet files, or one DuckDB file |
| Reading | `src/qanat/reading.py` | The reading tools. Every one of them filters in SQL |
| Filings — XBRL | `src/qanat/xbrl.py` | Inline XBRL: contexts, units, scale, signs |
| Filings — sections | `src/qanat/sections.py` | Section boundaries, and the span each one records |
| Screen | `src/qanat/screen.py` | Which companies passed, on a date or over a window |
| MCP (stdio) | `src/qanat/mcp.py` | The tool surface, and the three scopes |
| MCP (HTTP) | `src/qanat/mcp_http.py` | The same tools on `/mcp`, with Host and token checks |
| CLI | `src/qanat/cli.py` | `init`, `check`, `plan`, `prune`, `ls`, `graph`, `run`, `screen`, and what starts the servers |
| Editor logic | `src/qanat/editor.py` | Mutations that write `qanat.yaml` |
| Graph | `src/qanat/graph.py` | The read model, and the terminal drawing of it |
| Connectors | `src/qanat/sources/` | Plugin surface: one `fetch()` per connector |

## Adding a connector

1. Implement `fetch(source, root) -> DataFrame` in `src/qanat/sources/your_connector.py`
2. Register it in `src/qanat/sources/__init__.py`
3. Add the name to `Connector` in `src/qanat/models.py`
4. Document options in README and add a test if feasible

## Pull requests

- Keep diffs focused — one feature or fix per PR when possible
- Run `uv run pytest` and `uv run ruff check src tests audit` before opening
- Update README if you add user-visible behavior
- The stage contract in `docs/contract.md` is intentional — changes need discussion

## Community

- [Discord](https://discord.gg/JUmwATScS8) for questions
- GitHub Issues for bugs and feature requests
