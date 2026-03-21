import { Component, OnInit, inject, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import { FeedbackService } from './services/feedback.service';
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

  feedbackMessage = '';
  isError = false;

  ngOnInit(): void {
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
}
