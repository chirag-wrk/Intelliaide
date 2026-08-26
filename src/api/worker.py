"""
RCA Worker — standalone entrypoint for Kubernetes Job pods.

Reads configuration from environment variables, runs the OrchestratorAgent
workflow, and writes results + progress to a shared PVC directory so the
API pod can relay status to the frontend.

Environment variables
---------------------
SESSION_ID            Unique session identifier (set by the API).
USER_QUERY            Problem statement / user query.
JOB_DIR               Per-job directory on the shared PVC
                      (e.g. /shared/jobs/<session_id>).
MUST_GATHER_BASE_DIR  Resolved path to the must-gather root folder
                      inside JOB_DIR/input/.

MODE                  "analyze" (default) or "deepening".
ORCHESTRATOR_SESSION_ID  (deepening only) Internal orchestrator session id.
FEEDBACK_TEXT            (deepening only) User feedback text.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

_root = Path(__file__).resolve().parent
for p in (_root, _root / "core", _root / "machine_learning"):
    p_str = str(p)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)

from orchestrator_agent import OrchestratorAgent, clear_agent_memory, _TeeWriter
from must_gather_file_selector import MUST_GATHER_DOCS_DIR_DEFAULT
from app_paths import get_results_dir, get_memory_file_path


def _read_env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


_OWNER: str = ""


def _write_status(job_dir: Path, status: dict) -> None:
    """Atomically write status.json (write tmp then rename).

    Automatically preserves the ``owner``, ``problem_statement``, and
    ``case_number`` fields so callers don't have to pass them every time.
    """
    final = job_dir / "status.json"
    if final.exists():
        try:
            prev = json.loads(final.read_text(encoding="utf-8"))
            for keep_key in ("owner", "problem_statement", "case_number", "deepening_round"):
                if keep_key not in status and keep_key in prev:
                    status[keep_key] = prev[keep_key]
        except Exception:
            pass
    if "owner" not in status and _OWNER:
        status["owner"] = _OWNER
    status["updated_at"] = datetime.now().isoformat()
    tmp = job_dir / "status.json.tmp"
    tmp.write_text(json.dumps(status, indent=2), encoding="utf-8")
    tmp.rename(final)


def _copy_results_to_job(results_dir: Path, job_results_dir: Path) -> None:
    """Copy all result files from the app's Results/ dir into the job's
    results directory on the shared PVC."""
    job_results_dir.mkdir(parents=True, exist_ok=True)
    for item in results_dir.iterdir():
        dest = job_results_dir / item.name
        if item.is_file():
            shutil.copy2(item, dest)
        elif item.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(item, dest)


def _copy_agent_memory_to_job(job_dir: Path) -> None:
    """Copy Config/agent_memory.json to the job directory on the shared PVC
    so the API pod can restore it for feedback/deepening rounds."""
    mem_path = get_memory_file_path()
    if mem_path.exists():
        dest = job_dir / "agent_memory.json"
        shutil.copy2(mem_path, dest)


def _cleanup_input(job_dir: Path, session_id: str) -> None:
    """Delete the bulky extracted must-gather input/ dir after the last
    deepening round.  Keeps results/, status.json and agent_memory.json
    so the session still shows in the Portal jobs board."""
    input_dir = job_dir / "input"
    if input_dir.exists():
        shutil.rmtree(input_dir, ignore_errors=True)
        print(f"[Cleanup] Removed input/ for session {session_id} (no more deepening rounds)")


def _resolve_root(extract_base: Path) -> str:
    """If extraction produced a single top-level directory, return it."""
    children = [p for p in extract_base.iterdir() if p.is_dir()]
    if len(children) == 1:
        return str(children[0])
    return str(extract_base)


def _maybe_extract_archive(must_gather_base_dir: str, job_dir: Path,
                           session_id: str) -> str:
    """If *must_gather_base_dir* points to an archive file, extract it into
    the job's ``input/`` directory and return the resolved root path.
    If it's already a directory, return it unchanged."""
    p = Path(must_gather_base_dir)
    if p.is_dir():
        return must_gather_base_dir

    if not p.is_file():
        raise FileNotFoundError(f"Must-gather path not found: {must_gather_base_dir}")

    _write_status(job_dir, {
        "session_id": session_id,
        "status": "running",
        "phase": "extracting",
        "progress": 2,
        "message": f"Extracting {p.name} …",
    })

    extract_dir = job_dir / "input"
    extract_dir.mkdir(parents=True, exist_ok=True)

    size_gb = p.stat().st_size / (1024**3)
    print(f"[Worker] Extracting archive {p.name} ({size_gb:.2f} GB) via CLI tar+pigz …")
    name_lower = p.name.lower()

    if name_lower.endswith(".zip"):
        with zipfile.ZipFile(p, "r") as zf:
            zf.extractall(extract_dir)
    else:
        if name_lower.endswith((".tar.gz", ".tgz")):
            cmd = ["tar", "xf", str(p), "-C", str(extract_dir), "-I", "pigz"]
        elif name_lower.endswith(".tar.bz2"):
            cmd = ["tar", "xjf", str(p), "-C", str(extract_dir)]
        else:
            cmd = ["tar", "xf", str(p), "-C", str(extract_dir)]
        subprocess.run(cmd, check=True)

    p.unlink(missing_ok=True)
    resolved = _resolve_root(extract_dir)
    print(f"[Worker] Extraction complete → {resolved}")
    return resolved


def _run_analyze(session_id: str, user_query: str, must_gather_base_dir: str,
                 job_dir: Path) -> None:
    """Run a fresh analysis workflow."""
    must_gather_base_dir = _maybe_extract_archive(must_gather_base_dir, job_dir, session_id)

    job_results_dir = job_dir / "results"
    job_results_dir.mkdir(parents=True, exist_ok=True)
    results_dir = get_results_dir()

    def progress_callback(event_type, message="", data=None):
        phase_map = {
            "files_identified_intent": ("file_selection", 15),
            "file_selection_llm_result": ("file_selection", 25),
            "files_identified": ("file_selection", 30),
            "files_by_priority": ("file_selection", 35),
            "files_availability": ("file_selection", 40),
            "data_analysis_done": ("yaml_processing", 55),
            "phase3_logs_start": ("log_processing", 60),
            "log_processing_result": ("log_processing", 70),
            "phase4_aggregation": ("data_aggregation", 75),
            "llm_start_intent": ("rca_analysis", 80),
            "llm_start": ("rca_analysis", 85),
            "llm_done": ("rca_analysis", 95),
        }
        if event_type in phase_map:
            phase, prog = phase_map[event_type]
            _write_status(job_dir, {
                "session_id": session_id,
                "status": "running",
                "phase": phase,
                "progress": prog,
                "message": str(message) if message else "",
            })
        if event_type == "terminal_block" and isinstance(data, dict):
            for line in data.get("lines", []):
                s = str(line)
                if "Phase 1" in s:
                    _write_status(job_dir, {"session_id": session_id, "status": "running",
                                            "phase": "file_selection", "progress": 10})
                elif "Phase 2" in s:
                    _write_status(job_dir, {"session_id": session_id, "status": "running",
                                            "phase": "yaml_processing", "progress": 45})
                elif "Phase 3" in s:
                    _write_status(job_dir, {"session_id": session_id, "status": "running",
                                            "phase": "log_processing", "progress": 60})
                elif "Phase 4" in s:
                    _write_status(job_dir, {"session_id": session_id, "status": "running",
                                            "phase": "data_aggregation", "progress": 75})
                elif "Phase 5" in s:
                    _write_status(job_dir, {"session_id": session_id, "status": "running",
                                            "phase": "rca_analysis", "progress": 80})

    _write_status(job_dir, {
        "session_id": session_id,
        "status": "running",
        "phase": "initializing",
        "progress": 0,
        "message": "Starting analysis...",
    })

    clear_agent_memory()

    orchestrator = OrchestratorAgent(reset=False)
    result = orchestrator.execute_workflow(
        user_query,
        MUST_GATHER_DOCS_DIR_DEFAULT,
        must_gather_base_dir=must_gather_base_dir,
        progress_callback=progress_callback,
        output_session_id=session_id,
    )

    _copy_results_to_job(results_dir, job_results_dir)
    _copy_agent_memory_to_job(job_dir)

    orch_status = result.get("status", "error")
    if orch_status != "error":
        _write_status(job_dir, {
            "session_id": session_id,
            "status": "completed",
            "phase": "completed",
            "progress": 100,
            "message": "Analysis complete",
            "rca_summary": result.get("rca_summary"),
            "real_session_id": result.get("session_id"),
        })
    else:
        _write_status(job_dir, {
            "session_id": session_id,
            "status": "error",
            "phase": "error",
            "progress": 0,
            "message": result.get("error", "Unknown error"),
            "error": result.get("error", "Unknown error"),
        })


def _restore_agent_memory_from_pvc(job_dir: Path) -> None:
    """Restore agent_memory.json from the shared PVC into the worker's local
    Config/ so the OrchestratorAgent can find the session from the first pass."""
    src = job_dir / "agent_memory.json"
    if src.exists():
        dest = get_memory_file_path()
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def _run_deepening(session_id: str, orch_session_id: str, feedback_text: str,
                   must_gather_base_dir: str, job_dir: Path) -> None:
    """Run a feedback / deepening round."""
    _restore_agent_memory_from_pvc(job_dir)

    job_results_dir = job_dir / "results"
    job_results_dir.mkdir(parents=True, exist_ok=True)
    results_dir = get_results_dir()

    def progress_callback(event_type, message="", data=None):
        phase_map = {
            "rca_continue": ("file_selection", 25),
            "data_analysis_done": ("yaml_processing", 50),
            "phase3_logs_start": ("log_processing", 60),
            "log_processing_result": ("log_processing", 70),
            "phase4_aggregation": ("data_aggregation", 75),
            "llm_start_intent": ("rca_analysis", 80),
            "llm_start": ("rca_analysis", 85),
            "llm_done": ("rca_analysis", 95),
        }
        if event_type in phase_map:
            phase, prog = phase_map[event_type]
            _write_status(job_dir, {
                "session_id": session_id,
                "status": "running",
                "phase": phase,
                "progress": prog,
                "message": str(message) or "",
            })
        if event_type == "terminal_block" and isinstance(data, dict):
            for line in data.get("lines", []):
                s = str(line)
                if "Phase 2" in s:
                    _write_status(job_dir, {"session_id": session_id, "status": "running",
                                            "phase": "yaml_processing", "progress": 45})
                elif "Phase 3" in s:
                    _write_status(job_dir, {"session_id": session_id, "status": "running",
                                            "phase": "log_processing", "progress": 60})
                elif "Phase 4" in s:
                    _write_status(job_dir, {"session_id": session_id, "status": "running",
                                            "phase": "data_aggregation", "progress": 75})
                elif "Phase 5" in s:
                    _write_status(job_dir, {"session_id": session_id, "status": "running",
                                            "phase": "rca_analysis", "progress": 80})

    _write_status(job_dir, {
        "session_id": session_id,
        "status": "running",
        "phase": "initializing",
        "progress": 5,
        "message": "Preparing deeper analysis with additional priority files...",
    })

    orch = OrchestratorAgent(reset=False)
    result = orch.continue_rca_with_feedback(
        orch_session_id,
        False,
        must_gather_base_dir=must_gather_base_dir,
        feedback_text=feedback_text,
        progress_callback=progress_callback,
    )

    _copy_results_to_job(results_dir, job_results_dir)
    _copy_agent_memory_to_job(job_dir)

    if isinstance(result, dict) and result.get("status") == "error":
        _write_status(job_dir, {
            "session_id": session_id,
            "status": "error",
            "error": result.get("error", "Deepening failed"),
            "message": result.get("error", "Deepening failed"),
        })
    else:
        rca_summary = result.get("rca_summary", "") if isinstance(result, dict) else ""
        has_more = result.get("has_more_priorities", False) if isinstance(result, dict) else False
        _write_status(job_dir, {
            "session_id": session_id,
            "status": "completed",
            "phase": "completed",
            "progress": 100,
            "message": "Deepened analysis complete",
            "rca_summary": rca_summary,
            "has_more_priorities": has_more,
        })

        if not has_more:
            _cleanup_input(job_dir, session_id)


def _bootstrap_gcp_credentials() -> None:
    try:
        from llm_rca_agent import ensure_gcp_credentials_from_config
        cred_path = ensure_gcp_credentials_from_config()
        if cred_path:
            print(f"GCP credentials ready at {cred_path}", flush=True)
    except Exception as exc:
        print(f"Warning: Could not bootstrap GCP credentials from config: {exc}", flush=True)


def main() -> None:
    global _OWNER
    _bootstrap_gcp_credentials()
    session_id = _read_env("SESSION_ID")
    user_query = _read_env("USER_QUERY")
    job_dir = Path(_read_env("JOB_DIR"))
    must_gather_base_dir = _read_env("MUST_GATHER_BASE_DIR")
    mode = _read_env("MODE", "analyze")
    _OWNER = _read_env("OWNER", "unknown")

    if not session_id or not job_dir.as_posix():
        print("ERROR: SESSION_ID and JOB_DIR are required", file=sys.stderr)
        sys.exit(1)

    job_dir.mkdir(parents=True, exist_ok=True)

    log_file = None
    original_stdout = sys.stdout
    try:
        log_path = job_dir / "results" / "workflow_console.txt"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "a", encoding="utf-8")
        sys.stdout = _TeeWriter(log_file, original_stdout)
    except Exception:
        pass

    try:
        if mode == "deepening":
            orch_session_id = _read_env("ORCHESTRATOR_SESSION_ID")
            feedback_text = _read_env("FEEDBACK_TEXT")
            if not orch_session_id:
                print("ERROR: ORCHESTRATOR_SESSION_ID required for deepening", file=sys.stderr)
                sys.exit(1)
            _run_deepening(session_id, orch_session_id, feedback_text,
                           must_gather_base_dir, job_dir)
        else:
            if not user_query:
                print("ERROR: USER_QUERY is required for analyze mode", file=sys.stderr)
                sys.exit(1)
            _run_analyze(session_id, user_query, must_gather_base_dir, job_dir)
    except Exception as exc:
        _write_status(job_dir, {
            "session_id": session_id,
            "status": "error",
            "message": str(exc),
            "error": str(exc),
        })
        print(f"Worker failed: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        sys.stdout = original_stdout
        if log_file:
            try:
                log_file.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
