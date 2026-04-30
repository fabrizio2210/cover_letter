import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { FeedbackService } from '../../core/services/feedback.service';
import { Field } from '../../shared/models/models';

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
              <input type="text" class="table-input" [(ngModel)]="editField" aria-label="Edit field name" placeholder="Field name" />
            </td>
            <td class="actions">
              <button *ngIf="editIndex !== i" (click)="startEdit(i)">Edit</button>
              <div *ngIf="editIndex === i">
                <button type="button" (click)="saveEdit(i)" aria-label="Save field">Save</button>
                <button type="button" (click)="cancelEdit()" aria-label="Cancel edit">Cancel</button>
                <button type="button" class="danger" (click)="confirmDelete(f)" aria-label="Delete field">Delete</button>
              </div>
            </td>
          </tr>

          <tr class="new-row">
            <td>
              <input type="text" class="table-input" [(ngModel)]="newField" aria-label="New field name" placeholder="New field name" />
            </td>
            <td class="actions">
              <button type="button" (click)="createField()" aria-label="Create field">Create</button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  `
  ,
  styleUrls: ['./fields-list.component.css']
})
export class FieldsListComponent implements OnInit {
  private http = inject(HttpClient);
  private feedbackService = inject(FeedbackService);

  fields: Field[] = [];

  editIndex: number | null = null;
  editField = '';

  newField = '';

  ngOnInit(): void {
    this.getFields();
  }

  getFields(): void {
    this.http.get<Field[]>('/api/admin/fields').subscribe({
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

    if (!this.editField || !this.editField.trim()) {
      this.showFeedback('Field name cannot be empty.', true);
      return;
    }

    if (this.editField.trim() === (f.field || '').trim()) {
      this.showFeedback('No changes detected.');
      this.cancelEdit();
      return;
    }

    this.http.put(`/api/admin/fields/${f.id}`, { field: this.editField.trim() }).subscribe({
      next: () => {
        this.showFeedback('Field updated successfully.');
        this.getFields();
        this.cancelEdit();
      },
      error: (err) => this.showFeedback('Failed to update field.', true, err)
    });
  }

  createField(): void {
    if (!this.newField || !this.newField.trim()) {
      this.showFeedback('Field name cannot be empty.', true);
      return;
    }
    const payload = { field: this.newField.trim() };
    this.http.post<Field>('/api/admin/fields', payload).subscribe({
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
    this.http.delete(`/api/admin/fields/${f.id}`).subscribe({
      next: () => {
        this.showFeedback('Field deleted successfully.');
        this.getFields();
      },
      error: (err) => this.showFeedback('Failed to delete field.', true, err)
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
