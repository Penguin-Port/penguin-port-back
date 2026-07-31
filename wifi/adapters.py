from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class NetworkAuthorization:
    reference: str
    status: str


class NetworkAdapter(Protocol):
    def authorize(self, *, pass_id, expires_at) -> NetworkAuthorization: ...

    def revoke(self, *, reference: str) -> bool: ...

    def get_status(self, *, reference: str) -> str: ...

    def disconnect_device(self, *, reference: str) -> bool: ...


class DemoNetworkAdapter:
    def authorize(self, *, pass_id, expires_at):
        return NetworkAuthorization(reference=f"demo:{pass_id}", status="AUTHORIZED")

    def revoke(self, *, reference: str):
        return True

    def get_status(self, *, reference: str):
        return "AUTHORIZED" if reference else "REVOKED"

    def disconnect_device(self, *, reference: str):
        return True


def get_network_adapter() -> NetworkAdapter:
    # 운영 환경에서는 설정값에 따라 UniFi/RADIUS/MikroTik 구현을 반환한다.
    return DemoNetworkAdapter()
