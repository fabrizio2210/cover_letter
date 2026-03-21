import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { RouterModule } from '@angular/router';
import { forkJoin } from 'rxjs';
import { FeedbackService } from './services/feedback.service';
import { Company, Field } from './models/models';

@Component({
  selector: 'app-companies-list',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './companies-list.component.html',
  styleUrls: ['./companies-list.component.css']
})
export class CompaniesListComponent implements OnInit {
  private http = inject(HttpClient);
  private feedbackService = inject(FeedbackService);

  companies: Company[] = [];
  fields: Field[] = [];

  editIndex: number | null = null;
  editName = '';
  editFieldId = '';

  newName = '';
  newFieldId = '';

  ngOnInit(): void {
    this.getCompanies();
    this.getFields();
  }

  getCompanies(): void {
    this.http.get<Company[]>('/api/companies').subscribe({
      next: (data) => { this.companies = data || []; },
      error: (err) => this.showFeedback('Failed to fetch companies.', true, err)
    });
  }

  getFields(): void {
    this.http.get<Field[]>('/api/fields').subscribe({
      next: (data) => { this.fields = data || []; },
      error: (err) => this.showFeedback('Failed to fetch fields.', true, err)
    });
  }

  startEdit(i: number): void {
    this.editIndex = i;
    const c = this.companies[i];
    this.editName = c.name;
    this.editFieldId = c.field_info?.id || c.field_id || '';
    this.clearFeedback();
  }

  cancelEdit(): void {
    this.editIndex = null;
    this.editName = '';
    this.editFieldId = '';
  }

  saveEdit(i: number): void {
    const c = this.companies[i];
    const nextName = this.editName.trim();
    const currentName = (c.name || '').trim();
    const currentFieldId = c.field_info?.id || c.field_id || '';
    const nextFieldId = this.editFieldId || '';
    const nameChanged = nextName !== currentName;
    const fieldChanged = nextFieldId !== currentFieldId;

    if (!nameChanged && !fieldChanged) {
      this.showFeedback('No changes detected.');
      this.cancelEdit();
      return;
    }

    if (!nextName) {
      this.showFeedback('Company name cannot be empty.', true);
      return;
    }

    const requests = [];

    if (nameChanged) {
      const fieldIdForUpdate = nextFieldId || currentFieldId;
      if (!fieldIdForUpdate) {
        this.showFeedback('Cannot rename a company without a field association until the backend supports name-only updates.', true);
        return;
      }

      requests.push(
        this.http.put(`/api/companies/${c.id}`, {
          name: nextName,
          description: c.description || '',
          field_id: fieldIdForUpdate
        })
      );
    }

    if (fieldChanged && (!nameChanged || !nextFieldId)) {
      requests.push(this.http.put(`/api/companies/${c.id}/field`, { field_id: nextFieldId || null }));
    }

    forkJoin(requests).subscribe({
      next: () => {
        this.showFeedback('Company updated successfully.');
        this.getCompanies();
        this.cancelEdit();
      },
      error: (err) => this.showFeedback('Failed to update company.', true, err)
    });
  }

  createOrUpdateFromLastRow(): void {
    if (!this.newName || !this.newName.trim()) {
      this.showFeedback('Company name cannot be empty.', true);
      return;
    }
    const payload = {
      name: this.newName.trim(),
      description: '',
      field_id: this.newFieldId || ''
    };

    this.http.post<Company>('/api/companies', payload).subscribe({
      next: (created) => {
        this.companies = [...this.companies, created];
        this.showFeedback('Company created successfully.');
        this.newName = '';
        this.newFieldId = '';
      },
      error: (err) => this.showFeedback('Failed to create company.', true, err)
    });
  }

  confirmDelete(c: Company) {
    if (window.confirm(`Delete company "${c.name}"?`)) {
      this.deleteCompany(c);
    }
  }

  deleteCompany(c: Company): void {
    this.http.delete(`/api/companies/${c.id}`).subscribe({
      next: () => {
        this.showFeedback('Company deleted successfully.');
        this.getCompanies();
      },
      error: (err) => this.showFeedback('Failed to delete company.', true, err)
    });
  }

  private showFeedback(message: string, isError = false, error?: HttpErrorResponse): void {
    console.error(error || message);
    this.feedbackService.showFeedback(message, isError);
  }

  private clearFeedback(): void {
    this.feedbackService.clearFeedback();
  }
}
