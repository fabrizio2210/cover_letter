import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient, HttpHeaders, HttpErrorResponse } from '@angular/common/http';
import { RouterModule, Router } from '@angular/router';
import { forkJoin } from 'rxjs';

export interface Company {
  _id: string;
  name: string;
  fieldId?: string;
  fieldInfo?: { _id: string; field: string } | any;
}

@Component({
  selector: 'app-companies-list',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './companies-list.component.html',
  styleUrls: ['./companies-list.component.css']
})
export class CompaniesListComponent implements OnInit {
  private http = inject(HttpClient);
  private router = inject(Router);

  companies: Company[] = [];
  fields: { _id: string; field: string }[] = [];

  editIndex: number | null = null;
  editName = '';
  editFieldId = '';

  newName = '';
  newFieldId = '';

  feedbackMessage = '';
  isError = false;

  ngOnInit(): void {
    this.getCompanies();
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

  getCompanies(): void {
    const headers = this.getAuthHeaders();
    if (!headers.has('Authorization')) return;
    this.http.get<Company[]>('/api/companies', { headers }).subscribe({
      next: (data) => { this.companies = data || []; },
      error: (err) => this.showFeedback('Failed to fetch companies.', true, err)
    });
  }

  getFields(): void {
    const headers = this.getAuthHeaders();
    if (!headers.has('Authorization')) return;
    this.http.get<{ _id: string; field: string }[]>('/api/fields', { headers }).subscribe({
      next: (data) => { this.fields = data || []; },
      error: (err) => this.showFeedback('Failed to fetch fields.', true, err)
    });
  }

  startEdit(i: number): void {
    this.editIndex = i;
    const c = this.companies[i];
    this.editName = c.name;
    this.editFieldId = (c.fieldId || (c.fieldInfo && (c.fieldInfo._id || c.fieldInfo[0]?._id)) || '') as string;
    this.clearFeedback();
  }

  cancelEdit(): void {
    this.editIndex = null;
    this.editName = '';
    this.editFieldId = '';
  }

  saveEdit(i: number): void {
    const c = this.companies[i];
    const headers = this.getAuthHeaders();
    const observables: any[] = [];

    if (this.editName !== c.name) {
      observables.push(this.http.put(`/api/companies/${c._id}/name`, { name: this.editName }, { headers }));
    }
    const origField = c.fieldId || c.fieldInfo?._id || '';
    if ((this.editFieldId || '') !== (origField || '')) {
      observables.push(this.http.put(`/api/companies/${c._id}/field`, { field_id: this.editFieldId }, { headers }));
    }

    if (observables.length === 0) {
      this.showFeedback('No changes detected.');
      this.cancelEdit();
      return;
    }

    forkJoin(observables).subscribe({
      next: () => {
        this.showFeedback('Company updated successfully.');
        this.getCompanies();
        this.cancelEdit();
      },
      error: (err) => this.showFeedback('Failed to update company.', true, err)
    });
  }

  createOrUpdateFromLastRow(): void {
    const headers = this.getAuthHeaders();
    if (!this.newName || !this.newName.trim()) {
      this.showFeedback('Company name cannot be empty.', true);
      return;
    }
  const payload = { name: this.newName.trim(), field_id: this.newFieldId || undefined };
  this.http.post<Company>('/api/companies', payload, { headers }).subscribe({
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
    const headers = this.getAuthHeaders();
    if (!headers.has('Authorization')) return;
    this.http.delete(`/api/companies/${c._id}`, { headers }).subscribe({
      next: () => {
        this.showFeedback('Company deleted successfully.');
        this.getCompanies();
      },
      error: (err) => this.showFeedback('Failed to delete company.', true, err)
    });
  }

  private showFeedback(message: string, isError = false, error?: HttpErrorResponse): void {
    this.feedbackMessage = message;
    this.isError = isError;
    console.error(error || message);
    if (error?.status === 401) {
      this.router.navigate(['/login']);
    }
    setTimeout(() => this.clearFeedback(), 5000);
  }

  private clearFeedback(): void {
    this.feedbackMessage = '';
    this.isError = false;
  }
}
