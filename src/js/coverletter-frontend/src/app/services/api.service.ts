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
  getFields(): Observable<Field[]> {
    return this.http.get<Field[]>(`${this.apiBase}/fields`)
      .pipe(catchError(() => of([])));
  }

  // Companies
  getCompanies(): Observable<Company[]> {
    return this.http.get<Company[]>(`${this.apiBase}/companies`)
      .pipe(catchError(() => of([])));
  }

  // Recipients
  getRecipients(): Observable<Recipient[]> {
    return this.http.get<Recipient[]>(`${this.apiBase}/recipients`)
      .pipe(catchError(() => of([])));
  }

  // Identities
  getIdentities(): Observable<Identity[]> {
    return this.http.get<Identity[]>(`${this.apiBase}/identities`)
      .pipe(catchError(() => of([])));
  }

  // Job Descriptions
  getJobDescriptions(): Observable<JobDescription[]> {
    return this.http.get<JobDescription[]>(`${this.apiBase}/job-descriptions`)
      .pipe(catchError(() => of([])));
  }

  getJobDescription(id: string): Observable<JobDescription> {
    return this.http.get<JobDescription>(`${this.apiBase}/job-descriptions/${id}`)
      .pipe(catchError(() => of({} as JobDescription)));
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
