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

export interface FeedbackMessage {
  message: string;
  isError: boolean;
}