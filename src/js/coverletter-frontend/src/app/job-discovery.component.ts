import { Component, ElementRef, OnDestroy, OnInit, ViewChild, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { Subscription, forkJoin } from 'rxjs';

import { ApiService } from './services/api.service';
import { FeedbackService } from './services/feedback.service';
import { IdentityContextService } from './services/identity-context.service';
import { CrawlProgress, Identity, JobDescription } from './models/models';

type ScoreFilterMode = 'atLeast' | 'exactly' | 'atMost';

@Component({
  selector: 'app-job-discovery',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './job-discovery.component.html',
  styleUrls: ['./job-discovery.component.css']
})
export class JobDiscoveryComponent implements OnInit, OnDestroy {
  private api = inject(ApiService);
  private feedbackService = inject(FeedbackService);
  private identityContext = inject(IdentityContextService);
  private router = inject(Router);
  private route = inject(ActivatedRoute);

  jobs: JobDescription[] = [];
  identities: Identity[] = [];
  hiddenJobIds = new Set<string>();
  selectedJobId = '';

  loading = false;
  reranking = false;
  triggeringCrawl = false;

  selectedIdentityId = '';
  private routeIdentityId = '';
  selectedCompanyId = '';
  selectedCompanyName = '';
  searchQuery = '';
  scoreThreshold = 0.0;
  scoreFilterMode: ScoreFilterMode = 'atLeast';
  readonly scorePresetValues = [0, 1, 2, 3, 4, 5];
  remoteOnly = false;
  aiSkillGapAnalysis = false;
  private crawlStreamSubscription?: Subscription;
  private crawlSnapshotsByIdentity = new Map<string, CrawlProgress>();

  @ViewChild('companyDetailsPanel') private companyDetailsPanel?: ElementRef<HTMLElement>;
  @ViewChild('selectedOpportunitySection') private selectedOpportunitySection?: ElementRef<HTMLElement>;

  ngOnInit(): void {
    this.route.queryParamMap.subscribe((params) => {
      this.selectedCompanyId = params.get('companyId') || '';
      this.selectedCompanyName = params.get('companyName') || '';

      this.routeIdentityId = (params.get('identityId') || '').trim();
      const sharedIdentityId = this.identityContext.getSelectedIdentityId();
      this.selectedIdentityId = this.routeIdentityId || sharedIdentityId;

      if (this.routeIdentityId && this.routeIdentityId !== sharedIdentityId) {
        this.identityContext.setSelectedIdentityId(this.routeIdentityId);
      }
    });

    this.loadData();
    this.subscribeToCrawlProgress();
  }

  ngOnDestroy(): void {
    this.crawlStreamSubscription?.unsubscribe();
  }

  loadData(): void {
    this.loading = true;

    forkJoin({
      jobs: this.api.getJobDescriptions(),
      identities: this.api.getIdentities(),
      activeCrawls: this.api.getActiveCrawls(),
    }).subscribe({
      next: ({ jobs, identities, activeCrawls }) => {
        this.jobs = jobs || [];
        this.identities = identities || [];
        this.setCrawlSnapshots(activeCrawls || []);

        if (this.jobs.length > 0) {
          if (!this.selectedJobId || !this.jobs.some((job) => job.id === this.selectedJobId)) {
            this.selectedJobId = this.jobs[0].id;
          }
        } else {
          this.selectedJobId = '';
        }

        const availableIdentityIds = this.identities.map((identity) => identity.id).filter(Boolean);
        const resolvedIdentityId = this.identityContext.ensureValidIdentityId(availableIdentityIds, this.selectedIdentityId);
        this.selectedIdentityId = resolvedIdentityId;
        if (this.routeIdentityId !== resolvedIdentityId) {
          this.updateIdentityQueryParam(resolvedIdentityId);
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
      .filter((job) => this.matchesIdentity(job))
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

  get identityScopedJobsCount(): number {
    return this.jobs.filter((job) => this.matchesIdentity(job)).length;
  }

  get selectedJob(): JobDescription | null {
    if (!this.selectedJobId) {
      return this.filteredJobs[0] || null;
    }
    return this.filteredJobs.find((job) => job.id === this.selectedJobId) || this.filteredJobs[0] || null;
  }

  get selectedCompanyDisplayName(): string {
    const selected = this.selectedJob;
    if (!selected) {
      return 'Select a job';
    }
    return selected.company_info?.name || selected.company_name || 'Unknown company';
  }

  get selectedCompanyDescription(): string {
    const selected = this.selectedJob;
    return selected?.company_info?.description || 'No company description available for this job yet.';
  }

  get selectedCompanyOpenPositions(): number {
    const selected = this.selectedJob;
    if (!selected) {
      return 0;
    }

    const selectedCompanyId = this.getJobCompanyId(selected);
    if (selectedCompanyId) {
      return this.filteredJobs.filter((job) => this.getJobCompanyId(job) === selectedCompanyId).length;
    }

    const selectedCompanyName = (selected.company_info?.name || selected.company_name || '').trim().toLowerCase();
    if (!selectedCompanyName) {
      return 0;
    }

    return this.filteredJobs.filter((job) => {
      const companyName = (job.company_info?.name || job.company_name || '').trim().toLowerCase();
      return companyName === selectedCompanyName;
    }).length;
  }

  get activeIdentityName(): string {
    return this.identities.find((identity) => identity.id === this.selectedIdentityId)?.name || 'No identity selected';
  }

  get selectedIdentityCrawlProgress(): CrawlProgress | null {
    return this.crawlSnapshotsByIdentity.get(this.selectedIdentityId) || null;
  }

  get selectedIdentityHasActiveCrawl(): boolean {
    const progress = this.selectedIdentityCrawlProgress;
    return progress?.status === 'queued' || progress?.status === 'running';
  }

  get crawlActionLabel(): string {
    if (this.triggeringCrawl) {
      return 'Queueing Crawl...';
    }
    if (this.selectedIdentityHasActiveCrawl) {
      return 'Crawl Running';
    }
    return 'Start Crawl';
  }

  triggerCrawl(): void {
    if (!this.selectedIdentityId) {
      this.feedbackService.showFeedback('Select an identity before starting a crawl.', true);
      return;
    }

    if (this.selectedIdentityHasActiveCrawl) {
      this.feedbackService.showFeedback(`A crawl is already active for ${this.activeIdentityName}.`, true);
      return;
    }

    this.triggeringCrawl = true;
    this.api.triggerCrawl(this.selectedIdentityId).subscribe({
      next: ({ identity_id, run_id }) => {
        this.triggeringCrawl = false;
        this.crawlSnapshotsByIdentity.set(identity_id, {
          run_id,
          identity_id,
          status: 'queued',
          phase: 'queued',
          message: 'Waiting for worker pickup',
          estimated_total: 4,
          completed: 0,
          percent: 0,
          started_at: null,
          updated_at: {
            seconds: Math.floor(Date.now() / 1000),
            nanos: 0,
          },
          finished_at: null,
          reason: '',
        });
        this.feedbackService.showFeedback(`Crawl queued for ${this.activeIdentityName}.`);
      },
      error: (error) => {
        this.triggeringCrawl = false;
        const apiMessage = error?.error?.error || 'Failed to queue crawl.';
        this.feedbackService.showFeedback(apiMessage, true);
      },
    });
  }

  getCrawlPhaseLabel(progress: CrawlProgress | null): string {
    switch (progress?.phase) {
      case 'workflow1_company_discovery':
        return 'Company discovery';
      case 'workflow2_ats_enrichment':
        return 'ATS enrichment';
      case 'workflow3_ats_job_extraction':
        return 'Job extraction';
      case 'workflow4_4dayweek':
        return '4dayweek scraping';
      case 'finalizing':
        return 'Finalizing';
      default:
        return 'Queued';
    }
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

    if (this.selectedJobId === job.id) {
      const replacement = this.filteredJobs.find((candidate) => candidate.id !== job.id);
      this.selectedJobId = replacement?.id || '';
    }

    this.feedbackService.showFeedback(`Hidden ${job.title} from this view.`);
  }

  selectJob(job: JobDescription): void {
    this.selectedJobId = job.id;
    this.focusSelectedOpportunity();
  }

  isSelectedJob(job: JobDescription): boolean {
    return this.selectedJob?.id === job.id;
  }

  getFirstRationale(job: JobDescription): string {
    const rationale = job.scores?.find((score) => score.rationale)?.rationale;
    return rationale || 'No rationale available yet. Trigger reranking to enrich this job.';
  }

  renderJobDescription(description?: string): string {
    const rawDescription = (description || '').trim();
    if (!rawDescription) {
      return 'No job description available for this role yet.';
    }

    const decoded = this.decodeHtmlEntities(rawDescription);

    if (this.looksLikeHtml(decoded)) {
      return decoded;
    }

    return this.escapeHtml(decoded).replace(/\n/g, '<br>');
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

  onIdentityChange(identityId: string): void {
    const normalizedIdentityId = (identityId || '').trim();
    this.selectedIdentityId = normalizedIdentityId;
    this.identityContext.setSelectedIdentityId(normalizedIdentityId);
    this.updateIdentityQueryParam(normalizedIdentityId);
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

  private matchesIdentity(job: JobDescription): boolean {
    if (!this.selectedIdentityId) {
      return true;
    }

    if ((job.scores || []).some((score) => score.identity_id === this.selectedIdentityId)) {
      return true;
    }

    // Fallback for jobs that are not scored yet: match by identity/company field.
    const selectedIdentity = this.identities.find((identity) => identity.id === this.selectedIdentityId);
    if (!selectedIdentity) {
      return false;
    }

    const identityFieldId = selectedIdentity.field_id || selectedIdentity.field_info?.id || '';
    if (!identityFieldId) {
      return false;
    }

    const companyFieldId = job.company_info?.field_id || job.company_info?.field_info?.id || '';
    return companyFieldId === identityFieldId;
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

  private decodeHtmlEntities(value: string): string {
    if (typeof document === 'undefined') {
      return value;
    }

    const textarea = document.createElement('textarea');
    textarea.innerHTML = value;
    return textarea.value;
  }

  private looksLikeHtml(value: string): boolean {
    return /<\/?[a-z][\s\S]*>/i.test(value);
  }

  private escapeHtml(value: string): string {
    return value
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/\"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  private focusSelectedOpportunity(): void {
    requestAnimationFrame(() => {
      const panel = this.companyDetailsPanel?.nativeElement;
      const selectedOpportunity = this.selectedOpportunitySection?.nativeElement;

      if (!panel || !selectedOpportunity) {
        return;
      }

      panel.scrollTo({
        top: Math.max(selectedOpportunity.offsetTop - 8, 0),
        behavior: 'smooth'
      });
    });
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

  private updateIdentityQueryParam(identityId: string): void {
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: {
        identityId: identityId || null
      },
      queryParamsHandling: 'merge'
    });
  }

  private subscribeToCrawlProgress(): void {
    this.crawlStreamSubscription?.unsubscribe();
    this.crawlStreamSubscription = this.api.subscribeToCrawlProgress().subscribe({
      next: (progress) => {
        this.crawlSnapshotsByIdentity.set(progress.identity_id, progress);

        if (progress.identity_id !== this.selectedIdentityId) {
          return;
        }

        if (progress.status === 'completed') {
          this.feedbackService.showFeedback(`Crawl completed for ${this.activeIdentityName}.`);
        }

        if (progress.status === 'failed' || progress.status === 'rejected') {
          this.feedbackService.showFeedback(progress.message || `Crawl ${progress.status} for ${this.activeIdentityName}.`, true);
        }
      },
      error: () => {
        this.feedbackService.showFeedback('Lost crawl progress stream connection.', true);
      },
    });
  }

  private setCrawlSnapshots(snapshots: CrawlProgress[]): void {
    this.crawlSnapshotsByIdentity.clear();
    snapshots.forEach((snapshot) => {
      const existing = this.crawlSnapshotsByIdentity.get(snapshot.identity_id);
      if (!existing || this.getTimestampSeconds(snapshot.updated_at) >= this.getTimestampSeconds(existing.updated_at)) {
        this.crawlSnapshotsByIdentity.set(snapshot.identity_id, snapshot);
      }
    });
  }

  private getTimestampSeconds(value?: string | { seconds: number; nanos: number } | null): number {
    if (!value) {
      return 0;
    }
    if (typeof value === 'string') {
      return Math.floor(new Date(value).getTime() / 1000);
    }
    return value.seconds || 0;
  }
}
