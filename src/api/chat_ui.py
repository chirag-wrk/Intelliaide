"""
Simple Chat UI for Must-Gather YAML Analysis & RCA

- User enters problem statement (replaces hardcoded value in orchestrator).
- Progress updates: key files identified, YAML processing (list of files),
  extracted objects and ML classification (Majority Error, Rare pattern, etc.),
  LLM processing.
- Displays compression ratio (actual YAML size vs ML payload sent to LLM).
- Displays the RCA report.
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
import threading
import queue
import os
import sys
import atexit
from pathlib import Path

# Project root and paths so core and machine_learning modules are importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
for p in (PROJECT_ROOT, PROJECT_ROOT / "core", PROJECT_ROOT / "machine_learning"):
    p_str = str(p)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)

# Base dir = must-gather logs root (YAML files); default shown in UI and used when field/env empty.
from must_gather_file_selector import MUST_GATHER_DOCS_DIR_DEFAULT


def run_ui():
    """Run the chat UI (imports orchestrator inside to avoid circular deps)."""
    from orchestrator_agent import OrchestratorAgent, clear_agent_memory

    # Clear agent memory when Python exits so next run starts a new session
    atexit.register(clear_agent_memory)

    root = tk.Tk()
    root.title("Must-Gather Analysis & RCA Chat")
    root.geometry("900x750")
    root.minsize(700, 500)

    # Queue for thread-safe UI updates
    update_queue = queue.Queue()

    # --- Problem statement ---
    ttk.Label(root, text="Problem statement", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(10, 2))
    problem_text = scrolledtext.ScrolledText(root, height=4, wrap=tk.WORD, font=("Segoe UI", 10), padx=8, pady=8)
    problem_text.pack(fill=tk.X, padx=10, pady=(0, 6))
    problem_text.insert("1.0", "container image garbage collection is failing to function as expected")
    problem_text.config(state=tk.NORMAL)

    # --- Must-gather paths (optional) ---
    path_frame = ttk.Frame(root)
    path_frame.pack(fill=tk.X, padx=10, pady=4)
    ttk.Label(path_frame, text="Must-gather root (parent or content folder):").pack(side=tk.LEFT)
    base_dir_var = tk.StringVar(value="")
    base_dir_entry = ttk.Entry(path_frame, textvariable=base_dir_var, width=50)
    base_dir_entry.pack(side=tk.LEFT, padx=4, fill=tk.X, expand=True)

    def browse_base_dir():
        d = filedialog.askdirectory(title="Select must-gather logs root (YAML files)")
        if d:
            base_dir_var.set(d)

    ttk.Button(path_frame, text="Browse...", command=browse_base_dir).pack(side=tk.LEFT)
    ttk.Label(root, text="Point to the folder that contains namespaces/, cluster-scoped-resources/, etc., or its parent (one child with any name).", font=("Segoe UI", 8), foreground="gray").pack(anchor="w", padx=10)

    # --- Progress / log area ---
    ttk.Label(root, text="Progress & RCA", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(10, 2))
    progress_frame = ttk.Frame(root)
    progress_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 6))
    progress_text = scrolledtext.ScrolledText(
        progress_frame, wrap=tk.WORD, font=("Consolas", 9), state=tk.DISABLED, padx=8, pady=8
    )
    progress_text.pack(fill=tk.BOTH, expand=True)

    def append_progress(msg, tag=None):
        progress_text.config(state=tk.NORMAL)
        if tag:
            progress_text.insert(tk.END, msg + "\n", tag)
        else:
            progress_text.insert(tk.END, msg + "\n")
        progress_text.see(tk.END)
        progress_text.config(state=tk.DISABLED)

    # Tags for styling
    progress_text.tag_configure("heading", font=("Segoe UI", 10, "bold"), foreground="navy")
    progress_text.tag_configure("success", foreground="green")
    progress_text.tag_configure("error", foreground="red")
    progress_text.tag_configure("dim", foreground="gray")

    DELAY_MS = 700  # Slight delay after key sections so user can read
    POLL_MS = 250
    # Phase 3 sub-index: 3.1, 3.2, ... for each YAML file (mutable so closure can update)
    yaml_sub_index = [0]

    def process_one_message():
        try:
            msg = update_queue.get_nowait()
            if msg is None:
                return
            kind = msg.get("kind", "append")
            step = msg.get("step", "")
            text = msg.get("text", "")
            data = msg.get("data")
            need_delay = False
            if kind == "workflow_done":
                result = msg.get("result") or {}
                rca_feedback_state["session_id"] = result.get("session_id")
                rca_feedback_state["base_dir"] = msg.get("base_dir") or ""
                if result.get("need_rca_feedback"):
                    append_progress("", None)
                    append_progress("========== Provide feedback ==========", "heading")
                    append_progress("RCA was based on {} priority files only. If not satisfactory, you can add medium/low priority data.".format(result.get("rca_priority_stage", "high")), "dim")
                    feedback_frame.pack(fill=tk.X, padx=10, pady=4)
                else:
                    append_progress("\nDone.", "success")
            elif kind == "feedback_done":
                result = msg.get("result") or {}
                if result.get("satisfactory"):
                    append_progress("RCA marked satisfactory. Done.", "success")
                    feedback_frame.pack_forget()
                elif result.get("need_rca_feedback"):
                    report = result.get("rca_summary") or ""
                    if report:
                        append_progress("", None)
                        append_progress("========== UPDATED ROOT CAUSE ANALYSIS ==========", "heading")
                        for line in report.splitlines():
                            append_progress(line)
                    append_progress("", None)
                    append_progress("Provide feedback again if needed.", "dim")
                else:
                    msg_text = result.get("message") or "No further priority files."
                    append_progress(msg_text, "dim")
                    feedback_frame.pack_forget()
                run_btn.config(state=tk.NORMAL)
            elif kind == "append":
                append_progress(text, msg.get("tag"))
            elif kind == "progress":
                if step == "files_identified_intent":
                    append_progress("--- Phase 1: Selecting the Probable and available files for Issue analysis ---", "heading")
                    append_progress(text, "dim")
                    need_delay = True
                elif step == "file_selection_llm_result":
                    used_llm = (data or {}).get("used_llm", False)
                    append_progress("  Files selected for analysis:", "heading")
                    if used_llm:
                        cat = (data or {}).get("problem_category", "")
                        if cat:
                            append_progress("    Problem category: " + str(cat), "dim")
                        reasoning = (data or {}).get("reasoning", "")
                        if reasoning:
                            append_progress("    Explanation: " + (reasoning[:400] + "..." if len(reasoning) > 400 else reasoning), "dim")
                    else:
                        reason = (data or {}).get("reason", "")
                        if reason:
                            append_progress("    Reason: " + str(reason), "dim")
                    need_delay = True
                elif step == "files_identified":
                    append_progress("  Shortlisted files for analysis based on the user presented issue:", "heading")
                    for f in (data or {}).get("files", []):
                        append_progress("    • " + str(f))
                    need_delay = True
                elif step == "files_availability":
                    d = data or {}
                    append_progress("")
                    append_progress("  Shortlisted files: availability in must-gather directory (Phase 1)", "heading")
                    root_name = d.get("root_display_name") or ""
                    content_name = d.get("content_display_name") or ""
                    if root_name:
                        append_progress("  Root folder (user-supplied): " + str(root_name), "dim")
                    if content_name:
                        append_progress("  Content folder (under root): " + str(content_name), "dim")
                    for line in (d.get("summary_text") or text).strip().split("\n"):
                        append_progress("  " + line, "dim")
                    append_progress("  Found in supplied directory (absolute paths):", "heading")
                    for item in d.get("found_in_supplied_dir", [])[:50]:
                        res = item.get("resolved", "")
                        if res:
                            append_progress("    • " + str(res))
                    if len(d.get("found_in_supplied_dir", [])) > 50:
                        append_progress("    ... and " + str(len(d.get("found_in_supplied_dir", [])) - 50) + " more", "dim")
                    append_progress("  Found elsewhere under root (absolute paths):", "heading")
                    for item in d.get("found_elsewhere", [])[:50]:
                        res = item.get("resolved", "")
                        if res:
                            append_progress("    • " + str(res))
                    if len(d.get("found_elsewhere", [])) > 50:
                        append_progress("    ... and " + str(len(d.get("found_elsewhere", [])) - 50) + " more", "dim")
                    append_progress("  Not found (absolute path attempted):", "heading")
                    not_found_items = d.get("not_found_items") or []
                    if not not_found_items and d.get("not_found"):
                        not_found_items = [{"original": o, "resolved": ""} for o in d.get("not_found", [])]
                    for item in not_found_items[:50]:
                        res = item.get("resolved", "") if isinstance(item, dict) else ""
                        orig = item.get("original", item) if isinstance(item, dict) else item
                        display = res or str(orig)
                        append_progress("    • " + str(display))
                        if res and res != orig:
                            append_progress("      (shortlisted: " + str(orig) + ")", "dim")
                    if len(not_found_items) > 50:
                        append_progress("    ... and " + str(len(not_found_items) - 50) + " more", "dim")
                    need_delay = True
                elif step == "file_selection_only_done":
                    append_progress("")
                    append_progress("  File selection complete (file_selection_only mode). Exiting.", "success")
                    for f in (data or {}).get("files", []):
                        append_progress("    • " + str(f))
                    need_delay = True
                elif step == "file_analysis_intent":
                    yaml_sub_index[0] = 0  # reset for Phase 2 sub-items
                    append_progress("")
                    append_progress("--- Phase 2: Selected YAML files processing — Display the YAML being processed ---", "heading")
                    append_progress(text, "dim")
                    need_delay = True
                elif step == "yaml_files_list":
                    d = data or {}
                    root_name = d.get("root_display_name") or ""
                    content_name = d.get("content_display_name") or ""
                    if root_name:
                        append_progress("  Root folder: " + str(root_name), "dim")
                    if content_name:
                        append_progress("  Content folder: " + str(content_name), "dim")
                    append_progress("  Files to process (absolute paths):", "heading")
                    for f in d.get("files", []):
                        append_progress("    • " + str(f))
                    log_shortlisted = d.get("log_shortlisted") or []
                    log_resolved = d.get("log_resolved") or []
                    log_not_found = d.get("log_not_found") or []
                    if log_shortlisted:
                        append_progress("  Log files shortlisted (LLM placeholder paths):", "heading")
                        for p in log_shortlisted:
                            append_progress("    • " + str(p), "dim")
                    if log_resolved:
                        append_progress("  Log files resolved for Phase 3 (absolute paths):", "heading")
                        for p in log_resolved:
                            append_progress("    • " + str(p))
                    if log_not_found:
                        append_progress("  Log files not found (absolute path attempted):", "heading")
                        for p in log_not_found:
                            if isinstance(p, dict):
                                res = p.get("resolved", "") or p.get("original", "")
                                append_progress("    • " + str(res))
                                if p.get("resolved") and p.get("resolved") != p.get("original"):
                                    append_progress("      (shortlisted: " + str(p.get("original", "")) + ")", "dim")
                            else:
                                append_progress("    • " + str(p), "dim")
                    need_delay = True
                elif step == "yaml_processing":
                    yaml_sub_index[0] += 1
                    n = yaml_sub_index[0]
                    ordinal = "1st" if n == 1 else "2nd" if n == 2 else "3rd" if n == 3 else f"{n}th"
                    fname = (data or {}).get("file", text)
                    append_progress(f"  YAML being processed ({ordinal}): " + str(fname), "dim")
                elif step == "classification":
                    n = yaml_sub_index[0]
                    fname = (data or {}).get("file", "")
                    append_progress(f"  Phase 2 ML results ({n}): " + str(fname), "heading")
                    expl = (data or {}).get("explanation", "")
                    if expl:
                        append_progress("    " + expl, "dim")
                    table = (data or {}).get("table", [])
                    if table:
                        append_progress(f"    {'Chunk':<8} {'Lines':<15} {'Classification':<20} {'Reason':<40}")
                        for row in table[:20]:
                            c = row.get("Chunk", "")
                            ln = row.get("Lines", "")
                            cl = row.get("Classification", "")
                            r = (row.get("Reason", "") or "")[:40]
                            append_progress(f"    {c!s:<8} {ln!s:<15} {cl!s:<20} {r}")
                        if len(table) > 20:
                            append_progress(f"    ... and {len(table) - 20} more rows", "dim")
                    need_delay = True
                elif step == "classification_no_output":
                    n = yaml_sub_index[0]
                    append_progress(f"  Phase 2 ({n}): " + text, "dim")
                elif step == "files_no_output":
                    append_progress("")
                    append_progress("  Why no ML output for some YAML files:", "heading")
                    for item in (data or {}).get("files", []):
                        fname = item.get("file", "")
                        reason = item.get("reason", "")
                        append_progress(f"    • {fname}: {reason}", "dim")
                    need_delay = True
                elif step == "data_analysis_done":
                    append_progress("")
                    append_progress("  Phase 2 complete — YAML processing done.", "success")
                    s = (data or {}).get("summary", {})
                    if s:
                        append_progress(f"    Files processed: {s.get('files_processed', 0)}")
                        append_progress(f"    Objects extracted: {s.get('critical_fields_extracted', 0)}")
                    need_delay = True
                elif step == "files_by_priority":
                    d = data or {}
                    append_progress("")
                    append_progress("  Files by priority (first run uses {} only):".format(d.get("rca_priority_stage", "all")), "heading")
                    for pri in ("high", "medium", "low"):
                        entries = d.get(pri, [])
                        if not entries:
                            continue
                        append_progress(f"  {pri.upper()} priority ({len(entries)}):", "heading")
                        for e in entries:
                            if isinstance(e, str):
                                path = e
                            else:
                                path = (e.get("resolved") or e.get("path") or "").strip()
                            if path:
                                append_progress(f"    [FILE]  {path}")
                    need_delay = True
                elif step == "phase4_aggregation":
                    append_progress("")
                    append_progress("--- Phase 4: Relevant data aggregation from YAML and LOG files ---", "heading")
                    append_progress(text or "Aggregating YAML errors and log Error file contents for RCA.", "dim")
                    need_delay = True
                elif step == "aggregated_errors":
                    d = data or {}
                    append_progress("")
                    append_progress("  Aggregated Error Objects (classification == Error only):", "heading")
                    txt = (d.get("text") or "").strip()
                    if txt:
                        for line in txt.split("\n")[:80]:
                            append_progress("    " + line, "dim")
                        if txt.count("\n") >= 80:
                            append_progress("    ... (see terminal for full JSON)", "dim")
                    path = d.get("path", "")
                    if path:
                        append_progress("  Written to: " + str(path), "dim")
                    need_delay = True
                elif step == "phase3_logs_start":
                    append_progress("")
                    append_progress("--- Phase 3: Selected log files being processed with details ---", "heading")
                    append_progress(text, "dim")
                    d = data or {}
                    root_name = d.get("root_display_name") or ""
                    content_name = d.get("content_display_name") or ""
                    if root_name:
                        append_progress("  Root folder: " + str(root_name), "dim")
                    if content_name:
                        append_progress("  Content folder: " + str(content_name), "dim")
                    log_shortlisted = d.get("log_shortlisted") or []
                    log_resolved = d.get("log_resolved") or []
                    log_not_found = d.get("log_not_found") or []
                    if log_shortlisted:
                        append_progress("  Log files shortlisted (LLM placeholder paths):", "heading")
                        for p in log_shortlisted:
                            append_progress("    • " + str(p), "dim")
                    if log_resolved:
                        append_progress("  Log files resolved for ML processing (absolute paths):", "heading")
                        for p in log_resolved:
                            append_progress("    • " + str(p))
                    if log_not_found:
                        append_progress("  Log files not found (absolute path attempted):", "heading")
                        for p in log_not_found:
                            if isinstance(p, dict):
                                res = p.get("resolved", "") or p.get("original", "")
                                append_progress("    • " + str(res))
                                if p.get("resolved") and p.get("resolved") != p.get("original"):
                                    append_progress("      (shortlisted: " + str(p.get("original", "")) + ")", "dim")
                            else:
                                append_progress("    • " + str(p), "dim")
                    need_delay = True
                elif step == "log_processing_result":
                    d = data or {}
                    append_progress("")
                    append_progress("  Log processing (Error, Information, Warning) — one set per log file:", "heading")
                    if d.get("error"):
                        append_progress("    " + str(d["error"]), "dim")
                    n_logs = d.get("logs_count", 0)
                    if n_logs:
                        append_progress(f"  Log files processed: {n_logs} (each file gets Error/Information/Warning/Unknown outputs)", "dim")
                    elif not d.get("error"):
                        for p in (d.get("log_not_found") or []):
                            if isinstance(p, dict):
                                res = p.get("resolved", "") or p.get("original", "")
                                append_progress("    Not found: " + str(res), "dim")
                                if p.get("resolved") and p.get("resolved") != p.get("original"):
                                    append_progress("      (shortlisted: " + str(p.get("original", "")) + ")", "dim")
                            else:
                                append_progress("    Not found: " + str(p), "dim")
                    if not d.get("error"):
                        for name in ("Error", "Warning", "Information", "Unknown"):
                            s = (d.get("summary") or {}).get(name)
                            if not s:
                                continue
                            append_progress(f"  {name}:", "heading")
                            append_progress(f"    Entries: {s.get('count', 0)}, Templates: {s.get('templates_count', 0)}", "dim")
                            paths = s.get("paths") or []
                            if paths:
                                for p in paths:
                                    append_progress(f"    Output: {p}", "dim")
                        append_progress("  Per log file (one set each):", "heading")
                        for pf in (d.get("per_file") or []):
                            append_progress(f"    {pf.get('file', '')}", "dim")
                            for level_name, path in (pf.get("saved") or {}).items():
                                append_progress(f"      {level_name} -> {path}", "dim")
                    need_delay = True
                elif step == "exit_after_analysis_done":
                    append_progress("")
                    append_progress("  YAML and LOG analysis complete. Exiting (exit_after_analysis mode).", "success")
                    need_delay = True
                elif step == "terminal_block":
                    for line in (data or {}).get("lines", []):
                        append_progress(line, "dim")
                    need_delay = True
                elif step == "rca_continue":
                    append_progress("")
                    append_progress("--- Adding more priority files and re-running RCA ---", "heading")
                    append_progress(text, "dim")
                    d = data or {}
                    if d.get("new_priority"):
                        append_progress("  New priority: " + str(d.get("new_priority")), "dim")
                    if d.get("file_count"):
                        append_progress("  File count: " + str(d.get("file_count")), "dim")
                    need_delay = True
                elif step == "llm_start_intent":
                    append_progress("")
                    append_progress("--- Phase 5: Root Cause analysis details ---", "heading")
                    append_progress(text, "dim")
                    need_delay = True
                elif step == "llm_start":
                    append_progress("  Sending Error-classified objects to LLM (line numbers stripped)...", "dim")
                    need_delay = True
                elif step == "llm_done":
                    append_progress("")
                    append_progress("  Phase 5 complete — RCA done.", "success")
                    total = (data or {}).get("total_yaml_bytes", 0)
                    payload = (data or {}).get("payload_bytes", 0)
                    expl = (data or {}).get("compression_explanation", "")
                    if expl:
                        append_progress("  " + expl, "dim")
                    if payload and total:
                        ratio = total / payload
                        append_progress(f"  Compression ratio: YAML total {total:,} bytes → ML payload {payload:,} bytes = {ratio:.1f}x smaller", "success")
                    elif payload:
                        append_progress(f"  ML payload size: {payload:,} bytes", "dim")
                    report = (data or {}).get("report_with_user_query") or (data or {}).get("rca_summary") or ""
                    if report:
                        append_progress("")
                        append_progress("========== ROOT CAUSE ANALYSIS ==========", "heading")
                        for line in report.splitlines():
                            append_progress(line)
                    need_delay = True
                elif step == "phase6_cost":
                    append_progress("")
                    append_progress("--- Phase 6: Cost Calculation ---", "heading")
                    cost_table = (data or {}).get("cost_table", "")
                    if cost_table:
                        for line in cost_table.splitlines():
                            append_progress(line, "dim" if line.startswith("  ") else None)
                    need_delay = True
            if need_delay:
                root.after(DELAY_MS, process_one_message)
            else:
                root.after(POLL_MS, process_one_message)
        except queue.Empty:
            root.after(POLL_MS, process_one_message)
        except Exception:
            root.after(POLL_MS, process_one_message)

    def process_queue():
        process_one_message()

    root.after(POLL_MS, process_queue)

    btn_frame = ttk.Frame(root)
    btn_frame.pack(pady=10)

    def clear_progress():
        clear_agent_memory()
        progress_text.config(state=tk.NORMAL)
        progress_text.delete("1.0", tk.END)
        progress_text.config(state=tk.DISABLED)
        feedback_frame.pack_forget()
        rca_feedback_state["session_id"] = None
        rca_feedback_state["base_dir"] = None
        append_progress("Agent memory cleared. Next Run will start a new session.", "dim")

    def on_exit():
        clear_agent_memory()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_exit)

    run_btn = ttk.Button(btn_frame, text="Run Analysis")
    run_btn.pack(side=tk.LEFT, padx=4)
    ttk.Button(btn_frame, text="Clear", command=clear_progress).pack(side=tk.LEFT, padx=4)
    ttk.Button(btn_frame, text="Exit", command=on_exit).pack(side=tk.LEFT, padx=4)

    # RCA feedback (shown when need_rca_feedback after first or continued RCA)
    feedback_frame = ttk.LabelFrame(root, text="RCA feedback", padding=6)
    feedback_frame.pack(fill=tk.X, padx=10, pady=4)
    ttk.Label(feedback_frame, text="Is the RCA satisfactory?").pack(side=tk.LEFT, padx=(0, 10))
    satisfactory_btn = ttk.Button(feedback_frame, text="RCA satisfactory")
    satisfactory_btn.pack(side=tk.LEFT, padx=4)
    not_satisfactory_btn = ttk.Button(feedback_frame, text="RCA not satisfactory")
    not_satisfactory_btn.pack(side=tk.LEFT, padx=4)
    feedback_frame.pack_forget()

    rca_feedback_state = {"session_id": None, "base_dir": None}

    def run_workflow():
        problem = problem_text.get("1.0", tk.END).strip()
        if not problem:
            messagebox.showwarning("Input", "Please enter a problem statement.")
            return

        # Docs dir: MUST_GATHER_STRUCTURE.md etc. (hardcoded default; env MUST_GATHER_DOCS_DIR overrides).
        # Base dir: must-gather logs root where YAML files live (user sets via "Must-gather base dir" or env).
        must_gather_docs_dir = os.environ.get("MUST_GATHER_DOCS_DIR") or MUST_GATHER_DOCS_DIR_DEFAULT
        must_gather_base_dir = base_dir_var.get().strip() or None
        if not must_gather_base_dir:
            must_gather_base_dir = os.environ.get("MUST_GATHER_BASE_DIR") or None
        if not must_gather_base_dir:
            messagebox.showwarning("Input", "Please enter the must-gather root folder (the folder you supplied in the UI).")
            return

        def progress_callback(step, message, data):
            update_queue.put({"kind": "progress", "step": step, "text": message, "data": data})

        def worker():
            run_btn.config(state=tk.DISABLED)
            append_progress("Starting workflow...", "heading")
            append_progress("Problem: " + problem[:200] + ("..." if len(problem) > 200 else ""), "dim")
            result = None
            try:
                orchestrator = OrchestratorAgent()
                result = orchestrator.execute_workflow(
                    problem,
                    must_gather_docs_dir,
                    must_gather_base_dir=must_gather_base_dir,
                    progress_callback=progress_callback,
                )
                if result and result.get("status") == "completed":
                    ratio = result.get("compression_ratio")
                    if ratio:
                        update_queue.put({
                            "kind": "append",
                            "text": f"\nCompression ratio: {ratio:.1f}x (YAML size / ML payload size)",
                            "tag": "success",
                        })
                    if result.get("need_rca_feedback"):
                        update_queue.put({"kind": "workflow_done", "result": result, "base_dir": must_gather_base_dir})
                    else:
                        update_queue.put({"kind": "append", "text": "\nDone.", "tag": "success"})
                elif result and result.get("status") == "error":
                    update_queue.put({"kind": "append", "text": "Error: " + result.get("error", "Unknown"), "tag": "error"})
            except Exception as e:
                update_queue.put({"kind": "append", "text": "Error: " + str(e), "tag": "error"})
            finally:
                if not (result and result.get("need_rca_feedback")):
                    run_btn.config(state=tk.NORMAL)

        threading.Thread(target=worker, daemon=True).start()

    def on_rca_satisfactory():
        session_id = rca_feedback_state.get("session_id")
        if not session_id:
            messagebox.showwarning("Feedback", "No session. Run analysis first.")
            return
        satisfactory_btn.config(state=tk.DISABLED)
        not_satisfactory_btn.config(state=tk.DISABLED)
        run_btn.config(state=tk.DISABLED)

        def worker_sat():
            try:
                orch = OrchestratorAgent()
                res = orch.continue_rca_with_feedback(session_id, True)
                update_queue.put({"kind": "feedback_done", "result": res})
            except Exception as e:
                update_queue.put({"kind": "feedback_done", "result": {"satisfactory": True, "error": str(e)}})
            finally:
                satisfactory_btn.config(state=tk.NORMAL)
                not_satisfactory_btn.config(state=tk.NORMAL)

        threading.Thread(target=worker_sat, daemon=True).start()

    def on_rca_not_satisfactory():
        session_id = rca_feedback_state.get("session_id")
        base_dir = rca_feedback_state.get("base_dir") or ""
        if not session_id:
            messagebox.showwarning("Feedback", "No session. Run analysis first.")
            return
        satisfactory_btn.config(state=tk.DISABLED)
        not_satisfactory_btn.config(state=tk.DISABLED)
        run_btn.config(state=tk.DISABLED)

        def progress_callback(step, message, data):
            update_queue.put({"kind": "progress", "step": step, "text": message, "data": data})

        def worker_not_sat():
            try:
                orch = OrchestratorAgent()
                res = orch.continue_rca_with_feedback(session_id, False, must_gather_base_dir=base_dir, progress_callback=progress_callback)
                update_queue.put({"kind": "feedback_done", "result": res})
            except Exception as e:
                update_queue.put({"kind": "feedback_done", "result": {"need_rca_feedback": False, "message": "Error: " + str(e)}})
            finally:
                satisfactory_btn.config(state=tk.NORMAL)
                not_satisfactory_btn.config(state=tk.NORMAL)

        threading.Thread(target=worker_not_sat, daemon=True).start()

    satisfactory_btn.config(command=on_rca_satisfactory)
    not_satisfactory_btn.config(command=on_rca_not_satisfactory)

    run_btn.config(command=run_workflow)

    root.mainloop()


if __name__ == "__main__":
    run_ui()
