"""`${VAR}` in a definition's options, resolved against the environment.

**This lived in `rest.py`, and the rest connector is gone.** It was never a
property of HTTP: a contact address, a key, a path -- anything a definition
should not have written into the file in plain text. `sec` reads `user_agent`
through it, which is the one caller left.
"""

from __future__ import annotations

import os
import re
from typing import Any

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class MissingEnv(ValueError):
    """A `${VAR}` in the options that the environment does not set."""


def expand(value: Any, missing: set[str] | None = None) -> Any:
    """Replace `${NAME}` from the environment.

    An unset name used to become an empty string, which produced `Illegal header
    value b'Bearer '`, or a request sent with a blank API key, or the actively wrong
    "rest needs options.url" when the option was set and the variable was not.
    Collect the names instead and let the caller say which one is missing.
    """
    if isinstance(value, str):
        def sub(m: re.Match) -> str:
            name = m.group(1)
            if name not in os.environ:
                if missing is not None:
                    missing.add(name)
                return m.group(0)
            return os.environ[name]
        return _ENV.sub(sub, value)
    if isinstance(value, dict):
        return {k: expand(v, missing) for k, v in value.items()}
    if isinstance(value, list):
        return [expand(v, missing) for v in value]
    return value


__all__ = ["MissingEnv", "expand"]
