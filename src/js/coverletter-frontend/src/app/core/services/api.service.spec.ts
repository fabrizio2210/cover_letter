/// <reference types="jasmine" />

import { TestBed } from '@angular/core/testing';
import { HttpClientTestingModule, HttpTestingController } from '@angular/common/http/testing';
import { ApiService } from './api.service';
import { AuthService } from '../auth/auth.service';

describe('ApiService', () => {
  let service: ApiService;
  let httpMock: HttpTestingController;
  let authServiceSpy: jasmine.SpyObj<AuthService>;

  beforeEach(() => {
    authServiceSpy = jasmine.createSpyObj('AuthService', ['getToken']);
    authServiceSpy.getToken.and.returnValue('test-token');

    TestBed.configureTestingModule({
      imports: [HttpClientTestingModule],
      providers: [
        ApiService,
        { provide: AuthService, useValue: authServiceSpy },
      ],
    });

    service = TestBed.inject(ApiService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
  });

  // Fields
  it('listFields calls GET /api/fields', () => {
    service.listFields().subscribe();
    const req = httpMock.expectOne('/api/fields');
    expect(req.request.method).toBe('GET');
    req.flush([]);
  });

  it('getFields returns empty array on error', (done) => {
    service.getFields().subscribe((result) => {
      expect(result).toEqual([]);
      done();
    });
    const req = httpMock.expectOne('/api/fields');
    req.error(new ProgressEvent('error'));
  });

  // Companies
  it('listCompanies calls GET /api/companies', () => {
    service.listCompanies().subscribe();
    const req = httpMock.expectOne('/api/companies');
    expect(req.request.method).toBe('GET');
    req.flush([]);
  });

  it('createCompany calls POST /api/companies with payload', () => {
    const payload = { name: 'Acme' };
    service.createCompany(payload).subscribe();
    const req = httpMock.expectOne('/api/companies');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual(payload);
    req.flush({ id: '1', name: 'Acme' });
  });

  it('deleteCompany calls DELETE /api/companies/:id', () => {
    service.deleteCompany('abc123').subscribe();
    const req = httpMock.expectOne('/api/companies/abc123');
    expect(req.request.method).toBe('DELETE');
    req.flush({ message: 'deleted' });
  });

  // Recipients
  it('listRecipients calls GET /api/recipients', () => {
    service.listRecipients().subscribe();
    const req = httpMock.expectOne('/api/recipients');
    expect(req.request.method).toBe('GET');
    req.flush([]);
  });

  it('createRecipient calls POST /api/recipients', () => {
    const payload = { email: 'a@b.com', name: 'Alice' };
    service.createRecipient(payload).subscribe();
    const req = httpMock.expectOne('/api/recipients');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual(payload);
    req.flush({ id: '1', ...payload });
  });

  it('deleteRecipient calls DELETE /api/recipients/:id', () => {
    service.deleteRecipient('r1').subscribe();
    const req = httpMock.expectOne('/api/recipients/r1');
    expect(req.request.method).toBe('DELETE');
    req.flush({ message: 'deleted' });
  });

  // Identities
  it('getIdentities calls GET /api/identities', () => {
    service.getIdentities().subscribe();
    const req = httpMock.expectOne('/api/identities');
    expect(req.request.method).toBe('GET');
    req.flush([]);
  });

  it('getIdentities returns empty array on error', (done) => {
    service.getIdentities().subscribe((result) => {
      expect(result).toEqual([]);
      done();
    });
    const req = httpMock.expectOne('/api/identities');
    req.error(new ProgressEvent('error'));
  });

  // Crawls
  it('triggerCrawl calls POST /api/crawls with identity_id', () => {
    service.triggerCrawl('identity-1').subscribe();
    const req = httpMock.expectOne('/api/crawls');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ identity_id: 'identity-1' });
    req.flush({ message: 'queued', run_id: 'r1', identity_id: 'identity-1', status: 'queued' });
  });

  it('getActiveCrawls calls GET /api/crawls/active without filter', () => {
    service.getActiveCrawls().subscribe();
    const req = httpMock.expectOne('/api/crawls/active');
    expect(req.request.method).toBe('GET');
    req.flush([]);
  });

  it('getActiveCrawls includes identity_id query param when provided', () => {
    service.getActiveCrawls('id-99').subscribe();
    const req = httpMock.expectOne('/api/crawls/active?identity_id=id-99');
    expect(req.request.method).toBe('GET');
    req.flush([]);
  });

  it('getLastRunWorkflowStats calls GET /api/crawls/last-run/workflow-stats', () => {
    service.getLastRunWorkflowStats().subscribe();
    const req = httpMock.expectOne('/api/crawls/last-run/workflow-stats');
    expect(req.request.method).toBe('GET');
    req.flush({ completed_at: null, workflows: [] });
  });

  it('getLastRunWorkflowStats returns fallback on error', (done) => {
    service.getLastRunWorkflowStats().subscribe((result) => {
      expect(result.workflows).toEqual([]);
      expect(result.completed_at).toBeNull();
      done();
    });
    const req = httpMock.expectOne('/api/crawls/last-run/workflow-stats');
    req.error(new ProgressEvent('error'));
  });

  // Job scoring
  it('scoreJobDescription calls POST /api/job-descriptions/:id/score', () => {
    service.scoreJobDescription('job-1').subscribe();
    const req = httpMock.expectOne('/api/job-descriptions/job-1/score');
    expect(req.request.method).toBe('POST');
    req.flush({ message: 'queued' });
  });

  it('getJobPreferenceScores calls GET /api/job-preference-scores without filters', () => {
    service.getJobPreferenceScores().subscribe();
    const req = httpMock.expectOne('/api/job-preference-scores');
    expect(req.request.method).toBe('GET');
    req.flush([]);
  });

  it('getJobPreferenceScores appends query params when filters provided', () => {
    service.getJobPreferenceScores({ jobId: 'j1', identityId: 'i1' }).subscribe();
    const req = httpMock.expectOne('/api/job-preference-scores?job_id=j1&identity_id=i1');
    expect(req.request.method).toBe('GET');
    req.flush([]);
  });
});
