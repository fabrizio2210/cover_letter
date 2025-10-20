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

  // State for the modal
  showRecipientModal = false;
  isEditMode = false;
  selectedRecipient: Partial<Recipient> = {};
  originalRecipient: Partial<Recipient> = {}; // Store the original state for comparison

  // --- Fields support added ---
  fields: { _id: string; field: string; }[] = [];
  selectedFieldId: string = '';    // ID selected in the modal
  newFieldName: string = '';       // for creating a new field inline

  ngOnInit(): void {
    this.getRecipients();
    this.getFields(); // load fields for dropdown
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
    this.selectedFieldId = '';
    this.newFieldName = '';
    this.showRecipientModal = true;
    this.clearFeedback();
    this.getFields();
  }

  openEditRecipientModal(recipient: Recipient): void {
    this.isEditMode = true;
    this.selectedRecipient = { ...recipient }; // Use a copy for editing
    this.originalRecipient = { ...recipient }; // Store the original state
    // derive field id from recipient.fieldInfo (handle array or object)
    let origFieldId = '';
    const fi = (recipient as any).fieldInfo;
    if (Array.isArray(fi) && fi.length) {
      origFieldId = fi[0]._id;
    } else if (fi && fi._id) {
      origFieldId = fi._id;
    } else if ((recipient as any).field) {
      origFieldId = (recipient as any).field; // fallback if raw stored id
    }
    this.selectedFieldId = origFieldId || '';
    this.newFieldName = '';
    this.showRecipientModal = true;
    this.clearFeedback();
    this.getFields();
  }

  closeModal(): void {
    this.showRecipientModal = false;
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

  createField(): void {
    if (!this.newFieldName || !this.newFieldName.trim()) {
      this.showFeedback('Field name cannot be empty.', true);
      return;
    }
    const headers = this.getAuthHeaders();
    const payload = { field: this.newFieldName.trim() };
    this.http.post<{ _id: string; field: string }>('/api/fields', payload, { headers }).subscribe({
      next: (created) => {
        // add to local list and select it
        if (created && created._id) {
          this.fields = [...this.fields, created];
          this.selectedFieldId = created._id;
          this.newFieldName = '';
          this.showFeedback('Field created and selected.');
        } else {
          this.showFeedback('Field created (unexpected response shape).');
        }
      },
      error: (err) => this.showFeedback('Failed to create field.', true, err)
    });
  }

  // Helper: call backend to associate a field with recipient
  associateFieldWithRecipient(recipientId: string, fieldId: string) {
    const headers = this.getAuthHeaders();
    return this.http.put(`/api/recipients/${recipientId}/field`, { fieldId }, { headers });
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
      next: (createdRecipient: any) => {
        // If a field was selected, associate it with the created recipient (createdRecipient should include _id)
        const createdId = createdRecipient?._id || createdRecipient?.InsertedID || createdRecipient?.InsertedId;
        if (this.selectedFieldId && createdId) {
          this.associateFieldWithRecipient(createdId, this.selectedFieldId).subscribe({
            next: () => {
              this.showFeedback('Recipient added and field associated successfully.');
              this.getRecipients();
              this.closeModal();
            },
            error: (err) => this.showFeedback('Recipient added but failed to associate field.', true, err)
          });
        } else {
          this.showFeedback('Recipient added successfully.');
          this.getRecipients();
          this.closeModal();
        }
      },
      error: (err) => this.showFeedback('Failed to add recipient.', true, err)
    });
  }

  updateRecipient(): void {
    const headers = this.getAuthHeaders();
    const { _id } = this.selectedRecipient;
    const observables: any[] = [];

    // Only send requests for fields that have changed.
    if (this.selectedRecipient.name !== this.originalRecipient.name) {
      observables.push(this.http.put(`/api/recipients/${_id}/name`, { name: this.selectedRecipient.name }, { headers }));
    }
    if (this.selectedRecipient.description !== this.originalRecipient.description) {
      observables.push(this.http.put(`/api/recipients/${_id}/description`, { description: this.selectedRecipient.description }, { headers }));
    }

    // detect field change
    // derive original field id
    let originalFieldId = '';
    const origFI = (this.originalRecipient as any).fieldInfo;
    if (Array.isArray(origFI) && origFI.length) {
      originalFieldId = origFI[0]._id;
    } else if (origFI && origFI._id) {
      originalFieldId = origFI._id;
    } else if ((this.originalRecipient as any).field) {
      originalFieldId = (this.originalRecipient as any).field;
    }
    if ((this.selectedFieldId || '') !== (originalFieldId || '')) {
      // add association request (can be empty to clear - backend currently treats only set)
      observables.push(this.http.put(`/api/recipients/${_id}/field`, { fieldId: this.selectedFieldId }, { headers }));
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
