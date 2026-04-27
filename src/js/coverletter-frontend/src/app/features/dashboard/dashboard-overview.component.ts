import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { ApiService } from '../../core/services/api.service';
import { FeedbackService } from '../../core/services/feedback.service';
import { ActivitySummaryResponse, CrawlProgress, LastRunWorkflowStatsItem, LastRunWorkflowStatsResponse, ScoredJobDescription, WorkflowCumulativeJobsItem, WorkflowCumulativeJobsResponse } from '../../shared/models/models';
import { Subscription } from 'rxjs';
import { dashboardWorkflowOrder, getCrawlSnapshotKey, getCrawlStatusRank, getWorkflowLabel } from '../../shared/utils/workflow-utils';

@Component({
  selector: 'app-dashboard-overview',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './dashboard-overview.component.html',
  styleUrls: ['./dashboard-overview.component.css']
})
export class DashboardOverviewComponent implements OnInit, OnDestroy {
  private apiService = inject(ApiService);
  private feedbackService = inject(FeedbackService);

  // Stats
  activeApplicationsCount = 0;
  totalJobsScrapedCount = 0;
  topAiScoredCount = 0;
  sentLettersCount = 0;

  // Top scored jobs feed
  topScoredJobs: ScoredJobDescription[] = [];
  loadingJobs = false;
  loadingStats = false;
  loadingWorkflowStats = false;
  loadingWorkflowCumulativeJobs = false;
  activeCrawl: CrawlProgress | null = null;
  lastRunWorkflowStats: LastRunWorkflowStatsResponse = {
    completed_at: null,
    workflows: [],
  };
  workflowCumulativeJobs: WorkflowCumulativeJobsResponse = {
    workflows: [],
  };
  activitySummary: ActivitySummaryResponse | null = null;
  activitySummaryLoading = false;
  private crawlSnapshotsByKey = new Map<string, CrawlProgress>();
  private crawlStreamSubscription?: Subscription;

  ngOnInit(): void {
    this.loadStats();
    this.loadTopScoredJobs();
    this.loadWorkflowStats();
    this.loadWorkflowCumulativeJobs();
    this.loadActiveCrawls();
    this.loadActivitySummary();
    this.subscribeToCrawlProgress();
  }

  ngOnDestroy(): void {
    this.crawlStreamSubscription?.unsubscribe();
  }

  private async loadStats(): Promise<void> {
    this.loadingStats = true;
    try {
      const [active, total, topScored, sent] = await Promise.all([
        this.apiService.getActiveApplicationsCount(),
        this.apiService.getTotalJobsScrapedCount(),
        this.apiService.getTopScoredJobsCount(),
        this.apiService.getSentLettersCount(),
      ]);

      this.activeApplicationsCount = active;
      this.totalJobsScrapedCount = total;
      this.topAiScoredCount = topScored;
      this.sentLettersCount = sent;
    } catch (error) {
      console.error('Error loading stats:', error);
      // Silently fail - stats will show 0
    } finally {
      this.loadingStats = false;
    }
  }

  private async loadTopScoredJobs(): Promise<void> {
    this.loadingJobs = true;
    try {
      this.topScoredJobs = await this.apiService.getTopScoredJobs();
    } catch (error) {
      console.error('Error loading jobs:', error);
    } finally {
      this.loadingJobs = false;
    }
  }

  draftLetter(jobId: string): void {
    // TODO: Implement draft letter creation from job
    this.feedbackService.showFeedback('Letter drafting coming soon!', false);
  }

  get crawlPhaseLabel(): string {
    return getWorkflowLabel(this.activeCrawl?.workflow_id || this.activeCrawl?.workflow);
  }

  get workflowStatsRows(): LastRunWorkflowStatsItem[] {
    const rowsById = new Map(this.lastRunWorkflowStats.workflows.map((workflow) => [workflow.workflow_id, workflow]));

    return dashboardWorkflowOrder
      .map((workflowId) => rowsById.get(workflowId))
      .filter((workflow): workflow is LastRunWorkflowStatsItem => !!workflow);
  }

  get hasWorkflowStats(): boolean {
    return this.workflowStatsRows.length > 0;
  }

  get workflowCumulativeJobsRows(): WorkflowCumulativeJobsItem[] {
    const rowsById = new Map(
      this.workflowCumulativeJobs.workflows.map((workflow) => [workflow.workflow_id, workflow])
    );

    return dashboardWorkflowOrder.map((workflowId) => {
      const row = rowsById.get(workflowId);
      if (row) {
        return row;
      }
      return {
        workflow_id: workflowId,
        discovered_jobs_cumulative: 0,
      };
    });
  }

  get lastRunCompletedAtLabel(): string {
    const timestamp = this.lastRunWorkflowStats.completed_at;
    if (!timestamp) {
      return '';
    }

    const completedAt = typeof timestamp === 'string'
      ? new Date(timestamp)
      : new Date((timestamp.seconds || 0) * 1000);

    if (Number.isNaN(completedAt.getTime())) {
      return '';
    }

    return completedAt.toLocaleString();
  }

  getCompanyInitials(company: string): string {
    return company
      .split(' ')
      .map(word => word[0])
      .join('')
      .toUpperCase()
      .slice(0, 2);
  }

  getWorkflowLabel(workflowId: LastRunWorkflowStatsItem['workflow_id']): string {
    return getWorkflowLabel(workflowId);
  }

  getCumulativeCounterLabel(workflowId: WorkflowCumulativeJobsItem['workflow_id']): string {
    if (workflowId === 'crawler_ycombinator') {
      return 'Discovered companies';
    }
    return 'Discovered jobs';
  }

  getJobTags(job: ScoredJobDescription): string[] {
    const tags: string[] = [];

    if (job.platform) {
      tags.push(job.platform);
    }

    if (job.location) {
      tags.push(job.location);
    }

    if (job.score?.scoring_status) {
      tags.push(job.score.scoring_status);
    }

    return tags.slice(0, 3);
  }

  private async loadWorkflowStats(): Promise<void> {
    this.loadingWorkflowStats = true;
    try {
      this.lastRunWorkflowStats = await this.apiService.getLastRunWorkflowStats().toPromise() || {
        completed_at: null,
        workflows: [],
      };
    } catch (error) {
      console.error('Error loading workflow stats:', error);
      this.lastRunWorkflowStats = {
        completed_at: null,
        workflows: [],
      };
    } finally {
      this.loadingWorkflowStats = false;
    }
  }

  private async loadWorkflowCumulativeJobs(): Promise<void> {
    this.loadingWorkflowCumulativeJobs = true;
    try {
      this.workflowCumulativeJobs = await this.apiService.getWorkflowCumulativeJobs().toPromise() || {
        workflows: [],
      };
    } catch (error) {
      console.error('Error loading cumulative workflow jobs:', error);
      this.workflowCumulativeJobs = { workflows: [] };
    } finally {
      this.loadingWorkflowCumulativeJobs = false;
    }
  }

  private loadActiveCrawls(): void {
    this.apiService.getActiveCrawls().subscribe({
      next: (crawls) => {
        this.setCrawlSnapshots(crawls);
      },
    });
  }

  private subscribeToCrawlProgress(): void {
    this.crawlStreamSubscription = this.apiService.subscribeToCrawlProgress().subscribe({
      next: (progress) => {
        this.crawlSnapshotsByKey.set(getCrawlSnapshotKey(progress), progress);
        this.activeCrawl = this.pickMostRelevantCrawl(Array.from(this.crawlSnapshotsByKey.values()));
      },
      error: () => {
        // Avoid duplicate toasts here; Job Discovery already surfaces stream failures more directly.
      },
    });
  }

  private pickMostRelevantCrawl(crawls: CrawlProgress[]): CrawlProgress | null {
    if (!crawls.length) {
      return null;
    }

    const prioritized = [...crawls].sort((left, right) => {
      const leftRank = this.getStatusRank(left.status);
      const rightRank = this.getStatusRank(right.status);
      if (leftRank !== rightRank) {
        return rightRank - leftRank;
      }
      return this.getTimestampSeconds(right.updated_at) - this.getTimestampSeconds(left.updated_at);
    });

    const selected = prioritized[0] || null;
    if (!selected) {
      return null;
    }
    // Ensure completed, estimated_total, and percent are properly initialized
    return {
      ...selected,
      completed: selected.completed ?? 0,
      percent: selected.percent ?? 0,
      estimated_total: selected.estimated_total ?? 0,
    };
  }

  private setCrawlSnapshots(crawls: CrawlProgress[]): void {
    this.crawlSnapshotsByKey.clear();
    crawls.forEach((crawl) => {
      this.crawlSnapshotsByKey.set(getCrawlSnapshotKey(crawl), crawl);
    });
    this.activeCrawl = this.pickMostRelevantCrawl(Array.from(this.crawlSnapshotsByKey.values()));
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

  private getStatusRank(status: CrawlProgress['status']): number {
    return getCrawlStatusRank(status);
  }

  private loadActivitySummary(): void {
    this.activitySummaryLoading = true;
    this.apiService.getActivitySummary().subscribe({
      next: (summary) => {
        this.activitySummary = summary;
        this.activitySummaryLoading = false;
      },
      error: () => {
        this.activitySummary = null;
        this.activitySummaryLoading = false;
      }
    });
  }

  isQueueEmpty(queueDepth: any): boolean {
    return queueDepth.crawler_trigger === 0 &&
      queueDepth.crawler_ycombinator === 0 &&
      queueDepth.crawler_ats_job_extraction === 0 &&
      queueDepth.crawler_levelsfyi === 0 &&
      queueDepth.crawler_4dayweek === 0 &&
      queueDepth.crawler_enrichment_ats === 0 &&
      queueDepth.job_scoring === 0;
  }
}
