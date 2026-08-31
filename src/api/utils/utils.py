"""
Shared utilities for the Must-Gather Analysis API.

Currently provides ZIP archive helpers used by the API layer.
"""

import io
import zipfile
from pathlib import Path
from typing import Sequence, Tuple, Union


def create_zip_from_files(
    file_entries: Sequence[Union[Path, Tuple[Path, str]]],
) -> io.BytesIO:
    """Create an in-memory ZIP archive from a list of files.

    Each entry is either:
      - A ``Path`` — archived under its filename (``path.name``).
      - A ``(Path, arcname)`` tuple — archived under the explicit *arcname*.

    Returns a seeked-to-zero ``BytesIO`` buffer ready for streaming.
    Skips entries whose path does not exist on disk.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for entry in file_entries:
            if isinstance(entry, tuple):
                path, arcname = entry
            else:
                path = entry
                arcname = path.name
            path = Path(path)
            if path.is_file():
                zf.write(path, arcname)
    buf.seek(0)
    return buf


def create_zip_from_buffers(
    entries: Sequence[Tuple[str, Union[str, bytes]]],
) -> io.BytesIO:
    """Create an in-memory ZIP from name/content pairs (no disk files needed).

    Each entry is ``(arcname, content)`` where *content* is ``str`` or ``bytes``.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, content in entries:
            data = content.encode("utf-8") if isinstance(content, str) else content
            zf.writestr(arcname, data)
    buf.seek(0)
    return buf
