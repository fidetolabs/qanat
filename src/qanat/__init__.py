"""Qanat: an agent-native workflow engine for building and backtesting alphas as DAGs.

A qanat is the underground channel that carried water across the desert for two
thousand years: it moved the water, it never owned it.
"""

# Read from the installed metadata rather than written here. A literal drifts the
# moment a release bumps pyproject and forgets this line -- 0.1.4 and 0.1.5 both
# shipped announcing themselves as 0.1.3, in `qanat --version` and in the banner
# `qanat serve` prints.
try:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _version

    __version__ = _version("qanat-fdtl")
except PackageNotFoundError:  # a source checkout that was never installed
    __version__ = "0.0.0+dev"

from qanat.models import Project, Source, Stage, Step, Universe
from qanat.store import Store

__all__ = ["Project", "Source", "Stage", "Step", "Store", "Universe", "__version__"]
