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
  fieldInfo?: { _id: string; field: string; } | any; // tolerate array or object
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

  // In-place editing state
  editIndex: number | null = null;
  editRecipient: Partial<Recipient> = {};
  editFieldId: string = '';

  // New recipient row state
  newRecipient: Partial<Recipient> = { name: '', email: '', description: '' };
  newRecipientFieldId: string = '';

  // --- Fields support added ---
  fields: { _id: string; field: string; }[] = [];
  selectedFieldId: string = '';    // ID selected in the modal
  newFieldName: string = ''; // shared for both edit and new

  ngOnInit(): void {
    this.getRecipients();
    this.getFields();
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

  // --- In-place Editing Methods ---
  startEditRecipient(index: number): void {
    this.editIndex = index;
    const recipient = this.recipients[index];
    this.editRecipient = { ...recipient };
    // derive field id
    let origFieldId = '';
    const fi = (recipient as any).fieldInfo;
    if (Array.isArray(fi) && fi.length) {
      origFieldId = fi[0]._id;
    } else if (fi && fi._id) {
      origFieldId = fi._id;
    } else if ((recipient as any).field) {
      origFieldId = (recipient as any).field;
    }
    this.editFieldId = origFieldId || '';
    this.newFieldName = '';
    this.clearFeedback();
  }

  cancelEdit(): void {
    this.editIndex = null;
    this.editRecipient = {};
    this.editFieldId = '';
    this.newFieldName = '';
  }

  saveEditRecipient(index: number): void {
    const recipient = this.recipients[index];
    const headers = this.getAuthHeaders();
    const { _id } = recipient;
    const observables: any[] = [];

    if (this.editRecipient.name !== recipient.name) {
      observables.push(this.http.put(`/api/recipients/${_id}/name`, { name: this.editRecipient.name }, { headers }));
    }
    if (this.editRecipient.email !== recipient.email) {
      observables.push(this.http.put(`/api/recipients/${_id}/email`, { email: this.editRecipient.email }, { headers }));
    }
    if (this.editRecipient.description !== recipient.description) {
      observables.push(this.http.put(`/api/recipients/${_id}/description`, { description: this.editRecipient.description }, { headers }));
    }
    // field change
    let origFieldId = '';
    const fi = (recipient as any).fieldInfo;
    if (Array.isArray(fi) && fi.length) {
      origFieldId = fi[0]._id;
    } else if (fi && fi._id) {
      origFieldId = fi._id;
    } else if ((recipient as any).field) {
      origFieldId = (recipient as any).field;
    }
    if ((this.editFieldId || '') !== (origFieldId || '')) {
      observables.push(this.http.put(`/api/recipients/${_id}/field`, { fieldId: this.editFieldId }, { headers }));
    }

    if (observables.length === 0) {
      this.showFeedback('No changes detected.');
      this.cancelEdit();
      return;
    }

    forkJoin(observables).subscribe({
      next: () => {
        this.showFeedback('Recipient updated successfully.');
        this.getRecipients();
        this.cancelEdit();
      },
      error: (err) => this.showFeedback('Failed to update recipient.', true, err),
    });
  }

  // --- New Recipient Row Methods ---
  saveNewRecipient(): void {
    const headers = this.getAuthHeaders();
    this.http.post<Recipient>('/api/recipients', this.newRecipient, { headers }).subscribe({
      next: (createdRecipient: any) => {
        const createdId = createdRecipient?._id || createdRecipient?.InsertedID || createdRecipient?.InsertedId;
        if (this.newRecipientFieldId && createdId) {
          this.associateFieldWithRecipient(createdId, this.newRecipientFieldId).subscribe({
            next: () => {
              this.showFeedback('Recipient added and field associated successfully.');
              this.getRecipients();
              this.resetNewRecipient();
            },
            error: (err) => this.showFeedback('Recipient added but failed to associate field.', true, err)
          });
        } else {
          this.showFeedback('Recipient added successfully.');
          this.getRecipients();
          this.resetNewRecipient();
        }
      },
      error: (err) => this.showFeedback('Failed to add recipient.', true, err)
    });
  }

  resetNewRecipient(): void {
    this.newRecipient = { name: '', email: '', description: '' };
    this.newRecipientFieldId = '';
    this.newFieldName = '';
  }

  // --- Fields API methods ---
  getFields(): void {
    const headers = this.getAuthHeaders();
    if (!headers.has('Authorization')) return;
    this.http.get<{ _id: string; field: string }[]>('/api/fields', { headers }).subscribe({
      next: (data) => { this.fields = data || []; },
      error: (err) => { this.showFeedback('Failed to fetch fields.', true, err); }
    });
  }

  // Helper: call backend to associate a field with recipient
  associateFieldWithRecipient(recipientId: string, fieldId: string) {
    const headers = this.getAuthHeaders();
    return this.http.put(`/api/recipients/${recipientId}/field`, { fieldId }, { headers });
  }

  // --- Field Creation (shared for edit/new) ---
  createField(editRowIndex?: number): void {
    if (!this.newFieldName || !this.newFieldName.trim()) {
      this.showFeedback('Field name cannot be empty.', true);
      return;
    }
    const headers = this.getAuthHeaders();
    const payload = { field: this.newFieldName.trim() };
    this.http.post<{ _id: string; field: string }>('/api/fields', payload, { headers }).subscribe({
      next: (created) => {
        if (created && created._id) {
          this.fields = [...this.fields, created];
          if (editRowIndex !== undefined && this.editIndex === editRowIndex) {
            this.editFieldId = created._id;
          } else {
            this.newRecipientFieldId = created._id;
          }
          this.newFieldName = '';
          this.showFeedback('Field created and selected.');
        } else {
          this.showFeedback('Field created (unexpected response shape).');
        }
      },
      error: (err) => this.showFeedback('Failed to create field.', true, err)
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
