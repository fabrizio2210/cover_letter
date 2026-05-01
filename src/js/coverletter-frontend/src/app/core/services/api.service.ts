import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, of } from 'rxjs';
import { catchError } from 'rxjs/operators';
import { Field, Company, Recipient, Identity, JobDescription, JobPreferenceScore, ScoredJobDescription, CoverLetter, CrawlProgress, ScoringProgress, LastRunWorkflowStatsResponse, JobUpdateEvent, WorkflowCumulativeJobsResponse, ActivitySummaryResponse } from '../../shared/models/models';
import { AuthService } from '../auth/auth.service';

@Injectable({
  providedIn: 'root'
})
export class ApiService {
  private apiBase = '/api';

  constructor(private http: HttpClient, private authService: AuthService) {}

  // Fields
  listFields(): Observable<Field[]> {
    return this.http.get<Field[]>(`${this.apiBase}/fields`);
  }

  getFields(): Observable<Field[]> {
    return this.listFields()
      .pipe(catchError(() => of([])));
  }

  // Companies
  listCompanies(): Observable<Company[]> {
    return this.http.get<Company[]>(`${this.apiBase}/companies`);
  }

  getCompanies(): Observable<Company[]> {
    return this.listCompanies()
      .pipe(catchError(() => of([])));
  }

  createCompany(payload: Partial<Company>): Observable<Company> {
    return this.http.post<Company>(`${this.apiBase}/companies`, payload);
  }

  updateCompany(id: string, payload: Partial<Company>): Observable<{ message: string }> {
    return this.http.put<{ message: string }>(`${this.apiBase}/companies/${id}`, payload);
  }

  updateCompanyField(id: string, fieldId: string | null): Observable<{ message: string; modifiedCount: number }> {
    return this.http.put<{ message: string; modifiedCount: number }>(`${this.apiBase}/companies/${id}/field`, {
      field_id: fieldId
    });
  }

  deleteCompany(id: string): Observable<{ message: string }> {
    return this.http.delete<{ message: string }>(`${this.apiBase}/companies/${id}`);
  }

  // Recipients
  listRecipients(): Observable<Recipient[]> {
    return this.http.get<Recipient[]>(`${this.apiBase}/recipients`);
  }

  getRecipients(): Observable<Recipient[]> {
    return this.listRecipients()
      .pipe(catchError(() => of([])));
  }

  createRecipient(payload: Partial<Recipient>): Observable<Recipient> {
    return this.http.post<Recipient>(`${this.apiBase}/recipients`, payload);
  }

  updateRecipientName(id: string, name: string): Observable<{ message: string }> {
    return this.http.put<{ message: string }>(`${this.apiBase}/recipients/${id}/name`, { name });
  }

  updateRecipientDescription(id: string, description: string): Observable<{ message: string }> {
    return this.http.put<{ message: string }>(`${this.apiBase}/recipients/${id}/description`, { description });
  }

  updateRecipientCompany(id: string, companyId: string | null): Observable<{ message: string; modifiedCount: number }> {
    return this.http.put<{ message: string; modifiedCount: number }>(`${this.apiBase}/recipients/${id}/company`, {
      companyId
    });
  }

  deleteRecipient(id: string): Observable<{ message: string }> {
    return this.http.delete<{ message: string }>(`${this.apiBase}/recipients/${id}`);
  }

  generateRecipientCoverLetter(id: string): Observable<{ message: string }> {
    return this.http.post<{ message: string }>(`${this.apiBase}/recipients/${id}/generate-cover-letter`, {});
  }

  // Identities
  getIdentities(): Observable<Identity[]> {
    return this.http.get<Identity[]>(`${this.apiBase}/identities`)
      .pipe(catchError(() => of([])));
  }

  // Job Descriptions
  listJobDescriptions(): Observable<JobDescription[]> {
    return this.http.get<JobDescription[]>(`${this.apiBase}/job-descriptions`);
  }

  getJobDescriptions(): Observable<JobDescription[]> {
    return this.listJobDescriptions()
      .pipe(catchError(() => of([])));
  }

  getJobDescription(id: string): Observable<JobDescription> {
    return this.http.get<JobDescription>(`${this.apiBase}/job-descriptions/${id}`)
      .pipe(catchError(() => of({} as JobDescription)));
  }

  getJobPreferenceScores(filters?: { jobId?: string; identityId?: string }): Observable<JobPreferenceScore[]> {
    const params = new URLSearchParams();
    if (filters?.jobId) {
      params.set('job_id', filters.jobId);
    }
    if (filters?.identityId) {
      params.set('identity_id', filters.identityId);
    }

    const query = params.toString();
    const url = query
      ? `${this.apiBase}/job-preference-scores?${query}`
      : `${this.apiBase}/job-preference-scores`;

    return this.http.get<JobPreferenceScore[]>(url)
      .pipe(catchError(() => of([])));
  }

  scoreJobDescription(id: string, identityId: string): Observable<{ message: string }> {
    return this.http.post<{ message: string }>(`${this.apiBase}/job-descriptions/${id}/score`, { identity_id: identityId });
  }

  checkJobDescription(id: string, identityId: string): Observable<{ message: string }> {
    return this.http.post<{ message: string }>(`${this.apiBase}/job-descriptions/${id}/check`, { identity_id: identityId });
  }

  triggerCrawl(identityId: string): Observable<{ message: string; run_id: string; identity_id: string; status: string }> {
    return this.http.post<{ message: string; run_id: string; identity_id: string; status: string }>(`${this.apiBase}/crawls`, {
      identity_id: identityId,
    });
  }

  getActiveCrawls(identityId?: string): Observable<CrawlProgress[]> {
    const url = identityId
      ? `${this.apiBase}/crawls/active?identity_id=${encodeURIComponent(identityId)}`
      : `${this.apiBase}/crawls/active`;

    return this.http.get<CrawlProgress[]>(url)
      .pipe(catchError(() => of([])));
  }

  getLastRunWorkflowStats(): Observable<LastRunWorkflowStatsResponse> {
    return this.http.get<LastRunWorkflowStatsResponse>(`${this.apiBase}/crawls/last-run/workflow-stats`)
      .pipe(catchError(() => of({
        completed_at: null,
        workflows: [],
      })));
  }

  getWorkflowCumulativeJobs(): Observable<WorkflowCumulativeJobsResponse> {
    return this.http.get<WorkflowCumulativeJobsResponse>(`${this.apiBase}/crawls/workflow-cumulative-jobs`)
      .pipe(catchError(() => of({ workflows: [] })));
  }

  getActivitySummary(identityId?: string): Observable<ActivitySummaryResponse> {
    const url = identityId
      ? `${this.apiBase}/crawls/activity-summary?identity_id=${encodeURIComponent(identityId)}`
      : `${this.apiBase}/crawls/activity-summary`;

    return this.http.get<ActivitySummaryResponse>(url)
      .pipe(catchError(() => of({
        identity_id: '',
        active_workflows: [],
        global_queue_depth: {
          crawler_trigger: 0,
          crawler_ycombinator: 0,
          crawler_hackernews: 0,
          crawler_ats_job_extraction: 0,
          crawler_levelsfyi: 0,
          crawler_4dayweek: 0,
          crawler_enrichment_ats: 0,
          job_scoring: 0,
        },
      })));
  }

  getActiveScoring(identityId?: string): Observable<ScoringProgress[]> {
    const url = identityId
      ? `${this.apiBase}/scoring/active?identity_id=${encodeURIComponent(identityId)}`
      : `${this.apiBase}/scoring/active`;

    return this.http.get<ScoringProgress[]>(url)
      .pipe(catchError(() => of([])));
  }

  subscribeToCrawlProgress(): Observable<CrawlProgress> {
    return new Observable<CrawlProgress>((observer) => {
      const token = this.authService.getToken();
      if (!token) {
        observer.error(new Error('Missing auth token'));
        return undefined;
      }

      const abortController = new AbortController();

      void fetch(`${this.apiBase}/crawls/stream`, {
        method: 'GET',
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: 'text/event-stream',
        },
        signal: abortController.signal,
      })
        .then(async (response) => {
          if (response.status === 401) {
            this.authService.logout();
            throw new Error('Unauthorized');
          }

          if (!response.ok || !response.body) {
            throw new Error(`Failed to open crawl progress stream (${response.status})`);
          }

          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = '';

          while (true) {
            const { done, value } = await reader.read();
            if (done) {
              break;
            }

            buffer += decoder.decode(value, { stream: true });
            const events = buffer.split('\n\n');
            buffer = events.pop() || '';

            for (const eventChunk of events) {
              const lines = eventChunk
                .split('\n')
                .map((line) => line.trim())
                .filter(Boolean);

              const dataLine = lines.find((line) => line.startsWith('data:'));
              if (!dataLine) {
                continue;
              }

              const payload = dataLine.slice(5).trim();
              if (!payload) {
                continue;
              }

              observer.next(JSON.parse(payload) as CrawlProgress);
            }
          }

          observer.complete();
        })
        .catch((error) => {
          if (abortController.signal.aborted) {
            return;
          }
          observer.error(error);
        });

      return () => abortController.abort();
    });
  }

  subscribeToScoringProgress(): Observable<ScoringProgress> {
    return new Observable<ScoringProgress>((observer) => {
      const token = this.authService.getToken();
      if (!token) {
        observer.error(new Error('Missing auth token'));
        return undefined;
      }

      const abortController = new AbortController();

      void fetch(`${this.apiBase}/scoring/stream`, {
        method: 'GET',
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: 'text/event-stream',
        },
        signal: abortController.signal,
      })
        .then(async (response) => {
          if (response.status === 401) {
            this.authService.logout();
            throw new Error('Unauthorized');
          }

          if (!response.ok || !response.body) {
            throw new Error(`Failed to open scoring progress stream (${response.status})`);
          }

          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = '';

          while (true) {
            const { done, value } = await reader.read();
            if (done) {
              break;
            }

            buffer += decoder.decode(value, { stream: true });
            const events = buffer.split('\n\n');
            buffer = events.pop() || '';

            for (const eventChunk of events) {
              const lines = eventChunk
                .split('\n')
                .map((line) => line.trim())
                .filter(Boolean);

              const dataLine = lines.find((line) => line.startsWith('data:'));
              if (!dataLine) {
                continue;
              }

              const payload = dataLine.slice(5).trim();
              if (!payload) {
                continue;
              }

              observer.next(JSON.parse(payload) as ScoringProgress);
            }
          }

          observer.complete();
        })
        .catch((error) => {
          if (abortController.signal.aborted) {
            return;
          }
          observer.error(error);
        });

      return () => abortController.abort();
    });
  }

  subscribeToJobUpdates(): Observable<JobUpdateEvent> {
    return new Observable<JobUpdateEvent>((observer) => {
      const token = this.authService.getToken();
      if (!token) {
        observer.error(new Error('Missing auth token'));
        return undefined;
      }

      const abortController = new AbortController();

      void fetch(`${this.apiBase}/job-descriptions/stream`, {
        method: 'GET',
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: 'text/event-stream',
        },
        signal: abortController.signal,
      })
        .then(async (response) => {
          if (response.status === 401) {
            this.authService.logout();
            throw new Error('Unauthorized');
          }

          if (!response.ok || !response.body) {
            throw new Error(`Failed to open job updates stream (${response.status})`);
          }

          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = '';

          while (true) {
            const { done, value } = await reader.read();
            if (done) {
              break;
            }

            buffer += decoder.decode(value, { stream: true });
            const events = buffer.split('\n\n');
            buffer = events.pop() || '';

            for (const eventChunk of events) {
              const lines = eventChunk
                .split('\n')
                .map((line) => line.trim())
                .filter(Boolean);

              const dataLine = lines.find((line) => line.startsWith('data:'));
              if (!dataLine) {
                continue;
              }

              const payload = dataLine.slice(5).trim();
              if (!payload) {
                continue;
              }

              observer.next(JSON.parse(payload) as JobUpdateEvent);
            }
          }

          observer.complete();
        })
        .catch((error) => {
          if (abortController.signal.aborted) {
            return;
          }
          observer.error(error);
        });

      return () => abortController.abort();
    });
  }

  // Cover Letters
  getCoverLetters(): Observable<CoverLetter[]> {
    return this.http.get<CoverLetter[]>(`${this.apiBase}/cover-letters`)
      .pipe(catchError(() => of([])));
  }

  getCoverLetter(id: string): Observable<CoverLetter> {
    return this.http.get<CoverLetter>(`${this.apiBase}/cover-letters/${id}`)
      .pipe(catchError(() => of({} as CoverLetter)));
  }

  // Dashboard aggregation methods
  async getActiveApplicationsCount(): Promise<number> {
    try {
      const coverLetters = await this.getCoverLetters().toPromise();
      return coverLetters?.length || 0;
    } catch {
      return 0;
    }
  }

  async getTotalJobsScrapedCount(): Promise<number> {
    try {
      const jobs = await this.getJobDescriptions().toPromise();
      return jobs?.length || 0;
    } catch {
      return 0;
    }
  }

  async getTopScoredJobsCount(): Promise<number> {
    try {
      const scores = await this.getJobPreferenceScores().toPromise();
      if (!scores) return 0;
      return Array.from(this.getBestScoreByJob(scores).values())
        .filter((score) => (score.weighted_score || 0) >= 4.0)
        .length;
    } catch {
      return 0;
    }
  }

  async getSentLettersCount(): Promise<number> {
    try {
      const coverLetters = await this.getCoverLetters().toPromise();
      if (!coverLetters) return 0;
      // Count letters with status 'sent' or 'delivered'
      return coverLetters.filter(cl => cl.status === 'sent' || cl.status === 'delivered').length;
    } catch {
      return 0;
    }
  }

  async getTopScoredJobs(limit: number = 5): Promise<ScoredJobDescription[]> {
    try {
      const [jobs, scores] = await Promise.all([
        this.getJobDescriptions().toPromise(),
        this.getJobPreferenceScores().toPromise(),
      ]);
      if (!jobs || !scores) return [];

      const bestScoreByJob = this.getBestScoreByJob(scores);

      return jobs
        .map((job) => ({
          ...job,
          score: bestScoreByJob.get(job.id) || null,
        }))
        .filter((job) => !!job.score)
        .sort((left, right) => (right.score?.weighted_score || 0) - (left.score?.weighted_score || 0))
        .slice(0, limit);
    } catch {
      return [];
    }
  }

  private getBestScoreByJob(scores: JobPreferenceScore[]): Map<string, JobPreferenceScore> {
    const bestScoreByJob = new Map<string, JobPreferenceScore>();

    scores.forEach((score) => {
      const existing = bestScoreByJob.get(score.job_id);
      if (!existing || (score.weighted_score || 0) > (existing.weighted_score || 0)) {
        bestScoreByJob.set(score.job_id, score);
      }
    });

    return bestScoreByJob;
  }
}
