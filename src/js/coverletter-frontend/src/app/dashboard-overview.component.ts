import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ApiService } from './services/api.service';
import { FeedbackService } from './services/feedback.service';
import { CrawlProgress, JobDescription } from './models/models';
import { Subscription } from 'rxjs';

@Component({
  selector: 'app-dashboard-overview',
  standalone: true,
  imports: [CommonModule],
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
  topScoredJobs: JobDescription[] = [];
  loadingJobs = false;
  loadingStats = false;
  activeCrawl: CrawlProgress | null = null;
  private crawlStreamSubscription?: Subscription;

  // Placeholder data for now (will replace with real data from API)
  placeholderJobs: JobDescription[] = [
    {
      id: '1',
      title: 'Senior Product Designer',
      description: 'We are looking for a Senior Product Designer...',
      location: 'Remote, US',
      platform: 'Ashby',
      external_job_id: 'ashby_1',
      source_url: 'https://ashby.example.com/1',
      company_name: 'Ashby',
      weighted_score: 4.9,
    } as JobDescription,
    {
      id: '2',
      title: 'Principal Engineer',
      description: 'Lead our technical strategy and architecture...',
      location: 'European Timezones',
      platform: '4dayweek.io',
      external_job_id: '4dw_1',
      source_url: 'https://4dayweek.io/1',
      company_name: '4dayweek.io',
      weighted_score: 4.7,
    } as JobDescription,
    {
      id: '3',
      title: 'Creative Technologist',
      description: 'Join our creative studio and build amazing digital experiences...',
      location: 'Hybrid, NYC',
      platform: 'Vercel',
      external_job_id: 'vercel_1',
      source_url: 'https://vercel.com/1',
      company_name: 'Vercel',
      weighted_score: 4.5,
    } as JobDescription
  ];

  ngOnInit(): void {
    this.loadStats();
    this.loadTopScoredJobs();
    this.loadActiveCrawls();
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
      // For MVP: use placeholder data
      // TODO: Replace with real API call once backend supports scoring
      this.topScoredJobs = this.placeholderJobs;
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
    switch (this.activeCrawl?.phase) {
      case 'workflow1_company_discovery':
        return 'Company discovery';
      case 'enrichment_ats_enrichment':
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

  getCompanyInitials(company: string): string {
    return company
      .split(' ')
      .map(word => word[0])
      .join('')
      .toUpperCase()
      .slice(0, 2);
  }

  private loadActiveCrawls(): void {
    this.apiService.getActiveCrawls().subscribe({
      next: (crawls) => {
        this.activeCrawl = this.pickMostRelevantCrawl(crawls);
      },
    });
  }

  private subscribeToCrawlProgress(): void {
    this.crawlStreamSubscription = this.apiService.subscribeToCrawlProgress().subscribe({
      next: (progress) => {
        this.activeCrawl = this.pickMostRelevantCrawl([
          ...(this.activeCrawl ? [this.activeCrawl] : []),
          progress,
        ]);
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

  private getStatusRank(status: CrawlProgress['status']): number {
    if (status === 'running') {
      return 3;
    }
    if (status === 'queued') {
      return 2;
    }
    return 1;
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
