import type { AgentMemory, ConsoleMessage, WorkflowStep } from '../types';
import { mockRCATier1, mockRCATier2, mockRCAFinal, mockRCASummary } from './mock_rca_exports';

export { mockRCATier1, mockRCATier2, mockRCAFinal };

export const mockAgentMemory: AgentMemory = {
  sessions: [
    {
      session_id: 'session_20260216_192727',
      problem_statement:
        'Autosizing causes control plane node to reboot twice during upgrade',
      status: 'rca_completed',
      created_at: '2026-02-16T19:27:27.944086',
      updated_at: '2026-02-16T19:33:30.465229',
      results: {
        file_suggestions: {
          suggested_files: [
            { path: 'cluster-scoped-resources/config.openshift.io/clusteroperators.yaml', priority: 'high', reason: 'cluster operator status during upgrade' },
            { path: 'namespaces/openshift-cluster-version/pods/*/logs/current.log', priority: 'high', reason: 'cluster version operator logs for upgrade process' },
            { path: 'namespaces/openshift-cluster-version/pods/*/logs/previous.log', priority: 'high', reason: 'previous cluster version operator logs' },
            { path: 'namespaces/openshift-cluster-version/core/events.yaml', priority: 'high', reason: 'upgrade events and status changes' },
            { path: 'namespaces/openshift-machine-config-operator/pods/*/logs/current.log', priority: 'high', reason: 'machine config operator logs for node configuration' },
            { path: 'namespaces/openshift-machine-config-operator/pods/*/logs/previous.log', priority: 'high', reason: 'previous machine config operator logs' },
            { path: 'namespaces/openshift-machine-config-operator/core/events.yaml', priority: 'high', reason: 'machine config events during upgrade' },
            { path: 'host_service_logs/masters/machine-config-daemon_service.log', priority: 'high', reason: 'machine config daemon service logs on control plane' },
            { path: 'host_service_logs/masters/kubelet_service.log', priority: 'high', reason: 'kubelet service logs on control plane nodes' },
            { path: 'cluster-scoped-resources/core/nodes/*.yaml', priority: 'high', reason: 'node status and conditions during upgrade' },
            { path: 'cluster-scoped-resources/machineconfiguration.openshift.io/machineconfigs.yaml', priority: 'medium', reason: 'machine configuration definitions' },
            { path: 'cluster-scoped-resources/machineconfiguration.openshift.io/machineconfigpools.yaml', priority: 'medium', reason: 'machine config pool status' },
            { path: 'machine_config_ondisk/*/mcs-machine-config-content.json', priority: 'medium', reason: 'on-disk machine config content' },
            { path: 'machine_config_ondisk/*/bootstrapconfigdiff', priority: 'medium', reason: 'bootstrap configuration differences' },
            { path: 'host_service_logs/masters/crio_service.log', priority: 'medium', reason: 'container runtime logs on control plane' },
            { path: 'nodes/*/kubelet/journal/journal.log', priority: 'medium', reason: 'kubelet journal logs for detailed node diagnostics' },
            { path: 'namespaces/openshift-etcd-operator/pods/*/logs/current.log', priority: 'low', reason: 'etcd operator logs for control plane stability' },
            { path: 'namespaces/openshift-etcd-operator/pods/*/logs/previous.log', priority: 'low', reason: 'previous etcd operator logs' },
            { path: 'etcd_info/endpoint_health.json', priority: 'low', reason: 'etcd cluster health during upgrade' },
            { path: 'etcd_info/member_list.json', priority: 'low', reason: 'etcd member status during upgrade' },
          ],
          reasoning: '',
          problem_category: 'Cluster Operator / Node/Machine Config',
          priority: {
            'cluster-scoped-resources/config.openshift.io/clusteroperators.yaml': 'high',
            'host_service_logs/masters/kubelet_service.log': 'high',
            'host_service_logs/masters/crio_service.log': 'medium',
            'etcd_info/endpoint_health.json': 'low',
          },
          input_tokens: 27443,
          output_tokens: 615,
          file_availability: {
            base_folder: '/home/cdate/Downloads/must-gather-x212-20251202/must-gather.local.102763098502795361',
            total_shortlisted: 20,
            found_in_supplied_dir: [
              { original: 'cluster-scoped-resources/config.openshift.io/clusteroperators.yaml', resolved: '/resolved/clusteroperators.yaml' },
              { original: 'namespaces/openshift-cluster-version/core/events.yaml', resolved: '/resolved/events.yaml' },
              { original: 'namespaces/openshift-machine-config-operator/core/events.yaml', resolved: '/resolved/mco-events.yaml' },
              { original: 'host_service_logs/masters/kubelet_service.log', resolved: '/resolved/kubelet_service.log' },
              { original: 'cluster-scoped-resources/core/nodes/master01.yaml', resolved: '/resolved/master01.yaml' },
              { original: 'cluster-scoped-resources/core/nodes/master02.yaml', resolved: '/resolved/master02.yaml' },
              { original: 'cluster-scoped-resources/core/nodes/master03.yaml', resolved: '/resolved/master03.yaml' },
              { original: 'cluster-scoped-resources/core/nodes/worker01.yaml', resolved: '/resolved/worker01.yaml' },
              { original: 'host_service_logs/masters/crio_service.log', resolved: '/resolved/crio_service.log' },
              { original: 'etcd_info/endpoint_health.json', resolved: '/resolved/endpoint_health.json' },
              { original: 'etcd_info/member_list.json', resolved: '/resolved/member_list.json' },
            ],
            found_elsewhere: [],
            not_found: [
              'namespaces/openshift-cluster-version/pods/*/logs/current.log',
              'namespaces/openshift-cluster-version/pods/*/logs/previous.log',
              'namespaces/openshift-machine-config-operator/pods/*/logs/current.log',
              'namespaces/openshift-machine-config-operator/pods/*/logs/previous.log',
              'host_service_logs/masters/machine-config-daemon_service.log',
              'cluster-scoped-resources/machineconfiguration.openshift.io/machineconfigs.yaml',
              'cluster-scoped-resources/machineconfiguration.openshift.io/machineconfigpools.yaml',
              'machine_config_ondisk/*/mcs-machine-config-content.json',
              'machine_config_ondisk/*/bootstrapconfigdiff',
              'nodes/*/kubelet/journal/journal.log',
              'namespaces/openshift-etcd-operator/pods/*/logs/current.log',
              'namespaces/openshift-etcd-operator/pods/*/logs/previous.log',
            ],
            summary_text: 'Total shortlisted: 20\n  Found: 11\n  Not found: 12',
          },
        },
        ml_classification_result: {},
        data_analysis: {
          ml_classification_result: {
            output_flags: { include_errors: true, include_majority_errors: true, include_config_changes: true, include_normal_reports: false, include_summary: true, max_instances_per_pattern: 0 },
            files: {},
          },
          classification_table: [
            { Chunk: 2, Lines: '4-146', Classification: 'Majority', Reason: 'High frequency (2.94%); Normal/healthy status indicators' },
            { Chunk: 3, Lines: '147-274', Classification: 'Majority', Reason: 'High frequency (2.94%); Normal/healthy status indicators' },
            { Chunk: 4, Lines: '275-397', Classification: 'Majority', Reason: 'High frequency (2.94%); Normal/healthy status indicators' },
            { Chunk: 5, Lines: '398-664', Classification: 'Majority', Reason: 'High frequency (2.94%); Normal/healthy status indicators' },
            { Chunk: 6, Lines: '665-760', Classification: 'Majority', Reason: 'High frequency (5.88%); Normal/healthy status indicators' },
            { Chunk: 7, Lines: '761-859', Classification: 'Majority', Reason: 'High frequency (8.82%); Normal/healthy status indicators' },
            { Chunk: 8, Lines: '860-1017', Classification: 'Timeout', Reason: 'Repeated timeout connection errors' },
            { Chunk: 9, Lines: '1018-1108', Classification: 'Config', Reason: 'Configuration mismatch warnings' },
            { Chunk: 10, Lines: '1109-1216', Classification: 'Majority', Reason: 'High frequency (2.94%); Normal/healthy status indicators' },
            { Chunk: 11, Lines: '1217-1320', Classification: 'Error', Reason: 'Rare pattern: CNI configuration failure' },
            { Chunk: 12, Lines: '1321-1446', Classification: 'Majority', Reason: 'High frequency (2.94%); Normal/healthy status indicators' },
          ],
          total_yaml_bytes: 254678,
          yaml_file_sizes: {
            'clusteroperators.yaml': 158254,
            'events.yaml': 456,
            'master01.yaml': 24112,
            'master02.yaml': 24345,
            'master03.yaml': 24100,
            'worker01.yaml': 23411,
          },
          log_payload: [
            { file: 'kubelet_service_highfreq_error.txt', content: '', original_size: 130933586 },
            { file: 'crio_service_highfreq_error.txt', content: '', original_size: 122970994 },
          ],
        },
        rca_summary: mockRCASummary,
        rca_result: {
          rca_summary: 'Network configuration failures and container runtime disruptions during the upgrade process...',
          input_tokens: 176702,
          output_tokens: 1000,
          cost_usd: 0.545106,
        },
        agent_thoughts: [
          "I'll help you analyze the must-gather bundle to diagnose the autosizing issue causing control plane node reboots during upgrade.",
          'The LLM API is available with 200,000 token context window.',
          'File selection identified this as a Cluster Operator / Node/Machine Config issue.',
          'HIGH priority YAML files show no Error-classified objects. The issue may be in logs.',
          'Let me add MEDIUM priority files for more context.',
          'Token budget is well within limits. Performing initial RCA.',
          'The RCA was cut off. Let me evaluate and determine if deeper analysis is needed.',
          'RCA evaluation shows LOW confidence. Providing final report with limitations noted.',
        ],
        agent_iteration_count: 10,
        agent_total_tokens: 137459,
      },
      tool_history: [
        { agent: 'check_llm_availability', timestamp: '2026-02-16T19:27:30.123', success: true, summary: 'LLM API is available. Model: claude-sonnet-4@20250514.' },
        { agent: 'select_files', timestamp: '2026-02-16T19:27:45.456', success: true, summary: 'Found 20 files. Category: Cluster Operator / Node/Machine Config.' },
        { agent: 'analyze_yaml', timestamp: '2026-02-16T19:28:10.789', success: true, summary: '6 YAML files processed, 38 objects extracted, 0 Error-classified.' },
        { agent: 'analyze_logs', timestamp: '2026-02-16T19:30:20.012', success: true, summary: 'kubelet_service.log: 57,200 Error logs, 249 templates.' },
        { agent: 'analyze_logs', timestamp: '2026-02-16T19:31:05.345', success: true, summary: 'crio_service.log: 152,311 Error logs, 29 templates.' },
        { agent: 'analyze_json', timestamp: '2026-02-16T19:31:20.678', success: true, summary: '2 JSON files processed, 0 errors found.' },
        { agent: 'validate_token_budget', timestamp: '2026-02-16T19:31:33.901', success: true, summary: 'Token budget OK. Estimated 140,236 input tokens.' },
        { agent: 'perform_rca', timestamp: '2026-02-16T19:32:50.234', success: true, summary: 'RCA complete. 176,702 input / 1,000 output tokens. Cost: $0.5451.' },
        { agent: 'evaluate_rca', timestamp: '2026-02-16T19:33:15.567', success: true, summary: 'RCA Evaluation: confidence=LOW. Missing areas identified.' },
      ],
    },
  ],
  problem_statements: [
    { session_id: 'session_20260216_192727', problem_statement: 'Autosizing causes control plane node to reboot twice during upgrade' },
  ],
  metadata: {
    created_at: '2026-02-16T19:27:27.943643',
    last_updated: '2026-02-16T19:33:30.465241',
    total_sessions: 1,
  },
};

export const mockConsoleOutput = `
====================================================================================================
AGENTIC ORCHESTRATOR — ReAct Loop
====================================================================================================
Problem: Autosizing causes control plane node to reboot twice during upgrade
Max iterations: 25

--- Agent iteration 1/25 ---
  [Agent thinks]: I'll help you analyze the must-gather bundle to diagnose the autosizing issue.
  [Tool call] check_llm_availability({})
  [Tool result] success=True, summary=LLM API is available. Model: claude-sonnet-4@20250514.

--- Agent iteration 2/25 ---
  [Agent thinks]: Excellent! The LLM API is available. Now let me analyze your problem statement...
  [Tool call] select_files({"problem_statement": "Autosizing causes control plane node to reboot twice during upgrade"})

=== Phase 1: File Selection ===
  [Agent] Analyzing problem statement...
  [Tool] Found 20 relevant files. Category: Cluster Operator / Node/Machine Config.
  Priority breakdown: 10 high, 6 medium, 4 low.

=== Phase 2: YAML Processing ===
  [Tool] Processing clusteroperators.yaml...
  [Tool] Processing events.yaml...
  [Tool] Processing master01.yaml, master02.yaml, master03.yaml, worker01.yaml...
  [Success] 6 YAML files processed, 38 objects extracted.

=== Phase 3: Log Processing ===
  [Tool] Parsing kubelet_service.log with Drain3...
  [Tool] Detected 249 error templates (57,200 error lines).
  [Tool] Parsing crio_service.log with Drain3...
  [Tool] Detected 29 error templates (152,311 error lines).

=== Phase 4: Data Aggregation ===
  [Agent] Correlating YAML classifications and log error patterns...
  [Agent] Aggregating Error-classified objects for RCA payload.

=== Phase 5: Root Cause Analysis ===
  [Agent] Validating token budget: 140,236 / 182,000 tokens OK.
  [Agent] Sending payload to LLM for root cause analysis...
  [Success] RCA Complete. Compression ratio: 607.18x

====================================================================================================
LLM API TOKEN & COST SUMMARY
====================================================================================================
  Phase                              Input tokens  Output tokens     Cost (USD)
  0. Orchestrator ReAct loop              131,867          5,592   $   0.479481
  1. File selection                        27,443            615   $   0.091554
  5. RCA (extracted payload)              176,702          1,000   $   0.545106
  Total                                                            $   1.116141

  COMPRESSION RATIO
  Total input: 254,159,258 bytes → Payload to LLM: 418,593 bytes = 607.18x
====================================================================================================
`;

export const mockWorkflowSteps: WorkflowStep[] = [
  { id: 'init', label: 'Initialize Agent', phase: 'initializing', status: 'completed' },
  { id: 'files', label: 'File System Scan', phase: 'file_selection', status: 'completed' },
  { id: 'classify', label: 'Log Classification', phase: 'log_processing', status: 'completed' },
  { id: 'rca', label: 'Root Cause Analysis', phase: 'rca_analysis', status: 'completed' },
];

export const mockConsoleMessages: ConsoleMessage[] = [
  { id: 1, text: '', type: 'normal' },
  { id: 2, text: '=== Phase 1: File Selection ===', type: 'heading', timestamp: '19:27:30' },
  { id: 3, text: '  [Agent] Analyzing problem statement...', type: 'dim', timestamp: '19:27:30' },
  { id: 4, text: '  [Tool] Found 20 relevant files.', type: 'info', timestamp: '19:27:45' },
  { id: 5, text: '', type: 'normal' },
  { id: 6, text: '=== Phase 2: YAML Processing ===', type: 'heading', timestamp: '19:28:10' },
  { id: 7, text: '  [Tool] Processing clusteroperators.yaml...', type: 'dim', timestamp: '19:28:10' },
  { id: 8, text: '  [Tool] Processing node-config.yaml...', type: 'dim', timestamp: '19:28:15' },
  { id: 9, text: '', type: 'normal' },
  { id: 10, text: '=== Phase 3: Log Processing ===', type: 'heading', timestamp: '19:30:00' },
  { id: 11, text: '  [Tool] Parsing kubelet_service.log with Drain3...', type: 'dim', timestamp: '19:30:00' },
  { id: 12, text: '  [Tool] Detected 249 error templates.', type: 'info', timestamp: '19:30:20' },
  { id: 13, text: '', type: 'normal' },
  { id: 14, text: '=== Phase 4: Data Aggregation ===', type: 'heading', timestamp: '19:31:00' },
  { id: 15, text: '  [Agent] Correlating timestamps between audit logs and etcd logs...', type: 'dim', timestamp: '19:31:05' },
  { id: 16, text: '', type: 'normal' },
  { id: 17, text: '=== Phase 5: Root Cause Analysis ===', type: 'heading', timestamp: '19:32:00' },
  { id: 18, text: '  [Agent] Generating final report...', type: 'dim', timestamp: '19:32:50' },
  { id: 19, text: '  [Success] RCA Complete.', type: 'success', timestamp: '19:33:15' },
];
