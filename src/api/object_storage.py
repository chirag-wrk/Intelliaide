"""
GCS object storage for must-gather archives, results, and session state.

Used **only by the API pod** to durably store input archives, analysis
results, agent memory, and session metadata.  Workers never interact
with GCS directly — all data flows through the API's callback endpoints.
"""

import json
import logging
import os
from pathlib import Path
from typing import Iterator, Optional

from google.cloud.exceptions import NotFound

from google.cloud import storage

logger = logging.getLogger(__name__)

GCS_BUCKET_NAME: str = os.environ.get("GCS_BUCKET_NAME", "rca-inputs")
GCS_PROJECT_ID: str = os.environ.get("GCS_PROJECT_ID", "")

_client: Optional[storage.Client] = None


def _get_client() -> storage.Client:
    global _client
    if _client is None:
        kwargs = {}
        if GCS_PROJECT_ID:
            kwargs["project"] = GCS_PROJECT_ID
        _client = storage.Client(**kwargs)
    return _client


def _bucket() -> storage.Bucket:
    return _get_client().bucket(GCS_BUCKET_NAME)


def _archive_blob_name(session_id: str, suffix: str = "") -> str:
    if not suffix:
        suffix = ".tar.gz"
    return f"{session_id}/archive{suffix}"


def upload_archive(session_id: str, local_path: Path) -> str:
    """Upload an archive to GCS using resumable upload.

    Returns the ``gs://`` URI of the uploaded object.
    """
    suffix = "".join(local_path.suffixes) or ".tar.gz"
    blob_name = _archive_blob_name(session_id, suffix)
    blob = _bucket().blob(blob_name)

    blob.upload_from_filename(
        str(local_path),
        timeout=1800,
    )

    uri = f"gs://{GCS_BUCKET_NAME}/{blob_name}"
    logger.info("Uploaded %s to %s (%.2f GB)",
                local_path.name, uri,
                local_path.stat().st_size / (1024 ** 3))
    return uri


def stream_download(session_id: str, chunk_size: int = 8 * 1024 * 1024) -> Iterator[bytes]:
    """Yield the archive for *session_id* as a stream of byte chunks.

    Used by the API's ``GET /callback/input/{sid}`` to proxy the archive
    to the worker without loading the entire file into memory.
    """
    prefix = f"{session_id}/"
    blobs = list(_bucket().list_blobs(prefix=prefix, max_results=5))

    archive_blob = None
    for b in blobs:
        if b.name.startswith(prefix + "archive"):
            archive_blob = b
            break

    if archive_blob is None:
        raise FileNotFoundError(
            f"No archive found in GCS for session {session_id}"
        )

    archive_blob.reload()
    with archive_blob.open("rb") as f:
        while True:
            data = f.read(chunk_size)
            if not data:
                break
            yield data


def get_archive_metadata(session_id: str) -> dict:
    """Return metadata (size, content_type) for the archive blob."""
    prefix = f"{session_id}/"
    blobs = list(_bucket().list_blobs(prefix=prefix, max_results=5))

    for b in blobs:
        if b.name.startswith(prefix + "archive"):
            b.reload()
            return {
                "size": b.size,
                "content_type": b.content_type or "application/octet-stream",
                "name": b.name.split("/")[-1],
            }

    raise FileNotFoundError(
        f"No archive found in GCS for session {session_id}"
    )


def delete_session_objects(session_id: str) -> int:
    """Delete all GCS objects under the session prefix. Returns count deleted."""
    prefix = f"{session_id}/"
    blobs = list(_bucket().list_blobs(prefix=prefix))
    count = 0
    for blob in blobs:
        blob.delete()
        count += 1
    if count:
        logger.info("Deleted %d GCS objects for session %s", count, session_id)
    return count


def delete_prefix(session_id: str, key_prefix: str) -> int:
    """Delete blobs under ``{session_id}/{key_prefix}``. Returns count deleted."""
    prefix = f"{session_id}/{key_prefix}"
    blobs = list(_bucket().list_blobs(prefix=prefix))
    count = 0
    for blob in blobs:
        blob.delete()
        count += 1
    return count


# ---------------------------------------------------------------------------
# Generic key-value helpers (JSON / bytes / text)
# ---------------------------------------------------------------------------

def write_json(session_id: str, key: str, data: dict) -> None:
    """Write a JSON-serialisable dict to ``{session_id}/{key}``."""
    blob = _bucket().blob(f"{session_id}/{key}")
    blob.upload_from_string(
        json.dumps(data, indent=2),
        content_type="application/json",
    )


def read_json(session_id: str, key: str) -> dict | None:
    """Read a JSON object from ``{session_id}/{key}``. Returns None if missing."""
    blob = _bucket().blob(f"{session_id}/{key}")
    try:
        return json.loads(blob.download_as_text(encoding="utf-8"))
    except NotFound:
        return None


def write_bytes(session_id: str, key: str, data: bytes,
                content_type: str = "application/octet-stream") -> None:
    """Write raw bytes to ``{session_id}/{key}``."""
    blob = _bucket().blob(f"{session_id}/{key}")
    blob.upload_from_string(data, content_type=content_type)


def read_bytes(session_id: str, key: str) -> bytes | None:
    """Read raw bytes from ``{session_id}/{key}``. Returns None if missing."""
    blob = _bucket().blob(f"{session_id}/{key}")
    try:
        return blob.download_as_bytes()
    except NotFound:
        return None


def write_text(session_id: str, key: str, text: str) -> None:
    """Write UTF-8 text to ``{session_id}/{key}``."""
    blob = _bucket().blob(f"{session_id}/{key}")
    blob.upload_from_string(text.encode("utf-8"), content_type="text/plain; charset=utf-8")


def read_text(session_id: str, key: str) -> str | None:
    """Read UTF-8 text from ``{session_id}/{key}``. Returns None if missing."""
    blob = _bucket().blob(f"{session_id}/{key}")
    try:
        return blob.download_as_text(encoding="utf-8")
    except NotFound:
        return None


def list_session_prefixes(limit: int = 1000) -> list[str]:
    """Return up to *limit* session-id prefixes in the bucket."""
    iterator = _get_client().list_blobs(
        GCS_BUCKET_NAME, prefix="", delimiter="/", max_results=limit,
    )
    # Consume the iterator so .prefixes is populated.
    for _ in iterator:
        pass
    return [p.rstrip("/") for p in iterator.prefixes]
