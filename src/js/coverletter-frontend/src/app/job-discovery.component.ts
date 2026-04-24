import { Component, ElementRef, OnDestroy, OnInit, ViewChild, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { Subscription, forkJoin } from 'rxjs';

import { ApiService } from './core/services/api.service';
import { FeedbackService } from './core/services/feedback.service';
import { IdentityContextService } from './core/services/identity-context.service';
import { CrawlProgress, Identity, JobDescription, JobPreferenceScore, JobUpdateEvent, ScoredJobDescription, ScoringProgress } from './shared/models/models';
import { getCrawlSnapshotKey, getCrawlStatusRank, getWorkflowLabel } from './shared/utils/workflow-utils';

type ScoreFilterMode = 'atLeast' | 'exactly' | 'atMost';
type ProgressSource = 'crawl' | 'scoring';

interface ActiveProgressSnapshot {
  source: ProgressSource;
  run_id: string;
  identity_id: string;
  status: string;
  message?: string;
  estimated_total: number;
  completed: number;
  percent: number;
}

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

  rawJobs: JobDescription[] = [];
  jobScores: JobPreferenceScore[] = [];
  jobs: ScoredJobDescription[] = [];
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
  private scoringStreamSubscription?: Subscription;
  private jobUpdateStreamSubscription?: Subscription;
  private crawlSnapshotsByKey = new Map<string, CrawlProgress>();
  private scoringSnapshotsByIdentity = new Map<string, ScoringProgress>();
  private completedProgressEvents = new Set<string>();
  private checkedJobIds = new Set<string>();

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
    this.subscribeToScoringProgress();
    this.subscribeToJobUpdates();
  }

  ngOnDestroy(): void {
    this.crawlStreamSubscription?.unsubscribe();
    this.scoringStreamSubscription?.unsubscribe();
    this.jobUpdateStreamSubscription?.unsubscribe();
  }

  loadData(): void {
    this.loading = true;

    forkJoin({
      jobs: this.api.getJobDescriptions(),
      scores: this.api.getJobPreferenceScores(),
      identities: this.api.getIdentities(),
      activeCrawls: this.api.getActiveCrawls(),
      activeScoring: this.api.getActiveScoring(),
    }).subscribe({
      next: ({ jobs, scores, identities, activeCrawls, activeScoring }) => {
        this.rawJobs = jobs || [];
        this.jobScores = scores || [];
        this.identities = identities || [];
        this.setCrawlSnapshots(activeCrawls || []);
        this.setScoringSnapshots(activeScoring || []);

        const availableIdentityIds = this.identities.map((identity) => identity.id).filter(Boolean);
        const resolvedIdentityId = this.identityContext.ensureValidIdentityId(availableIdentityIds, this.selectedIdentityId);
        this.selectedIdentityId = resolvedIdentityId;
        if (this.routeIdentityId !== resolvedIdentityId) {
          this.updateIdentityQueryParam(resolvedIdentityId);
        }

        this.applyScoresToJobs();
        this.checkDisplayedJobs();

        this.loading = false;
      },
      error: () => {
        this.loading = false;
        this.feedbackService.showFeedback('Failed to load Job Discovery data.', true);
      }
    });
  }

  get filteredJobs(): ScoredJobDescription[] {
    return this.jobs
      .filter((job) => !this.hiddenJobIds.has(job.id))
      .filter((job) => this.matchesIdentity(job))
      .filter((job) => this.matchesCompany(job))
      .filter((job) => this.passesScoreFilter(job))
      .filter((job) => this.matchesSearch(job))
      .filter((job) => !this.remoteOnly || this.isRemote(job.location))
      .sort((a, b) => this.getScoreValue(b) - this.getScoreValue(a));
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

  get selectedJob(): ScoredJobDescription | null {
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
    const progress = this.pickMostRelevantCrawl(this.getCrawlSnapshotsForIdentity(this.selectedIdentityId));
    if (!progress) {
      return null;
    }
    // Ensure completed and percent are properly initialized
    return {
      ...progress,
      completed: progress.completed ?? 0,
      percent: progress.percent ?? 0,
      estimated_total: progress.estimated_total ?? 0,
    };
  }

  get selectedIdentityScoringProgress(): ScoringProgress | null {
    const progress = this.scoringSnapshotsByIdentity.get(this.selectedIdentityId);
    if (!progress) {
      return null;
    }
    return {
      ...progress,
      completed: progress.completed ?? 0,
      percent: progress.percent ?? 0,
      estimated_total: progress.estimated_total ?? 0,
    };
  }

  get selectedIdentityActiveProgress(): ActiveProgressSnapshot | null {
    const crawl = this.selectedIdentityCrawlProgress;
    const scoring = this.selectedIdentityScoringProgress;

    if (crawl && this.isActiveProgressStatus(crawl.status)) {
      return this.toActiveProgressSnapshot('crawl', crawl);
    }

    if (scoring && this.isActiveProgressStatus(scoring.status)) {
      return this.toActiveProgressSnapshot('scoring', scoring);
    }

    if (crawl) {
      return this.toActiveProgressSnapshot('crawl', crawl);
    }

    if (scoring) {
      return this.toActiveProgressSnapshot('scoring', scoring);
    }

    return null;
  }

  get selectedIdentityHasActiveCrawl(): boolean {
    const progress = this.selectedIdentityCrawlProgress;
    return progress?.status === 'queued' || progress?.status === 'running';
  }

  isActiveProgressStatus(status: string): boolean {
    return status === 'queued' || status === 'running';
  }

  getProgressPhaseLabel(progress: ActiveProgressSnapshot): string {
    if (progress.source === 'crawl') {
      return getWorkflowLabel(this.selectedIdentityCrawlProgress?.workflow_id || this.selectedIdentityCrawlProgress?.workflow);
    }
    return 'AI scoring';
  }

  getProgressSourceLabel(progress: ActiveProgressSnapshot): string {
    return progress.source === 'crawl' ? 'Crawl' : 'Scoring';
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
        const queuedProgress: CrawlProgress = {
          run_id,
          identity_id,
          status: 'queued',
          workflow: 'queued',
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
        };
        this.crawlSnapshotsByKey.set(getCrawlSnapshotKey(queuedProgress), queuedProgress);
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
    return getWorkflowLabel(progress?.workflow_id || progress?.workflow);
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

  rerankSingleJob(job: ScoredJobDescription): void {
    this.api.scoreJobDescription(job.id).subscribe({
      next: () => this.feedbackService.showFeedback(`Scoring queued for ${job.title}.`),
      error: () => this.feedbackService.showFeedback(`Failed to queue scoring for ${job.title}.`, true)
    });
  }

  prepareCoverLetter(job: ScoredJobDescription): void {
    this.router.navigate(['/dashboard/cover-letters'], {
      queryParams: { jobId: job.id }
    });
    this.feedbackService.showFeedback(`Opened cover letters for ${job.title}.`);
  }

  markAsNotInterested(job: ScoredJobDescription): void {
    this.hiddenJobIds.add(job.id);

    if (this.selectedJobId === job.id) {
      const replacement = this.filteredJobs.find((candidate) => candidate.id !== job.id);
      this.selectedJobId = replacement?.id || '';
    }

    this.feedbackService.showFeedback(`Hidden ${job.title} from this view.`);
  }

  selectJob(job: ScoredJobDescription): void {
    this.selectedJobId = job.id;
    this.focusSelectedOpportunity();
  }

  isSelectedJob(job: ScoredJobDescription): boolean {
    return this.selectedJob?.id === job.id;
  }

  getFirstRationale(job: ScoredJobDescription): string {
    const rationale = job.score?.preference_scores
      .find((preferenceScore) => preferenceScore.rationale)
      ?.rationale;
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
    this.applyScoresToJobs();
  }

  private passesScoreFilter(job: ScoredJobDescription): boolean {
    const score = this.getScoreValue(job);
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

  private matchesCompany(job: ScoredJobDescription): boolean {
    if (!this.selectedCompanyId) {
      return true;
    }

    return this.getJobCompanyId(job) === this.selectedCompanyId;
  }

  private matchesIdentity(job: ScoredJobDescription): boolean {
    if (!this.selectedIdentityId) {
      return true;
    }

    if (job.score?.identity_id === this.selectedIdentityId) {
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

  private matchesSearch(job: ScoredJobDescription): boolean {
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

  private getJobCompanyId(job: ScoredJobDescription): string {
    return job.company_info?.id || job.company_id || '';
  }

  private getScoreValue(job: ScoredJobDescription): number {
    return Number(job.score?.weighted_score ?? 0);
  }

  private getPreferredScoreForJob(jobId: string): JobPreferenceScore | null {
    const scores = this.jobScores.filter((score) => score.job_id === jobId);
    if (scores.length === 0) {
      return null;
    }

    if (this.selectedIdentityId) {
      return scores.find((score) => score.identity_id === this.selectedIdentityId) || null;
    }

    return scores.reduce<JobPreferenceScore | null>((best, current) => {
      if (!best) {
        return current;
      }
      return (current.weighted_score || 0) > (best.weighted_score || 0) ? current : best;
    }, null);
  }

  private applyScoresToJobs(): void {
    this.jobs = this.rawJobs.map((job) => ({
      ...job,
      score: this.getPreferredScoreForJob(job.id),
    }));

    if (this.jobs.length > 0) {
      if (!this.selectedJobId || !this.jobs.some((job) => job.id === this.selectedJobId)) {
        this.selectedJobId = this.jobs[0].id;
      }
      return;
    }

    this.selectedJobId = '';
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
        this.crawlSnapshotsByKey.set(getCrawlSnapshotKey(progress), progress);

        if (progress.identity_id !== this.selectedIdentityId) {
          return;
        }

        if (progress.status === 'completed') {
          this.feedbackService.showFeedback(`Crawl completed for ${this.activeIdentityName}.`);
        }

        if (progress.status === 'failed' || progress.status === 'rejected') {
          this.feedbackService.showFeedback(progress.message || `Crawl ${progress.status} for ${this.activeIdentityName}.`, true);
        }

        this.refreshJobsOnTerminalProgress('crawl', progress.identity_id, progress.run_id, progress.status);
      },
      error: () => {
        this.feedbackService.showFeedback('Lost crawl progress stream connection.', true);
      },
    });
  }

  private subscribeToScoringProgress(): void {
    this.scoringStreamSubscription?.unsubscribe();
    this.scoringStreamSubscription = this.api.subscribeToScoringProgress().subscribe({
      next: (progress) => {
        this.scoringSnapshotsByIdentity.set(progress.identity_id, progress);

        if (progress.identity_id === this.selectedIdentityId && progress.status === 'completed') {
          this.feedbackService.showFeedback(`Scoring completed for ${this.activeIdentityName}.`);
        }

        if (progress.identity_id === this.selectedIdentityId && progress.status === 'failed') {
          this.feedbackService.showFeedback(progress.message || `Scoring failed for ${this.activeIdentityName}.`, true);
        }

        this.refreshJobsOnTerminalProgress('scoring', progress.identity_id, progress.run_id, progress.status);
      },
      error: () => {
        this.feedbackService.showFeedback('Lost scoring progress stream connection.', true);
      },
    });
  }

  private setCrawlSnapshots(snapshots: CrawlProgress[]): void {
    this.crawlSnapshotsByKey.clear();
    snapshots.forEach((snapshot) => {
      this.crawlSnapshotsByKey.set(getCrawlSnapshotKey(snapshot), snapshot);
    });
  }

  private setScoringSnapshots(snapshots: ScoringProgress[]): void {
    this.scoringSnapshotsByIdentity.clear();
    snapshots.forEach((snapshot) => {
      const existing = this.scoringSnapshotsByIdentity.get(snapshot.identity_id);
      if (!existing || this.getTimestampSeconds(snapshot.updated_at) >= this.getTimestampSeconds(existing.updated_at)) {
        this.scoringSnapshotsByIdentity.set(snapshot.identity_id, snapshot);
      }
    });
  }

  private toActiveProgressSnapshot(source: ProgressSource, progress: CrawlProgress | ScoringProgress): ActiveProgressSnapshot {
    return {
      source,
      run_id: progress.run_id,
      identity_id: progress.identity_id,
      status: progress.status,
      message: progress.message,
      estimated_total: progress.estimated_total ?? 0,
      completed: progress.completed ?? 0,
      percent: progress.percent ?? 0,
    };
  }

  private getCrawlSnapshotsForIdentity(identityId: string): CrawlProgress[] {
    if (!identityId) {
      return [];
    }

    return Array.from(this.crawlSnapshotsByKey.values())
      .filter((snapshot) => snapshot.identity_id === identityId);
  }

  private pickMostRelevantCrawl(crawls: CrawlProgress[]): CrawlProgress | null {
    if (!crawls.length) {
      return null;
    }

    const prioritized = [...crawls].sort((left, right) => {
      const leftRank = getCrawlStatusRank(left.status);
      const rightRank = getCrawlStatusRank(right.status);
      if (leftRank !== rightRank) {
        return rightRank - leftRank;
      }

      return this.getTimestampSeconds(right.updated_at) - this.getTimestampSeconds(left.updated_at);
    });

    return prioritized[0] || null;
  }

  private refreshJobsOnTerminalProgress(source: ProgressSource, identityId: string, runId: string, status: string): void {
    if (!identityId || !runId) {
      return;
    }

    if (identityId !== this.selectedIdentityId) {
      return;
    }

    if (status !== 'completed' && status !== 'failed' && status !== 'rejected') {
      return;
    }

    const completionKey = `${source}:${identityId}:${runId}:${status}`;
    if (this.completedProgressEvents.has(completionKey)) {
      return;
    }

    this.completedProgressEvents.add(completionKey);
    this.loadData();
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

  private checkDisplayedJobs(): void {
    this.filteredJobs.forEach((job) => {
      if (!job.id || this.checkedJobIds.has(job.id)) {
        return;
      }
      this.checkedJobIds.add(job.id);
      this.api.checkJobDescription(job.id).subscribe({ error: () => {} });
    });
  }

  private subscribeToJobUpdates(): void {
    this.jobUpdateStreamSubscription?.unsubscribe();
    this.jobUpdateStreamSubscription = this.api.subscribeToJobUpdates().subscribe({
      next: (event: JobUpdateEvent) => {
        if (event.job_id) {
          this.reloadSingleJob(event.job_id);
        }
      },
      error: () => {
        this.feedbackService.showFeedback('Lost job updates stream connection.', true);
      },
    });
  }

  private reloadSingleJob(jobId: string): void {
    this.api.getJobDescription(jobId).subscribe({
      next: (updatedJob) => {
        if (!updatedJob?.id) {
          return;
        }
        const index = this.rawJobs.findIndex((j) => j.id === jobId);
        if (index >= 0) {
          this.rawJobs[index] = updatedJob;
        } else {
          this.rawJobs = [...this.rawJobs, updatedJob];
        }
        this.applyScoresToJobs();
      },
      error: () => {},
    });
  }
}
