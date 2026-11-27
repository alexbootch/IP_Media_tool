"""Safe exception details for the runtime journal and node diagnostics."""

import os
from pathlib import Path
import traceback


def exception_details(error):
    """Report exception type, errno, file basename and source locations without values or locals."""
    details = {"exception": type(error).__name__}
    if isinstance(error, OSError):
        if isinstance(error.errno, int):
            details.update(errno=error.errno, reason=os.strerror(error.errno))
        if isinstance(error.filename, str):
            details["file"] = Path(error.filename).name[:120]
    details["frames"] = [
        f"{Path(frame.filename).name}:{frame.lineno} ({frame.name})"
        for frame in traceback.extract_tb(error.__traceback__, limit=-6)
    ]
    return details
