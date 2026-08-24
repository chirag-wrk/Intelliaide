"""
LLM RCA Agent

Uses Claude LLM to aggregate, summarize, and perform root cause analysis (RCA)
on YAML analysis data returned by ML_YAML_CLASSIFICATION (Error-classified objects only).
Sends only YAML objects to the LLM (line numbers removed).
"""

import os
import re
import json
import subprocess
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

try:
    import google.auth
    import google.auth.transport.requests
    GOOGLE_AUTH_AVAILABLE = True
except ImportError:
    GOOGLE_AUTH_AVAILABLE = False


# ---------------------------------------------------------------------------
# GCP Vertex AI / LLM connection helpers
# ---------------------------------------------------------------------------

_GCP_CREDENTIALS_CACHE: Optional[str] = None


def _normalize_service_account_key_data(key_data: dict) -> dict:
    """Return a copy of service-account JSON with a usable private_key."""
    normalized = dict(key_data)
    private_key = normalized.get("private_key")
    if isinstance(private_key, str):
        normalized["private_key"] = (
            private_key.replace("\\n", "\n").replace("\r\n", "\n").strip()
        )
    return normalized


def _service_account_private_key_is_valid(key_data: dict) -> bool:
    """Return True when embedded service-account JSON has a parseable private key."""
    if not isinstance(key_data, dict) or key_data.get("type") != "service_account":
        return False
    normalized = _normalize_service_account_key_data(key_data)
    if not normalized.get("private_key"):
        return False
    if GOOGLE_AUTH_AVAILABLE:
        try:
            from google.oauth2 import service_account
            service_account.Credentials.from_service_account_info(
                normalized,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            return True
        except Exception:
            return False
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        load_pem_private_key(normalized["private_key"].encode("utf-8"), password=None)
        return True
    except Exception:
        return False


def _load_gcp_credentials_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not data.get("type"):
            return None
        if data.get("type") == "service_account" and not _service_account_private_key_is_valid(data):
            return None
        return data
    except Exception:
        return None


def _is_valid_gcp_credentials_file(path: str) -> bool:
    return _load_gcp_credentials_json(path) is not None


def _materialize_service_account_credentials(key_data: dict) -> str:
    normalized = _normalize_service_account_key_data(key_data)
    if not _service_account_private_key_is_valid(normalized):
        raise RuntimeError(
            "Invalid service account private_key in config.json. "
            "Download a fresh JSON key from GCP Console and either: "
            "(1) set claude.service_account_key_file to that file path, or "
            "(2) create gcloud-adc-secret with --from-file=application_default_credentials.json=<key.json>. "
            "Do not paste private_key by hand; copy/paste often corrupts the PEM."
        )
    cred_dir = os.environ.get("CLOUDSDK_CONFIG") or "/tmp/gcloud"
    os.makedirs(cred_dir, exist_ok=True)
    cred_path = os.path.join(cred_dir, "service_account_credentials.json")
    with open(cred_path, "w", encoding="utf-8") as f:
        json.dump(normalized, f)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = cred_path
    return cred_path


def ensure_gcp_credentials_from_config(config_path: str = "config.json") -> Optional[str]:
    """
    Materialize GCP credentials for Vertex AI.

    Priority:
      1. Mounted GOOGLE_APPLICATION_CREDENTIALS file (k8s gcloud-adc-secret)
      2. claude.service_account_key_file in config.json
      3. claude.service_account_key_data in config.json (only if private key parses)
    """
    global _GCP_CREDENTIALS_CACHE
    if _GCP_CREDENTIALS_CACHE and os.path.isfile(_GCP_CREDENTIALS_CACHE):
        return _GCP_CREDENTIALS_CACHE

    config = load_config(config_path)
    claude_config = config.get("claude", {}) if isinstance(config.get("claude"), dict) else {}

    adc_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if adc_path and _is_valid_gcp_credentials_file(adc_path):
        _GCP_CREDENTIALS_CACHE = adc_path
        return adc_path

    key_file = (claude_config.get("service_account_key_file") or "").strip()
    if key_file and _is_valid_gcp_credentials_file(key_file):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = key_file
        _GCP_CREDENTIALS_CACHE = key_file
        return key_file

    key_data = claude_config.get("service_account_key_data")
    if isinstance(key_data, dict) and key_data.get("type") == "service_account":
        cred_path = _materialize_service_account_credentials(key_data)
        _GCP_CREDENTIALS_CACHE = cred_path
        return cred_path

    if adc_path and os.path.isfile(adc_path):
        raise RuntimeError(
            f"GCP credentials at {adc_path} are missing or invalid. "
            "Recreate gcloud-adc-secret from a downloaded service-account JSON file."
        )

    return None


def _resolve_auth_type(claude_config: dict) -> str:
    auth_type = (claude_config.get("auth_type") or "").strip()
    if auth_type:
        return auth_type
    adc_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if adc_path and _is_valid_gcp_credentials_file(adc_path):
        return "gcloud"
    if (claude_config.get("service_account_key_file") or "").strip():
        return "gcloud"
    if isinstance(claude_config.get("service_account_key_data"), dict):
        return "gcloud"
    if claude_config.get("api_key"):
        return "api_key"
    raise RuntimeError(
        "Missing required 'claude.auth_type' in config.json. "
        "Use 'gcloud' with a mounted service-account JSON / ADC file, or 'api_key' with api_key."
    )


def _get_gcloud_token() -> str:
    """Get OAuth2 access token for GCP Vertex AI authentication.

    Prefers google-auth library (works with GOOGLE_APPLICATION_CREDENTIALS
    and authorized_user / service_account credentials). Falls back to
    gcloud CLI if the library is not installed.
    """
    ensure_gcp_credentials_from_config()

    if GOOGLE_AUTH_AVAILABLE:
        scopes = ["https://www.googleapis.com/auth/cloud-platform"]
        credentials, _ = google.auth.default(scopes=scopes)
        credentials.refresh(google.auth.transport.requests.Request())
        if not credentials.token:
            raise RuntimeError("google-auth returned empty token")
        return credentials.token

    result = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to get gcloud access token: {result.stderr.strip()}")
    return result.stdout.strip()


def resolve_api_token(claude_config: dict) -> str:
    """Return the Bearer token from explicitly configured auth settings."""
    auth_type = _resolve_auth_type(claude_config)
    if auth_type == "gcloud":
        return _get_gcloud_token()
    if auth_type == "api_key":
        api_key = claude_config.get("api_key")
        if not api_key:
            raise RuntimeError("Missing required 'claude.api_key' in config.json for auth_type='api_key'.")
        return api_key
    raise RuntimeError(f"Unsupported 'claude.auth_type': {auth_type}. Use 'gcloud' or 'api_key'.")


def resolve_vertex_project_id(claude_config: dict) -> str:
    """Resolve GCP project ID for Vertex endpoint URLs."""
    project_id = (claude_config.get("project_id") or "").strip()
    if project_id:
        return project_id

    sa = claude_config.get("service_account_key_data")
    if isinstance(sa, dict) and sa.get("project_id"):
        return str(sa["project_id"]).strip()

    adc_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if adc_path and os.path.isfile(adc_path):
        try:
            with open(adc_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("project_id"):
                return str(data["project_id"]).strip()
        except Exception:
            pass

    raise RuntimeError(
        "Missing Vertex project_id. Set claude.project_id or "
        "service_account_key_data.project_id in config.json."
    )


VERTEX_ENDPOINT_PATTERN = (
    "https://aiplatform.googleapis.com/v1/projects/{project_id}/locations/global/"
    "publishers/anthropic/models/{model_id}:rawPredict"
)


def resolve_api_endpoint(claude_config: dict) -> str:
    """
    Build the full API endpoint URL from config.

    Requires model_id in config. Uses {project_id} from service account when present.
    """
    api_url = claude_config.get("api_url", "").rstrip("/")
    model_id = claude_config.get("model_id")
    if not model_id:
        raise RuntimeError("Missing required 'claude.model_id' in config.json.")

    endpoint_pattern = claude_config.get("endpoint_pattern") or VERTEX_ENDPOINT_PATTERN
    project_id = resolve_vertex_project_id(claude_config)

    if "itpc-gcp-hcm-pe-eng-claude" in endpoint_pattern and project_id != "itpc-gcp-hcm-pe-eng-claude":
        endpoint_pattern = VERTEX_ENDPOINT_PATTERN

    return endpoint_pattern.format(
        api_url=api_url,
        model_id=model_id,
        project_id=project_id,
    )


# ---------------------------------------------------------------------------
# RCA report section normalization
# ---------------------------------------------------------------------------

# Canonical section order the frontend expects
_RCA_SECTION_ORDER = [
    "## User Reported Issue",
    "## Executive Summary",
    "## Chronology of Events",
    "## Primary Root Cause(s)",
    "## Secondary Causes / Contributing Factors",
    "## Aggregated Error Patterns",
    "## Remediation",
    "## Analysis Coverage",
]


def _normalize_rca_report_sections(report_text: str) -> str:
    """Repair common heading omissions/typos in LLM RCA output.

    The frontend expects exact ``## `` section headers.  In practice the model
    sometimes emits resolution content but omits the heading, or uses heading
    variants like ``## Resolutions`` or ``## Recommendations``.
    This function:
      1. Normalizes all resolution/remediation heading variants to ``## Remediation``.
      2. Inserts ``## Remediation`` when missing but numbered resolution content
         exists after ``## Aggregated Error Patterns`` or ``## Secondary Causes``.
    """
    if not report_text or report_text.startswith(("Error:", "Claude API error:")):
        return report_text

    normalized = report_text

    # --- 0. Normalize chronology heading variants to ## Chronology of Events ---
    # LLM may emit ###, add bold markers, or vary the wording across passes.
    normalized = re.sub(
        r"(?im)^#{1,6}\s+\*{0,2}(?:(?:event\s+)?(?:chronology|timeline)(?:\s+of)?(?:\s+(?:key\s+)?events?)?|(?:key\s+)?events?\s+(?:chronology|timeline))\*{0,2}\s*$",
        "## Chronology of Events",
        normalized,
    )

    # --- 1. Normalize all heading variants to ## Remediation ---
    normalized = re.sub(
        r"(?im)^##\s*(resolution|resolutions|recommendation|recommendations|remidiation|remediation)\s*$",
        "## Remediation",
        normalized,
    )
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()

    has_remediation = bool(re.search(r"(?im)^##\s+Remediation\s*$", normalized))

    if has_remediation:
        return normalized

    lines = normalized.splitlines()

    def _find_heading_line(heading: str) -> int:
        for idx, line in enumerate(lines):
            if line.strip() == heading:
                return idx
        return -1

    def _insert_heading(insert_at: int, heading: str) -> None:
        block = [heading]
        if insert_at > 0 and lines[insert_at - 1].strip():
            block.insert(0, "")
        if insert_at < len(lines) and lines[insert_at].strip():
            block.append("")
        for i, item in enumerate(block):
            lines.insert(insert_at + i, item)

    # --- 2. Insert ## Remediation if missing but content exists ---
    aggregated_idx = _find_heading_line("## Aggregated Error Patterns")
    if aggregated_idx != -1:
        i = aggregated_idx + 1
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i < len(lines) and lines[i].lstrip().startswith("|"):
            while i < len(lines) and (not lines[i].strip() or lines[i].lstrip().startswith("|")):
                i += 1
            while i < len(lines) and not lines[i].strip():
                i += 1
            if i < len(lines) and not lines[i].startswith("## "):
                _insert_heading(i, "## Remediation")
                has_remediation = True

    if not has_remediation:
        secondary_idx = _find_heading_line("## Secondary Causes / Contributing Factors")
        if secondary_idx != -1:
            for i in range(secondary_idx + 1, len(lines)):
                if lines[i].startswith("## "):
                    break
                if re.match(r"^\s*\d+\.\s+\[", lines[i]):
                    _insert_heading(i, "## Remediation")
                    has_remediation = True
                    break

    return "\n".join(lines)


def _remove_line_numbers_from_structure(structure: Any) -> Any:
    """
    Remove all line number metadata from a structure (dict/list).
    Keeps only the actual field values (YAML object content).
    """
    if isinstance(structure, dict):
        result = {}
        for key, value in structure.items():
            if key == "_line_numbers" or (isinstance(key, str) and key.endswith("_line_numbers")):
                continue
            result[key] = _remove_line_numbers_from_structure(value)
        return result
    if isinstance(structure, list):
        return [_remove_line_numbers_from_structure(item) for item in structure]
    return structure


try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False

def estimate_tokens(text: str, model_id: str = "") -> int:
    """Estimate token count using GPT's cl100k_base tokenizer for accurate chunking."""
    if not text:
        return 0
    if TIKTOKEN_AVAILABLE:
        try:
            encoding = tiktoken.get_encoding("cl100k_base")
            return len(encoding.encode(text))
        except Exception as e:
            pass # Fallback below if tokenization fails
            
    # Fallback to a safe character approximation if tiktoken is missing
    return max(1, int(len(text) / 3.7))


SUMMARY_MAX_CHARS = 80000


def _get_folder_path(file_path: str) -> str:
    """Extracts the folder path from a file string to group logical components."""
    if not file_path: return "unknown"
    p = Path(file_path)
    return str(p.parent) if len(p.parts) > 1 else "root"

def _split_large_log_entry_tokens(entry: Dict[str, Any], max_tokens: int) -> List[Dict[str, Any]]:
    """Split an oversized log entry into sub-entries at line boundaries based on tokens."""
    content = entry.get("content", "")
    if estimate_tokens(content) <= max_tokens:
        return [entry]

    lines = content.split("\n")
    parts: List[str] = []
    current_lines: List[str] = []
    current_tokens = 0

    for line in lines:
        line_toks = estimate_tokens(line + "\n")
        if current_tokens + line_toks > max_tokens and current_lines:
            parts.append("\n".join(current_lines))
            current_lines = [line]
            current_tokens = line_toks
        else:
            current_lines.append(line)
            current_tokens += line_toks

    if current_lines:
        parts.append("\n".join(current_lines))

    file_name = entry.get("file", "unknown")
    total = len(parts)
    return [
        {**entry, "file": f"{file_name} (part {i+1}/{total})", "content": part}
        for i, part in enumerate(parts)
    ]

def call_claude_reduce_rca(
    chunk_summaries: List[str],
    config_path: str = "config.json",
    problem_statement: Optional[str] = None,
) -> Dict[str, Any]:
    """
    The 'Reduce' step: Synthesizes multiple individual chunk RCA findings 
    into one final, cohesive Master RCA.
    """
    err_result = lambda msg: {"text": msg, "input_tokens": 0, "output_tokens": 0}
    if not REQUESTS_AVAILABLE:
        return err_result("Error: requests package is required.")
        
    config = load_config(config_path)
    claude_config = config.get("claude", {})
    
    try:
        api_token = resolve_api_token(claude_config)
    except RuntimeError as e:
        return err_result(f"Error: {e}")
        
    api_endpoint = resolve_api_endpoint(claude_config)
    max_tokens = claude_config.get("max_tokens", 16384)
    verify_ssl = claude_config.get("verify_ssl", True)
    is_custom_gateway = claude_config.get("custom_gateway", False)

    user_problem = (problem_statement or "").strip() or "Not specified"
    
    combined_summaries = ""
    for idx, text in enumerate(chunk_summaries):
        combined_summaries += f"\n\n=== FINDINGS FROM CHUNK {idx+1} ===\n{text}\n"

    user_content = f"""You are an expert OpenShift/Kubernetes system analyst.
You are provided with several partial Root Cause Analysis (RCA) findings. These findings were generated by analyzing different chunks of a massive log/YAML dataset.

1. USER REPORTED ISSUE:
{user_problem}

2. PARTIAL FINDINGS FROM ALL CHUNKS:
{combined_summaries}

INSTRUCTIONS:
- Synthesize all the chunk findings into one cohesive, final Master Root Cause Analysis report.
- Identify the primary overarching root cause(s) across all chunks.
- Merge and chronologically sort the Chronology of Events (remove duplicates).
- Merge all Aggregated Error Patterns into one unified table.
- Do not mention that this was generated from "chunks". Present it as one unified analysis.

OUTPUT FORMAT — use these EXACT ## headings (frontend parses them programmatically):
## User Reported Issue
(restate in 1-3 sentences)
## Executive Summary
(state the key cause clearly)
## Chronology of Events
(REQUIRED when timestamps appear in the findings; omit otherwise.
 Format as bullet points with bold timestamps:
 - **<timestamp>**: <description of event>
 Merge all chunks chronologically; remove duplicates.)
## Primary Root Cause(s)
(numbered items with bullet-point evidence:
 1. **<Short title>**
    - <evidence point>
    - Evidence: <specific log/yaml reference>
 Do NOT use ### sub-headings — use numbered items with sub-bullets only.)
## Secondary Causes / Contributing Factors
(bullet list)
## Aggregated Error Patterns
(MUST be a pipe-delimited markdown table:
| Pattern | Source | Classification | Significance |
|---------|--------|----------------|--------------|
| ... | ... | ... | ... |
Merge all patterns from all chunks.)
## Remediation
(MANDATORY — numbered, step-by-step resolution actions. Always include this section.
 Use this EXACT format for every item — no variations:

 ### 1. [SEVERITY] <Title>
 <Description of what to do and why.>

 **Steps:**
 ```bash
 # specific commands
 ```

 **Validation:**
 ```bash
 # commands to confirm the fix worked
 ```

 Use severity tags: [IMMEDIATE — CRITICAL], [HIGH], [MEDIUM].
 Number items sequentially starting from 1.
 Merge and deduplicate resolutions from all chunks.
 Reference specific OpenShift/Kubernetes components, namespaces, and file paths.
 Do NOT use plain numbered lists — MUST use ### sub-headings for each item.)

Do NOT rename headings. Do NOT use plain text for Aggregated Error Patterns.
"""

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_token}"}
    
    if is_custom_gateway:
        payload = {
            "anthropic_version": "vertex-2023-10-16",
            "messages": [{"role": "user", "content": [{"type": "text", "text": user_content}]}],
            "max_tokens": max_tokens,
            "temperature": 0,
        }
    else:
        model_id = claude_config.get("model_id")
        if not model_id:
            return err_result("Error: Missing required 'claude.model_id' in config.json.")
        payload = {
            "model": model_id,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": user_content}],
        }
    
    try:
        response = requests.post(api_endpoint, headers=headers, json=payload, verify=verify_ssl, timeout=600)
        response.raise_for_status()
        response_json = response.json()
        
        usage = response_json.get("usage", {})
        input_tokens = usage.get("input_tokens") or usage.get("input_tokens_count", 0)
        output_tokens = usage.get("output_tokens") or usage.get("output_tokens_count", 0)
        
        response_text = None
        if "content" in response_json and isinstance(response_json["content"], list):
            response_text = "".join(item.get("text", "") for item in response_json["content"] if isinstance(item, dict) and "text" in item)
            
        if not response_text and "text" in response_json:
            response_text = response_json["text"]
            
        _mid = claude_config.get("model_id", "")
        if not input_tokens: input_tokens = estimate_tokens(user_content, _mid)
        if not output_tokens: output_tokens = estimate_tokens(response_text or "", _mid)
        
        response_text = _normalize_rca_report_sections(response_text or "No text returned.")
        return {
            "text": response_text, 
            "input_tokens": input_tokens, 
            "output_tokens": output_tokens
        }
    except Exception as e:
        return err_result(f"Claude API error during final reduce: {str(e)}")

def _hierarchical_reduce(
    summary_files: List[Path],
    config_path: str,
    problem_statement: Optional[str],
    max_chunk_tokens: int,
    model_id: str,
    level: int = 1
) -> Tuple[str, int, int]:
    """
    Recursively reduces summary files. If combined summaries exceed token limits,
    they are batched, reduced into intermediate files, and recursed until 1 file remains.
    Returns: (final_rca_text, total_input_tokens, total_output_tokens)
    """
    from app_paths import get_results_dir
    import os

    if not summary_files:
        return "No data to reduce.", 0, 0

    # 1. Read the text from all files (freeing memory immediately after batching)
    summaries = []
    for fp in summary_files:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                summaries.append(f.read())
        except Exception as e:
            print(f"[Reduce] Error reading {fp}: {e}")

    # Prompt overhead allowance
    overhead = estimate_tokens(problem_statement or "", model_id) + 2000
    available_tokens = max_chunk_tokens - overhead

    # 2. Batch the summaries by token size
    batches = []
    current_batch = []
    current_tokens = 0

    for summ in summaries:
        toks = estimate_tokens(summ, model_id)
        if current_tokens + toks > available_tokens and current_batch:
            batches.append(current_batch)
            current_batch = [summ]
            current_tokens = toks
        else:
            current_batch.append(summ)
            current_tokens += toks
            
    if current_batch:
        batches.append(current_batch)

    # 3. Base Case: Everything fits into ONE prompt
    if len(batches) == 1:
        res = call_claude_reduce_rca(batches[0], config_path, problem_statement)
        return res.get("text", ""), res.get("input_tokens", 0), res.get("output_tokens", 0)

    # 4. Recursive Case: Too large, requires intermediate reduction
    print(f"[RCA Reduce] Level {level} summaries exceed budget. Splitting into {len(batches)} batches.")
    
    next_level_files = []
    total_in = 0
    total_out = 0

    for i, batch in enumerate(batches):
        res = call_claude_reduce_rca(batch, config_path, problem_statement)
        text = res.get("text", "")
        total_in += res.get("input_tokens", 0)
        total_out += res.get("output_tokens", 0)

        # Write intermediate reduction to disk to save memory
        out_path = get_results_dir() / f"reduce_level{level}_batch{i+1}.txt"
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(text)
            next_level_files.append(out_path)
        except Exception as e:
            print(f"[Reduce] Warning: Could not save intermediate batch: {e}")

    # Clear memory of raw summaries before recursion
    del summaries
    del batches

    # Recurse on the new intermediate files
    final_text, sub_in, sub_out = _hierarchical_reduce(
        next_level_files, config_path, problem_statement, max_chunk_tokens, model_id, level + 1
    )
    
    return final_text, total_in + sub_in, total_out + sub_out


def chunk_payload(
    yaml_errors: Dict[str, Any],
    log_entries: List[Dict[str, Any]],
    max_chunk_tokens: int = 80000
) -> List[Dict[str, Any]]:
    """
    Groups files logically by folder path, then builds chunks up to max_chunk_tokens.
    Ensures context is preserved by keeping related files in the same chunk where possible.
    """
    all_items = []
    
    # 1. Normalize YAMLs
    for fkey, objs in yaml_errors.items():
        all_items.append({
            "folder": _get_folder_path(fkey), 
            "type": "yaml", 
            "file": fkey, 
            "payload": objs
        })

    # 2. Normalize Logs
    for entry in log_entries:
        fkey = entry.get("file", "")
        all_items.append({
            "folder": _get_folder_path(fkey), 
            "type": "log", 
            "file": fkey, 
            "payload": entry
        })

    # 3. Group logically by folder
    grouped_items = {}
    for item in all_items:
        grouped_items.setdefault(item["folder"], []).append(item)

    chunks = []
    current_chunk = {"yaml_errors": {}, "log_entries": [], "_tokens": 0}

    def close_chunk():
        nonlocal current_chunk
        if current_chunk["yaml_errors"] or current_chunk["log_entries"]:
            # Remove the internal tracker before finalizing
            clean_chunk = {
                "yaml_errors": current_chunk["yaml_errors"],
                "log_entries": current_chunk["log_entries"]
            }
            chunks.append(clean_chunk)
            current_chunk = {"yaml_errors": {}, "log_entries": [], "_tokens": 0}

    # 4. Pack chunks while iterating through logical groups
    for folder, items in grouped_items.items():
        for item in items:
            item_str = json.dumps(item["payload"], indent=2, default=str)
            item_tokens = estimate_tokens(item_str)

            # Handle oversized items
            if item_tokens > max_chunk_tokens:
                if item["type"] == "log":
                    split_logs = _split_large_log_entry_tokens(item["payload"], max_chunk_tokens)
                    for sub_log in split_logs:
                        sub_toks = estimate_tokens(json.dumps(sub_log, indent=2, default=str))
                        if current_chunk["_tokens"] + sub_toks > max_chunk_tokens:
                            close_chunk()
                        current_chunk["log_entries"].append(sub_log)
                        current_chunk["_tokens"] += sub_toks
                elif item["type"] == "yaml":
                    # Fallback for massive YAML arrays: force a chunk close, then add it
                    # (You can implement `_split_large_yaml_tokens` similarly if needed)
                    if current_chunk["_tokens"] > 0:
                        close_chunk()
                    current_chunk["yaml_errors"][item["file"]] = item["payload"]
                    close_chunk()
                continue

            # Standard packing
            if current_chunk["_tokens"] + item_tokens > max_chunk_tokens:
                close_chunk()

            if item["type"] == "yaml":
                current_chunk["yaml_errors"][item["file"]] = item["payload"]
            else:
                current_chunk["log_entries"].append(item["payload"])
            
            current_chunk["_tokens"] += item_tokens

    close_chunk()
    
    print(f"[RCA Chunked] Produced {len(chunks)} contextual chunk(s) (Limit: {max_chunk_tokens:,} tokens)")
    return chunks


def prepare_payload_for_llm(ml_classification_result: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Prepare data for LLM: normalize input format, remove all line numbers,
    keep only YAML objects (critical_fields).

    Accepts two input formats (defensive — handles both):
      Format A (flat):    {file_name: [obj1, obj2, ...]}
      Format B (wrapped): {file_name: {"objects": [obj1, ...], "summary": {...}}}

    Args:
        ml_classification_result: Dict mapping file_name -> objects (either format).

    Returns:
        Dict mapping file_name -> list of YAML objects (critical_fields with line numbers stripped)
    """
    payload = {}
    for file_name, file_data in (ml_classification_result or {}).items():
        # Normalize: extract the objects list regardless of input format
        if isinstance(file_data, dict) and "objects" in file_data:
            objects = file_data["objects"]  # Format B
        elif isinstance(file_data, list):
            objects = file_data             # Format A
        else:
            continue  # skip non-data entries (e.g. metadata keys)

        yaml_objects = []
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            raw_critical_fields = obj.get("critical_fields", {})
            # Strip line numbers; send only YAML object content
            cleaned = _remove_line_numbers_from_structure(raw_critical_fields)
            if cleaned:
                yaml_objects.append(cleaned)
        if yaml_objects:
            payload[file_name] = yaml_objects
    return payload


# ISO timestamp pattern (e.g. 2025-12-11T14:25:06.104163841Z or 2026-01-12 14:25:06)
_ISO_TS = re.compile(r"(\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)")
# Log time= pattern (e.g. time="2025-12-11T12:24:50.249038947Z" or time="2025-12-11 12:25:06")
_LOG_TIME = re.compile(r'time="(\d{4}-\d{2}-\d{2}(?:T|\s)\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)"', re.IGNORECASE)
# CRI-O style: time="2025-12-11 .249038947Z" (date + space + fractional seconds)
_LOG_TIME_CRIO = re.compile(r'time="(\d{4}-\d{2}-\d{2})\s+\.(\d+)Z"', re.IGNORECASE)
# Fallback: date only (e.g. time="2025-12-11 .249038947Z" when CRI-O regex did not match)
_LOG_TIME_DATE = re.compile(r'time="(\d{4}-\d{2}-\d{2})[\s.]', re.IGNORECASE)


def _parse_iso_like(ts_str: str) -> Optional[str]:
    """Normalize to sortable ISO-like string (YYYY-MM-DDTHH:MM:SS). Returns None if unparseable."""
    if not ts_str or len(ts_str) < 19:
        return None
    # Allow space or T after date; allow fractional seconds
    normalized = ts_str.replace(" ", "T", 1).strip()
    if "T" in normalized:
        date_part, rest = normalized.split("T", 1)
        rest = re.sub(r"[^\d:]", "", rest[:8])  # keep only HH:MM:SS digits/colons
        if len(rest) >= 6:
            return f"{date_part}T{rest[:2]}:{rest[2:4]}:{rest[4:6]}"
    return normalized[:19] if len(normalized) >= 19 else None


def _extract_timestamps_from_value(value: Any, key_path: str, out: List[Tuple[str, str, str]]) -> None:
    """Recursively extract timestamp keys and ISO values from YAML-like structure. Appends (sortable_ts, key_path, value_snippet)."""
    if isinstance(value, dict):
        for k, v in value.items():
            key_lower = k.lower() if isinstance(k, str) else ""
            path = f"{key_path}.{k}" if key_path else k
            if any(t in key_lower for t in ("timestamp", "time", "created", "updated", "transition", "eventtime", "observed")):
                if isinstance(v, str) and _ISO_TS.search(v):
                    sortable = _parse_iso_like(_ISO_TS.search(v).group(1))
                    if sortable:
                        out.append((sortable, path, v[:120]))
                else:
                    _extract_timestamps_from_value(v, path, out)
            else:
                _extract_timestamps_from_value(v, path, out)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _extract_timestamps_from_value(item, f"{key_path}[{i}]", out)
    elif isinstance(value, str) and key_path and _ISO_TS.search(value):
        sortable = _parse_iso_like(_ISO_TS.search(value).group(1))
        if sortable:
            out.append((sortable, key_path, value[:120]))


def build_chronology_from_payload(
    payload_yaml: Dict[str, List[Dict[str, Any]]],
    log_error_entries: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, str]]:
    """
    Build a time-ordered chronology from YAML timestamps and log line timestamps.
    For use in RCA so the LLM can include a chronology of events with timestamps.

    Returns:
        List of {"timestamp": sortable_iso, "source": file or "YAML: file", "snippet": short description}
    """
    events: List[Tuple[str, str, str]] = []  # (sortable_ts, source, snippet)

    for file_name, objects in (payload_yaml or {}).items():
        for obj in objects:
            cf = obj if isinstance(obj, dict) else (obj.get("critical_fields") or obj)
            _extract_timestamps_from_value(cf, f"YAML:{file_name}", events)

    for entry in (log_error_entries or []):
        content = entry.get("content") or ""
        file_name = entry.get("file") or "log"
        for line in content.split("\n"):
            line = line.strip()
            if not line:
                continue
            # time="2025-12-11T12:24:50.249Z" or time="2025-12-11 12:24:50"
            m = _LOG_TIME.search(line)
            if m:
                raw = m.group(1)
                sortable = _parse_iso_like(raw.replace(" ", "T"))
                if sortable:
                    snippet = line[:150].replace("\n", " ")
                    events.append((sortable, file_name, snippet))
                continue
            # CRI-O: time="2025-12-11 .249038947Z" (preserve fractional order)
            m_crio = _LOG_TIME_CRIO.search(line)
            if m_crio:
                date_part = m_crio.group(1)
                frac = m_crio.group(2)[:9].ljust(9, "0")  # nanoseconds -> 9 digits
                sortable = f"{date_part}T00:00:00.{frac}"
                events.append((sortable, file_name, line[:150].replace("\n", " ")))
                continue
            # time="2025-12-11 .249Z" (date only, no fraction captured)
            m2 = _LOG_TIME_DATE.search(line)
            if m2:
                sortable = _parse_iso_like(m2.group(1) + "T00:00:00")
                if sortable:
                    events.append((sortable, file_name, line[:150].replace("\n", " ")))
                continue
            # ISO at start of line
            iso = _ISO_TS.match(line)
            if iso:
                sortable = _parse_iso_like(iso.group(1))
                if sortable:
                    events.append((sortable, file_name, line[:150].replace("\n", " ")))

    # Dedupe by (ts, snippet) and sort by timestamp
    seen = set()
    unique = []
    for sortable_ts, source, snippet in events:
        key = (sortable_ts, snippet[:80])
        if key in seen:
            continue
        seen.add(key)
        unique.append((sortable_ts, source, snippet))
    unique.sort(key=lambda x: x[0])

    return [{"timestamp": ts, "source": src, "snippet": snip} for ts, src, snip in unique]


def format_chronology_block(chronology: List[Dict[str, str]], max_events: int = 150) -> str:
    """
    Format chronology list as a clear text block for the prompt.
    Samples events if too many (first + last) to stay within token limits.
    """
    if not chronology:
        return ""
    if len(chronology) <= max_events:
        sampled = chronology
    else:
        half = max_events // 2
        sampled = chronology[:half] + chronology[-half:]
    lines = []
    for e in sampled:
        ts = e.get("timestamp", "")
        src = e.get("source", "")
        snip = (e.get("snippet") or "").replace("\n", " ").strip()[:120]
        lines.append(f"  {ts}  |  {src}  |  {snip}")
    return "\n".join(lines)


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


def load_config(config_path: str = "config.json") -> Dict:
    """Load configuration from JSON file. Uses app_paths when config_path is default (frozen exe)."""
    try:
        from app_paths import get_config_path
        if config_path == "config.json":
            path = get_config_path()
        else:
            path = Path(config_path)
    except ImportError:
        path = Path(__file__).parent / config_path
    if not path.exists():
        print(f"Warning: Could not load config from {config_path} (tried: ['{path}', '{path}']). "
              "File missing, empty, or invalid JSON.")
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg
    except Exception as e:
        print(f"Warning: Could not load config from {path}: {e}")
        return {}


def build_rca_prompt(payload_for_llm: Dict[str, Any], problem_statement: Optional[str] = None) -> str:
    """
    Build the full RCA user-content prompt string from the payload.
    Shared by call_claude_rca (for the API call) and validate_token_budget (for accurate estimation).
    """
    chronology = payload_for_llm.get("chronology") or []
    chronology_block = format_chronology_block(chronology)
    payload_for_data = {k: v for k, v in payload_for_llm.items() if k != "chronology"}
    data_str = json.dumps(payload_for_data, indent=2, default=str)
    user_problem = (problem_statement or "").strip() or "Not specified"

    chronology_section = ""
    if chronology_block:
        chronology_section = f"""
2. CHRONOLOGY OF EVENTS (include in your ## Chronology of Events section):
{chronology_block}

3. AGGREGATED DATA (YAML/LOG/JSON error objects with file metadata):
"""
    else:
        chronology_section = """
2. AGGREGATED DATA (YAML/LOG/JSON error objects with file metadata):
"""

    return f"""You are an expert OpenShift/Kubernetes system analyst performing root cause analysis.

TASK: Identify the root cause(s) and state clearly: "The key cause for the user's problem is: [...]"

SOURCES:

1. USER REPORTED ISSUE (primary focus):
{user_problem}
{chronology_section}
{data_str}

INSTRUCTIONS:
- Correlate the user's issue with YAML error patterns and/or log error content.
- Identify PRIMARY root cause(s) with evidence, SECONDARY contributing factors,
  and the CHAIN OF EVENTS (use CHRONOLOGY data above when provided).
- Prioritize root causes by relevance to the USER REPORTED ISSUE.
- Base analysis on actual evidence only — do not fabricate.

CAUSAL HIERARCHY — evaluate in order before naming PRIMARY root cause:

  L0  External / infrastructure prerequisites
      - Do api / api-int / apps hostnames from infrastructures.yaml resolve?
      - Are upstream DNS servers in dnses.yaml reachable/correct?
      - Corporate proxy / trusted CA (proxies.yaml) if x509 or ImagePull present
      - Evidence: "no such host", NXDOMAIN, lookup failures, failed
        podnetworkconnectivitychecks to API endpoints
  L1  API reachability / kubelet → API path
      - Lease renewal failures, NotReady, NodeStatusUnknown
  L2  etcd quorum / peer communication
  L3  CNI / OVN / OVS dataplane
  L4  Runtime / CRI-O / image pull / TLS trust bundle / operator pods

RULES:
- You may list L2–L4 findings, but do NOT mark them PRIMARY if an unresolved
  L0/L1 issue explains them.
- High-frequency OVN/CRI-O/NetworkManager errors after API loss are usually
  SECONDARY cascade symptoms.
- If evidence is insufficient to confirm or deny L0 (external DNS), say so
  explicitly under Primary Root Cause as "Unresolved prerequisite" and list
  the exact external DNS records that must be verified outside must-gather.
- For RHOSO/Nova downs coinciding with master NotReady: treat Nova as a
  dependent symptom unless Nova-specific evidence predates control-plane loss.

REMEDIATION RULE:
- If L0 is primary or unresolved, remediation MUST start with verifying/
  creating external DNS records for api, api-int, and apps (and VIP/LB
  mappings). Do not lead with OVN restart, CRI-O wipe, or etcd restore.
- If proxy/TLS trust is primary, remediation MUST address proxies.yaml /
  trustedCA / additionalTrustBundle before runtime-only fixes.

EVIDENCE DISCIPLINE (generic — all issue types):
- Extract distinctive literals from the USER REPORTED ISSUE (numbers, timeouts,
  durations, field names, object kinds, error substrings, hostnames). Prefer
  config/API objects whose fields or names match those literals over
  high-frequency unrelated log noise.
- When the failure implicates a class of cluster config objects, inventory ALL
  objects of that class in the evidence before naming a multi-component
  infrastructure cascade as PRIMARY.
- Prefer the simplest actionable config remediation that explains the reported
  error (fix/move/remove/disable/reconfigure the matching object) over
  multi-layer control-plane collapse narratives, unless evidence shows those
  config objects are healthy and the cascade is independently causal.
- High-frequency secondary symptoms (TLS EOF, cAdvisor, OVN retries, operator
  churn, CrashLoop storms) must not outrank a quieter config field or CR that
  literally matches the user error.
- Use must-gather routing/index guidance for component-specific field paths and
  inventories; do not invent cascade primacy that contradicts that guidance.

OUTPUT FORMAT — use these EXACT ## headings (frontend parses them programmatically):

## User Reported Issue
(restate in 1-3 sentences)
## Executive Summary
(state the key cause clearly)
## Chronology of Events
(REQUIRED when CHRONOLOGY data was provided above; omit otherwise.
 Format: - **<timestamp>**: <description>)
## Primary Root Cause(s)
(numbered items with bullet-point evidence.
 For each candidate include:
 - **Prerequisite check:** L0 DNS/API resolution = Confirmed failure | Ruled out | Insufficient evidence)
## Secondary Causes / Contributing Factors
(bullet list)
## Aggregated Error Patterns
(MUST be a pipe-delimited markdown table:
| Pattern | Source | Classification | Significance |
|---------|--------|----------------|--------------|
| ... | ... | ... | ... |)
## Remediation
(MANDATORY — numbered, step-by-step resolution actions. Always include this section.
 Use this EXACT format for every item — no variations:

 ### 1. [SEVERITY] <Title>
 <Description of what to do and why.>

 **Steps:**
 ```bash
 # specific commands
 ```

 **Validation:**
 ```bash
 # commands to confirm the fix worked
 ```

 Use severity tags: [IMMEDIATE — CRITICAL], [HIGH], [MEDIUM].
 Number items sequentially starting from 1.
 Reference specific OpenShift/Kubernetes components, namespaces, and file paths.
 Do NOT use plain numbered lists — MUST use ### sub-headings for each item.)

Do NOT rename headings. Do NOT use plain text for Aggregated Error Patterns."""


def call_claude_rca(
    payload_for_llm: Dict[str, Any],
    config_path: str = "config.json",
    problem_statement: Optional[str] = None,
    prior_chunk_summary: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Call Claude LLM to aggregate, summarize, and perform root cause analysis on the payload.
    
    Args:
        payload_for_llm: Combined payload: {"yaml_errors": {file_name: [objects]}, "log_errors": [{file, content}, ...]}
        config_path: Path to config.json
        problem_statement: Optional user problem statement for context
        prior_chunk_summary: Condensed summary from prior chunks (for chunked RCA processing)
    
    Returns:
        Dict with keys: text, input_tokens, output_tokens (or error string in text)
    """
    err_result = lambda msg: {"text": msg, "input_tokens": 0, "output_tokens": 0}
    if not REQUESTS_AVAILABLE:
        return err_result("Error: requests package is required. Install with: pip install requests")
    
    config = load_config(config_path)
    claude_config = config.get("claude", {})
    max_tokens = claude_config.get("max_tokens", 16384)
    verify_ssl = claude_config.get("verify_ssl", True)
    is_custom_gateway = claude_config.get("custom_gateway", False)

    # ── GCP Vertex AI endpoint & gcloud token auth ──
    try:
        api_token = resolve_api_token(claude_config)
    except RuntimeError as e:
        return err_result(f"Error: {e}")
    api_endpoint = resolve_api_endpoint(claude_config)

    if not api_token:
        return err_result("Error: No API token available. Check auth_type/api_key in config.json or ANTHROPIC_API_KEY env var.")

    # OLD VERSION — Red Hat custom gateway / direct Anthropic endpoint logic (no longer active):
    # api_key = claude_config.get("api_key") or os.getenv("ANTHROPIC_API_KEY")
    # api_url = claude_config.get("api_url", "https://api.anthropic.com").rstrip("/")
    # is_custom_gateway = "api.anthropic.com" not in api_url
    # if is_custom_gateway:
    #     api_endpoint = f"{api_url}/sonnet/models/{model_id}:streamRawPredict"
    # else:
    #     api_endpoint = f"{api_url}/v1/messages"

    user_content = build_rca_prompt(payload_for_llm, problem_statement)
    if prior_chunk_summary:
        user_content = (
            "PRIOR ANALYSIS CONTEXT (from earlier data chunks — "
            "refine and expand upon these findings with the new evidence below):\n"
            "---\n"
            f"{prior_chunk_summary}\n"
            "---\n\n"
            + user_content
        )
    
    # ── TEMPORARY: Save full RCA payload to Results/payload.txt ──
    try:
        from app_paths import get_results_dir
        _payload_path = get_results_dir() / "payload.txt"
        with open(_payload_path, "w", encoding="utf-8") as _pf:
            _pf.write("=" * 100 + "\n")
            _pf.write("RCA PAYLOAD — Full prompt sent to LLM\n")
            _pf.write("=" * 100 + "\n\n")
            _pf.write(user_content)
            _pf.write("\n")
        print(f"[RCA] Payload saved to: {_payload_path}")
    except Exception as _save_err:
        print(f"[RCA] Warning: Could not save payload: {_save_err}")
    # ── END TEMPORARY ──

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_token}"}
    
    if is_custom_gateway:
        payload = {
            "anthropic_version": "vertex-2023-10-16",
            "messages": [{"role": "user", "content": [{"type": "text", "text": user_content}]}],
            "max_tokens": max_tokens,
            "temperature": 0,
        }
    else:
        model_id = claude_config.get("model_id")
        if not model_id:
            return err_result("Error: Missing required 'claude.model_id' in config.json.")
        payload = {
            "model": model_id,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": user_content}],
        }
    
    try:
        response = requests.post(
            api_endpoint,
            headers=headers,
            json=payload,
            verify=verify_ssl,
            timeout=600,
        )
        response.raise_for_status()
        response_json = response.json()
        
        # Extract usage (input_tokens, output_tokens) from API response if present
        usage = response_json.get("usage") or {}
        input_tokens = usage.get("input_tokens") or usage.get("input_tokens_count")
        output_tokens = usage.get("output_tokens") or usage.get("output_tokens_count")
        
        if is_custom_gateway:
            response_text = None
            if "content" in response_json:
                content = response_json["content"]
                if isinstance(content, list) and content:
                    text_parts = []
                    for item in content:
                        if isinstance(item, dict) and "text" in item:
                            text_parts.append(item["text"])
                    response_text = "".join(text_parts) if text_parts else None
                elif isinstance(content, str):
                    response_text = content
            if not response_text and "text" in response_json:
                response_text = response_json["text"]
            if not response_text:
                response_text = json.dumps(response_json, indent=2)
        else:
            if "content" in response_json and response_json["content"]:
                response_text = response_json["content"][0].get("text", "")
            else:
                response_text = json.dumps(response_json, indent=2)
        
        response_text = _normalize_rca_report_sections(response_text or "No response content from Claude.")
        _mid = claude_config.get("model_id", "")
        if input_tokens is None:
            input_tokens = estimate_tokens(user_content, _mid)
        if output_tokens is None:
            output_tokens = estimate_tokens(response_text, _mid)
        return {"text": response_text, "input_tokens": input_tokens or 0, "output_tokens": output_tokens or 0}
    except requests.exceptions.RequestException as e:
        err = str(e)
        if hasattr(e, "response") and e.response is not None:
            try:
                err += "\n" + json.dumps(e.response.json(), indent=2)
            except Exception:
                err += f"\nStatus: {e.response.status_code}\n{e.response.text[:500]}"
        return err_result(f"Claude API error: {err}")


def _condense_rca_for_continuation(full_rca: str, max_chars: int = 4000) -> str:
    """Extract key findings from a full RCA to reduce token usage in continuation prompts.

    Keeps Executive Summary and Primary Root Cause(s) in full, trims everything else.
    """
    if not full_rca or len(full_rca) <= max_chars:
        return full_rca

    sections: Dict[str, str] = {}
    current_heading = "_preamble"
    current_lines: List[str] = []

    for line in full_rca.split("\n"):
        if line.startswith("## "):
            sections[current_heading] = "\n".join(current_lines).strip()
            current_heading = line.strip()
            current_lines = []
        else:
            current_lines.append(line)
    sections[current_heading] = "\n".join(current_lines).strip()

    priority_headings = [
        "## Executive Summary",
        "## Primary Root Cause(s)",
    ]
    secondary_headings = [
        "## Chronology of Events",
        "## Secondary Causes / Contributing Factors",
        "## Remediation",
    ]

    parts: List[str] = []
    for h in priority_headings:
        if h in sections and sections[h]:
            parts.append(f"{h}\n{sections[h]}")

    remaining_budget = max_chars - sum(len(p) for p in parts) - 200
    for h in secondary_headings:
        body = sections.get(h, "")
        if body and remaining_budget > 200:
            trimmed = body[:remaining_budget]
            parts.append(f"{h}\n{trimmed}")
            remaining_budget -= len(trimmed)

    return "\n\n".join(parts) if parts else full_rca[:max_chars]


def call_claude_rca_continued(
    payload_for_llm: Dict[str, Any],
    config_path: str = "config.json",
    problem_statement: Optional[str] = None,
    previous_rca_summary: Optional[str] = None,
    previous_priority_stage: str = "high",
    new_priority_stage: str = "medium",
    user_feedback_text: str = "",
) -> Dict[str, Any]:
    """
    Call Claude for an updated RCA given previous RCA and new file content (continuation).
    Used when the user indicates RCA is not satisfactory and we add medium/low priority data.
    """
    err_result = lambda msg: {"text": msg, "input_tokens": 0, "output_tokens": 0}
    if not REQUESTS_AVAILABLE:
        return err_result("Error: requests package is required. Install with: pip install requests")
    config = load_config(config_path)
    claude_config = config.get("claude", {})
    max_tokens = claude_config.get("max_tokens", 16384)
    verify_ssl = claude_config.get("verify_ssl", True)
    is_custom_gateway = claude_config.get("custom_gateway", False)

    # ── GCP Vertex AI endpoint & gcloud token auth ──
    try:
        api_token = resolve_api_token(claude_config)
    except RuntimeError as e:
        return err_result(f"Error: {e}")
    api_endpoint = resolve_api_endpoint(claude_config)

    if not api_token:
        return err_result("Error: No API token available. Check auth_type/api_key in config.json.")

    payload_for_data = {k: v for k, v in payload_for_llm.items() if k != "chronology"}
    data_str = json.dumps(payload_for_data, indent=2, default=str)
    chronology = payload_for_llm.get("chronology") or []
    chronology_block = format_chronology_block(chronology)
    user_problem = (problem_statement or "").strip() or "Not specified"

    condensed_rca = _condense_rca_for_continuation(
        (previous_rca_summary or "").strip(), max_chars=4000
    )

    chronology_section = ""
    if chronology_block:
        chronology_section = f"""
NEW DATA — CHRONOLOGY OF EVENTS (from {new_priority_stage} priority files):
{chronology_block}

"""
    feedback_section = ""
    if user_feedback_text and user_feedback_text.strip():
        feedback_section = f"""
3. USER FEEDBACK (why the previous RCA was not satisfactory — pay close attention to this):
{user_feedback_text.strip()[:4000]}

"""

    data_section_num = "4" if feedback_section else "3"
    user_content = f"""You are an expert OpenShift/Kubernetes system analyst. Produce an UPDATED RCA incorporating additional data from {new_priority_stage} priority files.

1. USER REPORTED ISSUE:
{user_problem}

2. KEY FINDINGS FROM PREVIOUS RCA (from {previous_priority_stage} priority files):
---
{condensed_rca}
---
{feedback_section}
{data_section_num}. NEW EVIDENCE (from {new_priority_stage} priority files only — not previously analyzed):
{chronology_section}
{data_str}

INSTRUCTIONS:
- Combine previous key findings with the new evidence above. State: "The key cause for the user's problem is: [...]"
- If USER FEEDBACK or NEW EVIDENCE contradicts the previous primary root
  cause, REPLACE it. Do not "confirm" a prior cause when DNS/API resolution
  or infrastructure gaps better explain the timeline.
- Prefer infrastructure prerequisites (external DNS, VIP/LB reachability,
  proxy/TLS trust) over in-cluster symptoms (OVN socket, CRI-O x509, etcd
  dial errors) when both are present and the former explains the latter.
- If new data changes the root cause, explain how. If it confirms, add supporting evidence.
- Do NOT repeat raw data already covered by the previous findings. Focus on what the new evidence adds or changes.

CAUSAL HIERARCHY — re-evaluate in order (L0→L4) even if the previous RCA skipped it:
  L0 External DNS / VIP / proxy trust → L1 API reachability → L2 etcd → L3 OVN/CNI → L4 CRI-O/runtime/operators
- Do NOT mark L2–L4 PRIMARY if unresolved L0/L1 explains the outage.
- For each Primary Root Cause include: **Prerequisite check:** L0 = Confirmed failure | Ruled out | Insufficient evidence
- If L0 is primary/unresolved, remediation MUST start with external DNS (api/api-int/apps) or proxy trust — not OVN/CRI-O/etcd first.
- Apply EVIDENCE DISCIPLINE: match user-error literals to config fields; complete
  inventory of the implicated config class; prefer actionable config remediation
  over cascade narratives unless that config class is proven healthy.

OUTPUT FORMAT — use these EXACT ## headings (frontend parses them programmatically):

## User Reported Issue
(restate in 1-3 sentences)
## Executive Summary
(state the key cause clearly)
## Chronology of Events
(REQUIRED when CHRONOLOGY data was provided above; omit otherwise.
 Format as bullet points with bold timestamps:
 - **<timestamp>**: <description of event>)
## Primary Root Cause(s)
(numbered items with bullet-point evidence:
 1. **<Short title>**
    - <evidence point>
    - Evidence: <specific log/yaml reference>
 Do NOT use ### sub-headings — use numbered items with sub-bullets only.)
## Secondary Causes / Contributing Factors
(bullet list)
## Aggregated Error Patterns
(MUST be a pipe-delimited markdown table:
| Pattern | Source | Classification | Significance |
|---------|--------|----------------|--------------|
| ... | ... | ... | ... |)
## Remediation
(MANDATORY — numbered, step-by-step resolution actions. Always include this section.
 Use this EXACT format for every item — no variations:

 ### 1. [SEVERITY] <Title>
 <Description of what to do and why.>

 **Steps:**
 ```bash
 # specific commands
 ```

 **Validation:**
 ```bash
 # commands to confirm the fix worked
 ```

 Use severity tags: [IMMEDIATE — CRITICAL], [HIGH], [MEDIUM].
 Number items sequentially starting from 1.
 Note whether each resolution supersedes, supplements, or is unchanged from the previous round.
 Reference specific OpenShift/Kubernetes components, namespaces, and file paths.
 Do NOT use plain numbered lists — MUST use ### sub-headings for each item.)

Do NOT rename headings. Do NOT use plain text for Aggregated Error Patterns.
Do NOT use ### sub-headings inside Primary Root Cause(s) — use numbered items with sub-bullets only.
"""
    # ── TEMPORARY: Save continuation RCA payload to Results/payload.txt ──
    try:
        from app_paths import get_results_dir
        _payload_path = get_results_dir() / "payload.txt"
        with open(_payload_path, "w", encoding="utf-8") as _pf:
            _pf.write("=" * 100 + "\n")
            _pf.write("RCA CONTINUATION PAYLOAD — Full prompt sent to LLM\n")
            _pf.write("=" * 100 + "\n\n")
            _pf.write(user_content)
            _pf.write("\n")
        print(f"[RCA continued] Payload saved to: {_payload_path}")
    except Exception as _save_err:
        print(f"[RCA continued] Warning: Could not save payload: {_save_err}")
    # ── END TEMPORARY ──

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_token}"}
    if is_custom_gateway:
        payload = {
            "anthropic_version": "vertex-2023-10-16",
            "messages": [{"role": "user", "content": [{"type": "text", "text": user_content}]}],
            "max_tokens": max_tokens,
            "temperature": 0,
        }
    else:
        model_id = claude_config.get("model_id")
        if not model_id:
            return err_result("Error: Missing required 'claude.model_id' in config.json.")
        payload = {"model": model_id, "max_tokens": max_tokens, "messages": [{"role": "user", "content": user_content}]}
    try:
        response = requests.post(api_endpoint, headers=headers, json=payload, verify=verify_ssl, timeout=600)
        response.raise_for_status()
        response_json = response.json()
        usage = response_json.get("usage") or {}
        input_tokens = usage.get("input_tokens") or usage.get("input_tokens_count")
        output_tokens = usage.get("output_tokens") or usage.get("output_tokens_count")
        response_text = None
        if "content" in response_json:
            content = response_json["content"]
            if isinstance(content, list) and content:
                text_parts = [item.get("text", "") for item in content if isinstance(item, dict) and "text" in item]
                response_text = "".join(text_parts) if text_parts else None
            elif isinstance(content, str):
                response_text = content
        if not response_text and "text" in response_json:
            response_text = response_json["text"]
        response_text = _normalize_rca_report_sections(response_text or json.dumps(response_json, indent=2))
        _mid = claude_config.get("model_id", "")
        if input_tokens is None:
            input_tokens = estimate_tokens(user_content, _mid)
        if output_tokens is None:
            output_tokens = estimate_tokens(response_text, _mid)
        return {"text": response_text, "input_tokens": input_tokens or 0, "output_tokens": output_tokens or 0}
    except requests.exceptions.RequestException as e:
        return err_result(f"Claude API error: {str(e)}")


def run_rca_and_summary_continued(
    ml_classification_result: Dict[str, List[Dict]],
    config_path: str = "config.json",
    problem_statement: Optional[str] = None,
    log_error_entries: Optional[List[Dict[str, Any]]] = None,
    previous_rca_summary: Optional[str] = None,
    previous_priority_stage: str = "high",
    new_priority_stage: str = "medium",
    user_feedback_text: str = "",
) -> Dict[str, Any]:
    """Run continuation RCA via file-based Map-Reduce."""
    from app_paths import get_results_dir

    payload_yaml = prepare_payload_for_llm(ml_classification_result)
    if not payload_yaml and not (log_error_entries and len(log_error_entries) > 0):
        return {
            "payload_sent": {}, "rca_summary": "", "chronology": [], "payload_bytes": 0,
            "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "error": "No new data.",
        }

    config = load_config(config_path)
    claude_config = config.get("claude", {})
    max_chunk_tokens = claude_config.get("max_chunk_tokens", 60000)
    model_id = claude_config.get("model_id")
    if not model_id:
        return {
            "payload_sent": {}, "rca_summary": "", "chronology": [], "payload_bytes": 0,
            "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "error": "Missing required 'claude.model_id' in config.json.",
        }

    chunks = chunk_payload(
        yaml_errors=payload_yaml, 
        log_entries=log_error_entries or [], 
        max_chunk_tokens=max_chunk_tokens
    )

    print(f"[RCA Continued] Payload split into {len(chunks)} chunk(s). Starting Map-Reduce.")

    total_input_tokens = 0
    total_output_tokens = 0
    total_payload_bytes = 0
    all_chronology: List[Dict[str, str]] = []
    
    chunk_files: List[Path] = []
    chunks_succeeded = 0
    error = None

    # Write the baseline (previous RCA) to disk so the reducer incorporates it
    if previous_rca_summary:
        baseline_path = get_results_dir() / "continued_baseline_rca.txt"
        with open(baseline_path, "w", encoding="utf-8") as f:
            f.write(f"=== PREVIOUS BASELINE RCA ===\n{previous_rca_summary}")
        chunk_files.append(baseline_path)

    # --- 1. MAP PHASE ---
    for i, chunk_data in enumerate(chunks):
        chunk_yaml = chunk_data["yaml_errors"]
        chunk_logs = chunk_data["log_entries"]

        chunk_llm_payload: Dict[str, Any] = {"yaml_errors": chunk_yaml}
        if chunk_logs:
            chunk_llm_payload["log_errors"] = chunk_logs
            
        chronology = build_chronology_from_payload(
            chunk_yaml if isinstance(chunk_yaml, dict) else {}, chunk_logs,
        )
        if chronology:
            chunk_llm_payload["chronology"] = chronology
            all_chronology.extend(chronology)

        payload_bytes = len(json.dumps(chunk_llm_payload, default=str).encode("utf-8"))
        total_payload_bytes += payload_bytes

        rca_response = call_claude_rca_continued(
            chunk_llm_payload,
            config_path=config_path,
            problem_statement=problem_statement,
            previous_rca_summary=previous_rca_summary, 
            previous_priority_stage=previous_priority_stage,
            new_priority_stage=new_priority_stage,
            user_feedback_text=user_feedback_text if i == 0 else "", 
        )

        rca_text = rca_response.get("text", "")
        total_input_tokens += rca_response.get("input_tokens", 0)
        total_output_tokens += rca_response.get("output_tokens", 0)

        if rca_text.startswith("Error:") or rca_text.startswith("Claude API error:"):
            error = rca_text
            print(f"[RCA Continued] WARNING: Chunk {i+1} failed: {rca_text[:200]}")
            continue

        chunks_succeeded += 1

        try:
            chunk_file_path = get_results_dir() / f"continued_map_chunk_{i+1}_rca.txt"
            with open(chunk_file_path, "w", encoding="utf-8") as f:
                f.write(f"--- Continued Chunk {i+1} Findings ---\n\n{rca_text}")
            chunk_files.append(chunk_file_path)
            print(f"[RCA Continued] Chunk {i+1}/{len(chunks)} saved to {chunk_file_path.name}")
        except Exception as e:
            pass

    # --- 2. HIERARCHICAL REDUCE PHASE ---
    if chunks_succeeded == 0:
        final_rca_text = error or "Analysis failed completely."
    else:
        print(f"[RCA Continued] Initiating hierarchical reduce on {len(chunk_files)} files...")
        final_rca_text, reduce_in, reduce_out = _hierarchical_reduce(
            chunk_files, config_path, problem_statement, max_chunk_tokens, model_id
        )
        total_input_tokens += reduce_in
        total_output_tokens += reduce_out

        if final_rca_text.startswith("Error:") or final_rca_text.startswith("Claude API error:"):
            error = final_rca_text
            final_rca_text = "Master synthesis failed. Review files in Results/."

    all_chronology.sort(key=lambda x: x.get("timestamp", ""))

    final_rca_text = _normalize_rca_report_sections(final_rca_text)

    price_in = float(claude_config.get("price_per_1m_input_tokens", 3.0))
    price_out = float(claude_config.get("price_per_1m_output_tokens", 15.0))
    cost_usd = ((total_input_tokens / 1_000_000 * price_in) + (total_output_tokens / 1_000_000 * price_out))

    return {
        "payload_sent": {}, "rca_summary": final_rca_text, "chronology": all_chronology,
        "payload_bytes": total_payload_bytes, "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens, "cost_usd": round(cost_usd, 6),
        "chunks_processed": len(chunks), "chunks_succeeded": chunks_succeeded, "error": error,
    }


def run_rca_and_summary(
    ml_classification_result: Dict[str, List[Dict]],
    config_path: str = "config.json",
    problem_statement: Optional[str] = None,
    log_error_entries: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Run initial RCA. Delegates to run_rca_chunked which handles any payload size."""
    return run_rca_chunked(
        ml_classification_result,
        config_path=config_path,
        problem_statement=problem_statement,
        log_error_entries=log_error_entries,
    )


def run_rca_chunked(
    ml_classification_result: Dict[str, List[Dict]],
    config_path: str = "config.json",
    problem_statement: Optional[str] = None,
    log_error_entries: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Process RCA payload via Map-Reduce chunking (Memory Optimized)."""
    from app_paths import get_results_dir
    
    payload_yaml = prepare_payload_for_llm(ml_classification_result)
    config = load_config(config_path)
    claude_config = config.get("claude", {})
    max_chunk_tokens = claude_config.get("max_chunk_tokens", 60000)
    model_id = claude_config.get("model_id")
    print("RCA Chunked using:", model_id)
    if not model_id:
        return {
            "payload_sent": {}, "rca_summary": "", "chronology": [], "payload_bytes": 0,
            "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "error": "Missing required 'claude.model_id' in config.json.",
        }
    
    chunks = chunk_payload(
        yaml_errors=payload_yaml, 
        log_entries=log_error_entries or [], 
        max_chunk_tokens=max_chunk_tokens
    )

    print(f"[RCA Chunked] Payload split into {len(chunks)} chunk(s). Starting Map-Reduce.")

    total_input_tokens = 0
    total_output_tokens = 0
    total_payload_bytes = 0
    all_chronology: List[Dict[str, str]] = []
    
    chunk_files: List[Path] = []  # Store FILE PATHS instead of full texts
    chunks_succeeded = 0
    error = None

    # --- 1. MAP PHASE: Process each chunk independently ---
    for i, chunk_data in enumerate(chunks):
        chunk_yaml = chunk_data["yaml_errors"]
        chunk_logs = chunk_data["log_entries"]

        chunk_llm_payload: Dict[str, Any] = {"yaml_errors": chunk_yaml}
        if chunk_logs:
            chunk_llm_payload["log_errors"] = chunk_logs
            
        chronology = build_chronology_from_payload(
            chunk_yaml if isinstance(chunk_yaml, dict) else {}, chunk_logs,
        )
        if chronology:
            chunk_llm_payload["chronology"] = chronology
            all_chronology.extend(chronology)

        payload_bytes = len(json.dumps(chunk_llm_payload, default=str).encode("utf-8"))
        total_payload_bytes += payload_bytes

        rca_response = call_claude_rca(
            chunk_llm_payload,
            config_path=config_path,
            problem_statement=problem_statement,
            prior_chunk_summary=None, 
        )

        rca_text = rca_response.get("text", "")
        total_input_tokens += rca_response.get("input_tokens", 0)
        total_output_tokens += rca_response.get("output_tokens", 0)

        if rca_text.startswith("Error:") or rca_text.startswith("Claude API error:"):
            error = rca_text
            print(f"[RCA Chunked] WARNING: Chunk {i+1} failed: {rca_text[:200]}")
            continue

        chunks_succeeded += 1
        
        # Write directly to disk to save memory
        try:
            chunk_file_path = get_results_dir() / f"map_chunk_{i+1}_rca.txt"
            with open(chunk_file_path, "w", encoding="utf-8") as f:
                f.write(f"--- Chunk {i+1} Findings ---\n\n{rca_text}")
            chunk_files.append(chunk_file_path)
            print(f"[RCA Chunked] Chunk {i+1}/{len(chunks)} complete. Saved to {chunk_file_path.name}")
        except Exception as e:
            print(f"[RCA Chunked] Warning: Could not save chunk to disk: {e}")

    # --- 2. HIERARCHICAL REDUCE PHASE ---
    if chunks_succeeded == 0:
        final_rca_text = error or "Analysis failed completely."
    elif chunks_succeeded == 1:
        print("[RCA Chunked] Only 1 chunk processed. Skipping reduce step.")
        with open(chunk_files[0], "r", encoding="utf-8") as f:
            final_rca_text = f.read().replace("--- Chunk 1 Findings ---\n\n", "")
    else:
        print(f"[RCA Chunked] Initiating hierarchical reduce on {chunks_succeeded} files...")
        final_rca_text, reduce_in, reduce_out = _hierarchical_reduce(
            chunk_files, config_path, problem_statement, max_chunk_tokens, model_id
        )
        total_input_tokens += reduce_in
        total_output_tokens += reduce_out

        if final_rca_text.startswith("Error:") or final_rca_text.startswith("Claude API error:"):
            error = final_rca_text
            final_rca_text = "Master synthesis failed. Review intermediate files in Results/."

    all_chronology.sort(key=lambda x: x.get("timestamp", ""))

    final_rca_text = _normalize_rca_report_sections(final_rca_text)

    price_in = float(claude_config.get("price_per_1m_input_tokens", 3.0))
    price_out = float(claude_config.get("price_per_1m_output_tokens", 15.0))
    cost_usd = ((total_input_tokens / 1_000_000 * price_in) + (total_output_tokens / 1_000_000 * price_out))

    return {
        "payload_sent": {}, "rca_summary": final_rca_text, "chronology": all_chronology,
        "payload_bytes": total_payload_bytes, "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens, "cost_usd": round(cost_usd, 6),
        "chunks_processed": len(chunks), "chunks_succeeded": chunks_succeeded, "error": error,
    }

def main():
    """
    Standalone executor to manually combine chunk summaries from the Results folder.
    Useful for debugging or recovering a failed Reduce phase.
    """
    import sys
    import re
    from pathlib import Path

    # --- Add the parent directory to Python's path ---
    # This allows us to import app_paths if this script is in a subfolder
    current_dir = Path(__file__).resolve().parent
    parent_dir = current_dir.parent
    if str(parent_dir) not in sys.path:
        sys.path.insert(0, str(parent_dir))

    try:
        from app_paths import get_results_dir, get_config_path
    except ImportError as e:
        print(f"Error: Could not import app_paths ({e}).")
        print("Make sure your directory structure is correct.")
        return

    results_dir = get_results_dir()
    config_path = str(get_config_path())

    # Find all chunk summary files (handles both initial and continued chunks)
    chunk_files = list(results_dir.glob("*chunk*_rca.txt"))

    if not chunk_files:
        print(f"No chunk files found in {results_dir}.")
        print("Please ensure your map step successfully saved files like 'map_chunk_1_rca.txt'.")
        return

    # Sort files numerically so they are combined in the correct chronological order
    def extract_chunk_num(filepath: Path) -> int:
        match = re.search(r'chunk_(\d+)', filepath.name)
        return int(match.group(1)) if match else 9999

    chunk_files.sort(key=extract_chunk_num)

    print("=" * 80)
    print(f"MANUAL REDUCE TRIGGERED: Combining {len(chunk_files)} chunk(s)")
    print("=" * 80)
    for f in chunk_files:
        print(f"  - {f.name}")

    # Load configuration
    config = load_config(config_path)
    claude_config = config.get("claude", {})
    max_chunk_tokens = claude_config.get("max_chunk_tokens", 80000)
    model_id = claude_config.get("model_id")
    if not model_id:
        print("[Reduce] Error: Missing required 'claude.model_id' in config.json.")
        return

    # Hardcoded for testing. In a real scenario, you might read this from agent_memory.json
    problem_statement = "Autosizing causes control plane node to reboot twice during upgrade"

    print(f"\n[Reduce] Initiating hierarchical reduce (Limit: {max_chunk_tokens:,} tokens)...")
    
    final_rca_text, total_in, total_out = _hierarchical_reduce(
        summary_files=chunk_files,
        config_path=config_path,
        problem_statement=problem_statement,
        max_chunk_tokens=max_chunk_tokens,
        model_id=model_id
    )

    if final_rca_text.startswith("Error:") or final_rca_text.startswith("Claude API error:"):
        print(f"\n[Reduce] Failed to combine summaries:\n{final_rca_text}")
        return

    # Save the final synthesized output
    master_out_path = results_dir / "MANUAL_MASTER_RCA.txt"
    try:
        with open(master_out_path, "w", encoding="utf-8") as f:
            f.write(final_rca_text)
        print(f"\n[Reduce] SUCCESS! Master RCA written to: {master_out_path}")
        print(f"[Reduce] Tokens used — Input: {total_in:,} | Output: {total_out:,}")
    except Exception as e:
        print(f"\n[Reduce] Warning: Master RCA generated, but failed to save to disk: {e}")

def generate_rca_pass_summary(
    rca_text: str,
    problem_statement: str = "",
    config_path: str = "config.json",
) -> str:
    """Call the LLM to produce a short, executive-style summary of a single
    RCA pass.  Returns the summary text, or an empty string on failure.

    The output is flowing prose (no headings, no bullet points) covering the
    user issue, key findings, chronology highlights and primary root cause.
    """
    if not REQUESTS_AVAILABLE or not rca_text.strip():
        return ""

    config = load_config(config_path)
    claude_config = config.get("claude", {})
    verify_ssl = claude_config.get("verify_ssl", True)
    is_custom_gateway = claude_config.get("custom_gateway", False)

    try:
        api_token = resolve_api_token(claude_config)
    except RuntimeError as e:
        print(f"[RCA Summary] Token error: {e}")
        return ""
    api_endpoint = resolve_api_endpoint(claude_config)
    if not api_token:
        return ""

    prompt = (
        "You are an expert technical writer summarizing a Root Cause Analysis.\n\n"
        "Below is a detailed RCA report from one analysis pass and the user's "
        "original problem statement.\n\n"
        "YOUR TASK: Produce a structured RCA report summary with EXACTLY two sections "
        "using the headings below. Use ## for each heading.\n\n"
        "## Issue\n"
        "Restate the user's reported issue / problem in 2-3 clear sentences.\n\n"
        "## Summary\n"
        "Write a concise executive summary paragraph (100-200 words) of the RCA findings "
        "covering the primary root cause, key chronology highlights, and contributing factors. "
        "No sub-headings or bullet points in this section — flowing prose only.\n\n"
        "RULES:\n"
        "- Use ONLY the two ## headings above — no other headings.\n"
        "- Do NOT include resolution or remediation steps.\n"
        "- Keep the entire output short and crisp.\n"
        "- Write for a manager or stakeholder who needs a quick overview.\n\n"
    )
    if problem_statement:
        prompt += f"USER PROBLEM STATEMENT:\n{problem_statement}\n\n"
    prompt += f"DETAILED RCA REPORT:\n{rca_text}"

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_token}"}

    if is_custom_gateway:
        body = {
            "anthropic_version": "vertex-2023-10-16",
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
            "max_tokens": 4096,
            "temperature": 0,
        }
    else:
        model_id = claude_config.get("model_id")
        if not model_id:
            return {"success": False, "message": "Missing required 'claude.model_id' in config.json."}
        body = {
            "model": model_id,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        }

    try:
        resp = requests.post(api_endpoint, headers=headers, json=body, verify=verify_ssl, timeout=120)
        resp.raise_for_status()
        resp_json = resp.json()

        summary_text = ""
        if is_custom_gateway:
            content = resp_json.get("content", [])
            if isinstance(content, list):
                summary_text = "".join(
                    item.get("text", "") for item in content if isinstance(item, dict)
                )
            elif isinstance(content, str):
                summary_text = content
            if not summary_text:
                summary_text = resp_json.get("text", "")
        else:
            content = resp_json.get("content", [])
            if content and isinstance(content, list):
                summary_text = content[0].get("text", "")

        return (summary_text or "").strip()
    except Exception as e:
        print(f"[RCA Summary] LLM call failed: {e}")
        return ""


_DAG_VALID_CATEGORIES = {"primary", "symptom", "secondary"}
_DAG_VALID_EDGE_KINDS = {"primary", "secondary"}


def _extract_json_object_from_llm_text(text: str) -> str:
    """Best-effort extraction of a JSON object from LLM output.

    Handles fenced blocks, bare JSON, and common prose prefixes such as
    \"Here is the DAG:\" before the object.
    """
    text = (text or "").strip()
    if not text:
        return text

    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fence_match:
        return fence_match.group(1).strip()

    try:
        json.loads(text)
        return text
    except (json.JSONDecodeError, TypeError):
        pass

    start = text.find("{")
    if start < 0:
        return text

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text


def _parse_causal_dag_response(raw_text: str) -> Dict[str, Any]:
    """Parse and validate the LLM's causal-DAG JSON response.

    Strips accidental ```json fences, then enforces the minimal contract the
    frontend relies on: non-empty node list with valid categories, and edges
    that only reference existing node ids. Invalid edges are dropped rather
    than failing the whole result. Returns {} if the response is unusable.
    """
    text = _extract_json_object_from_llm_text(raw_text)

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}

    if not isinstance(data, dict):
        return {}

    raw_nodes = data.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        return {}

    nodes = []
    node_ids = set()
    for n in raw_nodes:
        if not isinstance(n, dict):
            continue
        node_id = str(n.get("id", "")).strip()
        title = str(n.get("title", "")).strip()
        if not node_id or not title:
            continue
        category = str(n.get("category", "")).strip().lower()
        if category not in _DAG_VALID_CATEGORIES:
            category = "secondary"
        clean_node = {
            "id": node_id,
            "title": title,
            "date": str(n.get("date", "")).strip(),
            "short": str(n.get("short", "")).strip(),
            "category": category,
        }
        badge = str(n.get("badge", "")).strip()
        if badge:
            clean_node["badge"] = badge
        nodes.append(clean_node)
        node_ids.add(node_id)

    if not nodes:
        return {}

    edges = []
    for e in data.get("edges", []) if isinstance(data.get("edges"), list) else []:
        if not isinstance(e, dict):
            continue
        frm = str(e.get("from", "")).strip()
        to = str(e.get("to", "")).strip()
        if not frm or not to or frm not in node_ids or to not in node_ids:
            continue
        kind = str(e.get("kind", "")).strip().lower()
        if kind not in _DAG_VALID_EDGE_KINDS:
            kind = "secondary"
        edges.append({"from": frm, "to": to, "kind": kind})

    return {"nodes": nodes, "edges": edges}


def generate_rca_causal_dag(
    rca_text: str,
    problem_statement: str = "",
    config_path: str = "config.json",
) -> Dict[str, Any]:
    """Call the LLM to distill a finished RCA report into a structured causal
    DAG (nodes + directed edges) for diagram rendering in the UI.

    This is a separate, downstream LLM call from the RCA generation itself —
    it receives only the already-finished RCA report text (which already
    contains every timestamp and evidence quote needed), not the raw
    chronology array or yaml/log payload sent to the original RCA call.
    Re-sending that raw evidence would be redundant (already distilled into
    the report's own Primary/Secondary Causes and Chronology sections) and
    would risk requiring chunking again for large sessions.

    Returns a dict of shape {"nodes": [...], "edges": [...]}, or {} on
    failure/unavailability so callers can safely skip writing a DAG file.
    """
    if not REQUESTS_AVAILABLE:
        print("[RCA Causal DAG] Skipped: requests library unavailable")
        return {}
    if not rca_text.strip():
        print("[RCA Causal DAG] Skipped: empty RCA text")
        return {}

    config = load_config(config_path)
    claude_config = config.get("claude", {})
    verify_ssl = claude_config.get("verify_ssl", True)
    is_custom_gateway = claude_config.get("custom_gateway", False)

    try:
        api_token = resolve_api_token(claude_config)
    except RuntimeError as e:
        print(f"[RCA Causal DAG] Token error: {e}")
        return {}
    api_endpoint = resolve_api_endpoint(claude_config)
    if not api_token:
        print("[RCA Causal DAG] Skipped: no API token")
        return {}

    print(f"[RCA Causal DAG] Calling LLM ({len(rca_text):,} chars of RCA text)...")

    prompt = (
        "You are an expert SRE distilling a finished Root Cause Analysis report into a "
        "structured causal graph (a directed acyclic graph / DAG) for diagram rendering.\n\n"
        "Below is the detailed RCA report and the user's original problem statement.\n\n"
        "YOUR TASK: Read the report's \"Primary Root Cause(s)\", \"Secondary Causes / "
        "Contributing Factors\", \"User Reported Issue\", and \"Chronology of Events\" "
        "sections, and output a single JSON object (and NOTHING else — no prose, no "
        "markdown code fences) with this exact shape:\n\n"
        "{\n"
        "  \"nodes\": [\n"
        "    {\"id\": \"kebab-case-unique-id\", \"title\": \"short node title\", "
        "\"date\": \"timestamp or date range as stated in the report\", "
        "\"short\": \"one-sentence evidence summary\", "
        "\"category\": \"primary|symptom|secondary\", "
        "\"badge\": \"1\"}\n"
        "  ],\n"
        "  \"edges\": [\n"
        "    {\"from\": \"node-id\", \"to\": \"node-id\", \"kind\": \"primary|secondary\"}\n"
        "  ]\n"
        "}\n\n"
        "RULES:\n"
        "- Create one node per distinct causal claim or reported symptom in the report — "
        "do not invent facts not present in the report.\n"
        "- category: \"primary\" for nodes the report identifies as a primary/root cause, "
        "\"symptom\" for the user-reported issue/observable failure, \"secondary\" for "
        "contributing/compounding factors. Base this on how the report itself frames each "
        "item, not on any fixed incident-specific rule.\n"
        "- \"badge\" is OPTIONAL: only set it (to the number as a string) when the report "
        "explicitly numbers that item as \"Primary Root Cause #N\". Omit the field entirely "
        "otherwise.\n"
        "- Create a directed edge for every causal relationship the report states or clearly "
        "implies (e.g. \"X caused Y\", \"X compounded by Y\", \"X led to Y\"). kind=\"primary\" "
        "for the report's main causal chain, kind=\"secondary\" for contributing/compounding "
        "relationships.\n"
        "- Every edge's \"from\" and \"to\" MUST reference an \"id\" that exists in \"nodes\".\n"
        "- \"id\" values must be short, unique, kebab-case slugs (e.g. \"etcd-quorum-loss\").\n"
        "- Output ONLY the JSON object. No explanation before or after it.\n\n"
    )
    if problem_statement:
        prompt += f"USER PROBLEM STATEMENT:\n{problem_statement}\n\n"
    prompt += f"DETAILED RCA REPORT:\n{rca_text}"

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_token}"}

    if is_custom_gateway:
        body = {
            "anthropic_version": "vertex-2023-10-16",
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
            "max_tokens": 4096,
            "temperature": 0,
        }
    else:
        model_id = claude_config.get("model_id")
        if not model_id:
            return {}
        body = {
            "model": model_id,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        }

    try:
        resp = requests.post(api_endpoint, headers=headers, json=body, verify=verify_ssl, timeout=120)
        resp.raise_for_status()
        resp_json = resp.json()

        dag_text = ""
        if is_custom_gateway:
            content = resp_json.get("content", [])
            if isinstance(content, list):
                dag_text = "".join(
                    item.get("text", "") for item in content if isinstance(item, dict)
                )
            elif isinstance(content, str):
                dag_text = content
            if not dag_text:
                dag_text = resp_json.get("text", "")
        else:
            content = resp_json.get("content", [])
            if content and isinstance(content, list):
                dag_text = content[0].get("text", "")

        parsed = _parse_causal_dag_response(dag_text)
        if parsed.get("nodes"):
            print(
                f"[RCA Causal DAG] Parsed {len(parsed['nodes'])} nodes, "
                f"{len(parsed.get('edges', []))} edges"
            )
        else:
            preview = (dag_text or "").strip().replace("\n", " ")[:240]
            print(f"[RCA Causal DAG] No usable DAG in LLM response. Preview: {preview!r}")
        return parsed
    except Exception as e:
        print(f"[RCA Causal DAG] LLM call failed: {e}")
        return {}


if __name__ == "__main__":
    main()