"""
Must-Gather Analysis API

Headless API that accepts USER QUERY (and optionally MUST GATHER ROOT FOLDER),
delegates the analysis to a Kubernetes Job running the worker entrypoint,
and returns status/results by reading the shared PVC.

Also serves agent_memory.json, workflow_console.txt, and rca_summary.txt
for the React frontend dashboard (CORS-enabled).

The must-gather root folder is resolved in this order:
  1. API request body  (must_gather_root_folder)  — overrides everything
  2. Config/config.json (must_gather_base_dir)     — default when not in request

Run: uvicorn api:app --host 0.0.0.0 --port 8000
"""

import base64
import html
import io
import json
import logging
import os
import re
import secrets
import shutil
import sys
import tarfile
import threading
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

_root = Path(__file__).resolve().parent
for p in (_root, _root / "core", _root / "machine_learning"):
    p_str = str(p)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

# Import after path setup
from orchestrator_agent import OrchestratorAgent, clear_agent_memory
from app_paths import get_config_path, get_memory_file_path
from utils.utils import create_zip_from_files, create_zip_from_buffers
from utils.causal_dag_image import render_causal_dag_png
from utils.rca_doc_export import append_rca_stage_bundle_entries
import job_runner

logger = logging.getLogger(__name__)

MUST_GATHER_EXTRACT_DIR = Path(os.environ.get("MUST_GATHER_EXTRACT_DIR", "/data/must-gather"))

SHARED_PVC_MOUNT = Path(os.environ.get("SHARED_PVC_MOUNT", "/shared"))

JOBS_CLUSTER_MODE: str = os.environ.get("JOBS_CLUSTER_MODE", "disabled")
LOCAL_SESSION_DIR: str = os.environ.get("LOCAL_SESSION_DIR", "/data/sessions")

if JOBS_CLUSTER_MODE == "enabled":
    UPLOAD_DIR = Path(LOCAL_SESSION_DIR) / "_uploads"
else:
    UPLOAD_DIR = SHARED_PVC_MOUNT / "_uploads"

ADMIN_USERS: set[str] = {
    u.strip().lower()
    for u in os.environ.get("ADMIN_USERS", "").split(",")
    if u.strip()
}


def _get_authenticated_user(request: Request) -> str:
    """Return the username from the OAuth Proxy's X-Forwarded-User header."""
    return request.headers.get("X-Forwarded-User", "").strip()


def _check_admin(request: Request) -> None:
    """Validate that the authenticated user is in the admin list."""
    user = _get_authenticated_user(request)
    if not user or user.lower() not in ADMIN_USERS:
        raise HTTPException(status_code=403, detail="Admin access denied")


app = FastAPI(
    title="Must-Gather Analysis API",
    description="Run root-cause analysis on must-gather data using a user query and must-gather root folder.",
    version="3.0.0",
)


@app.on_event("startup")
def _bootstrap_gcp_credentials() -> None:
    """Use Vertex service_account_key_data from mounted config.json when present."""
    try:
        from llm_rca_agent import ensure_gcp_credentials_from_config
        cred_path = ensure_gcp_credentials_from_config()
        if cred_path:
            logger.info("GCP credentials ready at %s", cred_path)
    except Exception as exc:
        logger.warning("Could not bootstrap GCP credentials from config: %s", exc)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/whoami")
def whoami(request: Request):
    """Return the identity of the authenticated user (from OAuth Proxy headers)."""
    user = _get_authenticated_user(request)
    email = request.headers.get("X-Forwarded-Email", "").strip()
    is_admin = bool(user and user.lower() in ADMIN_USERS)
    return {"name": user or "anonymous", "email": email, "is_admin": is_admin}


# Track the most recently started session so result endpoints that don't
# receive a session_id can fall back to it (backward compatibility).

# Result files written by each session — cleared before a new analysis and by /clear.
_SESSION_RESULT_FILES = [
    "rca_summary.txt",
    "RCA_Tier1.txt", "RCA_Tier2.txt", "RCA_Final.txt",
    "rca_report_summary.txt",
    "workflow_console.txt", "payload.txt",
    "errors_aggregate.json", "resolved_paths.json", "hardcoded_files.json",
]


# Regex: match must-gather archives by filename
# Matches any must-gather archive regardless of separator, prefix, or compression format:
#   must-gather-xxx.tar.gz  must_gather.zip  mustgather-ridge.tar.gz
#   ocp-must-gather.tar.gz  MUST-GATHER.tgz  must-gather.tar.bz2  must-gather.tar
_MG_PATTERN = re.compile(
    r"must[-_]?gather.*\.(zip|tar\.gz|tgz|tar\.bz2|tar)$",
    re.IGNORECASE,
)

# ── Hydra REST API constants (from hydra_client.py) ─────────────
_HYDRA_BASE_URL = "https://access.redhat.com/hydra/rest/cases"
_HYDRA_DL_BASE  = "https://attachments.access.redhat.com/hydra/rest/cases"
_HYDRA_SSO_URL  = "https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token"
_HYDRA_CLIENT_ID     = os.environ.get("HYDRA_CLIENT_ID",     "251e51e5-a80a-4cf4-8543-051aa196e44d")
_HYDRA_CLIENT_SECRET = os.environ.get("HYDRA_CLIENT_SECRET", "qTNR50Z87C5R5OeUIvAQwmWAslr47l3H")

# Optional: set HYDRA_STUB_DIR to a local directory with a .zip/.tar.gz to
# bypass the live Hydra API (useful when VPN is unavailable during dev).
HYDRA_STUB_DIR = os.environ.get("HYDRA_STUB_DIR", "").strip()


def _hydra_fetch_token() -> str:
    """Obtain a short-lived Bearer token from Red Hat SSO."""
    body = urlencode({
        "grant_type": "client_credentials",
        "scope": "api.customer_case_management",
    }).encode("utf-8")
    b64 = base64.standard_b64encode(
        f"{_HYDRA_CLIENT_ID}:{_HYDRA_CLIENT_SECRET}".encode()
    ).decode("ascii")
    req = urllib.request.Request(
        _HYDRA_SSO_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {b64}",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())["access_token"]


def _hydra_list_attachments(case_number: str, token: str) -> list:
    """Return the list of attachment dicts for *case_number*."""
    url = f"{_HYDRA_BASE_URL}/{case_number}/attachments"
    req = urllib.request.Request(
        url, method="GET",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
    if isinstance(data, list):
        return data
    for key in ("data", "attachments", "items", "results"):
        if isinstance(data.get(key), list):
            return data[key]
    return []


def _hydra_attachment_uuid(item: dict) -> str | None:
    """Extract the download UUID from an attachment dict."""
    for key in ("uuid", "attachmentId", "id", "downloadId", "Id"):
        val = item.get(key)
        if val is not None:
            return str(val).strip()
    return None


def _hydra_download(case_number: str, uuid: str, token: str, output_path: str) -> None:
    """Stream the attachment to *output_path* in 1 MB chunks."""
    url = f"{_HYDRA_DL_BASE}/{case_number}/attachments/{uuid}"
    req = urllib.request.Request(
        url, method="GET",
        headers={"Authorization": f"Bearer {token}"},
    )
    logger.info("[Hydra] streaming %s → %s", url, output_path)
    with urllib.request.urlopen(req, timeout=1800) as resp:
        with open(output_path, "wb") as fh:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                fh.write(chunk)


class AnalyzeResponse(BaseModel):
    """Response from /analyze and /case-attachments."""

    status: str
    session_id: str | None = None
    rca_summary: str | None = None
    error: str | None = None
    compression_ratio: float | None = None
    total_yaml_bytes: int | None = None
    total_log_bytes: int | None = None
    payload_bytes: int | None = None


class AnalysisCancelledError(Exception):
    """Raised inside progress_callback when the user cancels the analysis."""


class CaseAttachmentRequest(BaseModel):
    """Request body for POST /case-attachments."""
    case_number: str = Field(..., description="Red Hat support case number (e.g. 01234567)")
    attachment_uuid: str | None = Field(None, description="UUID of a specific attachment to download (from a prior 'multiple' response)")
    filename: str | None = Field(None, description="Filename matching the attachment_uuid (used as the temp download name)")


def _hydra_list_mg_attachments(case_number: str) -> list[dict]:
    """Return all must-gather attachments for *case_number* WITHOUT downloading.

    Each entry: {uuid, filename, size_bytes, created}.
    Handles HYDRA_STUB_DIR for offline testing.
    """
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    # ── Offline stub ──────────────────────────────────────────────────────────
    if HYDRA_STUB_DIR:
        stub_path = Path(HYDRA_STUB_DIR)
        if not stub_path.is_dir():
            raise RuntimeError(f"HYDRA_STUB_DIR '{HYDRA_STUB_DIR}' is not a directory.")
        candidates = sorted(
            [p for p in stub_path.iterdir() if p.is_file() and _MG_PATTERN.search(p.name)],
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        if not candidates:
            candidates = sorted(
                [p for p in stub_path.iterdir()
                 if p.is_file() and (p.suffix == ".zip" or p.name.endswith(".tar.gz"))],
                key=lambda p: p.stat().st_mtime, reverse=True,
            )
        return [
            {
                "uuid": p.name,   # filename doubles as stub UUID
                "filename": p.name,
                "size_bytes": p.stat().st_size,
                "created": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d"),
            }
            for p in candidates
        ]

    # ── Live Hydra ────────────────────────────────────────────────────────────
    logger.info("[Hydra] fetching SSO token for case %s", case_number)
    token = _hydra_fetch_token()
    logger.info("[Hydra] listing attachments for case %s", case_number)
    raw_items = _hydra_list_attachments(case_number, token)

    result = []
    for item in raw_items:
        fname = item.get("fileName") or item.get("name") or ""
        if not _MG_PATTERN.search(fname):
            continue
        uuid = _hydra_attachment_uuid(item)
        if not uuid:
            continue
        result.append({
            "uuid": uuid,
            "filename": fname or f"must-gather-{case_number}.tar.gz",
            "size_bytes": item.get("fileSize") or item.get("size") or 0,
            "created": item.get("createdDate") or item.get("created") or "",
        })

    logger.info("[Hydra] found %d must-gather attachment(s) for case %s", len(result), case_number)
    return result


def _hydra_download_specific(case_number: str, attachment_uuid: str, filename: str) -> tuple[str, str]:
    """Download one attachment by UUID into UPLOAD_DIR.

    Returns (local_file_id, filename).
    In stub mode, attachment_uuid is the filename inside HYDRA_STUB_DIR.
    """
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    # ── Offline stub ──────────────────────────────────────────────────────────
    if HYDRA_STUB_DIR:
        stub_path = Path(HYDRA_STUB_DIR)
        src = stub_path / attachment_uuid
        if not src.is_file():
            raise RuntimeError(f"Stub file '{attachment_uuid}' not found in HYDRA_STUB_DIR.")
        suffix = _archive_suffix(src.name)
        local_file_id = f"upload_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        dest = UPLOAD_DIR / f"{local_file_id}{suffix}"
        shutil.copy2(str(src), str(dest))
        logger.info("[Stub] %s → %s", src.name, dest)
        return local_file_id, src.name

    # ── Live Hydra ────────────────────────────────────────────────────────────
    token = _hydra_fetch_token()
    safe_name = re.sub(r'[^\w.\-]', '_', filename) or f"must-gather-{case_number}.tar.gz"
    tmp_dir = Path(f"/tmp/case_{case_number}")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    output_path = str(tmp_dir / safe_name)
    _hydra_download(case_number, attachment_uuid, token, output_path)

    downloaded = Path(output_path)
    suffix = _archive_suffix(downloaded.name)
    local_file_id = f"upload_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    dest = UPLOAD_DIR / f"{local_file_id}{suffix}"
    shutil.move(str(downloaded), str(dest))
    logger.info("[Hydra] case %s uuid %s → %s", case_number, attachment_uuid, dest)
    return local_file_id, filename


# ── Background download tracking (file-based, shared across worker processes) ─
if JOBS_CLUSTER_MODE == "enabled":
    _DOWNLOAD_STATUS_DIR = Path(LOCAL_SESSION_DIR) / "_download_status"
else:
    _DOWNLOAD_STATUS_DIR = SHARED_PVC_MOUNT / "_download_status"
_DOWNLOAD_STATUS_DIR.mkdir(parents=True, exist_ok=True)


def _dl_status_path(download_id: str) -> Path:
    return _DOWNLOAD_STATUS_DIR / f"{download_id}.json"


def _write_dl_status(download_id: str, data: dict) -> None:
    p = _dl_status_path(download_id)
    p.write_text(json.dumps(data), encoding="utf-8")


def _read_dl_status(download_id: str) -> dict | None:
    p = _dl_status_path(download_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _start_background_download(
    download_id: str, case_number: str, attachment_uuid: str, filename: str,
) -> None:
    """Spawn a daemon thread that downloads the Hydra attachment in the background."""
    def _worker():
        try:
            local_file_id, fname = _hydra_download_specific(
                case_number, attachment_uuid, filename,
            )
            _write_dl_status(download_id, {
                "status": "downloaded",
                "local_file_id": local_file_id,
                "filename": fname,
            })
            logger.info("[BgDownload] %s finished → %s", download_id, local_file_id)
        except Exception as exc:
            logger.exception("[BgDownload] %s failed", download_id)
            _write_dl_status(download_id, {
                "status": "error",
                "detail": str(exc),
            })

    _write_dl_status(download_id, {
        "status": "downloading",
        "filename": filename,
    })
    t = threading.Thread(target=_worker, daemon=True)
    t.start()


@app.get("/download-status/{download_id}")
def get_download_status(download_id: str):
    """Poll the status of a background Hydra download."""
    entry = _read_dl_status(download_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Unknown download_id")
    return entry


@app.post("/case-attachments")
def fetch_case_attachment(request: CaseAttachmentRequest):
    """Fetch must-gather archive(s) for a support case via Hydra.

    Downloads are kicked off in a background thread so the HTTP response
    returns immediately.  The frontend polls ``GET /download-status/{id}``
    until the download finishes.

    * No ``attachment_uuid`` → list must-gather archives, auto-download if 1.
    * With ``attachment_uuid`` → download that specific archive.

    Both download paths return ``{status: "downloading", download_id, filename}``
    and the frontend polls for completion.
    """
    case_number = (request.case_number or "").strip()
    if not case_number:
        raise HTTPException(status_code=400, detail="case_number is required")

    # ── Mode 2: user chose a specific attachment ──────────────────────────────
    if request.attachment_uuid:
        fname = (request.filename or f"must-gather-{case_number}.tar.gz").strip()
        download_id = f"dl_{secrets.token_hex(8)}"
        _start_background_download(download_id, case_number, request.attachment_uuid.strip(), fname)
        return {"status": "downloading", "download_id": download_id, "filename": fname}

    # ── Mode 1: list must-gather attachments ──────────────────────────────────
    try:
        mg_items = _hydra_list_mg_attachments(case_number)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise HTTPException(
                status_code=404,
                detail=f"Case {case_number} not found in Hydra. "
                       "Please verify the case number is correct and that the Hydra credentials have access.",
            )
        logger.exception("Hydra HTTP error for case %s: %s", case_number, exc)
        raise HTTPException(status_code=502, detail=f"Hydra API returned HTTP {exc.code}: {exc.reason}")
    except Exception as exc:
        logger.exception("Attachment listing failed for case %s", case_number)
        raise HTTPException(status_code=500, detail=f"Listing failed: {exc}")

    if not mg_items:
        raise HTTPException(
            status_code=404,
            detail=f"No must-gather archive (.zip / .tar.gz) found for case {case_number}.",
        )

    if len(mg_items) > 1:
        return {"status": "multiple", "attachments": mg_items}

    # Single match — kick off background download and return immediately
    item = mg_items[0]
    download_id = f"dl_{secrets.token_hex(8)}"
    _start_background_download(download_id, case_number, item["uuid"], item["filename"])
    return {"status": "downloading", "download_id": download_id, "filename": item["filename"]}

def _load_must_gather_base_dir_from_config() -> str:
    """Read must_gather_base_dir from Config/config.json. Returns '' if not set."""
    try:
        cfg_path = get_config_path()
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            value = (cfg.get("must_gather_base_dir") or "").strip()
            if value and not os.path.isabs(value):
                value = str((_root / value).resolve())
            return value
    except Exception:
        pass
    return ""


def _get_session_base_dir(session_id: str) -> str:
    """Read must_gather_base_dir stored in agent_memory for a given session.

    The orchestrator stores the resolved base dir in
    session['results']['must_gather_base_dir'] at the end of execute_workflow.
    This is the source of truth for the feedback deepening round.
    """
    try:
        mem_path = get_memory_file_path()
        if mem_path.exists():
            with open(mem_path, "r", encoding="utf-8") as f:
                mem = json.load(f)
            for s in mem.get("sessions", []):
                if s.get("session_id") == session_id:
                    return (s.get("results", {}).get("must_gather_base_dir") or "").strip()
    except Exception:
        pass
    return ""


def _resolve_session_id(session_id: str | None) -> str | None:
    """Return the effective session_id. No fallback — caller must provide it."""
    return session_id or None


def _restore_agent_memory_from_pvc(session_id: str) -> bool:
    """Copy agent_memory.json from the shared PVC job directory into the API
    pod's Config/ so OrchestratorAgent can find the session.

    Returns True if the file was restored.
    """
    src = job_runner.get_job_dir(session_id) / "agent_memory.json"
    if not src.exists():
        return False
    dest = get_memory_file_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    logger.info("Restored agent_memory.json from PVC for session %s", session_id)
    return True


def _job_results_dir(session_id: str) -> Path:
    """Return the results directory for a session on the shared PVC."""
    return job_runner.get_job_dir(session_id) / "results"


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

def _cleanup_session_extract(must_gather_base_dir: str) -> None:
    """Delete the extracted must-gather directory for an uploaded session.

    The archive is extracted to MUST_GATHER_EXTRACT_DIR/{api_session_id}/ and
    then the inner folder (the actual must-gather root) is passed as
    must_gather_base_dir.  We climb one level up from must_gather_base_dir: if
    that parent lives under MUST_GATHER_EXTRACT_DIR, we remove the whole
    session subtree.
    """
    if not must_gather_base_dir:
        return
    try:
        base = Path(must_gather_base_dir).resolve()
        parent = base.parent
        extract_root = MUST_GATHER_EXTRACT_DIR.resolve()
        # Only delete if the directory is actually inside our managed extract root
        if str(parent).startswith(str(extract_root)) and parent != extract_root:
            if parent.exists():
                shutil.rmtree(parent, ignore_errors=True)
                logger.info("Cleaned up extracted must-gather data: %s", parent)
        elif str(base).startswith(str(extract_root)) and base != extract_root:
            # Fallback: base itself is the session directory
            if base.exists():
                shutil.rmtree(base, ignore_errors=True)
                logger.info("Cleaned up extracted must-gather data: %s", base)
    except Exception:
        pass


class AnalyzeRequest(BaseModel):
    """Request body for /analyze."""

    user_query: str = Field(..., description="User problem statement or query for analysis")
    must_gather_root_folder: str = Field(
        default="",
        description="Path to the must-gather root folder (YAML/logs). "
                    "Optional — if omitted, the value from Config/config.json 'must_gather_base_dir' is used.",
    )
    local_file_id: str = Field(
        default="",
        description="Local file ID returned by /upload-must-gather. "
                    "The archive is extracted directly from the pod's local storage.",
    )
    case_number: str = Field(
        default="",
        description="Optional support case number to associate with this analysis session (e.g. CS-12345).",
    )
    owner: str = Field(
        default="",
        description="Name of the user submitting this job (used for session ownership).",
    )


class FeedbackRequest(BaseModel):
    session_id: str
    satisfactory: bool
    feedback_text: str = ""
    original_session_id: str = ""


# ---------------------------------------------------------------------------
# Archive helpers
# ---------------------------------------------------------------------------

_ALLOWED_ARCHIVE_SUFFIXES = (".zip", ".tar.gz")


def _archive_suffix(filename: str) -> str:
    """Return the recognised archive suffix for *filename*, or '' if none."""
    lower = filename.lower()
    if lower.endswith(".tar.gz"):
        return ".tar.gz"
    if lower.endswith(".tar.bz2"):
        return ".tar.bz2"
    if lower.endswith(".tar.xz"):
        return ".tar.xz"
    if lower.endswith(".tgz"):
        return ".tgz"
    if lower.endswith(".tar"):
        return ".tar"
    if lower.endswith(".zip"):
        return ".zip"
    return ""


def _resolve_root(extract_base: Path) -> str:
    """If the extraction produced a single top-level directory, return it;
    otherwise return *extract_base* itself."""
    children = [p for p in extract_base.iterdir() if p.is_dir()]
    if len(children) == 1:
        return str(children[0])
    return str(extract_base)


def _extract_zip_to(zip_path: Path, extract_base: Path) -> str:
    extract_base.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_base)
        return _resolve_root(extract_base)
    except Exception:
        if extract_base.exists():
            shutil.rmtree(extract_base, ignore_errors=True)
        raise


def _extract_tar_to(tar_path: Path, extract_base: Path) -> str:
    extract_base.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(tar_path, "r:*") as tf:
            tf.extractall(extract_base, filter="data")
        return _resolve_root(extract_base)
    except Exception:
        if extract_base.exists():
            shutil.rmtree(extract_base, ignore_errors=True)
        raise


def _extract_archive_to(archive_path: Path, extract_base: Path) -> str:
    """Detect archive type and extract into *extract_base*."""
    if zipfile.is_zipfile(archive_path):
        return _extract_zip_to(archive_path, extract_base)
    try:
        tarfile.open(archive_path, "r:*").close()
        return _extract_tar_to(archive_path, extract_base)
    except tarfile.TarError:
        pass
    raise ValueError(f"Unrecognised archive format: {archive_path.name}")


def _extract_local_upload(local_file_id: str, job_input_dir: Path) -> str:
    """Extract a locally saved upload archive into the job's input directory.

    Deletes the archive file after extraction.
    """
    candidates = sorted(UPLOAD_DIR.glob(f"{local_file_id}.*"))
    if not candidates:
        raise FileNotFoundError(f"Upload {local_file_id} not found")
    archive_path = candidates[0]
    try:
        return _extract_archive_to(archive_path, job_input_dir)
    finally:
        archive_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    """Health check for load balancers and monitoring."""
    return {"status": "ok"}


@app.get("/pod-status")
def pod_status():
    """Return active K8s analysis jobs instead of per-pod busy flag."""
    try:
        active = job_runner.list_active_jobs()
    except Exception:
        active = []
    return {
        "busy": len(active) > 0,
        "active_session_id": active[0]["session_id"] if active else None,
        "active_jobs": active,
    }


@app.get("/sessions")
def list_sessions(owner: str = Query(default="")):
    """Return sessions backed by K8s Jobs, optionally filtered by owner."""
    try:
        return job_runner.list_all_jobs(owner=owner.strip() or None)
    except Exception:
        logger.exception("Failed to list sessions")
        return []


@app.post("/clear-sessions")
def clear_sessions_history():
    """Remove all completed / error / cancelled sessions from K8s and PVC.

    Active (running) sessions are preserved.
    """
    try:
        all_jobs = job_runner.list_all_jobs()
    except Exception:
        logger.exception("Failed to list jobs for cleanup")
        raise HTTPException(status_code=500, detail="Failed to list sessions")

    removed = 0
    for entry in all_jobs:
        if entry.get("status") in ("completed", "error", "cancelled"):
            sid = entry["session_id"]
            try:
                job_runner.cleanup_job(sid)
                removed += 1
            except Exception:
                logger.warning("Failed to clean up session %s", sid)

    return {"removed": removed}


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

@app.post("/admin/validate")
def admin_validate(request: Request):
    """Check if the current user is an admin (via OAuth Proxy headers)."""
    user = _get_authenticated_user(request)
    is_admin = bool(user and user.lower() in ADMIN_USERS)
    return {"valid": is_admin, "user": user}


@app.get("/admin/stats")
def admin_stats(request: Request):
    """Return aggregate stats for the admin dashboard."""
    _check_admin(request)
    all_jobs = job_runner.list_all_jobs()

    total = len(all_jobs)
    running = sum(1 for j in all_jobs if j["status"] in ("running", "in_progress", "queued"))
    completed = sum(1 for j in all_jobs if j["status"] in ("completed", "rca_completed"))
    failed = sum(1 for j in all_jobs if j["status"] in ("error", "failed"))

    pvc_usage = {}
    try:
        usage = shutil.disk_usage(str(SHARED_PVC_MOUNT))
        pvc_usage = {
            "total_gb": round(usage.total / (1024 ** 3), 2),
            "used_gb": round(usage.used / (1024 ** 3), 2),
            "free_gb": round(usage.free / (1024 ** 3), 2),
            "used_pct": round(usage.used / usage.total * 100, 1) if usage.total else 0,
        }
    except Exception:
        pass

    active_pods = 0
    try:
        active_pods = len(job_runner.list_active_jobs())
    except Exception:
        pass

    owners: dict[str, int] = {}
    for j in all_jobs:
        o = j.get("owner", "unknown")
        owners[o] = owners.get(o, 0) + 1

    return {
        "total": total,
        "running": running,
        "completed": completed,
        "failed": failed,
        "active_pods": active_pods,
        "pvc_usage": pvc_usage,
        "owners": owners,
    }


@app.get("/admin/sessions")
def admin_sessions(request: Request):
    """Return ALL sessions (unfiltered) for admin view."""
    _check_admin(request)
    return job_runner.list_all_jobs()


@app.post("/admin/cancel/{session_id}")
def admin_cancel(session_id: str, request: Request):
    """Admin: cancel any running job."""
    _check_admin(request)
    return job_runner.cancel_job(session_id)


@app.post("/admin/delete/{session_id}")
def admin_delete(session_id: str, request: Request):
    """Admin: delete any session and its data."""
    _check_admin(request)
    job_runner.cleanup_job(session_id)
    return {"status": "deleted", "session_id": session_id}


@app.post("/upload-must-gather")
async def upload_must_gather(file: UploadFile = File(...)):
    """Receive a must-gather archive from the frontend and save it locally.

    Accepted formats: .zip, .tar.gz

    Returns a local_file_id that the client passes to /analyze.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    suffix = _archive_suffix(file.filename)
    if not suffix:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Accepted formats: {', '.join(_ALLOWED_ARCHIVE_SUFFIXES)}",
        )

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    local_file_id = f"upload_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    local_path = UPLOAD_DIR / f"{local_file_id}{suffix}"

    try:
        with open(local_path, "wb") as f:
            while chunk := await file.read(8 * 1024 * 1024):
                f.write(chunk)
    except Exception as e:
        if local_path.exists():
            local_path.unlink(missing_ok=True)
        logger.exception("Failed to save uploaded must-gather locally")
        raise HTTPException(status_code=500, detail=f"Failed to save file: {e}")

    return {"local_file_id": local_file_id, "filename": file.filename}


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest, req: Request = None):
    """
    Start a must-gather analysis by creating a Kubernetes Job.

    - **user_query**: Problem statement or question.
    - **must_gather_root_folder** *(optional)*: Absolute path to the folder containing must-gather data.
      If omitted, the value from `Config/config.json` -> `must_gather_base_dir` is used.
    - **local_file_id** *(optional)*: ID returned by /upload-must-gather.
    """
    user_query = (request.user_query or "").strip()
    if not user_query:
        raise HTTPException(status_code=400, detail="user_query is required and cannot be empty")

    local_file_id = (request.local_file_id or "").strip()

    session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(4)}"

    job_dir = job_runner.get_job_dir(session_id)
    job_input_dir = job_dir / "input"
    job_input_dir.mkdir(parents=True, exist_ok=True)

    if local_file_id:
        # Move the raw archive into the job input dir — the worker pod
        # will extract it (avoids blocking this HTTP request for minutes).
        candidates = sorted(UPLOAD_DIR.glob(f"{local_file_id}.*"))
        if not candidates:
            raise HTTPException(status_code=404, detail=f"Upload '{local_file_id}' not found or expired")
        archive_path = candidates[0]
        dest_archive = job_input_dir / archive_path.name
        try:
            shutil.move(str(archive_path), str(dest_archive))
        except Exception as e:
            logger.exception("Failed to move archive %s to job dir", archive_path)
            raise HTTPException(status_code=500, detail=f"Failed to prepare upload: {e}")
        must_gather_base_dir = str(dest_archive)
        logger.info("Moved archive %s → %s (extraction deferred to worker)", archive_path.name, dest_archive)
    else:
        must_gather_root_folder = (request.must_gather_root_folder or "").strip()
        if not must_gather_root_folder:
            must_gather_root_folder = _load_must_gather_base_dir_from_config()

        if not must_gather_root_folder:
            raise HTTPException(
                status_code=400,
                detail="must_gather_root_folder was not provided in the request and "
                       "'must_gather_base_dir' is not set in Config/config.json.",
            )

        root_path = Path(must_gather_root_folder)
        if not root_path.exists():
            raise HTTPException(
                status_code=400,
                detail=f"must_gather_root_folder does not exist: {must_gather_root_folder}",
            )
        if not root_path.is_dir():
            raise HTTPException(
                status_code=400,
                detail=f"must_gather_root_folder must be a directory: {must_gather_root_folder}",
            )

        resolved = root_path.resolve()

        if not any(os.listdir(resolved)):
            raise HTTPException(
                status_code=400,
                detail=f"must_gather_root_folder is empty: {must_gather_root_folder}. "
                       "Please upload a must-gather archive instead.",
            )

        shared_pvc = Path(SHARED_PVC_MOUNT).resolve()
        if not str(resolved).startswith(str(shared_pvc)):
            logger.info(
                "must_gather_root_folder %s is not on shared PVC (%s); "
                "copying to job input dir %s",
                resolved, shared_pvc, job_input_dir,
            )
            dest = job_input_dir / resolved.name
            try:
                shutil.copytree(str(resolved), str(dest), dirs_exist_ok=True)
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to copy must-gather data to shared PVC: {e}",
                )
            must_gather_base_dir = str(dest)
        else:
            must_gather_base_dir = str(resolved)

    owner = (_get_authenticated_user(req) if req else "") or (request.owner or "").strip() or "unknown"

    # Write initial status.json before creating the Job so the worker
    # inherits problem_statement / case_number / owner from the start.
    status_file = job_dir / "status.json"
    status_file.write_text(json.dumps({
        "session_id": session_id,
        "status": "pending",
        "phase": "queued",
        "progress": 0,
        "problem_statement": user_query,
        "case_number": getattr(request, "case_number", "") or "",
        "owner": owner,
    }, indent=2), encoding="utf-8")

    archive_path = Path(must_gather_base_dir) if Path(must_gather_base_dir).is_file() else None

    try:
        job_runner.create_analysis_job(
            session_id=session_id,
            user_query=user_query,
            must_gather_base_dir=must_gather_base_dir,
            owner=owner,
            archive_local_path=archive_path,
        )
    except Exception as e:
        logger.exception("Failed to create K8s Job for session %s", session_id)
        raise HTTPException(status_code=500, detail=f"Failed to create analysis job: {e}")

    return AnalyzeResponse(
        status="started",
        session_id=session_id,
    )


@app.get("/status/{session_id}")
def get_status(session_id: str):
    """Check workflow status for a running or completed session.

    Reads status.json from the shared PVC and cross-checks the K8s Job phase.
    """
    return job_runner.get_job_status(session_id)


@app.post("/cancel/{session_id}")
def cancel_analysis(session_id: str):
    """Cancel a running analysis by deleting its Kubernetes Job."""
    try:
        return job_runner.cancel_job(session_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to cancel job: {e}")



# ---------------------------------------------------------------------------
# Endpoints for React frontend dashboard
# ---------------------------------------------------------------------------

@app.get("/agent-memory")
def get_agent_memory(session_id: str | None = Query(default=None)):
    """Return agent_memory.json scoped to a specific session on the PVC."""
    if session_id:
        pvc_mem = job_runner.get_job_dir(session_id) / "agent_memory.json"
        if pvc_mem.exists():
            try:
                with open(pvc_mem, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass

    return JSONResponse(content={"sessions": [], "metadata": {}})


@app.get("/workflow-console", response_class=PlainTextResponse)
def get_workflow_console(session_id: str | None = Query(default=None), session: str | None = Query(default=None)):
    """Return workflow_console.txt from the job's results directory."""
    sid = _resolve_session_id(session_id or session)
    if sid:
        console_path = _job_results_dir(sid) / "workflow_console.txt"
        if console_path.exists():
            try:
                return PlainTextResponse(console_path.read_text(encoding="utf-8"))
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to read workflow_console.txt: {e}")

    return PlainTextResponse("No workflow console output yet.", status_code=200)


@app.get("/rca-summary", response_class=PlainTextResponse)
def get_rca_summary(session_id: str | None = Query(default=None), session: str | None = Query(default=None)):
    """Return rca_summary.txt from the job's results directory."""
    sid = _resolve_session_id(session_id or session)
    if sid:
        rca_path = _job_results_dir(sid) / "rca_summary.txt"
        if rca_path.exists():
            try:
                return PlainTextResponse(rca_path.read_text(encoding="utf-8"))
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to read rca_summary.txt: {e}")

    return PlainTextResponse("No RCA summary available yet.", status_code=200)


_STAGE_TO_FILE = {
    "Tier_1": "RCA_Tier1.txt",
    "Tier_2": "RCA_Tier2.txt",
    "Final":  "RCA_Final.txt",
}


@app.get("/rca-stage/{stage}", response_class=PlainTextResponse)
def get_rca_stage(stage: str, session_id: str | None = Query(default=None), session: str | None = Query(default=None)):
    """Return an RCA stage file (Tier_1 / Tier_2 / Final) from the job's results."""
    filename = _STAGE_TO_FILE.get(stage)
    if not filename:
        raise HTTPException(status_code=400, detail=f"Invalid stage '{stage}'. Must be one of: {', '.join(_STAGE_TO_FILE)}")

    sid = _resolve_session_id(session_id or session)
    if sid:
        rca_path = _job_results_dir(sid) / filename
        if rca_path.exists():
            try:
                return PlainTextResponse(rca_path.read_text(encoding="utf-8"))
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to read {filename}: {e}")

    return PlainTextResponse("", status_code=204)


@app.get("/rca-dag/{stage}")
def get_rca_dag(stage: str, session_id: str | None = Query(default=None), session: str | None = Query(default=None)):
    """Return the causal-DAG JSON for an RCA stage (Tier_1 / Tier_2 / Final).

    Returns 204 with no body if the DAG file doesn't exist yet — either the
    session predates this feature, or DAG generation failed (best-effort,
    non-blocking; never breaks the core RCA flow).
    """
    filename = _STAGE_TO_FILE.get(stage)
    if not filename:
        raise HTTPException(status_code=400, detail=f"Invalid stage '{stage}'. Must be one of: {', '.join(_STAGE_TO_FILE)}")

    dag_filename = filename.replace(".txt", "_dag.json")
    sid = _resolve_session_id(session_id or session)
    if sid:
        dag_path = _job_results_dir(sid) / dag_filename
        if dag_path.exists():
            try:
                return JSONResponse(content=json.loads(dag_path.read_text(encoding="utf-8")))
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to read {dag_filename}: {e}")

    return PlainTextResponse("", status_code=204)


@app.get("/rca-report-summary", response_class=PlainTextResponse)
def get_rca_report_summary(session_id: str | None = Query(default=None), session: str | None = Query(default=None)):
    """Return the pre-generated RCA report summary."""
    sid = _resolve_session_id(session_id or session)
    if sid:
        summary_path = _job_results_dir(sid) / "rca_report_summary.txt"
        if summary_path.exists():
            try:
                return PlainTextResponse(summary_path.read_text(encoding="utf-8"))
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to read rca_report_summary.txt: {e}")

    return PlainTextResponse("", status_code=204)


_RCA_STAGE_FILES = [
    ("RCA_Tier1.txt", "RCA_Tier_1.doc"),
    ("RCA_Tier2.txt", "RCA_Tier_2.doc"),
    ("RCA_Final.txt", "RCA_Final.doc"),
]


def _load_stage_dag_png(results_dir: Path, disk_name: str) -> bytes | None:
    """Render the causal-DAG PNG for an RCA stage text file, if JSON exists."""
    dag_path = results_dir / disk_name.replace(".txt", "_dag.json")
    if not (dag_path.is_file() and dag_path.stat().st_size > 0):
        return None
    try:
        dag = json.loads(dag_path.read_text(encoding="utf-8"))
        return render_causal_dag_png(dag)
    except Exception as exc:
        logger.warning("Skipping causal DAG embed for %s: %s", dag_path.name, exc)
        return None


def _build_word_doc_html(title: str, body_text: str) -> str:
    """Plain Word HTML without causal DAG (used by non-stage docs)."""
    from utils.rca_doc_export import build_rca_stage_doc_html
    return build_rca_stage_doc_html(title, body_text)


def _build_word_doc_html_rich(title: str, body_html: str) -> str:
    """Build a Word-compatible HTML document with pre-built HTML body content."""
    safe_title = html.escape(title)
    return f"""<!DOCTYPE html>
<html xmlns:o="urn:schemas-microsoft-com:office:office"
xmlns:w="urn:schemas-microsoft-com:office:word"
xmlns="http://www.w3.org/TR/REC-html40">
<head>
  <meta charset="utf-8" />
  <title>{safe_title}</title>
  <style>
    body {{
      font-family: Arial, sans-serif;
      font-size: 11pt;
      line-height: 1.5;
      margin: 1in;
      color: #1f2937;
    }}
    h1 {{ font-size: 18pt; color: #111827; margin: 0 0 6px 0; }}
    h2 {{ font-size: 13pt; color: #1e3a5f; margin: 22px 0 8px 0; border-bottom: 1px solid #e2e8f0; padding-bottom: 4px; }}
    h3 {{ font-size: 11pt; color: #334155; margin: 16px 0 6px 0; }}
    p  {{ margin: 6px 0; }}
    .subtitle {{ font-size: 10pt; color: #64748b; margin: 0 0 20px 0; }}
    table {{ border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 10pt; }}
    th {{ background: #f1f5f9; border: 1px solid #cbd5e1; padding: 7px 10px; text-align: left; font-weight: 600; color: #475569; }}
    td {{ border: 1px solid #e2e8f0; padding: 6px 10px; color: #374151; }}
    tr:nth-child(even) td {{ background: #f8fafc; }}
    .badge-high {{ color: #b91c1c; font-weight: 600; }}
    .badge-medium {{ color: #d97706; font-weight: 600; }}
    .badge-low {{ color: #475569; font-weight: 600; }}
    .not-found {{ color: #9ca3af; font-style: italic; }}
  </style>
</head>
<body>
  <h1>{safe_title}</h1>
  {body_html}
</body>
</html>"""


@app.get("/rca-bundle-zip")
def get_rca_bundle_zip(session_id: str | None = Query(default=None), session: str | None = Query(default=None)):
    """Return a ZIP containing each available RCA stage as a .doc document."""
    sid = _resolve_session_id(session_id or session)
    if not sid:
        raise HTTPException(status_code=400, detail="session_id is required")
    results_dir = _job_results_dir(sid)

    doc_entries: list[tuple[str, str | bytes]] = []
    for disk_name, arc_name in _RCA_STAGE_FILES:
        src = results_dir / disk_name
        if not (src.is_file() and src.stat().st_size > 0):
            continue
        text = src.read_text(encoding="utf-8", errors="replace")
        title = arc_name.rsplit(".", 1)[0].replace("_", " ")
        dag_png = _load_stage_dag_png(results_dir, disk_name)
        append_rca_stage_bundle_entries(
            doc_entries,
            arc_prefix="",
            doc_filename=arc_name,
            title=title,
            body_text=text,
            dag_png=dag_png,
        )

    if not doc_entries:
        raise HTTPException(status_code=404, detail="No RCA stage files are available yet.")

    buf = create_zip_from_buffers(doc_entries)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="RCA_Reports_Bundle.zip"'},
    )


def _extract_resolution_section(rca_text: str) -> str:
    """Extract the Resolution/Remediation section from an RCA stage file."""
    if not rca_text:
        return ""
    cleaned = re.sub(r"\n*={4,}[\t ]*\n[\t ]*LLM API TOKEN[\s\S]*$", "", rca_text)
    sections: dict[str, str] = {}
    lines = cleaned.split("\n")
    current = ""
    buf: list[str] = []
    for line in lines:
        m = re.match(r"^##\s+(.+)", line)
        if m:
            if current:
                sections[current.lower()] = "\n".join(buf).strip()
            current = m.group(1).strip()
            buf = []
        else:
            buf.append(line)
    if current:
        sections[current.lower()] = "\n".join(buf).strip()
    for key, val in sections.items():
        norm = re.sub(r"[^a-z]", "", key)
        if norm.startswith("resolution") or norm.startswith("remediation") or norm.startswith("recommendation"):
            return val
    return ""


def _build_rca_summary_doc_html(exec_summary_text: str, resolution_md: str) -> str:
    """Build the RCA Summary .doc body: Issue -> Resolution -> Summary."""
    sections: dict[str, str] = {}
    lines = exec_summary_text.split("\n")
    current = ""
    buf: list[str] = []
    for line in lines:
        m = re.match(r"^##\s+(.+)", line)
        if m:
            if current:
                sections[current.lower()] = "\n".join(buf).strip()
            current = m.group(1).strip()
            buf = []
        else:
            buf.append(line)
    if current:
        sections[current.lower()] = "\n".join(buf).strip()

    issue_text = sections.get("issue", "")
    summary_text = sections.get("summary", "")
    body_parts: list[str] = []
    if issue_text:
        body_parts.append(f'<h2>Issue</h2>\n<p>{html.escape(issue_text).replace(chr(10), "<br/>")}</p>')
    if resolution_md:
        body_parts.append(f'<h2>Resolution</h2>\n<p>{html.escape(resolution_md).replace(chr(10), "<br/>")}</p>')
    if summary_text:
        body_parts.append(f'<h2>Summary</h2>\n<p>{html.escape(summary_text).replace(chr(10), "<br/>")}</p>')
    return _build_word_doc_html_rich("RCA Summary", "\n".join(body_parts))


def _read_latest_session() -> dict:
    """Read the latest session from agent_memory.json."""
    mem_path = get_memory_file_path()
    if not mem_path.exists():
        return {}
    try:
        with open(mem_path, "r", encoding="utf-8") as f:
            mem = json.load(f)
        sessions = mem.get("sessions", [])
        return sessions[-1] if sessions else {}
    except Exception:
        return {}


def _read_session_by_id(session_id: str | None) -> dict:
    """Read a specific session by ID from agent_memory.json."""
    if not session_id:
        return _read_latest_session()
    mem_path = get_memory_file_path()
    if not mem_path.exists():
        return {}
    try:
        with open(mem_path, "r", encoding="utf-8") as f:
            mem = json.load(f)
        for s in reversed(mem.get("sessions", [])):
            if s.get("session_id") == session_id:
                return s
        return _read_latest_session()
    except Exception:
        return {}


def _fmt_kb(b: int) -> str:
    return f"{round(b / 1024, 1)} KB" if b else "0 KB"


def _fmt_mb(b: int) -> str:
    return f"{round(b / 1_000_000, 2)} MB" if b else "0 MB"


def _tok(n: int) -> str:
    return f"{n:,}" if n else "-"


def _usd(v: float) -> str:
    return f"${v:.6f}" if v else "-"


def _build_analytics_pdf(session: dict) -> bytes:
    """Generate a PDF analytics dashboard with matplotlib charts using fpdf2.

    Produces pie charts (classification, priority), bar charts (file sizes,
    components), and cost/token tables — matching the Analytics tab exactly.
    """
    _pip_tmp = Path(__file__).resolve().parent.parent / ".pip_tmp"
    if _pip_tmp.exists() and str(_pip_tmp) not in sys.path:
        sys.path.insert(0, str(_pip_tmp))

    from fpdf import FPDF
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import numpy as _np

    r = session.get("results", {}) if session else {}
    fs = r.get("file_suggestions", {}) or {}
    da = r.get("data_analysis", {}) or {}

    suggested_files = fs.get("suggested_files") or []
    class_table = da.get("classification_table") or []
    yaml_sizes = da.get("yaml_file_sizes") or {}
    file_avail = fs.get("file_availability") or {}

    found_count = (
        len(file_avail.get("found_in_supplied_dir") or [])
        + len(file_avail.get("found_elsewhere") or [])
    )
    # B1: match frontend — always use da.total_yaml_bytes
    yaml_processed_bytes = da.get("total_yaml_bytes") or 0
    # B2: match frontend log_error_entries fallback
    log_entries = r.get("log_error_entries") or (da.get("log_payload") or [])
    total_log_bytes = r.get("total_log_bytes")
    if total_log_bytes is None:
        total_log_bytes = sum(e.get("original_size", 0) for e in log_entries) if isinstance(log_entries, list) else 0
    agent_cycles = r.get("agent_iteration_count") or 0

    prio_counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    for f in suggested_files:
        p = f.get("priority", "")
        if p in prio_counts:
            prio_counts[p] += 1

    class_map: dict[str, int] = {}
    for row in class_table:
        c = row.get("Classification", "Unknown")
        class_map[c] = class_map.get(c, 0) + 1

    # D2: top 8 to match frontend
    top_sizes = sorted(yaml_sizes.items(), key=lambda x: -x[1])[:8]

    rca_result = r.get("rca_result") or {}
    fs_in    = fs.get("input_tokens") or 0
    fs_out   = fs.get("output_tokens") or 0
    orch_in  = r.get("agent_input_tokens") or r.get("agent_total_tokens") or 0
    orch_out = r.get("agent_output_tokens") or 0
    rca_in   = rca_result.get("input_tokens") or 0
    rca_out  = rca_result.get("output_tokens") or 0
    fs_cost   = r.get("file_selection_cost_usd") or 0.0
    orch_cost = r.get("orchestrator_cost_usd") or 0.0
    rca_cost  = rca_result.get("cost_usd") or 0.0
    # B4: use None-check instead of or to handle explicit 0.0 correctly
    _raw_total_cost = r.get("total_cost_usd")
    total_cost = _raw_total_cost if _raw_total_cost is not None else (fs_cost + orch_cost + rca_cost)
    payload_bytes = rca_result.get("payload_bytes") or 0
    # Shortlisted bytes for compression line (matches frontend)
    total_shortlisted = (r.get("total_yaml_bytes") or da.get("total_yaml_bytes") or 0) + (total_log_bytes or 0)

    CHART_RED    = "#EE0000"
    CHART_NAVY   = "#1e3a5f"
    CHART_ORANGE = "#ea580c"
    CHART_PURPLE = "#7c3aed"
    CHART_GREEN  = "#16a34a"
    CHART_BLUE   = "#2563eb"
    CHART_AMBER  = "#d97706"
    CHART_BG     = "#f8fafc"
    # D4: match frontend COLORS.red for priority Tier 1
    CHART_PRIO_RED = "#dc2626"

    PIE_COLORS = [CHART_RED, CHART_NAVY, CHART_ORANGE, CHART_PURPLE, CHART_GREEN, CHART_BLUE]

    _DEJAVU = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    _HAS_UNICODE = os.path.exists(_DEJAVU)
    EM    = "\u2014" if _HAS_UNICODE else "-"
    ARROW = "\u2192" if _HAS_UNICODE else "->"
    ELLIP = "\u2026" if _HAS_UNICODE else ".."

    # ── Chart helpers ─────────────────────────────────────────────────────

    def _chart_png(fig) -> bytes:
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                    facecolor=CHART_BG, transparent=False)
        plt.close(fig)
        buf.seek(0)
        return buf.read()

    def _add_titles(fig, ax, title: str, subtitle: str = "") -> None:
        if subtitle:
            fig.suptitle(title, fontsize=9, fontweight="bold", color="#1e293b", y=0.98)
            fig.text(0.5, 0.93, subtitle, ha="center", fontsize=5.5, color="#64748b")
        else:
            ax.set_title(title, fontsize=9, fontweight="bold", color="#1e293b", pad=8)

    def _pie_chart(labels: list, values: list, colors: list, title: str,
                   subtitle: str = "", outside_labels: bool = False) -> bytes:
        fig, ax = plt.subplots(figsize=(4.2, 3.6), facecolor=CHART_BG)
        label_args = {}
        if outside_labels:
            label_args["labels"] = [f"{l} {v}" for l, v in zip(labels, [
                f"{val / sum(values) * 100:.0f}%" if sum(values) > 0 else "" for val in values
            ])]
            label_args["labeldistance"] = 1.15
        else:
            label_args["labels"] = None
        wedges, texts, autotexts = ax.pie(
            values, colors=colors,
            autopct=lambda p: f"{p:.0f}%" if (p > 4 and not outside_labels) else "",
            pctdistance=0.78,
            wedgeprops={"width": 0.55, "edgecolor": "white", "linewidth": 1.5},
            startangle=90,
            textprops={"fontsize": 7},
            **label_args,
        )
        for at in autotexts:
            at.set_fontsize(7)
            at.set_color("white")
            at.set_fontweight("bold")
        _add_titles(fig, ax, title, subtitle)
        handles = [mpatches.Patch(color=c, label=l) for c, l in zip(colors, labels)]
        ax.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, -0.12),
                  ncol=min(3, len(labels)), fontsize=6.5, frameon=False)
        fig.subplots_adjust(top=0.90, bottom=0.12)
        return _chart_png(fig)

    def _vbar_chart(labels: list, values: list, title: str, subtitle: str,
                    color: str, ylabel: str = "") -> bytes:
        fig, ax = plt.subplots(figsize=(4.2, 3.6), facecolor=CHART_BG)
        ax.set_facecolor(CHART_BG)
        x_pos = _np.arange(len(labels))
        ax.bar(x_pos, values, color=color, width=0.6, edgecolor="white")
        ax.set_xticks(x_pos)
        ax.set_xticklabels(labels, fontsize=6, rotation=-25, ha="left")
        if ylabel:
            ax.set_ylabel(ylabel, fontsize=7)
        _add_titles(fig, ax, title, subtitle)
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(axis="y", labelsize=7)
        ax.yaxis.grid(True, linestyle="--", alpha=0.5)
        ax.set_axisbelow(True)
        fig.subplots_adjust(top=0.88, bottom=0.22)
        return _chart_png(fig)

    def _grouped_vbar_chart(labels: list, series: list[tuple[str, list, str]],
                            title: str, subtitle: str) -> bytes:
        fig, ax = plt.subplots(figsize=(4.2, 3.6), facecolor=CHART_BG)
        ax.set_facecolor(CHART_BG)
        n = len(series)
        width = 0.7 / n
        x_pos = _np.arange(len(labels))
        for i, (name, vals, col) in enumerate(series):
            offset = (i - n / 2 + 0.5) * width
            ax.bar(x_pos + offset, vals, width=width, color=col, label=name, edgecolor="white")
        ax.set_xticks(x_pos)
        ax.set_xticklabels(labels, fontsize=5.5, rotation=-30, ha="left")
        _add_titles(fig, ax, title, subtitle)
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(axis="y", labelsize=7)
        ax.yaxis.grid(True, linestyle="--", alpha=0.5)
        ax.set_axisbelow(True)
        ax.legend(fontsize=6, frameon=False)
        fig.subplots_adjust(top=0.88, bottom=0.25)
        return _chart_png(fig)

    # ── Generate chart PNGs ───────────────────────────────────────────────

    cls_labels = list(class_map.keys()) if class_map else ["No data"]
    cls_vals   = list(class_map.values()) if class_map else [1]
    png_cls = _pie_chart(cls_labels, cls_vals,
                         PIE_COLORS[:len(cls_labels)],
                         "YAML Classification Distribution",
                         "ML classification of YAML objects in analyzed files"
                         " (Error / Majority Error / CONFIG / Majority)")

    prio_labels = ["Tier 1", "Tier 2", "Final"]
    prio_vals   = [prio_counts["high"], prio_counts["medium"], prio_counts["low"]]
    if any(prio_vals):
        png_prio = _pie_chart(prio_labels, prio_vals,
                              [CHART_PRIO_RED, CHART_AMBER, CHART_GREEN],
                              "File Priority Distribution (Suggested)",
                              "All suggested files by tier",
                              outside_labels=True)
    else:
        png_prio = None

    # D2: top 8, 14-char truncation, vertical bars, color matches frontend COLORS.teal (#EE0000)
    if top_sizes:
        sz_labels = [n if len(n) <= 14 else n[:14] + ELLIP for n, _ in top_sizes]
        sz_vals   = [round(s / 1024, 1) for _, s in top_sizes]
        png_sizes = _vbar_chart(sz_labels, sz_vals,
                                "File Sizes (Top 8)",
                                "Size distribution of processed configuration files (KB)",
                                CHART_RED, "KB")
    else:
        png_sizes = None

    # D3: vertical grouped bars with Total/Found/Not Found matching frontend
    comp_map: dict[str, dict] = {}
    for f in suggested_files:
        parts = f.get("path", "").split("/")
        comp = parts[1] if parts[0] == "namespaces" and len(parts) > 1 else parts[0]
        comp = comp[:16] if comp else "unknown"
        if comp not in comp_map:
            comp_map[comp] = {"total": 0, "found": 0, "missing": 0}
        comp_map[comp]["total"] += 1
    for item in (file_avail.get("found_in_supplied_dir") or []) + (file_avail.get("found_elsewhere") or []):
        parts = (item.get("original") or "").split("/")
        comp = parts[1] if parts[0] == "namespaces" and len(parts) > 1 else parts[0]
        comp = comp[:16]
        if comp in comp_map:
            comp_map[comp]["found"] += 1
    for path in (file_avail.get("not_found") or []):
        parts = path.split("/")
        comp = parts[1] if parts[0] == "namespaces" and len(parts) > 1 else parts[0]
        comp = comp[:16]
        if comp in comp_map:
            comp_map[comp]["missing"] += 1

    if comp_map:
        sorted_comps = sorted(comp_map.items(), key=lambda x: -x[1]["total"])[:10]
        c_labels = [c if len(c) <= 16 else c[:16] + ELLIP for c, _ in sorted_comps]
        c_total  = [v["total"]   for _, v in sorted_comps]
        c_found  = [v["found"]   for _, v in sorted_comps]
        c_miss   = [v["missing"] for _, v in sorted_comps]
        png_comp = _grouped_vbar_chart(
            c_labels,
            [("Total", c_total, CHART_BLUE), ("Found", c_found, CHART_RED), ("Not Found", c_miss, CHART_ORANGE)],
            "Files by Component",
            "File availability grouped by OpenShift component",
        )
    else:
        png_comp = None

    # ── Build PDF ─────────────────────────────────────────────────────────

    pdf = FPDF()
    pdf.set_margins(20, 20, 20)
    pdf.set_auto_page_break(auto=True, margin=20)

    _DEJAVU_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    if _HAS_UNICODE and os.path.exists(_DEJAVU_B):
        pdf.add_font("DejaVu", "", _DEJAVU, uni=True)
        pdf.add_font("DejaVu", "B", _DEJAVU_B, uni=True)
        F = "DejaVu"
    else:
        F = "Helvetica"

    pdf.add_page()

    W = pdf.w - 40

    C_RED   = (238, 0, 0)
    C_NAVY  = (30, 58, 95)
    C_DARK  = (31, 41, 55)
    C_GRAY  = (100, 116, 139)
    C_BG    = (248, 250, 252)
    C_HEAD  = (241, 245, 249)
    C_WHITE = (255, 255, 255)
    C_ALT   = (248, 250, 252)

    def set_color(c: tuple): pdf.set_text_color(*c)
    def set_fill(c: tuple):  pdf.set_fill_color(*c)
    def set_draw(c: tuple):  pdf.set_draw_color(*c)

    set_fill(C_RED)
    set_draw(C_RED)
    pdf.rect(0, 0, pdf.w, 3, "F")

    pdf.ln(4)
    pdf.set_font(F, "B", 22)
    set_color(C_DARK)
    pdf.cell(0, 12, "Analytics Dashboard", ln=True)

    pdf.set_font(F, "", 10)
    set_color(C_GRAY)
    pdf.cell(0, 6, "Insights derived from diagnostic bundles and logs.", ln=True)
    pdf.ln(6)

    stats = [
        ("Files Analyzed",  str(found_count)),
        ("YAML Processed",  _fmt_kb(yaml_processed_bytes)),
        ("Logs Processed",  _fmt_kb(total_log_bytes)),
        ("Agent Cycles",    str(agent_cycles)),
    ]
    box_w = W / 4
    box_h = 22.0
    y_box = pdf.get_y()
    set_draw((203, 213, 225))
    for i, (label, value) in enumerate(stats):
        x = pdf.l_margin + i * box_w
        set_fill(C_BG)
        pdf.rect(x, y_box, box_w - 2, box_h, "FD")
        pdf.set_xy(x + 3, y_box + 3)
        pdf.set_font(F, "", 7)
        set_color(C_GRAY)
        pdf.cell(box_w - 6, 4, label.upper(), ln=False)
        pdf.set_xy(x + 3, y_box + 9)
        pdf.set_font(F, "B", 13)
        set_color(C_NAVY)
        pdf.cell(box_w - 6, 8, value)
    pdf.ln(box_h + 6)

    # ── PDF helper functions ──────────────────────────────────────────────

    def section(title: str, subtitle: str = "") -> None:
        pdf.ln(3)
        set_fill(C_NAVY)
        set_draw(C_NAVY)
        pdf.set_font(F, "B", 10)
        set_color(C_WHITE)
        pdf.cell(0, 8, f"  {title}", ln=True, fill=True)
        if subtitle:
            pdf.set_font(F, "", 7)
            set_color(C_GRAY)
            pdf.cell(0, 5, f"  {subtitle}", ln=True)
        pdf.ln(1)

    cw = [W * 0.44, W * 0.18, W * 0.18, W * 0.20]

    def _cost_header() -> None:
        set_fill(C_HEAD)
        set_draw((203, 213, 225))
        set_color(C_GRAY)
        pdf.set_font(F, "B", 8)
        for lbl, w in zip(["Phase", "Input Tokens", "Output Tokens", "Cost (USD)"], cw):
            pdf.cell(w, 7, lbl, border=1, fill=True)
        pdf.ln()

    def _cost_row(cells: list, alt: bool = False, dim: bool = False) -> None:
        if alt:
            set_fill(C_ALT)
        set_draw((226, 232, 240))
        set_color(C_DARK if not dim else C_GRAY)
        pdf.set_font(F, "", 8.5)
        for cell, w in zip(cells, cw):
            pdf.cell(w, 7, str(cell)[:70], border=1, fill=alt)
        pdf.ln()

    def _cost_total_row(cost_val: str) -> None:
        set_fill((232, 237, 245))
        set_draw((203, 213, 225))
        pdf.set_font(F, "B", 9)
        set_color(C_DARK)
        pdf.cell(cw[0] + cw[1] + cw[2], 7, "  Total", border=1, fill=True)
        pdf.cell(cw[3], 7, cost_val, border=1, fill=True)
        pdf.ln()

    def _compression_line(shortlisted_b: int, payload_b: int, out_tok: int) -> None:
        if payload_b <= 0:
            return
        ratio = f"{(shortlisted_b / payload_b):.1f}" if shortlisted_b > 0 and payload_b > 0 else EM
        pdf.ln(3)
        set_fill(C_BG)
        set_draw((203, 213, 225))
        pdf.rect(pdf.l_margin, pdf.get_y(), W, 10, "FD")
        pdf.set_xy(pdf.l_margin + 4, pdf.get_y() + 2)
        pdf.set_font(F, "", 8)
        set_color(C_GRAY)
        note = (
            f"Compression: Shortlisted files: {shortlisted_b / 1e6:.2f} MB"
            f"  {ARROW}  Payload to LLM: {round(payload_b / 1024)} KB ({ratio}x)"
            f"  {ARROW}  Final output: {out_tok:,} tokens"
        )
        pdf.cell(W - 8, 6, note)
        pdf.ln(12)

    def _embed_chart(png_bytes: bytes, x: float, y: float, w: float) -> None:
        tmp = io.BytesIO(png_bytes)
        pdf.image(tmp, x=x, y=y, w=w)

    chart_w = W / 2 - 2
    chart_h = chart_w * (3.6 / 4.2)

    # ── Charts: Row 1 — Classification (left) + File Sizes (right)
    chart_y = pdf.get_y()
    _embed_chart(png_cls, x=pdf.l_margin, y=chart_y, w=chart_w)
    if png_sizes:
        _embed_chart(png_sizes, x=pdf.l_margin + chart_w + 4, y=chart_y, w=chart_w)
    pdf.set_y(chart_y + chart_h + 4)

    # ── Charts: Row 2 — Priority (left) + Components (right)
    chart_y2 = pdf.get_y()
    if png_prio:
        _embed_chart(png_prio, x=pdf.l_margin, y=chart_y2, w=chart_w)
    if png_comp:
        _embed_chart(png_comp, x=pdf.l_margin + chart_w + 4, y=chart_y2, w=chart_w)
    if png_prio or png_comp:
        pdf.set_y(chart_y2 + chart_h + 4)

    # Force new page so the cost table is never split across pages
    pdf.add_page()
    section("Token Usage & Cost Summary", "LLM API consumption across all workflow phases")

    def _render_cost_block(label: str, rows: list, total_val: str,
                           shortlisted_b: int, payload_b: int, out_tok: int) -> None:
        needed = 6 + 7 + len(rows) * 7 + 7 + 15
        if pdf.get_y() + needed > pdf.h - 20:
            pdf.add_page()
        pdf.set_font(F, "B", 8.5)
        set_color(C_NAVY)
        pdf.cell(0, 6, label, ln=True)
        _cost_header()
        for i, (phase, tin, tout, cost, dim) in enumerate(rows):
            _cost_row([phase, tin, tout, cost], alt=(i % 2 == 0), dim=dim)
        _cost_total_row(total_val)
        _compression_line(shortlisted_b, payload_b, out_tok)

    iter_lbl = f"0. Orchestrator ReAct loop ({agent_cycles} iter.)" if agent_cycles else "0. Orchestrator ReAct loop"
    total_rows = [
        (iter_lbl,                              _tok(orch_in), _tok(orch_out), _usd(orch_cost), False),
        ("1. File selection",                   _tok(fs_in),   _tok(fs_out),   _usd(fs_cost),   False),
        ("2. YAML processing (local ML)",       EM,            EM,             EM,               True),
        ("3. Log processing (local Drain3)",    EM,            EM,             EM,               True),
        ("4. Data aggregation (no LLM)",        EM,            EM,             EM,               True),
        ("5. RCA (extracted payload)",          _tok(rca_in),  _tok(rca_out),  _usd(rca_cost),  False),
    ]

    _render_cost_block(
        "Total (cumulative)", total_rows, _usd(total_cost),
        total_shortlisted, payload_bytes,
        rca_out if isinstance(rca_out, int) else 0,
    )

    per_stage = r.get("rca_costs_per_stage") or {}
    stage_defs = [("Tier1", "Tier 1"), ("Tier2", "Tier 2"), ("Final", "Final")]
    for stage_key, stage_label in stage_defs:
        sc = per_stage.get(stage_key)
        if not sc:
            continue
        s_orch_in  = sc.get("orchestrator_input_tokens") or 0
        s_orch_out = sc.get("orchestrator_output_tokens") or 0
        s_orch_cost = sc.get("orchestrator_cost_usd") or 0.0
        s_fs_in  = sc.get("file_selection_input_tokens") or 0
        s_fs_out = sc.get("file_selection_output_tokens") or 0
        s_fs_cost = sc.get("file_selection_cost_usd") or 0.0
        s_rca_in  = sc.get("input_tokens") or 0
        s_rca_out = sc.get("output_tokens") or 0
        s_rca_cost = sc.get("cost_usd") or 0.0

        stage_rows = [
            (f"0. Orchestrator ReAct loop ({stage_label} stage delta)", _tok(s_orch_in), _tok(s_orch_out), _usd(s_orch_cost), False),
            (f"1. File selection ({stage_label} stage delta)",          _tok(s_fs_in),   _tok(s_fs_out),   _usd(s_fs_cost),   False),
            ("2. YAML processing (local ML)",                          EM,              EM,               EM,                 True),
            ("3. Log processing (local Drain3)",                       EM,              EM,               EM,                 True),
            ("4. Data aggregation (no LLM)",                           EM,              EM,               EM,                 True),
            (f"5. RCA ({stage_label} pass only)",                      _tok(s_rca_in),  _tok(s_rca_out),  _usd(s_rca_cost),  False),
        ]

        s_payload = sc.get("payload_bytes") or 0
        s_shortlisted = sc.get("shortlisted_total_bytes") or (
            (sc.get("shortlisted_yaml_bytes") or 0) + (sc.get("shortlisted_log_bytes") or 0)
        )
        _render_cost_block(
            stage_label, stage_rows, _usd(s_orch_cost + s_fs_cost + s_rca_cost),
            s_shortlisted, s_payload,
            s_rca_out if isinstance(s_rca_out, int) else 0,
        )

    return bytes(pdf.output())


def _build_logging_tracing_doc_html(session: dict, last_rca_text: str) -> str:
    """Build a Word-compatible HTML document with logging/tracing source-reference data."""
    r = session.get("results", {}) if session else {}
    fs = r.get("file_suggestions", {}) or {}
    suggested_files = fs.get("suggested_files") or []
    file_avail = fs.get("file_availability") or {}

    sf_rows = ""
    for f in suggested_files:
        prio = f.get("priority", "")
        badge_cls = {"high": "badge-high", "medium": "badge-medium", "low": "badge-low"}.get(prio, "")
        sf_rows += (
            f"<tr>"
            f"<td class='{badge_cls}'>{html.escape(prio)}</td>"
            f"<td style='font-family:Courier New,monospace;font-size:9pt'>{html.escape(f.get('path',''))}</td>"
            f"<td>{html.escape(f.get('reason',''))}</td>"
            f"</tr>"
        )
    sf_section = f"""
<h2>Source Reference / Shortlisted Files</h2>
<table>
  <thead><tr><th width="80">Priority</th><th>Path</th><th>Reason</th></tr></thead>
  <tbody>{sf_rows if sf_rows else '<tr><td colspan="3" class="not-found">No shortlisted files</td></tr>'}</tbody>
</table>"""

    summary_text = html.escape(file_avail.get("summary_text") or "")
    found_in_dir = file_avail.get("found_in_supplied_dir") or []
    found_elsewhere = file_avail.get("found_elsewhere") or []
    not_found = file_avail.get("not_found") or []

    avail_rows = ""
    for item in found_in_dir:
        avail_rows += f"<tr><td style='color:#16a34a'>Found (supplied dir)</td><td style='font-family:Courier New,monospace;font-size:9pt'>{html.escape(item.get('original',''))}</td></tr>"
    for item in found_elsewhere:
        avail_rows += f"<tr><td style='color:#2563eb'>Found (elsewhere)</td><td style='font-family:Courier New,monospace;font-size:9pt'>{html.escape(item.get('original',''))}</td></tr>"
    for path in not_found:
        avail_rows += f"<tr><td class='not-found'>Not found</td><td style='font-family:Courier New,monospace;font-size:9pt;color:#9ca3af'>{html.escape(path)}</td></tr>"

    avail_section = f"""
<h2>File Availability</h2>
{f'<p>{summary_text}</p>' if summary_text else ''}
<table>
  <thead><tr><th width="160">Status</th><th>Path</th></tr></thead>
  <tbody>{avail_rows if avail_rows else '<tr><td colspan="2" class="not-found">No availability data</td></tr>'}</tbody>
</table>"""

    rca_section = ""
    if last_rca_text.strip():
        safe_rca = html.escape(last_rca_text).replace("\n", "<br/>")
        rca_section = f"""
<h2>Latest RCA Stage Content</h2>
<p class="subtitle">Full text of the latest RCA analysis stage</p>
<div style="font-size:10pt;line-height:1.6;color:#374151">{safe_rca}</div>"""

    body = sf_section + avail_section + rca_section
    return _build_word_doc_html_rich("Logging / Tracing", body)


def _read_session_from_pvc(sid: str) -> dict:
    """Read session data from the shared PVC (authoritative source).

    Checks both session_id and api_session_id to handle the internal-vs-API
    ID mismatch.  Falls back to _read_session_by_id (local agent_memory) if
    the PVC file is missing so existing behaviour is preserved.
    """
    pvc_mem = job_runner.get_job_dir(sid) / "agent_memory.json"
    if pvc_mem.exists():
        try:
            with open(pvc_mem, "r", encoding="utf-8") as f:
                mem = json.load(f)
            for s in reversed(mem.get("sessions", [])):
                if s.get("session_id") == sid or s.get("api_session_id") == sid:
                    return s
        except Exception:
            pass
    return _read_session_by_id(sid)


@app.get("/full-bundle-zip")
def download_full_bundle_zip(session_id: str | None = Query(default=None), session: str | None = Query(default=None)):
    """Return RCA_Bundle.zip containing all RCA docs, summary, and tracing info."""
    sid = _resolve_session_id(session_id or session)
    if not sid:
        raise HTTPException(status_code=400, detail="session_id is required")
    results_dir = _job_results_dir(sid)

    session_data = _read_session_from_pvc(sid)
    doc_entries: list[tuple[str, str | bytes]] = []

    for disk_name, arc_name in _RCA_STAGE_FILES:
        src = results_dir / disk_name
        if not (src.is_file() and src.stat().st_size > 0):
            continue
        text = src.read_text(encoding="utf-8", errors="replace")
        title = arc_name.rsplit(".", 1)[0].replace("_", " ")
        dag_png = _load_stage_dag_png(results_dir, disk_name)
        append_rca_stage_bundle_entries(
            doc_entries,
            arc_prefix="rca_report_bundle/",
            doc_filename=arc_name,
            title=title,
            body_text=text,
            dag_png=dag_png,
        )

    summary_src = results_dir / "rca_report_summary.txt"
    if summary_src.is_file() and summary_src.stat().st_size > 0:
        exec_summary = summary_src.read_text(encoding="utf-8", errors="replace")
        latest_rca = ""
        for dname, _ in reversed(_RCA_STAGE_FILES):
            s = results_dir / dname
            if s.is_file() and s.stat().st_size > 0:
                latest_rca = s.read_text(encoding="utf-8", errors="replace")
                break
        resolution_md = _extract_resolution_section(latest_rca)
        doc_entries.append(("RCA_Summary.doc", "\ufeff" + _build_rca_summary_doc_html(exec_summary, resolution_md)))

    if session_data:
        try:
            analytics_pdf = _build_analytics_pdf(session_data)
            doc_entries.append(("Analytics_Dashboard.pdf", analytics_pdf))
        except Exception as exc:
            logger.error("Analytics PDF generation failed: %s", exc, exc_info=True)

    last_rca_text = ""
    for disk_name, _ in reversed(_RCA_STAGE_FILES):
        src = results_dir / disk_name
        if src.is_file() and src.stat().st_size > 0:
            last_rca_text = src.read_text(encoding="utf-8", errors="replace")
            break
    if session_data or last_rca_text:
        lt_html = _build_logging_tracing_doc_html(session_data, last_rca_text)
        doc_entries.append(("Logging_Tracing.doc", "\ufeff" + lt_html))

    if not doc_entries:
        raise HTTPException(status_code=404, detail="No analysis results available for download.")

    buf = create_zip_from_buffers(doc_entries)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="RCA_Bundle.zip"'},
    )


@app.get("/config")
def get_config():
    """Return Config/config.json (with api_key masked) for the Settings view."""
    cfg_path = get_config_path()
    if not cfg_path.exists():
        raise HTTPException(status_code=404, detail="config.json not found")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if "claude" in cfg and "api_key" in cfg["claude"]:
            key = cfg["claude"]["api_key"]
            if key and len(key) > 8:
                cfg["claude"]["api_key"] = key[:4] + "****" + key[-4:]
        return cfg
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read config.json: {e}")


@app.post("/feedback")
def submit_feedback(req: FeedbackRequest):
    """Handle RCA feedback — satisfactory or request deeper analysis.

    For 'satisfactory', marks the session done and cleans up.
    For 'not satisfactory', creates a new K8s Job for the deepening round.
    """
    try:
        api_sid = req.session_id

        _restore_agent_memory_from_pvc(api_sid)

        orch = OrchestratorAgent(reset=False)

        orch_sid = next(
            (s["session_id"] for s in orch.memory.get("sessions", [])
             if s.get("api_session_id") == api_sid),
            api_sid,
        )

        if not orch.get_session(orch_sid):
            raise HTTPException(
                status_code=404,
                detail=f"Session '{api_sid}' not found in agent memory.",
            )

        if req.satisfactory:
            result = orch.continue_rca_with_feedback(orch_sid, True)
            if isinstance(result, dict) and result.get("status") == "error":
                raise HTTPException(status_code=400, detail=result.get("error", "Feedback processing failed"))
            job_runner.cleanup_job_input(api_sid)
            job_runner._delete_k8s_job_resource(api_sid)
            _cleanup_session_extract(_get_session_base_dir(orch_sid))
            return result

        base_dir = _get_session_base_dir(orch_sid) or _load_must_gather_base_dir_from_config()

        existing_status = job_runner.get_job_status(api_sid)
        job_owner = existing_status.get("owner", "unknown")

        try:
            job_runner.create_deepening_job(
                session_id=api_sid,
                orch_session_id=orch_sid,
                feedback_text=req.feedback_text,
                must_gather_base_dir=base_dir,
                owner=job_owner,
            )
        except Exception as e:
            logger.exception("Failed to create deepening K8s Job for session %s", api_sid)
            raise HTTPException(status_code=500, detail=f"Failed to create deepening job: {e}")

        return {"status": "deepening", "session_id": api_sid}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Feedback processing failed: {e}")


@app.post("/init")
def init_session():
    """Called by the frontend on page load.  No-op with K8s Job model."""
    return {"status": "ok"}


@app.post("/clear")
def clear_memory():
    """Clear agent memory."""
    try:
        clear_agent_memory()
        return {"status": "ok", "message": "Session data cleared."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear session: {e}")


# ---------------------------------------------------------------------------
# Worker callback endpoints (cross-cluster mode)
# ---------------------------------------------------------------------------

def _verify_callback_token(request: Request, session_id: str) -> None:
    """Validate the per-session bearer token from a worker callback."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = auth[7:]
    expected = job_runner.get_callback_token(session_id)
    if not expected or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="Invalid callback token")


@app.get("/callback/input/{session_id}")
async def callback_get_input(session_id: str, request: Request):
    """Stream the must-gather archive from GCS to the worker.

    The API proxies the archive — workers never talk to GCS directly.
    """
    _verify_callback_token(request, session_id)

    try:
        import object_storage
        meta = object_storage.get_archive_metadata(session_id)
        stream = object_storage.stream_download(session_id)
        return StreamingResponse(
            stream,
            media_type=meta["content_type"],
            headers={
                "Content-Disposition": f'attachment; filename="{meta["name"]}"',
                "Content-Length": str(meta["size"]),
            },
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No archive found for session {session_id}")
    except Exception as e:
        logger.exception("Failed to stream input for session %s", session_id)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/callback/status")
async def callback_post_status(request: Request):
    """Receive a status update from a worker."""
    body = await request.json()
    session_id = body.get("session_id", "")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")

    _verify_callback_token(request, session_id)

    job_dir = job_runner.get_job_dir(session_id)
    job_dir.mkdir(parents=True, exist_ok=True)

    status_file = job_dir / "status.json"
    existing = {}
    if status_file.exists():
        try:
            existing = json.loads(status_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    for key in ("owner", "problem_statement", "case_number", "deepening_round", "_callback_token"):
        if key not in body and key in existing:
            body[key] = existing[key]

    body["updated_at"] = datetime.now().isoformat()

    tmp = job_dir / "status.json.tmp"
    tmp.write_text(json.dumps(body, indent=2), encoding="utf-8")
    tmp.rename(status_file)

    if body.get("status") in ("completed", "error"):
        import asyncio
        asyncio.get_event_loop().call_later(
            30, lambda: _schedule_remote_cleanup(session_id)
        )

    return {"status": "ok"}


@app.post("/callback/results/{session_id}")
async def callback_post_results(session_id: str, request: Request):
    """Receive results tarball from a worker."""
    _verify_callback_token(request, session_id)

    job_dir = job_runner.get_job_dir(session_id)
    results_dir = job_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    body = await request.body()

    import io as _io
    import tarfile as _tarfile
    buf = _io.BytesIO(body)
    try:
        with _tarfile.open(fileobj=buf, mode="r:gz") as tar:
            tar.extractall(path=str(results_dir))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to extract results tar: {e}")

    return {"status": "ok", "files_received": len(list(results_dir.iterdir()))}


@app.post("/callback/agent-memory/{session_id}")
async def callback_post_agent_memory(session_id: str, request: Request):
    """Receive agent_memory.json from a worker."""
    _verify_callback_token(request, session_id)

    job_dir = job_runner.get_job_dir(session_id)
    job_dir.mkdir(parents=True, exist_ok=True)

    body = await request.body()
    dest = job_dir / "agent_memory.json"
    dest.write_bytes(body)

    return {"status": "ok"}


@app.get("/callback/agent-memory/{session_id}")
async def callback_get_agent_memory(session_id: str, request: Request):
    """Serve agent_memory.json to a worker (for deepening rounds)."""
    _verify_callback_token(request, session_id)

    job_dir = job_runner.get_job_dir(session_id)
    mem_file = job_dir / "agent_memory.json"
    if not mem_file.exists():
        raise HTTPException(status_code=404, detail="No agent memory found")

    return JSONResponse(content=json.loads(mem_file.read_text(encoding="utf-8")))


@app.post("/callback/console/{session_id}")
async def callback_post_console(session_id: str, request: Request):
    """Append console log text from a worker."""
    _verify_callback_token(request, session_id)

    job_dir = job_runner.get_job_dir(session_id)
    console_path = job_dir / "results" / "workflow_console.txt"
    console_path.parent.mkdir(parents=True, exist_ok=True)

    body = await request.body()
    with open(console_path, "ab") as f:
        f.write(body)

    return {"status": "ok"}


def _schedule_remote_cleanup(session_id: str) -> None:
    """Trigger cleanup of remote namespace and GCS objects."""
    try:
        job_runner.trigger_remote_cleanup(session_id)
    except Exception:
        logger.exception("Failed to clean up remote resources for session %s", session_id)


# ---------------------------------------------------------------------------
# Startup events (cross-cluster mode)
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def _startup_cross_cluster():
    """Initialize cross-cluster features if enabled."""
    if JOBS_CLUSTER_MODE != "enabled":
        return

    Path(LOCAL_SESSION_DIR).mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    try:
        _reconcile_running_sessions()
    except Exception:
        logger.exception("Startup reconciliation failed")


def _reconcile_running_sessions() -> None:
    """On startup, check for sessions that were running when the API restarted."""
    if JOBS_CLUSTER_MODE != "enabled":
        return

    import remote_cluster

    jobs_root = Path(LOCAL_SESSION_DIR) / "jobs"
    if not jobs_root.is_dir():
        return

    for sid_dir in jobs_root.iterdir():
        if not sid_dir.is_dir():
            continue
        status_file = sid_dir / "status.json"
        if not status_file.exists():
            continue
        try:
            data = json.loads(status_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("status") != "running":
            continue

        sid = sid_dir.name
        ns = remote_cluster.get_namespace_for_session(sid)
        if not ns:
            data["status"] = "error"
            data["message"] = "No namespace claim found after API restart"
            data["updated_at"] = datetime.now().isoformat()
            status_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            logger.info("Reconciliation: marked session %s as error (no namespace claim)", sid)
            continue
        name = f"rca-{sid.lower().replace('_', '-')}"[:63]
        try:
            phase = remote_cluster.get_job_phase(ns, name)
            if phase == "failed":
                data["status"] = "error"
                data["message"] = "Worker failed during API restart"
                data["updated_at"] = datetime.now().isoformat()
                status_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
                logger.info("Reconciliation: marked session %s as error (job failed)", sid)
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
