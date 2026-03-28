import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { lastValueFrom } from 'rxjs';
import { FeedbackService } from './services/feedback.service';
import { Field, Identity } from './models/models';

@Component({
  selector: 'app-identities-list',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule],
  styleUrls: ['./identities-list.component.css'],
  template: `
    <section>
      <h3>Identities</h3>
      <p class="is-size-6">Manage personas. Click a row to edit fields or expand the email signature.</p>

      <table class="identities-table">
        <thead>
          <tr>
            <th>Identity</th>
            <th>Name</th>
            <th>Description</th>
            <th>Field</th>
            <th>Roles</th>
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
                {{ id.field_info && id.field_info.field ? id.field_info.field : '-' }}
              </span>
              <span *ngIf="editIndex === i">
                <select [(ngModel)]="selectedFieldId">
                  <option [value]="''">-- none --</option>
                  <option *ngFor="let f of fields" [value]="f.id">{{ f.field }}</option>
                </select>
              </span>
            </td>

            <td *ngIf="editIndex !== i">{{ formatRoles(id.roles) }}</td>
            <td *ngIf="editIndex === i">
              <div *ngFor="let role of editRoles; let roleIndex = index; trackBy: trackRoleIndex" class="role-row">
                <input [(ngModel)]="editRoles[roleIndex]" placeholder="Role" />
                <button (click)="removeEditRole(roleIndex)">Remove</button>
              </div>
              <button (click)="addEditRole()">Add role</button>
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
            <td colspan="3"></td>
            <td>
              <input [(ngModel)]="newRoles" placeholder="Roles (comma separated)" />
            </td>
            <td></td>
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
  private feedbackService = inject(FeedbackService);

  identities: Identity[] = [];
  fields: Field[] = [];

  editIndex: number | null = null;
  editName = '';
  editDescription = '';
  selectedFieldId = '';
  editRoles: string[] = [];

  newIdentity = '';
  newRoles = '';
  templateOpen: Record<number, boolean> = {};

  ngOnInit(): void {
    this.getFields();
    this.getIdentities();
  }

  getFields(): void {
    this.http.get<Field[]>('/api/fields').subscribe({
      next: (data) => { this.fields = data || []; },
      error: (err) => this.showFeedback('Failed to fetch fields.', true, err)
    });
  }

  getIdentities(): void {
    this.http.get<Identity[]>('/api/identities').subscribe({
      next: (data) => { this.identities = data || []; this.templateOpen = {}; },
      error: (err) => this.showFeedback('Failed to fetch identities.', true, err)
    });
  }

  createIdentity(): void {
    if (!this.newIdentity || !this.newIdentity.trim()) {
      this.showFeedback('Identity id cannot be empty.', true);
      return;
    }
    const payload: { identity: string; roles?: string[] } = { identity: this.newIdentity.trim() };
    const roles = this.parseRolesInput(this.newRoles);
    if (roles.length > 0) {
      payload.roles = roles;
    }

    this.http.post('/api/identities', payload).subscribe({
      next: () => {
        this.showFeedback('Identity created.');
        this.newIdentity = '';
        this.newRoles = '';
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
    this.selectedFieldId = id.field_info && id.field_info.id ? id.field_info.id : '';
    this.editRoles = this.normalizeRoles(id.roles);
    if (this.editRoles.length === 0) {
      this.editRoles = [''];
    }
    this.clearFeedback();
  }

  cancelEdit(): void {
    this.editIndex = null;
    this.editName = '';
    this.editDescription = '';
    this.selectedFieldId = '';
    this.editRoles = [];
  }

  saveEdit(identity: Identity): void {
    const ops: any[] = [];
    if (this.editName.trim() !== (identity.name || '').trim()) {
      ops.push(this.http.put(`/api/identities/${identity.id}/name`, { name: this.editName.trim() }));
    }
    if (this.editDescription.trim() !== (identity.description || '').trim()) {
      ops.push(this.http.put(`/api/identities/${identity.id}/description`, { description: this.editDescription.trim() }));
    }
    if (this.selectedFieldId && (identity.field_info?.id !== this.selectedFieldId)) {
      ops.push(this.http.put(`/api/identities/${identity.id}/field`, { fieldId: this.selectedFieldId }));
    }

    const nextRoles = this.normalizeRoles(this.editRoles);
    const currentRoles = this.normalizeRoles(identity.roles);
    if (!this.sameRoles(nextRoles, currentRoles)) {
      ops.push(this.http.put(`/api/identities/${identity.id}/roles`, { roles: nextRoles }));
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
    this.http.delete(`/api/identities/${id.id}`).subscribe({
      next: () => { this.showFeedback('Identity deleted.'); this.getIdentities(); },
      error: (err) => this.showFeedback('Failed to delete identity.', true, err)
    });
  }

  toggleTemplate(i: number): void {
    this.templateOpen[i] = !this.templateOpen[i];
  }

  saveSignature(identity: Identity): void {
    this.http.put(`/api/identities/${identity.id}/signature`, { html_signature: identity.html_signature || '' }).subscribe({
      next: () => { this.showFeedback('Signature saved.'); this.getIdentities(); },
      error: (err) => this.showFeedback('Failed to save signature.', true, err)
    });
  }

  addEditRole(): void {
    this.editRoles.push('');
  }

  removeEditRole(index: number): void {
    this.editRoles.splice(index, 1);
    if (this.editRoles.length === 0) {
      this.editRoles.push('');
    }
  }

  formatRoles(roles?: string[]): string {
    const normalized = this.normalizeRoles(roles);
    return normalized.length > 0 ? normalized.join(', ') : '-';
  }

  private parseRolesInput(rolesInput: string): string[] {
    return this.normalizeRoles(rolesInput.split(','));
  }

  private normalizeRoles(roles?: string[]): string[] {
    return (roles || []).map((role) => role.trim()).filter((role) => role.length > 0);
  }

  private sameRoles(a: string[], b: string[]): boolean {
    if (a.length !== b.length) {
      return false;
    }
    return a.every((value, index) => value === b[index]);
  }

  trackRoleIndex(index: number): number {
    return index;
  }
  

  private showFeedback(message: string, isError = false, error?: HttpErrorResponse): void {
    console.error(error || message);
    this.feedbackService.showFeedback(message, isError);
  }

  private clearFeedback(): void { this.feedbackService.clearFeedback(); }
}
