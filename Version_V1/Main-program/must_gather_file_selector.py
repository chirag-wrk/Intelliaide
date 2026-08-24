"""
Must-Gather File Selector Module

This module analyzes user problem statements and suggests which files from a must-gather
collection need to be analyzed based on the must-gather structure documentation.

Uses Claude LLM to intelligently route problem statements to relevant files.
"""

import os
import json
import re
import time
from typing import Dict, List, Optional
from pathlib import Path


try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    print("Warning: requests package is required. Install with: pip install requests urllib3")

try:
    import ollama
    OLLAMA_AVAILABLE = True
except ImportError:
    OLLAMA_AVAILABLE = False
    ollama = None

# *************************************************************************************
# Single place to change the config file name: used for all LLM calls (model, api_key, etc.).
CONFIG_FILE = "config.json"
# *************************************************************************************


def load_config_by_name(config_file_name: Optional[str] = None) -> Dict:
    """
    Resolve config file by name, load it, and return the full config dict.
    Single function to change which config is used and load the model/API settings from it.
    :param config_file_name: Filename (e.g. "config.json") or None to use CONFIG_FILE. Can also be an absolute path.
    :return: Config dict. LLM settings are in the first top-level dict that has api_key or model_id (e.g. claude, claude-haiku, gemini).
    """
    if config_file_name is None:
        config_file_name = CONFIG_FILE
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    candidates = []
    if os.path.isabs(config_file_name) and os.path.isfile(config_file_name):
        candidates.append(config_file_name)
    else:
        try:
            from app_paths import get_config_path
            candidates.append(str(get_config_path(config_file_name)))
        except ImportError:
            pass
        candidates.append(os.path.join(project_root, "Config", os.path.basename(config_file_name)))
        candidates.append(os.path.join(script_dir, config_file_name))
    for path in candidates:
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, "rb") as f:
                raw = f.read()
            text = raw.decode("utf-8-sig").strip()
            if not text:
                continue
            return json.loads(text)
        except (json.JSONDecodeError, Exception):
            continue
    print(f"Warning: Could not load config from {config_file_name} (tried: {candidates}). File missing, empty, or invalid JSON.")
    return {}


def load_config(config_path: Optional[str] = None) -> Dict:
    """
    Load configuration. Delegates to load_config_by_name so config is resolved by filename and API details loaded from that file.
    """
    return load_config_by_name(config_path)


# Single source of truth: folder containing must-gather documentation for LLM file selection.
# Priority: config.json "must_gather_docs_dir" → app_paths.get_must_gather_docs_dir() → fallback DataSource/.
def _resolve_must_gather_docs_dir() -> str:
    """Resolve the must-gather docs directory from config.json, app_paths, or fallback."""
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    _project_root = os.path.dirname(_script_dir)  # project root directory

    # 1. Try config.json "must_gather_docs_dir"
    try:
        from app_paths import get_config_path
        cfg_path = str(get_config_path())
    except ImportError:
        cfg_path = os.path.join(_project_root, "Config", "config.json")
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            configured = cfg.get("must_gather_docs_dir", "")
            if configured:
                # Resolve relative paths against the project root
                if not os.path.isabs(configured):
                    configured = os.path.join(_project_root, configured)
                if os.path.isdir(configured):
                    return configured
        except Exception:
            pass

    # 2. Try app_paths
    try:
        from app_paths import get_must_gather_docs_dir as _get_docs_dir
        docs_dir = str(_get_docs_dir())
        if os.path.isdir(docs_dir):
            return docs_dir
    except ImportError:
        pass

    # 3. Fallback: DataSource is one level up from Main-program/
    return os.path.join(_project_root, "DataSource")

MUST_GATHER_DOCS_DIR_DEFAULT = _resolve_must_gather_docs_dir()


def load_must_gather_documentation(must_gather_docs_dir: str) -> Dict[str, str]:
    """
    Load all must-gather structure documentation files.
    """
    docs = {}
    doc_files = [
        'MUST_GATHER_STRUCTURE.md',
        'MUST_GATHER_INDEX.md',
        'MUST_GATHER_ROUTING_GUIDE.md',
        'MUST_GATHER_DOCUMENTATION_README.md',
    ]
    docs_path = Path(must_gather_docs_dir)
    if not docs_path.exists():
        raise FileNotFoundError(f"Must-gather documentation directory not found: {must_gather_docs_dir}")
    for doc_file in doc_files:
        doc_path = docs_path / doc_file
        if doc_path.exists():
            try:
                with open(doc_path, 'r', encoding='utf-8') as f:
                    docs[doc_file] = f.read()
            except Exception as e:
                print(f"Warning: Could not read {doc_file}: {e}")
        else:
            print(f"Warning: Documentation file not found: {doc_file}")
    return docs


class MustGatherFileSelector:
    """
    Selects relevant must-gather files based on user problem statements.
    Uses the LLM configured in the selected config file (by CONFIG_FILE or config_path).
    """

    def __init__(self, api_key: Optional[str] = None, api_url: Optional[str] = None,
                 model_id: Optional[str] = None, max_tokens: Optional[int] = None,
                 config_path: Optional[str] = None):
        if config_path is None:
            config_path = CONFIG_FILE
        config = load_config(config_path)
        llm_config = config.get("claude", {})
        if not isinstance(llm_config, dict):
            llm_config = {}
        self._llm_config = llm_config
        self._use_ollama = llm_config.get('provider') == 'ollama' or llm_config.get('use_ollama', False)
        if self._use_ollama:
            if not OLLAMA_AVAILABLE:
                raise ImportError("Ollama provider requires the ollama package. Install with: pip install ollama")
            self.model_id = model_id or llm_config.get('model_id', 'llama8B')
            self.ollama_model = llm_config.get('ollama_model', 'llama3.1')
            self.max_tokens = max_tokens if max_tokens is not None else llm_config.get('max_tokens', 4096)
            # Ollama options for factual, deterministic output (tuned for llama3.1 / must-gather file selection)
            opts = llm_config.get('ollama_options', {})
            self._ollama_options = {
                'temperature': opts.get('temperature', 0.0),
                'num_ctx': opts.get('num_ctx', 8192),
                'top_p': opts.get('top_p', 0.9),
                'repeat_penalty': opts.get('repeat_penalty', 1.2),
                'seed': opts.get('seed', 42),
            }
            self.api_key = self.api_url = None
            self.api_endpoint = None
            self._is_custom_gateway = self._is_openai_compatible = self._is_completions_api = False
            self.verify_ssl = True
            self._auth_header = None
            return
        if not REQUESTS_AVAILABLE:
            raise ImportError("requests package is required. Install with: pip install requests urllib3")
        self._api_key_override = api_key
        self.api_url = (api_url or llm_config.get('api_url') or '').strip().rstrip('/')
        self.model_id = model_id or llm_config.get('model_id')
        self.max_tokens = max_tokens if max_tokens is not None else llm_config.get('max_tokens')
        if not self.api_url:
            raise ValueError("'api_url' must be set in the config file or passed as argument.")
        if not self.model_id:
            raise ValueError("'model_id' must be set in the config file or passed as argument.")
        if self.max_tokens is None:
            raise ValueError("'max_tokens' must be set in the config file or passed as argument.")
        endpoint_pattern = llm_config.get('endpoint_pattern')
        api_ver = llm_config.get('api_version', '')
        is_openai_compatible = False
        if endpoint_pattern:
            from llm_rca_agent import resolve_api_endpoint
            self.api_endpoint = resolve_api_endpoint(llm_config)
            is_custom_gateway = True
        else:
            path_template = llm_config.get('path_template')
            if path_template:
                self.api_endpoint = path_template.format(api_url=self.api_url, model_id=self.model_id)
                is_custom_gateway = True
            else:
                if api_ver == 'v1beta':
                    self.api_endpoint = f"{self.api_url}/v1beta/openai/chat/completions"
                    is_custom_gateway = True
                    is_openai_compatible = True
                else:
                    self.api_endpoint = f"{self.api_url}/v1/messages"
                    is_custom_gateway = False
        if 'custom_gateway' in llm_config:
            is_custom_gateway = bool(llm_config['custom_gateway'])
        self._is_custom_gateway = is_custom_gateway
        self._is_openai_compatible = is_openai_compatible
        self._is_completions_api = '/v1/completions' in self.api_endpoint
        # Optional: use a different auth header (e.g. "user-key" or "api-key") instead of "Authorization: Bearer"
        self._auth_header = llm_config.get('auth_header', '').strip() or None
        verify_ssl_env = os.getenv("VERIFY_SSL", "").lower()
        if verify_ssl_env == "true":
            self.verify_ssl = True
        elif verify_ssl_env == "false":
            self.verify_ssl = False
        else:
            self.verify_ssl = llm_config.get('verify_ssl', True)
        # Request timeout (seconds); long prompts may need 300+ (default 300)
        self._request_timeout = llm_config.get('request_timeout', 300)
        # Temperature: 0–0.2 for factual/deterministic output (recommended for must-gather file selection)
        self._temperature = llm_config.get('temperature', 0)

    def _call_llm_api(self, prompt: str):
        print(f"Calling LLM (model: {self.model_id})", flush=True)
        if self._use_ollama:
            # Ensure enough output tokens for long file lists (small models may stop early otherwise)
            num_predict = self.max_tokens if self.max_tokens else 8192
            options = {**self._ollama_options, 'num_predict': num_predict}
            response = ollama.chat(
                model=self.ollama_model,
                messages=[{"role": "user", "content": prompt}],
                options=options,
            )
            msg = response.get("message") or {}
            text = msg.get("content") or ""
            eval_count = response.get("eval_count") or 0
            return (text.strip(), {"input_tokens": 0, "output_tokens": eval_count})
        try:
            from llm_rca_agent import resolve_api_token
            if self._api_key_override:
                api_token = self._api_key_override
            else:
                api_token = resolve_api_token(self._llm_config)
        except RuntimeError as e:
            raise RuntimeError(
                f"Failed to resolve LLM API token: {e}. "
                "Check auth_type, service account, or api_key in config.json."
            ) from e
        if self._auth_header:
            headers = {"Content-Type": "application/json", self._auth_header: api_token}
        else:
            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_token}"}
        api_version = self._llm_config.get('anthropic_version', self._llm_config.get('api_version', 'vertex-2023-10-16'))
        if self._is_completions_api:
            payload = {
                "model": self.model_id,
                "prompt": prompt,
                "max_tokens": self.max_tokens,
                "temperature": self._temperature,
            }
        elif self._is_openai_compatible:
            payload = {
                "model": self.model_id,
                "max_tokens": self.max_tokens,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": self._temperature,
            }
        elif self._is_custom_gateway:
            payload = {
                "anthropic_version": api_version,
                "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
                "max_tokens": self.max_tokens,
                "temperature": self._temperature,
            }
        else:
            payload = {
                "model": self.model_id,
                "max_tokens": self.max_tokens,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": self._temperature,
            }
        max_tries = 5
        last_response = None
        try:
            for attempt in range(max_tries):
                try:
                    response = requests.post(
                        self.api_endpoint, headers=headers, json=payload,
                        verify=self.verify_ssl, timeout=self._request_timeout
                    )
                except (requests.exceptions.Timeout, requests.exceptions.ReadTimeout) as e:
                    if attempt < max_tries - 1:
                        wait_sec = 2 ** attempt
                        print(f"  Read timeout, retry in {wait_sec}s (attempt {attempt + 1}/{max_tries})...", flush=True)
                        time.sleep(wait_sec)
                        continue
                    raise RuntimeError(f"LLM API read timeout after {max_tries} attempts: {e}") from e
                last_response = response
                if response.status_code == 429:
                    if attempt < max_tries - 1:
                        wait_sec = 2 ** attempt
                        time.sleep(wait_sec)
                        continue
                response.raise_for_status()
                response_json = response.json()
                usage = response_json.get("usage") or {}
                input_tokens = usage.get("input_tokens") or usage.get("input_tokens_count")
                output_tokens = usage.get("output_tokens") or usage.get("output_tokens_count")
                if self._is_completions_api:
                    try:
                        choice = (response_json.get('choices') or [{}])[0]
                        response_text = choice.get('text', '') or ''
                        if not response_text:
                            response_text = choice.get('message', {}).get('content', '') or ''
                    except (IndexError, KeyError, TypeError):
                        response_text = ''
                    if not response_text:
                        response_text = json.dumps(response_json, indent=2)
                    usage = response_json.get('usage') or {}
                    input_tokens = usage.get('prompt_tokens') or usage.get('input_tokens') or input_tokens
                    output_tokens = usage.get('completion_tokens') or usage.get('output_tokens') or output_tokens
                    return (response_text, {"input_tokens": input_tokens or self.estimate_token_count(prompt), "output_tokens": output_tokens or self.estimate_token_count(response_text)})
                if self._is_openai_compatible:
                    try:
                        response_text = (response_json.get('choices') or [{}])[0].get('message', {}).get('content', '') or ''
                    except (IndexError, KeyError, TypeError):
                        response_text = ''
                    if not response_text:
                        response_text = json.dumps(response_json, indent=2)
                    usage = response_json.get('usage') or {}
                    input_tokens = usage.get('prompt_tokens') or usage.get('input_tokens') or input_tokens
                    output_tokens = usage.get('completion_tokens') or usage.get('output_tokens') or output_tokens
                    return (response_text, {"input_tokens": input_tokens or self.estimate_token_count(prompt), "output_tokens": output_tokens or self.estimate_token_count(response_text)})
                if self._is_custom_gateway:
                    response_text = None
                    if 'content' in response_json:
                        if isinstance(response_json['content'], list) and len(response_json['content']) > 0:
                            text_parts = [item.get('text', '') for item in response_json['content'] if isinstance(item, dict) and 'text' in item]
                            if text_parts:
                                response_text = ''.join(text_parts)
                        elif isinstance(response_json['content'], str):
                            response_text = response_json['content']
                    if not response_text and 'text' in response_json:
                        response_text = response_json['text']
                    if not response_text and 'predictions' in response_json:
                        pred = response_json['predictions']
                        if isinstance(pred, list) and len(pred) > 0 and 'content' in pred[0]:
                            c = pred[0]['content']
                            response_text = ''.join([x.get('text', '') for x in c if isinstance(x, dict)]) if isinstance(c, list) else str(c)
                    if not response_text and response_json.get('choices'):
                        try:
                            response_text = (response_json.get('choices') or [{}])[0].get('message', {}).get('content', '') or ''
                        except (IndexError, KeyError, TypeError):
                            pass
                    if not response_text:
                        response_text = json.dumps(response_json, indent=2)
                    return (response_text, {"input_tokens": input_tokens or self.estimate_token_count(prompt), "output_tokens": output_tokens or self.estimate_token_count(response_text)})
                response_text = response_json['content'][0].get('text', '') if response_json.get('content') else json.dumps(response_json, indent=2)
                return (response_text, {"input_tokens": input_tokens or self.estimate_token_count(prompt), "output_tokens": output_tokens or self.estimate_token_count(response_text)})
            if last_response is not None:
                last_response.raise_for_status()
        except requests.exceptions.RequestException as e:
            error_msg = f"LLM API connection error: {e}"
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_msg += f"\nResponse: {json.dumps(e.response.json(), indent=2)}"
                except Exception:
                    error_msg += f"\nResponse status: {e.response.status_code}\nResponse text: {e.response.text[:500]}"
            raise RuntimeError(error_msg)

    def estimate_token_count(self, text: str) -> int:
        try:
            import tiktoken
            return len(tiktoken.get_encoding("cl100k_base").encode(text))
        except ImportError:
            return len(text) // 4

    def get_prompt_token_count(self, problem_statement: str, must_gather_docs_dir: str) -> Dict:
        try:
            docs = load_must_gather_documentation(must_gather_docs_dir)
        except FileNotFoundError:
            return {'error': f'No documentation found in {must_gather_docs_dir}', 'total_tokens': 0}
        if not docs:
            return {'error': f'No documentation found in {must_gather_docs_dir}', 'total_tokens': 0}
        total_docs_tokens = sum(self.estimate_token_count(c) for c in docs.values())
        problem_tokens = self.estimate_token_count(problem_statement.strip())
        prompt = self._create_file_selection_prompt(problem_statement.strip(), docs)
        total_prompt_tokens = self.estimate_token_count(prompt)
        return {
            'total_documentation_tokens': total_docs_tokens,
            'problem_statement_tokens': problem_tokens,
            'total_prompt_tokens': total_prompt_tokens,
            'max_tokens': self.max_tokens,
            'available_for_response': 200000 - total_prompt_tokens,
        }

    def suggest_files(self, problem_statement: str, must_gather_docs_dir: str) -> Dict:
        try:
            docs = load_must_gather_documentation(must_gather_docs_dir)
        except FileNotFoundError:
            docs = {}
        if not docs:
            default_yaml_paths = [
                "/quay*/cluster-scoped-resources/config.openshift.io/clusteroperators.yaml",
                "/quay*/cluster-scoped-resources/config.openshift.io/clusterversions.yaml",
                "/quay*/namespaces/openshift-cluster-version/core/events.yaml",
            ]
            return {
                'suggested_files': [{'path': p, 'priority': 'high', 'reason': 'Default list (no docs)'} for p in default_yaml_paths],
                'reasoning': f"No must-gather documentation found in {must_gather_docs_dir}. Using default YAML file list.",
                'problem_category': 'Unknown',
                'priority': {},
                'input_tokens': 0,
                'output_tokens': 0,
            }
        problem_statement = problem_statement.strip()
        print(f"\n[DEBUG] Problem statement being analyzed:\n{problem_statement}\n")
        prompt = self._create_file_selection_prompt(problem_statement, docs)
        try:
            response_text, usage = self._call_llm_api(prompt)
        except Exception as e:
            return {'error': str(e), 'suggested_files': [], 'reasoning': '', 'problem_category': 'Unknown', 'priority': {}, 'input_tokens': 0, 'output_tokens': 0}
        result = self._parse_llm_response(response_text)
        result['input_tokens'] = usage.get('input_tokens', 0)
        result['output_tokens'] = usage.get('output_tokens', 0)
        self._print_llm_file_selection_output(result, response_text, usage)
        return result

    def _print_llm_file_selection_output(self, result: Dict, raw_response_text: Optional[str] = None, usage: Optional[Dict] = None) -> None:
        print("\n" + "=" * 100)
        print("LLM FILE SELECTION — USER QUERY ANALYSIS (full output)")
        print("=" * 100)
        if raw_response_text:
            print("\n--- Raw LLM response ---\n" + raw_response_text + "\n--- End raw response ---\n")
        print("Problem category:", result.get('problem_category', 'Unknown'))
        if result.get('keywords_identified'):
            print("Keywords identified:", ", ".join(result['keywords_identified']))
        if result.get('affected_components'):
            print("Affected components:", ", ".join(result['affected_components']))
        for i, f in enumerate(result.get('suggested_files', []), 1):
            print(f"  {i}. [{f.get('priority', '')}] {f.get('path', '')}")
            if f.get('reason'):
                print(f"      Reason: {f['reason']}")
        if result.get('reasoning'):
            print("\nReasoning:\n" + "-" * 100 + "\n" + result['reasoning'])
        if result.get('additional_notes'):
            print("\nAdditional notes:\n" + "-" * 100 + "\n" + result['additional_notes'])
        inp = result.get('input_tokens') or (usage or {}).get('input_tokens', 0)
        out = result.get('output_tokens') or (usage or {}).get('output_tokens', 0)
        print(f"\nToken usage: input={inp}, output={out}")
        print("=" * 100 + "\n")

    def _create_file_selection_prompt(self, problem_statement: str, docs: Dict[str, str]) -> str:
        docs_context = ""
        for doc_name, doc_content in docs.items():
            docs_context += f"\n\n=== {doc_name} ===\n{doc_content}\n"
        return f"""You are an expert OpenShift/Kubernetes system analyst. Your task is to analyze a user's problem statement and suggest which files from a must-gather collection need to be analyzed.

MUST-GATHER DOCUMENTATION:
{docs_context}

USER PROBLEM STATEMENT:
{problem_statement} the user issue

TASK:

PRE-PROCESSING RULE:
Before analyzing the USER PROBLEM STATEMENT, perform a noise reduction step. 
If the statement contains words, symbols, or phrases unrelated to OpenShift, 
Kubernetes clusters, must-gather diagnostics, or technical infrastructure 
issues, discard the "noise" and proceed using only the refined, relevant 
technical context.

ANALYSIS REQUIREMENTS:

1. Analyze the user's problem statement and identify:
   - Problem category (e.g., API Server, Networking, Storage, Pod Issues, etc.)
   - Key keywords and symptoms
   - Affected components or namespaces (if mentioned)

2. Based on the must-gather structure documentation provided, suggest specific file paths or file patterns that should be analyzed. Consider:
   - Primary directories/files most relevant to the problem
   - Related directories/files that might provide context
   - Log files, configuration files, status files, or metrics files as appropriate

EXHAUSTIVE RULE APPLICATION (CRITICAL — apply before rules 3-30 below):

Rules 3-30 are NOT alternative explanations to choose between. They are independent
triggers. For EACH rule, independently check whether any of its keywords, symptoms, or
conditions appear anywhere in the USER PROBLEM STATEMENT — including symptoms that are
secondary, background, or not part of the narrative you infer as the root cause.

If a rule's trigger condition is met, you MUST apply that rule and include its MANDATORY
files, even if:
  - A different rule also matches and better explains what you believe is the primary
    root cause
  - The matching component or symptom is only mentioned briefly
  - You have already built a file list for your leading hypothesis

Multiple rules commonly co-fire in real incidents. For example: a statement mentioning
RHOSO/Nova-down AND NotReady masters must apply rule 27 (RHOSO) AND rule 6 (external DNS)
AND rule 16 (machine config) together — not only the rule matching your leading
hypothesis. A statement mentioning a webhook timeout AND API server errors must apply
rule 23 (webhooks) AND rule 4 (API server) together.

Before producing your final numbered list, re-scan the full USER PROBLEM STATEMENT against
every rule 3-30 one more time and add any MANDATORY files for rules you matched but did not
yet include, purely because another rule's story felt dominant.

OUTAGE PRIORITY TIERING (when symptoms include API down/unreachable, NotReady masters,
widespread Degraded operators, Nova/OpenStack/RHOSO down, or control-plane outage):

  [high] L0 infra — all that apply; never [medium] or [low]:
    cluster-scoped-resources/config.openshift.io/infrastructures.yaml
    cluster-scoped-resources/config.openshift.io/dnses.yaml
    pod_network_connectivity_check/podnetworkconnectivitychecks.yaml
    host_service_logs/masters/NetworkManager_service.log
    host_service_logs/masters/kubelet_service.log
    namespaces/openshift-dns/pods/*/*/*/logs/current.log and previous.log
    namespaces/openshift-dns/core/events.yaml
    (+ cluster-scoped-resources/config.openshift.io/proxies.yaml and images.yaml when rule 8 matches)

  [high] minimal L1 control-plane — cap at ~6 paths (symptom check only, not full log dumps):
    cluster-scoped-resources/core/nodes/*.yaml
    etcd_info/endpoint_health.json
    etcd_info/member_list.json
    etcd_info/alarm_list.json
    namespaces/openshift-kube-apiserver/core/events.yaml

  [medium] operators, cascade components, RHOSO/OpenStack workloads:
    cluster-scoped-resources/config.openshift.io/clusteroperators.yaml
    namespaces/openstack/ (events, pod logs, deployments)
    namespaces/openshift-etcd/, openshift-kube-apiserver/ pod logs
    machine-config, machine-api, MCO paths when NotReady/machine symptoms match

  [low] unless CNI/dataplane is clearly the primary cause:
    network_logs/ (OVN leader status, ovnk_database_store, cluster_scale)
    host_service_logs/*/crio_service.log, openvswitch*, ovs*
    ostree, monitoring/prometheus/alerting paths

  clusteroperators.yaml does NOT replace the L0 trio above (infrastructures.yaml, dnses.yaml,
  podnetworkconnectivitychecks.yaml — all three mandatory at [high] for outage symptoms).

MANDATORY INCLUSION RULES:

3. For EVERY component, service, or node mentioned in the problem (e.g. Kubelet, kubelet, node, container-runtime, CRI-O, networking, storage, a specific operator or namespace), you MUST include ALL of the following if they exist in the must-gather structure:
   - Pod logs (container-aware path): namespaces/<namespace>/pods/<pod-name>/<container-name>/<container-name>/logs/current.log and previous.log
     (shorthand namespaces/<namespace>/pods/*/logs/current.log is acceptable when the exact pod/container name is unknown, but prefer the nested container path when known)
   - Namespace events: namespaces/<namespace>/core/events.yaml
   - Pod definitions: namespaces/<namespace>/core/pods.yaml
   - Journal logs: host_service_logs/masters/<service>_service.log and host_service_logs/workers/<service>_service.log
   - Service logs from relevant pods
   - Namespace configmaps when operator config is relevant: namespaces/<namespace>/core/configmaps.yaml

4. For API server issues, ALWAYS include:
   - Infrastructure endpoints (API hostnames that must resolve externally): cluster-scoped-resources/config.openshift.io/infrastructures.yaml
   - DNS operator config (cluster domain + upstream DNS): cluster-scoped-resources/config.openshift.io/dnses.yaml
   - Network connectivity checks: pod_network_connectivity_check/podnetworkconnectivitychecks.yaml
   - API Priority and Fairness files: namespaces/openshift-kube-apiserver/pods/*/kube-apiserver/kube-apiserver/api_priority_and_fairness/priority_levels, queues, requests
   - Static pod startup/termination logs: static-pods/kube-apiserver/*-startup*.log.gz, static-pods/kube-apiserver/*-termination.log.gz
   - Audit logs from ALL API servers: audit_logs/kube-apiserver/, audit_logs/openshift-apiserver/, audit_logs/oauth-apiserver/
   - kube-apiserver namespace events: namespaces/openshift-kube-apiserver/core/events.yaml
   - kube-apiserver operator: namespaces/openshift-kube-apiserver-operator/pods/*/logs/current.log and previous.log, core/events.yaml
   - OpenShift API server: namespaces/openshift-apiserver/pods/*/logs/current.log and previous.log, core/events.yaml
   - OpenShift API server operator: namespaces/openshift-apiserver-operator/pods/*/logs/current.log and previous.log, core/events.yaml

5. For etcd issues, ALWAYS include:
   - ALL etcd_info files: endpoint_health.json, endpoint_status.json, member_list.json, alarm_list.json, object_count.json
   - etcd pod logs and events: namespaces/openshift-etcd/pods/*/logs/current.log and previous.log, namespaces/openshift-etcd/core/events.yaml
   - etcd audit logs: audit_logs/etcd/
   - etcd operator logs and events: namespaces/openshift-etcd-operator/pods/*/logs/current.log and previous.log, namespaces/openshift-etcd-operator/core/events.yaml
   - Also include infrastructures.yaml and dnses.yaml (peer/API reachability often fails before etcd itself)

6. For Node NotReady, kubelet lease failures, API unreachable/timeouts, control-plane outage, "no such host", NXDOMAIN, lookup/dial failures to api or api-int, ALWAYS include as [high] BEFORE OVN/CRI-O:
   - cluster-scoped-resources/config.openshift.io/infrastructures.yaml — apiServerURL / apiServerInternalURI that MUST exist in external DNS
   - cluster-scoped-resources/config.openshift.io/dnses.yaml — cluster domain + upstream/external DNS servers
   - cluster-scoped-resources/config.openshift.io/networks.yaml — network config (SDN/OVN type, CIDR)
   - pod_network_connectivity_check/podnetworkconnectivitychecks.yaml — failed checks to API/DNS endpoints
   - host_service_logs/masters/kubelet_service.log — lookup / dial / lease renewal failures
   - host_service_logs/workers/kubelet_service.log — same on worker nodes if affected
   - host_service_logs/masters/NetworkManager_service.log — resolver / DNS client signals
   - namespaces/openshift-dns/pods/*/*/*/logs/current.log and previous.log
   - namespaces/openshift-dns/core/events.yaml
   - cluster-scoped-resources/core/nodes/*.yaml — node conditions (Ready, MemoryPressure, DiskPressure, PIDPressure)
   Distinguish INTERNAL DNS (CoreDNS / openshift-dns) from EXTERNAL/INFRASTRUCTURE DNS (records outside the cluster for api.<domain>, api-int.<domain>, *.apps.<domain>). If lookup failures target api/api-int/apps hostnames, treat EXTERNAL DNS as a primary hypothesis; mark OVN/CRI-O/etcd as downstream until DNS is ruled out.

7. For networking issues, ALWAYS include FIRST:
   - Network connectivity checks: pod_network_connectivity_check/podnetworkconnectivitychecks.yaml
   - DNS operator config: cluster-scoped-resources/config.openshift.io/dnses.yaml
   - Infrastructure endpoints: cluster-scoped-resources/config.openshift.io/infrastructures.yaml
   - CoreDNS logs/events: namespaces/openshift-dns/pods/*/*/*/logs/current.log, previous.log, namespaces/openshift-dns/core/events.yaml
   - Network config: cluster-scoped-resources/config.openshift.io/networks.yaml
   - Network operator: namespaces/openshift-network-operator/pods/*/logs/current.log and previous.log, namespaces/openshift-network-operator/core/events.yaml
   THEN, only if internal CNI/dataplane is implicated (and external DNS/API resolution is not the clearer explanation), also include:
   - OVN status files: network_logs/leader_ovnnb_status, network_logs/leader_ovnsb_status, network_logs/ovn_kubernetes_top_pods
   - Network scale metrics: network_logs/cluster_scale
   - OVN database archive: network_logs/ovnk_database_store.tar.gz (for OVN state analysis when corruption/leader issues suspected)
   - OVN pod logs/events: namespaces/openshift-ovn-kubernetes/pods/*/logs/current.log, previous.log, namespaces/openshift-ovn-kubernetes/core/events.yaml
   - Container runtime logs: host_service_logs/masters/crio_service.log, host_service_logs/workers/crio_service.log
   - OVS service logs: host_service_logs/masters/openvswitch_service.log, ovs-configuration_service.log, ovs-vswitchd_service.log, ovsdb-server_service.log (and workers/ equivalents)
   For secondary/optional network CRs (conditionally collected, include when present and relevant):
   - NMState: cluster-scoped-resources/nmstate.io/nodenetworkstates/, nodenetworkconfigurationpolicies/ (if NMState enabled)
   - Multus: cluster-scoped-resources/k8s.cni.cncf.io/net-attach-def/, ippools/, multi-networkpolicy/ (if Multus enabled)
   - OVN-K CRs: cluster-scoped-resources/k8s.ovn.org/egressips/, clusteruserdefinednetworks/ (if OVN enabled)
   - OpenShift SDN: cluster-scoped-resources/network.openshift.io/hostsubnets/ (if SDN mode)
   Do NOT list OVN/CRI-O as the only [high] paths when API/DNS resolution symptoms are present.

8. For x509 / certificate signed by unknown authority / ImagePullBackOff / external registry / corporate proxy symptoms, ALWAYS include as [high]:
   - cluster-scoped-resources/config.openshift.io/proxies.yaml
   - cluster-scoped-resources/config.openshift.io/images.yaml
   - Trusted CA bundles: namespaces/openshift-config/core/configmaps.yaml (contains user-ca-bundle and trusted-ca-bundle injected by the proxy operator)
   - host_service_logs/masters/kubelet_service.log and host_service_logs/masters/crio_service.log
   - namespaces/openshift-image-registry/pods/*/logs/current.log, previous.log, and core/events.yaml (when registry-related)

9. For authentication / login / OAuth / IdP / 401 / forbidden-login issues, ALWAYS include:
   - cluster-scoped-resources/config.openshift.io/authentications/ (or authentications.yaml)
   - cluster-scoped-resources/config.openshift.io/oauths.yaml (if present)
   - cluster-scoped-resources/config.openshift.io/consoles.yaml (console OAuth redirect URL)
   - namespaces/openshift-authentication/pods/*/logs/current.log and previous.log
   - namespaces/openshift-authentication/core/events.yaml
   - namespaces/openshift-authentication-operator/pods/*/logs/current.log and previous.log, core/events.yaml
   - namespaces/openshift-oauth-apiserver/pods/*/logs/current.log and previous.log
   - audit_logs/oauth-apiserver/, audit_logs/oauth-server/
   - proxies.yaml when external IdP connectivity may be involved

10. For ImagePullBackOff / registry unavailable / image pull failures, ALWAYS include:
    - cluster-scoped-resources/config.openshift.io/images.yaml
    - cluster-scoped-resources/config.openshift.io/proxies.yaml
    - Image content policies / digest mirror sets (for disconnected/mirrored registries): cluster-scoped-resources/config.openshift.io/imagecontentpolicies/, imagedigestmirrorsets/, imagetagmirrorsets/ (if present)
    - namespaces/openshift-image-registry/pods/*/logs/current.log and previous.log
    - namespaces/openshift-image-registry/core/events.yaml
    - namespaces/openshift-image-registry/apps/deployments.yaml (registry operator/deployment status)
    - Affected namespace events: namespaces/<namespace>/core/events.yaml
    - host_service_logs/masters/crio_service.log and/or workers/crio_service.log

11. For security / RBAC / SCC / CSR / certificate / forbidden (authorization) issues, ALWAYS include:
    - cluster-scoped-resources/rbac.authorization.k8s.io/clusterroles.yaml
    - cluster-scoped-resources/rbac.authorization.k8s.io/clusterrolebindings.yaml
    - namespaces/<namespace>/rbac.authorization.k8s.io/roles.yaml and rolebindings.yaml (when namespace known)
    - cluster-scoped-resources/security.openshift.io/securitycontextconstraints.yaml
    - cluster-scoped-resources/core/certificatesigningrequests.yaml
    - namespaces/openshift-service-ca/pods/*/logs/current.log (when cert/service-CA related)
    - audit_logs/kube-apiserver/ when authorization denials need an audit trail

12. For Machine API / machine not provisioned / node not joining / MachineHealthCheck issues, ALWAYS include:
    - namespaces/openshift-machine-api/core/events.yaml
    - namespaces/openshift-machine-api/pods/*/logs/current.log and previous.log
    - namespaces/openshift-machine-api/apps/deployments.yaml (operator deployment status)
    - Machine objects: cluster-scoped-resources/machine.openshift.io/machines/ (individual Machine CRs with providerStatus/conditions)
    - MachineSets: cluster-scoped-resources/machine.openshift.io/machinesets/ (desired vs current replica counts)
    - MachineHealthChecks: cluster-scoped-resources/machine.openshift.io/machinehealthchecks/ (if MHC-related)
    - cluster-scoped-resources/core/nodes/*.yaml
    - host_service_logs/masters/kubelet_service.log and/or workers/kubelet_service.log
    - cluster-scoped-resources/config.openshift.io/infrastructures.yaml

13. For storage issues (also covers "Storage Version Migration" category), ALWAYS include:
    - PersistentVolumeClaims: namespaces/<namespace>/core/persistentvolumeclaims.yaml (affected namespace)
    - PersistentVolumes: cluster-scoped-resources/core/persistentvolumes/ (individual PV definitions and status)
    - Storage classes: cluster-scoped-resources/storage.k8s.io/storageclasses.yaml
    - Volume attachments and CSI: cluster-scoped-resources/storage.k8s.io/volumeattachments/, csidrivers.yaml, csinodes.yaml
    - CSI driver pod logs: namespaces/openshift-cluster-csi-drivers/pods/*/logs/current.log and previous.log (provisioner/attacher issues)
    - Volume snapshots (conditional): cluster-scoped-resources/snapshot.storage.k8s.io/volumesnapshotclasses/, volumesnapshotcontents/ (if snapshot controller enabled)
    - Affected namespace events: namespaces/<namespace>/core/events.yaml (FailedMount, FailedAttach, ProvisioningFailed events)
    - Storage migration resources: cluster-scoped-resources/migration.k8s.io/storageversionmigrations.yaml
    - Storage version migrator: namespaces/openshift-kube-storage-version-migrator/ (full namespace including pods/*/logs/, core/events.yaml, apps/deployments.yaml, batch/jobs.yaml, core/configmaps.yaml)
    - API services: cluster-scoped-resources/apiregistration.k8s.io/apiservices.yaml
    - Custom resource definitions: cluster-scoped-resources/apiextensions.k8s.io/customresourcedefinitions.yaml
    - For FailedMount: also nodes/*/dmesg or <node>_logs_kubelet.gz and kubelet_service.log

14. For IPsec/network security issues, ALWAYS include:
    - ALL IPsec subdirectories: network_logs/ipsec/status/, network_logs/ipsec/trafficstatus/, network_logs/ipsec/xfrm/
    - IPsec configuration files: network_logs/ipsec/<pod-name>_ipsec.conf, network_logs/ipsec/<pod-name>_ipsec.d/, network_logs/ipsec/<pod-name>_libreswan.log
    - NetworkManager logs: host_service_logs/masters/NetworkManager_service.log, host_service_logs/workers/NetworkManager_service.log

15. For monitoring/performance issues, ALWAYS include:
    - Prometheus configuration: monitoring/prometheus/status/config.json, monitoring/prometheus/status/flags.json
    - Alerting and recording rules: monitoring/prometheus/rules.json
    - Active targets: monitoring/prometheus/*/active-targets.json
    - TSDB status: monitoring/prometheus/*/status/tsdb.json
    - Runtime info: monitoring/prometheus/*/status/runtimeinfo.json
    - Alertmanager data: monitoring/alertmanager/status.json, monitoring/prometheus/alertmanagers.json
    - Metrics dump (for performance deep-dives): monitoring/metrics/metrics.openmetrics (large — only when performance analysis needed)
    - Monitoring stack pods and events: namespaces/openshift-monitoring/pods/*/logs/current.log and previous.log, namespaces/openshift-monitoring/core/events.yaml
    - For node resource pressure: nodes/*/sysinfo.log, nodes/*/dmesg, namespaces/<namespace>/core/resourcequotas.yaml when quota-related
    - Cluster resource quotas: cluster-scoped-resources/quota.openshift.io/clusterresourcequotas/ when quota-related
    - If the alert is SystemMemoryExceedsReservation (or similar kubelet reservation/memory alerts): ALSO apply rule 15a FIRST — do NOT treat Prometheus/cAdvisor errors as primary until KubeletConfig is validated

15a. For SystemMemoryExceedsReservation / kubelet memory reservation / systemReserved / kubeReserved / autoSizingReserved / auto-sizing memory alerts, ALWAYS include as [high] BEFORE monitoring deep-dives:
    - cluster-scoped-resources/machineconfiguration.openshift.io/kubeletconfigs/ (or kubeletconfigs.yaml) — KubeletConfig CRs
    - cluster-scoped-resources/machineconfiguration.openshift.io/machineconfigs.yaml — including any *auto-sizing* MachineConfigs
    - cluster-scoped-resources/machineconfiguration.openshift.io/machineconfigpools.yaml
    - host_service_logs/masters/kubelet_service.log and host_service_logs/workers/kubelet_service.log
    - cluster-scoped-resources/core/nodes/*.yaml (allocatable vs capacity)
    - machine_config_ondisk/*/mcs-machine-config-content.json when present (rendered kubelet config)
    CRITICAL field-placement check (must be reflected in reasons / later RCA):
    - autoSizingReserved belongs at KubeletConfig spec.autoSizingReserved (boolean), NOT nested inside spec.kubeletConfig
    - systemReserved / kubeReserved belong under spec.kubeletConfig only when auto-sizing is not used as intended
    - Filenames like *auto-sizing-disabled* are NOT proof of correct config — always inspect the KubeletConfig YAML structure
    - Prometheus/cAdvisor/TLS/monitoring errors are SECONDARY until KubeletConfig field placement is confirmed correct

16. For machine config/node issues, ALWAYS include:
    - Machine config daemon logs: host_service_logs/masters/machine-config-daemon-firstboot_service.log, machine-config-daemon-host_service.log (and workers/ equivalents)
    - System service logs: host_service_logs/masters/kubelet_service.log, host_service_logs/workers/kubelet_service.log
    - OS layer logs: host_service_logs/masters/ostree-finalize-staged_service.log, rpm-ostreed_service.log (and workers/ equivalents)
    - Node information: cluster-scoped-resources/core/nodes/*.yaml, nodes/*/
    - KubeletConfig CRs: cluster-scoped-resources/machineconfiguration.openshift.io/kubeletconfigs/ (or kubeletconfigs.yaml)
    - MachineConfigs and pools: machineconfigs.yaml, machineconfigpools.yaml
    - MCO operator: namespaces/openshift-machine-config-operator/pods/*/logs/current.log and previous.log, core/events.yaml
    - On-disk config diff: machine_config_ondisk/<node-name>/bootstrapconfigdiff, mcs-machine-config-content.json (when degraded nodes exist)
    - If NotReady / masters down: ALSO apply rule 6 (external DNS / API resolution) before OVN/CRI-O
    - If SystemMemoryExceedsReservation / autoSizingReserved: ALSO apply rule 15a

17. For operator issues, ALWAYS include:
    - cluster-scoped-resources/config.openshift.io/clusteroperators.yaml
    - Operator namespace events: namespaces/<operator-namespace>/core/events.yaml
    - Operator pod logs (current AND previous): namespaces/<operator-namespace>/pods/*/logs/current.log, namespaces/<operator-namespace>/pods/*/logs/previous.log
    - Related operator logs: namespaces/openshift-<operator>-operator/pods/*/logs/current.log
    - Operator configmaps: namespaces/<operator-namespace>/core/configmaps.yaml
    - CRDs owned by the operator: cluster-scoped-resources/apiextensions.k8s.io/customresourcedefinitions/ (when CR type is broken or missing)
    - API service registrations: cluster-scoped-resources/apiregistration.k8s.io/apiservices.yaml (when operator provides an aggregated API)

18. For infrastructure configuration, ALWAYS include:
    - Infrastructure config: cluster-scoped-resources/config.openshift.io/infrastructures.yaml
    - DNS config: cluster-scoped-resources/config.openshift.io/dnses.yaml
    - Proxy config: cluster-scoped-resources/config.openshift.io/proxies.yaml
    - Image/registry config: cluster-scoped-resources/config.openshift.io/images.yaml
    - Network config: cluster-scoped-resources/config.openshift.io/networks.yaml
    - Feature gates: cluster-scoped-resources/config.openshift.io/featuregates.yaml
    - Scheduler config: cluster-scoped-resources/config.openshift.io/schedulers.yaml
    - Console config: cluster-scoped-resources/config.openshift.io/consoles.yaml
    - Cluster version: namespaces/openshift-cluster-version/

19. For Ingress / Route / HAProxy / apps URL issues, ALWAYS include:
    - ingress_controllers/default/*/haproxy.config (or ingress_controllers/*/*/haproxy.config)
    - namespaces/openshift-ingress/pods/*/logs/current.log and previous.log
    - namespaces/openshift-ingress/core/events.yaml
    - namespaces/openshift-ingress/apps/deployments.yaml (router deployment status)
    - namespaces/openshift-ingress-operator/pods/*/logs/current.log and previous.log
    - namespaces/openshift-ingress-operator/core/events.yaml
    - namespaces/<namespace>/route.openshift.io/routes.yaml (when app namespace known)
    - namespaces/<namespace>/core/services.yaml (backend service for the route)
    - infrastructures.yaml and dnses.yaml (*.apps.<domain> external DNS)

20. For cluster upgrade / CVO stuck / update failed / Progressing issues, ALWAYS include:
    - cluster-scoped-resources/config.openshift.io/clusterversions.yaml (or clusterversions/)
    - cluster-scoped-resources/config.openshift.io/clusteroperators.yaml
    - cluster-scoped-resources/config.openshift.io/featuregates.yaml (feature gates can block upgrades)
    - namespaces/openshift-cluster-version/core/events.yaml
    - namespaces/openshift-cluster-version/pods/*/logs/current.log and previous.log
    - Events/logs for any Degraded operator namespace mentioned
    - machineconfigpools when node drain/MCP blocks the upgrade
    - namespaces/openshift-machine-config-operator/pods/*/logs/current.log and previous.log, core/events.yaml (MCO commonly blocks upgrades)

21. For workload issues (Deployment / ReplicaSet / StatefulSet / DaemonSet / Job / CronJob / rollout), ALWAYS include:
    - namespaces/<namespace>/apps/deployments.yaml, replicasets.yaml, statefulsets.yaml, daemonsets.yaml as relevant
    - namespaces/<namespace>/batch/jobs.yaml and cronjobs.yaml when job-related
    - namespaces/<namespace>/core/events.yaml and core/pods.yaml
    - namespaces/<namespace>/core/services.yaml (when workload connectivity is involved)
    - namespaces/<namespace>/core/configmaps.yaml (when workload config is relevant)
    - namespaces/<namespace>/autoscaling/horizontalpodautoscalers.yaml (when scaling-related)
    - Pod current.log and previous.log for failing pods

22. For unexplained Pending pods / FailedScheduling / scheduler issues, ALWAYS include:
    - namespaces/openshift-kube-scheduler/pods/*/logs/current.log and previous.log
    - namespaces/openshift-kube-scheduler/core/events.yaml
    - cluster-scoped-resources/config.openshift.io/schedulers.yaml (if present)
    - cluster-scoped-resources/core/nodes/*.yaml (node capacity/allocatable for resource fit)
    - namespaces/<namespace>/core/events.yaml (FailedScheduling)
    - namespaces/<namespace>/autoscaling/horizontalpodautoscalers.yaml (HPA can trigger Pending via rapid scale-up)
    - namespaces/openshift-kube-controller-manager/pods/*/logs/current.log and previous.log when controllers/replicas stuck

23. For admission webhook / ValidatingWebhook / MutatingWebhook / "failed to complete mutation" / webhook timeout denials, ALWAYS include as [high]:
    - ALL MutatingWebhookConfigurations and ValidatingWebhookConfigurations — prefer the
      directory form (the resolver auto-expands either layout to the same files):
      cluster-scoped-resources/admissionregistration.k8s.io/mutatingwebhookconfigurations/
      cluster-scoped-resources/admissionregistration.k8s.io/validatingwebhookconfigurations/
      (the aggregated .yaml form also resolves correctly if that is how must-gather stored it)
    - For EVERY webhook: note name, clientConfig.service (namespace/name/port), timeoutSeconds, failurePolicy, sideEffects, rules/operations
    - Match any timeout duration stated in the user error (e.g. "in 13s") to webhook timeoutSeconds fields — exact matches are mandatory HIGH findings; do not stop after the first few webhooks seen in apiserver logs
    - Endpoints / EndpointSlices for each webhook service (namespaces/<webhook-ns>/core/endpoints.yaml or discovery endpoint slices) to detect zero-endpoint backends
    - Webhook controller/operator pod logs current.log + previous.log and core/events.yaml in each webhook's namespace when identifiable (including common third-party injectors: vault, dynatrace, opentelemetry, service mesh, CI/CD sidecars, security agents — inventory from the CR list, do not assume a short list)
    - audit_logs/kube-apiserver/ (Conditional) and kube-apiserver pod logs only as supporting evidence of dial/timeout to webhook backends
    - Do NOT promote kube-apiserver CrashLoop / OVN / RBAC cascade as the sole [high] story until the full webhook inventory + timeout/endpoint match is represented in the file list

24. For Windows node issues, ALWAYS include (conditional — only if windows/ present):
    - host_service_logs/windows/log_files/kubelet/kubelet.log
    - host_service_logs/windows/log_files/containerd/containerd.log
    - host_service_logs/windows/log_files/kube-proxy/kube-proxy.log
    - host_service_logs/windows/log_files/hybrid-overlay/hybrid-overlay.log
    - host_service_logs/windows/log_files/wicd/
    - host_service_logs/windows/log_files/csi-proxy/csi-proxy.log when storage-related

25. For Service Mesh / Istio / Envoy / Kiali issues, ALWAYS include (conditional — only if istio/ present):
    - Istiod sync status: istio/namespaces/<namespace>/<revision>/debug-syncz.json
    - istio/namespaces/*/pods/*/config_dump_istiod.json
    - istio/namespaces/*/pods/*/config_dump_proxy.json
    - istio/namespaces/*/pods/*/proxy_stats
    - istio/cluster-scoped-resources/networking.istio.io/
    - Gateway API CRDs: cluster-scoped-resources/apiextensions.k8s.io/customresourcedefinitions/ (Gateway, GatewayClass, HTTPRoute, etc.)
    - Do NOT default to OVN-only when the problem clearly names Istio/Service Mesh

26. For platform-specific issues, ALWAYS include when platform is known or infrastructures.yaml indicates it:
    - Always: infrastructures.yaml, dnses.yaml, proxies.yaml
    - vSphere: namespaces/openshift-vsphere-csi-driver/ pods logs + csinodetopologies + cnsvspherevolumemigrations if present
    - ARO/Azure: namespaces/openshift-azure-operator/, openshift-azure-logging/ when present
    - AWS/GCP/IBM: infrastructures.yaml + platform operator namespaces if present; Machine API when nodes fail to join
    - Bare-metal: namespaces/openshift-machine-api/ (includes baremetal-operator pods), NMState resources (rule 7 conditional CRs), MetalLB namespace if present

27. For RHOSO / OpenStack Services on OpenShift / Nova / Neutron / Keystone / Galera / RabbitMQ / Heat / Cinder / Horizon issues, ALWAYS include:
    - Rule 6 first if masters NotReady or API unreachable (Nova-down is often a cascade)
    - namespaces/openstack/core/events.yaml
    - namespaces/openstack/pods/*/logs/current.log and previous.log for affected services (nova, neutron, keystone, rabbitmq, galera/mariadb, etc.)
    - namespaces/openstack/apps/deployments.yaml, statefulsets.yaml (service deployment/statefulset status)
    - namespaces/openstack/core/configmaps.yaml, services.yaml (OpenStack service config and endpoints)
    - L0 paths at [high]: cluster-scoped-resources/config.openshift.io/infrastructures.yaml, dnses.yaml, pod_network_connectivity_check/podnetworkconnectivitychecks.yaml, proxies.yaml, images.yaml when relevant
    - Rank [medium]: cluster-scoped-resources/config.openshift.io/clusteroperators.yaml and other openstack/operator cascade paths per OUTAGE PRIORITY TIERING

28. For Insights / compliance / insights-operator issues, ALWAYS include:
    - insights-data/ (archive if present)
    - namespaces/openshift-insights/pods/*/logs/current.log and previous.log
    - namespaces/openshift-insights/core/events.yaml

29. For OLM / OperatorHub / Subscription / CatalogSource / InstallPlan / CSV issues, ALWAYS include:
    - namespaces/openshift-operator-lifecycle-manager/pods/*/logs/current.log and previous.log
    - namespaces/openshift-operator-lifecycle-manager/core/events.yaml
    - namespaces/openshift-marketplace/pods/*/logs/current.log and previous.log (CatalogSource pods)
    - namespaces/openshift-marketplace/core/events.yaml
    - namespaces/<operator-namespace>/operators.coreos.com/subscriptions.yaml, installplans.yaml, clusterserviceversions.yaml (when the target operator namespace is known)
    - cluster-scoped-resources/apiextensions.k8s.io/customresourcedefinitions/ (CRDs installed by the operator)
    - cluster-scoped-resources/config.openshift.io/clusteroperators.yaml (marketplace operator status)

30. For Console / web console access / FeatureGate / TechPreview issues, ALWAYS include:
    - cluster-scoped-resources/config.openshift.io/consoles.yaml (console URL, OAuth config)
    - cluster-scoped-resources/config.openshift.io/featuregates.yaml (enabled feature sets, TechPreviewNoUpgrade)
    - namespaces/openshift-console/pods/*/logs/current.log and previous.log
    - namespaces/openshift-console/core/events.yaml
    - namespaces/openshift-console-operator/pods/*/logs/current.log and previous.log, core/events.yaml
    - cluster-scoped-resources/config.openshift.io/authentications.yaml (console login depends on OAuth)
    - infrastructures.yaml and dnses.yaml (console route DNS)

SPECIFICITY REQUIREMENTS:

31. Use SPECIFIC file paths whenever possible, not just directory patterns
32. Include BOTH current.log AND previous.log for pod logs
33. Include BOTH masters/ AND workers/ service logs when relevant
34. Specify exact JSON file names in etcd_info/, monitoring/, etc.
35. Include configuration files (configmaps.yaml, daemonsets.yaml) for operator namespaces

CROSS-REFERENCE REQUIREMENTS:

36. Always check for related components:
    - If etcd issues mentioned, include API server files AND infrastructures.yaml / dnses.yaml
    - If NotReady / API unreachable / control-plane or Nova/RHOSO down: apply rule 6 BEFORE OVN/CRI-O
    - If networking issues mentioned, include DNS (internal + external config) before blaming OVN; include CRI-O only when runtime/CNI is implicated
    - If x509 / ImagePull / external connectivity mentioned, include proxies.yaml and images.yaml
    - If operator issues mentioned, include cluster operator status and related namespaces
    - If OLM / Subscription / CatalogSource / CSV mentioned: apply rule 29
    - If console access / web console / FeatureGate / TechPreview mentioned: apply rule 30
    - If performance issues mentioned, include monitoring metrics and node diagnostics
    - If RHOSO / OpenStack / Nova / Galera / RabbitMQ mentioned: apply rule 27 and rule 6 first
    - If upgrade stuck: apply rule 20; also rule 30 if FeatureGate blocks it
    - If Ingress/apps URL: apply rule 19 (not OVN-only)
    - If Pending unexplained: apply rule 22 before assuming CNI
    - If SystemMemoryExceedsReservation / autoSizingReserved / kubelet reservation: apply rule 15a BEFORE monitoring primacy
    - If storage / PVC / mount / CSI mentioned: apply rule 13 (includes volume snapshots, CSI driver pods, PVCs)

37. Return file paths in three priority buckets: [high], [medium], [low]. Output only the list below.
    Priority hint: infrastructure prerequisites (external DNS, VIP/API URL, proxy, images) belong in
    [high] when symptoms match rules 6 or 8; OVN/CRI-O cascade evidence belongs in [medium] unless
    CNI is clearly primary.

NOTE ON PATH FORM: For cluster-scoped resources that OpenShift may collect as either a
single aggregated `.yaml` file or a directory of per-object YAML files (e.g. webhook
configurations, cluster operators, KubeletConfigs), the resolver expands `.yaml` ↔ directory
when the exact form is not on disk. **Exception — Node status:** must-gather stores only
per-node files at `cluster-scoped-resources/core/nodes/<node-name>.yaml` — there is no
aggregated `nodes.yaml` and a bare `core/nodes/` path does NOT resolve. Always use
`cluster-scoped-resources/core/nodes/*.yaml` (or a specific node file). **CoreDNS logs:**
use container-aware `namespaces/openshift-dns/pods/*/*/*/logs/current.log` (not `pods/*/logs/`).

OUTPUT FORMAT — CRITICAL: PROBLEM CATEGORY THEN Model Name THEN NUMBERED LIST WITH PRIORITY [high], [medium], [low] AND REASON

Your response must have exactly two parts in this order:
1. First line: "Problem category: <category>" where <category> is the problem category (e.g. API Server, etcd, Networking, External DNS, Storage, Storage Version Migration, Cluster Operator, Node/Machine Config, Machine API, KubeletConfig/Memory Reservation, OLM, Security/RBAC, Authentication, Image Registry, Ingress/Routes, Upgrade/CVO, Workloads, Scheduler, Admission Webhooks, Windows, Service Mesh, Platform, RHOSO/OpenStack, Insights, Console/FeatureGate, Monitoring, IPsec).
2. Then a numbered list. Each line: number, period, space, priority tag, space, path, then " — " and a short reason.

- **[high]** = utmost important, must be looked at first to solve the user-mentioned issue.
- **[medium]** = important for context or secondary diagnosis.
- **[low]** = helpful but lower priority.

Order: list all [high] paths first, then all [medium], then all [low]. No other text. No JSON. No markdown. No code blocks. No explanation. No headers other than the Problem category line.
You MUST return at least one path. Never return an empty response.
For each path, include a brief reason after the path, separated by " — " (space, dash, space), so the format is: number. [priority] path — reason

Example (this is the only allowed format):
Problem category: External DNS / RHOSO
1. [high] cluster-scoped-resources/config.openshift.io/infrastructures.yaml — API hostnames and VIPs
2. [high] cluster-scoped-resources/config.openshift.io/dnses.yaml — cluster DNS config
3. [high] pod_network_connectivity_check/podnetworkconnectivitychecks.yaml — DNSError / lookup failures
4. [high] namespaces/openshift-dns/pods/*/*/*/logs/current.log — CoreDNS upstream lookup errors
5. [high] cluster-scoped-resources/core/nodes/*.yaml — NotReady node conditions
6. [high] etcd_info/endpoint_health.json — etcd quorum health
7. [medium] cluster-scoped-resources/config.openshift.io/clusteroperators.yaml — operator status matrix
8. [medium] namespaces/openstack/core/events.yaml — Nova/RHOSO service events
9. [low] network_logs/leader_ovnnb_status — OVN dataplane (cascade unless CNI primary)

Rules:
- First line must be exactly "Problem category: <category>" (one line, then a blank line optional, then the list).
- Each list line: number, period, space, [high] or [medium] or [low], space, path, then " — " and a short reason (e.g. "1. [high] path — reason").
- Paths relative to /must-gather/ base directory.
- List high-priority paths first, then medium, then low.
- Base suggestions on the must-gather structure documentation above.
- NEVER skip journal logs, service logs, events, or configuration files for components mentioned in the problem.
- ALWAYS include both current.log and previous.log for pod logs.
- Before finalizing, verify you checked the problem statement against EVERY rule (3-30),
  not only the rule matching your primary hypothesis — include mandatory files for ALL
  matched rules.
- Before finalizing, verify NotReady/API-unreachable/RHOSO-down symptoms applied rule 6 at [high]
  before OVN/CRI-O/etcd cascade paths.
- Output NOTHING else. Just the numbered list with priority tags and reasons."""

    def _parse_llm_response(self, response: str) -> Dict:
        response = response.strip()
        if response.startswith('```'):
            first_newline = response.find('\n')
            if first_newline != -1:
                response = response[first_newline:].strip()
            if response.endswith('```'):
                response = response[:-3].strip()
        # Extract problem category from first line if present (e.g. "Problem category: API Server")
        problem_category = 'Unknown'
        lines = response.splitlines()
        if lines:
            first = lines[0].strip()
            m_cat = re.match(r'^Problem\s+category\s*:\s*(.+)$', first, re.IGNORECASE)
            if m_cat:
                problem_category = m_cat.group(1).strip()
                lines = lines[1:]  # skip category line for path parsing
                response = '\n'.join(lines)
        # Parse numbered list: "1. [high] path", "2. [medium] path", "1. path", etc.
        suggested_files = []
        for line in response.splitlines():
            line = line.strip()
            if not line:
                continue
            path = None
            priority = 'medium'
            m = re.match(r'^\s*\d+\.\s*\[(high|medium|low)\]\s*(.+)$', line, re.IGNORECASE)
            if m:
                priority = m.group(1).lower()
                path = m.group(2).strip()
            else:
                m = re.match(r'^\s*\d+\.\s*(.+)$', line)
                if m:
                    path = m.group(1).strip()
                    if path.startswith('[high]'):
                        priority, path = 'high', path[6:].strip()
                    elif path.startswith('[medium]'):
                        priority, path = 'medium', path[9:].strip()
                    elif path.startswith('[low]'):
                        priority, path = 'low', path[5:].strip()
                else:
                    m2 = re.match(r'^\s*\d+[\):]\s*(.+)$', line)
                    if m2:
                        path = m2.group(1).strip()
            if not path:
                for prefix in ('- ', '* ', '• '):
                    if line.startswith(prefix) and '/' in line:
                        path = line[len(prefix):].strip()
                        break
            if path and not path.startswith(('http://', 'https://')):
                reason = ''
                if ' — ' in path:
                    path, reason = path.split(' — ', 1)
                    path = path.strip()
                    reason = reason.strip()
                suggested_files.append({'path': path, 'priority': priority, 'reason': reason})
        # Fallback: if no numbered lines matched, treat lines that look like paths (contain /) as paths
        if not suggested_files and response:
            for line in response.splitlines():
                line = line.strip()
                if not line or len(line) < 5 or '/' not in line:
                    continue
                if line.startswith(('http', '#', '##', 'Example', 'Rules', 'Rule', 'Output', 'Return')):
                    continue
                if line.startswith(('- ', '* ', '• ')):
                    line = line[2:].strip()
                if re.match(r'^[\w\-.*/\[\]()]+$', line):
                    suggested_files.append({'path': line, 'priority': 'medium', 'reason': ''})
        out = {
            'suggested_files': suggested_files,
            'reasoning': '',
            'problem_category': problem_category,
            'priority': {f.get('path', ''): f.get('priority', 'medium') for f in suggested_files},
        }
        if not suggested_files and response:
            out['raw_response'] = response
        return out

    def print_file_suggestions(self, result: Dict):
        if 'error' in result:
            print(f"\n❌ Error: {result['error']}")
            if 'raw_response' in result:
                print(f"\nRaw response:\n{result['raw_response']}")
            return
        print("\n" + "="*100 + "\nMUST-GATHER FILE SUGGESTIONS\n" + "="*100)
        print(f"\nProblem Category: {result.get('problem_category', 'Unknown')}")
        if result.get('keywords_identified'):
            print(f"Keywords Identified: {', '.join(result['keywords_identified'])}")
        if result.get('affected_components'):
            print(f"Affected Components: {', '.join(result['affected_components'])}")
        for i, f in enumerate(result.get('suggested_files', []), 1):
            print(f"  {i}. [{f.get('priority', '')}] {f.get('path', '')}")
            if f.get('reason'):
                print(f"     Reason: {f['reason']}")
        if result.get('reasoning'):
            print(f"\nReasoning:\n" + "-" * 100 + "\n" + result['reasoning'])
        if result.get('additional_notes'):
            print(f"\nAdditional Notes:\n" + "-" * 100 + "\n" + result['additional_notes'])
        print("\n" + "="*100)


# ── TEMPORARY: dump LLM file selection as JSON for testing / HARDCODED_FILES ──
# Remove this function when no longer needed for testing.
def dump_as_hardcoded_files(result: Dict) -> None:
    """
    Save the suggest_files() result as a JSON file in Results/ folder,
    and also print the pasteable HARDCODED_FILES format to console.

    Saved to: Results/hardcoded_files.json

    Usage:
        result = selector.suggest_files(problem, docs_dir)
        dump_as_hardcoded_files(result)
    """
    import json as _json

    files = result.get("suggested_files", [])
    if not files:
        print("\n# No files to dump.")
        return

    # Resolve output directory
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    _project_root = os.path.dirname(_script_dir)
    out_dir = os.path.join(_project_root, "Results")
    os.makedirs(out_dir, exist_ok=True)

    out_path = os.path.join(out_dir, "hardcoded_files.json")
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            _json.dump(files, f, indent=2, ensure_ascii=False)
        print(f"\n[dump_as_hardcoded_files] Saved {len(files)} files to: {out_path}")
    except Exception as e:
        print(f"\n[dump_as_hardcoded_files] ERROR: Could not write {out_path}: {e}")
        import traceback
        traceback.print_exc()
        return

    # Also print pasteable HARDCODED_FILES format to console
    print("\n" + "=" * 80)
    print("# COPY-PASTE the block below into HARDCODED_FILES in tools.py")
    print("=" * 80)
    print("HARDCODED_FILES: list = [")
    for f in files:
        path = f.get("path", "").replace('"', '\\"')
        pri = f.get("priority", "medium")
        reason = f.get("reason", "").replace('"', '\\"')
        print(f'        {{"path": "{path}",')
        print(f'            "priority": "{pri}",')
        print(f'            "reason": "{reason}",')
        print(f"        }},")
    print("    ]")
    print("=" * 80 + "\n")
# ── END TEMPORARY ──


def main():
    user_problem_statement = "Kubelet configuration directory is not created on scaled out node."
    must_gather_docs_dir = MUST_GATHER_DOCS_DIR_DEFAULT
    try:
        selector = MustGatherFileSelector()
        print("Analyzing problem statement and suggesting must-gather files...")
        result = selector.suggest_files(user_problem_statement, must_gather_docs_dir)
        selector.print_file_suggestions(result)
        dump_as_hardcoded_files(result)  # TEMPORARY: print pasteable format
        return result
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return None


if __name__ == "__main__":
    main()
