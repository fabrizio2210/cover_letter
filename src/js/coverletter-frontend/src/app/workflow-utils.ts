import { CrawlProgress, LastRunWorkflowStatsItem } from './models/models';

export const dashboardWorkflowOrder: LastRunWorkflowStatsItem['workflow_id'][] = [
  'crawler_company_discovery',
  'crawler_levelsfyi',
  'crawler_4dayweek',
  'crawler_ats_job_extraction',
];

type WorkflowKey = NonNullable<CrawlProgress['workflow']> | NonNullable<CrawlProgress['workflow_id']>;

const workflowLabels: Record<WorkflowKey, string> = {
  queued: 'Queued',
  crawler_company_discovery: 'Company discovery',
  enrichment_ats_enrichment: 'ATS enrichment',
  crawler_ats_job_extraction: 'ATS extraction',
  crawler_4dayweek: '4dayweek discovery',
  crawler_levelsfyi: 'Levels.fyi discovery',
  finalizing: 'Finalizing',
};

export function getWorkflowLabel(workflow?: WorkflowKey | null): string {
  if (!workflow) {
    return 'Queued';
  }

  return workflowLabels[workflow] || 'Queued';
}

export function getCrawlSnapshotKey(progress: Pick<CrawlProgress, 'workflow_run_id' | 'run_id' | 'identity_id' | 'workflow_id' | 'workflow'>): string {
  return progress.workflow_run_id || `${progress.identity_id}:${progress.run_id}:${progress.workflow_id || progress.workflow}`;
}

export function getCrawlStatusRank(status: CrawlProgress['status']): number {
  if (status === 'running') {
    return 3;
  }

  if (status === 'queued') {
    return 2;
  }

  return 1;
}