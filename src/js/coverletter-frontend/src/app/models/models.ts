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
  scores?: JobPreferenceScore[];
  weighted_score?: number;
  max_score?: number;
  scoring_status?: string;
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
  identity_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'rejected';
  phase:
    | 'queued'
    | 'workflow1_company_discovery'
    | 'workflow2_ats_enrichment'
    | 'workflow3_ats_job_extraction'
    | 'workflow4_4dayweek'
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

export interface FeedbackMessage {
  message: string;
  isError: boolean;
}