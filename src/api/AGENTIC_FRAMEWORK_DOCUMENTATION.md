# Agentic AI Debugging Framework — End-to-End Documentation

**For: Project Managers & Stakeholders**
**Version:** 1.1
**Date:** August 2026

---

## Table of Contents

1. [Executive Overview](#1-executive-overview)
2. [Architecture & System Design](#2-architecture--system-design)
3. [Component Breakdown](#3-component-breakdown)
4. [End-to-End Workflow — How It Works](#4-end-to-end-workflow--how-it-works)
5. [Phase-by-Phase Detailed Walkthrough](#5-phase-by-phase-detailed-walkthrough)
6. [Incremental Deepening Strategy](#6-incremental-deepening-strategy)
7. [ML Classification — YAML Templatization](#7-ml-classification--yaml-templatization)
8. [ML Classification — Log Templatization (Drain3)](#8-ml-classification--log-templatization-drain3)
9. [Chronology of Events — Mini-Stage Pipeline](#9-chronology-of-events--mini-stage-pipeline)
10. [Example: RCA Payload Sent to LLM](#10-example-rca-payload-sent-to-llm)
11. [Example: Workflow Console Output](#11-example-workflow-console-output)
12. [Example: Root Cause Analysis (RCA) Report](#12-example-root-cause-analysis-rca-report)
13. [Cost Optimization & Compression](#13-cost-optimization--compression)
14. [Error Handling & Resilience](#14-error-handling--resilience)
15. [Configuration Reference](#15-configuration-reference)
16. [Key Metrics & KPIs](#16-key-metrics--kpis)
17. [Glossary](#17-glossary)

---

## 1. Executive Overview

### What Is This Framework?

The **Agentic AI Debugging Framework** is an autonomous, AI-powered diagnostic system designed to analyze OpenShift/Kubernetes **must-gather bundles** (diagnostic dumps) and produce **Root Cause Analysis (RCA) reports** for cluster issues.

### The Problem It Solves

When an OpenShift cluster experiences failures (e.g., pods crashing, operators degraded, garbage collection failures), engineers collect a **must-gather bundle** — a snapshot containing thousands of YAML configuration files, service logs, event records, and metrics. Manually sifting through 5,000+ files (often 200+ MB) to find the root cause is time-consuming and requires deep domain expertise.

### How It Solves It

The framework uses a **Agentic architecture** with:

- **An LLM-powered orchestrator agent** (Claude) that reasons about which files to examine
- **Local ML classifiers** that extract and classify anomalies from YAML files and logs *without* sending raw data to the LLM
- **A ReAct (Reasoning + Acting) loop** where the AI autonomously decides its next step, evaluates its own work, and deepens analysis when needed

### Key Value Proposition

| Metric                | Without Framework          | With Framework                     |
|-----------------------|----------------------------|------------------------------------|
| **Time to RCA**       | Hours to days              | Minutes (~3-5 min)                 |
| **Files examined**    | Manual selection           | Automated priority-based selection |
| **Data sent to LLM**  | Raw files (17+ MB)         | Compressed payload (~85 KB)        |
| **LLM cost per run**  | ~$13.40 (hypothetical raw) | ~$0.78 (with ML extraction)        |
| **Compression ratio** | 1:1                        | **207x**                           |
| **Cost savings**      | —                          | **$12.62 per run (94% reduction)** |

---

## 2. Architecture & System Design

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    USER INTERFACE (chat_ui.py)              │
│              Problem Statement + Must-Gather Path           │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│              ORCHESTRATOR AGENT (orchestrator_agent.py)     │
│                    ReAct Loop (Claude LLM)                  │
│          Thinks → Decides Tool → Observes → Repeats         │
│                  Max 25 iterations, 3 rounds                │
└──────┬──────┬──────┬──────┬──────┬──────┬──────┬────────────┘
       │      │      │      │      │      │      │
       ▼      ▼      ▼      ▼      ▼      ▼      ▼
   ┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐
   │check ││select││check ││analy-││analy-││valid-││perfo-│
   │_llm  ││_files││_file ││ze_   ││ze_   ││ate_  ││rm_   │
   │_avai-││      ││_avai-││yaml  ││logs  │|token_││rca   │
   │labil-││      ││labil-││      ││      ││budg- ││      │
   │ity   ││      ││ity   ││      ││      ││et    ││      │
   └──────┘└──────┘└──────┘└──────┘└──────┘└──────┘└──────┘
               │              │        │               │
               ▼              ▼        ▼               ▼
   ┌───────────────┐ ┌────────────────────┐  ┌──────────────┐
   │ Must-Gather   │ │  ML Classifiers    │  │ LLM          │
   │ File Selector │ │  (Local, No LLM)   │  │  RCA Engine  │
   │ (LLM-powered) │ │                    │  │              │
   │               │ │ • YAML: Drain3 +   │  │ Produces the │
   │ Reads docs,   │ │   pattern matching │  │ final RCA    │
   │ suggests      │ │ • Logs: Drain3     │  │ report       │
   │ priority files│ │   template mining  │  │              │
   └───────────────┘ └────────────────────┘  └──────────────┘
```

### Key Design Principles

1. **ML-First, LLM-Second**: Local ML classifiers (no API calls, no cost) do the heavy lifting of data reduction. Only the distilled, classified anomalies are sent to the LLM.
2. **Filter-Before-Drain3**: Raw YAML objects and log lines are filtered, cleaned, and categorized *before* Drain3 template mining runs. Drain3 never sees wrapper metadata, empty chunks, or irrelevant log levels unless explicitly configured.
3. **Mini-Stage Pipelines**: Every major phase (file selection, YAML processing, log processing, aggregation, chronology, RCA) is broken into discrete, observable mini-stages. Each stage has a clear input, output, and progress event for the UI and console.
4. **Chronology-First RCA**: A time-ordered chain of events is built from YAML timestamps and log lines *before* the LLM call, giving the RCA engine a structured timeline rather than asking it to reconstruct one from unstructured data.
5. **Incremental Deepening**: Start with high-priority files; only expand to medium/low if the RCA is not conclusive.
6. **Self-Evaluating**: After each RCA attempt, the agent evaluates its own confidence and decides whether to expand.
7. **Token-Budget Aware**: Validates payload size before every LLM call to prevent context overflow.
8. **Cost-Transparent**: Every run produces a detailed cost table comparing actual cost vs. hypothetical "send everything to LLM" cost.

---

## 3. Component Breakdown

### 3.1 Source Files

| File                           | Role                                                               | LLM Calls?                   |
|--------------------------------|--------------------------------------------------------------------|------------------------------|
| `orchestrator_agent.py`        | Master agent — ReAct loop, session management, Claude tool-use API | Yes (orchestrator reasoning) |
| `agent_prompts.py`             | System prompt and user message templates for the orchestrator      | —                            |
| `tools.py`                     | 8 tool definitions + executor functions (dispatch map)             | Some tools call LLM          |
| `data_analyzer.py`             | YAML critical field extraction + ML classification pipeline        | No (local ML only)           |
| `must_gather_file_selector.py` | LLM-powered file selection from must-gather structure docs         | Yes (1 LLM call)             |
| `llm_rca_agent.py`             | RCA payload builder + LLM call for root cause analysis             | Yes (1-2 LLM calls)          |
| `root_cause_analyzer_yaml.py`  | YAML-specific root cause analysis utilities                        | No                           |
| `chat_ui.py`                   | Streamlit-based web UI for user interaction                        | No                           |
| `app_paths.py`                 | Path resolution for frozen exe and development environments        | No                           |

### 3.2 ML Modules (Machine-learning/)

| File                        | Role                                                                                                    |
|-----------------------------|---------------------------------------------------------------------------------------------------------|
| `ML_YAML_CLASSIFICATION.py` | Drain3-based YAML object classification (Error / Majority Error / Majority / CONFIG)                    |
| `ML_LOG_CLASSIFICATION.py`  | Drain3-based log line classification (Rare Error / High-Freq Error / Warning / Info / Unknown / CONFIG) |

### 3.3 Configuration Files (Config/)

| File                   | Role                                                                      |
|------------------------|---------------------------------------------------------------------------|
| `config.json`          | LLM API keys, model settings, pricing, agent limits                       |
| `yaml_processing.yaml` | YAML chunking, critical fields, error patterns, classification thresholds |
| `log_config.json`      | Log processing configuration                                              |
| `agent_memory.json`    | Session state persistence (auto-managed)                                  |

### 3.4 Data Sources (DataSource/)

| File                                  | Role                                                          |
|---------------------------------------|---------------------------------------------------------------|
| `MUST_GATHER_STRUCTURE.md`            | Documentation of must-gather directory structure              |
| `MUST_GATHER_ROUTING_GUIDE.md`        | Problem-category-to-file mapping guide                        |
| `MUST_GATHER_INDEX.md`                | Index of all known must-gather file types                     |
| `MUST_GATHER_DOCUMENTATION_README.md` | Documentation readme                                          |
| `keyfields_yaml_ml_input.odt`         | ODT table mapping YAML files to their critical field paths    |

---

## 4. End-to-End Workflow — How It Works

### The 6-Phase Pipeline

```
Phase 0: LLM Availability Check
    │
    ▼
Phase 1: Intelligent File Selection
    │  (LLM analyzes problem → suggests priority files)
    ▼
Phase 2: YAML Processing (Local ML)
    │  (Extract critical fields → ML classify each object)
    ▼
Phase 3: Log Processing (Local Drain3)
    │  (Template mining → classify by severity)
    ▼
Phase 4: Data Aggregation + Chronology
    │  (Merge YAML errors + Log errors → build compact payload)
    │  (Build time-ordered chain of events from timestamps)
    ▼
Phase 5: Root Cause Analysis (LLM)
    │  (Send compressed payload + chronology to LLM → get RCA)
    ▼
Phase 6: Cost Calculation & Reporting
```

### Mini-Stage Model (All Phases)

Every phase decomposes into **mini-stages** — small, named steps that can be tracked independently in the workflow console and UI progress bar. The two ML pipelines (YAML and logs) each have their own internal mini-stage chains; chronology is built as a separate mini-stage chain inside Phase 4.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  PHASE 1 — File Selection                                               │
│  1.1  Load must-gather structure docs                                   │
│  1.2  LLM suggests prioritized file list (high / medium / low)          │
│  1.3  Resolve paths + check availability on disk                        │
│  1.4  Auto-redistribute priorities if all files same tier               │
├─────────────────────────────────────────────────────────────────────────┤
│  PHASE 2 — YAML Processing (per file)                                   │
│  2.1  Chunk YAML into individual Kubernetes objects                     │
│  2.2  Extract critical fields per object                                │
│  2.3  Pre-Drain3 clean (strip line numbers, timestamps, UUIDs)          │
│  2.4  Pre-Drain3 filter (drop empty / wrapper-only objects)             │
│  2.5  Drain3 template mining on filtered critical fields                │
│  2.6  Classify objects (Error / Majority Error / CONFIG / Majority)     │
│  2.7  Post-classification filter (keep Error only for RCA payload)      │
├─────────────────────────────────────────────────────────────────────────┤
│  PHASE 3 — Log Processing (per file)                                    │
│  3.1  Select pipeline branch (entire-file vs chunk; auto by size)       │
│  3.2  Read log lines (sequential or parallel chunks)                    │
│  3.3  Pre-Drain3 preprocess (remove timestamps via regex patterns)      │
│  3.4  Pre-Drain3 filter (categorize by severity: E / W / I / UNKNOWN)   │
│  3.5  Drain3 template mining per severity level                         │
│  3.6  Template shift (promote error/config templates from W/I/UNKNOWN)  │
│  3.7  Pareto split (rare errors vs high-frequency errors)               │
│  3.8  Write output files (rare, highfreq, warning, config, etc.)        │
├─────────────────────────────────────────────────────────────────────────┤
│  PHASE 4 — Aggregation + Chronology                                     │
│  4.1  Merge YAML error objects + log error entries into payload         │
│  4.2  Strip noise (UUIDs, commit hashes, redundant line numbers)        │
│  4.3  Extract timestamps from YAML critical fields                      │
│  4.4  Extract timestamps from log error lines                           │
│  4.5  Dedupe + sort → chronology list                                   │
│  4.6  Format chronology block for LLM prompt (cap at 150 events)        │
│  4.7  Validate token budget                                             │
├─────────────────────────────────────────────────────────────────────────┤
│  PHASE 5 — RCA (LLM)                                                    │
│  5.1  Build prompt (issue + chronology + YAML errors + log errors)      │
│  5.2  Call LLM (chunked if payload exceeds budget)                      │
│  5.3  Normalize report headings (## Chronology of Events)               │
│  5.4  Evaluate confidence → deepen or return final report               │
└─────────────────────────────────────────────────────────────────────────┘
```

### Workflow State Machine

```
START
  │
  ▼
[check_llm_availability] ──FAIL──→ STOP (report API error)
  │ OK
  ▼
[select_files] ──────────→ Returns prioritized file list + availability
  │
  ▼
[analyze_yaml + analyze_logs] ──→ HIGH priority files first
  │
  ▼
[validate_token_budget] ──EXCEEDS──→ Chunk payload
  │ OK
  ▼
[perform_rca] ──────────→ Initial RCA with high-priority data
  │
  ▼
[evaluate_rca] ──HIGH CONFIDENCE──→ Return FINAL REPORT
  │ MEDIUM/LOW
  ▼
[analyze_yaml + analyze_logs] ──→ MEDIUM priority files
  │
  ▼
[perform_rca] (continuation) ──→ Refined RCA
  │
  ▼
[evaluate_rca] ──HIGH──→ Return FINAL REPORT
  │ LOW
  ▼
[Round 3: LOW priority] ──→ Final attempt → FINAL REPORT
```

---

## 5. Phase-by-Phase Detailed Walkthrough

### Phase 0: LLM Availability Gate

**What happens:** The framework sends a minimal "ping" request to the Claude API to verify:
- The API endpoint is reachable
- The API key is valid
- The model ID is accepted

**If it fails:** The entire workflow stops immediately with a clear error message. No further tools are called.

**Tool:** `check_llm_availability`

### Phase 1: Intelligent File Selection

**What happens:** The user's problem statement (e.g., "container image garbage collection is failing") is sent to Claude along with must-gather structure documentation. Claude analyzes the problem and returns a **prioritized list of files** to examine.

**Key behaviors:**
- Files are tagged as **high**, **medium**, or **low** priority
- The problem is categorized (e.g., "Node/Machine Config", "Networking", "Etcd")
- File availability is auto-checked against the actual must-gather bundle on disk
- If all files are the same priority, the framework auto-redistributes (2/3 high, 1/6 medium, 1/6 low)

**Tool:** `select_files`
**LLM calls:** 1

**Example output:**
```
Problem category: Node/Machine Config

1. [high] host_service_logs/masters/kubelet_service.log
2. [high] host_service_logs/masters/crio_service.log
3. [high] cluster-scoped-resources/.../machineconfigs.yaml
4. [medium] namespaces/openshift-machine-config-operator/core/events.yaml
5. [medium] host_service_logs/masters/machine-config-daemon*.log
6. [low] cluster-scoped-resources/.../machineconfigpools.yaml
```

### Phase 2: YAML Processing (Local ML — No LLM Cost)

**What happens:** Each YAML file is processed through the local ML pipeline in seven mini-stages. Drain3 only runs on objects that survive the pre-Drain3 filtering steps.

| Mini-Stage | Name | What It Does |
|------------|------|--------------|
| **2.1** | Chunking | Split multi-object YAML files into individual Kubernetes objects (each `- ` item under `items:`) |
| **2.2** | Critical Field Extraction | Extract only diagnostically relevant fields per object (from `yaml_processing.yaml` or ODT table) |
| **2.3** | Pre-Drain3 Clean | Strip line numbers, timestamps, and alphanumeric noise (UUIDs, commit hashes) from field values |
| **2.4** | Pre-Drain3 Filter | Drop empty chunks and list-wrapper metadata (`continue`, `resourceVersion` only) — these never reach Drain3 |
| **2.5** | Drain3 Clustering | Feed cleaned critical fields to Drain3; group similar objects into templates |
| **2.6** | Classification | Label each object: Error, Majority Error, CONFIG, or Majority (frequency + pattern analysis) |
| **2.7** | Post-Classification Filter | Keep only **Error**-classified objects for the RCA payload (Majority Error excluded by default) |

**Tool:** `analyze_yaml`
**LLM calls:** 0 (entirely local)
**Source:** `data_analyzer.py` → `ML_YAML_CLASSIFICATION.py`

**Pre-Drain3 filter examples (mini-stage 2.4):**
```
[Filter] Skipping Chunk 1 (lines 1-12): Empty/wrapper metadata
[Filter] Skipping Chunk 3 (lines 45-58): List wrapper metadata only
```

**Example classification table (mini-stage 2.6):**
```
Chunk    Lines     Classification    Reason
─────────────────────────────────────────────────────────
13       433-474   Error             Rare patterns (anomaly): failed, error
33       1277-1323 Error             Rare patterns (anomaly): error, not found
62       2509-2554 Error             Rare patterns (anomaly): failed, error
69       2807-2850 Error             Rare patterns (anomaly): waiting, degraded
```

### Phase 3: Log Processing (Local Drain3 — No LLM Cost)

**What happens:** Each log file passes through an eight-mini-stage pipeline. **Severity filtering and timestamp stripping happen before Drain3** — Drain3 never processes the full raw file at once; it receives pre-categorized, timestamp-normalized lines per severity level.

| Mini-Stage | Name | What It Does |
|------------|------|--------------|
| **3.1** | Branch Selection | Auto-select `ENTIRE_FILE` (< 50 MB) or `CHUNK_SEQUENTIAL` / `CHUNK_PARALLEL` (≥ 50 MB); apply file-size-aware chunk scaling |
| **3.2** | Line Reading | Read file in chunks (configurable `chunk_size`, parallel workers for large files) |
| **3.3** | Pre-Drain3 Preprocess | Remove timestamps via configurable regex patterns (`log_config.json` → `preprocessing.timestamp_patterns`) |
| **3.4** | Pre-Drain3 Severity Filter | Categorize each line into `E` (Error), `W` (Warning), `I` (Info), or `UNKNOWN` using content-based phrase detection + structural prefixes |
| **3.5** | Drain3 Template Mining | Run Drain3 separately on each severity bucket using **preprocessed** (timestamp-free) lines; map clusters back to **original** lines for output |
| **3.6** | Template Shift | Promote error/config templates found in W/I/UNKNOWN levels into the Error or Config buckets (entire clusters move together) |
| **3.7** | Pareto Split | Split Error templates into **rare** (≤ 15 lines/template) and **high-frequency** (Pareto 80% coverage) |
| **3.8** | Output Write | Save `*_rare_error.txt`, `*_highfreq_error.txt`, `*_Warning.txt`, `*_ConfigChanges.txt`, and companion `*_templates.txt` files |

**Tool:** `analyze_logs`
**LLM calls:** 0 (entirely local)
**Source:** `ML_LOG_CLASSIFICATION.py` (`process_log_file`)

**Why filter before Drain3?**
- Reduces Drain3 input volume by 60–90% on typical service logs (Info lines dominate)
- Timestamp removal improves template clustering (same error at different times groups correctly)
- Severity buckets prevent Warning/Info noise from diluting Error template quality
- Chunk pipeline keeps memory bounded on 10+ MB log files

**Example processing summary (mini-stages 3.4–3.7):**
```
Processing: kubelet_service.log (11.57 MB)
  [Unknown]     34,955 logs → 140 templates
  [Information]    212 logs →   6 templates
  [Warning]        385 logs →  21 templates
  [highfreq_error] 10,630 lines → 28 templates (98.2% coverage)
  [rare_error]       199 lines → 54 templates (1.8% coverage)
  [Error]         10,829 logs →  82 templates (combined)
  Completed in 2968 ms
```

### Phase 4: Data Aggregation + Chronology

**What happens:** YAML error objects and log error entries are merged into a compact payload. In parallel, a **chronology of events** is built as a time-ordered timeline from timestamps found in both YAML and log data.

| Mini-Stage | Name | What It Does |
|------------|------|--------------|
| **4.1** | Payload Merge | Combine Error-classified YAML objects + rare/highfreq log entries into `payload_for_llm` |
| **4.2** | Noise Stripping | Remove UUIDs, commit hashes, redundant line numbers from payload text |
| **4.3** | YAML Timestamp Extraction | Recursively scan critical fields for ISO timestamps (`lastTimestamp`, `eventTime`, `transitionTime`, etc.) |
| **4.4** | Log Timestamp Extraction | Parse timestamps from log lines (ISO, Kubernetes `E0112`, systemd `Jan 12`, CRI-O `time="..."`) |
| **4.5** | Dedupe + Sort | Remove duplicate (timestamp, snippet) pairs; sort lexicographically for chronological order |
| **4.6** | Format Chronology Block | Render as `timestamp | source | snippet` lines; cap at 150 events (first half + last half if exceeded) |
| **4.7** | Token Budget Check | `validate_token_budget` ensures payload + chronology fit within model context window |

**Data priority order for the payload:**
1. Rare Pattern Errors + Error-classified YAML objects (anomalies)
2. Chronology of Events (time-ordered timeline)
3. Configuration Changes / CONFIG-classified YAML objects
4. High-Frequency Errors / Majority Error YAML objects
5. Warnings (only if budget allows and evaluator requests)
6. Information / Unknown (rarely included)

**Source:** `llm_rca_agent.py` → `build_chronology_from_payload()`, `format_chronology_block()`

### Phase 5: Root Cause Analysis (LLM)

**What happens:** The compressed, classified payload is sent to Claude with the user's problem statement. Claude produces a structured RCA report.

For continuation rounds, the previous RCA is included so Claude can **refine** rather than restart.

**Tool:** `perform_rca`
**LLM calls:** 1 per round

### Phase 6: Cost Calculation & Reporting

**What happens:** A detailed cost comparison table is generated showing:
- Actual cost with ML extraction
- Hypothetical cost without ML (raw data to LLM)
- Per-file compression ratios
- Token usage breakdown by phase

---

## 6. Incremental Deepening Strategy

The framework uses a **3-round incremental deepening** strategy:

```
Round 1: HIGH priority files only
    │
    ├─ evaluate_rca → confidence = HIGH → STOP, return report
    │
    └─ confidence = MEDIUM/LOW → continue to Round 2

Round 2: Add MEDIUM priority files
    │
    ├─ evaluate_rca → confidence = HIGH → STOP, return report
    │
    └─ confidence = LOW → continue to Round 3

Round 3: Add LOW priority files (maximum deepening)
    │
    └─ evaluate_rca → STOP regardless (all data exhausted)
```

### Self-Evaluation (evaluate_rca)

After each RCA, a separate LLM call evaluates:
- **Confidence score:** high / medium / low
- **Missing areas:** What was not investigated
- **Suggested files to add:** Which specific files from the remaining pool to analyze next
- **Next step suggestion:** "Return final report" or "Expand to medium-priority files"

### User Feedback Loop

After the final report is delivered, the user can:
1. **"RCA Satisfactory"** → Session marked complete
2. **"RCA Not Satisfactory"** → Framework expands to next priority tier
3. **New observation** → Framework determines if this is a continuation hint or a new problem, and routes accordingly

---

## 7. ML Classification — YAML Templatization

### YAML Mini-Stage Pipeline (Filter Before Drain3)

```
Raw YAML file
    │
    ▼
┌──────────────────────────────────────────────────────────┐
│  MINI-STAGE 2.1 — Chunking                               │
│  Split into per-object chunks (items:, objects: lists)   │
└──────────────────────────┬───────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────┐
│  MINI-STAGE 2.2 — Critical Field Extraction             │
│  Extract paths from yaml_processing.yaml / ODT table     │
└──────────────────────────┬───────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────┐
│  MINI-STAGE 2.3 — Pre-Drain3 Clean                      │
│  • Remove line numbers from structure                    │
│  • Strip ISO timestamps from field values                │
│  • Remove UUIDs, commit hashes, alphanumeric noise       │
└──────────────────────────┬───────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────┐
│  MINI-STAGE 2.4 — Pre-Drain3 Filter  ◄── FILTER GATE   │
│  DROP if:                                                │
│    • Empty critical fields ({})                          │
│    • Wrapper-only metadata (continue, resourceVersion)   │
│  KEEP: objects with real diagnostic content              │
└──────────────────────────┬───────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────┐
│  MINI-STAGE 2.5 — Drain3 Clustering                      │
│  Flatten critical fields → string → Drain3 templates     │
└──────────────────────────┬───────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────┐
│  MINI-STAGE 2.6 — Classification                         │
│  Analyze verbose error patterns + cluster frequency      │
│  → Error | Majority Error | CONFIG | Majority            │
└──────────────────────────┬───────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────┐
│  MINI-STAGE 2.7 — Post-Classification Filter             │
│  Keep Error only for RCA payload (Majority Error excluded)│
└──────────────────────────────────────────────────────────┘
```

### How YAML Objects Are Classified

The YAML ML classifier processes Kubernetes objects through these steps:

**Step 1 (2.1): Chunking** — A YAML file like `events.yaml` containing 72 event objects is split into 72 individual chunks, one per Kubernetes object.

**Step 2 (2.2): Critical Field Extraction** — Only the fields defined in `yaml_processing.yaml` are extracted. For `events.yaml`:
```yaml
events.yaml:
  paths:
    - "metadata.namespace"
    - "type"
    - "reason"
    - "message"
    - "count"
    - "involvedObject.kind"
    - "involvedObject.name"
```

**Step 3 (2.3–2.4): Pre-Drain3 Clean + Filter** — Before Drain3 sees any data:
- Line numbers, timestamps, and UUIDs are stripped from field values
- Empty chunks and list-wrapper metadata objects are dropped entirely
- Only objects with real diagnostic content proceed to Drain3

**Step 4 (2.5): Template Mining (Drain3)** — The cleaned critical field values are fed to Drain3, which groups similar objects into clusters/templates.

**Step 5 (2.6): Classification** — Based on frequency and error pattern analysis:

| Classification     | Criteria | Diagnostic Value |
|--------------------|-------------------------------------------------------------|---------------------------|
| **Error**          | Rare pattern (< 5% frequency) + contains error keywords     | HIGHEST                   |
| **Majority Error** | Common negative pattern (> 30% frequency)                   | MEDIUM                    |
| **CONFIG**         | Contains configuration change indicators                    | HIGH                      |
| **Majority**       | Normal/healthy pattern (> 10% frequency, no error keywords) | LOW (excluded by default) |

### Sample: YAML Classification Output

For `events.yaml` (72 objects total):

```json
{
  "events.yaml": {
    "objects": [
      {
        "object_index": 12,
        "start_line": 433,
        "end_line": 474,
        "classification": "Error",
        "reason": "Rare patterns (anomaly): failed, error",
        "critical_fields": {
          "metadata": { "namespace": "default" },
          "type": "Warning",
          "reason": "FailedToCreateEndpoint",
          "message": "Failed to create endpoint for service
                      openshift-ingress-canary/ingress-canary:
                      Internal error occurred...",
          "count": 1,
          "involvedObject": {
            "kind": "Endpoints",
            "name": "ingress-canary"
          }
        }
      },
      {
        "object_index": 61,
        "classification": "Error",
        "reason": "Rare patterns (anomaly): failed, error",
        "critical_fields": {
          "type": "Warning",
          "reason": "ImageGCFailed",
          "message": "get container status: runtime container status:
                      rpc error: code = NotFound desc = could not
                      find container...",
          "involvedObject": {
            "kind": "Node",
            "name": "ip-10-0-97-146.us-east-2.compute.internal"
          }
        }
      }
    ],
    "summary": {
      "total_objects": 72,
      "error_count": 6,
      "majority_error_count": 0,
      "config_change_count": 0,
      "normal_count": 66,
      "included_in_output": 6
    }
  }
}
```

**Key takeaway:** Of 72 YAML objects, only **6 Error-classified objects** are sent to the LLM. The other 66 normal objects are discarded, saving significant tokens and cost.

### YAML Processing Configuration Reference

From `Config/yaml_processing.yaml`:

```yaml
chunking:
  max_items_section_indent: 2     # Max indent to recognize 'items:' as list container
  list_item_indent_offset: 2      # Expected indent after 'items:'
  indent_tolerance: 2             # Tolerance when matching list item indents
  list_container_keys:
    - "items"
    - "objects"

critical_fields:
  default_paths:
    - "metadata.name"
    - "metadata.namespace"
    - "status.conditions"
    - "status.phase"

classification:
  common_pattern_threshold: 0.30  # 30% = Majority Error
  majority_threshold: 0.10        # 10% = Majority (healthy)
  error_threshold: 0.05           # 5% = Error (rare anomaly)

output_flags:
  include_errors: true
  include_majority_errors: true
  include_config_changes: true
  include_normal_reports: false    # Healthy objects excluded
```

---

## 8. ML Classification — Log Templatization (Drain3)

### Log Mini-Stage Pipeline (Filter Before Drain3)

```
Raw log file
    │
    ▼
┌──────────────────────────────────────────────────────────┐
│  MINI-STAGE 3.1 — Branch Selection                       │
│  < 50 MB → ENTIRE_FILE  |  ≥ 50 MB → CHUNK pipeline     │
│  Auto-scale chunk_size / workers by file size tier       │
└──────────────────────────┬───────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────┐
│  MINI-STAGE 3.2 — Line Reading                           │
│  Sequential or parallel chunk reader                     │
└──────────────────────────┬───────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────┐
│  MINI-STAGE 3.3 — Pre-Drain3 Preprocess                  │
│  remove_timestamps() via regex patterns (log_config.json)│
│  Produces: preprocessed line (for Drain3)              │
│            + original line (for output / chronology)    │
└──────────────────────────┬───────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────┐
│  MINI-STAGE 3.4 — Pre-Drain3 Severity Filter ◄── GATE   │
│  parse_log_level() per line:                             │
│    E  = error phrases (FAILED, FATAL, connection refused)│
│    W  = warning phrases (WARN, RETRY, BACKOFF)           │
│    I  = info prefixes / keywords                       │
│    UNKNOWN = unclassified                                │
│  Drain3 runs SEPARATELY per bucket — not on full file    │
└──────────────────────────┬───────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────┐
│  MINI-STAGE 3.5 — Drain3 Template Mining (per level)     │
│  Input: preprocessed lines  |  Output: original lines    │
└──────────────────────────┬───────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────┐
│  MINI-STAGE 3.6 — Template Shift                         │
│  Error/config templates in W/I/UNKNOWN → merge to E     │
└──────────────────────────┬───────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────┐
│  MINI-STAGE 3.7 — Pareto Split (Error level only)        │
│  rare_error (≤15 lines/template) vs highfreq_error (80%)│
└──────────────────────────┬───────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────┐
│  MINI-STAGE 3.8 — Output Write                           │
│  *_rare_error.txt, *_highfreq_error.txt, *_templates.txt│
└──────────────────────────────────────────────────────────┘
```

### Pre-Drain3 Filtering Details

**Timestamp removal (mini-stage 3.3)** strips these patterns before Drain3 clustering:

| Pattern | Example | Purpose |
|---------|---------|---------|
| ISO 8601 | `2024-01-12T14:22:56.123Z` | Normalize time-varying lines |
| Kubernetes | `E0112 14:22:56.789` | Keep level char, drop date/time |
| Systemd/journal | `Jan 12 14:22:56 hostname` | Remove journal prefix |
| Trailing time | `14:22:56.123` | Catch residual timestamps |

**Severity filter (mini-stage 3.4)** uses content-first detection — an Info-prefixed line containing `FAILED` or `connection refused` is classified as **Error**, not Info. This catches the common case where Kubernetes logs errors at Info level.

**Configurable in** `Config/log_config.json`:
```json
"preprocessing": {
  "remove_timestamps": true,
  "timestamp_patterns": ["..."]
},
"output": {
  "levels_to_process": ["E", "W", "I", "UNKNOWN"]
}
```

### How Log Files Are Classified

The log ML classifier uses **Drain3** (a streaming log template miner) to:

1. Parse each log line and extract its structure
2. Group similar lines into **templates** (replacing variable parts with `<*>`)
3. Classify templates by severity
4. Split errors into **rare** (anomalies) and **high-frequency** (common) patterns

### Sample: Rare Error Templates (CRI-O Service)

From `crio_service_rare_error_templates.txt`:

```
# Rare/Priority Error Templates (7 templates)

Template 1  |  cluster_id=3  |  lines=15
  <*> <*> level=warning msg="CNI plugin not yet initialized.
  Ignoring NetworkReady status: false, message: Network plugin
  returns error: no CNI configuration file in
  /etc/kubernetes/cni/net.d/. Has your network provider started?,
  reason: NetworkPluginNotReady"

Template 3  |  cluster_id=7  |  lines=10
  <*> <*> level=error msg="Killing container <*> failed:
  `/usr/bin/crun --root /run/crun --systemd-cgroup kill <*> KILL`
  failed: process not running: No such process\n : exit status 1"
  <*> name=/runtime.v1.RuntimeService/StopContainer

Template 6  |  cluster_id=1  |  lines=3
  <*> <*> level=warning msg="Error encountered when checking
  whether cri-o should wipe containers: open
  /var/run/crio/version: no such file or directory"
```

**Note:** The `<*>` markers represent variable content (timestamps, container IDs, PIDs) that Drain3 has abstracted away, leaving only the structural pattern of the error.

### Sample: High-Frequency Error Templates (CRI-O Service)

From `crio_service_highfreq_error_templates.txt`:

```
# High-Frequency Error Templates (1 templates)

Template 1  |  cluster_id=4  |  lines=295
  <*> <*> level=info msg="Stopping container: <*>
  (timeout: <*> <*> name=/runtime.v1.RuntimeService/StopContainer
```

**Key insight:** This single high-frequency template covers **295 lines** (83.1% of all error-classified CRI-O lines). The 7 rare templates cover only 60 lines (16.9%), but those 7 rare patterns are far more diagnostically valuable.

### Sample: Rare Error Templates (Kubelet Service)

From `kubelet_service_rare_error_templates.txt` (54 templates, first few shown):

```
Template 3  |  cluster_id=2  |  lines=12
  <*> E0112 <*> reflector.go:205] "Failed to watch"
  err="failed to list *v1.Node: nodes <*> is forbidden:
  User \"system:anonymous\" cannot list resource \"nodes\"..."

Template 4  |  cluster_id=19  |  lines=10
  <*> E0112 <*> csi_plugin.go:399] Failed to initialize
  CSINode: error updating CSINode annotation: timed out
  waiting for the condition; caused by: nodes <*> not found

Template 6  |  cluster_id=11  |  lines=9
  <*> E0112 <*> eviction_manager.go:292] "Eviction manager:
  failed to get summary stats" err="failed to get node info:
  node <*> not found"
```

### Log Processing Summary

| Log File                                    | Size     | Lines  | Templates | Rare Errors         | High-Freq Errors     | Time  --|
|---------------------------------------------|----------|--------|-----------|---------------------|----------------------|-------|
| kubelet_service.log                         | 11.57 MB | 46,381 | 249       | 54 templates (1.8%) | 28 templates (98.2%) | 2.97s |
| crio_service.log                            | 5.09 MB  | 18,600 | 84        | 7 templates (16.9%) | 1 template (83.1%)   | 1.53s |
| machine-config-daemon-firstboot_service.log | 0.20 MB  | 1,147  | 111       | 2 templates (100%)  | 0                    | 0.08s |

---

## 9. Chronology of Events — Mini-Stage Pipeline

The **Chronology of Events** is a time-ordered timeline built automatically from YAML timestamps and log line timestamps. It is constructed during Phase 4 (mini-stages 4.3–4.6) and injected into the LLM RCA prompt so the model can produce a structured `## Chronology of Events` section in the final report without reconstructing the timeline from scratch.

### Why Build Chronology Before the LLM?

| Without Chronology | With Chronology |
|--------------------|-----------------|
| LLM must infer event order from scattered snippets | LLM receives a pre-sorted timeline |
| Timestamps buried in 80 KB of compressed data | Dedicated `CHRONOLOGY OF EVENTS` section in prompt |
| Cross-file correlation (kubelet + CRI-O + events) is hard | All sources merged into one sorted list |
| Continuation rounds lose temporal context | Chronology merged and re-sorted across deepening rounds |

### Chronology Mini-Stage Pipeline

```
Classified YAML errors + Log error entries
    │
    ▼
┌──────────────────────────────────────────────────────────┐
│  MINI-STAGE 4.3 — YAML Timestamp Extraction              │
│  Recursive scan of critical_fields for keys containing:  │
│    timestamp, time, created, updated, transition,        │
│    eventTime, observedGeneration                         │
│  Parse ISO 8601 values → sortable timestamp string       │
└──────────────────────────┬───────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────┐
│  MINI-STAGE 4.4 — Log Timestamp Extraction             │
│  Per log error line, try (in order):                     │
│    1. time="2025-12-11T12:24:50.249Z"  (structured)     │
│    2. CRI-O time="2025-12-11 .249Z"    (fractional)     │
│    3. ISO at line start                (plain ISO)       │
│  Source: file name (e.g. crio_service_highfreq_error.txt)│
└──────────────────────────┬───────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────┐
│  MINI-STAGE 4.5 — Dedupe + Sort                          │
│  Key: (timestamp, snippet[:80])                          │
│  Sort: lexicographic on normalized ISO timestamp         │
│  Output: [{timestamp, source, snippet}, ...]           │
└──────────────────────────┬───────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────┐
│  MINI-STAGE 4.6 — Format Chronology Block                │
│  Render: "  {ts}  |  {source}  |  {snippet}"           │
│  Cap: 150 events (first 75 + last 75 if exceeded)        │
│  Injected as section 2 of the RCA prompt                 │
└──────────────────────────┬───────────────────────────────┘
                           ▼
                    LLM RCA prompt
                           ▼
              ## Chronology of Events (in report)
```

### Timestamp Sources

| Source Type | Example Field / Pattern | Normalized Output |
|-------------|-------------------------|-------------------|
| YAML `events.yaml` | `lastTimestamp: 2026-01-12T13:43:07Z` | `2026-01-12T13:43:07` |
| YAML `conditions` | `lastTransitionTime` | `2026-01-12T16:59:56` |
| Log (ISO) | `2026-01-12T13:43:07.123Z level=error` | `2026-01-12T13:43:07` |
| Log (CRI-O) | `time="2026-01-12T13:43:07.249Z"` | `2026-01-12T13:43:07` |
| Log (Kubelet) | `E0112 13:43:07.123456` | `0112-13:43:07` |

### Chronology in the RCA Prompt

When chronology data is available, the LLM prompt includes:

```
2. CHRONOLOGY OF EVENTS (include in your ## Chronology of Events section):
  2026-01-12T13:43:07  |  YAML:events.yaml  |  type=Warning reason=ImageGCFailed ...
  2026-01-12T13:43:07  |  crio_service_rare_error.txt  |  level=info msg="Stopping container..."
  2026-01-12T13:43:08  |  kubelet_service_rare_error.txt  |  DeleteContainer returned error...
```

The LLM is instructed to:
- Include a `## Chronology of Events` section in the RCA report when chronology data was provided
- Merge and deduplicate events across continuation/deepening rounds
- Use the chronology to support the chain-of-events narrative in the executive summary

### Chronology Across Deepening Rounds

When the orchestrator deepens analysis (HIGH → MEDIUM → LOW priority):

1. New YAML/log data from the expanded file set is analyzed
2. `build_chronology_from_payload()` runs on the new data
3. New events are **appended** to the accumulated chronology list
4. The full list is **re-sorted** by timestamp before the next RCA call
5. The deepening prompt includes: `NEW DATA — CHRONOLOGY OF EVENTS (from {tier} priority files)`

This ensures the timeline grows as more files are examined, without losing events from earlier rounds.

### Implementation Reference

| Function | File | Role |
|----------|------|------|
| `build_chronology_from_payload()` | `llm_rca_agent.py` | Extract + merge + sort events |
| `format_chronology_block()` | `llm_rca_agent.py` | Render prompt block (cap 150) |
| `_extract_timestamps_from_value()` | `llm_rca_agent.py` | Recursive YAML timestamp scan |
| `extract_timestamp_from_log()` | `ML_LOG_CLASSIFICATION.py` | Parse log line timestamps (also used for sampling) |
| `sample_lines_by_timestamp()` | `ML_LOG_CLASSIFICATION.py` | First/middle/last sampling per template cluster |

---

## 10. Example: RCA Payload Sent to LLM

The following is the actual payload structure sent to the LLM for root cause analysis. This is saved to `Results/payload.txt` for every run.

### Payload Structure

```
1. USER REPORTED ISSUE (primary focus):
   container image garbage collection is failing to function as expected

2. PREVIOUS RCA (if continuation):
   [Previous round's RCA text for refinement]

3. NEW DATA — CHRONOLOGY OF EVENTS:
   2026-01-12T13:43:07 | crio_service | Stopping container: df5cbe14...
   2026-01-12T13:43:08 | kubelet     | DeleteContainer returned error...
   2026-01-12T16:59:56 | crio_service | Stopping containers across all nodes
   2026-01-12T16:59:57 | kubelet     | Container deletion failures on all nodes

4. YAML ERROR OBJECTS:
   {
     "yaml_errors": {
       "events.yaml": [
         {
           "type": "Warning",
           "reason": "ImageGCFailed",
           "message": "get container status: rpc error: code = NotFound
                       desc = could not find container...",
           "involvedObject": { "kind": "Node", "name": "ip-10-0-97-146..." }
         },
         // ... 5 more error objects
       ]
     }
   }

5. LOG ERROR ENTRIES:
   {
     "log_errors": [
       {
         "file": "kubelet_service_highfreq_error.txt",
         "content": "[Rare + high-frequency error log lines]",
         "original_size": 12137055
       },
       {
         "file": "crio_service_highfreq_error.txt",
         "content": "[CRI-O error lines]",
         "original_size": 5341176
       }
     ]
   }
```

### Compression Statistics (Actual Run)

```
Total input (YAML + logs): 17,774,631 bytes
Payload sent to LLM:           85,815 bytes
Overall compression ratio:     207.13x

Per-file breakdown:
  events.yaml                        yaml     85,813 →  4,041    21.24x
  kubelet_service_highfreq_error.txt log  12,137,055 → 76,466   158.72x
  crio_service_highfreq_error.txt    log   5,341,176 →  2,035  2624.66x
  machine-config-daemon-firstboot... log     210,167 →    262   802.16x
```

---

## 11. Example: Workflow Console Output

The following is a condensed version of the actual console output from a complete run. The full output is saved to `Results/workflow_console.txt`.

```
====================================================================
AGENTIC ORCHESTRATOR — ReAct Loop
====================================================================
Problem: container image garbage collection is failing to function
         as expected
Max iterations: 25

--- Agent iteration 1/25 ---
  [Agent thinks]: I'll help analyze the must-gather bundle to
                  diagnose the container image garbage collection
                  issue. Let me start by checking LLM availability.
  [Tool call] check_llm_availability({})
  [Tool result] success=True, summary=LLM API is available.
                Model: claude-sonnet-4@20250514.
                Context window: 200,000 tokens.

--- Agent iteration 2/25 ---
  [Agent thinks]: Excellent! LLM available. Proceeding with file
                  selection...
  [Tool call] select_files({problem_statement: "container image
              garbage collection..."})

  LLM FILE SELECTION — Raw LLM Response:
    Problem category: Node/Machine Config
    1. [high] host_service_logs/masters/kubelet_service.log
    2. [high] host_service_logs/masters/crio_service.log
    3. [high] .../machineconfigs.yaml
    ...13 files total...

  File availability: 6 of 13 found, 6 not found

--- Agent iteration 3/25 ---
  [Tool call] analyze_logs({file_paths: [kubelet, crio]})

  Processing: kubelet_service.log (11.57 MB)
    [Error] 10,829 logs → 82 templates
    Completed in 2968 ms

  Processing: crio_service.log (5.09 MB)
    [Error] 355 logs → 8 templates
    Completed in 1530 ms

--- Agent iteration 4/25 ---
  [Tool call] analyze_yaml({file_paths: [events.yaml]})

  ML Classification Table:
    Chunk 13 (433-474)   Error   Rare patterns: failed, error
    Chunk 33 (1277-1323) Error   Rare patterns: error, not found
    Chunk 62 (2509-2554) Error   Rare patterns: failed, error
    Chunk 69 (2807-2850) Error   Rare patterns: waiting, degraded
    ...6 errors out of 72 total objects...

--- Agent iteration 5/25 ---
  [Tool call] validate_token_budget({...})
  [Tool result] Token budget OK. 32,597 tokens (available: 182,000)

--- Agent iteration 6/25 ---
  [Tool call] perform_rca({...})
  [Tool result] RCA complete. Tokens: 36270 in / 1346 out.
                Cost: $0.1290

--- Agent iteration 7/25 ---
  [Tool call] evaluate_rca({...})
  [Tool result] confidence=MEDIUM.
                Missing: node resource constraints, disk pressure,
                         machine config daemon operations

--- Agent iteration 8/25 ---
  [Agent thinks]: MEDIUM confidence. Expanding to medium-priority
                  files (machine-config-daemon logs)...
  [Tool call] analyze_logs({file_paths: [mcd-firstboot, mcd-host]})

--- Iterations 9-12: Refine RCA with medium-priority data ---

--- Agent iteration 12/25 ---
  [Tool call] evaluate_rca({...})
  [Tool result] confidence=HIGH. Suggestion: Return final report.

--- Agent iteration 13/25 ---
  [Agent] Returning final answer.

RCA summary written to: Results/rca_summary.txt

====================================================================
LLM API TOKEN & COST SUMMARY
====================================================================
  WITH ML EXTRACTION (current run)
    0. Orchestrator ReAct loop    164,814 in / 6,887 out  $0.5977
    1. File selection               16,330 in /   429 out  $0.0554
    5. RCA (extracted payload)      36,600 in / 1,114 out  $0.1265
    Total                                                  $0.7797

  WITHOUT ML (hypothetical)
    5. RCA (raw input)           4,443,657 in / 1,114 out $13.3477
    Total                                                 $13.4031

  Savings from ML extraction: $12.6234 USD
  Compression ratio: 207.13x
====================================================================
```

---

## 12. Example: Root Cause Analysis (RCA) Report

The following is the actual RCA report produced by the framework:

```
ROOT CAUSE ANALYSIS REPORT
================================================================

User Reported Issue:
  container image garbage collection is failing to function
  as expected

================================================================

## Executive Summary

The key cause for the user's problem is: Container runtime state
inconsistency between kubelet and CRI-O, where containers are
being stopped/removed by CRI-O but kubelet still maintains
references to these containers, causing garbage collection
operations to fail when attempting to query or delete
non-existent containers.

## Chronology of Events

- 2026-01-12T13:43:07: CRI-O begins stopping containers on
  ip-10-0-49-48
- 2026-01-12T13:43:08: Kubelet attempts to delete the same
  containers but fails with "container not found" errors
- 2026-01-12T16:59:56: Multiple simultaneous container stops
  by CRI-O across all three nodes
- 2026-01-12T16:59:57: Corresponding kubelet deletion failures
  on all nodes

## Primary Root Cause(s)

Container Runtime State Synchronization Failure: The fundamental
issue is a breakdown in state synchronization between kubelet
and CRI-O runtime. Evidence includes:

1. Container Status Query Failures: Multiple "NotFound" errors
   when kubelet attempts garbage collection operations
2. Container Deletion Race Conditions: CRI-O stops containers
   successfully, but kubelet's pod_container_deletor fails
3. Cross-Node Pattern: Affects all three master nodes,
   indicating a systemic rather than node-specific issue

## Analysis Coverage

Deepening Rounds: 2 (HIGH → MEDIUM priority)
- Round 1: kubelet + CRI-O + events → MEDIUM confidence
- Round 2: + machine-config-daemon logs → HIGH confidence

Files Analyzed: 5 total
Token Budget: 33,322 of 182,000 (comfortable)
LLM Errors: None
```

---

## 13. Cost Optimization & Compression

### How ML Extraction Saves Money

The framework's local ML classifiers reduce the data volume sent to the LLM by **orders of magnitude**:

```
                          Without ML        With ML         Savings
                          ──────────        ───────         ───────
Input to LLM:            17,774,631 B      85,815 B        207x less
Estimated tokens:        4,443,657         ~33,000         135x less
RCA cost (Phase 5):      $13.35           $0.13           $13.22
Total run cost:          $13.40           $0.78           $12.62
```

### Per-File Compression Ratios

| File                         | Type | Original Size | Payload Size | Compression |
|------------------------------|------|---------------|--------------|-------------|
| events.yaml                  | YAML | 85,813 B      | 4,041 B      | **21x**     |
| kubelet_service errors       | Log  | 12,137,055 B  | 76,466 B     | **159x**    |
| crio_service errors          | Log  | 5,341,176 B   | 2,035 B      | **2,625x**  |
| machine-config-daemon errors | Log  | 210,167 B     | 262 B        | **802x**    |

### Why This Works

1. **YAML pre-Drain3 filter:** Of 72 event objects, wrapper metadata and empty chunks are dropped before Drain3. Of the remaining objects, only 6 are Error anomalies. The other 66 "normal" objects provide no diagnostic value → 12x reduction just from classification.
2. **Log pre-Drain3 filter:** Severity categorization routes 46,381 raw lines into E/W/I/UNKNOWN buckets. Drain3 runs per bucket; Info lines (34,955) are processed separately and typically excluded from the RCA payload → major volume reduction before template mining.
3. **Drain3 compression:** 46,381 raw log lines are reduced to 82 Error templates. Only rare error templates + representative instances are sent → 159x reduction.
4. **Chronology pre-build:** Timestamps extracted locally (no LLM cost) give the RCA engine a ready-made timeline → better chain-of-events narrative without extra tokens.
5. **CRI-O extreme case:** 18,600 lines reduced to 8 error templates → 2,625x reduction.

---

## 14. Error Handling & Resilience

### LLM API Error Categories

The framework classifies LLM failures into categories with specific handling:

| Error Category      | Trigger                                  | Action                                     |
|---------------------|------------------------------------------|--------------------------------------------|
| **API_UNAVAILABLE** | Connection error, DNS failure, SSL error | STOP immediately, report error             |
| **AUTH_ERROR**      | HTTP 401/403                             | STOP immediately, suggest key update       |
| **RATE_LIMITED**    | HTTP 429                                 | Retry once after delay; stop on second hit |
| **OVERLOADED**      | HTTP 503/529                             | Retry once; stop on second hit             |
| **CONTEXT_OVERFLOW**| Payload too large                        | Reduce payload, retry once                 |
| **TIMEOUT**         | Request timed out                        | Retry once; stop on second failure         |
| **UNKNOWN_ERROR**   | Any other error                          | Log error, skip step, continue             |

### Guardrails

- Maximum **25 tool-call iterations** per run
- Maximum **3 deepening rounds** (HIGH → MEDIUM → LOW)
- Mandatory `validate_token_budget` before every `perform_rca` call
- Never fabricate evidence — if a file was not analyzed, it is not cited
- All LLM errors are visible in the final report's "Analysis Coverage" section

---

## 15. Configuration Reference

### config.json

```json
{
  "use_llm_file_selection": true,
  "file_selection_only": false,
  "exit_after_analysis": false,
  "quiet": false,
  "always_include_kubelet_files": false,
  "must_gather_docs_dir": "DataSource",
  "claude": {
    "api_key": "<your-api-key>",
    "api_url": "https://api.anthropic.com",
    "model_id": "claude-sonnet-4@20250514",
    "max_tokens": 16384,
    "verify_ssl": true,
    "price_per_1m_input_tokens": 3.0,
    "price_per_1m_output_tokens": 15.0
  },
  "agent": {
    "max_iterations": 25,
    "max_deepening_rounds": 3
  }
}
```

| Setting                        | Description                                             | Default |
|--------------------------------|---------------------------------------------------------|---------|
| `use_llm_file_selection`       | Use LLM for intelligent file selection vs. default list | `true`  |
| `file_selection_only`          | Run only file selection, skip analysis/RCA              | `false` |
| `exit_after_analysis`          | Exit after YAML/log analysis, skip LLM RCA              | `false` |
| `quiet`                        | Suppress verbose console output                         | `false` |
| `always_include_kubelet_files` | Always add kubelet logs regardless of LLM suggestion    | `false` |
| `max_iterations`               | Hard cap on ReAct loop iterations                       | `25`    |
| `max_deepening_rounds`         | Maximum priority expansion rounds                       | `3`     |

---

## 16. Key Metrics & KPIs

### Per-Run Metrics

| Metric                | Typical Value | Where to Find                  |
|-----------------------|---------------|--------------------------------|
| Total execution time  | 3-5 minutes   | Console output                 |
| ReAct loop iterations | 10-15         | `workflow_console.txt`         |
| Files analyzed        | 3-8           | RCA report "Analysis Coverage" |
| Deepening rounds      | 1-2           | RCA report "Analysis Coverage" |
| Final confidence      | HIGH          | `evaluate_rca` result          |
| Total LLM cost        | $0.50 - $1.00 | Cost table                     |
| Compression ratio     | 100x - 2600x  | Cost table                     |
| ML extraction savings | $10 - $15     | Cost table                     |

### Phase Timing Breakdown

| Phase                    | Typical Duration | Notes                              |
|--------------------------|------------------|------------------------------------|
| Phase 0: LLM check       | < 1s             | Network latency only               |
| Phase 1: File selection  | 2-5s             | 1 LLM call                         |
| Phase 2: YAML processing | 1-3s             | Local ML; includes pre-Drain3 filter + Drain3 |
| Phase 3: Log processing  | 1-5s             | Local ML; pre-filter + Drain3 per severity level |
| Phase 4: Aggregation + chronology | < 1s    | In-memory merge + timestamp extraction |
| Phase 5: RCA             | 5-15s            | 1-2 LLM calls (includes chronology in prompt) |
| Phase 6: Cost calc       | < 1s             | Local computation                  |

### Log Mini-Stage Timing (typical 11 MB kubelet log)

| Mini-Stage | Duration | Notes |
|------------|----------|-------|
| 3.1 Branch selection | < 1ms | Auto: CHUNK_PARALLEL |
| 3.2–3.4 Read + preprocess + filter | ~800ms | 60–70% of lines classified as Info (skipped by Drain3 Error pass) |
| 3.5 Drain3 mining | ~1.5s | Per severity bucket |
| 3.6–3.8 Shift + Pareto + write | ~600ms | Rare/highfreq file output |
| **Total** | **~3s** | For 11.57 MB kubelet_service.log |

---

## 17. Glossary

| Term                       | Definition                                                                                                                     |
|----------------------------|--------------------------------------------------------------------------------------------------------------------------------|
| **Must-gather**            | A diagnostic dump collected from an OpenShift/Kubernetes cluster containing YAML configs, logs, events, and metrics            |
| **RCA**                    | Root Cause Analysis — a structured report identifying why an issue occurred                                                    |
| **ReAct loop**             | Reasoning + Acting pattern where the AI thinks about what to do, takes an action (tool call), observes the result, and repeats |
| **Drain3**                 | An online log template mining algorithm that groups similar log lines into patterns                                            |
| **Critical fields**        | The specific YAML paths (e.g., `status.conditions`, `reason`, `message`) that are diagnostically relevant for a given file     |
| **Chunking**               | Splitting a multi-object YAML file into individual Kubernetes objects for per-object classification                            |
| **Incremental deepening**  | The strategy of starting with high-priority files and only expanding to lower priorities if the RCA needs more evidence        |
| **Token budget**           | The maximum number of tokens that can be sent to the LLM in a single call (limited by model context window)                    |
| **Error (classification)** | A rare anomaly pattern detected by the ML classifier — highest diagnostic value                                                |
| **Majority Error**         | A common negative pattern — many objects share this error, making it less unusual                                              |
| **CONFIG**                 | A configuration change detected in YAML or log data                                                                            |
| **Majority**               | A normal/healthy pattern — excluded from RCA payload by default                                                                |
| **Compression ratio**      | The ratio of original data size to the payload sent to the LLM (higher = more savings)                                         |
| **Mini-stage**             | A discrete, named sub-step within a phase (e.g., 3.4 = pre-Drain3 severity filter) with a clear input/output                     |
| **Pre-Drain3 filter**      | Any cleaning, categorization, or dropping of data that happens *before* Drain3 template mining runs                              |
| **Chronology of Events**   | A time-ordered list of `{timestamp, source, snippet}` built from YAML and log timestamps before the LLM RCA call               |
| **Chain of events**        | The narrative timeline in the RCA report; derived from the chronology block injected into the LLM prompt                         |
| **Template shift**         | Moving entire Drain3 template clusters from Warning/Info/Unknown levels into Error or Config buckets based on content patterns   |
| **Pareto split**           | Dividing Error templates into rare (anomalous) vs high-frequency (common) using line-count threshold or 80% cumulative coverage   |

---

## Output Files Reference

| File                             | Location   | Contents                                   |
|----------------------------------|------------|--------------------------------------------|
| `rca_summary.txt`                | `Results/` | Final RCA report with cost table           |
| `payload.txt`                    | `Results/` | Exact prompt sent to LLM for RCA           |
| `workflow_console.txt`           | `Results/` | Full console output of the run             |
| `hardcoded_files.json`           | `Results/` | Files selected by LLM for analysis         |
| `errors_aggregate.json`          | Root dir   | Aggregated YAML classified objects         |
| `*_rare_error.txt`               | Root dir   | Rare error log lines per service           |
| `*_highfreq_error.txt`           | Root dir   | High-frequency error log lines per service |
| `*_rare_error_templates.txt`     | Root dir   | Drain3 templates for rare errors           |
| `*_highfreq_error_templates.txt` | Root dir   | Drain3 templates for high-frequency errors |
| `*_Warning.txt`                  | Root dir   | Warning-level log lines per service        |
| `*_Information.txt`              | Root dir   | Info-level log lines per service           |
| `*_Unknown.txt`                  | Root dir   | Unclassified log lines per service         |
| `agent_memory.json`              | `Config/`  | Session state (auto-managed)               |
| `chronology` (in session results)| `Results/` | Time-ordered events list (also in RCA payload) |

---


