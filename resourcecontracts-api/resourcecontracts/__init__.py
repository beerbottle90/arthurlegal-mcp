"""resourcecontracts — dependency-free client for the ResourceContracts.org API.

Public repository of petroleum and mining contracts (NRGI / CCSI / partners),
CC BY-SA 4.0. See ``API.md`` for the API reference and ``README.md`` for usage.
"""

from .client import (
    API_BASE,
    OPENLAND_API_BASE,
    SITE_BASE,
    ResourceContractsClient,
    ResourceContractsError,
)

__version__ = "0.1.0"
__all__ = [
    "ResourceContractsClient",
    "ResourceContractsError",
    "API_BASE",
    "SITE_BASE",
    "OPENLAND_API_BASE",
    "__version__",
]
