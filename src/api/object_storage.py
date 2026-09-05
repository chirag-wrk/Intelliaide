"""
Object storage for must-gather archives, results, and session state.

Supports **S3-compatible** backends (AWS S3, MinIO) and **GCS**.
The backend is selected via the ``OBJECT_STORAGE_BACKEND`` env var
(``"s3"`` by default).

Used **only by the API pod** to durably store input archives, analysis
results, agent memory, and session metadata.  Workers never interact
with object storage directly — all data flows through the API's
callback endpoints.
"""

import json
import logging
import os
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

OBJECT_STORAGE_BACKEND: str = os.environ.get("OBJECT_STORAGE_BACKEND", "s3")

# ---- S3 / MinIO configuration ------------------------------------------------

S3_BUCKET_NAME: str = os.environ.get("S3_BUCKET_NAME", "rca-inputs")
S3_ENDPOINT_URL: str = os.environ.get("S3_ENDPOINT_URL", "")
S3_ACCESS_KEY: str = os.environ.get("AWS_ACCESS_KEY_ID", "")
S3_SECRET_KEY: str = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
S3_REGION: str = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

# ---- GCS configuration -------------------------------------------------------

GCS_BUCKET_NAME: str = os.environ.get("GCS_BUCKET_NAME", "rca-inputs")
GCS_PROJECT_ID: str = os.environ.get("GCS_PROJECT_ID", "")

# ---- Lazy clients ------------------------------------------------------------

_s3_client = None
_gcs_client: Optional["storage.Client"] = None


def _get_s3_client():
    global _s3_client
    if _s3_client is None:
        import boto3
        kwargs = {"region_name": S3_REGION}
        if S3_ENDPOINT_URL:
            kwargs["endpoint_url"] = S3_ENDPOINT_URL
        if S3_ACCESS_KEY and S3_SECRET_KEY:
            kwargs["aws_access_key_id"] = S3_ACCESS_KEY
            kwargs["aws_secret_access_key"] = S3_SECRET_KEY
        _s3_client = boto3.client("s3", **kwargs)
    return _s3_client


def _get_gcs_client():
    global _gcs_client
    if _gcs_client is None:
        from google.cloud import storage
        kwargs = {}
        if GCS_PROJECT_ID:
            kwargs["project"] = GCS_PROJECT_ID
        _gcs_client = storage.Client(**kwargs)
    return _gcs_client


def _bucket_name() -> str:
    if OBJECT_STORAGE_BACKEND == "gcs":
        return GCS_BUCKET_NAME
    return S3_BUCKET_NAME


def _gcs_bucket():
    return _get_gcs_client().bucket(GCS_BUCKET_NAME)


def _archive_blob_name(session_id: str, suffix: str = "") -> str:
    if not suffix:
        suffix = ".tar.gz"
    return f"{session_id}/archive{suffix}"


# ---------------------------------------------------------------------------
# Upload / download
# ---------------------------------------------------------------------------

def upload_archive(session_id: str, local_path: Path) -> str:
    """Upload an archive to object storage using resumable upload.

    Returns a ``s3://`` or ``gs://`` URI of the uploaded object.
    """
    suffix = "".join(local_path.suffixes) or ".tar.gz"
    blob_name = _archive_blob_name(session_id, suffix)
    bucket = _bucket_name()

    if OBJECT_STORAGE_BACKEND == "gcs":
        blob = _gcs_bucket().blob(blob_name)
        blob.upload_from_filename(str(local_path), timeout=1800)
        uri = f"gs://{bucket}/{blob_name}"
    else:
        _get_s3_client().upload_file(str(local_path), bucket, blob_name)
        uri = f"s3://{bucket}/{blob_name}"

    logger.info("Uploaded %s to %s (%.2f GB)",
                local_path.name, uri,
                local_path.stat().st_size / (1024 ** 3))
    return uri


def upload_archive_key(session_id: str, key: str, local_path: Path) -> str:
    """Upload a local file to ``{session_id}/{key}`` via a filename-based upload.

    Unlike :func:`write_bytes`, this streams from disk rather than buffering the
    whole file in memory, so it is safe for large archives.
    Returns a ``s3://`` or ``gs://`` URI of the uploaded object.
    """
    blob_name = f"{session_id}/{key}"
    bucket = _bucket_name()

    if OBJECT_STORAGE_BACKEND == "gcs":
        blob = _gcs_bucket().blob(blob_name)
        blob.upload_from_filename(str(local_path), timeout=1800)
        return f"gs://{bucket}/{blob_name}"
    else:
        _get_s3_client().upload_file(str(local_path), bucket, blob_name)
        return f"s3://{bucket}/{blob_name}"


def download_key_to_file(session_id: str, key: str, dest: Path,
                         chunk_size: int = 8 * 1024 * 1024) -> bool:
    """Stream ``{session_id}/{key}`` to a local file. Returns False if missing."""
    blob_key = f"{session_id}/{key}"

    if OBJECT_STORAGE_BACKEND == "gcs":
        from google.cloud.exceptions import NotFound
        blob = _gcs_bucket().blob(blob_key)
        try:
            blob.download_to_filename(str(dest))
            return True
        except NotFound:
            return False
    else:
        from botocore.exceptions import ClientError
        s3 = _get_s3_client()
        try:
            with open(dest, "wb") as f:
                body = s3.get_object(Bucket=_bucket_name(), Key=blob_key)["Body"]
                while True:
                    data = body.read(chunk_size)
                    if not data:
                        break
                    f.write(data)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return False
            raise


def stream_download(session_id: str, chunk_size: int = 8 * 1024 * 1024) -> Iterator[bytes]:
    """Yield the archive for *session_id* as a stream of byte chunks."""
    prefix = f"{session_id}/"

    if OBJECT_STORAGE_BACKEND == "gcs":
        blobs = list(_gcs_bucket().list_blobs(prefix=prefix, max_results=5))
        archive_blob = None
        for b in blobs:
            if b.name.startswith(prefix + "archive"):
                archive_blob = b
                break
        if archive_blob is None:
            raise FileNotFoundError(
                f"No archive found in object storage for session {session_id}"
            )
        archive_blob.reload()
        with archive_blob.open("rb") as f:
            while True:
                data = f.read(chunk_size)
                if not data:
                    break
                yield data
    else:
        s3 = _get_s3_client()
        bucket = _bucket_name()
        resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=5)
        archive_key = None
        for obj in resp.get("Contents", []):
            if obj["Key"].startswith(prefix + "archive"):
                archive_key = obj["Key"]
                break
        if archive_key is None:
            raise FileNotFoundError(
                f"No archive found in object storage for session {session_id}"
            )
        body = s3.get_object(Bucket=bucket, Key=archive_key)["Body"]
        while True:
            data = body.read(chunk_size)
            if not data:
                break
            yield data


def get_archive_metadata(session_id: str) -> dict:
    """Return metadata (size, content_type) for the archive blob."""
    prefix = f"{session_id}/"

    if OBJECT_STORAGE_BACKEND == "gcs":
        blobs = list(_gcs_bucket().list_blobs(prefix=prefix, max_results=5))
        for b in blobs:
            if b.name.startswith(prefix + "archive"):
                b.reload()
                return {
                    "size": b.size,
                    "content_type": b.content_type or "application/octet-stream",
                    "name": b.name.split("/")[-1],
                }
    else:
        s3 = _get_s3_client()
        bucket = _bucket_name()
        resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=5)
        for obj in resp.get("Contents", []):
            if obj["Key"].startswith(prefix + "archive"):
                head = s3.head_object(Bucket=bucket, Key=obj["Key"])
                return {
                    "size": head["ContentLength"],
                    "content_type": head.get("ContentType", "application/octet-stream"),
                    "name": obj["Key"].split("/")[-1],
                }

    raise FileNotFoundError(
        f"No archive found in object storage for session {session_id}"
    )


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------

def delete_session_objects(session_id: str) -> int:
    """Delete all objects under the session prefix. Returns count deleted."""
    prefix = f"{session_id}/"

    if OBJECT_STORAGE_BACKEND == "gcs":
        blobs = list(_gcs_bucket().list_blobs(prefix=prefix))
        count = 0
        for blob in blobs:
            blob.delete()
            count += 1
    else:
        s3 = _get_s3_client()
        bucket = _bucket_name()
        count = 0
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if objects:
                s3.delete_objects(Bucket=bucket, Delete={"Objects": objects})
                count += len(objects)

    if count:
        logger.info("Deleted %d objects for session %s", count, session_id)
    return count


def delete_prefix(session_id: str, key_prefix: str) -> int:
    """Delete blobs under ``{session_id}/{key_prefix}``. Returns count deleted."""
    prefix = f"{session_id}/{key_prefix}"

    if OBJECT_STORAGE_BACKEND == "gcs":
        blobs = list(_gcs_bucket().list_blobs(prefix=prefix))
        count = 0
        for blob in blobs:
            blob.delete()
            count += 1
        return count
    else:
        s3 = _get_s3_client()
        bucket = _bucket_name()
        count = 0
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if objects:
                s3.delete_objects(Bucket=bucket, Delete={"Objects": objects})
                count += len(objects)
        return count


# ---------------------------------------------------------------------------
# Generic key-value helpers (JSON / bytes / text)
# ---------------------------------------------------------------------------

def write_json(session_id: str, key: str, data: dict) -> None:
    """Write a JSON-serialisable dict to ``{session_id}/{key}``."""
    blob_key = f"{session_id}/{key}"
    payload = json.dumps(data, indent=2).encode("utf-8")

    if OBJECT_STORAGE_BACKEND == "gcs":
        blob = _gcs_bucket().blob(blob_key)
        blob.upload_from_string(payload, content_type="application/json")
    else:
        _get_s3_client().put_object(
            Bucket=_bucket_name(), Key=blob_key,
            Body=payload, ContentType="application/json",
        )


def read_json(session_id: str, key: str) -> dict | None:
    """Read a JSON object from ``{session_id}/{key}``. Returns None if missing."""
    blob_key = f"{session_id}/{key}"

    if OBJECT_STORAGE_BACKEND == "gcs":
        from google.cloud.exceptions import NotFound
        blob = _gcs_bucket().blob(blob_key)
        try:
            return json.loads(blob.download_as_text(encoding="utf-8"))
        except NotFound:
            return None
    else:
        from botocore.exceptions import ClientError
        try:
            resp = _get_s3_client().get_object(Bucket=_bucket_name(), Key=blob_key)
            return json.loads(resp["Body"].read().decode("utf-8"))
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                return None
            raise


def write_bytes(session_id: str, key: str, data: bytes,
                content_type: str = "application/octet-stream") -> None:
    """Write raw bytes to ``{session_id}/{key}``."""
    blob_key = f"{session_id}/{key}"

    if OBJECT_STORAGE_BACKEND == "gcs":
        blob = _gcs_bucket().blob(blob_key)
        blob.upload_from_string(data, content_type=content_type)
    else:
        _get_s3_client().put_object(
            Bucket=_bucket_name(), Key=blob_key,
            Body=data, ContentType=content_type,
        )


def read_bytes(session_id: str, key: str) -> bytes | None:
    """Read raw bytes from ``{session_id}/{key}``. Returns None if missing."""
    blob_key = f"{session_id}/{key}"

    if OBJECT_STORAGE_BACKEND == "gcs":
        from google.cloud.exceptions import NotFound
        blob = _gcs_bucket().blob(blob_key)
        try:
            return blob.download_as_bytes()
        except NotFound:
            return None
    else:
        from botocore.exceptions import ClientError
        try:
            resp = _get_s3_client().get_object(Bucket=_bucket_name(), Key=blob_key)
            return resp["Body"].read()
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                return None
            raise


def write_text(session_id: str, key: str, text: str) -> None:
    """Write UTF-8 text to ``{session_id}/{key}``."""
    blob_key = f"{session_id}/{key}"
    payload = text.encode("utf-8")

    if OBJECT_STORAGE_BACKEND == "gcs":
        blob = _gcs_bucket().blob(blob_key)
        blob.upload_from_string(payload, content_type="text/plain; charset=utf-8")
    else:
        _get_s3_client().put_object(
            Bucket=_bucket_name(), Key=blob_key,
            Body=payload, ContentType="text/plain; charset=utf-8",
        )


def read_text(session_id: str, key: str) -> str | None:
    """Read UTF-8 text from ``{session_id}/{key}``. Returns None if missing."""
    blob_key = f"{session_id}/{key}"

    if OBJECT_STORAGE_BACKEND == "gcs":
        from google.cloud.exceptions import NotFound
        blob = _gcs_bucket().blob(blob_key)
        try:
            return blob.download_as_text(encoding="utf-8")
        except NotFound:
            return None
    else:
        from botocore.exceptions import ClientError
        try:
            resp = _get_s3_client().get_object(Bucket=_bucket_name(), Key=blob_key)
            return resp["Body"].read().decode("utf-8")
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                return None
            raise


def list_session_prefixes(limit: int = 1000) -> list[str]:
    """Return up to *limit* session-id prefixes in the bucket."""
    if OBJECT_STORAGE_BACKEND == "gcs":
        iterator = _get_gcs_client().list_blobs(
            GCS_BUCKET_NAME, prefix="", delimiter="/", max_results=limit,
        )
        for _ in iterator:
            pass
        return [p.rstrip("/") for p in iterator.prefixes]
    else:
        s3 = _get_s3_client()
        bucket = _bucket_name()
        resp = s3.list_objects_v2(Bucket=bucket, Prefix="", Delimiter="/", MaxKeys=limit)
        return [p["Prefix"].rstrip("/") for p in resp.get("CommonPrefixes", [])]


def list_keys(session_id: str, key_prefix: str = "", limit: int = 1000) -> list[str]:
    """Return object keys (relative to *session_id*) under ``{session_id}/{key_prefix}``.

    The returned keys are stripped of the ``{session_id}/`` prefix so they can be
    passed straight back into :func:`read_bytes` / :func:`read_json` etc.
    """
    full_prefix = f"{session_id}/{key_prefix}"
    strip = f"{session_id}/"

    if OBJECT_STORAGE_BACKEND == "gcs":
        blobs = _get_gcs_client().list_blobs(
            GCS_BUCKET_NAME, prefix=full_prefix, max_results=limit,
        )
        return [b.name[len(strip):] for b in blobs if b.name.startswith(strip)]
    else:
        s3 = _get_s3_client()
        bucket = _bucket_name()
        keys: list[str] = []
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=full_prefix,
                                       PaginationConfig={"MaxItems": limit}):
            for obj in page.get("Contents", []):
                if obj["Key"].startswith(strip):
                    keys.append(obj["Key"][len(strip):])
        return keys
