import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ApiService } from './services/api.service';
import { FeedbackService } from './services/feedback.service';
import { JobDescription } from './models/models';

@Component({
  selector: 'app-dashboard-overview',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './dashboard-overview.component.html',
  styleUrls: ['./dashboard-overview.component.css']
})
export class DashboardOverviewComponent implements OnInit {
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

  getCompanyInitials(company: string): string {
    return company
      .split(' ')
      .map(word => word[0])
      .join('')
      .toUpperCase()
      .slice(0, 2);
  }
}
