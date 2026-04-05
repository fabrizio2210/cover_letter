import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { lastValueFrom } from 'rxjs';
import { FeedbackService } from './services/feedback.service';
import { Field, Identity, IdentityPreference } from './models/models';

const IDENTITY_ICONS = ['terminal', 'layers', 'brush', 'favorite', 'psychology', 'engineering', 'code', 'category'];
const ICON_COLORS = [
  { bg: '#dbe3ff', text: '#0053db' },
  { bg: '#e8def8', text: '#6a50a7' },
  { bg: '#d8f2dd', text: '#1a6831' },
  { bg: '#ffddb3', text: '#8b4000' },
];

@Component({
  selector: 'app-identities',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './identities.component.html',
  styleUrls: ['./identities.component.css'],
})
export class IdentitiesComponent implements OnInit {
  private http = inject(HttpClient);
  private feedbackService = inject(FeedbackService);

  identities: Identity[] = [];
  fields: Field[] = [];
  loading = false;

  // Per-card edit state
  editCardIndex: number | null = null;
  editName = '';
  editDescription = '';
  selectedFieldId = '';
  editRoles: string[] = [];
  editPreferences: IdentityPreference[] = [];

  // Signature editing (independent of card edit mode)
  editSignatureIndex: number | null = null;

  // Create modal state
  showCreateModal = false;
  newIdentityId = '';
  newName = '';
  newDescription = '';
  newFieldId = '';
  newRolesInput = '';

  readonly dotRange = [1, 2, 3, 4, 5];

  ngOnInit(): void {
    this.loading = true;
    Promise.all([
      lastValueFrom(this.http.get<Field[]>('/api/fields')),
      lastValueFrom(this.http.get<Identity[]>('/api/identities')),
    ]).then(([fields, identities]) => {
      this.fields = fields || [];
      this.identities = identities || [];
    }).catch((err) => this.showFeedback('Failed to load data.', true, err))
      .finally(() => { this.loading = false; });
  }

  // ─── Card meta helpers ──────────────────────────────────────────────────────

  getIdentityIcon(i: number): string {
    return IDENTITY_ICONS[i % IDENTITY_ICONS.length];
  }

  getIconBg(i: number): string {
    return ICON_COLORS[i % ICON_COLORS.length].bg;
  }

  getIconColor(i: number): string {
    return ICON_COLORS[i % ICON_COLORS.length].text;
  }

  normalizeRoles(roles?: string[]): string[] {
    return (roles || []).map(r => r.trim()).filter(r => r.length > 0);
  }

  // ─── Identity CRUD ──────────────────────────────────────────────────────────

  openCreateModal(): void {
    this.newIdentityId = '';
    this.newName = '';
    this.newDescription = '';
    this.newFieldId = '';
    this.newRolesInput = '';
    this.showCreateModal = true;
  }

  closeCreateModal(): void {
    this.showCreateModal = false;
  }

  createIdentity(): void {
    if (!this.newIdentityId.trim()) {
      this.showFeedback('Identity slug is required.', true);
      return;
    }
    const roles = this.parseRolesInput(this.newRolesInput);
    const payload: Record<string, unknown> = { identity: this.newIdentityId.trim() };
    if (this.newName.trim()) payload['name'] = this.newName.trim();
    if (this.newDescription.trim()) payload['description'] = this.newDescription.trim();
    if (this.newFieldId) payload['field_id'] = this.newFieldId;
    if (roles.length > 0) payload['roles'] = roles;

    this.http.post('/api/identities', payload).subscribe({
      next: () => {
        this.showFeedback('Identity created.');
        this.closeCreateModal();
        this.reloadIdentities();
      },
      error: (err) => this.showFeedback('Failed to create identity.', true, err),
    });
  }

  confirmDelete(identity: Identity): void {
    if (window.confirm(`Delete identity "${identity.identity}"?`)) {
      this.http.delete(`/api/identities/${identity.id}`).subscribe({
        next: () => { this.showFeedback('Identity deleted.'); this.reloadIdentities(); },
        error: (err) => this.showFeedback('Failed to delete identity.', true, err),
      });
    }
  }

  // ─── Card edit mode ─────────────────────────────────────────────────────────

  startEdit(i: number): void {
    if (this.editSignatureIndex === i) this.editSignatureIndex = null;
    this.editCardIndex = i;
    const id = this.identities[i];
    this.editName = id.name || '';
    this.editDescription = id.description || '';
    this.selectedFieldId = id.field_info?.id || '';
    this.editRoles = this.normalizeRoles(id.roles);
    if (this.editRoles.length === 0) this.editRoles = [''];
    this.editPreferences = (id.preferences || []).map(p => ({ ...p }));
  }

  cancelEdit(): void {
    this.editCardIndex = null;
  }

  async saveEdit(identity: Identity): Promise<void> {
    const ops: Promise<unknown>[] = [];

    if (this.editName.trim() !== (identity.name || '').trim()) {
      ops.push(lastValueFrom(this.http.put(`/api/identities/${identity.id}/name`, { name: this.editName.trim() })));
    }
    if (this.editDescription.trim() !== (identity.description || '').trim()) {
      ops.push(lastValueFrom(this.http.put(`/api/identities/${identity.id}/description`, { description: this.editDescription.trim() })));
    }
    if (this.selectedFieldId && this.selectedFieldId !== (identity.field_info?.id || '')) {
      ops.push(lastValueFrom(this.http.put(`/api/identities/${identity.id}/field`, { fieldId: this.selectedFieldId })));
    }

    const nextRoles = this.normalizeRoles(this.editRoles);
    const currentRoles = this.normalizeRoles(identity.roles);
    if (!this.sameRoles(nextRoles, currentRoles)) {
      ops.push(lastValueFrom(this.http.put(`/api/identities/${identity.id}/roles`, { roles: nextRoles })));
    }

    const nextPrefs = this.editPreferences.filter(p => p.key.trim());
    const currentPrefsJson = JSON.stringify(identity.preferences || []);
    const nextPrefsJson = JSON.stringify(nextPrefs);
    if (nextPrefsJson !== currentPrefsJson) {
      ops.push(lastValueFrom(this.http.put(`/api/identities/${identity.id}/preferences`, { preferences: nextPrefs })));
    }

    if (ops.length === 0) {
      this.showFeedback('No changes detected.');
      this.cancelEdit();
      return;
    }

    try {
      await Promise.all(ops);
      this.showFeedback('Identity updated.');
      this.cancelEdit();
      this.reloadIdentities();
    } catch (err) {
      this.showFeedback('Failed to update identity.', true, err as HttpErrorResponse);
    }
  }

  // ─── Role editing ────────────────────────────────────────────────────────────

  addRole(): void {
    this.editRoles.push('');
  }

  removeRole(index: number): void {
    this.editRoles.splice(index, 1);
    if (this.editRoles.length === 0) this.editRoles.push('');
  }

  trackByIndex(index: number): number {
    return index;
  }

  // ─── Preference editing ──────────────────────────────────────────────────────

  addPreference(): void {
    this.editPreferences.push({ key: '', label: '', weight: 3, enabled: true });
  }

  removePreference(index: number): void {
    this.editPreferences.splice(index, 1);
  }

  setPrefWeight(prefIndex: number, weight: number): void {
    this.editPreferences[prefIndex].weight = weight;
  }

  // ─── Signature editing ───────────────────────────────────────────────────────

  toggleSignature(i: number): void {
    this.editSignatureIndex = this.editSignatureIndex === i ? null : i;
  }

  saveSignature(identity: Identity): void {
    this.http.put(`/api/identities/${identity.id}/signature`, { html_signature: identity.html_signature || '' }).subscribe({
      next: () => { this.showFeedback('Signature saved.'); this.reloadIdentities(); },
      error: (err) => this.showFeedback('Failed to save signature.', true, err),
    });
  }

  // ─── Private helpers ─────────────────────────────────────────────────────────

  private reloadIdentities(): void {
    this.http.get<Identity[]>('/api/identities').subscribe({
      next: (data) => { this.identities = data || []; },
      error: (err) => this.showFeedback('Failed to reload identities.', true, err),
    });
  }

  private parseRolesInput(input: string): string[] {
    return input.split(',').map(r => r.trim()).filter(r => r.length > 0);
  }

  private sameRoles(a: string[], b: string[]): boolean {
    return a.length === b.length && a.every((v, i) => v === b[i]);
  }

  private showFeedback(message: string, isError = false, error?: unknown): void {
    if (error) console.error(error);
    this.feedbackService.showFeedback(message, isError);
  }
}
