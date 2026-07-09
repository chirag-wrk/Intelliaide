"""
GCS object storage for must-gather archives.

Used **only by the API pod** to durably store large input archives and
stream them to workers on a remote cluster via proxy endpoints.
Workers never interact with GCS directly.
"""

import logging
import os
from pathlib import Path
from typing import Iterator, Optional

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
