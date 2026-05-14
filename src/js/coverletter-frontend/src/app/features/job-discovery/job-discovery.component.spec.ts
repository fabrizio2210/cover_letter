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
  let apiServiceSpy: jasmine.SpyObj<ApiService>;
  let feedbackServiceSpy: jasmine.SpyObj<FeedbackService>;

  beforeEach(() => {
    apiServiceSpy = jasmine.createSpyObj('ApiService', [
      'getJobDescriptions',
      'getJobPreferenceScores',
      'getIdentities',
      'getActiveCrawls',
      'getActiveScoring',
      'getActivitySummary',
      'subscribeToCrawlProgress',
      'subscribeToScoringProgress',
      'subscribeToJobUpdates',
      'scoreJobDescription',
      'checkJobDescription',
      'getJobDescription',
    ]);

    feedbackServiceSpy = jasmine.createSpyObj('FeedbackService', ['showFeedback']);

    const identityContextStub = jasmine.createSpyObj('IdentityContextService', [
      'getSelectedIdentityId',
      'setSelectedIdentityId',
      'ensureValidIdentityId',
    ]);

    const routerStub = jasmine.createSpyObj('Router', ['navigate']);

    TestBed.configureTestingModule({
      imports: [JobDiscoveryComponent],
      providers: [
        { provide: ApiService, useValue: apiServiceSpy },
        { provide: FeedbackService, useValue: feedbackServiceSpy },
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

  it('rerankVisibleJobs sends selected identity_id in scoring requests', () => {
    const selectedIdentityId = 'identity-1';
    component.selectedIdentityId = selectedIdentityId;
    component.jobs = [
      {
        id: 'job-1',
        title: 'Platform Engineer',
        description: 'Role description',
        location: 'Remote',
        platform: 'ashby',
        external_job_id: 'ext-1',
        source_url: 'https://example.com/job-1',
        score: {
          id: 'score-1',
          job_id: 'job-1',
          identity_id: selectedIdentityId,
          preference_scores: [],
          weighted_score: 4,
        },
      } as ScoredJobDescription,
    ];
    apiServiceSpy.scoreJobDescription.and.returnValue(of({ message: 'queued' }));

    component.rerankVisibleJobs();

    expect(apiServiceSpy.scoreJobDescription).toHaveBeenCalledWith('job-1', selectedIdentityId);
  });

  it('rerankVisibleJobs requires selected identity before queueing', () => {
    component.selectedIdentityId = '';
    component.jobs = [
      {
        id: 'job-2',
        title: 'Backend Engineer',
        description: 'Role description',
        location: 'Remote',
        platform: 'ashby',
        external_job_id: 'ext-2',
        source_url: 'https://example.com/job-2',
      } as ScoredJobDescription,
    ];

    component.rerankVisibleJobs();

    expect(apiServiceSpy.scoreJobDescription).not.toHaveBeenCalled();
    expect(feedbackServiceSpy.showFeedback).toHaveBeenCalledWith('Select an identity before queueing scoring.', true);
  });

  it('rerankSingleJob sends selected identity_id in scoring requests', () => {
    const selectedIdentityId = 'identity-2';
    component.selectedIdentityId = selectedIdentityId;
    apiServiceSpy.scoreJobDescription.and.returnValue(of({ message: 'queued' }));
    const job = {
      id: 'job-3',
      title: 'SRE',
      description: 'Role description',
      location: 'Remote',
      platform: 'greenhouse',
      external_job_id: 'ext-3',
      source_url: 'https://example.com/job-3',
    } as ScoredJobDescription;

    component.rerankSingleJob(job);

    expect(apiServiceSpy.scoreJobDescription).toHaveBeenCalledWith('job-3', selectedIdentityId);
  });

  it('rerankSingleJob requires selected identity before queueing', () => {
    component.selectedIdentityId = '';
    const job = {
      id: 'job-4',
      title: 'Data Engineer',
      description: 'Role description',
      location: 'Remote',
      platform: 'lever',
      external_job_id: 'ext-4',
      source_url: 'https://example.com/job-4',
    } as ScoredJobDescription;

    component.rerankSingleJob(job);

    expect(apiServiceSpy.scoreJobDescription).not.toHaveBeenCalled();
    expect(feedbackServiceSpy.showFeedback).toHaveBeenCalledWith('Select an identity before queueing scoring.', true);
  });
});

describe('JobDiscoveryComponent refreshJobsOnTerminalProgress', () => {
  let component: JobDiscoveryComponent;
  let apiServiceSpy: jasmine.SpyObj<ApiService>;
  let feedbackServiceSpy: jasmine.SpyObj<FeedbackService>;

  beforeEach(() => {
    apiServiceSpy = jasmine.createSpyObj('ApiService', [
      'getJobDescriptions',
      'getJobPreferenceScores',
      'getIdentities',
      'getActiveCrawls',
      'getActiveScoring',
      'getActivitySummary',
      'subscribeToCrawlProgress',
      'subscribeToScoringProgress',
      'subscribeToJobUpdates',
      'scoreJobDescription',
      'checkJobDescription',
      'getJobDescription',
    ]);

    feedbackServiceSpy = jasmine.createSpyObj('FeedbackService', ['showFeedback']);

    const identityContextStub = jasmine.createSpyObj('IdentityContextService', [
      'getSelectedIdentityId',
      'setSelectedIdentityId',
      'ensureValidIdentityId',
    ]);

    const routerStub = jasmine.createSpyObj('Router', ['navigate']);

    apiServiceSpy.getJobDescriptions.and.returnValue(of({
      items: [],
      page: 1,
      page_size: 25,
      total_count: 0,
      total_pages: 0,
      has_next_page: false,
      has_prev_page: false,
    }));
    apiServiceSpy.getJobPreferenceScores.and.returnValue(of([]));
    apiServiceSpy.getIdentities.and.returnValue(of([]));
    apiServiceSpy.getActiveCrawls.and.returnValue(of([]));
    apiServiceSpy.getActiveScoring.and.returnValue(of([]));
    apiServiceSpy.getActivitySummary.and.returnValue(of({
      active_workflows: [],
      global_queue_depth: {
        crawler_trigger: 0,
        crawler_ycombinator: 0,
        crawler_hackernews: 0,
        crawler_ats_job_extraction: 0,
        crawler_levelsfyi: 0,
        crawler_4dayweek: 0,
        crawler_enrichment_ats: 0,
        job_scoring: 0,
      },
    } as any));
    apiServiceSpy.subscribeToCrawlProgress.and.returnValue(of());
    apiServiceSpy.subscribeToScoringProgress.and.returnValue(of());
    apiServiceSpy.subscribeToJobUpdates.and.returnValue(of());
    identityContextStub.ensureValidIdentityId.and.returnValue('identity-1');

    TestBed.configureTestingModule({
      imports: [JobDiscoveryComponent],
      providers: [
        { provide: ApiService, useValue: apiServiceSpy },
        { provide: FeedbackService, useValue: feedbackServiceSpy },
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
    component.selectedIdentityId = 'identity-1';
  });

  it('scoring completion reloads scores only without triggering full loadData', () => {
    apiServiceSpy.getJobPreferenceScores.calls.reset();
    apiServiceSpy.getJobDescriptions.calls.reset();

    (component as any).refreshJobsOnTerminalProgress('scoring', 'identity-1', 'run-1', 'completed');

    expect(apiServiceSpy.getJobPreferenceScores).toHaveBeenCalledTimes(1);
    expect(apiServiceSpy.getJobDescriptions).not.toHaveBeenCalled();
  });

  it('crawl completion triggers full loadData', () => {
    apiServiceSpy.getJobDescriptions.calls.reset();
    apiServiceSpy.getJobPreferenceScores.calls.reset();

    (component as any).refreshJobsOnTerminalProgress('crawl', 'identity-1', 'run-2', 'completed');

    expect(apiServiceSpy.getJobDescriptions).toHaveBeenCalled();
  });

  it('retirement-check crawl completion does not trigger full loadData', () => {
    apiServiceSpy.getJobDescriptions.calls.reset();
    apiServiceSpy.getJobPreferenceScores.calls.reset();

    (component as any).refreshJobsOnTerminalProgress(
      'crawl',
      'identity-1',
      'run-2',
      'completed',
      'enrichment_retiring_jobs',
    );

    expect(apiServiceSpy.getJobDescriptions).not.toHaveBeenCalled();
    expect(apiServiceSpy.getJobPreferenceScores).not.toHaveBeenCalled();
  });

  it('scoring completion is deduplicated for the same run_id', () => {
    apiServiceSpy.getJobPreferenceScores.calls.reset();

    (component as any).refreshJobsOnTerminalProgress('scoring', 'identity-1', 'run-3', 'completed');
    (component as any).refreshJobsOnTerminalProgress('scoring', 'identity-1', 'run-3', 'completed');

    expect(apiServiceSpy.getJobPreferenceScores).toHaveBeenCalledTimes(1);
  });

  it('filters enrichment_retiring_jobs from visible activity workflows', () => {
    component.activitySummary = {
      active_workflows: [
        { workflow_id: 'crawler_4dayweek', status: 'running', message: '' },
        { workflow_id: 'enrichment_retiring_jobs', status: 'running', message: '' },
      ],
      global_queue_depth: {
        crawler_trigger: 0,
        crawler_ycombinator: 0,
        crawler_hackernews: 0,
        crawler_ats_job_extraction: 0,
        crawler_levelsfyi: 0,
        crawler_4dayweek: 0,
        crawler_enrichment_ats: 0,
        job_scoring: 0,
      },
    } as any;

    expect(component.visibleActivityWorkflows.length).toBe(1);
    expect(component.visibleActivityWorkflows[0].workflow_id).toBe('crawler_4dayweek');
  });

  it('does not treat enrichment_retiring_jobs as an active crawl blocker', () => {
    component.selectedIdentityId = 'identity-1';
    (component as any).setCrawlSnapshots([
      {
        run_id: 'run-enrichment',
        identity_id: 'identity-1',
        status: 'running',
        workflow_id: 'enrichment_retiring_jobs',
        workflow: 'enrichment_retiring_jobs',
        message: 'Checking stale jobs',
        estimated_total: 1,
        completed: 0,
        percent: 0,
        updated_at: { seconds: 10, nanos: 0 },
      },
    ]);

    expect(component.selectedIdentityHasActiveCrawl).toBeFalse();
  });

  it('reloadSingleJob updates only the targeted job entry', () => {
    component.rawJobs = [
      {
        id: 'job-1',
        title: 'Old title',
        description: 'Old description',
        location: 'Remote',
        platform: 'ashby',
        external_job_id: 'ext-1',
        source_url: 'https://example.com/job-1',
      } as any,
      {
        id: 'job-2',
        title: 'Second job',
        description: 'Desc',
        location: 'Remote',
        platform: 'ashby',
        external_job_id: 'ext-2',
        source_url: 'https://example.com/job-2',
      } as any,
    ];
    apiServiceSpy.getJobDescription.and.returnValue(of({
      id: 'job-1',
      title: 'Updated title',
      description: 'Updated description',
      location: 'Remote',
      platform: 'ashby',
      external_job_id: 'ext-1',
      source_url: 'https://example.com/job-1',
    } as any));

    (component as any).reloadSingleJob('job-1');

    expect(component.rawJobs.length).toBe(2);
    expect(component.rawJobs[0].id).toBe('job-1');
    expect(component.rawJobs[0].title).toBe('Updated title');
    expect(component.rawJobs[1].id).toBe('job-2');
    expect(component.rawJobs[1].title).toBe('Second job');
  });
});

