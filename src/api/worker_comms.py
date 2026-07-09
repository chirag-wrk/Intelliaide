"""
Worker communication abstraction.

Dispatches between two modes based on the ``COMMS_MODE`` env var:

- ``pvc``      (default) — legacy single-cluster mode; reads/writes the
  shared PVC filesystem exactly as before.
- ``callback`` — cross-cluster mode; the worker talks exclusively to the
  API pod over HTTPS.  No GCS SDK, no cloud credentials beyond Vertex AI.

Every public function in this module is a drop-in replacement for the
corresponding filesystem operation in ``worker.py``.
"""

import io
import json
import logging
import os
import shutil
import tarfile
import threading
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

COMMS_MODE: str = os.environ.get("COMMS_MODE", "pvc")
API_CALLBACK_URL: str = os.environ.get("API_CALLBACK_URL", "").rstrip("/")
CALLBACK_TOKEN: str = os.environ.get("CALLBACK_TOKEN", "")

_MAX_RETRIES = 3
_BACKOFF_BASE = 2


def _is_callback() -> bool:
    return COMMS_MODE == "callback"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {CALLBACK_TOKEN}",
    }


def _post(path: str, *, json_body: dict | None = None,
          data: bytes | None = None, stream_file: Path | None = None,
          content_type: str = "application/json",
          timeout: int = 300) -> requests.Response:
    """POST to the API with retry logic."""
    url = f"{API_CALLBACK_URL}{path}"
    headers = _headers()

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            if json_body is not None:
                resp = requests.post(url, json=json_body, headers=headers,
                                     timeout=timeout)
            elif stream_file is not None:
                headers["Content-Type"] = content_type
                with open(stream_file, "rb") as f:
                    resp = requests.post(url, data=f, headers=headers,
                                         timeout=timeout)
            elif data is not None:
                headers["Content-Type"] = content_type
                resp = requests.post(url, data=data, headers=headers,
                                     timeout=timeout)
            else:
                resp = requests.post(url, headers=headers, timeout=timeout)

            resp.raise_for_status()
            return resp

        except (requests.RequestException, IOError) as exc:
            if attempt < _MAX_RETRIES:
                delay = _BACKOFF_BASE ** attempt
                logger.warning(
                    "Callback POST %s failed (attempt %d/%d): %s  — retrying in %ds",
                    path, attempt, _MAX_RETRIES, exc, delay,
                )
                time.sleep(delay)
            else:
                logger.error(
                    "Callback POST %s failed after %d attempts: %s",
                    path, _MAX_RETRIES, exc,
                )
                raise


def _get(path: str, *, timeout: int = 600,
         stream: bool = False) -> requests.Response:
    """GET from the API with retry logic."""
    url = f"{API_CALLBACK_URL}{path}"
    headers = _headers()

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout,
                                stream=stream)
            resp.raise_for_status()
            return resp

        except (requests.RequestException, IOError) as exc:
            if attempt < _MAX_RETRIES:
                delay = _BACKOFF_BASE ** attempt
                logger.warning(
                    "Callback GET %s failed (attempt %d/%d): %s  — retrying in %ds",
                    path, attempt, _MAX_RETRIES, exc, delay,
                )
                time.sleep(delay)
            else:
                logger.error(
                    "Callback GET %s failed after %d attempts: %s",
                    path, _MAX_RETRIES, exc,
                )
                raise


# ---------------------------------------------------------------------------
# Input download
# ---------------------------------------------------------------------------

def download_input(session_id: str, local_dir: Path) -> Path:
    """Download the must-gather archive from the API and return the local path.

    In PVC mode this is a no-op — the archive is already at *local_dir*.
    """
    if not _is_callback():
        return local_dir

    local_dir.mkdir(parents=True, exist_ok=True)

    resp = _get(f"/callback/input/{session_id}", stream=True, timeout=1800)

    content_disp = resp.headers.get("Content-Disposition", "")
    filename = "archive.tar.gz"
    if "filename=" in content_disp:
        filename = content_disp.split("filename=")[-1].strip('"')

    dest = local_dir / filename
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8 * 1024 * 1024):
            f.write(chunk)

    logger.info("Downloaded input archive to %s (%.2f GB)",
                dest, dest.stat().st_size / (1024 ** 3))
    return dest


# ---------------------------------------------------------------------------
# Status reporting
# ---------------------------------------------------------------------------

def report_status(session_id: str, status: dict, job_dir: Path | None = None) -> None:
    """Report job status.

    In callback mode, POSTs to the API.
    In PVC mode, writes status.json atomically (preserving sticky fields).
    """
    if not _is_callback():
        _write_status_pvc(job_dir, status)
        return

    status["session_id"] = session_id
    try:
        _post("/callback/status", json_body=status)
    except Exception:
        logger.error("Failed to report status for session %s", session_id)


def _write_status_pvc(job_dir: Path, status: dict) -> None:
    """Legacy PVC status write — atomic rename with sticky fields."""
    from datetime import datetime as _dt

    _STICKY_KEYS = ("owner", "problem_statement", "case_number", "deepening_round")
    job_dir.mkdir(parents=True, exist_ok=True)
    final = job_dir / "status.json"
    if final.exists():
        try:
            prev = json.loads(final.read_text(encoding="utf-8"))
            for key in _STICKY_KEYS:
                if key not in status and key in prev:
                    status[key] = prev[key]
        except Exception:
            pass
    status["updated_at"] = _dt.now().isoformat()
    tmp = job_dir / "status.json.tmp"
    tmp.write_text(json.dumps(status, indent=2), encoding="utf-8")
    tmp.rename(final)


# ---------------------------------------------------------------------------
# Result upload
# ---------------------------------------------------------------------------

def upload_results(session_id: str, results_dir: Path,
                   job_results_dir: Path | None = None) -> None:
    """Upload analysis results.

    In callback mode, creates a tar archive and POSTs it to the API.
    In PVC mode, copies files to the job results dir on the shared PVC.
    """
    if not _is_callback():
        _copy_results_pvc(results_dir, job_results_dir)
        return

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for item in results_dir.iterdir():
            tar.add(str(item), arcname=item.name)
    tar_bytes = buf.getvalue()

    _post(
        f"/callback/results/{session_id}",
        data=tar_bytes,
        content_type="application/gzip",
        timeout=600,
    )
    logger.info("Uploaded results for session %s (%d bytes)", session_id, len(tar_bytes))


def _copy_results_pvc(results_dir: Path, job_results_dir: Path) -> None:
    """Legacy PVC result copy."""
    job_results_dir.mkdir(parents=True, exist_ok=True)
    for item in results_dir.iterdir():
        dest = job_results_dir / item.name
        if item.is_file():
            shutil.copy2(item, dest)
        elif item.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(item, dest)


# ---------------------------------------------------------------------------
# Agent memory
# ---------------------------------------------------------------------------

def upload_agent_memory(session_id: str, memory_path: Path,
                        job_dir: Path | None = None) -> None:
    """Upload agent_memory.json to the API (or copy to PVC)."""
    if not memory_path.exists():
        return

    if not _is_callback():
        if job_dir:
            dest = job_dir / "agent_memory.json"
            shutil.copy2(memory_path, dest)
        return

    _post(
        f"/callback/agent-memory/{session_id}",
        data=memory_path.read_bytes(),
        content_type="application/json",
    )
    logger.info("Uploaded agent memory for session %s", session_id)


def download_agent_memory(session_id: str, dest_path: Path,
                          job_dir: Path | None = None) -> None:
    """Download agent_memory.json from the API (or copy from PVC)."""
    if not _is_callback():
        if job_dir:
            src = job_dir / "agent_memory.json"
            if src.exists():
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest_path)
        return

    try:
        resp = _get(f"/callback/agent-memory/{session_id}")
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(resp.content)
        logger.info("Downloaded agent memory for session %s", session_id)
    except Exception:
        logger.warning("No agent memory available for session %s", session_id)


# ---------------------------------------------------------------------------
# Console log streaming
# ---------------------------------------------------------------------------

class ConsoleBuffer:
    """Batches console output and flushes to the API periodically."""

    def __init__(self, session_id: str, flush_interval: float = 5.0,
                 max_buffer: int = 4096):
        self._session_id = session_id
        self._flush_interval = flush_interval
        self._max_buffer = max_buffer
        self._buffer: list[str] = []
        self._buffer_size = 0
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._stopped = False

    def write(self, text: str) -> None:
        if not _is_callback() or not text:
            return

        with self._lock:
            self._buffer.append(text)
            self._buffer_size += len(text)

            if self._buffer_size >= self._max_buffer:
                self._flush_locked()
            elif self._timer is None:
                self._timer = threading.Timer(self._flush_interval, self._flush)
                self._timer.daemon = True
                self._timer.start()

    def _flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

        if not self._buffer or self._stopped:
            return

        text = "".join(self._buffer)
        self._buffer.clear()
        self._buffer_size = 0

        try:
            _post(
                f"/callback/console/{self._session_id}",
                data=text.encode("utf-8"),
                content_type="text/plain",
                timeout=30,
            )
        except Exception:
            pass

    def close(self) -> None:
        with self._lock:
            self._stopped = True
            self._flush_locked()
