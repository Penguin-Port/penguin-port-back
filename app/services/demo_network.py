from integrations.providers import get_network_adapter


def authorize(pass_id: str, *, expires_at=None) -> str:
    result = get_network_adapter().authorize(pass_id=pass_id, expires_at=expires_at)
    return result.reference


def revoke(reference: str) -> bool:
    return get_network_adapter().revoke(reference=reference)
