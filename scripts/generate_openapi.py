from pathlib import Path
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.main import app


spec = app.openapi()
spec["paths"] = {
    path: item
    for path, item in spec["paths"].items()
    if not path.startswith("/api/v1") and path != "/health"
}
output = Path(__file__).resolve().parent.parent / "docs" / "openapi.yaml"
output.write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8")
print(f"Wrote {output} with {len(spec['paths'])} PDF MVP paths")
