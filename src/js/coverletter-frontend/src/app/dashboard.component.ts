import { Component, OnInit, inject, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import { FeedbackService } from './core/services/feedback.service';
import { Subscription } from 'rxjs';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CommonModule, RouterModule],
  templateUrl: './dashboard.component.html',
  styleUrls: ['./dashboard.component.css', './styles/feedback.css']
})
export class DashboardComponent implements OnInit, OnDestroy {
  private feedbackService = inject(FeedbackService);
  private feedbackSubscription?: Subscription;
  private readonly sidebarCollapsedStorageKey = 'dashboard.sidebarCollapsed';

  feedbackMessage = '';
  isError = false;
  isSidebarCollapsed = false;

  ngOnInit(): void {
    this.restoreSidebarPreference();

    this.feedbackSubscription = this.feedbackService.feedback$.subscribe(
      ({ message, isError }) => {
        this.feedbackMessage = message;
        this.isError = isError;
        if (message) {
          setTimeout(() => this.feedbackService.clearFeedback(), 5000);
        }
      }
    );
  }

  ngOnDestroy(): void {
    if (this.feedbackSubscription) {
      this.feedbackSubscription.unsubscribe();
    }
  }

  toggleSidebar(): void {
    this.isSidebarCollapsed = !this.isSidebarCollapsed;

    try {
      localStorage.setItem(this.sidebarCollapsedStorageKey, String(this.isSidebarCollapsed));
    } catch {
      // Ignore storage errors and keep in-memory preference.
    }
  }

  private restoreSidebarPreference(): void {
    try {
      const storedValue = localStorage.getItem(this.sidebarCollapsedStorageKey);
      if (storedValue === null) {
        this.isSidebarCollapsed = false;
        return;
      }

      this.isSidebarCollapsed = storedValue === 'true';
    } catch {
      this.isSidebarCollapsed = false;
    }
  }
}
