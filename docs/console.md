# The console

`qanat serve` starts the scheduler and a web console on http://127.0.0.1:8420. The console explains
itself; this file is only for the things you cannot work out by looking at it.

## It polls, it does not stream

The console holds no socket open. It asks `/api/graph` every 2.5 seconds, and every 120
milliseconds while a run is in flight. That is what makes a replay look continuous.

## Nothing moves unless a job ran

There is no idle animation. During a replay a packet crosses an arrow only when the step on that
arrow has just written the table it points at.

A source never fills a bar, because a replay does not re-poll it. Nor does a PnL table, which is
written once when the run ends. Tables outside the alpha being priced stay dark, because nothing
happened to them.

## Blends are left out of the correlation heatmap

A blend of two alphas correlates with its own parts by construction, so including it would fill the
map with a number that means nothing. Only single alphas are compared.

## Editing writes to disk

Everything you change is written to `qanat.yaml`, and `qanat check` still holds the project against
the stage contract when you apply it. There is no separate console state to fall out of sync.
