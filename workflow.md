# IntelliAide RCA Tool — Workflow & Architecture Reference

> Branch: `feature/rca-portal-multisession`
> Last updated: March 2026

---

## Table of Contents

1. [High-Level Architecture](#1-high-level-architecture)
2. [What Changed From the Previous Pull](#2-what-changed-from-the-previous-pull)
3. [Job Queue & Thread Pool](#3-job-queue--thread-pool)
4. [Per-Session Result Storage](#4-per-session-result-storage)
5. [End-to-End Job Submission Flow](#5-end-to-end-job-submission-flow)
6. [Hydra Integration Flow](#6-hydra-integration-flow)
7. [Deeper Analysis (Feedback) Flow](#7-deeper-analysis-feedback-flow)
8. [Frontend Routing — Portal vs LiveAnalysis](#8-frontend-routing--portal-vs-liveanalysis)
9. [RCA Report & Export Flow](#9-rca-report--export-flow)
10. [API Reference](#10-api-reference)
11. [Deployment](#11-deployment)

---

## 1. High-Level Architecture

```
Browser
 ├── /                    → Portal (Frontend 2) — landing page, job submission, tracking board
 └── /analysis?session=   → LiveAnalysis (Frontend 1) — live progress, console, RCA steps
     /rca-report?session= → RCAReport — rendered report tabs (High / Medium / Low)

       ▼ HTTP (proxied via Nginx in prod, direct in dev)

FastAPI  api.py  (port 8000)
 ├── POST /case-attachments   — Hydra: list/download must-gather archive
 ├── POST /upload-must-gather — receive user-uploaded archive
 ├── POST /analyze            — enqueue analysis job, return session_id immediately
 ├── GET  /status/{session}   — live progress polling
 ├── GET  /sessions           — all session statuses (jobs board)
 ├── GET  /rca-stage/{stage}  — read RCA_{High,Medium,Low}.txt
 ├── GET  /rca-bundle-zip     — stream all stages as ZIP
 ├── POST /feedback           — satisfactory / trigger deeper analysis
 └── POST /cancel/{session}   — signal cancellation

       ▼ Python in-process

OrchestratorAgent  (Main-program/orchestrator_agent.py)
 └── execute_workflow()
      Phase 1 — file selection
      Phase 2 — YAML processing
      Phase 3 — log processing
      Phase 4 — data aggregation
      Phase 5 — RCA via LLM (Google Vertex AI)
      Writes → RCA_High.txt  RCA_Medium.txt  RCA_Low.txt
```

### Directory Layout (runtime)

```
Version_V1/
├── api.py                        backend entrypoint
├── app_paths.py                  path helpers (frozen/dev dual-mode)
├── Config/
│   ├── config.json               LLM keys, must_gather_base_dir
│   └── agent_memory.json         orchestrator session history
├── Results/                      shared flat results (current job)
│   ├── RCA_High.txt
│   ├── RCA_Medium.txt
│   ├── RCA_Low.txt
│   ├── rca_summary.txt
│   ├── workflow_console.txt
│   └── <session_id>/             per-session snapshot (immutable after job)
│       ├── RCA_High.txt
│       ├── RCA_Medium.txt
│       └── ...
└── utils/
    └── utils.py                  ZIP-creation helper

/tmp/must-gather/                 MUST_GATHER_EXTRACT_DIR (configurable)
├── _uploads/                     received archives (upload_<timestamp>.zip/.tar.gz/.tgz)
└── <session_id>/                 extracted must-gather tree per job
```

---

## 2. What Changed From the Previous Pull

### 2.1 Portal Landing Page (Frontend 2)

**File:** `Frontend/src/components/Portal.tsx`

A brand-new page at route `/` replaces the old direct-to-LiveAnalysis landing.

| Feature | Detail |
|---|---|
| **New Submittal form** | Left column: issue description textarea + drag-and-drop file zone + case number input (Hydra path) |
| **Jobs Tracking Board** | Right column: live table of all submitted sessions — status badges, progress %, direct links to LiveAnalysis and RCAReport |
| **Immediate board refresh** | After `startNewAnalysis()` resolves, `getSessions()` fires immediately (not waiting for the 1.5 s poll cycle) |
| **`isSubmitting` state** | Button shows "Creating job…" with spinner from click until the new session appears in the board |
| **Clear button** | `POST /clear-sessions` removes all completed / error / cancelled sessions from the in-memory board |
| **Poll interval** | 1 500 ms (reduced from 3 000 ms for faster board updates) |
| **Deep-link columns** | "View Progress" opens `/analysis?session=<id>` in a new tab; "View Report" opens `/rca-report?session=<id>` in a new tab |

### 2.2 LiveAnalysis Stripped of Jobs Board (Frontend 1)

**File:** `Frontend/src/components/LiveAnalysis.tsx`

- Removed the embedded "My Jobs & Tracking" board (state, polling, JSX)
- Added `?session=` URL parameter support via `useSearchParams`
- On mount with a `?session=` param: fetches session status and calls `resumeSession()` in `AnalysisContext` to attach the UI to an already-running or completed analysis
- **Session Info Banner**: a top strip showing session ID, issue description, filename / case number — visible whenever a deep-linked session is loaded

### 2.3 AnalysisContext — `resumeSession` & `isDeepening`

**File:** `Frontend/src/contexts/AnalysisContext.tsx`

| Addition | Purpose |
|---|---|
| `isDeepening: boolean` | Distinguishes a deeper-analysis (feedback) run from a fresh analysis so the RCAReport does not wipe existing stage tabs |
| `resumeSession(sid, status)` | Attaches polling to an existing session opened via deep-link without triggering a new `/analyze` call |
| Commented-out `initSession()` on mount | The old code called `POST /init` on every page load which deleted all RCA files. Cleanup now happens inside `run_workflow()` on the backend at the start of each new job only |

### 2.4 App Routing Restructured

**File:** `Frontend/src/App.tsx`

```
Before  single layout with sidebar  "/"  →  LiveAnalysis

After
  "/"      →  <Portal />       (no sidebar — full-page landing)
  "/*"     →  <AppShell />     (sidebar layout)
    "/analysis"     →  <LiveAnalysis />
    "/rca-report"   →  <RCAReport />
    "/analytics"    →  <Analytics />
    ...
```

### 2.5 Per-Session Result Snapshots

**Files:** `Version_V1/api.py`, `Version_V1/app_paths.py`

- `get_session_results_dir(session_id)` creates and returns `Results/<session_id>/`
- After every successful analysis (initial and deepening), RCA files are **copied** from the shared `Results/` folder into `Results/<session_id>/`
- All report-serving endpoints (`/rca-stage`, `/rca-summary`, `/workflow-console`, `/rca-bundle-zip`) accept an optional `?session=` query param and read from the per-session snapshot when available
- This prevents a concurrent user's analysis from overwriting a completed report

### 2.6 Deepening Snapshot & High-Priority Merge

**File:** `Version_V1/api.py` — `run_deepening()`

- `run_deepening` now snapshots its results to `Results/<deepening_session_id>/`
- Before snapshotting, if `RCA_High.txt` is missing from shared `Results/` but exists in the original session's snapshot, it is **merged back** into `Results/` so it is included in the deepening snapshot
- `FeedbackRequest` model accepts `original_session_id` so the backend knows which snapshot to merge from
- The frontend passes `originalSessionRef.current` (captured before deepening started) in the `submitFeedback` call

### 2.7 RCA Export — Individual Stages + ZIP Bundle

**Files:** `Version_V1/api.py`, `Frontend/src/services/api.ts`, `Frontend/src/components/RCAReport.tsx`, `Frontend/src/utils/exportUtils.ts`, `Version_V1/utils/utils.py`

| Export type | Mechanism |
|---|---|
| PDF (current stage) | Browser `window.print()` targeting `#rca-export-root` |
| DOC (current stage) | Stage text wrapped in Word-compatible HTML, streamed as `.docx` |
| ODT (current stage) | Same as DOC, streamed as `.odt` |
| ZIP bundle (all stages) | `GET /rca-bundle-zip?session=<id>` — backend creates an in-memory ZIP of all `RCA_*.txt` files and streams it; frontend anchor-click with 1 s delayed `revokeObjectURL` |

### 2.8 `.tgz` Archive Bug Fix

**File:** `Version_V1/api.py` — `_archive_suffix()`

The function previously did not recognise `.tgz` as a valid extension. Files were saved without an extension, causing `_extract_local_upload` to fail with "Upload not found or expired". Fixed by explicitly mapping `.tgz → .tgz` alongside `.tar.gz` and `.zip`.

### 2.9 Multi-Attachment Selection for Case Number

When `POST /case-attachments` finds more than one must-gather archive for a case:

```json
{
  "status": "multiple",
  "attachments": [
    { "uuid": "...", "filename": "...", "size_bytes": 123456, "created": "2026-03-15" }
  ]
}
```

The Portal renders a selection list; the user picks one, which triggers a second `POST /case-attachments` with `attachment_uuid` to download it.
If only one attachment exists it is downloaded automatically and the result is `{ "status": "downloaded", "local_file_id": "...", "filename": "..." }`.

### 2.10 Backend Dockerfile — `utils/` Added

**File:** `Version_V1/Dockerfile`

`COPY utils/ ./utils/` was added so `utils/utils.py` (the in-memory ZIP helper) is available inside the container image.

---

## 3. Job Queue & Thread Pool

### Design Rationale

The orchestrator writes to `sys.stdout` extensively. Running two instances simultaneously corrupts the shared console log. A **single worker thread** backed by `queue.Queue` ensures jobs are processed strictly one at a time while the HTTP API stays non-blocking.

### Structures

```python
_JOB_QUEUE:     queue.Queue          # unbounded FIFO of callables
_WORKER_THREAD: threading.Thread     # single daemon thread ("analysis-worker")
_WORKER_LOCK:   threading.Lock       # guards thread creation
_workflow_status: dict[str, dict]    # in-memory session tracking (lives for process lifetime)
_cancel_events:   dict[str, Event]   # per-session cancellation signals
```

### Worker Lifecycle

```
_ensure_worker()
    └── if _WORKER_THREAD is None or not alive:
         start new daemon thread running _job_worker()

_job_worker()
    loop:
        job_fn = _JOB_QUEUE.get(timeout=120)   ← blocks up to 2 min
        job_fn()                                 ← runs run_workflow() synchronously
        _JOB_QUEUE.task_done()
    (exits after 120 s idle; _ensure_worker() restarts it on the next submission)
```

### POST /analyze — What Happens Synchronously vs Asynchronously

```
POST /analyze  (on the HTTP request thread)
│
├─ SYNC  _extract_local_upload()    ← unzips archive into /tmp/must-gather/<session_id>/
│                                     (this is the main source of submission latency)
├─ SYNC  Register session in _workflow_status as "queued"
├─ SYNC  _JOB_QUEUE.put(run_workflow)
├─ SYNC  _ensure_worker()
└─ SYNC  Return { session_id, status: "queued" }   ← HTTP response sent

run_workflow()  (on the worker thread — starts when the worker picks it up)
│
├─ Update status → "running"
├─ Clear shared Results/ folder
├─ OrchestratorAgent.execute_workflow()
│   ├─ Phase 1  file_selection     progress 15–40 %
│   ├─ Phase 2  yaml_processing    progress 40–55 %
│   ├─ Phase 3  log_processing     progress 55–70 %
│   ├─ Phase 4  data_aggregation   progress 70–75 %
│   └─ Phase 5  rca_analysis       progress 75–100 %
│       Writes: RCA_High.txt  RCA_Medium.txt  RCA_Low.txt
│               rca_summary.txt  workflow_console.txt
├─ Update status → "completed"
└─ Snapshot: copy RCA files to Results/<session_id>/
```

### Progress Reporting

Inside `run_workflow()` a `progress_callback(event_type, message, data)` closure is passed to the orchestrator. On every known event (e.g. `"files_identified"`, `"llm_start"`, `"llm_done"`) the callback updates `_workflow_status[session_id]` in-memory. The frontend polls `GET /status/<session_id>` every 2 s to read these updates and re-render the progress bar and step list.

### Cancellation

`POST /cancel/<session_id>` sets a `threading.Event` stored in `_cancel_events[session_id]`. The `progress_callback` checks the event on every call; if set it raises `AnalysisCancelledError`, which propagates up through the orchestrator and is caught by `run_workflow()` which marks the session as `"cancelled"`.

### Queue Depth Behaviour

- If a second job is submitted while the first is running, it is placed in the queue and shown as `"queued"` in the jobs board with position `(queue_depth + 1)`
- The worker picks it up automatically when the first job finishes
- Multiple users can submit simultaneously — all jobs queue and run in FIFO order

---

## 4. Per-Session Result Storage

### Problem Solved

The orchestrator writes all output to a single shared `Results/` directory. Without snapshots, User B starting a new job while User A is reading their report causes User A's files to be overwritten mid-read.

### Directory Structure

```
Version_V1/Results/
│
├── RCA_High.txt            ← shared, overwritten by every new job
├── RCA_Medium.txt
├── RCA_Low.txt
├── rca_summary.txt
├── workflow_console.txt
│
├── session_20260331_062313_1068123/     ← immutable snapshot for this session
│   ├── RCA_High.txt
│   ├── RCA_Medium.txt
│   ├── RCA_Low.txt
│   ├── rca_summary.txt
│   └── workflow_console.txt
│
└── session_20260331_070544_2ab1f9/      ← another user's snapshot
    └── ...
```

### `_resolve_results_dir(session)` — Resolution Logic

```python
def _resolve_results_dir(session):
    if session:
        snap = get_session_results_dir(session)   # Results/<session>/
        if any(snap.iterdir()):                   # snapshot has content
            return snap
    return get_results_dir()                      # fall back to shared Results/
```

All report endpoints call `_resolve_results_dir(session)` so that:

| Caller | `?session=` | Reads from |
|---|---|---|
| Live analysis tab | absent | Shared `Results/` (real-time) |
| Deep-linked report tab | `session_XYZ` | `Results/session_XYZ/` (snapshot) |
| Portal "View Report" | `session_XYZ` | `Results/session_XYZ/` (snapshot) |

### Snapshot Timing

```
run_workflow() completes successfully
    │
    └── for fname in [RCA_High.txt, RCA_Medium.txt, RCA_Low.txt,
                      rca_summary.txt, workflow_console.txt]:
             shutil.copy2(Results/fname, Results/<session_id>/fname)
```

### Deepening Snapshot Merge

When a deeper analysis run completes:

```
1. For each of [RCA_High.txt, RCA_Medium.txt, RCA_Low.txt]:
     if file is MISSING from Results/  AND  exists in Results/<original_session_id>/:
         shutil.copy2(Results/<original_session_id>/fname, Results/fname)
         (restore it so it is included in the deepening snapshot)

2. Copy all available RCA files to Results/<deepening_session_id>/
```

This guarantees that High-priority results from the first pass are never lost when Medium/Low deepening runs rewrite `Results/` only partially.

---

## 5. End-to-End Job Submission Flow

### Path A — File Upload

```
User drops .zip / .tar.gz on Portal
    │
    └── Portal: setSelectedFile(file)

User fills Issue Description, clicks "Submit Job"
    │
    └── Portal.handleSubmit()
         ├── setIsSubmitting(true)
         │
         └── AnalysisContext.startNewAnalysis(issue, caseNum)
              │
              ├── setIsUploading(true)
              ├── POST /upload-must-gather  (XHR with progress %)
              │    └── Backend saves to UPLOAD_DIR/upload_<ts>.zip
              │
              ├── POST /analyze { user_query, local_file_id }
              │    ├── _extract_local_upload() → /tmp/must-gather/<session_id>/
              │    ├── Register session as "queued" in _workflow_status
              │    ├── _JOB_QUEUE.put(run_workflow)
              │    ├── _ensure_worker()
              │    └── Return { session_id, status: "queued" }
              │
              └── startRealPolling(session_id)  → GET /status every 2 s

         ├── getSessions() immediately → board refreshes without waiting for poll
         ├── Clear form fields
         └── setIsSubmitting(false)
```

### Path B — Case Number (Hydra)

```
User enters case number, clicks "Fetch"
    │
    └── POST /case-attachments { case_number }
         ├── _hydra_fetch_token()   ← SSO client-credentials
         ├── GET hydra/rest/cases/<case>/attachments
         └── Filter by _MG_PATTERN  (must-gather*.zip/.tar.gz/.tgz/...)

         ┌── Single found ──────────────────────────────────────────────┐
         │   Download → UPLOAD_DIR/upload_<ts>.tgz                      │
         │   Return { status: "downloaded", local_file_id, filename }    │
         │   Portal: setCaseStep("ready"), shows filename                │
         └──────────────────────────────────────────────────────────────┘

         ┌── Multiple found ────────────────────────────────────────────┐
         │   Return { status: "multiple", attachments: [...] }           │
         │   Portal: setCaseStep("selecting"), shows selection list       │
         │   User picks one                                               │
         │   POST /case-attachments { case_number, attachment_uuid, ...} │
         │   Download → UPLOAD_DIR/upload_<ts>.tgz                      │
         │   Portal: setCaseStep("ready"), shows filename                │
         └──────────────────────────────────────────────────────────────┘

User clicks "Start Analysis"
    └── Same as Path A from  POST /analyze  onwards
        (archive already in UPLOAD_DIR — extraction in /analyze is fast for cached files)
```

---

## 6. Hydra Integration Flow

### Authentication

```
POST https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token
  Authorization: Basic base64(HYDRA_CLIENT_ID:HYDRA_CLIENT_SECRET)
  Body: grant_type=client_credentials&scope=api.customer_case_management

  → { access_token: "eyJ..." }
```

`HYDRA_CLIENT_ID` and `HYDRA_CLIENT_SECRET` are injected via the OpenShift `must-gather-app-config` Secret.

### Attachment Listing

```
GET https://access.redhat.com/hydra/rest/cases/<case_number>/attachments
Authorization: Bearer <access_token>

Response normalised to a flat list, then filtered by:
  _MG_PATTERN = must[-_]?gather.*\.(zip|tar\.gz|tgz|tar\.bz2|tar)$   (case-insensitive)

Each kept entry: { uuid, filename, size_bytes, created }
```

### Download

```
GET https://attachments.access.redhat.com/hydra/rest/cases/<case>/attachments/<uuid>
Authorization: Bearer <access_token>

Streamed in 1 MB chunks → /tmp/case_<case>/<filename>
Moved to UPLOAD_DIR/upload_<timestamp><suffix>
```

### Offline Development Stub

```bash
export HYDRA_STUB_DIR=/path/to/dir/with/must-gather.tar.gz
```

When set, the live Hydra API is bypassed entirely. The directory is scanned for `must-gather*` archives; filenames double as UUIDs. Useful on dev machines without VPN.

---

## 7. Deeper Analysis (Feedback) Flow

```
User views RCAReport, clicks "Not Satisfied"
    └── showFeedbackForm = true  (no API call yet)

User types feedback, clicks "Submit"
    │
    └── RCAReport.handleSubmitFeedback()
         │
         └── POST /feedback {
               session_id:         current session,
               satisfactory:       false,
               feedback_text:      user's text,
               original_session_id: session ID from BEFORE deepening started
             }
              │
              ├── Backend: lookup orchestrator session from agent_memory
              ├── Register api_sid in _workflow_status as "running" immediately
              ├── Spawn daemon thread: run_deepening()
              └── Return { status: "deepening", session_id: api_sid }  ← HTTP response

Frontend: result.status === "deepening"
    └── AnalysisContext.startDeepening(session_id)
         ├── setIsRunning(true), setIsDeepening(true)
         └── startRealPolling(session_id)   ← same 2 s poll loop

run_deepening()  (background thread)
    │
    ├── Redirect stdout → append to workflow_console.txt
    ├── orch.continue_rca_with_feedback(orch_sid, False, feedback_text=...)
    │    └── Orchestrator runs the next priority tier
    │        (e.g. Medium after initial High-only run)
    │
    ├── On success:
    │    ├── Merge RCA_High.txt from original snapshot into Results/ if missing
    │    ├── shutil.copy2 all RCA files → Results/<deepening_session_id>/
    │    └── _workflow_status[api_sid] → "completed"
    │
    ├── On AnalysisCancelledError → "cancelled"
    └── On Exception             → "error"

Frontend on "completed":
    ├── isRunning → false, isDeepening → false
    ├── justFinishedDeepeningRef = true
    └── RCAReport: post-deepening burst poll
         Retries loadStages() every 2 s for up to 30 s
         Stops early when more stage tabs appear than before deepening
```

### What `isDeepening` Controls in the UI

| `isDeepening` state | Behaviour |
|---|---|
| `true` | Blue "Deeper analysis running" banner shown in RCAReport; existing stage tabs preserved; full-screen spinner suppressed |
| `false` (transition from `true`) | Burst poll fires to fetch new/updated stages |

---

## 8. Frontend Routing — Portal vs LiveAnalysis

### Route Table

| URL | Component | Sidebar |
|---|---|---|
| `/` | `Portal` | No |
| `/analysis` | `LiveAnalysis` | Yes |
| `/analysis?session=<id>` | `LiveAnalysis` (resume mode) | Yes |
| `/rca-report` | `RCAReport` (context session) | Yes |
| `/rca-report?session=<id>` | `RCAReport` (deep-linked) | Yes |
| `/analytics` | `Analytics` | Yes |
| `/logging-tracing` | `LoggingTracing` | Yes |
| `/remediation` | `Remediation` | Yes |
| `/console` | `Console` | Yes |

### Deep-Link Behaviour — `/analysis?session=<id>`

```
LiveAnalysis mounts
    │
    ├── useSearchParams() → linkedSessionId = "session_20260331_..."
    ├── GET /status/<linkedSessionId>
    │
    └── resumeSession(id, status):
         ├── "queued" / "running"  → setIsRunning(true), startRealPolling(id)
         ├── "completed"           → setProgress(100), fetchConsole()
         └── "error"               → show error state

    Session Info Banner renders:
        Session ID | Issue description | Filename or Case number
```

### Deep-Link Behaviour — `/rca-report?session=<id>`

```
RCAReport mounts
    │
    ├── reportSession = searchParams.get("session") || contextSessionId
    ├── loadStages(reportSession)
    │    ├── GET /rca-stage/High?session=<id>
    │    ├── GET /rca-stage/Medium?session=<id>
    │    └── GET /rca-stage/Low?session=<id>
    │
    └── If stages empty → retry every 3 s up to 10 times
        (handles race where snapshot not yet written)
```

### Portal Jobs Board — Link Logic

| Session status | "Report URL" column |
|---|---|
| `queued` | N/A |
| `running` / `in_progress` | "View Progress" → `/analysis?session=<id>` (new tab) |
| `completed` / `rca_completed` | "View Report" → `/rca-report?session=<id>` (new tab) |
| `error` / `cancelled` | Error badge |

---

## 9. RCA Report & Export Flow

### Stage Loading

```
RCAReport.loadStages(session)
    │
    ├── Promise.all([
    │       GET /rca-stage/High?session=<id>,
    │       GET /rca-stage/Medium?session=<id>,
    │       GET /rca-stage/Low?session=<id>
    │   ])
    │
    └── Keep only stages with non-empty body text → render as tabs
        204 response = stage not yet written for this priority tier
```

### Export Options

| Button | Mechanism | Output |
|---|---|---|
| Export as PDF | `window.print()` on `#rca-export-root` | Browser PDF dialog |
| Export as DOC | Stage text → Word-compatible HTML, streamed | `.docx` download |
| Export as ODT | Same as DOC | `.odt` download |
| Download All RCAs (ZIP) | `GET /rca-bundle-zip?session=<id>` | `RCA_Reports_Bundle.zip` |

### ZIP Bundle Download Fix

The frontend:
1. Creates a temporary `<a>` element
2. Appends it to `document.body`
3. Calls `a.click()`
4. Removes the element
5. Calls `URL.revokeObjectURL(url)` inside `setTimeout(..., 1000)` to ensure the browser initiates the download before the blob URL is invalidated

---

## 10. API Reference

| Method | Path | Body / Params | Response |
|---|---|---|---|
| `GET` | `/health` | — | `{ status: "ok" }` |
| `GET` | `/pod-status` | — | `{ busy, active_session_id }` |
| `POST` | `/upload-must-gather` | multipart `file` | `{ local_file_id, filename }` |
| `POST` | `/case-attachments` | `{ case_number, attachment_uuid?, filename? }` | `{ status: "downloaded"\|"multiple", ... }` |
| `POST` | `/analyze` | `{ user_query, local_file_id?, must_gather_root_folder?, case_number? }` | `{ session_id, status: "queued" }` |
| `GET` | `/status/{session_id}` | — | `{ session_id, status, phase, progress, message, ... }` |
| `GET` | `/sessions` | — | `[ JobSession, ... ]` |
| `POST` | `/cancel/{session_id}` | — | `{ status: "cancelling" }` |
| `POST` | `/clear-sessions` | — | `{ status: "ok" }` |
| `GET` | `/workflow-console` | `?session=` | plain text log |
| `GET` | `/rca-summary` | `?session=` | plain text summary |
| `GET` | `/rca-stage/{stage}` | `?session=` | plain text (204 if not ready) |
| `GET` | `/rca-bundle-zip` | `?session=` | `application/zip` stream |
| `POST` | `/feedback` | `{ session_id, satisfactory, feedback_text?, original_session_id? }` | `{ status: "ok"\|"deepening", session_id? }` |
| `GET` | `/agent-memory` | — | full `agent_memory.json` |
| `GET` | `/config` | — | `config.json` (API key masked) |
| `POST` | `/init` | — | `{ status: "ok"\|"busy" }` |
| `POST` | `/clear` | — | `{ status: "ok" }` |

### `JobSession` Object

```typescript
interface JobSession {
  session_id:        string;
  status:            "queued" | "running" | "completed" | "rca_completed" | "error" | "cancelled";
  phase:             string;
  progress:          number;      // 0–100
  message:           string;
  case_number?:      string;
  problem_statement?: string;
  created_at?:       string;      // ISO 8601
  filename?:         string;
  rca_summary?:      string;
  error?:            string;
}
```

---

## 11. Deployment

### Build & Push Images

```bash
podman login quay.io -u rh-ee-skini -p <password>

podman build -t quay.io/rh-ee-cdate/must-gather-api:latest      ./Version_V1
podman build -t quay.io/rh-ee-cdate/must-gather-frontend:latest  ./Frontend

podman push quay.io/rh-ee-cdate/must-gather-api:latest
podman push quay.io/rh-ee-cdate/must-gather-frontend:latest
```

### Full Redeploy (clean slate)

```bash
chmod +x k8s/*.sh
./k8s/redeploy-openshift.sh
```

Equivalent manual steps (prefer the script — it syncs secrets correctly):

```bash
./k8s/delete-openshift.sh
podman build -t quay.io/rh-ee-cdate/must-gather-frontend:latest ./Frontend
podman build -t quay.io/rh-ee-cdate/must-gather-api:latest ./Version_V1
podman push quay.io/rh-ee-cdate/must-gather-frontend:latest
podman push quay.io/rh-ee-cdate/must-gather-api:latest
./k8s/deploy-openshift.sh
```

Do **not** `oc apply -f k8s/secret.yaml` or `k8s/app-secret.yaml` — use `./k8s/sync-secrets-from-config.sh` instead.

### Rolling Update (changed files only)

```bash
# Backend changed
podman build -t quay.io/rh-ee-cdate/must-gather-api:latest ./Version_V1
podman push  quay.io/rh-ee-cdate/must-gather-api:latest
oc rollout restart deploy/must-gather-api      -n must-gather
oc rollout status  deploy/must-gather-api      -n must-gather

# Frontend changed
podman build -t quay.io/rh-ee-cdate/must-gather-frontend:latest ./Frontend
podman push  quay.io/rh-ee-cdate/must-gather-frontend:latest
oc rollout restart deploy/must-gather-frontend -n must-gather
oc rollout status  deploy/must-gather-frontend -n must-gather
```

### Environment Variables (API Pod)

| Variable | Source | Default | Purpose |
|---|---|---|---|
| `MUST_GATHER_EXTRACT_DIR` | Dockerfile | `/tmp/must-gather` | Root for uploads and extraction |
| `HYDRA_CLIENT_ID` | `must-gather-app-config` Secret | — | Hydra OAuth2 client ID |
| `HYDRA_CLIENT_SECRET` | `must-gather-app-config` Secret | — | Hydra OAuth2 client secret |
| `HYDRA_STUB_DIR` | dev only | `""` | Path to local archive for offline Hydra testing |
| `GOOGLE_APPLICATION_CREDENTIALS` | `gcloud-adc-secret` | — | GCP ADC for Vertex AI (LLM calls) |

### OpenShift Kubernetes Resources

| File | Resource | Purpose |
|---|---|---|
| `k8s/namespace.yaml` | Namespace `must-gather` | Isolation |
| `k8s/configmap.yaml` | ConfigMap | Non-secret app config |
| `k8s/sync-secrets-from-config.sh` | Secrets `gcloud-adc-secret`, `must-gather-app-config` | Vertex SA + config.json (from `Version_V1/Config/config.json`) |
| `k8s/api-deployment.yaml` | Deployment + Service | FastAPI backend pod |
| `k8s/frontend-deployment.yaml` | Deployment + Service | Nginx + React build |
| `k8s/openshift-routes.yaml` | Routes | Public HTTPS endpoints |
