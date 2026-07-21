"""
Kubernetes Job runner for RCA worker pods — cross-cluster mode.

Creates, monitors, cancels and cleans up batch/v1 Job resources that execute
the ``worker.py`` entrypoint inside the pre-built RCA image.

Jobs run in ephemeral namespaces on a remote cluster.  Job metadata is stored
as K8s annotations; results and session state are persisted to GCS.
"""

import json
import logging
import os
import secrets as _secrets
from datetime import datetime
from pathlib import Path

from kubernetes import client

import remote_cluster
import object_storage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------
WORKER_IMAGE: str = os.environ.get("WORKER_IMAGE", "")
JOB_ACTIVE_DEADLINE: int = int(os.environ.get("JOB_ACTIVE_DEADLINE_SECONDS", "3600"))
JOB_TTL_AFTER_FINISHED: int = int(os.environ.get("JOB_TTL_AFTER_FINISHED", "600"))

WORKER_CPU_REQUEST: str = os.environ.get("WORKER_CPU_REQUEST", "1")
WORKER_CPU_LIMIT: str = os.environ.get("WORKER_CPU_LIMIT", "2")
WORKER_MEM_REQUEST: str = os.environ.get("WORKER_MEM_REQUEST", "1Gi")
WORKER_MEM_LIMIT: str = os.environ.get("WORKER_MEM_LIMIT", "2Gi")

API_CALLBACK_URL: str = os.environ.get("API_CALLBACK_URL", "")
CALLBACK_VERIFY_SSL: str = os.environ.get("CALLBACK_VERIFY_SSL", "false")


def _job_name(session_id: str) -> str:
    """Derive a deterministic K8s Job name from the session id.

    K8s names must be lowercase alphanumeric / dashes and <= 63 chars.
    """
    safe = session_id.lower().replace("_", "-")
    name = f"rca-{safe}"
    return name[:63]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_analysis_job(
    session_id: str,
    user_query: str,
    must_gather_base_dir: str,
    owner: str = "",
    archive_local_path: Path | None = None,
) -> str:
    """Create a K8s Job that runs ``worker.py`` in analyze mode.

    *archive_local_path* is uploaded to GCS first, and the worker
    downloads it from the API at runtime.

    Returns the Job name.
    """
    return _create_remote_analysis_job(
        session_id=session_id,
        user_query=user_query,
        archive_local_path=archive_local_path,
        owner=owner,
    )


def create_deepening_job(
    session_id: str,
    orch_session_id: str,
    feedback_text: str,
    owner: str = "",
) -> str:
    """Create a K8s Job for a feedback / deepening round.

    Deletes any existing Job for this session first (the completed analysis
    Job) to avoid name collisions.  Increments ``deepening_round`` so the
    admin portal can display which round is running.

    Returns the Job name.
    """
    return _create_remote_deepening_job(
        session_id=session_id,
        orch_session_id=orch_session_id,
        feedback_text=feedback_text,
        owner=owner,
    )


def get_job_status(session_id: str) -> dict:
    """Read job status for this session.

    Reads the ``rca.intelliaide/status`` annotation from the K8s Job.
    Falls back to GCS ``status.json`` for TTL-deleted Jobs.
    """
    ns = remote_cluster.get_namespace_for_session(session_id)
    if ns:
        name = _job_name(session_id)
        ann = remote_cluster.get_job_annotations(ns, name)
        status_json = ann.get(remote_cluster._ann("status"), "")
        if status_json:
            try:
                status = json.loads(status_json)
                if status.get("status") in ("completed", "error"):
                    return status
                k8s_phase = remote_cluster.get_job_phase(ns, name)
                if k8s_phase == "failed" and status.get("status") != "error":
                    status.update({"status": "error", "message": "Worker pod failed unexpectedly"})
                return status
            except (json.JSONDecodeError, TypeError):
                pass

    gcs_status = object_storage.read_json(session_id, "status.json")
    if gcs_status:
        return gcs_status

    return {
        "session_id": session_id,
        "status": "unknown",
        "phase": "unknown",
        "progress": 0,
        "message": "No status available",
    }


def cancel_job(session_id: str) -> dict:
    """Delete the K8s Job (cascading) to terminate the worker pod."""
    cancelled_status = {
        "session_id": session_id,
        "status": "cancelled",
        "message": "Analysis cancelled by user",
        "updated_at": datetime.now().isoformat(),
    }

    ns = remote_cluster.get_namespace_for_session(session_id)
    if ns:
        name = _job_name(session_id)
        try:
            remote_cluster.patch_job_annotations(ns, name, {
                remote_cluster._ann("status"): json.dumps(cancelled_status),
            })
        except Exception:
            pass
        try:
            remote_cluster.delete_job(ns, name)
        except Exception:
            pass
        remote_cluster.release_namespace(session_id)
    try:
        object_storage.write_json(session_id, "status.json", cancelled_status)
    except Exception:
        pass
    try:
        object_storage.delete_prefix(session_id, "archive")
    except Exception:
        pass

    return {"status": "cancelled", "session_id": session_id}


def cleanup_job(session_id: str) -> None:
    """Delete the K8s Job resource and remove ALL job data."""
    ns = remote_cluster.get_namespace_for_session(session_id)
    if ns:
        try:
            remote_cluster.delete_job(ns, _job_name(session_id))
        except Exception:
            pass
        remote_cluster.release_namespace(session_id)
    try:
        object_storage.delete_session_objects(session_id)
    except Exception:
        pass


def cleanup_job_input(session_id: str) -> None:
    """No-op — the archive is in GCS, worker uses emptyDir scratch."""
    return


def list_all_jobs(owner: str | None = None) -> list[dict]:
    """Return every session — from live K8s Jobs AND from GCS history.

    Queries live K8s Jobs across the namespace pool and falls back to
    GCS ``status.json`` objects for TTL-deleted sessions.

    When *owner* is provided, only sessions belonging to that owner are returned.
    """
    seen_sids: set[str] = set()
    result: list[dict] = []

    for job in remote_cluster.list_jobs_in_pool("app=rca-worker"):
        sid = (job.metadata.labels or {}).get("session-id", "")
        if not sid or sid in seen_sids:
            continue
        seen_sids.add(sid)
        status_json = (job.metadata.annotations or {}).get(
            remote_cluster._ann("status"), ""
        )
        status_data = {}
        if status_json:
            try:
                status_data = json.loads(status_json)
            except Exception:
                pass
        if not status_data:
            status_data = get_job_status(sid)
        created_at = None
        if job.metadata.creation_timestamp:
            created_at = job.metadata.creation_timestamp.isoformat()
        result.append(_session_entry(sid, status_data, created_at))

    for prefix in object_storage.list_session_prefixes():
        if prefix in seen_sids:
            continue
        gcs_status = object_storage.read_json(prefix, "status.json")
        if not gcs_status:
            continue
        seen_sids.add(prefix)
        created_at = gcs_status.get("updated_at")
        result.append(_session_entry(prefix, gcs_status, created_at))

    if owner:
        result = [e for e in result if e.get("owner", "").lower() == owner.lower()]

    result.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    return result


def list_active_jobs() -> list[dict]:
    """Return only currently active/running jobs (for admin stats)."""
    return [
        {"session_id": c["session_id"], "name": c["namespace"]}
        for c in remote_cluster.list_active_claims()
    ]


def _session_entry(sid: str, status_data: dict, created_at: str | None) -> dict:
    """Build a single session dict for the /sessions response."""
    return {
        "session_id": sid,
        "status": status_data.get("status", "unknown"),
        "phase": status_data.get("phase", "unknown"),
        "progress": status_data.get("progress", 0),
        "message": status_data.get("message", ""),
        "problem_statement": status_data.get("problem_statement", ""),
        "case_number": status_data.get("case_number", ""),
        "created_at": created_at,
        "filename": status_data.get("filename", ""),
        "error": status_data.get("error", ""),
        "owner": status_data.get("owner", ""),
        "deepening_round": status_data.get("deepening_round", 1),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _write_remote_status(session_id: str, status: dict) -> None:
    """Patch the status annotation on the K8s Job.

    Also writes to GCS on terminal status for historical persistence.
    """
    status["updated_at"] = datetime.now().isoformat()

    ns = remote_cluster.get_namespace_for_session(session_id)
    if ns:
        name = _job_name(session_id)
        remote_cluster.patch_job_annotations(ns, name, {
            remote_cluster._ann("status"): json.dumps(status),
        })

    if status.get("status") in ("completed", "error", "cancelled"):
        try:
            object_storage.write_json(session_id, "status.json", status)
        except Exception:
            logger.warning("Failed to persist terminal status to GCS for %s", session_id)


# ---------------------------------------------------------------------------
# Remote job helpers
# ---------------------------------------------------------------------------

def _create_remote_analysis_job(
    session_id: str,
    user_query: str,
    archive_local_path: Path | None,
    owner: str = "",
) -> str:
    """Upload archive to GCS, claim a namespace, create Job with annotations."""
    if archive_local_path and archive_local_path.exists():
        object_storage.upload_archive(session_id, archive_local_path)

    callback_token = _secrets.token_urlsafe(32)
    ns = remote_cluster.claim_namespace(session_id)

    initial_status = {
        "session_id": session_id,
        "status": "running",
        "phase": "initializing",
        "progress": 0,
        "message": "Remote job created, waiting for pod to start...",
        "owner": owner or "unknown",
    }

    env_extras = [
        client.V1EnvVar(name="USER_QUERY", value=user_query),
        client.V1EnvVar(name="MODE", value="analyze"),
        client.V1EnvVar(name="OWNER", value=owner),
        client.V1EnvVar(name="COMMS_MODE", value="callback"),
        client.V1EnvVar(name="API_CALLBACK_URL", value=API_CALLBACK_URL),
        client.V1EnvVar(name="CALLBACK_TOKEN", value=callback_token),
        client.V1EnvVar(name="CALLBACK_VERIFY_SSL", value=CALLBACK_VERIFY_SSL),
    ]

    name = remote_cluster.create_remote_job(
        namespace=ns,
        session_id=session_id,
        env_extras=env_extras,
        callback_token=callback_token,
        initial_status=initial_status,
    )

    logger.info("Created remote analysis job %s in namespace %s", name, ns)
    return name


def _create_remote_deepening_job(
    session_id: str,
    orch_session_id: str,
    feedback_text: str,
    owner: str = "",
) -> str:
    """Create a deepening job on the remote cluster.

    Reuses the same namespace claim.  Deletes the previous Job first
    to avoid name collisions.  Reads and increments deepening_round
    from the existing Job annotation.
    """
    ns = remote_cluster.get_namespace_for_session(session_id)

    prev_round = 1
    if ns:
        old_name = _job_name(session_id)
        ann = remote_cluster.get_job_annotations(ns, old_name)
        try:
            prev_round = int(ann.get(remote_cluster._ann("deepening-round"), "1"))
        except (ValueError, TypeError):
            pass
        try:
            remote_cluster.delete_job(ns, old_name)
        except Exception:
            pass
    else:
        ns = remote_cluster.claim_namespace(session_id)

    new_round = prev_round + 1
    callback_token = _secrets.token_urlsafe(32)

    initial_status = {
        "session_id": session_id,
        "status": "running",
        "phase": "initializing",
        "progress": 5,
        "message": "Remote deepening job created...",
        "owner": owner or "unknown",
        "deepening_round": new_round,
    }

    env_extras = [
        client.V1EnvVar(name="MODE", value="deepening"),
        client.V1EnvVar(name="ORCHESTRATOR_SESSION_ID", value=orch_session_id),
        client.V1EnvVar(name="FEEDBACK_TEXT", value=feedback_text),
        client.V1EnvVar(name="OWNER", value=owner),
        client.V1EnvVar(name="COMMS_MODE", value="callback"),
        client.V1EnvVar(name="API_CALLBACK_URL", value=API_CALLBACK_URL),
        client.V1EnvVar(name="CALLBACK_TOKEN", value=callback_token),
        client.V1EnvVar(name="CALLBACK_VERIFY_SSL", value=CALLBACK_VERIFY_SSL),
    ]

    name = remote_cluster.create_remote_job(
        namespace=ns,
        session_id=session_id,
        env_extras=env_extras,
        callback_token=callback_token,
        initial_status=initial_status,
    )

    logger.info("Created remote deepening job %s in namespace %s", name, ns)
    return name


def get_callback_token(session_id: str) -> str:
    """Retrieve the callback token for a session from K8s Job annotation."""
    ns = remote_cluster.get_namespace_for_session(session_id)
    if not ns:
        return ""
    ann = remote_cluster.get_job_annotations(ns, _job_name(session_id))
    return ann.get(remote_cluster._ann("callback-token"), "")


def trigger_remote_cleanup(session_id: str) -> None:
    """Release the namespace back to the pool and clean up the input archive.

    Preserves results and status.json in GCS for historical queries.
    """
    status = get_job_status(session_id)
    if status.get("status") not in ("completed", "error", "cancelled"):
        status["status"] = "completed"
        status["updated_at"] = datetime.now().isoformat()
    try:
        object_storage.write_json(session_id, "status.json", status)
    except Exception:
        logger.warning("Failed to persist final status to GCS for %s", session_id)

    remote_cluster.release_namespace(session_id)

    try:
        object_storage.delete_prefix(session_id, "archive")
    except Exception:
        logger.warning("Failed to delete archive from GCS for session %s", session_id)
