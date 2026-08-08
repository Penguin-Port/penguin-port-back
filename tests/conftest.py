import os
import tempfile
from pathlib import Path


TEST_DATABASE = Path(tempfile.gettempdir()) / "smartpass-fastapi-pytest.sqlite3"
TEST_DATABASE.unlink(missing_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DATABASE.as_posix()}"
os.environ["DEMO_KEY"] = "demo-key"
os.environ["DEMO_OTP_CODE"] = "123456"
