# Backwards-compatibility shim — all types now live in app/schemas/
# Import from app.schemas directly in new code.
from app.schemas import *  # noqa: F401, F403
