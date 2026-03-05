import os

# Disable auth for all tests by default.
# Auth-specific tests override this with patch.dict.
os.environ.setdefault("AUTH_ENABLED", "false")
