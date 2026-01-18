import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterModule, Router } from '@angular/router';
import { HttpClient, HttpHeaders, HttpErrorResponse } from '@angular/common/http';
import { lastValueFrom } from 'rxjs';
import { FeedbackService } from './services/feedback.service';

export interface Field {
  _id: string;
  field: string;
}

export interface Identity {
  _id: string;
  identity: string;
  name?: string;
  description?: string;
  fieldInfo?: Field[];
  html_signature?: string;
}

@Component({
  selector: 'app-identities-list',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule],
  template: `
    <section>
      <h3>Identities</h3>
      <p class="is-size-6">Manage personas. Click a row to edit fields or expand the email template.</p>

      <table class="identities-table">
        <thead>
          <tr>
            <th>Identity</th>
            <th>Name</th>
            <th>Description</th>
            <th>Field</th>
            <th>Email signature</th>
            <th class="actions">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr *ngFor="let id of identities; let i = index">
            <td>{{ id.identity }}</td>

            <td *ngIf="editIndex !== i">{{ id.name || '' }}</td>
            <td *ngIf="editIndex === i">
              <input [(ngModel)]="editName" placeholder="Name" />
            </td>

            <td *ngIf="editIndex !== i">{{ id.description || '' }}</td>
            <td *ngIf="editIndex === i">
              <input [(ngModel)]="editDescription" placeholder="Description" />
            </td>

            <td>
              <span *ngIf="!(editIndex === i)">
                {{ id.fieldInfo && id.fieldInfo.length ? id.fieldInfo[0].field : '-' }}
              </span>
              <span *ngIf="editIndex === i">
                <select [(ngModel)]="selectedFieldId">
                  <option [value]="''">-- none --</option>
                  <option *ngFor="let f of fields" [value]="f._id">{{ f.field }}</option>
                </select>
              </span>
            </td>

            <td>
              <div *ngIf="!templateOpen[i]">
                <button (click)="toggleTemplate(i)">Edit signature</button>
              </div>
              <div *ngIf="templateOpen[i]">
                <textarea [(ngModel)]="identities[i].html_signature" rows="6" cols="40"></textarea>
                <div>
                  <button (click)="saveSignature(identities[i])">Save Signature</button>
                  <button (click)="toggleTemplate(i)">Close</button>
                </div>
              </div>
            </td>

            <td class="actions">
              <div *ngIf="editIndex !== i">
                <button (click)="startEdit(i)">Edit</button>
                <button (click)="confirmDelete(identities[i])">Delete</button>
              </div>
              <div *ngIf="editIndex === i">
                <button (click)="saveEdit(identities[i])">Save</button>
                <button (click)="cancelEdit()">Cancel</button>
              </div>
            </td>
          </tr>

          <tr class="new-row">
            <td>
              <input [(ngModel)]="newIdentity" placeholder="New identity id" />
            </td>
            <td colspan="4"></td>
            <td class="actions">
              <button (click)="createIdentity()">Create</button>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- Field creation removed: default is none -->
    </section>
  `
})
export class IdentitiesListComponent implements OnInit {
  private http = inject(HttpClient);
  private router = inject(Router);
  private feedbackService = inject(FeedbackService);

  identities: Identity[] = [];
  fields: Field[] = [];

  editIndex: number | null = null;
  editName = '';
  editDescription = '';
  selectedFieldId = '';

  newIdentity = '';
  templateOpen: Record<number, boolean> = {};

  ngOnInit(): void {
    this.getFields();
    this.getIdentities();
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

  getIdentities(): void {
    const headers = this.getAuthHeaders();
    if (!headers.has('Authorization')) return;
    this.http.get<Identity[]>('/api/identities', { headers }).subscribe({
      next: (data) => { this.identities = data || []; this.templateOpen = {}; },
      error: (err) => this.showFeedback('Failed to fetch identities.', true, err)
    });
  }

  createIdentity(): void {
    if (!this.newIdentity || !this.newIdentity.trim()) {
      this.showFeedback('Identity id cannot be empty.', true);
      return;
    }
    const headers = this.getAuthHeaders();
    if (!headers.has('Authorization')) return;
    const payload = { identity: this.newIdentity.trim() };
    this.http.post('/api/identities', payload, { headers }).subscribe({
      next: () => {
        this.showFeedback('Identity created.');
        this.newIdentity = '';
        this.getIdentities();
      },
      error: (err) => this.showFeedback('Failed to create identity.', true, err)
    });
  }

  startEdit(i: number): void {
    this.editIndex = i;
    const id = this.identities[i];
    this.editName = id?.name || '';
    this.editDescription = id?.description || '';
    this.selectedFieldId = id.fieldInfo && id.fieldInfo.length ? id.fieldInfo[0]._id : '';
    this.clearFeedback();
  }

  cancelEdit(): void {
    this.editIndex = null;
    this.editName = '';
    this.editDescription = '';
    this.selectedFieldId = '';
  }

  saveEdit(identity: Identity): void {
    const headers = this.getAuthHeaders();
    if (!headers.has('Authorization')) return;

    const ops: any[] = [];
    if (this.editName.trim() !== (identity.name || '').trim()) {
      ops.push(this.http.put(`/api/identities/${identity._id}/name`, { name: this.editName.trim() }, { headers }));
    }
    if (this.editDescription.trim() !== (identity.description || '').trim()) {
      ops.push(this.http.put(`/api/identities/${identity._id}/description`, { description: this.editDescription.trim() }, { headers }));
    }
    if (this.selectedFieldId && (identity.fieldInfo?.[0]?._id !== this.selectedFieldId)) {
      ops.push(this.http.put(`/api/identities/${identity._id}/field`, { fieldId: this.selectedFieldId }, { headers }));
    }

    if (ops.length === 0) {
      this.showFeedback('No changes detected.');
      this.cancelEdit();
      return;
    }

    const promises = ops.map(o => lastValueFrom(o));
    Promise.all(promises).then(() => {
      this.showFeedback('Identity updated.');
      this.cancelEdit();
      this.getFields();
      this.getIdentities();
    }).catch((err) => this.showFeedback('Failed to update identity.', true, err));
  }

  confirmDelete(id: Identity): void {
    if (window.confirm(`Delete identity "${id.identity}"?`)) {
      this.deleteIdentity(id);
    }
  }

  deleteIdentity(id: Identity): void {
    const headers = this.getAuthHeaders();
    if (!headers.has('Authorization')) return;
    this.http.delete(`/api/identities/${id._id}`, { headers }).subscribe({
      next: () => { this.showFeedback('Identity deleted.'); this.getIdentities(); },
      error: (err) => this.showFeedback('Failed to delete identity.', true, err)
    });
  }

  toggleTemplate(i: number): void {
    this.templateOpen[i] = !this.templateOpen[i];
  }

  saveSignature(identity: Identity): void {
    const headers = this.getAuthHeaders();
    if (!headers.has('Authorization')) return;
    this.http.put(`/api/identities/${identity._id}/signature`, { html_signature: identity.html_signature || '' }, { headers }).subscribe({
      next: () => { this.showFeedback('Signature saved.'); this.getIdentities(); },
      error: (err) => this.showFeedback('Failed to save signature.', true, err)
    });
  }
  

  private showFeedback(message: string, isError = false, error?: HttpErrorResponse): void {
    console.error(error || message);
    if (error?.status === 401) {
      this.router.navigate(['/login']);
    }
    this.feedbackService.showFeedback(message, isError);
  }

  private clearFeedback(): void { this.feedbackService.clearFeedback(); }
}
