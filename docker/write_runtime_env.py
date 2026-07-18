#!/usr/bin/env python3
"""Write the non-secret container contract for root SSH shells."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import tempfile


destination = Path("/run/see-through/runtime.env")
values = {
    "SEE_THROUGH_BIND_HOST": os.environ["SEE_THROUGH_BIND_HOST"],
    "SEE_THROUGH_PORT": os.environ["SEE_THROUGH_PORT"],
    "SEE_THROUGH_DATA_DIR": os.environ["SEE_THROUGH_DATA_DIR"],
    "SEE_THROUGH_MODEL_CACHE": os.environ["SEE_THROUGH_MODEL_CACHE"],
    "SEE_THROUGH_PREFETCH": os.environ.get("SEE_THROUGH_PREFETCH", "auto"),
    "HF_HOME": os.environ["HF_HOME"],
}
fd, temporary_name = tempfile.mkstemp(prefix="runtime.env.", dir=destination.parent)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        for name, value in values.items():
            handle.write(f"export {name}={shlex.quote(value)}\n")
        handle.write("export PYTHONPATH=/opt/see-through/common:/opt/see-through/server\n")
        handle.write("export PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary_name, destination)
finally:
    try:
        os.unlink(temporary_name)
    except FileNotFoundError:
        pass
