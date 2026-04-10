import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { forkJoin } from 'rxjs';
import { ApiService } from './services/api.service';
import { FeedbackService } from './services/feedback.service';
import { IdentityContextService } from './services/identity-context.service';
import { Company, Field, Identity, Recipient } from './models/models';

interface RecipientFormState {
  id?: string;
  name: string;
  email: string;
  description: string;
  company_id: string;
}

@Component({
  selector: 'app-recipients',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './recipients.component.html',
  styleUrls: ['./recipients.component.css']
})
export class RecipientsComponent implements OnInit {
  private api = inject(ApiService);
  private feedbackService = inject(FeedbackService);
  private identityContext = inject(IdentityContextService);

  loading = false;
  selectedIdentityId = '';

  companies: Company[] = [];
  recipients: Recipient[] = [];
  fields: Field[] = [];
  identities: Identity[] = [];

  selectedRecipientId: string | null = null;
  generatingRecipientId: string | null = null;

  isRecipientModalOpen = false;

  recipientForm: RecipientFormState = this.createEmptyRecipientForm();

  ngOnInit(): void {
    this.loadPageData();
  }

  loadPageData(): void {
    this.loading = true;

    forkJoin({
      companies: this.api.getCompanies(),
      recipients: this.api.getRecipients(),
      fields: this.api.getFields(),
      identities: this.api.getIdentities()
    }).subscribe({
      next: ({ companies, recipients, fields, identities }) => {
        this.companies = [...companies].sort((left, right) => left.name.localeCompare(right.name));
        this.recipients = [...recipients].sort((left, right) => (left.name || left.email).localeCompare(right.name || right.email));
        this.fields = [...fields].sort((left, right) => left.field.localeCompare(right.field));
        this.identities = [...identities].sort((left, right) => (left.name || left.identity).localeCompare(right.name || right.identity));

        const availableIdentityIds = this.identities.map((identity) => identity.id).filter(Boolean);
        this.selectedIdentityId = this.identityContext.ensureValidIdentityId(
          availableIdentityIds,
          this.selectedIdentityId || this.identityContext.getSelectedIdentityId()
        );

        this.ensureSelections();
        this.loading = false;
      }
    });
  }

  onIdentityFilterChange(identityId: string): void {
    const normalizedIdentityId = (identityId || '').trim();
    this.selectedIdentityId = normalizedIdentityId;
    this.identityContext.setSelectedIdentityId(normalizedIdentityId);
    this.ensureSelections();
  }

  selectRecipient(recipient: Recipient): void {
    this.selectedRecipientId = recipient.id;
  }

  openRecipientCreateModal(): void {
    this.recipientForm = this.createEmptyRecipientForm();
    this.isRecipientModalOpen = true;
  }

  openRecipientEditModal(recipient: Recipient, event?: Event): void {
    event?.stopPropagation();
    this.recipientForm = {
      id: recipient.id,
      name: recipient.name || '',
      email: recipient.email,
      description: recipient.description || '',
      company_id: recipient.company_info?.id || recipient.company_id || ''
    };
    this.isRecipientModalOpen = true;
  }

  closeRecipientModal(): void {
    this.isRecipientModalOpen = false;
    this.recipientForm = this.createEmptyRecipientForm();
  }

  saveRecipient(): void {
    const name = this.recipientForm.name.trim();
    const email = this.recipientForm.email.trim();
    const description = this.recipientForm.description.trim();
    const companyId = this.recipientForm.company_id || '';

    if (!name) {
      this.showFeedback('Recipient name cannot be empty.', true);
      return;
    }

    if (!email) {
      this.showFeedback('Recipient email cannot be empty.', true);
      return;
    }

    if (!this.recipientForm.id) {
      this.api.createRecipient({
        name,
        email,
        description,
        company_id: companyId || undefined
      }).subscribe({
        next: (recipient) => {
          this.selectedRecipientId = recipient.id;
          this.closeRecipientModal();
          this.showFeedback('Recipient added successfully.');
          this.loadPageData();
        },
        error: (error) => this.showFeedback('Failed to save recipient.', true, error)
      });
      return;
    }

    const originalRecipient = this.recipients.find((recipient) => recipient.id === this.recipientForm.id);
    if (!originalRecipient) {
      this.showFeedback('Recipient not found.', true);
      return;
    }

    const requests = [];

    if ((originalRecipient.name || '') !== name) {
      requests.push(this.api.updateRecipientName(originalRecipient.id, name));
    }

    if ((originalRecipient.description || '') !== description) {
      requests.push(this.api.updateRecipientDescription(originalRecipient.id, description));
    }

    const originalCompanyId = originalRecipient.company_info?.id || originalRecipient.company_id || '';
    if (originalCompanyId !== companyId) {
      requests.push(this.api.updateRecipientCompany(originalRecipient.id, companyId || null));
    }

    if (requests.length === 0) {
      this.closeRecipientModal();
      this.showFeedback('No recipient changes detected.');
      return;
    }

    forkJoin(requests).subscribe({
      next: () => {
        this.closeRecipientModal();
        this.showFeedback('Recipient updated successfully.');
        this.loadPageData();
      },
      error: (error) => this.showFeedback('Failed to save recipient.', true, error)
    });
  }

  deleteRecipient(recipient: Recipient, event?: Event): void {
    event?.stopPropagation();

    if (!window.confirm(`Delete recipient "${recipient.name || recipient.email}"?`)) {
      return;
    }

    this.api.deleteRecipient(recipient.id).subscribe({
      next: () => {
        if (this.selectedRecipientId === recipient.id) {
          this.selectedRecipientId = null;
        }
        this.showFeedback('Recipient deleted successfully.');
        this.loadPageData();
      },
      error: (error) => this.showFeedback('Failed to delete recipient.', true, error)
    });
  }

  generateRecipientCoverLetter(recipient: Recipient, event?: Event): void {
    event?.stopPropagation();
    this.generatingRecipientId = recipient.id;

    this.api.generateRecipientCoverLetter(recipient.id).subscribe({
      next: () => {
        this.generatingRecipientId = null;
        this.showFeedback('Generation queued successfully.');
      },
      error: (error) => {
        this.generatingRecipientId = null;
        this.showFeedback('Failed to queue generation.', true, error);
      }
    });
  }

  get selectedRecipient(): Recipient | null {
    return this.visibleRecipients.find((recipient) => recipient.id === this.selectedRecipientId) || null;
  }

  get visibleRecipients(): Recipient[] {
    return this.recipients.filter((recipient) => this.matchesSelectedIdentityForRecipient(recipient));
  }

  companyInitials(company: Company | null): string {
    if (!company?.name) {
      return '--';
    }

    return company.name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase() || '')
      .join('');
  }

  recipientInitials(recipient: Recipient | null): string {
    const source = recipient?.name || recipient?.email || '';
    if (!source) {
      return '--';
    }

    return source
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase() || '')
      .join('');
  }

  private ensureSelections(): void {
    const currentRecipientSet = this.visibleRecipients;
    const hasSelectedRecipient = !!this.selectedRecipientId && currentRecipientSet.some((recipient) => recipient.id === this.selectedRecipientId);
    this.selectedRecipientId = hasSelectedRecipient ? this.selectedRecipientId : currentRecipientSet[0]?.id || null;
  }

  private getRecipientCompanyId(recipient: Recipient): string {
    return recipient.company_info?.id || recipient.company_id || '';
  }

  private matchesSelectedIdentity(company: Company): boolean {
    if (!this.selectedIdentityId) {
      return true;
    }

    const selectedIdentity = this.identities.find((identity) => identity.id === this.selectedIdentityId);
    if (!selectedIdentity) {
      return false;
    }

    const identityFieldId = selectedIdentity.field_id || selectedIdentity.field_info?.id || '';
    if (!identityFieldId) {
      return false;
    }

    const companyFieldId = company.field_id || company.field_info?.id || '';
    return companyFieldId === identityFieldId;
  }

  private matchesSelectedIdentityForRecipient(recipient: Recipient): boolean {
    if (!this.selectedIdentityId) {
      return true;
    }

    const recipientCompanyId = this.getRecipientCompanyId(recipient);
    if (!recipientCompanyId) {
      return false;
    }

    const linkedCompany = this.companies.find((company) => company.id === recipientCompanyId);
    if (!linkedCompany) {
      return false;
    }

    return this.matchesSelectedIdentity(linkedCompany);
  }

  private createEmptyRecipientForm(): RecipientFormState {
    return {
      name: '',
      email: '',
      description: '',
      company_id: ''
    };
  }

  private showFeedback(message: string, isError = false, error?: unknown): void {
    console.error(error || message);
    this.feedbackService.showFeedback(message, isError);
  }
}