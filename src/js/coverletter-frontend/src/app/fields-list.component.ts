import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterModule, Router } from '@angular/router';
import { HttpClient, HttpHeaders, HttpErrorResponse } from '@angular/common/http';
import { FeedbackService } from './services/feedback.service';

export interface Field {
  _id: string;
  field: string;
}

@Component({
  selector: 'app-fields-list',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule],
  template: `
    <section class="fields">
      <h3>Fields</h3>
      <p class="is-size-6">List of sectors (e.g. fashion, charities). Backend should return joined results (field + identities).</p>

      <table class="fields-table">
        <thead>
          <tr>
            <th>Field</th>
            <th class="actions">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr *ngFor="let f of fields; let i = index">
            <td *ngIf="editIndex !== i">{{ f.field }}</td>
            <td *ngIf="editIndex === i">
              <input [(ngModel)]="editField" placeholder="Field name" />
            </td>
            <td class="actions">
              <button *ngIf="editIndex !== i" (click)="startEdit(i)">Edit</button>
              <div *ngIf="editIndex === i">
                <button (click)="saveEdit(i)">Save</button>
                <button (click)="cancelEdit()">Cancel</button>
                <button class="danger" (click)="confirmDelete(f)">Delete</button>
              </div>
            </td>
          </tr>

          <tr class="new-row">
            <td>
              <input [(ngModel)]="newField" placeholder="New field name" />
            </td>
            <td class="actions">
              <button (click)="createField()">Create</button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  `
})
export class FieldsListComponent implements OnInit {
  private http = inject(HttpClient);
  private router = inject(Router);
  private feedbackService = inject(FeedbackService);

  fields: Field[] = [];

  editIndex: number | null = null;
  editField = '';

  newField = '';

  ngOnInit(): void {
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

  getFields(): void {
    const headers = this.getAuthHeaders();
    if (!headers.has('Authorization')) return;
    this.http.get<Field[]>('/api/fields', { headers }).subscribe({
      next: (data) => { this.fields = data || []; },
      error: (err) => this.showFeedback('Failed to fetch fields.', true, err)
    });
  }

  startEdit(i: number): void {
    this.editIndex = i;
    this.editField = this.fields[i]?.field || '';
    this.clearFeedback();
  }

  cancelEdit(): void {
    this.editIndex = null;
    this.editField = '';
  }

  saveEdit(i: number): void {
    const f = this.fields[i];
    const headers = this.getAuthHeaders();
    if (!headers.has('Authorization')) return;

    if (!this.editField || !this.editField.trim()) {
      this.showFeedback('Field name cannot be empty.', true);
      return;
    }

    if (this.editField.trim() === (f.field || '').trim()) {
      this.showFeedback('No changes detected.');
      this.cancelEdit();
      return;
    }

    this.http.put(`/api/fields/${f._id}`, { field: this.editField.trim() }, { headers }).subscribe({
      next: () => {
        this.showFeedback('Field updated successfully.');
        this.getFields();
        this.cancelEdit();
      },
      error: (err) => this.showFeedback('Failed to update field.', true, err)
    });
  }

  createField(): void {
    const headers = this.getAuthHeaders();
    if (!headers.has('Authorization')) return;
    if (!this.newField || !this.newField.trim()) {
      this.showFeedback('Field name cannot be empty.', true);
      return;
    }
    const payload = { field: this.newField.trim() };
    this.http.post<Field>('/api/fields', payload, { headers }).subscribe({
      next: () => {
        this.showFeedback('Field created successfully.');
        this.newField = '';
        this.getFields();
      },
      error: (err) => this.showFeedback('Failed to create field.', true, err)
    });
  }

  confirmDelete(f: Field) {
    if (window.confirm(`Delete field "${f.field}"?`)) {
      this.deleteField(f);
    }
  }

  deleteField(f: Field): void {
    const headers = this.getAuthHeaders();
    if (!headers.has('Authorization')) return;
    this.http.delete(`/api/fields/${f._id}`, { headers }).subscribe({
      next: () => {
        this.showFeedback('Field deleted successfully.');
        this.getFields();
      },
      error: (err) => this.showFeedback('Failed to delete field.', true, err)
    });
  }

  private showFeedback(message: string, isError = false, error?: HttpErrorResponse): void {
    console.error(error || message);
    if (error?.status === 401) {
      this.router.navigate(['/login']);
    }
    this.feedbackService.showFeedback(message, isError);
  }

  private clearFeedback(): void {
    this.feedbackService.clearFeedback();
  }
}
