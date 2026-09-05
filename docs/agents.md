# Notes on the agent surface

The README covers adding qanat to an MCP client and what the 27 tools are. These are the parts
that only matter once you are using them.

## The agent's surface is wider than the CLI's

Six tools share a name with a command and do the same thing: `check`, `plan`, `run`, `backtest`,
`report`, `compare`. Two are renamed — `qanat backtests` is `list_backtests`, `qanat alphas` is
`list_alphas`. Everything else differs, deliberately.

An agent navigates where a person reads. `qanat ls` prints stages, tables and jobs in one dump;
an agent gets `list_tables`, `list_steps` and `describe_table` separately, and `describe_table`
plus `sample_table` to look inside one. `qanat report` prints a whole run; an agent also gets
`period` and `weights` to open one point on the curve.

Going the other way, `save_step`, `save_source` and `use_alpha` have no CLI at all. You author a
step in your editor; an agent authors one through the API.

Five commands have no tool. `init` and `prune` because an agent works inside a project that
already exists and does not drop tables; `serve` because `open_console` does it in-session; `mcp`
because it is the transport; `ls` because it is three tools here.

## `sample_table` honours `as_of`

Passing an as-of date reads the table through the same point-in-time view a replay uses, so an
agent inspecting history sees what a step would have seen on that date. Without it, an agent
reasoning about the past reasons about rows that had not happened yet.

## `backtest_conditions` exists to stop a guess

A window nobody chose produces a number nobody should act on. This tool returns the dates the data
actually covers, the universes declared, the current defaults for `rebalance` and `decay`, and an
explicit `ask_the_person_for` list. It is the one tool whose job is to make the agent stop and ask.

## `--read-only`

Keeps the 20 tools that read, drops the 7 that write: `run`, `backtest`, `use_alpha`, `save_step`,
`remove_step`, `save_source`, `open_console`. Worth using when an agent is exploring a project
whose numbers somebody else depends on.
