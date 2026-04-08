import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { forkJoin } from 'rxjs';

import { ApiService } from './services/api.service';
import { FeedbackService } from './services/feedback.service';
import { Identity, JobDescription } from './models/models';

type ScoreFilterMode = 'atLeast' | 'exactly' | 'atMost';

@Component({
  selector: 'app-job-discovery',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './job-discovery.component.html',
  styleUrls: ['./job-discovery.component.css']
})
export class JobDiscoveryComponent implements OnInit {
  private api = inject(ApiService);
  private feedbackService = inject(FeedbackService);
  private router = inject(Router);
  private route = inject(ActivatedRoute);

  jobs: JobDescription[] = [];
  identities: Identity[] = [];
  hiddenJobIds = new Set<string>();

  loading = false;
  reranking = false;

  selectedIdentityId = '';
  selectedCompanyId = '';
  selectedCompanyName = '';
  searchQuery = '';
  scoreThreshold = 0.0;
  scoreFilterMode: ScoreFilterMode = 'atLeast';
  readonly scorePresetValues = [0, 1, 2, 3, 4, 5];
  remoteOnly = false;
  aiSkillGapAnalysis = false;

  ngOnInit(): void {
    this.route.queryParamMap.subscribe((params) => {
      this.selectedCompanyId = params.get('companyId') || '';
      this.selectedCompanyName = params.get('companyName') || '';
    });

    this.loadData();
  }

  loadData(): void {
    this.loading = true;

    forkJoin({
      jobs: this.api.getJobDescriptions(),
      identities: this.api.getIdentities()
    }).subscribe({
      next: ({ jobs, identities }) => {
        this.jobs = jobs || [];
        this.identities = identities || [];
        if (!this.selectedIdentityId && this.identities.length > 0) {
          this.selectedIdentityId = this.identities[0].id;
        }
        this.loading = false;
      },
      error: () => {
        this.loading = false;
        this.feedbackService.showFeedback('Failed to load Job Discovery data.', true);
      }
    });
  }

  get filteredJobs(): JobDescription[] {
    return this.jobs
      .filter((job) => !this.hiddenJobIds.has(job.id))
      .filter((job) => this.matchesCompany(job))
      .filter((job) => this.passesScoreFilter(job))
      .filter((job) => this.matchesSearch(job))
      .filter((job) => !this.remoteOnly || this.isRemote(job.location))
      .sort((a, b) => (b.weighted_score || 0) - (a.weighted_score || 0));
  }

  get activeCompanyLabel(): string {
    if (!this.selectedCompanyId) {
      return '';
    }

    const matchingJob = this.jobs.find((job) => this.getJobCompanyId(job) === this.selectedCompanyId);
    return this.selectedCompanyName || matchingJob?.company_info?.name || matchingJob?.company_name || 'Selected company';
  }

  get scoreFilterLabel(): string {
    switch (this.scoreFilterMode) {
      case 'exactly':
        return `Score = ${this.scoreThreshold.toFixed(1)}`;
      case 'atMost':
        return `Score <= ${this.scoreThreshold.toFixed(1)}`;
      default:
        return `Score >= ${this.scoreThreshold.toFixed(1)}`;
    }
  }

  get activeIdentityName(): string {
    return this.identities.find((identity) => identity.id === this.selectedIdentityId)?.name || 'No identity selected';
  }

  rerankVisibleJobs(): void {
    const jobsToScore = this.filteredJobs.slice(0, 20);
    if (jobsToScore.length === 0) {
      this.feedbackService.showFeedback('No visible jobs to rerank.', true);
      return;
    }

    this.reranking = true;
    let completed = 0;
    let failed = 0;

    jobsToScore.forEach((job) => {
      this.api.scoreJobDescription(job.id).subscribe({
        next: () => {
          completed += 1;
          if (completed + failed === jobsToScore.length) {
            this.finishRerank(completed, failed);
          }
        },
        error: () => {
          failed += 1;
          if (completed + failed === jobsToScore.length) {
            this.finishRerank(completed, failed);
          }
        }
      });
    });
  }

  rerankSingleJob(job: JobDescription): void {
    this.api.scoreJobDescription(job.id).subscribe({
      next: () => this.feedbackService.showFeedback(`Scoring queued for ${job.title}.`),
      error: () => this.feedbackService.showFeedback(`Failed to queue scoring for ${job.title}.`, true)
    });
  }

  prepareCoverLetter(job: JobDescription): void {
    this.router.navigate(['/dashboard/cover-letters'], {
      queryParams: { jobId: job.id }
    });
    this.feedbackService.showFeedback(`Opened cover letters for ${job.title}.`);
  }

  markAsNotInterested(job: JobDescription): void {
    this.hiddenJobIds.add(job.id);
    this.feedbackService.showFeedback(`Hidden ${job.title} from this view.`);
  }

  getFirstRationale(job: JobDescription): string {
    const rationale = job.scores?.find((score) => score.rationale)?.rationale;
    return rationale || 'No rationale available yet. Trigger reranking to enrich this job.';
  }

  formatScore(score?: number): string {
    return (score ?? 0).toFixed(1);
  }

  setScorePreset(value: number): void {
    this.scoreThreshold = value;
  }

  updateScoreThreshold(value: number | string): void {
    const parsed = typeof value === 'string' ? parseFloat(value) : value;
    if (Number.isNaN(parsed)) {
      return;
    }
    this.scoreThreshold = Math.max(0, Math.min(5, parsed));
  }

  clearCompanyFilter(): void {
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: {
        companyId: null,
        companyName: null
      },
      queryParamsHandling: 'merge'
    });
  }

  private passesScoreFilter(job: JobDescription): boolean {
    const score = Number(job.weighted_score ?? 0);
    const threshold = this.scoreThreshold;

    switch (this.scoreFilterMode) {
      case 'exactly':
        return Math.abs(score - threshold) < 0.05;
      case 'atMost':
        return score <= threshold;
      default:
        return score >= threshold;
    }
  }

  private matchesCompany(job: JobDescription): boolean {
    if (!this.selectedCompanyId) {
      return true;
    }

    return this.getJobCompanyId(job) === this.selectedCompanyId;
  }

  private matchesSearch(job: JobDescription): boolean {
    if (!this.searchQuery.trim()) {
      return true;
    }

    const q = this.searchQuery.trim().toLowerCase();
    const title = (job.title || '').toLowerCase();
    const company = (job.company_info?.name || job.company_name || '').toLowerCase();
    const description = (job.description || '').toLowerCase();

    return title.includes(q) || company.includes(q) || description.includes(q);
  }

  private isRemote(location?: string): boolean {
    if (!location) {
      return false;
    }
    const normalized = location.toLowerCase();
    return normalized.includes('remote') || normalized.includes('worldwide') || normalized.includes('anywhere');
  }

  private getJobCompanyId(job: JobDescription): string {
    return job.company_info?.id || job.company_id || '';
  }

  private finishRerank(completed: number, failed: number): void {
    this.reranking = false;

    if (failed === 0) {
      this.feedbackService.showFeedback(`Queued reranking for ${completed} jobs.`);
      return;
    }

    this.feedbackService.showFeedback(`Queued ${completed} jobs, ${failed} failed.`, failed > 0);
  }
}
