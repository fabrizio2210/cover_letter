import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient, HttpHeaders, HttpErrorResponse } from '@angular/common/http';
import { Router } from '@angular/router';
import { FeedbackService } from './services/feedback.service';
import { forkJoin } from 'rxjs';

export interface Recipient {
  _id: string;
  email: string;
  name?: string;
  description?: string;
  fieldInfo?: { _id: string; field: string; } | any;
  companyId?: string;
  companyInfo?: { _id: string; name: string; } | any;
  companyName?: string;
}

@Component({
  selector: 'app-recipients-list',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './recipients-list.component.html',
  styleUrls: ['./recipients-list.component.css']
})
export class RecipientsListComponent implements OnInit {
  private http = inject(HttpClient);
  private router = inject(Router);
  private feedbackService = inject(FeedbackService);

  recipients: Recipient[] = [];
  feedbackMessage = '';
  isError = false;

  editIndex: number | null = null;
  editRecipient: Partial<Recipient> = {};
  editFieldId: string = '';

  newRecipient: Partial<Recipient> = { name: '', email: '', description: '' };
  newRecipientFieldId: string = '';

  fields: { _id: string; field: string; }[] = [];
  selectedFieldId: string = '';
  newFieldName: string = '';

  companies: { _id: string; name: string; fieldId?: string }[] = [];
  newCompanyName: string = '';

  ngOnInit(): void {
    this.getRecipients();
    this.getFields();
    this.getCompanies();
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

  startEditRecipient(index: number): void {
    this.editIndex = index;
    const recipient = this.recipients[index];
    this.editRecipient = { ...recipient };
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

    const origCompanyName = recipient.companyInfo?.name || '';
    if ((this.editRecipient as any).companyName && (this.editRecipient as any).companyName !== origCompanyName) {
      const newName = (this.editRecipient as any).companyName.trim();
      if (newName) {
        this.http.post<{ _id: string; name: string }>('/api/companies', { name: newName }, { headers }).subscribe({
          next: (created) => {
            if (created && created._id) {
              this.companies = [...this.companies, created];
              this.associateCompanyWithRecipient(_id, created._id).subscribe({
                next: () => {
                  if (observables.length === 0) {
                    this.showFeedback('Recipient updated successfully.');
                    this.getRecipients();
                    this.cancelEdit();
                  } else {
                    forkJoin(observables).subscribe({
                      next: () => {
                        this.showFeedback('Recipient updated successfully.');
                        this.getRecipients();
                        this.cancelEdit();
                      },
                      error: (err) => this.showFeedback('Failed to update recipient.', true, err),
                    });
                  }
                },
                error: (err) => this.showFeedback('Failed to associate new company.', true, err)
              });
            } else {
              this.showFeedback('Company created but unexpected response shape.', true);
            }
          },
          error: (err) => this.showFeedback('Failed to create company.', true, err)
        });
        return;
      }
    } else if ((this.editRecipient as any).companyId && (this.editRecipient as any).companyId !== recipient.companyInfo?._id) {
      observables.push(this.http.put(`/api/recipients/${_id}/company`, { companyId: (this.editRecipient as any).companyId }, { headers }));
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

  saveNewRecipient(): void {
    const headers = this.getAuthHeaders();
    if (!headers.has('Authorization')) return;

    const createAndAssociate = (companyId?: string) => {
      const payload: any = { ...this.newRecipient };
      if (companyId) payload.companyId = companyId;
      this.http.post<Recipient>('/api/recipients', payload, { headers }).subscribe({
        next: (createdRecipient: any) => {
          const createdId = createdRecipient?._id || createdRecipient?.InsertedID || createdRecipient?.InsertedId;
          if (companyId && createdId) {
            this.associateCompanyWithRecipient(createdId, companyId).subscribe({
              next: () => {
                this.showFeedback('Recipient added and company associated successfully.');
                this.getRecipients();
                this.resetNewRecipient();
              },
              error: (err) => this.showFeedback('Recipient added but failed to associate company.', true, err)
            });
          } else {
            this.showFeedback('Recipient added successfully.');
            this.getRecipients();
            this.resetNewRecipient();
          }
        },
        error: (err) => this.showFeedback('Failed to add recipient.', true, err)
      });
    };

    if (this.newCompanyName && this.newCompanyName.trim()) {
      const payload = { name: this.newCompanyName.trim() };
      this.http.post<{ _id: string; name: string }>('/api/companies', payload, { headers }).subscribe({
        next: (created) => {
          if (created && created._id) {
            this.companies = [...this.companies, created];
            createAndAssociate(created._id);
            this.newCompanyName = '';
          } else {
            this.showFeedback('Company created but unexpected response shape.', true);
          }
        },
        error: (err) => this.showFeedback('Failed to create company.', true, err)
      });
    } else {
      createAndAssociate(this.newRecipient.companyId as string | undefined);
    }
  }

  getFields(): void {
    const headers = this.getAuthHeaders();
    if (!headers.has('Authorization')) return;
    this.http.get<{ _id: string; field: string }[]>('/api/fields', { headers }).subscribe({
      next: (data) => { this.fields = data || []; },
      error: (err) => { this.showFeedback('Failed to fetch fields.', true, err); }
    });
  }

  getCompanies(): void {
    const headers = this.getAuthHeaders();
    if (!headers.has('Authorization')) return;
    this.http.get<{ _id: string; name: string; fieldId?: string }[]>('/api/companies', { headers }).subscribe({
      next: (data) => { this.companies = data || []; },
      error: (err) => this.showFeedback('Failed to fetch companies.', true, err)
    });
  }

  createCompany(): void {
    if (!this.newCompanyName || !this.newCompanyName.trim()) {
      this.showFeedback('Company name cannot be empty.', true);
      return;
    }
    const headers = this.getAuthHeaders();
    const payload = { name: this.newCompanyName.trim() };
    this.http.post<{ _id: string; name: string }>('/api/companies', payload, { headers }).subscribe({
      next: (created) => {
        if (created && created._id) {
          this.companies = [...this.companies, created];
          this.newRecipient.companyId = created._id;
          this.newCompanyName = '';
          this.showFeedback('Company created and selected.');
        } else {
          this.showFeedback('Company created (unexpected response shape).');
        }
      },
      error: (err) => this.showFeedback('Failed to create company.', true, err)
    });
  }

  associateCompanyWithRecipient(recipientId: string, companyId: string) {
    const headers = this.getAuthHeaders();
    return this.http.put(`/api/recipients/${recipientId}/company`, { companyId }, { headers });
  }

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

  private resetNewRecipient(): void {
    this.newRecipient = { name: '', email: '', description: '' };
    this.newRecipientFieldId = '';
    this.newCompanyName = '';
  }

  confirmDelete(recipient: any) {
    if (window.confirm(`Are you sure you want to delete recipient "${recipient.name}"?`)) {
      this.deleteRecipient(recipient);
    }
  }

  deleteRecipient(recipient: any): void {
    const headers = this.getAuthHeaders();
    if (!headers.has('Authorization')) return;
    const id = recipient._id;
    this.http.delete(`/api/recipients/${id}`, { headers }).subscribe({
      next: () => {
        this.showFeedback('Recipient deleted successfully.');
        this.getRecipients();
      },
      error: (err) => this.showFeedback('Failed to delete recipient.', true, err)
    });
  }
}
