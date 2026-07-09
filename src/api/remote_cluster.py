"""
Remote Kubernetes cluster operations for cross-cluster job dispatch.

Namespaces on the jobs cluster are **pre-provisioned in a pool** and made
available via a ConfigMap mounted on the API pod.  This module claims a
namespace from the pool, creates Jobs in it, and monitors them.  Namespace
recycling after job completion is handled by an external operator — the API
does not create or delete namespaces, nor provision secrets.

The pool ConfigMap (mounted as a file) contains a JSON list of namespace
names.  The API tracks which ones are in use in-memory and persists the
mapping to local storage so it survives restarts.

Uses a dedicated kubeconfig (mounted as a Secret on the API pod) so
that in-cluster auth for the control cluster is not disturbed.
"""

import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)

JOBS_CLUSTER_KUBECONFIG: str = os.environ.get("JOBS_CLUSTER_KUBECONFIG", "")

NAMESPACE_POOL_FILE: str = os.environ.get(
    "NAMESPACE_POOL_FILE", "/config/namespace-pool/namespaces.json"
)

WORKER_IMAGE: str = os.environ.get("WORKER_IMAGE", "")
WORKER_CPU_REQUEST: str = os.environ.get("WORKER_CPU_REQUEST", "250m")
WORKER_CPU_LIMIT: str = os.environ.get("WORKER_CPU_LIMIT", "2000m")
WORKER_MEM_REQUEST: str = os.environ.get("WORKER_MEM_REQUEST", "512Mi")
WORKER_MEM_LIMIT: str = os.environ.get("WORKER_MEM_LIMIT", "2Gi")

JOB_ACTIVE_DEADLINE: int = int(os.environ.get("JOB_ACTIVE_DEADLINE_SECONDS", "3600"))
JOB_TTL_AFTER_FINISHED: int = int(os.environ.get("JOB_TTL_AFTER_FINISHED", "600"))

LOCAL_SESSION_DIR: str = os.environ.get("LOCAL_SESSION_DIR", "/data/sessions")

_api_client: Optional[client.ApiClient] = None
_batch_v1: Optional[client.BatchV1Api] = None

_pool_lock = threading.Lock()
_claimed: dict[str, str] = {}  # session_id -> namespace


def _init() -> None:
    global _api_client, _batch_v1
    if _batch_v1 is not None:
        return

    if not JOBS_CLUSTER_KUBECONFIG:
        raise RuntimeError(
            "JOBS_CLUSTER_KUBECONFIG is not set. "
            "It must point to a kubeconfig file for the remote jobs cluster."
        )

    _api_client = config.new_client_from_config(config_file=JOBS_CLUSTER_KUBECONFIG)
    _batch_v1 = client.BatchV1Api(api_client=_api_client)

    _load_claims()


def _claims_file() -> Path:
    return Path(LOCAL_SESSION_DIR) / "_namespace_claims.json"


def _save_claims() -> None:
    """Persist the session→namespace mapping to disk."""
    p = _claims_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(_claimed, indent=2), encoding="utf-8")


def _load_claims() -> None:
    """Restore the session→namespace mapping from disk on startup."""
    global _claimed
    p = _claims_file()
    if p.exists():
        try:
            _claimed = json.loads(p.read_text(encoding="utf-8"))
            logger.info("Restored %d namespace claims from disk", len(_claimed))
        except Exception:
            _claimed = {}


def _load_pool() -> list[str]:
    """Read the namespace pool from the mounted ConfigMap file.

    Re-reads on every call so that pool changes (ConfigMap updates)
    are picked up without restarting the API pod.
    """
    pool_path = Path(NAMESPACE_POOL_FILE)
    if not pool_path.exists():
        raise RuntimeError(
            f"Namespace pool file not found: {NAMESPACE_POOL_FILE}. "
            "Mount the ConfigMap containing the namespace list."
        )
    data = json.loads(pool_path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise RuntimeError(
            f"Namespace pool file must contain a non-empty JSON array of namespace names."
        )
    return data


# ---- Namespace pool operations ----

def claim_namespace(session_id: str) -> str:
    """Claim an available namespace from the pool for a session.

    Raises RuntimeError if the pool is exhausted.
    Returns the namespace name.
    """
    with _pool_lock:
        if session_id in _claimed:
            return _claimed[session_id]

        pool = _load_pool()
        in_use = set(_claimed.values())

        for ns in pool:
            if ns not in in_use:
                _claimed[session_id] = ns
                _save_claims()
                logger.info("Claimed namespace %s for session %s", ns, session_id)
                return ns

        raise RuntimeError(
            f"No available namespaces in pool (pool size={len(pool)}, "
            f"in-use={len(in_use)}). Wait for a running job to complete."
        )


def release_namespace(session_id: str) -> None:
    """Release a namespace back to the pool after job completion."""
    with _pool_lock:
        ns = _claimed.pop(session_id, None)
        if ns:
            _save_claims()
            logger.info("Released namespace %s from session %s", ns, session_id)


def get_namespace_for_session(session_id: str) -> str:
    """Return the namespace assigned to a session, or empty string if none."""
    with _pool_lock:
        return _claimed.get(session_id, "")


def list_active_claims() -> list[dict]:
    """Return all current namespace claims."""
    with _pool_lock:
        return [
            {"session_id": sid, "namespace": ns}
            for sid, ns in _claimed.items()
        ]


# ---- Job management ----

def _job_name(session_id: str) -> str:
    safe = session_id.lower().replace("_", "-")
    name = f"rca-{safe}"
    return name[:63]


def create_remote_job(
    namespace: str,
    session_id: str,
    env_extras: list[client.V1EnvVar],
    worker_image: str = "",
) -> str:
    """Create a K8s Job in a pre-provisioned namespace.

    Secrets (GCP ADC, app-config, callback-token) are expected to already
    exist in the namespace.  The Job spec uses emptyDir for scratch space
    and has no pod affinity constraints.
    """
    _init()

    image = worker_image or WORKER_IMAGE
    if not image:
        raise RuntimeError("WORKER_IMAGE is not set")

    name = _job_name(session_id)

    env = [
        client.V1EnvVar(name="SESSION_ID", value=session_id),
        client.V1EnvVar(name="JOB_DIR", value="/workspace"),
        client.V1EnvVar(
            name="GOOGLE_APPLICATION_CREDENTIALS",
            value="/secrets/gcloud/application_default_credentials.json",
        ),
        client.V1EnvVar(name="CLOUDSDK_CONFIG", value="/tmp/gcloud"),
        client.V1EnvVar(name="HOME", value="/tmp"),
    ] + env_extras

    volume_mounts = [
        client.V1VolumeMount(name="workspace", mount_path="/workspace"),
        client.V1VolumeMount(
            name="gcloud-adc", mount_path="/secrets/gcloud", read_only=True,
        ),
        client.V1VolumeMount(
            name="app-config-secret",
            mount_path="/app/Config/config.json",
            sub_path="config.json",
            read_only=True,
        ),
    ]

    volumes = [
        client.V1Volume(
            name="workspace",
            empty_dir=client.V1EmptyDirVolumeSource(),
        ),
        client.V1Volume(
            name="gcloud-adc",
            secret=client.V1SecretVolumeSource(secret_name="gcloud-adc-secret"),
        ),
        client.V1Volume(
            name="app-config-secret",
            secret=client.V1SecretVolumeSource(secret_name="must-gather-app-config"),
        ),
    ]

    container = client.V1Container(
        name="rca-worker",
        image=image,
        image_pull_policy="Always",
        command=["python", "worker.py"],
        env=env,
        volume_mounts=volume_mounts,
        resources=client.V1ResourceRequirements(
            requests={"cpu": WORKER_CPU_REQUEST, "memory": WORKER_MEM_REQUEST},
            limits={"cpu": WORKER_CPU_LIMIT, "memory": WORKER_MEM_LIMIT},
        ),
    )

    safe_owner = _env_extras_get(env_extras, "OWNER", "unknown")
    safe_owner = safe_owner.replace(" ", "_").replace(":", "_")[:63]

    job = client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=client.V1ObjectMeta(
            name=name,
            namespace=namespace,
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
                ),
            ),
            backoff_limit=0,
            active_deadline_seconds=JOB_ACTIVE_DEADLINE,
            ttl_seconds_after_finished=JOB_TTL_AFTER_FINISHED,
        ),
    )

    _batch_v1.create_namespaced_job(namespace=namespace, body=job)
    logger.info("Created remote Job %s in namespace %s", name, namespace)
    return name


def _env_extras_get(env_extras: list, name: str, default: str = "") -> str:
    for env_var in env_extras:
        if env_var.name == name:
            return env_var.value or default
    return default


def get_job_phase(namespace: str, job_name: str) -> str:
    """Query the remote cluster for the Job's phase."""
    _init()
    try:
        job = _batch_v1.read_namespaced_job_status(name=job_name, namespace=namespace)
    except ApiException:
        return "unknown"

    if job.status.succeeded and job.status.succeeded > 0:
        return "succeeded"
    if job.status.failed and job.status.failed > 0:
        return "failed"
    if job.status.active and job.status.active > 0:
        return "active"
    return "unknown"


def delete_job(namespace: str, job_name: str) -> None:
    """Delete a Job with cascading pod termination."""
    _init()
    try:
        _batch_v1.delete_namespaced_job(
            name=job_name,
            namespace=namespace,
            body=client.V1DeleteOptions(propagation_policy="Foreground"),
        )
    except ApiException as exc:
        if exc.status != 404:
            logger.warning("Failed to delete remote Job %s: %s", job_name, exc)
