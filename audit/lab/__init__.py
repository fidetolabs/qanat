"""Machinery the chaos audit reuses on every run.

Split from the probes on purpose: `lab/` is the part that has to keep working, and
a run that has to repair its own harness first is a run that finds nothing.
"""

from lab import data, harness

__all__ = ["data", "harness"]
