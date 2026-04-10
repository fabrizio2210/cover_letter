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

	identityID := "69a41885879f30791d7dfa77"
	crawlHub.publish(&models.CrawlProgress{
		RunId:          "run-1",
		IdentityId:     identityID,
		Status:         "running",
		Phase:          "workflow1_company_discovery",
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

func TestGetActiveCrawls_FiltersByIdentity(t *testing.T) {
	resetCrawlStateForTests()
	crawlHub.publish(&models.CrawlProgress{RunId: "run-1", IdentityId: "identity-a", Status: "queued", Phase: "queued", EstimatedTotal: 100, UpdatedAt: timestampPtr(time.Now().UTC())})
	crawlHub.publish(&models.CrawlProgress{RunId: "run-2", IdentityId: "identity-b", Status: "completed", Phase: "finalizing", EstimatedTotal: 100, Percent: 100, UpdatedAt: timestampPtr(time.Now().UTC())})

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
		Phase:          "workflow1_company_discovery",
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