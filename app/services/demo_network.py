def authorize(pass_id: str) -> str:
    return f"demo:{pass_id}"


def revoke(reference: str) -> bool:
    return True
