from integrations.providers import (
    DemoNetworkAdapter,
    HttpNetworkAdapter,
    NetworkAdapter,
    NetworkAuthorization,
)
from integrations.providers import get_network_adapter as _get_network_adapter


def get_network_adapter() -> NetworkAdapter:
    return _get_network_adapter()


__all__ = [
    "DemoNetworkAdapter",
    "HttpNetworkAdapter",
    "NetworkAdapter",
    "NetworkAuthorization",
    "get_network_adapter",
]
