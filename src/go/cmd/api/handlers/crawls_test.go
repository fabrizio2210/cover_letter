package handlers

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	"github.com/fabrizio2210/cover_letter/src/go/cmd/api/models"
	"github.com/gin-gonic/gin"
	"github.com/go-redis/redis/v8"
	"github.com/stretchr/testify/require"
	"go.mongodb.org/mongo-driver/bson"
)

func TestTriggerCrawl_InvalidIdentityID(t *testing.T) {
	resetCrawlStateForTests()
	body := bytes.NewBufferString(`{"identity_id":"invalid"}`)
	req, _ := http.NewRequest(http.MethodPost, "/api/crawls", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req

	TriggerCrawl(c)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

func TestTriggerCrawl_QueuesPayload(t *testing.T) {
	resetCrawlStateForTests()
	m, err := miniredis.Run()
	require.NoError(t, err)
	defer m.Close()

	rclient := redis.NewClient(&redis.Options{Addr: m.Addr()})
	SetRedisClientForTests(rclient)
	t.Setenv("CRAWLER_TRIGGER_QUEUE_NAME", "test_crawler_queue")

	identityID := "69a41885879f30791d7dfa77"

	fakeC := &fakeClient{db: &fakeDatabase{cols: map[string]*fakeCollection{
		"identities": {findOneDoc: bson.M{"roles": bson.A{"software engineer"}}},
	}}}
	oldMongo := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeC }
	defer func() { GetMongoClient = oldMongo }()

	body := bytes.NewBufferString(`{"identity_id":"` + identityID + `"}`)
	req, _ := http.NewRequest(http.MethodPost, "/api/crawls", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req

	TriggerCrawl(c)
	require.Equal(t, http.StatusAccepted, w.Code)

	queueValues, err := rclient.LRange(context.Background(), "test_crawler_queue", 0, -1).Result()
	require.NoError(t, err)
	require.Len(t, queueValues, 1)

	var payload map[string]interface{}
	require.NoError(t, json.Unmarshal([]byte(queueValues[0]), &payload))
	require.Equal(t, identityID, payload["identity_id"])
	require.NotEmpty(t, payload["run_id"])
	require.NotNil(t, payload["requested_at"])

	var response map[string]interface{}
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &response))
	require.Equal(t, "queued", response["status"])
	require.Equal(t, identityID, response["identity_id"])
	require.NotEmpty(t, response["run_id"])

	snapshots := crawlHub.listSnapshots(identityID)
	require.Len(t, snapshots, 1)
	require.Equal(t, "queued", snapshots[0].Status)
}

func TestTriggerCrawl_RejectsDuplicateIdentity(t *testing.T) {
	resetCrawlStateForTests()
	m, err := miniredis.Run()
	require.NoError(t, err)
	defer m.Close()

	rclient := redis.NewClient(&redis.Options{Addr: m.Addr()})
	SetRedisClientForTests(rclient)

	fakeC := &fakeClient{db: &fakeDatabase{cols: map[string]*fakeCollection{
		"identities": {findOneDoc: bson.M{"roles": bson.A{"software engineer"}}},
	}}}
	oldMongo := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeC }
	defer func() { GetMongoClient = oldMongo }()

	identityID := "69a41885879f30791d7dfa77"
	crawlHub.publish(&models.CrawlProgress{
		RunId:          "run-1",
		IdentityId:     identityID,
		Status:         "running",
		Workflow:       "crawler_company_discovery",
		EstimatedTotal: 100,
		Completed:      10,
		Percent:        10,
		UpdatedAt:      timestampPtr(time.Now().UTC()),
	})

	body := bytes.NewBufferString(`{"identity_id":"` + identityID + `"}`)
	req, _ := http.NewRequest(http.MethodPost, "/api/crawls", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req

	TriggerCrawl(c)
	require.Equal(t, http.StatusConflict, w.Code)
	require.Contains(t, w.Body.String(), "already running")
	require.Contains(t, w.Body.String(), "run-1")
}

func TestTriggerCrawl_RejectsIdentityNotFound(t *testing.T) {
	resetCrawlStateForTests()

	fakeC := &fakeClient{db: &fakeDatabase{cols: map[string]*fakeCollection{
		"identities": {findOneDoc: nil},
	}}}
	oldMongo := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeC }
	defer func() { GetMongoClient = oldMongo }()

	identityID := "69a41885879f30791d7dfa77"
	body := bytes.NewBufferString(`{"identity_id":"` + identityID + `"}`)
	req, _ := http.NewRequest(http.MethodPost, "/api/crawls", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req

	TriggerCrawl(c)
	require.Equal(t, http.StatusBadRequest, w.Code)
	require.Contains(t, w.Body.String(), "Identity not found")
}

func TestTriggerCrawl_RejectsIdentityWithNoRoles(t *testing.T) {
	resetCrawlStateForTests()

	fakeC := &fakeClient{db: &fakeDatabase{cols: map[string]*fakeCollection{
		"identities": {findOneDoc: bson.M{"roles": bson.A{}}},
	}}}
	oldMongo := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeC }
	defer func() { GetMongoClient = oldMongo }()

	identityID := "69a41885879f30791d7dfa77"
	body := bytes.NewBufferString(`{"identity_id":"` + identityID + `"}`)
	req, _ := http.NewRequest(http.MethodPost, "/api/crawls", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req

	TriggerCrawl(c)
	require.Equal(t, http.StatusBadRequest, w.Code)
	require.Contains(t, w.Body.String(), "no roles")
}

func TestGetActiveCrawls_FiltersByIdentity(t *testing.T) {
	resetCrawlStateForTests()
	crawlHub.publish(&models.CrawlProgress{RunId: "run-1", IdentityId: "identity-a", Status: "queued", Workflow: "queued", EstimatedTotal: 100, UpdatedAt: timestampPtr(time.Now().UTC())})
	crawlHub.publish(&models.CrawlProgress{RunId: "run-2", IdentityId: "identity-b", Status: "completed", Workflow: "finalizing", EstimatedTotal: 100, Percent: 100, UpdatedAt: timestampPtr(time.Now().UTC())})

	req, _ := http.NewRequest(http.MethodGet, "/api/crawls/active?identity_id=identity-b", nil)
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req

	GetActiveCrawls(c)
	require.Equal(t, http.StatusOK, w.Code)

	var snapshots []models.CrawlProgress
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &snapshots))
	require.Len(t, snapshots, 1)
	require.Equal(t, "identity-b", snapshots[0].IdentityId)
	require.Equal(t, "completed", snapshots[0].Status)
}

func TestGetActiveCrawls_PreservesDistinctWorkflowContributions(t *testing.T) {
	resetCrawlStateForTests()
	now := time.Now().UTC()

	crawlHub.publish(&models.CrawlProgress{
		RunId:          "run-1",
		WorkflowRunId:  "wf-1",
		WorkflowId:     "crawler_company_discovery",
		IdentityId:     "identity-a",
		Status:         "running",
		Workflow:       "crawler_company_discovery",
		EstimatedTotal: 100,
		Completed:      20,
		Percent:        20,
		UpdatedAt:      timestampPtr(now),
	})
	crawlHub.publish(&models.CrawlProgress{
		RunId:          "run-1",
		WorkflowRunId:  "wf-2",
		WorkflowId:     "crawler_ats_job_extraction",
		IdentityId:     "identity-a",
		Status:         "running",
		Workflow:       "crawler_ats_job_extraction",
		EstimatedTotal: 80,
		Completed:      10,
		Percent:        12,
		UpdatedAt:      timestampPtr(now.Add(1 * time.Second)),
	})

	req, _ := http.NewRequest(http.MethodGet, "/api/crawls/active?identity_id=identity-a", nil)
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req

	GetActiveCrawls(c)
	require.Equal(t, http.StatusOK, w.Code)

	var snapshots []models.CrawlProgress
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &snapshots))
	require.Len(t, snapshots, 2)

	workflowRuns := map[string]bool{}
	for _, snapshot := range snapshots {
		workflowRuns[snapshot.WorkflowRunId] = true
	}
	require.True(t, workflowRuns["wf-1"])
	require.True(t, workflowRuns["wf-2"])
}

func TestGetLastRunWorkflowStats_EmptyState(t *testing.T) {
	resetCrawlStateForTests()
	req, _ := http.NewRequest(http.MethodGet, "/api/crawls/last-run/workflow-stats", nil)
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req

	GetLastRunWorkflowStats(c)
	require.Equal(t, http.StatusOK, w.Code)

	var response map[string]interface{}
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &response))
	require.Nil(t, response["completed_at"])
	workflows, ok := response["workflows"].([]interface{})
	require.True(t, ok)
	require.Len(t, workflows, 0)
}

func TestGetLastRunWorkflowStats_CrawlerOnlyStableOrder(t *testing.T) {
	resetCrawlStateForTests()
	now := time.Now().UTC()

	crawlHub.publish(&models.CrawlProgress{
		RunId:         "run-early",
		WorkflowRunId: "wf-old",
		WorkflowId:    "crawler_company_discovery",
		IdentityId:    "identity-1",
		Status:        "completed",
		Workflow:      "crawler_company_discovery",
		Completed:     5,
		Percent:       100,
		UpdatedAt:     timestampPtr(now.Add(-10 * time.Minute)),
		FinishedAt:    timestampPtr(now.Add(-10 * time.Minute)),
	})
	crawlHub.publish(&models.CrawlProgress{
		RunId:      "run-early",
		IdentityId: "identity-1",
		Status:     "completed",
		Workflow:   "finalizing",
		Percent:    100,
		UpdatedAt:  timestampPtr(now.Add(-9 * time.Minute)),
		FinishedAt: timestampPtr(now.Add(-9 * time.Minute)),
	})

	crawlHub.publish(&models.CrawlProgress{
		RunId:         "run-latest",
		WorkflowRunId: "wf-lvl",
		WorkflowId:    "crawler_levelsfyi",
		IdentityId:    "identity-2",
		Status:        "completed",
		Workflow:      "crawler_levelsfyi",
		Completed:     7,
		Percent:       100,
		UpdatedAt:     timestampPtr(now.Add(-4 * time.Minute)),
		FinishedAt:    timestampPtr(now.Add(-4 * time.Minute)),
	})
	crawlHub.publish(&models.CrawlProgress{
		RunId:         "run-latest",
		WorkflowRunId: "wf-4dw",
		WorkflowId:    "crawler_4dayweek",
		IdentityId:    "identity-2",
		Status:        "completed",
		Workflow:      "crawler_4dayweek",
		Completed:     3,
		Percent:       100,
		UpdatedAt:     timestampPtr(now.Add(-3 * time.Minute)),
		FinishedAt:    timestampPtr(now.Add(-3 * time.Minute)),
	})
	crawlHub.publish(&models.CrawlProgress{
		RunId:         "run-latest",
		WorkflowRunId: "wf-company",
		WorkflowId:    "crawler_company_discovery",
		IdentityId:    "identity-2",
		Status:        "completed",
		Workflow:      "crawler_company_discovery",
		Completed:     11,
		Percent:       100,
		UpdatedAt:     timestampPtr(now.Add(-2 * time.Minute)),
		FinishedAt:    timestampPtr(now.Add(-2 * time.Minute)),
	})
	crawlHub.publish(&models.CrawlProgress{
		RunId:         "run-latest",
		WorkflowRunId: "wf-ats",
		WorkflowId:    "crawler_ats_job_extraction",
		IdentityId:    "identity-2",
		Status:        "completed",
		Workflow:      "crawler_ats_job_extraction",
		Completed:     17,
		Percent:       100,
		UpdatedAt:     timestampPtr(now.Add(-1 * time.Minute)),
		FinishedAt:    timestampPtr(now.Add(-1 * time.Minute)),
	})
	crawlHub.publish(&models.CrawlProgress{
		RunId:         "run-latest",
		WorkflowRunId: "wf-enrichment",
		WorkflowId:    "enrichment_ats_enrichment",
		IdentityId:    "identity-2",
		Status:        "completed",
		Workflow:      "enrichment_ats_enrichment",
		Completed:     99,
		Percent:       100,
		UpdatedAt:     timestampPtr(now.Add(-30 * time.Second)),
		FinishedAt:    timestampPtr(now.Add(-30 * time.Second)),
	})
	crawlHub.publish(&models.CrawlProgress{
		RunId:      "run-latest",
		IdentityId: "identity-2",
		Status:     "completed",
		Workflow:   "finalizing",
		Percent:    100,
		UpdatedAt:  timestampPtr(now),
		FinishedAt: timestampPtr(now),
	})

	req, _ := http.NewRequest(http.MethodGet, "/api/crawls/last-run/workflow-stats", nil)
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req

	GetLastRunWorkflowStats(c)
	require.Equal(t, http.StatusOK, w.Code)

	var response map[string]interface{}
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &response))
	require.NotNil(t, response["completed_at"])

	workflows, ok := response["workflows"].([]interface{})
	require.True(t, ok)
	require.Len(t, workflows, 4)

	ids := make([]string, 0, len(workflows))
	for _, raw := range workflows {
		workflow, castOK := raw.(map[string]interface{})
		require.True(t, castOK)
		ids = append(ids, workflow["workflow_id"].(string))
	}
	require.Equal(t, []string{
		"crawler_company_discovery",
		"crawler_levelsfyi",
		"crawler_4dayweek",
		"crawler_ats_job_extraction",
	}, ids)
}

func TestGetLastRunWorkflowStats_NoFinalizingSignal(t *testing.T) {
	resetCrawlStateForTests()
	now := time.Now().UTC()

	// Simulate the real dispatcher behaviour: individual crawler_ workflows complete
	// but no "finalizing" signal is ever sent. The dashboard widget must still populate.
	crawlHub.publish(&models.CrawlProgress{
		RunId:         "run-no-finalizing",
		WorkflowRunId: "wf-lvl",
		WorkflowId:    "crawler_levelsfyi",
		IdentityId:    "identity-1",
		Status:        "completed",
		Workflow:      "crawler_levelsfyi",
		Completed:     8,
		Percent:       100,
		UpdatedAt:     timestampPtr(now.Add(-2 * time.Minute)),
		FinishedAt:    timestampPtr(now.Add(-2 * time.Minute)),
	})
	crawlHub.publish(&models.CrawlProgress{
		RunId:         "run-no-finalizing",
		WorkflowRunId: "wf-ats",
		WorkflowId:    "crawler_ats_job_extraction",
		IdentityId:    "identity-1",
		Status:        "completed",
		Workflow:      "crawler_ats_job_extraction",
		Completed:     12,
		Percent:       100,
		UpdatedAt:     timestampPtr(now.Add(-1 * time.Minute)),
		FinishedAt:    timestampPtr(now.Add(-1 * time.Minute)),
	})

	req, _ := http.NewRequest(http.MethodGet, "/api/crawls/last-run/workflow-stats", nil)
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req

	GetLastRunWorkflowStats(c)
	require.Equal(t, http.StatusOK, w.Code)

	var response map[string]interface{}
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &response))
	require.NotNil(t, response["completed_at"])

	workflows, ok := response["workflows"].([]interface{})
	require.True(t, ok)
	require.Len(t, workflows, 2)

	ids := make([]string, 0, len(workflows))
	for _, raw := range workflows {
		workflow, castOK := raw.(map[string]interface{})
		require.True(t, castOK)
		ids = append(ids, workflow["workflow_id"].(string))
	}
	// Stable order per dashboardWorkflowOrder; only the two completed workflows appear.
	require.Equal(t, []string{
		"crawler_levelsfyi",
		"crawler_ats_job_extraction",
	}, ids)
}

func TestGetWorkflowCumulativeJobs_EmptyStateDefaultsToZero(t *testing.T) {
	fakeC := &fakeClient{db: &fakeDatabase{cols: map[string]*fakeCollection{
		"stats": {findOneDoc: nil},
	}}}
	oldMongo := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeC }
	defer func() { GetMongoClient = oldMongo }()

	req, _ := http.NewRequest(http.MethodGet, "/api/crawls/workflow-cumulative-jobs", nil)
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req

	GetWorkflowCumulativeJobs(c)
	require.Equal(t, http.StatusOK, w.Code)

	var response map[string]interface{}
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &response))

	workflows, ok := response["workflows"].([]interface{})
	require.True(t, ok)
	require.Len(t, workflows, 4)

	ids := make([]string, 0, len(workflows))
	for _, raw := range workflows {
		workflow := raw.(map[string]interface{})
		ids = append(ids, workflow["workflow_id"].(string))
		require.Equal(t, float64(0), workflow["discovered_jobs_cumulative"])
	}
	require.Equal(t, []string{
		"crawler_company_discovery",
		"crawler_levelsfyi",
		"crawler_4dayweek",
		"crawler_ats_job_extraction",
	}, ids)
}

func TestGetWorkflowCumulativeJobs_PartialDocumentPreservesStableOrder(t *testing.T) {
	fakeC := &fakeClient{db: &fakeDatabase{cols: map[string]*fakeCollection{
		"stats": {
			findOneDoc: bson.M{
				"_id": "crawler_workflow_cumulative_jobs",
				"discovered_jobs_by_workflow": bson.M{
					"crawler_levelsfyi":          int32(12),
					"crawler_ats_job_extraction": int64(33),
				},
			},
		},
	}}}
	oldMongo := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeC }
	defer func() { GetMongoClient = oldMongo }()

	req, _ := http.NewRequest(http.MethodGet, "/api/crawls/workflow-cumulative-jobs", nil)
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req

	GetWorkflowCumulativeJobs(c)
	require.Equal(t, http.StatusOK, w.Code)

	var response map[string]interface{}
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &response))

	workflows := response["workflows"].([]interface{})
	require.Len(t, workflows, 4)

	byID := map[string]float64{}
	ids := make([]string, 0, len(workflows))
	for _, raw := range workflows {
		workflow := raw.(map[string]interface{})
		id := workflow["workflow_id"].(string)
		ids = append(ids, id)
		byID[id] = workflow["discovered_jobs_cumulative"].(float64)
	}

	require.Equal(t, []string{
		"crawler_company_discovery",
		"crawler_levelsfyi",
		"crawler_4dayweek",
		"crawler_ats_job_extraction",
	}, ids)
	require.Equal(t, float64(0), byID["crawler_company_discovery"])
	require.Equal(t, float64(12), byID["crawler_levelsfyi"])
	require.Equal(t, float64(0), byID["crawler_4dayweek"])
	require.Equal(t, float64(33), byID["crawler_ats_job_extraction"])
}

func TestStreamCrawlProgress_StreamsSSEEvent(t *testing.T) {
	resetCrawlStateForTests()
	req, _ := http.NewRequest(http.MethodGet, "/api/crawls/stream", nil)
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req

	go StreamCrawlProgress(c)
	time.Sleep(50 * time.Millisecond)
	crawlHub.publish(&models.CrawlProgress{
		RunId:          "run-1",
		IdentityId:     "69a41885879f30791d7dfa77",
		Status:         "running",
		Workflow:       "crawler_company_discovery",
		Message:        "Collecting company candidates",
		EstimatedTotal: 100,
		Completed:      15,
		Percent:        15,
		UpdatedAt:      timestampPtr(time.Now().UTC()),
	})
	time.Sleep(50 * time.Millisecond)

	body := w.Body.String()
	require.Contains(t, body, "event: crawl-progress")
	require.Contains(t, body, "data: {")
	require.Contains(t, body, `"run_id":"run-1"`)
	require.Contains(t, body, `"identity_id":"69a41885879f30791d7dfa77"`)
	require.Contains(t, w.Header().Get("Content-Type"), "text/event-stream")
}
