# The console

`qanat serve` starts the scheduler and a web console on http://127.0.0.1:8420. The console explains
itself; this file is only for the things you cannot work out by looking at it.

## The conversation is the navigation

The thread runs down the left and the surface beside it shows whatever the last meaningful tool
call was about. You navigate by asking; clicking is the fallback rather than the default.

There are four surfaces and one is up at a time. **Data** is what you connected and what is in it.
**Alpha** is the graph, and every step is built there -- a step landing in `features` is a feature
step, the same step landing in `weights` is an alpha, so they are one gesture with a different
target column. **Backtest** is the book and what a replay earned. **Live** is what it is earning
forward.

The four segments across the top are the state of the project and the way to reach each surface.
There is no separate tab row: two navigations for one set of places is one too many.

## How the console knows what the agent is doing

Nothing is reported to it. The agent talks to *this console's own HTTP API* -- a DuckDB file takes
one writer and the console is holding it -- so every tool call is already a request arriving at
this server. A middleware notes what was asked and which surface it is about, and `/api/trace`
hands that over.

The page stamps its own fetches with `X-Qanat-UI` and the server leaves those out. Without it the
console feeds itself: a repaint issues a request, the request lands in the trace, the trace tells
the console to repaint.

Following is a default, not a hijack. Pin the stage and it stays where you put it while the thread
goes on saying what moved.

## It streams

`/api/ask/stream` is server-sent events, carrying the reply and the tool calls it makes on one
connection so their order is the order they happened in. The CLI runs with
`--include-partial-messages`, so the text arrives as it is written rather than in one piece when
the process exits.

A model emits a clause at a time, not a character, so the client holds a target and walks toward it
a few characters a frame. Markdown renders as it grows: a table appears when its rule line lands
rather than sitting as pipes until the end.

The graph still polls. It asks `/api/graph` every 2.5 seconds, and every 120 milliseconds while a
run is in flight, which is what makes a replay look continuous.

## What to ask next is read off the project

A console with no strategy in it and one whose live scorer is failing are at different points of
the same arc. `/api/next` answers from the state -- "the graph stops before the weights stage",
"one alpha is not a book" -- so an empty console still has a next move, and a question already
asked is not offered again.

## Nothing moves unless a job ran

There is no idle animation. During a replay a packet crosses an arrow only when the step on that
arrow has just written the table it points at.

A source never fills a bar, because a replay does not re-poll it. Nor does a PnL table, which is
written once when the run ends. Tables outside the alpha being priced stay dark, because nothing
happened to them.

## The graph draws what is missing

A project with no alpha has not finished, it has stopped -- the pipeline ends in a portfolio, and
`qanat check` says "nothing writes into the weights stage yet". So the weights column carries a
dashed slot reading *no alpha yet*, because a graph that simply ends looks identical to one that is
done.

## Blends are left out of the correlation heatmap

A blend of two alphas correlates with its own parts by construction, so including it would fill the
map with a number that means nothing. Only single alphas are compared.

## Editing writes to disk

Everything you change is written to `qanat.yaml`, and `qanat check` still holds the project against
the stage contract when you apply it. There is no separate console state to fall out of sync.

Comments in that file survive the write. The new values are laid onto the tree parsed from the
file rather than dumped over it, so a note against a step is still there after the step beside it
is edited.
