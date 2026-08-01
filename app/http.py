from datetime import datetime, timezone
from uuid import uuid4


def success(data, *, status: int = 200) -> dict:
    return {
        "data": data,
        "meta": {
            "requestId": f"req_{uuid4().hex}",
            "serverTime": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
    }
