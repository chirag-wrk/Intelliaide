"""
Tool Definitions and Executors for the Agentic Orchestrator

Each tool is defined as:
  - A JSON schema dict (for Claude's tool_use API)
  - A wrapper function that calls the existing module and returns {success, data, summary}

Tools are permanent tools -- they will not become agents.
No abstract base class, no inheritance. Just dicts and functions.

Tool inventory:
  - check_llm_availability: Health-check the LLM API before any workflow
  - select_files: Wraps MustGatherFileSelector.suggest_files()
  - check_file_availability: Wraps DataAnalyzer.report_files_availability()
  - analyze_yaml: Wraps DataAnalyzer.analyze_files(file_types=['yaml'])
  - analyze_logs: Wraps DataAnalyzer.analyze_files(file_types=['log'])
  - validate_token_budget: Pre-flight token check + chunk plan (no trimming)
  - perform_rca: Wraps run_rca_chunked() / run_rca_and_summary_continued()
  - generate_causal_dag: Wraps generate_rca_causal_dag(); invoked by orchestrator only
"""

import os
import json
import traceback
import time
from typing import Dict, Any, List, Optional, Tuple


# ---------------------------------------------------------------------------
# 0. MODEL TOKEN LIMITS REGISTRY
#    Model-specific context window and max output tokens.
#    Used by validate_token_budget and check_llm_availability.
#    Keys are model_id prefixes — we match the longest prefix.
# ---------------------------------------------------------------------------

MODEL_TOKEN_LIMITS: Dict[str, Dict[str, int]] = {
    # Claude 3.5 family
    "claude-3-5-sonnet": {"context_window": 200_000, "max_output_tokens": 8_192},
    "claude-3-5-haiku":  {"context_window": 200_000, "max_output_tokens": 8_192},
    # Claude 3 family
    "claude-3-opus":     {"context_window": 200_000, "max_output_tokens": 4_096},
    "claude-3-sonnet":   {"context_window": 200_000, "max_output_tokens": 4_096},
    "claude-3-haiku":    {"context_window": 200_000, "max_output_tokens": 4_096},
    # Claude Sonnet 4 (2025)
    "claude-sonnet-4":   {"context_window": 200_000, "max_output_tokens": 16_000},
    # Claude 4 family (future-proofing)
    "claude-4":          {"context_window": 200_000, "max_output_tokens": 16_000},
    # Fallback (conservative)
    "default":           {"context_window": 200_000, "max_output_tokens": 4_096},
}

# Safety margin: reserve this many tokens beyond max_output_tokens to avoid
# cutting it too close to the context window boundary.
TOKEN_SAFETY_MARGIN = 2_000


def get_model_limits(model_id: str) -> Dict[str, int]:
    """
    Look up context_window and max_output_tokens for a model ID.
    Matches the longest prefix in MODEL_TOKEN_LIMITS; falls back to 'default'.
    """
    if not model_id:
        return MODEL_TOKEN_LIMITS["default"]
    model_lower = model_id.lower()
    best_match = "default"
    best_len = 0
    for prefix in MODEL_TOKEN_LIMITS:
        if prefix == "default":
            continue
        if model_lower.startswith(prefix) and len(prefix) > best_len:
            best_match = prefix
            best_len = len(prefix)
    return MODEL_TOKEN_LIMITS[best_match]


CHARS_PER_TOKEN: Dict[str, float] = {
    "claude":  3.5,
    "llama":   3.8,
    "gpt-4":   3.7,
    "gpt-3":   4.0,
    "mistral": 3.9,
}


def estimate_tokens(text: str, model_id: str = "") -> int:
    """Estimate token count using model-aware chars-per-token ratios."""
    if not text:
        return 0
    model_lower = (model_id or "").lower()
    cpt = next((v for k, v in CHARS_PER_TOKEN.items() if k in model_lower), 3.7)
    return max(1, int(len(text) / cpt))


# ---------------------------------------------------------------------------
# 0b. LLM ERROR CLASSIFICATION HELPER
#     Classifies HTTP errors from LLM API calls into the categories
#     defined in agent_prompts.py ERROR HANDLING section.
# ---------------------------------------------------------------------------

def classify_llm_error(exception: Exception) -> Dict[str, Any]:
    """
    Classify an exception from an LLM API call into a structured error dict.

    Returns:
        {
            "error_category": str,   # API_UNAVAILABLE | RATE_LIMITED | OVERLOADED |
                                     # AUTH_ERROR | CONTEXT_OVERFLOW | TIMEOUT | UNKNOWN_ERROR
            "message": str,          # Human-readable error description
            "retry_allowed": bool,   # Whether a retry is recommended
            "retry_after_seconds": int | None,  # Suggested wait before retry
            "http_status": int | None,
        }
    """
    result = {
        "error_category": "UNKNOWN_ERROR",
        "message": str(exception),
        "retry_allowed": False,
        "retry_after_seconds": None,
        "http_status": None,
    }

    exc_str = str(exception).lower()

    # requests.exceptions.ConnectionError, OSError, socket errors
    if any(kw in exc_str for kw in ("connectionerror", "connection refused", "dns", "name resolution",
                                     "ssl", "certificate", "econnrefused", "network")):
        result["error_category"] = "API_UNAVAILABLE"
        result["message"] = f"LLM API unreachable: {exception}"
        return result

    # requests.exceptions.Timeout
    if "timeout" in exc_str or "timed out" in exc_str:
        result["error_category"] = "TIMEOUT"
        result["message"] = f"LLM API request timed out: {exception}"
        result["retry_allowed"] = True
        result["retry_after_seconds"] = 5
        return result

    # HTTP status-based classification (from requests.exceptions.HTTPError)
    try:
        import requests as _req
        if isinstance(exception, _req.exceptions.HTTPError) and exception.response is not None:
            status = exception.response.status_code
            result["http_status"] = status
            if status == 401 or status == 403:
                result["error_category"] = "AUTH_ERROR"
                result["message"] = f"LLM API authentication failed (HTTP {status}). Check your API key in config.json."
                return result
            if status == 429:
                result["error_category"] = "RATE_LIMITED"
                result["message"] = f"LLM API rate limit exceeded (HTTP 429)."
                result["retry_allowed"] = True
                retry_after = exception.response.headers.get("retry-after")
                result["retry_after_seconds"] = int(retry_after) if retry_after and retry_after.isdigit() else 30
                return result
            if status == 529 or status == 503:
                result["error_category"] = "OVERLOADED"
                result["message"] = f"LLM API is overloaded (HTTP {status})."
                result["retry_allowed"] = True
                result["retry_after_seconds"] = 15
                return result
            if status == 400:
                body = ""
                try:
                    body = exception.response.text.lower()
                except Exception:
                    pass
                if "context" in body or "token" in body or "too long" in body or "too large" in body:
                    result["error_category"] = "CONTEXT_OVERFLOW"
                    result["message"] = f"Payload too large for model context window (HTTP 400): {exception}"
                    result["retry_allowed"] = True  # retry with reduced payload
                    return result
    except ImportError:
        pass

    return result


# ---------------------------------------------------------------------------
# 1. TOOL DEFINITIONS  (sent to Claude tool_use API as the `tools` parameter)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    # ---- check_llm_availability ----
    {
        "name": "check_llm_availability",
        "description": (
            "Health-check the LLM API to verify it is reachable and the API key is valid. "
            "Returns the model ID, context window size, max output tokens, and whether the API "
            "responded successfully. Call this FIRST, before any other tool. If it fails, the "
            "entire workflow must stop — no file selection or RCA is possible without a working LLM."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    # ---- select_files ----
    {
        "name": "select_files",
        "description": (
            "Analyze the user's problem statement against the must-gather structure documentation "
            "and return a prioritized list of files (YAML + .log + JSON) that should be analyzed. "
            "Each file has a priority (high/medium/low) and a reason. Also returns the identified "
            "problem category, keywords, and affected components. "
            "ALSO automatically checks file availability on disk — the result includes which "
            "files were found and which are missing. You do NOT need to call check_file_availability "
            "separately after this. Proceed directly to analyze_yaml / analyze_logs with the "
            "available files."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "problem_statement": {
                    "type": "string",
                    "description": "The user's problem description or query to analyze.",
                },
                "must_gather_docs_dir": {
                    "type": "string",
                    "description": "Directory containing must-gather structure documentation files (MUST_GATHER_*.md).",
                },
            },
            "required": ["problem_statement", "must_gather_docs_dir"],
        },
    },
    # ---- check_file_availability ----
    {
        "name": "check_file_availability",
        "description": (
            "Check which files from a list actually exist in the must-gather directory. "
            "Returns available files (with resolved paths), files not found, and errors. "
            "Call this after select_files to see which suggested files are present on disk."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of file paths to check (e.g. '/<must-gather-root>/<content-folder>/...'). "
                        "These are the paths returned by select_files."
                    ),
                },
            },
            "required": ["file_paths"],
        },
    },
    # ---- analyze_yaml ----
    {
        "name": "analyze_yaml",
        "description": (
            "Analyze YAML files from the must-gather using ML classification. "
            "Extracts critical fields from each YAML object, classifies them via the ML model "
            "(Error, Majority Error, Majority, CONFIG), and returns aggregated error objects "
            # "(classification == Error only) for use in RCA. "
            "Call this with YAML file paths after checking availability."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of YAML file paths to analyze.",
                },
            },
            "required": ["file_paths"],
        },
    },
    # ---- analyze_logs ----
    {
        "name": "analyze_logs",
        "description": (
            "Analyze log/txt files from the must-gather using Drain3 log template mining. "
            "Classifies log lines into Majority Error, Rare Pattern Error, Configuration Changes, Information, Warning levels and Unknown levels "
            "error-level entries and templates. Returns per-file results and saved output paths. "
            "Call this with log file paths after checking availability and flags based on requirement of the iteration of RCA."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of log/txt file paths to analyze.",
                },
            },
            "required": ["file_paths"],
        },
    },
    # ---- analyze_json ----
    {
        "name": "analyze_json",
        "description": (
            "Analyze JSON files from the must-gather (e.g. etcd endpoint_health.json, "
            "Prometheus targets, alertmanager status). Uses dual-path processing: "
            "Path A detects obvious errors via keyword matching (e.g. droppedAlertmanagers, "
            "firing alerts, down endpoints). Path B extracts critical fields and classifies "
            "via ML (Error, Majority Error, Majority, CONFIG) when no simple errors found. "
            "Results merge into the same error pool as analyze_yaml so perform_rca sees them "
            "automatically. Call this with JSON file paths after checking availability."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of JSON file paths to analyze.",
                },
            },
            "required": ["file_paths"],
        },
    },
    # ---- validate_token_budget ----
    {
        "name": "validate_token_budget",
        "description": (
            "Validate that the combined RCA payload fits within the model's context window. "
            "Automatically uses accumulated YAML error objects and log error entries from "
            "prior analyze_yaml / analyze_logs calls — you do NOT need to pass them. "
            "Just pass problem_statement. Returns estimated token count, whether it fits, "
            "and — if it does not — a chunking plan. "
            "Call this BEFORE every perform_rca call. This is mandatory."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "problem_statement": {
                    "type": "string",
                    "description": "The user's problem statement.",
                },
                "previous_rca_summary": {
                    "type": "string",
                    "description": "If this is a continuation RCA, the previous RCA text (adds to token count).",
                },
            },
            "required": ["problem_statement"],
        },
    },
    # ---- perform_rca ----
    {
        "name": "perform_rca",
        "description": (
            "Perform root cause analysis by sending accumulated YAML error objects and "
            "log error entries to the LLM. Automatically uses data from prior "
            "analyze_yaml / analyze_logs calls — you do NOT need to pass yaml_errors "
            "or log_error_entries. Just pass problem_statement. "
            "Returns the RCA summary text, token usage, and cost. "
            "Call this after validate_token_budget confirms the payload fits. "
            "Supports continuation: pass previous_rca_summary to refine an existing RCA. "
            "If the LLM call fails, returns success=false with error_category and retry guidance."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "problem_statement": {
                    "type": "string",
                    "description": "The user's original problem statement for context in the RCA.",
                },
                "previous_rca_summary": {
                    "type": "string",
                    "description": (
                        "If this is a continuation (deepening round), the previous RCA summary text. "
                        "Omit or set to empty string for the first RCA call."
                    ),
                },
                "previous_priority_stage": {
                    "type": "string",
                    "description": (
                        "The priority stage of the previous RCA (e.g. 'high'). "
                        "Only used when previous_rca_summary is provided."
                    ),
                },
                "new_priority_stage": {
                    "type": "string",
                    "description": (
                        "The priority stage of the new data being added (e.g. 'medium'). "
                        "Only used when previous_rca_summary is provided."
                    ),
                },
            },
            "required": ["problem_statement"],
        },
    },
]


# Tools the ReAct agent may call (causal DAG is orchestrator-driven, not agent-selected).
ORCHESTRATOR_TOOL_DEFINITIONS = TOOL_DEFINITIONS

# ---------------------------------------------------------------------------
# 2. TOOL EXECUTOR FUNCTIONS
#    Each function receives (params: dict, context: dict) and returns:
#    { "success": bool, "data": <full result>, "summary": <short string for LLM> }
#
#    context contains:
#      - must_gather_selector: MustGatherFileSelector instance
#      - data_analyzer: DataAnalyzer instance
#      - config_path: str
#      - must_gather_base_dir: str
#      - must_gather_docs_dir: str
#      - progress_callback: optional callable
# ---------------------------------------------------------------------------


def execute_check_llm_availability(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Health-check the LLM API.

    Sends a minimal request to verify:
    - The API endpoint is reachable
    - The API key is valid (not 401/403)
    - The model ID is accepted
    Returns model limits (context_window, max_output_tokens) on success.
    """
    config_path = context.get("config_path", "config.json")

    try:
        from llm_rca_agent import load_config as load_rca_config
    except ImportError:
        return {
            "success": False,
            "data": {"error_category": "UNKNOWN_ERROR"},
            "summary": "ERROR: Cannot import llm_rca_agent to load config.",
        }

    try:
        import requests
    except ImportError:
        return {
            "success": False,
            "data": {"error_category": "UNKNOWN_ERROR"},
            "summary": "ERROR: requests package not available.",
        }

    try:
        from llm_rca_agent import resolve_api_token, resolve_api_endpoint

        config = load_rca_config(config_path)
        claude_config = config.get("claude", {})
        model_id = claude_config.get("model_id", "claude-sonnet-4-6")
        max_tokens_config = claude_config.get("max_tokens", 16384)
        verify_ssl = claude_config.get("verify_ssl", True)
        is_custom_gateway = claude_config.get("custom_gateway", False)

        # ── GCP Vertex AI endpoint & gcloud token auth ──
        try:
            api_token = resolve_api_token(claude_config)
        except RuntimeError as e:
            return {
                "success": False,
                "data": {"error_category": "AUTH_ERROR", "message": str(e)},
                "summary": f"ERROR: {e}",
            }
        api_endpoint = resolve_api_endpoint(claude_config)

        if not api_token:
            return {
                "success": False,
                "data": {
                    "error_category": "AUTH_ERROR",
                    "message": "No API token available. Check auth_type/api_key in config.json or ANTHROPIC_API_KEY env var.",
                },
                "summary": "ERROR: No LLM API token configured. Check auth_type and credentials.",
            }

        # OLD VERSION — Red Hat custom gateway / direct Anthropic endpoint (no longer active):
        # api_key = claude_config.get("api_key") or os.getenv("ANTHROPIC_API_KEY")
        # api_url = (claude_config.get("api_url", "https://api.anthropic.com")).rstrip("/")
        # is_custom_gateway = "api.anthropic.com" not in api_url
        # if is_custom_gateway:
        #     api_endpoint = f"{api_url}/sonnet/models/{model_id}:streamRawPredict"
        # else:
        #     api_endpoint = f"{api_url}/v1/messages"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_token}",
        }

        if is_custom_gateway:
            payload = {
                "anthropic_version": "vertex-2023-10-16",
                "messages": [{"role": "user", "content": [{"type": "text", "text": "ping"}]}],
                "max_tokens": 5,
                "temperature": 0,
            }
        else:
            payload = {
                "model": model_id,
                "max_tokens": 5,
                "messages": [{"role": "user", "content": "ping"}],
            }

        response = requests.post(
            api_endpoint,
            headers=headers,
            json=payload,
            verify=verify_ssl,
            timeout=30,
        )
        response.raise_for_status()

        # If we get here, the API is reachable and the key is valid
        model_limits = get_model_limits(model_id)
        # Allow config to override defaults
        effective_max_output = min(
            max_tokens_config,
            model_limits["max_output_tokens"],
        )

        # Check for rate-limit headers
        rate_limit_info = {}
        for header in ("x-ratelimit-limit-requests", "x-ratelimit-remaining-requests",
                       "x-ratelimit-limit-tokens", "x-ratelimit-remaining-tokens",
                       "retry-after"):
            val = response.headers.get(header)
            if val:
                rate_limit_info[header] = val

        data = {
            "model_id": model_id,
            "api_url": api_endpoint,
            "context_window": model_limits["context_window"],
            "max_output_tokens": effective_max_output,
            "is_custom_gateway": is_custom_gateway,
            "rate_limit_info": rate_limit_info,
        }

        summary = (
            f"LLM API is available. Model: {model_id}. "
            f"Context window: {model_limits['context_window']:,} tokens. "
            f"Max output: {effective_max_output:,} tokens."
        )
        if rate_limit_info:
            summary += f" Rate-limit headers: {rate_limit_info}"

        return {"success": True, "data": data, "summary": summary}

    except Exception as e:
        error_info = classify_llm_error(e)
        return {
            "success": False,
            "data": error_info,
            "summary": (
                f"ERROR: LLM API health check failed. "
                f"Category: {error_info['error_category']}. "
                f"Detail: {error_info['message']}"
            ),
        }


# ── ORIGINAL execute_select_files (commented out for testing) ─────────────
def execute_select_files(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Wrapper for MustGatherFileSelector.suggest_files().

    Returns files with priorities, reasoning, and problem_category.
    """
    selector = context.get("must_gather_selector")
    if not selector:
        return {
            "success": False,
            "data": {},
            "summary": "ERROR: Must-gather file selector not initialized.",
        }

    problem_statement = params.get("problem_statement", "")
    must_gather_docs_dir = params.get("must_gather_docs_dir") or context.get("must_gather_docs_dir", "")

    try:
        result = selector.suggest_files(problem_statement, must_gather_docs_dir)

        if result.get("error"):
            return {
                "success": False,
                "data": result,
                "summary": f"File selection error: {result['error']}",
            }

        suggested_files = result.get("suggested_files", [])
        problem_category = result.get("problem_category", "Unknown")

        # ── Auto-check file availability (saves a separate iteration) ──
        analyzer = context.get("data_analyzer")
        must_gather_base_dir = context.get("must_gather_base_dir", "")
        file_availability = None
        if analyzer and must_gather_base_dir and suggested_files:
            try:
                if must_gather_base_dir:
                    analyzer.must_gather_base_dir = must_gather_base_dir
                all_paths = [f.get("path", "") for f in suggested_files if f.get("path")]
                file_availability = analyzer.report_files_availability(all_paths)
                result["file_availability"] = file_availability
            except Exception as e:  
                # Non-fatal: availability check failed, Claude can call check_file_availability later
                result["file_availability_error"] = str(e)

        # Build priority counts
        priority_counts = {"high": 0, "medium": 0, "low": 0}
        for f in suggested_files:
            pri = f.get("priority", "medium").lower()
            if pri in priority_counts:
                priority_counts[pri] += 1

        # Build a concise file list for the summary
        file_list_lines = []
        for f in suggested_files:
            path = f.get("path", "")
            pri = f.get("priority", "medium")
            reason = f.get("reason", "")
            file_list_lines.append(f"  [{pri.upper()}] {path}" + (f" -- {reason}" if reason else ""))

        file_list_str = "\n".join(file_list_lines) if file_list_lines else "  (none)"

        summary = (
            f"Found {len(suggested_files)} files. "
            f"Category: {problem_category}. "
            f"Priority breakdown: {priority_counts['high']} high, "
            f"{priority_counts['medium']} medium, {priority_counts['low']} low.\n"
            f"Files:\n{file_list_str}"
        )

        # Add availability info to summary
        if file_availability:
            found_count = len(file_availability.get("found_in_supplied_dir", []))
            not_found_count = len(file_availability.get("not_found", []))
            summary += (
                f"\n\nFile availability: {found_count} found, {not_found_count} not found."
            )
            found_paths = [
                item.get("resolved", "") for item in file_availability.get("found_in_supplied_dir", [])
            ]
            not_found_paths = file_availability.get("not_found", [])
            if found_paths:
                summary += "\nAvailable:\n" + "\n".join(f"  FOUND: {p}" for p in found_paths)
            if not_found_paths:
                summary += "\nMissing:\n" + "\n".join(f"  NOT FOUND: {p}" for p in not_found_paths)

        # ── Save file selection result to Results/hardcoded_files.json ──
        try:
            from app_paths import get_results_dir
            results_dir = get_results_dir()
            out_path = results_dir / "hardcoded_files.json"
            import json as _json
            with open(out_path, "w", encoding="utf-8") as f:
                _json.dump(suggested_files, f, indent=2, ensure_ascii=False)
            print(f"[select_files] File selection saved to: {out_path}")
        except Exception as save_err:
            print(f"[select_files] Warning: Could not save file selection: {save_err}")

        return {
            "success": True,
            "data": result,
            "summary": summary,
        }

    except Exception as e:
        error_info = classify_llm_error(e)
        return {
            "success": False,
            "data": {
                "error": str(e),
                "error_category": error_info["error_category"],
                "retry_allowed": error_info["retry_allowed"],
                "retry_after_seconds": error_info["retry_after_seconds"],
                "traceback": traceback.format_exc(),
            },
            "summary": (
                f"ERROR calling select_files. Category: {error_info['error_category']}. "
                f"Detail: {error_info['message']}. "
                f"Retry allowed: {error_info['retry_allowed']}."
            ),
       }
#── END ORIGINAL execute_select_files ────────────────────────────────────


# def execute_select_files(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
#     """
#     DUMMY replacement for testing — loads a hardcoded file list from
#     Results/hardcoded_files.json instead of calling MustGatherFileSelector.suggest_files().

#     The return format matches what the original function (and
#     must_gather_file_selector.py suggest_files()) would produce so the rest
#     of the orchestrator pipeline works unchanged.
#     """

#     # Load hardcoded file list from Results/hardcoded_files.json
#     script_dir = os.path.dirname(os.path.abspath(__file__))
#     project_root = os.path.dirname(script_dir)
#     hardcoded_path = os.path.join(project_root, "Results", "hardcoded_files.json")

#     try:
#         with open(hardcoded_path, "r", encoding="utf-8") as f:
#             HARDCODED_FILES: list = json.load(f)
#         print(f"[select_files] Loaded {len(HARDCODED_FILES)} hardcoded files from: {hardcoded_path}")
#     except FileNotFoundError:
#         return {
#             "success": False,
#             "data": {},
#             "summary": f"ERROR: Hardcoded files not found at {hardcoded_path}",
#         }
#     except (json.JSONDecodeError, Exception) as e:
#         return {
#             "success": False,
#             "data": {},
#             "summary": f"ERROR: Failed to parse {hardcoded_path}: {e}",
#         }

#     problem_statement = params.get("problem_statement", "")

#     # ── Build the "selector result" dict (same schema as suggest_files) ──
#     suggested_files = HARDCODED_FILES
#     problem_category = "Testing-Hardcoded"

#     priority_dict = {f["path"]: f.get("priority", "medium") for f in suggested_files}

#     selector_result = {
#         "suggested_files": suggested_files,
#         "reasoning": "DUMMY selector — returning hardcoded file list for testing.",
#         "problem_category": problem_category,
#         "priority": priority_dict,
#         "input_tokens": 0,
#         "output_tokens": 0,
#     }

#     # ── Build priority counts ────────────────────────────────────────────
#     priority_counts = {"high": 0, "medium": 0, "low": 0}
#     for f in suggested_files:
#         pri = f.get("priority", "medium").lower()
#         if pri in priority_counts:
#             priority_counts[pri] += 1

#     # ── Build summary (same format the orchestrator expects) ─────────────
#     file_list_lines = []
#     for f in suggested_files:
#         path = f.get("path", "")
#         pri = f.get("priority", "medium")
#         reason = f.get("reason", "")
#         file_list_lines.append(f"  [{pri.upper()}] {path}" + (f" -- {reason}" if reason else ""))

#     file_list_str = "\n".join(file_list_lines) if file_list_lines else "  (none)"

#     summary = (
#         f"[DUMMY SELECTOR] Found {len(suggested_files)} files. "
#         f"Category: {problem_category}. "
#         f"Priority breakdown: {priority_counts['high']} high, "
#         f"{priority_counts['medium']} medium, {priority_counts['low']} low.\n"
#         f"Files:\n{file_list_str}"
#     )

#     return {
#         "success": True,
#         "data": selector_result,
#         "summary": summary,
#     }


def execute_check_file_availability(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Wrapper for DataAnalyzer.report_files_availability().

    Checks which suggested files actually exist on disk.
    """
    analyzer = context.get("data_analyzer")
    if not analyzer:
        return {
            "success": False,
            "data": {},
            "summary": "ERROR: Data analyzer not initialized.",
        }

    file_paths = params.get("file_paths", [])
    if not file_paths:
        return {
            "success": False,
            "data": {},
            "summary": "ERROR: No file_paths provided.",
        }

    # Ensure the analyzer has the correct base dir
    must_gather_base_dir = context.get("must_gather_base_dir", "")
    if must_gather_base_dir:
        analyzer.must_gather_base_dir = must_gather_base_dir

    try:
        result = analyzer.report_files_availability(file_paths)

        found_count = len(result.get("found_in_supplied_dir", []))
        not_found_count = len(result.get("not_found", []))
        total = result.get("total_shortlisted", len(file_paths))

        found_paths = [
            item.get("resolved", item.get("original", ""))
            for item in result.get("found_in_supplied_dir", [])
        ]
        not_found_paths = result.get("not_found", [])

        found_str = "\n".join(f"  FOUND: {p}" for p in found_paths) if found_paths else "  (none found)"
        not_found_str = "\n".join(f"  NOT FOUND: {p}" for p in not_found_paths) if not_found_paths else ""

        summary = (
            f"{found_count} of {total} files found; "
            f"{not_found_count} not found.\n"
            f"{found_str}"
        )
        if not_found_str:
            summary += f"\n{not_found_str}"

        return {
            "success": True,
            "data": result,
            "summary": summary,
        }

    except Exception as e:
        return {
            "success": False,
            "data": {"error": str(e), "traceback": traceback.format_exc()},
            "summary": f"ERROR checking file availability: {e}",
        }


def _get_all_unanalyzed_files(context: Dict[str, Any], file_type: str) -> List[str]:
    """Return all unanalyzed high-priority resolved paths of *file_type*.

    file_type must be one of: 'yaml', 'json', 'log'.

    Populated by orchestrator_agent._store_tool_result after select_files
    completes.  Falls back to an empty list if the mapping is not yet
    available (caller should then use whatever Claude passed in params).
    """
    agent_state = context.get("agent_state", {})
    high_files: List[str] = agent_state.get("files_available_by_priority", {}).get("high", [])
    if not high_files:
        return []

    already_analyzed: set = set(agent_state.get("files_analyzed", []))
    unanalyzed = [f for f in high_files if f not in already_analyzed]

    _yaml_json = (".yaml", ".yml", ".json")
    if file_type == "yaml":
        return [f for f in unanalyzed if f.lower().endswith((".yaml", ".yml"))]
    if file_type == "json":
        return [f for f in unanalyzed if f.lower().endswith(".json")]
    # log — everything that is not a structured config file
    return [f for f in unanalyzed if not f.lower().endswith(_yaml_json)]


def execute_analyze_yaml(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Wrapper for DataAnalyzer.analyze_files(file_types=['yaml']).

    Processes YAML files with ML classification; returns aggregated error objects.
    """
    analyzer = context.get("data_analyzer")
    if not analyzer:
        return {
            "success": False,
            "data": {},
            "summary": "ERROR: Data analyzer not initialized.",
        }

    # Batch mode: always process every unanalyzed high-priority YAML file so
    # all YAML analysis completes in a single ReAct iteration regardless of
    # how many files Claude chose to pass.
    batch_files = _get_all_unanalyzed_files(context, "yaml")
    file_paths = batch_files if batch_files else params.get("file_paths", [])
    if not file_paths:
        return {
            "success": False,
            "data": {},
            "summary": "ERROR: No YAML files to analyze (all already processed or none available).",
        }

    must_gather_base_dir = context.get("must_gather_base_dir", "")
    if must_gather_base_dir:
        analyzer.must_gather_base_dir = must_gather_base_dir

    progress_callback = context.get("progress_callback")

    try:
        result = analyzer.analyze_files(
            file_paths,
            file_types=["yaml"],
            progress_callback=progress_callback,
        )

        # ml_classification_result from data_analyzer is:
        #   {"output_flags": {...}, "files": {"filename": {"objects": [...], "summary": {...}}, ...}}
        # The actual per-file classified objects are under the "files" key.
        raw_ml_result = result.get("ml_classification_result", {})
        ml_files = raw_ml_result.get("files", {}) if isinstance(raw_ml_result, dict) else {}

        # Count actual error objects per file
        n_files_with_errors = len(ml_files)
        total_error_objects = 0
        for file_data in ml_files.values():
            if isinstance(file_data, dict):
                total_error_objects += len(file_data.get("objects", []))
            elif isinstance(file_data, list):
                total_error_objects += len(file_data)

        files_processed = result.get("summary", {}).get("files_processed", 0)
        total_objects = result.get("summary", {}).get("critical_fields_extracted", 0)
        failed = result.get("failed_files", [])

        # Build per-file error summary
        per_file_lines = []
        for fname, file_data in ml_files.items():
            if isinstance(file_data, dict):
                n_objs = len(file_data.get("objects", []))
            elif isinstance(file_data, list):
                n_objs = len(file_data)
            else:
                n_objs = 0
            per_file_lines.append(f"  {fname}: {n_objs} error object(s)")

        per_file_str = "\n".join(per_file_lines) if per_file_lines else "  (no error objects found)"

        summary = (
            f"YAML analysis complete. {files_processed} file(s) processed, "
            f"{total_objects} total objects extracted, "
            f"{total_error_objects} Error-classified objects across {n_files_with_errors} file(s)."
        )
        if failed:
            summary += f"\n  Failed/not found: {', '.join(failed)}"
        summary += f"\nError objects per file:\n{per_file_str}"

        return {
            "success": True,
            "data": {
                "ml_classification_result": raw_ml_result,
                "classification_table": result.get("classification_table"),
                "total_yaml_bytes": result.get("total_yaml_bytes", 0),
                "yaml_file_sizes": result.get("yaml_file_sizes", {}),
                "files_processed": files_processed,
                "total_objects": total_objects,
                "failed_files": failed,
                "analyzed_files": result.get("analyzed_files", []),
                "files_with_no_output": result.get("files_with_no_output", []),
            },
            "summary": summary,
        }

    except Exception as e:
        return {
            "success": False,
            "data": {"error": str(e), "traceback": traceback.format_exc()},
            "summary": f"ERROR in YAML analysis: {e}",
        }


def execute_analyze_logs(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Wrapper for DataAnalyzer.analyze_files(file_types=['log']).

    Processes log files with Drain3 template mining; returns per-file results
    and Error-level entries for RCA.
    """
    analyzer = context.get("data_analyzer")
    if not analyzer:
        return {
            "success": False,
            "data": {},
            "summary": "ERROR: Data analyzer not initialized.",
        }

    # Batch mode: always process every unanalyzed high-priority log file so
    # all log analysis completes in a single ReAct iteration.
    batch_files = _get_all_unanalyzed_files(context, "log")
    file_paths = batch_files if batch_files else params.get("file_paths", [])
    if not file_paths:
        return {
            "success": False,
            "data": {},
            "summary": "ERROR: No log files to analyze (all already processed or none available).",
        }

    must_gather_base_dir = context.get("must_gather_base_dir", "")
    if must_gather_base_dir:
        analyzer.must_gather_base_dir = must_gather_base_dir

    progress_callback = context.get("progress_callback")

    try:
        result = analyzer.analyze_files(
            file_paths,
            file_types=["log"],
            progress_callback=progress_callback,
        )

        log_result = result.get("log_processing_result", {})
        logs_count = log_result.get("logs_count", 0)
        per_file = log_result.get("per_file", [])

        _error_levels = {"RareError", "HighFreqError", "Error"}
        log_error_entries = []
        total_log_bytes = 0
        for pf in per_file:
            source_path = pf.get("file", "")
            try:
                original_size = os.path.getsize(source_path) if source_path and os.path.exists(source_path) else 0
            except Exception:
                original_size = 0
            total_log_bytes += original_size
            for level_name, path in (pf.get("saved") or {}).items():
                if level_name not in _error_levels or not path:
                    continue
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    log_error_entries.append({
                        "file": os.path.basename(path),
                        "content": content,
                        "original_size": original_size,
                    })
                except Exception:
                    pass

        # Build summary stats
        summary_stats = log_result.get("summary", {})
        summary_lines = []
        for name, s in summary_stats.items():
            summary_lines.append(
                f"  {name}: {s.get('count', 0)} entries, {s.get('templates_count', 0)} templates"
            )
        stats_str = "\n".join(summary_lines) if summary_lines else "  (no log stats)"

        summary = (
            f"Log analysis complete. {logs_count} log file(s) processed. "
            f"{len(log_error_entries)} Error-level output file(s) collected."
        )
        if log_result.get("error"):
            summary += f"\n  Note: {log_result['error']}"
        summary += f"\nSummary:\n{stats_str}"

        failed = result.get("failed_files", [])
        if failed:
            summary += f"\n  Failed/not found: {', '.join(failed)}"

        return {
            "success": True,
            "data": {
                "log_processing_result": log_result,
                "log_error_entries": log_error_entries,
                "total_log_bytes": total_log_bytes,
                "logs_count": logs_count,
                "failed_files": failed,
                "analyzed_files": result.get("analyzed_files", []),
            },
            "summary": summary,
        }

    except Exception as e:
        return {
            "success": False,
            "data": {"error": str(e), "traceback": traceback.format_exc()},
            "summary": f"ERROR in log analysis: {e}",
        }


def execute_analyze_json(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Wrapper for DataAnalyzer.analyze_files(file_types=['json']).

    Processes JSON files through dual-path analysis:
      Path A: Keyword-based error detection (e.g. droppedAlertmanagers, firing alerts)
      Path B: Critical field extraction + ML classification (when no simple errors found)

    Results merge into the same accumulated_yaml_errors pool so perform_rca
    and validate_token_budget see them automatically.
    """
    analyzer = context.get("data_analyzer")
    if not analyzer:
        return {
            "success": False,
            "data": {},
            "summary": "ERROR: Data analyzer not initialized.",
        }

    # Batch mode: always process every unanalyzed high-priority JSON file so
    # all JSON analysis completes in a single ReAct iteration.
    batch_files = _get_all_unanalyzed_files(context, "json")
    file_paths = batch_files if batch_files else params.get("file_paths", [])
    if not file_paths:
        return {
            "success": False,
            "data": {},
            "summary": "ERROR: No JSON files to analyze (all already processed or none available).",
        }

    must_gather_base_dir = context.get("must_gather_base_dir", "")
    if must_gather_base_dir:
        analyzer.must_gather_base_dir = must_gather_base_dir

    progress_callback = context.get("progress_callback")

    try:
        result = analyzer.analyze_files(
            file_paths,
            file_types=["json"],
            progress_callback=progress_callback,
        )

        raw_ml_result = result.get("ml_classification_result", {})
        ml_files = raw_ml_result.get("files", {}) if isinstance(raw_ml_result, dict) else {}

        # Count error objects per file (handle both simple_error_detection and ML-classified)
        n_files_with_errors = 0
        total_error_objects = 0
        for fname, file_data in ml_files.items():
            if isinstance(file_data, dict):
                if file_data.get("simple_error_detection"):
                    # Path A: simple keyword detection (e.g. droppedAlertmanagers)
                    n_files_with_errors += 1
                    total_error_objects += sum(
                        e.get("count", 0) for e in file_data.get("error_summary", [])
                    )
                else:
                    # Path B: ML-classified objects
                    objs = file_data.get("objects", [])
                    if objs:
                        n_files_with_errors += 1
                        total_error_objects += len(objs)
            elif isinstance(file_data, list):
                if file_data:
                    n_files_with_errors += 1
                    total_error_objects += len(file_data)

        json_files_processed = result.get("summary", {}).get("json_files_processed", 0)
        total_objects = result.get("summary", {}).get("critical_fields_extracted", 0)
        failed = result.get("failed_files", [])

        # Build per-file summary
        per_file_lines = []
        for fname, file_data in ml_files.items():
            if isinstance(file_data, dict):
                if file_data.get("simple_error_detection"):
                    err_count = sum(e.get("count", 0) for e in file_data.get("error_summary", []))
                    reason = file_data.get("reason", "")
                    per_file_lines.append(f"  {fname}: {err_count} error(s) [simple detection] — {reason}")
                else:
                    n_objs = len(file_data.get("objects", []))
                    per_file_lines.append(f"  {fname}: {n_objs} error object(s) [ML classified]")
            elif isinstance(file_data, list):
                per_file_lines.append(f"  {fname}: {len(file_data)} error object(s)")

        per_file_str = "\n".join(per_file_lines) if per_file_lines else "  (no error objects found)"

        summary = (
            f"JSON analysis complete. {json_files_processed} file(s) processed, "
            f"{total_error_objects} error(s) across {n_files_with_errors} file(s)."
        )
        if failed:
            summary += f"\n  Failed/not found: {', '.join(failed)}"
        summary += f"\nError objects per file:\n{per_file_str}"

        return {
            "success": True,
            "data": {
                "ml_classification_result": raw_ml_result,
                "classification_table": result.get("classification_table"),
                "json_files_processed": json_files_processed,
                "total_objects": total_objects,
                "failed_files": failed,
                "analyzed_files": result.get("analyzed_files", []),
                "files_with_no_output": result.get("files_with_no_output", []),
            },
            "summary": summary,
        }

    except Exception as e:
        return {
            "success": False,
            "data": {"error": str(e), "traceback": traceback.format_exc()},
            "summary": f"ERROR in JSON analysis: {e}",
        }


def execute_validate_token_budget(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pre-flight check: builds the EXACT same RCA prompt that perform_rca would
    build (including chronology, instructions, JSON formatting) and estimates
    tokens on the finished string. If the payload exceeds the budget, computes
    a chunk plan (how many API calls are needed) and reports it so the agent
    can proceed immediately with perform_rca, which handles chunking internally.
    """
    config_path = context.get("config_path", "config.json")
    agent_state = context.get("agent_state", {})

    yaml_errors = params.get("yaml_errors") or agent_state.get("accumulated_yaml_errors", {})
    log_error_entries = params.get("log_error_entries") or agent_state.get("accumulated_log_entries", [])
    problem_statement = params.get("problem_statement") or agent_state.get("problem_statement", "")
    previous_rca_summary = params.get("previous_rca_summary", "")

    # Load model limits
    try:
        from llm_rca_agent import load_config as load_rca_config
        config = load_rca_config(config_path)
        claude_config = config.get("claude", {})
        model_id = claude_config.get("model_id", "claude-sonnet-4-6")
        max_tokens_config = claude_config.get("max_tokens", 16384)
    except Exception:
        model_id = "claude-sonnet-4-6"
        max_tokens_config = 16384

    model_limits = get_model_limits(model_id)
    context_window = model_limits["context_window"]
    max_output = min(max_tokens_config, model_limits["max_output_tokens"])
    available_for_input = context_window - max_output - TOKEN_SAFETY_MARGIN

    # ------------------------------------------------------------------
    # Build the EXACT same prompt that perform_rca will send to the LLM.
    # This eliminates the estimation gap that caused overflow.
    # ------------------------------------------------------------------
    try:
        from llm_rca_agent import (
            prepare_payload_for_llm,
            build_chronology_from_payload,
            build_rca_prompt,
            chunk_payload,
        )
    except ImportError:
        return {
            "success": False,
            "data": {},
            "summary": "ERROR: Cannot import llm_rca_agent for accurate token estimation.",
        }

    def _build_full_prompt(yaml_errs, log_entries, prev_rca=""):
        """Reconstruct the exact prompt that perform_rca would build."""
        payload_yaml = prepare_payload_for_llm(yaml_errs)
        payload_for_llm = {"yaml_errors": payload_yaml}
        if log_entries:
            payload_for_llm["log_errors"] = log_entries
        chronology = build_chronology_from_payload(payload_yaml, log_entries)
        if chronology:
            payload_for_llm["chronology"] = chronology
        prompt = build_rca_prompt(payload_for_llm, problem_statement)
        if prev_rca:
            prompt += f"\n\nPREVIOUS RCA SUMMARY:\n{prev_rca}"
        return prompt

    full_prompt = _build_full_prompt(yaml_errors, log_error_entries, previous_rca_summary)
    raw_tokens = estimate_tokens(full_prompt, model_id)
    # 10% safety margin on top of the accurate estimate
    estimated_total = int(raw_tokens * 1.25)

    # ------------------------------------------------------------------
    # Per-file token breakdown (for reporting and trimming decisions)
    # ------------------------------------------------------------------
    file_breakdown = []
    for fname, objs in yaml_errors.items():
        single_payload = prepare_payload_for_llm({fname: objs})
        s = json.dumps(single_payload, indent=2, default=str)
        obj_count = len(objs) if isinstance(objs, list) else len(objs.get("objects", []) if isinstance(objs, dict) else [])
        file_breakdown.append({
            "file": fname, "type": "yaml", "tokens": estimate_tokens(s, model_id), "objects": obj_count,
        })
    for entry in log_error_entries:
        s = json.dumps({"file": entry.get("file", ""), "content": entry.get("content", "")}, indent=2, default=str)
        file_breakdown.append({
            "file": entry.get("file", "unknown_log"), "type": "log", "tokens": estimate_tokens(s, model_id),
        })
    file_breakdown.sort(key=lambda x: x["tokens"], reverse=True)

    breakdown_lines = []
    for fb in file_breakdown:
        obj_info = f", {fb['objects']} objects" if "objects" in fb else ""
        breakdown_lines.append(f"  {fb['file']} ({fb['type']}{obj_info}): ~{fb['tokens']:,} tokens")
    breakdown_str = "\n".join(breakdown_lines) if breakdown_lines else "  (empty)"

    fits = estimated_total <= available_for_input
    headroom = available_for_input - estimated_total

    data = {
        "model_id": model_id,
        "context_window": context_window,
        "max_output_tokens": max_output,
        "safety_margin": TOKEN_SAFETY_MARGIN,
        "available_for_input": available_for_input,
        "estimated_tokens": {
            "raw_prompt_tokens": raw_tokens,
            "with_safety_margin": estimated_total,
        },
        "fits": fits,
        "headroom_tokens": headroom,
        "file_breakdown": file_breakdown,
    }

    if fits:
        summary = (
            f"Token budget OK. Estimated {estimated_total:,} tokens "
            f"(raw: {raw_tokens:,} + 10% safety). "
            f"Available: {available_for_input:,}. Headroom: {headroom:,}. "
            f"Model: {model_id}.\n"
            f"Per-file breakdown:\n{breakdown_str}"
        )
        return {"success": True, "data": data, "summary": summary}

    # ------------------------------------------------------------------
    # PAYLOAD EXCEEDS BUDGET — compute a chunk plan (no data is removed)
    # perform_rca will split the payload into N LLM calls automatically.
    # ------------------------------------------------------------------
# ------------------------------------------------------------------
    # PAYLOAD EXCEEDS BUDGET — compute a chunk plan (no data is removed)
    # perform_rca will split the payload into N LLM calls automatically.
    # ------------------------------------------------------------------
    payload_yaml_clean = prepare_payload_for_llm(yaml_errors)
    
    # NEW: Fetch max_chunk_tokens from config, default to 60000
    try:
        from llm_rca_agent import load_config as load_rca_config
        rca_config = load_rca_config(config_path)
        max_chunk_tokens = rca_config.get("claude", {}).get("max_chunk_tokens", 80000)
    except Exception:
        max_chunk_tokens = 80000

    plan = chunk_payload(
        yaml_errors=payload_yaml_clean, 
        log_entries=log_error_entries or [], 
        max_chunk_tokens=max_chunk_tokens
    )
    n_chunks = len(plan)

    chunk_descriptions = []
    for idx, chunk in enumerate(plan):
        yaml_count = len(chunk.get("yaml_errors", {}))
        log_files = [e.get("file", "?") for e in chunk.get("log_entries", [])]
        parts = []
        if yaml_count:
            parts.append(f"{yaml_count} YAML file(s)")
        if log_files:
            parts.append(f"{len(log_files)} log(s): {', '.join(log_files[:5])}")
            if len(log_files) > 5:
                parts[-1] += f" (+{len(log_files) - 5} more)"
        chunk_descriptions.append(
            f"  Chunk {idx+1}: " + (", ".join(parts) if parts else "(empty)")
        )
    chunk_desc_str = "\n".join(chunk_descriptions)

    agent_state["_chunk_count"] = n_chunks

    summary = (
        f"Token budget EXCEEDED. Estimated {estimated_total:,} tokens "
        f"(available: {available_for_input:,}, "
        f"excess: {estimated_total - available_for_input:,}).\n"
        f"Payload will be processed in {n_chunks} chunk(s) "
        f"({n_chunks} internal API call(s), NOT orchestrator iterations):\n"
        f"{chunk_desc_str}\n"
        f"Per-file breakdown:\n{breakdown_str}\n"
        f"Proceed with perform_rca — chunking is handled automatically."
    )

    data["fits"] = True
    data["chunk_count"] = n_chunks
    data["chunk_plan_summary"] = chunk_desc_str

    return {"success": True, "data": data, "summary": summary}


def execute_perform_rca(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Wrapper for run_rca_chunked() / run_rca_and_summary_continued().

    Both paths use chunk-and-summarize to handle any payload size.
    Sends aggregated YAML errors + log error entries to LLM for root cause analysis.
    Supports both initial and continuation (deepening) RCA calls.

    Auto-reads accumulated data from agent_state when yaml_errors / log_error_entries
    are not provided by Claude (avoids the data-handoff problem where Claude can only
    see summaries but not the actual classified objects).
    """
    config_path = context.get("config_path", "config.json")
    agent_state = context.get("agent_state", {})

    # Auto-read from agent_state when params are empty/missing
    problem_statement = params.get("problem_statement") or agent_state.get("problem_statement", "")
    all_yaml_errors = params.get("yaml_errors") or agent_state.get("accumulated_yaml_errors", {})
    all_log_entries = params.get("log_error_entries") or agent_state.get("accumulated_log_entries", [])
    previous_rca_summary = params.get("previous_rca_summary", "")
    previous_priority_stage = params.get("previous_priority_stage", "high")
    new_priority_stage = params.get("new_priority_stage", "medium")

    # Note: yaml_errors may be in either format (flat list or wrapped with "objects"/"summary").
    # llm_rca_agent.prepare_payload_for_llm() handles both formats defensively.

    # Determine whether this is a first-time or continuation RCA
    is_continuation = bool(previous_rca_summary and previous_rca_summary.strip())

    if is_continuation:
        # Only send NEW data (delta) — files not already analyzed in the previous RCA round
        prev_yaml_keys = set(agent_state.get("_last_rca_yaml_keys", []))
        prev_log_files = set(agent_state.get("_last_rca_log_files", []))
        yaml_errors = {k: v for k, v in all_yaml_errors.items() if k not in prev_yaml_keys}
        log_error_entries = [e for e in all_log_entries if e.get("file", "") not in prev_log_files]
    else:
        yaml_errors = all_yaml_errors
        log_error_entries = all_log_entries

    # Track which data keys this RCA round covers (for delta computation next round)
    agent_state["_last_rca_yaml_keys"] = list(all_yaml_errors.keys())
    agent_state["_last_rca_log_files"] = [e.get("file", "") for e in all_log_entries]

    try:
        if is_continuation:
            from llm_rca_agent import run_rca_and_summary_continued
            rca_result = run_rca_and_summary_continued(
                yaml_errors,
                config_path=config_path,
                problem_statement=problem_statement,
                log_error_entries=log_error_entries if log_error_entries else None,
                previous_rca_summary=previous_rca_summary,
                previous_priority_stage=previous_priority_stage,
                new_priority_stage=new_priority_stage,
            )
        else:
            from llm_rca_agent import run_rca_chunked
            rca_result = run_rca_chunked(
                yaml_errors,
                config_path=config_path,
                problem_statement=problem_statement,
                log_error_entries=log_error_entries if log_error_entries else None,
            )

        rca_text = rca_result.get("rca_summary", "")
        input_tokens = rca_result.get("input_tokens", 0)
        output_tokens = rca_result.get("output_tokens", 0)
        cost_usd = rca_result.get("cost_usd", 0.0)
        payload_bytes = rca_result.get("payload_bytes", 0)
        error = rca_result.get("error")
        chunks_processed = rca_result.get("chunks_processed", 1)

        rca_preview = rca_text[:2000] + "..." if len(rca_text) > 2000 else rca_text

        mode_label = "continuation" if is_continuation else "initial"
        if chunks_processed > 1:
            mode_label += f" (chunked: {chunks_processed} API calls)"
        summary = (
            f"RCA {mode_label} complete. "
            f"Tokens: {input_tokens} in / {output_tokens} out. "
            f"Cost: ${cost_usd:.4f}. Payload: {payload_bytes} bytes."
        )
        if error:
            summary += f"\n  WARNING: {error}"
        summary += f"\n\nRCA Summary:\n{rca_preview}"

        return {
            "success": not bool(error),
            "data": {
                "rca_summary": rca_text,
                "chronology": rca_result.get("chronology", []),
                "payload_bytes": payload_bytes,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost_usd,
                "chunks_processed": chunks_processed,
                "error": error,
            },
            "summary": summary,
        }

    except Exception as e:
        error_info = classify_llm_error(e)
        return {
            "success": False,
            "data": {
                "error": str(e),
                "error_category": error_info["error_category"],
                "retry_allowed": error_info["retry_allowed"],
                "retry_after_seconds": error_info["retry_after_seconds"],
                "traceback": traceback.format_exc(),
            },
            "summary": (
                f"ERROR performing RCA. Category: {error_info['error_category']}. "
                f"Detail: {error_info['message']}. "
                f"Retry allowed: {error_info['retry_allowed']}."
            ),
        }

_STAGE_FILE_KEY_FOR_DAG: Dict[str, str] = {
    "high": "Tier1",
    "medium": "Tier2",
    "low": "Final",
    "reselect": "Tier2",
}


def execute_generate_causal_dag(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Call generate_rca_causal_dag and persist RCA_{stage}_dag.json."""
    config_path = context.get("config_path", "config.json")
    agent_state = context.get("agent_state", {})

    rca_text = (params.get("rca_text") or agent_state.get("rca_text") or "").strip()
    problem_statement = (
        params.get("problem_statement")
        or agent_state.get("problem_statement", "")
    )

    if not rca_text:
        return {
            "success": False,
            "data": {},
            "summary": (
                "ERROR: No RCA text available. Call perform_rca first, then call "
                "generate_causal_dag in a separate iteration."
            ),
        }

    try:
        from llm_rca_agent import generate_rca_causal_dag
        from app_paths import get_results_dir
    except ImportError as e:
        return {
            "success": False,
            "data": {},
            "summary": f"ERROR: Cannot import dependencies for causal DAG: {e}",
        }

    priority_stage = (
        params.get("priority_stage")
        or agent_state.get("rca_priority_stage", "high")
    )
    stage_str = _STAGE_FILE_KEY_FOR_DAG.get(
        str(priority_stage).split("_")[-1],
        "Tier1",
    )

    causal_dag = generate_rca_causal_dag(rca_text, problem_statement, config_path)
    if not causal_dag.get("nodes"):
        return {
            "success": False,
            "data": {"stage": stage_str},
            "summary": (
                "Causal DAG generation returned no usable nodes. "
                "Proceed to the FINAL REPORT — diagram rendering will be skipped."
            ),
        }

    try:
        results_dir = get_results_dir()
        dag_path = results_dir / f"RCA_{stage_str}_dag.json"
        dag_path.write_text(json.dumps(causal_dag), encoding="utf-8")
        node_count = len(causal_dag["nodes"])
        edge_count = len(causal_dag.get("edges", []))
        print(f"[Orchestrator] RCA causal DAG written to: {dag_path.name}")
        return {
            "success": True,
            "data": {
                "dag_path": str(dag_path),
                "stage": stage_str,
                "node_count": node_count,
                "edge_count": edge_count,
            },
            "summary": (
                f"Causal DAG written to {dag_path.name} "
                f"({node_count} nodes, {edge_count} edges). "
                "Proceed to the FINAL REPORT now."
            ),
        }
    except Exception as e:
        return {
            "success": False,
            "data": {"stage": stage_str},
            "summary": f"ERROR writing causal DAG file: {e}. Proceed to FINAL REPORT anyway.",
        }


# ---------------------------------------------------------------------------
# 4. TOOL EXECUTORS DISPATCH MAP
# ---------------------------------------------------------------------------

TOOL_EXECUTORS: Dict[str, Any] = {
    "check_llm_availability": execute_check_llm_availability,
    "select_files": execute_select_files,
    "check_file_availability": execute_check_file_availability,
    "analyze_yaml": execute_analyze_yaml,
    "analyze_json": execute_analyze_json,
    "analyze_logs": execute_analyze_logs,
    "validate_token_budget": execute_validate_token_budget,
    "perform_rca": execute_perform_rca,
    "generate_causal_dag": execute_generate_causal_dag,
}
