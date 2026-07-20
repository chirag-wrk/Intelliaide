"""
Kubernetes Job runner for RCA worker pods.

Creates, monitors, cancels and cleans up batch/v1 Job resources that execute
the ``worker.py`` entrypoint inside the pre-built RCA image.

Supports two modes controlled by ``JOBS_CLUSTER_MODE``:

- ``disabled`` (default) — single-cluster mode.  All data exchange happens
  through a shared PVC mounted at ``SHARED_PVC_MOUNT``.
- ``enabled`` — cross-cluster mode.  Jobs run in ephemeral namespaces on a
  remote cluster.  Job metadata is stored as K8s annotations; results and
  session state are persisted to GCS.
"""

import json
import logging
import os
import secrets as _secrets
import shutil
from datetime import datetime
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

WORKER_CPU_REQUEST: str = os.environ.get("WORKER_CPU_REQUEST", "1")
WORKER_CPU_LIMIT: str = os.environ.get("WORKER_CPU_LIMIT", "2")
WORKER_MEM_REQUEST: str = os.environ.get("WORKER_MEM_REQUEST", "1Gi")
WORKER_MEM_LIMIT: str = os.environ.get("WORKER_MEM_LIMIT", "2Gi")

JOBS_CLUSTER_MODE: str = os.environ.get("JOBS_CLUSTER_MODE", "disabled")
API_CALLBACK_URL: str = os.environ.get("API_CALLBACK_URL", "")
CALLBACK_VERIFY_SSL: str = os.environ.get("CALLBACK_VERIFY_SSL", "false")

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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_job_dir(session_id: str) -> Path | None:
    """Return the job directory on the shared PVC (single-cluster mode).

    Returns ``None`` in remote mode — callers must use K8s annotations
    and GCS instead.
    """
    if _is_remote():
        return None
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
    Job) to avoid name collisions.  Increments ``deepening_round`` so the
    admin portal can display which round is running.

    Returns the Job name.
    """
    if _is_remote():
        return _create_remote_deepening_job(
            session_id=session_id,
            orch_session_id=orch_session_id,
            feedback_text=feedback_text,
            owner=owner,
        )

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
    """Read job status for this session.

    In remote mode, reads the ``rca.intelliaide/status`` annotation from the
    K8s Job.  Falls back to GCS ``status.json`` for TTL-deleted Jobs.
    In single-cluster mode, reads ``status.json`` from the PVC.
    """
    if _is_remote():
        return _get_remote_job_status(session_id)

    status_file = Path(_job_dir_on_pvc(session_id)) / "status.json"
    status: dict = {}
    if status_file.exists():
        try:
            raw = json.loads(status_file.read_text(encoding="utf-8"))
            status = {k: v for k, v in raw.items() if not k.startswith("_")}
        except Exception:
            pass

    if status.get("status") in ("completed", "error"):
        return status

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


def _get_remote_job_status(session_id: str) -> dict:
    """Read status from K8s Job annotation, falling back to GCS."""
    import remote_cluster
    import object_storage

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
    if _is_remote():
        import remote_cluster
        import object_storage

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

    _init_k8s()
    job_dir = Path(_job_dir_on_pvc(session_id))
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
        return

    _init_k8s()
    _delete_k8s_job_resource(session_id)
    job_dir = Path(_job_dir_on_pvc(session_id))
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)


def cleanup_job_input(session_id: str) -> None:
    """Delete only the bulky extracted must-gather input/ dir on the PVC.

    In remote mode this is a no-op (the archive is in GCS, worker uses
    emptyDir scratch).
    """
    if _is_remote():
        return
    input_dir = Path(_job_dir_on_pvc(session_id)) / "input"
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
    """Return every session — from live K8s Jobs AND from historical records.

    In remote mode, queries live K8s Jobs across the namespace pool and
    falls back to GCS ``status.json`` objects for TTL-deleted sessions.
    In single-cluster mode, queries local K8s and PVC filesystem.

    When *owner* is provided, only sessions belonging to that owner are returned.
    """
    seen_sids: set[str] = set()
    result: list[dict] = []

    if _is_remote():
        import remote_cluster
        import object_storage

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

        jobs_root = Path(SHARED_PVC_MOUNT) / "jobs"
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
    """Write status.json atomically, preserving sticky fields (single-cluster mode)."""
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


def _write_remote_status(session_id: str, status: dict) -> None:
    """Patch the status annotation on the K8s Job (cross-cluster mode).

    Also writes to GCS on terminal status for historical persistence.
    """
    import remote_cluster
    import object_storage

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

    job_dir = Path(_job_dir_on_pvc(session_id))
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


def _create_remote_analysis_job(
    session_id: str,
    user_query: str,
    archive_local_path: Path | None,
    owner: str = "",
) -> str:
    """Upload archive to GCS, claim a namespace, create Job with annotations."""
    import remote_cluster
    import object_storage

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
    import remote_cluster

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
    """Retrieve the callback token for a session.

    In remote mode, reads from K8s Job annotation.
    In single-cluster mode, reads from status.json on the PVC.
    """
    if _is_remote():
        import remote_cluster
        ns = remote_cluster.get_namespace_for_session(session_id)
        if not ns:
            return ""
        ann = remote_cluster.get_job_annotations(ns, _job_name(session_id))
        return ann.get(remote_cluster._ann("callback-token"), "")

    status_file = Path(_job_dir_on_pvc(session_id)) / "status.json"
    if status_file.exists():
        try:
            data = json.loads(status_file.read_text(encoding="utf-8"))
            return data.get("_callback_token", "")
        except Exception:
            pass
    return ""


def trigger_remote_cleanup(session_id: str) -> None:
    """Release the namespace back to the pool and clean up the input archive.

    Preserves results and status.json in GCS for historical queries.
    """
    if not _is_remote():
        return

    import remote_cluster
    import object_storage

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
