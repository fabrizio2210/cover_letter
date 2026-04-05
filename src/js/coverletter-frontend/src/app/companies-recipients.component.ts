import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { forkJoin } from 'rxjs';
import { ApiService } from './services/api.service';
import { FeedbackService } from './services/feedback.service';
import { Company, Field, JobDescription, Recipient } from './models/models';

type ManagementTab = 'companies' | 'recipients';

interface CompanyFormState {
  id?: string;
  name: string;
  description: string;
  field_id: string;
  ats_provider: string;
  ats_slug: string;
}

interface RecipientFormState {
  id?: string;
  name: string;
  email: string;
  description: string;
  company_id: string;
}

@Component({
  selector: 'app-companies-recipients',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './companies-recipients.component.html',
  styleUrls: ['./companies-recipients.component.css']
})
export class CompaniesRecipientsComponent implements OnInit {
  private api = inject(ApiService);
  private feedbackService = inject(FeedbackService);

  activeTab: ManagementTab = 'companies';
  loading = false;
  showCompaniesWithJobsOnly = false;

  companies: Company[] = [];
  recipients: Recipient[] = [];
  fields: Field[] = [];
  jobs: JobDescription[] = [];

  selectedCompanyId: string | null = null;
  selectedRecipientId: string | null = null;
  generatingRecipientId: string | null = null;

  isCompanyModalOpen = false;
  isRecipientModalOpen = false;

  companyForm: CompanyFormState = this.createEmptyCompanyForm();
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
      jobs: this.api.getJobDescriptions()
    }).subscribe({
      next: ({ companies, recipients, fields, jobs }) => {
        this.companies = [...companies].sort((left, right) => left.name.localeCompare(right.name));
        this.recipients = [...recipients].sort((left, right) => (left.name || left.email).localeCompare(right.name || right.email));
        this.fields = [...fields].sort((left, right) => left.field.localeCompare(right.field));
        this.jobs = [...jobs].sort((left, right) => (right.updated_at ? 1 : 0) - (left.updated_at ? 1 : 0));
        this.ensureSelections();
        this.loading = false;
      }
    });
  }

  switchTab(tab: ManagementTab): void {
    this.activeTab = tab;
    this.ensureSelections();
  }

  toggleCompaniesWithJobsOnly(value: boolean): void {
    this.showCompaniesWithJobsOnly = value;
    this.ensureSelections();
  }

  selectCompany(company: Company): void {
    this.selectedCompanyId = company.id;
  }

  selectRecipient(recipient: Recipient): void {
    this.selectedRecipientId = recipient.id;
  }

  openCompanyCreateModal(): void {
    this.companyForm = this.createEmptyCompanyForm();
    this.isCompanyModalOpen = true;
  }

  openCompanyEditModal(company: Company): void {
    this.companyForm = {
      id: company.id,
      name: company.name,
      description: company.description || '',
      field_id: company.field_info?.id || company.field_id || '',
      ats_provider: company.ats_provider || '',
      ats_slug: company.ats_slug || ''
    };
    this.isCompanyModalOpen = true;
  }

  closeCompanyModal(): void {
    this.isCompanyModalOpen = false;
    this.companyForm = this.createEmptyCompanyForm();
  }

  saveCompany(): void {
    const name = this.companyForm.name.trim();
    const description = this.companyForm.description.trim();
    const atsProvider = this.companyForm.ats_provider.trim();
    const atsSlug = this.companyForm.ats_slug.trim();
    const fieldId = this.companyForm.field_id;

    if (!name) {
      this.showFeedback('Company name cannot be empty.', true);
      return;
    }

    if (atsSlug && !atsProvider) {
      this.showFeedback('Select an ATS provider before setting an ATS slug.', true);
      return;
    }

    if (this.companyForm.id && !fieldId) {
      this.showFeedback('Sector is required when updating a company.', true);
      return;
    }

    const payload: Partial<Company> = {
      name,
      description,
      field_id: fieldId || ''
    };

    if (atsProvider) {
      payload.ats_provider = atsProvider;
    }
    if (atsSlug) {
      payload.ats_slug = atsSlug;
    }

    if (this.companyForm.id) {
      this.api.updateCompany(this.companyForm.id, payload).subscribe({
        next: () => {
          this.closeCompanyModal();
          this.showFeedback('Company updated successfully.');
          this.loadPageData();
        },
        error: (error: unknown) => this.showFeedback('Failed to save company.', true, error)
      });
      return;
    }

    this.api.createCompany(payload).subscribe({
      next: (company: Company) => {
        this.selectedCompanyId = company.id;
        this.closeCompanyModal();
        this.showFeedback('Company created successfully.');
        this.loadPageData();
      },
      error: (error: unknown) => this.showFeedback('Failed to save company.', true, error)
    });
  }

  deleteCompany(company: Company, event?: Event): void {
    event?.stopPropagation();

    if (!window.confirm(`Delete company "${company.name}"?`)) {
      return;
    }

    this.api.deleteCompany(company.id).subscribe({
      next: () => {
        if (this.selectedCompanyId === company.id) {
          this.selectedCompanyId = null;
        }
        this.showFeedback('Company deleted successfully.');
        this.loadPageData();
      },
      error: (error) => this.showFeedback('Failed to delete company.', true, error)
    });
  }

  openRecipientCreateModal(prefilledCompanyId = ''): void {
    this.recipientForm = {
      ...this.createEmptyRecipientForm(),
      company_id: prefilledCompanyId
    };
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

  focusRecipientCompany(recipient: Recipient): void {
    const companyId = recipient.company_info?.id || recipient.company_id || '';
    if (!companyId) {
      return;
    }

    this.activeTab = 'companies';
    this.selectedCompanyId = companyId;
  }

  get selectedCompany(): Company | null {
    return this.companies.find((company) => company.id === this.selectedCompanyId) || null;
  }

  get visibleCompanies(): Company[] {
    if (!this.showCompaniesWithJobsOnly) {
      return this.companies;
    }

    return this.companies.filter((company) => this.companyJobsCount(company) > 0);
  }

  get selectedRecipient(): Recipient | null {
    return this.recipients.find((recipient) => recipient.id === this.selectedRecipientId) || null;
  }

  get selectedCompanyRecipients(): Recipient[] {
    if (!this.selectedCompany) {
      return [];
    }

    return this.recipients.filter((recipient) => this.getRecipientCompanyId(recipient) === this.selectedCompany?.id);
  }

  get selectedCompanyJobs(): JobDescription[] {
    if (!this.selectedCompany) {
      return [];
    }

    return this.jobs.filter((job) => this.getJobCompanyId(job) === this.selectedCompany?.id).slice(0, 8);
  }

  companyJobsCount(company: Company): number {
    return this.jobs.filter((job) => this.getJobCompanyId(job) === company.id).length;
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

  fieldName(company: Company): string {
    return company.field_info?.field || 'Unassigned';
  }

  companyAtsLabel(company: Company): string {
    if (company.ats_provider && company.ats_slug) {
      return `${company.ats_provider}:${company.ats_slug}`;
    }

    if (company.ats_slug) {
      return company.ats_slug;
    }

    return 'Not linked';
  }

  private ensureSelections(): void {
    if (this.activeTab === 'companies') {
      const currentCompanySet = this.visibleCompanies;
      const hasSelectedCompany = !!this.selectedCompanyId && currentCompanySet.some((company) => company.id === this.selectedCompanyId);
      this.selectedCompanyId = hasSelectedCompany ? this.selectedCompanyId : currentCompanySet[0]?.id || null;
      return;
    }

    const hasSelectedRecipient = !!this.selectedRecipientId && this.recipients.some((recipient) => recipient.id === this.selectedRecipientId);
    this.selectedRecipientId = hasSelectedRecipient ? this.selectedRecipientId : this.recipients[0]?.id || null;
  }

  private getRecipientCompanyId(recipient: Recipient): string {
    return recipient.company_info?.id || recipient.company_id || '';
  }

  private getJobCompanyId(job: JobDescription): string {
    return job.company_info?.id || job.company_id || '';
  }

  private createEmptyCompanyForm(): CompanyFormState {
    return {
      name: '',
      description: '',
      field_id: '',
      ats_provider: '',
      ats_slug: ''
    };
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