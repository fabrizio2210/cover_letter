/// <reference types="jasmine" />

import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, Router, convertToParamMap } from '@angular/router';
import { of } from 'rxjs';

import { ApiService } from '../../core/services/api.service';
import { FeedbackService } from '../../core/services/feedback.service';
import { IdentityContextService } from '../../core/services/identity-context.service';
import { Identity, ScoredJobDescription } from '../../shared/models/models';
import { JobDiscoveryComponent } from './job-discovery.component';

describe('JobDiscoveryComponent identity filtering', () => {
  let component: JobDiscoveryComponent;

  beforeEach(() => {
    const apiServiceStub = jasmine.createSpyObj('ApiService', [
      'getJobDescriptions',
      'getJobPreferenceScores',
      'getIdentities',
      'getActiveCrawls',
      'getActiveScoring',
      'getActivitySummary',
      'subscribeToCrawlProgress',
      'subscribeToScoringProgress',
      'subscribeToJobUpdates',
      'checkJobDescription',
      'getJobDescription',
    ]);

    const feedbackServiceStub = jasmine.createSpyObj('FeedbackService', ['showFeedback']);

    const identityContextStub = jasmine.createSpyObj('IdentityContextService', [
      'getSelectedIdentityId',
      'setSelectedIdentityId',
      'ensureValidIdentityId',
    ]);

    const routerStub = jasmine.createSpyObj('Router', ['navigate']);

    TestBed.configureTestingModule({
      imports: [JobDiscoveryComponent],
      providers: [
        { provide: ApiService, useValue: apiServiceStub },
        { provide: FeedbackService, useValue: feedbackServiceStub },
        { provide: IdentityContextService, useValue: identityContextStub },
        { provide: Router, useValue: routerStub },
        {
          provide: ActivatedRoute,
          useValue: {
            queryParamMap: of(convertToParamMap({})),
          },
        },
      ],
    });

    const fixture = TestBed.createComponent(JobDiscoveryComponent);
    component = fixture.componentInstance;
  });

  it('keeps unscored jobs visible when company field metadata is missing', () => {
    const identity: Identity = {
      id: 'identity-1',
      identity: 'platform-eng',
      field_id: 'field-platform',
    };

    const unscoredJob = {
      id: 'job-1',
      title: 'Backend Engineer',
      description: 'Role description',
      location: 'Remote',
      platform: '4dayweek',
      external_job_id: 'ext-1',
      source_url: 'https://example.com/job-1',
      score: null,
    } as ScoredJobDescription;

    component.identities = [identity];
    component.selectedIdentityId = identity.id;

    const matches = (component as any).matchesIdentity(unscoredJob);
    expect(matches).toBeTrue();
  });

  it('filters out unscored jobs when company field metadata conflicts with identity field', () => {
    const identity: Identity = {
      id: 'identity-1',
      identity: 'platform-eng',
      field_id: 'field-platform',
    };

    const unscoredJob = {
      id: 'job-2',
      title: 'Data Scientist',
      description: 'Role description',
      location: 'Remote',
      platform: '4dayweek',
      external_job_id: 'ext-2',
      source_url: 'https://example.com/job-2',
      score: null,
      company_info: {
        id: 'company-1',
        name: 'Example',
        field_id: 'field-data',
      },
    } as ScoredJobDescription;

    component.identities = [identity];
    component.selectedIdentityId = identity.id;

    const matches = (component as any).matchesIdentity(unscoredJob);
    expect(matches).toBeFalse();
  });

  it('keeps jobs visible when score belongs to selected identity', () => {
    const identity: Identity = {
      id: 'identity-1',
      identity: 'platform-eng',
      field_id: 'field-platform',
    };

    const scoredJob = {
      id: 'job-3',
      title: 'Senior Engineer',
      description: 'Role description',
      location: 'Remote',
      platform: '4dayweek',
      external_job_id: 'ext-3',
      source_url: 'https://example.com/job-3',
      score: {
        id: 'score-1',
        job_id: 'job-3',
        identity_id: identity.id,
        preference_scores: [],
        weighted_score: 4,
      },
    } as ScoredJobDescription;

    component.identities = [identity];
    component.selectedIdentityId = identity.id;

    const matches = (component as any).matchesIdentity(scoredJob);
    expect(matches).toBeTrue();
  });
});
