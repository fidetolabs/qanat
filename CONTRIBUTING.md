# Contributing to Qanat

Qanat is MIT-licensed and open to pull requests. This guide covers local setup and where to
change things.

## Local development

```bash
git clone https://github.com/fidetolabs/qanat.git && cd qanat
uv sync --extra dev

qanat init demo && cd demo
uv run qanat run
uv run qanat serve          # console + editor at http://127.0.0.1:8420
```

Or with pip: `pip install -e ".[dev]"`.

Run tests:

```bash
uv run pytest
uv run ruff check src tests
```

### Testing against Postgres

`tests/test_postgres.py` skips itself unless it can reach a server. Start one — this is the
Postgres service alone, not the whole image build, so it takes about five seconds:

```bash
docker compose up -d postgres
uv run pytest tests/test_postgres.py     # about four seconds
```

Worth doing for anything touching `store.py`. Qanat reaches Postgres by attaching it to DuckDB,
and the two engines disagree in ways a DuckDB file never shows you: Postgres refuses a non-constant
`DEFAULT` in `ALTER TABLE`, will not drop a table a view depends on, and will not cast an integer to
a timestamp. Every one of those was a real bug that the rest of the suite ran straight past.

## Architecture

| Layer | Path | Role |
| --- | --- | --- |
| Project file | `qanat.yaml` | Stages, sources, steps, retention, store URL |
| Contract | `src/qanat/project.py` | Validates the stage rules |
| Runner | `src/qanat/runner.py` | Executes sources and steps |
| Scheduler | `src/qanat/scheduler.py` | Cron + retention cleanup |
| Store | `src/qanat/store.py` | DuckDB engine; DuckDB file or Postgres via ATTACH |
| Console API | `src/qanat/api.py` | Graph, editor endpoints, run-now |
| Console UI | `src/qanat/console/` | Dashboard + pipeline editor |
| Editor logic | `src/qanat/editor.py` | Mutations that write `qanat.yaml` |
| Connectors | `src/qanat/sources/` | Plugin surface: one `fetch()` per connector |

## Adding a connector

1. Implement `fetch(source, root) -> DataFrame` in `src/qanat/sources/your_connector.py`
2. Register it in `src/qanat/sources/__init__.py`
3. Add the name to `Connector` in `src/qanat/models.py`
4. Document options in README and add a test if feasible

## Pull requests

- Keep diffs focused — one feature or fix per PR when possible
- Run `pytest` and `ruff check` before opening
- Update README if you add user-visible behavior
- The stage contract in `docs/contract.md` is intentional — changes need discussion

## Community

- [Discord](https://discord.gg/JUmwATScS8) for questions
- GitHub Issues for bugs and feature requests
