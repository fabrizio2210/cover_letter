export interface Field {
  id: string;
  field: string;
}

export interface Company {
  id: string;
  name: string;
  description?: string;
  field_id?: string;
  field_info?: Field;
  ats_provider?: string;
  ats_slug?: string;
}

export interface Recipient {
  id: string;
  email: string;
  name?: string;
  description?: string;
  company_id?: string;
  company_info?: Company;
}

export interface Identity {
  id: string;
  identity: string;
  name?: string;
  description?: string;
  field_id?: string;
  field_info?: Field;
  roles?: string[];
  html_signature?: string;
  preferences?: IdentityPreference[];
}

export interface HistoryPart {
  text: string;
}

export interface HistoryEntry {
  role: 'user' | 'model';
  parts: HistoryPart[];
}

export interface Timestamp {
  seconds: number;
  nanos: number;
}

export interface IdentityPreference {
  key: string;
  guidance?: string;
  weight: number;
  enabled: boolean;
}

export interface JobPreferenceScore {
  id: string;
  job_id: string;
  identity_id: string;
  preference_scores: PreferenceScore[];
  scoring_status?: string;
  weighted_score?: number;
  max_score?: number;
}

export interface PreferenceScore {
  preference_key: string;
  preference_guidance?: string;
  preference_weight?: number;
  score: number;
  rationale?: string;
  scored_at?: string | Timestamp;
}

export interface JobDescription {
  id: string;
  company_id?: string;
  company_name?: string;
  title: string;
  description: string;
  location: string;
  platform: string;
  external_job_id: string;
  source_url: string;
  created_at?: string | Timestamp;
  updated_at?: string | Timestamp;
  company_info?: Company;
}

export interface ScoredJobDescription extends JobDescription {
  score?: JobPreferenceScore | null;
}

export interface CoverLetter {
  id: string;
  recipient_id: string;
  cover_letter?: string;
  prompt?: string;
  status?: string;
  conversation_id?: string;
  history?: HistoryEntry[];
  created_at?: string | Timestamp;
  updated_at?: string | Timestamp;
  recipient_info?: Recipient;
}

export interface CrawlProgress {
  run_id: string;
  workflow_run_id?: string;
  workflow_id?:
    | 'crawler_ycombinator'
    | 'crawler_hackernews'
    | 'enrichment_ats_enrichment'
    | 'crawler_ats_job_extraction'
    | 'crawler_4dayweek'
    | 'crawler_levelsfyi';
  identity_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'rejected';
  workflow:
    | 'queued'
    | 'crawler_ycombinator'
    | 'crawler_hackernews'
    | 'enrichment_ats_enrichment'
    | 'crawler_ats_job_extraction'
    | 'crawler_4dayweek'
    | 'crawler_levelsfyi'
    | 'finalizing';
  message?: string;
  estimated_total: number;
  completed: number;
  percent: number;
  started_at?: string | Timestamp | null;
  updated_at?: string | Timestamp;
  finished_at?: string | Timestamp | null;
  reason?: string;
}

export interface ScoringProgress {
  run_id: string;
  identity_id: string;
  status: 'running' | 'completed' | 'failed';
  message?: string;
  estimated_total: number;
  completed: number;
  percent: number;
  started_at?: string | Timestamp | null;
  updated_at?: string | Timestamp;
  finished_at?: string | Timestamp | null;
  reason?: string;
}

export interface LastRunWorkflowStatsItem {
  workflow_id:
    | 'crawler_ycombinator'
    | 'crawler_hackernews'
    | 'crawler_ats_job_extraction'
    | 'crawler_4dayweek'
    | 'crawler_levelsfyi';
  discovered_jobs: number;
  discovered_companies: number;
}

export interface LastRunWorkflowStatsResponse {
  completed_at?: string | Timestamp | null;
  workflows: LastRunWorkflowStatsItem[];
}

export interface WorkflowCumulativeJobsItem {
  workflow_id:
    | 'crawler_ycombinator'
    | 'crawler_hackernews'
    | 'crawler_ats_job_extraction'
    | 'crawler_4dayweek'
    | 'crawler_levelsfyi';
  discovered_jobs_cumulative: number;
}

export interface WorkflowCumulativeJobsResponse {
  workflows: WorkflowCumulativeJobsItem[];
}

export interface ActiveWorkflowItem {
  workflow_id: string;
  status: 'queued' | 'running';
  message?: string;
}

export interface ActivityQueueDepth {
  crawler_trigger: number;
  crawler_ycombinator: number;
  crawler_hackernews: number;
  crawler_ats_job_extraction: number;
  crawler_levelsfyi: number;
  crawler_4dayweek: number;
  crawler_enrichment_ats: number;
  job_scoring: number;
}

export interface ActivitySummaryResponse {
  identity_id: string;
  active_workflows: ActiveWorkflowItem[];
  global_queue_depth: ActivityQueueDepth;
}

export interface FeedbackMessage {
  message: string;
  isError: boolean;
}

export interface JobUpdateEvent {
  job_id: string;
  workflow_id?: string;
  workflow_run_id?: string;
  emitted_at?: string | Timestamp | null;
}
