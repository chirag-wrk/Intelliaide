"""
Remote Kubernetes cluster operations for cross-cluster job dispatch.

Namespaces on the jobs cluster are **pre-provisioned in a pool** and made
available via a ConfigMap mounted on the API pod.  This module picks a
namespace from the pool, creates Jobs in it, and monitors them.  Namespace
recycling after job completion is handled by an external operator — the API
does not create or delete namespaces.  Secrets (GCP ADC, app-config) are
copied from the API's control-cluster namespace into the target namespace
at Job creation time.

The pool ConfigMap (mounted as a file) contains a JSON list of namespace
names.  Active K8s Jobs are the sole source of truth for which namespaces
are in use — there is no in-memory session-to-namespace mapping.

Uses a dedicated kubeconfig (mounted as a Secret on the API pod) so
that in-cluster auth for the control cluster is not disturbed.
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)

JOBS_CLUSTER_KUBECONFIG: str = os.environ.get("JOBS_CLUSTER_KUBECONFIG", "")

K8S_NAMESPACE: str = os.environ.get("K8S_NAMESPACE", "intellaide-system")
GCP_SECRET_NAME: str = os.environ.get("GCP_SECRET_NAME", "gcp-credentials")
APP_CONFIG_SECRET_NAME: str = os.environ.get("APP_CONFIG_SECRET_NAME", "intellaide-system-app-config")

NAMESPACE_POOL_FILE: str = os.environ.get(
    "NAMESPACE_POOL_FILE", "/config/namespace-pool/namespaces.json"
)

WORKER_IMAGE: str = os.environ.get("WORKER_IMAGE", "")
WORKER_CPU_REQUEST: str = os.environ.get("WORKER_CPU_REQUEST", "1")
WORKER_CPU_LIMIT: str = os.environ.get("WORKER_CPU_LIMIT", "2")
WORKER_MEM_REQUEST: str = os.environ.get("WORKER_MEM_REQUEST", "1Gi")
WORKER_MEM_LIMIT: str = os.environ.get("WORKER_MEM_LIMIT", "2Gi")

JOB_ACTIVE_DEADLINE: int = int(os.environ.get("JOB_ACTIVE_DEADLINE_SECONDS", "3600"))
JOB_TTL_AFTER_FINISHED: int = int(os.environ.get("JOB_TTL_AFTER_FINISHED", "600"))

ANNOTATION_PREFIX = "rca.intelliaide"

_api_client: Optional[client.ApiClient] = None
_batch_v1: Optional[client.BatchV1Api] = None
_jobs_core_v1: Optional[client.CoreV1Api] = None
_control_core_v1: Optional[client.CoreV1Api] = None


def _init() -> None:
    global _api_client, _batch_v1, _jobs_core_v1, _control_core_v1
    if _batch_v1 is not None:
        return

    if not JOBS_CLUSTER_KUBECONFIG:
        raise RuntimeError(
            "JOBS_CLUSTER_KUBECONFIG is not set. "
            "It must point to a kubeconfig file for the remote jobs cluster."
        )

    _api_client = config.new_client_from_config(config_file=JOBS_CLUSTER_KUBECONFIG)
    _batch_v1 = client.BatchV1Api(api_client=_api_client)
    _jobs_core_v1 = client.CoreV1Api(api_client=_api_client)

    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    _control_core_v1 = client.CoreV1Api()


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


def _get_active_jobs_by_namespace() -> dict[str, list]:
    """Return {namespace: [active V1Job, ...]} across all pool namespaces."""
    result: dict[str, list] = {}
    try:
        pool = _load_pool()
    except RuntimeError:
        return result
    for ns in pool:
        try:
            jobs = _batch_v1.list_namespaced_job(
                namespace=ns, label_selector="app=rca-worker",
            )
        except ApiException:
            continue
        active = []
        for job in jobs.items:
            is_done = (
                (job.status.succeeded and job.status.succeeded > 0)
                or (job.status.failed and job.status.failed > 0)
            )
            if not is_done:
                active.append(job)
        if active:
            result[ns] = active
    return result


# ---- Namespace pool operations ----

def claim_namespace(session_id: str) -> str:
    """Pick an available namespace from the pool for a session.

    Looks at live K8s Jobs to determine which namespaces are busy.
    If the session already has an active Job, returns that namespace.
    Raises RuntimeError if the pool is exhausted.
    """
    _init()
    pool = _load_pool()
    active_by_ns = _get_active_jobs_by_namespace()

    for ns, jobs in active_by_ns.items():
        for job in jobs:
            sid = (job.metadata.labels or {}).get("session-id", "")
            if sid == session_id[:63]:
                return ns

    in_use = set(active_by_ns.keys())
    for ns in pool:
        if ns not in in_use:
            logger.info("Claimed namespace %s for session %s", ns, session_id)
            return ns

    raise RuntimeError(
        f"No available namespaces in pool (pool size={len(pool)}, "
        f"in-use={len(in_use)}). Wait for a running job to complete."
    )


def release_namespace(session_id: str) -> None:
    """No-op — namespace availability is determined by Job state."""


def get_namespace_for_session(session_id: str) -> str:
    """Return the namespace containing a Job for this session, or empty string."""
    try:
        _init()
    except Exception:
        return ""

    try:
        pool = _load_pool()
    except RuntimeError:
        return ""

    for ns in pool:
        try:
            jobs = _batch_v1.list_namespaced_job(
                namespace=ns,
                label_selector=f"app=rca-worker,session-id={session_id[:63]}",
            )
        except ApiException:
            continue
        if jobs.items:
            return ns
    return ""


def list_active_claims() -> list[dict]:
    """Return all namespaces with active (non-finished) Jobs."""
    _init()
    active_by_ns = _get_active_jobs_by_namespace()
    result = []
    for ns, jobs in active_by_ns.items():
        for job in jobs:
            sid = (job.metadata.labels or {}).get("session-id", "")
            result.append({"session_id": sid, "namespace": ns})
    return result


# ---- Annotation helpers ----

def _ann(key: str) -> str:
    """Return the fully-qualified annotation key."""
    return f"{ANNOTATION_PREFIX}/{key}"


def get_job_annotations(namespace: str, job_name: str) -> dict[str, str]:
    """Read annotations from a Job. Returns empty dict on 404."""
    _init()
    try:
        job = _batch_v1.read_namespaced_job(name=job_name, namespace=namespace)
        return dict(job.metadata.annotations or {})
    except ApiException:
        return {}


def patch_job_annotations(namespace: str, job_name: str,
                          annotations: dict[str, str]) -> None:
    """Strategic-merge-patch annotations onto a Job."""
    _init()
    try:
        _batch_v1.patch_namespaced_job(
            name=job_name,
            namespace=namespace,
            body={"metadata": {"annotations": annotations}},
        )
    except ApiException as exc:
        logger.warning("Failed to patch annotations on Job %s/%s: %s",
                       namespace, job_name, exc)


def list_jobs_in_pool(label_selector: str = "app=rca-worker") -> list[client.V1Job]:
    """Query all pool namespaces and return matching Jobs."""
    _init()
    result: list[client.V1Job] = []
    try:
        pool = _load_pool()
    except RuntimeError:
        return result
    for ns in pool:
        try:
            jobs = _batch_v1.list_namespaced_job(
                namespace=ns, label_selector=label_selector,
            )
            result.extend(jobs.items)
        except ApiException:
            continue
    return result


# ---- Secret copying ----

def _copy_secrets_to_namespace(target_namespace: str) -> None:
    """Copy GCP and app-config secrets from the control cluster to the jobs cluster namespace."""
    _init()
    for secret_name in (GCP_SECRET_NAME, APP_CONFIG_SECRET_NAME):
        try:
            source = _control_core_v1.read_namespaced_secret(
                name=secret_name, namespace=K8S_NAMESPACE,
            )
        except ApiException as exc:
            raise RuntimeError(
                f"Cannot read secret {secret_name} from {K8S_NAMESPACE}: {exc}"
            )

        target = client.V1Secret(
            metadata=client.V1ObjectMeta(
                name=secret_name, namespace=target_namespace,
            ),
            data=source.data,
            type=source.type,
        )

        try:
            _jobs_core_v1.create_namespaced_secret(
                namespace=target_namespace, body=target,
            )
            logger.info("Created secret %s in namespace %s",
                        secret_name, target_namespace)
        except ApiException as exc:
            if exc.status == 409:
                _jobs_core_v1.replace_namespaced_secret(
                    name=secret_name, namespace=target_namespace, body=target,
                )
                logger.info("Updated secret %s in namespace %s",
                            secret_name, target_namespace)
            else:
                raise


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
    callback_token: str = "",
    initial_status: dict | None = None,
) -> str:
    """Create a K8s Job in a pre-provisioned namespace.

    Copies GCP ADC and app-config secrets from the API's control-cluster
    namespace into the target namespace before creating the Job.

    *callback_token* and *initial_status* are stored as Job annotations
    so the API can read them back without local filesystem persistence.
    """
    _init()

    image = worker_image or WORKER_IMAGE
    if not image:
        raise RuntimeError("WORKER_IMAGE is not set")

    _copy_secrets_to_namespace(namespace)

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
            secret=client.V1SecretVolumeSource(secret_name=GCP_SECRET_NAME),
        ),
        client.V1Volume(
            name="app-config-secret",
            secret=client.V1SecretVolumeSource(secret_name=APP_CONFIG_SECRET_NAME),
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

    annotations = {}
    if callback_token:
        annotations[_ann("callback-token")] = callback_token
    if initial_status:
        annotations[_ann("status")] = json.dumps(initial_status)
    annotations[_ann("deepening-round")] = str(
        initial_status.get("deepening_round", 0) if initial_status else 0
    )

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
            annotations=annotations,
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
