"""
Kubernetes Job runner for RCA worker pods.

Creates, monitors, cancels and cleans up batch/v1 Job resources that execute
the ``worker.py`` entrypoint inside the pre-built RCA image.

Supports two modes controlled by ``JOBS_CLUSTER_MODE``:

- ``disabled`` (default) — single-cluster mode.  All data exchange happens
  through a shared PVC mounted at ``SHARED_PVC_MOUNT``.
- ``enabled`` — cross-cluster mode.  Jobs run in ephemeral namespaces on a
  remote cluster.  Data flows through the API (GCS-backed for large archives,
  HTTP callbacks for status/results).
"""

import json
import logging
import os
import secrets as _secrets
import shutil
from pathlib import Path
from typing import Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------
SHARED_PVC_MOUNT: str = os.environ.get("SHARED_PVC_MOUNT", "/shared")
SHARED_PVC_CLAIM_NAME: str = os.environ.get("SHARED_PVC_CLAIM_NAME", "must-gather-shared")
WORKER_IMAGE: str = os.environ.get("WORKER_IMAGE", "")
NAMESPACE: str = os.environ.get("K8S_NAMESPACE", "must-gather")
GCP_SECRET_NAME: str = os.environ.get("GCP_SECRET_NAME", "gcloud-adc-secret")
APP_CONFIG_SECRET_NAME: str = os.environ.get("APP_CONFIG_SECRET_NAME", "must-gather-app-config")
LOG_CONFIGMAP_NAME: str = os.environ.get("LOG_CONFIGMAP_NAME", "must-gather-log-config")
JOB_ACTIVE_DEADLINE: int = int(os.environ.get("JOB_ACTIVE_DEADLINE_SECONDS", "3600"))
JOB_TTL_AFTER_FINISHED: int = int(os.environ.get("JOB_TTL_AFTER_FINISHED", "600"))

WORKER_CPU_REQUEST: str = os.environ.get("WORKER_CPU_REQUEST", "250m")
WORKER_CPU_LIMIT: str = os.environ.get("WORKER_CPU_LIMIT", "2000m")
WORKER_MEM_REQUEST: str = os.environ.get("WORKER_MEM_REQUEST", "512Mi")
WORKER_MEM_LIMIT: str = os.environ.get("WORKER_MEM_LIMIT", "2Gi")

JOBS_CLUSTER_MODE: str = os.environ.get("JOBS_CLUSTER_MODE", "disabled")
LOCAL_SESSION_DIR: str = os.environ.get("LOCAL_SESSION_DIR", "/data/sessions")
API_CALLBACK_URL: str = os.environ.get("API_CALLBACK_URL", "")

# ---------------------------------------------------------------------------
# K8s client initialisation (lazy, once)
# ---------------------------------------------------------------------------
_batch_v1: Optional[client.BatchV1Api] = None
_core_v1: Optional[client.CoreV1Api] = None


def _init_k8s() -> None:
    global _batch_v1, _core_v1
    if _batch_v1 is not None:
        return
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    _batch_v1 = client.BatchV1Api()
    _core_v1 = client.CoreV1Api()


def _job_name(session_id: str) -> str:
    """Derive a deterministic K8s Job name from the session id.

    K8s names must be lowercase alphanumeric / dashes and <= 63 chars.
    """
    safe = session_id.lower().replace("_", "-")
    name = f"rca-{safe}"
    return name[:63]


def _is_remote() -> bool:
    return JOBS_CLUSTER_MODE == "enabled"


def _job_dir_on_pvc(session_id: str) -> str:
    """Return the path on the shared PVC for this job."""
    return f"{SHARED_PVC_MOUNT}/jobs/{session_id}"


def _job_dir_local(session_id: str) -> str:
    """Return the local session directory for cross-cluster mode."""
    return f"{LOCAL_SESSION_DIR}/jobs/{session_id}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_job_dir(session_id: str) -> Path:
    """Return the job directory — local storage in remote mode, PVC in local mode."""
    if _is_remote():
        return Path(_job_dir_local(session_id))
    return Path(_job_dir_on_pvc(session_id))


def create_analysis_job(
    session_id: str,
    user_query: str,
    must_gather_base_dir: str,
    owner: str = "",
    archive_local_path: Path | None = None,
) -> str:
    """Create a K8s Job that runs ``worker.py`` in analyze mode.

    In remote mode, *archive_local_path* is uploaded to GCS first, and the
    worker downloads it from the API at runtime.

    Returns the Job name.
    """
    if _is_remote():
        return _create_remote_analysis_job(
            session_id=session_id,
            user_query=user_query,
            archive_local_path=archive_local_path,
            owner=owner,
        )

    return _create_local_job(
        session_id=session_id,
        env_extras=[
            client.V1EnvVar(name="USER_QUERY", value=user_query),
            client.V1EnvVar(name="MODE", value="analyze"),
            client.V1EnvVar(name="OWNER", value=owner),
        ],
        must_gather_base_dir=must_gather_base_dir,
        owner=owner,
    )


def create_deepening_job(
    session_id: str,
    orch_session_id: str,
    feedback_text: str,
    must_gather_base_dir: str,
    owner: str = "",
) -> str:
    """Create a K8s Job for a feedback / deepening round.

    Deletes any existing Job for this session first (the completed analysis
    Job) to avoid name collisions.  Increments ``deepening_round`` in
    status.json so the admin portal can display which round is running.

    Returns the Job name.
    """
    job_dir = get_job_dir(session_id)
    prev_round = 1
    status_file = job_dir / "status.json"
    if status_file.exists():
        try:
            prev = json.loads(status_file.read_text(encoding="utf-8"))
            prev_round = prev.get("deepening_round", 1)
        except Exception:
            pass
    _write_status_file(job_dir, {"deepening_round": prev_round + 1})

    if _is_remote():
        return _create_remote_deepening_job(
            session_id=session_id,
            orch_session_id=orch_session_id,
            feedback_text=feedback_text,
            owner=owner,
        )

    _delete_existing_job(session_id)

    return _create_local_job(
        session_id=session_id,
        env_extras=[
            client.V1EnvVar(name="MODE", value="deepening"),
            client.V1EnvVar(name="ORCHESTRATOR_SESSION_ID", value=orch_session_id),
            client.V1EnvVar(name="FEEDBACK_TEXT", value=feedback_text),
            client.V1EnvVar(name="OWNER", value=owner),
        ],
        must_gather_base_dir=must_gather_base_dir,
        owner=owner,
    )


def get_job_status(session_id: str) -> dict:
    """Read status.json for this session.

    In remote mode, status.json is populated by callback handlers.
    Falls back to the K8s Job phase when status.json is missing or stale.
    """
    status_file = get_job_dir(session_id) / "status.json"
    status: dict = {}
    if status_file.exists():
        try:
            raw = json.loads(status_file.read_text(encoding="utf-8"))
            status = {k: v for k, v in raw.items() if not k.startswith("_")}
        except Exception:
            pass

    if status.get("status") in ("completed", "error"):
        return status

    if _is_remote():
        k8s_phase = _remote_job_phase(session_id)
    else:
        k8s_phase = _k8s_job_phase(session_id)

    if k8s_phase == "failed" and status.get("status") != "error":
        status.update({"status": "error", "message": "Worker pod failed unexpectedly"})
    elif not status:
        status = {
            "session_id": session_id,
            "status": "running" if k8s_phase in ("active", "unknown") else k8s_phase,
            "phase": "initializing",
            "progress": 0,
            "message": f"Job phase: {k8s_phase}",
        }
    return status


def cancel_job(session_id: str) -> dict:
    """Delete the K8s Job (cascading) to terminate the worker pod."""
    job_dir = get_job_dir(session_id)

    if _is_remote():
        import remote_cluster
        import object_storage

        ns = remote_cluster.get_namespace_for_session(session_id)
        if ns:
            name = _job_name(session_id)
            try:
                remote_cluster.delete_job(ns, name)
            except Exception:
                pass
            remote_cluster.release_namespace(session_id)
        try:
            object_storage.delete_session_objects(session_id)
        except Exception:
            pass

        _write_status_file(job_dir, {
            "session_id": session_id,
            "status": "cancelled",
            "message": "Analysis cancelled by user",
        })
        return {"status": "cancelled", "session_id": session_id}

    _init_k8s()
    name = _job_name(session_id)
    try:
        _batch_v1.delete_namespaced_job(
            name=name,
            namespace=NAMESPACE,
            body=client.V1DeleteOptions(propagation_policy="Foreground"),
        )
        _write_status_file(job_dir, {
            "session_id": session_id,
            "status": "cancelled",
            "message": "Analysis cancelled by user",
        })
        return {"status": "cancelled", "session_id": session_id}
    except ApiException as exc:
        if exc.status == 404:
            return {"status": "not_found", "session_id": session_id}
        raise


def cleanup_job(session_id: str) -> None:
    """Delete the K8s Job resource and remove ALL job data."""
    if _is_remote():
        import remote_cluster
        import object_storage

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
    else:
        _init_k8s()
        _delete_k8s_job_resource(session_id)

    job_dir = get_job_dir(session_id)
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)


def cleanup_job_input(session_id: str) -> None:
    """Delete only the bulky extracted must-gather input/ dir on the PVC.

    Keeps results/, status.json, agent_memory.json so the session still
    appears in the Portal jobs board after completion.
    """
    input_dir = get_job_dir(session_id) / "input"
    if input_dir.exists():
        shutil.rmtree(input_dir, ignore_errors=True)
        logger.info("Cleaned up input/ for session %s (reclaimed PVC space)", session_id)


def _delete_k8s_job_resource(session_id: str) -> None:
    """Delete only the K8s Job resource (not PVC data)."""
    _init_k8s()
    name = _job_name(session_id)
    try:
        _batch_v1.delete_namespaced_job(
            name=name,
            namespace=NAMESPACE,
            body=client.V1DeleteOptions(propagation_policy="Background"),
        )
    except ApiException as exc:
        if exc.status != 404:
            logger.warning("Failed to delete Job %s: %s", name, exc)


def list_all_jobs(owner: str | None = None) -> list[dict]:
    """Return every session — from live K8s Jobs AND from status files.

    K8s Jobs are auto-deleted after TTL, but status.json files persist.
    This ensures the Portal jobs board keeps showing completed sessions
    long after the K8s Job resource is garbage-collected.

    When *owner* is provided, only sessions belonging to that owner are returned.
    """
    seen_sids: set[str] = set()
    result: list[dict] = []

    if _is_remote():
        import remote_cluster
        for claim in remote_cluster.list_active_claims():
            sid = claim.get("session_id", "")
            if not sid or sid in seen_sids:
                continue
            seen_sids.add(sid)
            status_data = get_job_status(sid)
            result.append(_session_entry(sid, status_data, None))
    else:
        _init_k8s()
        try:
            jobs = _batch_v1.list_namespaced_job(
                namespace=NAMESPACE,
                label_selector="app=rca-worker",
            )
        except ApiException:
            jobs = None

        if jobs:
            for job in jobs.items:
                sid = (job.metadata.labels or {}).get("session-id", "")
                if not sid:
                    continue
                seen_sids.add(sid)
                status_data = get_job_status(sid)
                created_at = None
                if job.metadata.creation_timestamp:
                    created_at = job.metadata.creation_timestamp.isoformat()
                result.append(_session_entry(sid, status_data, created_at))

    jobs_root = Path(LOCAL_SESSION_DIR if _is_remote() else SHARED_PVC_MOUNT) / "jobs"
    if jobs_root.is_dir():
        for entry in jobs_root.iterdir():
            if not entry.is_dir():
                continue
            sid = entry.name
            if sid in seen_sids:
                continue
            status_file = entry / "status.json"
            if not status_file.exists():
                continue
            try:
                status_data = json.loads(status_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            created_at = status_data.get("updated_at")
            seen_sids.add(sid)
            result.append(_session_entry(sid, status_data, created_at))

    if owner:
        result = [e for e in result if e.get("owner", "").lower() == owner.lower()]

    result.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    return result


def list_active_jobs() -> list[dict]:
    """Return only currently active/running jobs (for admin stats)."""
    if _is_remote():
        import remote_cluster
        return [
            {"session_id": c["session_id"], "name": c["namespace"]}
            for c in remote_cluster.list_active_claims()
        ]

    _init_k8s()
    try:
        jobs = _batch_v1.list_namespaced_job(
            namespace=NAMESPACE,
            label_selector="app=rca-worker",
        )
    except ApiException:
        return []

    active = []
    if jobs:
        for job in jobs.items:
            if job.status.active and job.status.active > 0:
                sid = (job.metadata.labels or {}).get("session-id", "")
                active.append({"session_id": sid, "name": job.metadata.name})
    return active


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

def _write_status_file(job_dir: Path, status: dict) -> None:
    """Write status.json atomically, preserving sticky fields from previous state."""
    from datetime import datetime
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
    status["updated_at"] = datetime.now().isoformat()
    tmp = job_dir / "status.json.tmp"
    tmp.write_text(json.dumps(status, indent=2), encoding="utf-8")
    tmp.rename(final)


def _delete_existing_job(session_id: str) -> None:
    """Delete a completed/failed Job for this session so a new one can be created."""
    _init_k8s()
    name = _job_name(session_id)
    try:
        _batch_v1.delete_namespaced_job(
            name=name,
            namespace=NAMESPACE,
            body=client.V1DeleteOptions(propagation_policy="Background"),
        )
        logger.info("Deleted existing Job %s before creating replacement", name)
    except ApiException as exc:
        if exc.status != 404:
            logger.warning("Failed to delete existing Job %s: %s", name, exc)


def _k8s_job_phase(session_id: str) -> str:
    """Query K8s for the Job phase: active / succeeded / failed / unknown."""
    _init_k8s()
    name = _job_name(session_id)
    try:
        job = _batch_v1.read_namespaced_job_status(name=name, namespace=NAMESPACE)
    except ApiException:
        return "unknown"
    if job.status.succeeded and job.status.succeeded > 0:
        return "succeeded"
    if job.status.failed and job.status.failed > 0:
        return "failed"
    if job.status.active and job.status.active > 0:
        return "active"
    return "unknown"


def _create_local_job(
    session_id: str,
    env_extras: list[client.V1EnvVar],
    must_gather_base_dir: str,
    owner: str = "",
) -> str:
    """Build and create the K8s Job resource (single-cluster mode)."""
    _init_k8s()

    if not WORKER_IMAGE:
        raise RuntimeError(
            "WORKER_IMAGE environment variable is not set. "
            "It must point to the RCA container image (same image as the API pod)."
        )

    name = _job_name(session_id)
    job_dir_path = _job_dir_on_pvc(session_id)

    env = [
        client.V1EnvVar(name="SESSION_ID", value=session_id),
        client.V1EnvVar(name="JOB_DIR", value=job_dir_path),
        client.V1EnvVar(name="MUST_GATHER_BASE_DIR", value=must_gather_base_dir),
        client.V1EnvVar(name="GOOGLE_APPLICATION_CREDENTIALS",
                        value="/secrets/gcloud/application_default_credentials.json"),
        client.V1EnvVar(name="CLOUDSDK_CONFIG", value="/tmp/gcloud"),
        client.V1EnvVar(name="HOME", value="/tmp"),
    ] + env_extras

    volume_mounts = [
        client.V1VolumeMount(name="shared-data", mount_path=SHARED_PVC_MOUNT),
        client.V1VolumeMount(name="gcloud-adc", mount_path="/secrets/gcloud", read_only=True),
        client.V1VolumeMount(name="app-config-secret",
                             mount_path="/app/config/config.json",
                             sub_path="config.json", read_only=True),
        client.V1VolumeMount(name="log-config",
                             mount_path="/app/config/log_config.json",
                             sub_path="log_config.json", read_only=True),
    ]

    volumes = [
        client.V1Volume(
            name="shared-data",
            persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                claim_name=SHARED_PVC_CLAIM_NAME,
            ),
        ),
        client.V1Volume(
            name="gcloud-adc",
            secret=client.V1SecretVolumeSource(secret_name=GCP_SECRET_NAME),
        ),
        client.V1Volume(
            name="app-config-secret",
            secret=client.V1SecretVolumeSource(secret_name=APP_CONFIG_SECRET_NAME),
        ),
        client.V1Volume(
            name="log-config",
            config_map=client.V1ConfigMapVolumeSource(name=LOG_CONFIGMAP_NAME),
        ),
    ]

    container = client.V1Container(
        name="rca-worker",
        image=WORKER_IMAGE,
        image_pull_policy="Always",
        command=["python", "worker.py"],
        env=env,
        volume_mounts=volume_mounts,
        resources=client.V1ResourceRequirements(
            requests={"cpu": WORKER_CPU_REQUEST, "memory": WORKER_MEM_REQUEST},
            limits={"cpu": WORKER_CPU_LIMIT, "memory": WORKER_MEM_LIMIT},
        ),
    )

    safe_owner = (owner or "unknown").replace(" ", "_").replace(":", "_")[:63]

    job = client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=client.V1ObjectMeta(
            name=name,
            namespace=NAMESPACE,
            labels={
                "app": "rca-worker",
                "session-id": session_id[:63],
                "owner": safe_owner,
            },
        ),
        spec=client.V1JobSpec(
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(
                    labels={
                        "app": "rca-worker",
                        "session-id": session_id[:63],
                        "owner": safe_owner,
                    },
                ),
                spec=client.V1PodSpec(
                    restart_policy="Never",
                    containers=[container],
                    volumes=volumes,
                    affinity=client.V1Affinity(
                        pod_affinity=client.V1PodAffinity(
                            required_during_scheduling_ignored_during_execution=[
                                client.V1PodAffinityTerm(
                                    label_selector=client.V1LabelSelector(
                                        match_labels={"app": "must-gather-api"},
                                    ),
                                    topology_key="kubernetes.io/hostname",
                                )
                            ]
                        )
                    ),
                ),
            ),
            backoff_limit=0,
            active_deadline_seconds=JOB_ACTIVE_DEADLINE,
            ttl_seconds_after_finished=JOB_TTL_AFTER_FINISHED,
        ),
    )

    _batch_v1.create_namespaced_job(namespace=NAMESPACE, body=job)
    logger.info("Created K8s Job %s for session %s (owner=%s)", name, session_id, owner)

    job_dir = get_job_dir(session_id)
    _write_status_file(job_dir, {
        "session_id": session_id,
        "status": "running",
        "phase": "initializing",
        "progress": 0,
        "message": "Job created, waiting for pod to start...",
        "owner": owner or "unknown",
    })

    return name


# ---------------------------------------------------------------------------
# Cross-cluster (remote) job helpers
# ---------------------------------------------------------------------------

def _remote_job_phase(session_id: str) -> str:
    """Query the remote cluster for the Job phase."""
    try:
        import remote_cluster
        ns = remote_cluster.get_namespace_for_session(session_id)
        if not ns:
            return "unknown"
        name = _job_name(session_id)
        return remote_cluster.get_job_phase(ns, name)
    except Exception:
        return "unknown"


def _generate_callback_token(session_id: str, job_dir: Path) -> str:
    """Generate a per-session callback token and persist it in status.json."""
    token = _secrets.token_urlsafe(32)
    status_file = job_dir / "status.json"
    if status_file.exists():
        try:
            data = json.loads(status_file.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    else:
        data = {}
    data["_callback_token"] = token
    job_dir.mkdir(parents=True, exist_ok=True)
    status_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return token


def _create_remote_analysis_job(
    session_id: str,
    user_query: str,
    archive_local_path: Path | None,
    owner: str = "",
) -> str:
    """Upload to GCS, claim a namespace from the pool, create Job."""
    import remote_cluster
    import object_storage

    job_dir = get_job_dir(session_id)
    job_dir.mkdir(parents=True, exist_ok=True)

    if archive_local_path and archive_local_path.exists():
        object_storage.upload_archive(session_id, archive_local_path)

    callback_token = _generate_callback_token(session_id, job_dir)

    ns = remote_cluster.claim_namespace(session_id)

    env_extras = [
        client.V1EnvVar(name="USER_QUERY", value=user_query),
        client.V1EnvVar(name="MODE", value="analyze"),
        client.V1EnvVar(name="OWNER", value=owner),
        client.V1EnvVar(name="COMMS_MODE", value="callback"),
        client.V1EnvVar(name="API_CALLBACK_URL", value=API_CALLBACK_URL),
        client.V1EnvVar(name="CALLBACK_TOKEN", value=callback_token),
    ]

    name = remote_cluster.create_remote_job(
        namespace=ns,
        session_id=session_id,
        env_extras=env_extras,
    )

    _write_status_file(job_dir, {
        "session_id": session_id,
        "status": "running",
        "phase": "initializing",
        "progress": 0,
        "message": "Remote job created, waiting for pod to start...",
        "owner": owner or "unknown",
        "_remote_namespace": ns,
    })

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
    to avoid name collisions.
    """
    import remote_cluster

    job_dir = get_job_dir(session_id)

    ns = remote_cluster.get_namespace_for_session(session_id)
    if not ns:
        ns = remote_cluster.claim_namespace(session_id)

    try:
        old_name = _job_name(session_id)
        remote_cluster.delete_job(ns, old_name)
    except Exception:
        pass

    callback_token = _generate_callback_token(session_id, job_dir)

    env_extras = [
        client.V1EnvVar(name="MODE", value="deepening"),
        client.V1EnvVar(name="ORCHESTRATOR_SESSION_ID", value=orch_session_id),
        client.V1EnvVar(name="FEEDBACK_TEXT", value=feedback_text),
        client.V1EnvVar(name="OWNER", value=owner),
        client.V1EnvVar(name="COMMS_MODE", value="callback"),
        client.V1EnvVar(name="API_CALLBACK_URL", value=API_CALLBACK_URL),
        client.V1EnvVar(name="CALLBACK_TOKEN", value=callback_token),
    ]

    name = remote_cluster.create_remote_job(
        namespace=ns,
        session_id=session_id,
        env_extras=env_extras,
    )

    _write_status_file(job_dir, {
        "session_id": session_id,
        "status": "running",
        "phase": "initializing",
        "progress": 5,
        "message": "Remote deepening job created...",
        "owner": owner or "unknown",
        "_remote_namespace": ns,
    })

    logger.info("Created remote deepening job %s in namespace %s", name, ns)
    return name


def get_callback_token(session_id: str) -> str:
    """Retrieve the stored callback token for a session."""
    status_file = get_job_dir(session_id) / "status.json"
    if status_file.exists():
        try:
            data = json.loads(status_file.read_text(encoding="utf-8"))
            return data.get("_callback_token", "")
        except Exception:
            pass
    return ""


def trigger_remote_cleanup(session_id: str) -> None:
    """Release the namespace back to the pool and clean up GCS objects."""
    if not _is_remote():
        return

    import remote_cluster
    import object_storage

    remote_cluster.release_namespace(session_id)
    try:
        object_storage.delete_session_objects(session_id)
    except Exception:
        logger.warning("Failed to delete GCS objects for session %s", session_id)
