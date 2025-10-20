import { Component, OnInit, inject, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient, HttpHeaders, HttpErrorResponse } from '@angular/common/http';
import { Router } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { FeedbackService } from './services/feedback.service';
import { Subscription } from 'rxjs';

// Recipient Interface (no changes needed)
export interface Recipient {
  _id: string;
  email: string;
  name?: string;
  description?: string;
  fieldInfo?: { _id: string; field: string; } | any; // tolerate array or object

  // NEW: company fields used by template
  companyId?: string;
  companyInfo?: { _id: string; name: string; } | any;
  companyName?: string; // <-- Add this line
}

import { forkJoin, of } from 'rxjs';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule], // <-- Include RouterModule so template routerLink/routerLinkActive work
  templateUrl: './dashboard.component.html',
  styleUrls: ['./dashboard.component.css', './styles/feedback.css']
})
export class DashboardComponent implements OnInit, OnDestroy {
  private http = inject(HttpClient);
  private router = inject(Router);
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

  private getAuthHeaders(): HttpHeaders {
    const token = localStorage.getItem('token');
    if (!token) {
      this.router.navigate(['/login']);
      return new HttpHeaders();
    }
    return new HttpHeaders().set('Authorization', `Bearer ${token}`);
  }

  // --- User Feedback Handling ---
  private showFeedback(message: string, isError = false, error?: HttpErrorResponse): void {
    console.error(error || message);
    if (error?.status === 401) {
      this.router.navigate(['/login']);
    }
    this.feedbackService.showFeedback(message, isError);
  }

  private clearFeedback(): void {
    this.feedbackMessage = '';
    this.isError = false;
  }

}
