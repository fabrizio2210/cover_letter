import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient, HttpHeaders, HttpErrorResponse } from '@angular/common/http';
import { Router } from '@angular/router';
import { FormsModule } from '@angular/forms'; // <-- Import FormsModule

// Recipient Interface (no changes needed)
export interface Recipient {
  _id: string;
  email: string;
  name?: string;
  description?: string;
  fieldInfo?: { _id: string; field: string; };
}

import { forkJoin, of } from 'rxjs';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CommonModule, FormsModule], // <-- Add FormsModule
  templateUrl: './dashboard.component.html',
  styleUrls: ['./dashboard.component.css'] // <-- Suggest adding for component-specific styles
})
export class DashboardComponent implements OnInit {
  private http = inject(HttpClient);
  private router = inject(Router);

  recipients: Recipient[] = [];
  feedbackMessage = '';
  isError = false;

  // State for the modal
  showRecipientModal = false;
  isEditMode = false;
  selectedRecipient: Partial<Recipient> = {};
  originalRecipient: Partial<Recipient> = {}; // Store the original state for comparison

  ngOnInit(): void {
    this.getRecipients();
  }

  private getAuthHeaders(): HttpHeaders {
    const token = localStorage.getItem('token');
    if (!token) {
      this.router.navigate(['/login']);
      return new HttpHeaders();
    }
    return new HttpHeaders().set('Authorization', `Bearer ${token}`);
  }

  getRecipients(): void {
    const headers = this.getAuthHeaders();
    if (!headers.has('Authorization')) return;

    this.http.get<Recipient[]>('/api/recipients', { headers }).subscribe({
      next: (data) => { this.recipients = data; },
      error: (err) => { this.showFeedback('Failed to fetch recipients.', true, err); }
    });
  }

  // --- Modal Control ---
  openAddRecipientModal(): void {
    this.isEditMode = false;
    this.selectedRecipient = { name: '', email: '', description: '' };
    this.showRecipientModal = true;
    this.clearFeedback();
  }

  openEditRecipientModal(recipient: Recipient): void {
    this.isEditMode = true;
    this.selectedRecipient = { ...recipient }; // Use a copy for editing
    this.originalRecipient = { ...recipient }; // Store the original state
    this.showRecipientModal = true;
    this.clearFeedback();
  }

  closeModal(): void {
    this.showRecipientModal = false;
  }

  // --- CRUD Operations ---
  saveRecipient(): void {
    if (this.isEditMode) {
      this.updateRecipient();
    } else {
      this.createRecipient();
    }
  }

  createRecipient(): void {
    const headers = this.getAuthHeaders();
    this.http.post<Recipient>('/api/recipients', this.selectedRecipient, { headers }).subscribe({
      next: () => {
        this.showFeedback('Recipient added successfully.');
        this.getRecipients();
        this.closeModal();
      },
      error: (err) => this.showFeedback('Failed to add recipient.', true, err)
    });
  }

  updateRecipient(): void {
    const headers = this.getAuthHeaders();
    const { _id } = this.selectedRecipient;
    const observables = [];

    // Only send requests for fields that have changed.
    if (this.selectedRecipient.name !== this.originalRecipient.name) {
      observables.push(this.http.put(`/api/recipients/${_id}/name`, { name: this.selectedRecipient.name }, { headers }));
    }
    if (this.selectedRecipient.description !== this.originalRecipient.description) {
      observables.push(this.http.put(`/api/recipients/${_id}/description`, { description: this.selectedRecipient.description }, { headers }));
    }

    // If nothing changed, just close the modal and provide feedback.
    if (observables.length === 0) {
      this.showFeedback('No changes detected.');
      this.closeModal();
      return;
    }

    forkJoin(observables).subscribe({
      next: () => {
        this.showFeedback('Recipient updated successfully.');
        this.getRecipients();
        this.closeModal();
      },
      error: (err) => this.showFeedback('Failed to update recipient.', true, err),
    });
  }

  confirmDelete(recipient: Recipient): void {
    if (window.confirm(`Are you sure you want to delete ${recipient.name || recipient.email}? This action cannot be undone.`)) {
      this.deleteRecipient(recipient._id);
    }
  }

  private deleteRecipient(id: string): void {
    const headers = this.getAuthHeaders();
    this.http.delete(`/api/recipients/${id}`, { headers }).subscribe({
      next: () => {
        this.showFeedback('Recipient deleted successfully.');
        this.getRecipients(); // Refresh list
      },
      error: (err) => this.showFeedback('Failed to delete recipient.', true, err)
    });
  }

  // --- User Feedback Handling ---
  private showFeedback(message: string, isError = false, error?: HttpErrorResponse): void {
    this.feedbackMessage = message;
    this.isError = isError;
    console.error(error || message);

    if (error?.status === 401) {
      this.router.navigate(['/login']);
    }
    
    // Automatically clear the message after a few seconds
    setTimeout(() => this.clearFeedback(), 5000);
  }

  private clearFeedback(): void {
    this.feedbackMessage = '';
    this.isError = false;
  }
}
