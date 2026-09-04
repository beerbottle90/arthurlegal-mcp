"""eqanun — dependency-free client for Azerbaijan's official legislation API.

See ``API.md`` for the reconstructed API reference and ``README.md`` for usage.
"""

from .client import (
    API_BASE,
    SITE_BASE,
    STATUS,
    EqanunClient,
    EqanunError,
    __version__,
)

__all__ = [
    "EqanunClient",
    "EqanunError",
    "STATUS",
    "API_BASE",
    "SITE_BASE",
    "__version__",
]
