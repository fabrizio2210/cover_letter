import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, of } from 'rxjs';
import { catchError } from 'rxjs/operators';
import { Field, Company, Recipient, Identity, JobDescription, CoverLetter } from '../models/models';

@Injectable({
  providedIn: 'root'
})
export class ApiService {
  private apiBase = '/api';

  constructor(private http: HttpClient) {}

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

  scoreJobDescription(id: string): Observable<{ message: string }> {
    return this.http.post<{ message: string }>(`${this.apiBase}/job-descriptions/${id}/score`, {});
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
      const jobs = await this.getJobDescriptions().toPromise();
      if (!jobs) return 0;
      // Count jobs with weighted_score >= 4.0 (top tier)
      return jobs.filter(j => (j.weighted_score || 0) >= 4.0).length;
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

  async getTopScoredJobs(limit: number = 5): Promise<JobDescription[]> {
    try {
      const jobs = await this.getJobDescriptions().toPromise();
      if (!jobs) return [];
      // Sort by weighted_score descending and return top N
      return jobs
        .sort((a, b) => (b.weighted_score || 0) - (a.weighted_score || 0))
        .slice(0, limit);
    } catch {
      return [];
    }
  }
}
