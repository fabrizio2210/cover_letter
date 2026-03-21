import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { FeedbackService } from './services/feedback.service';
import { forkJoin } from 'rxjs';
import { Company, Recipient } from './models/models';

@Component({
  selector: 'app-recipients-list',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './recipients-list.component.html',
  styleUrls: ['./recipients-list.component.css']
})
export class RecipientsListComponent implements OnInit {
  private http = inject(HttpClient);
  private feedbackService = inject(FeedbackService);

  recipients: Recipient[] = [];

  editIndex: number | null = null;
  editRecipient: Partial<Recipient> = {};

  newRecipient: Partial<Recipient> = { name: '', email: '', description: '' };

  companies: Company[] = [];
  generatingId: string | null = null;

  ngOnInit(): void {
    this.getRecipients();
    this.getCompanies();
  }

  getRecipients(): void {
    this.http.get<Recipient[]>('/api/recipients').subscribe({
      next: (data) => { this.recipients = data; },
      error: (err) => { this.showFeedback('Failed to fetch recipients.', true, err); }
    });
  }

  startEditRecipient(index: number): void {
    this.editIndex = index;
    const recipient = this.recipients[index];
    this.editRecipient = { ...recipient };
    this.editRecipient.company_id = recipient.company_info?.id || '';
    this.clearFeedback();
  }

  cancelEdit(): void {
    this.editIndex = null;
    this.editRecipient = {};
  }

  saveEditRecipient(index: number): void {
    const recipient = this.recipients[index];
    const { id } = recipient;
    const observables: any[] = [];

    if (this.editRecipient.name !== recipient.name) {
      observables.push(this.http.put(`/api/recipients/${id}/name`, { name: this.editRecipient.name }));
    }
    if (this.editRecipient.description !== recipient.description) {
      observables.push(this.http.put(`/api/recipients/${id}/description`, { description: this.editRecipient.description }));
    }

    const origCompanyId = recipient.company_info?.id || '';
    if (this.editRecipient.company_id !== origCompanyId) {
      observables.push(this.http.put(`/api/recipients/${id}/company`, { companyId: this.editRecipient.company_id || null }));
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
    const payload: Partial<Recipient> = {
      name: this.newRecipient.name?.trim(),
      email: this.newRecipient.email?.trim(),
      description: this.newRecipient.description?.trim() || '',
      company_id: this.newRecipient.company_id || undefined
    };

    this.http.post<Recipient>('/api/recipients', payload).subscribe({
      next: () => {
        this.showFeedback('Recipient added successfully.');
        this.getRecipients();
        this.resetNewRecipient();
      },
      error: (err) => this.showFeedback('Failed to add recipient.', true, err)
    });
  }


  getCompanies(): void {
    this.http.get<Company[]>('/api/companies').subscribe({
      next: (data) => { this.companies = data || [];},
      error: (err) => this.showFeedback('Failed to fetch companies.', true, err)
    });
  }

  private showFeedback(message: string, isError = false, error?: HttpErrorResponse): void {
    console.error(error || message);
    this.feedbackService.showFeedback(message, isError);
  }

  private clearFeedback(): void {
    this.feedbackService.clearFeedback();
  }

  private resetNewRecipient(): void {
    this.newRecipient = { name: '', email: '', description: '' };
  }

  confirmDelete(recipient: Recipient) {
    if (window.confirm(`Are you sure you want to delete recipient "${recipient.name}"?`)) {
      this.deleteRecipient(recipient);
    }
  }

  deleteRecipient(recipient: Recipient): void {
    this.http.delete(`/api/recipients/${recipient.id}`).subscribe({
      next: () => {
        this.showFeedback('Recipient deleted successfully.');
        this.getRecipients();
      },
      error: (err) => this.showFeedback('Failed to delete recipient.', true, err)
    });
  }

  generate(recipient: Recipient): void {
    const id = recipient.id;
    this.generatingId = id;
    this.http.post(`/api/recipients/${id}/generate-cover-letter`, {}).subscribe({
      next: () => {
        this.showFeedback('Generation queued successfully.');
        this.generatingId = null;
      },
      error: (err) => {
        this.showFeedback('Failed to queue generation.', true, err);
        this.generatingId = null;
      }
    });
  }
}
