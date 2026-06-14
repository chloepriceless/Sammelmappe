import os
import sys
import tempfile
from pathlib import Path

# These MUST be set before any `app.*` import: app/config.py builds Settings() and
# app/auth.py builds the cookie serializer at import time, and app/main.py asserts a
# secure SECRET_KEY on import. Fixed assignment (not setdefault) so a developer's
# shell env can never point the suite at real data or run it with a real secret.
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production-use"
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="brs-test-data-")

sys.path.insert(0, str(Path(__file__).parent))
