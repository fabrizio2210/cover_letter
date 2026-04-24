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
	"go.mongodb.org/mongo-driver/bson/primitive"
)

func TestEnqueueJobRetireCheck_InvalidID(t *testing.T) {
	resetJobRetireStateForTests()

	body := bytes.NewBufferString(`{}`)
	req, _ := http.NewRequest(http.MethodPost, "/api/job-descriptions/invalid/retire-check", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req
	c.Params = append(c.Params, gin.Param{Key: "id", Value: "invalid"})

	EnqueueJobRetireCheck(c)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

func TestEnqueueJobRetireCheck_QueuesPayload(t *testing.T) {
	resetJobRetireStateForTests()

	m, err := miniredis.Run()
	require.NoError(t, err)
	defer m.Close()

	rclient := redis.NewClient(&redis.Options{Addr: m.Addr()})
	SetRedisClientForTests(rclient)
	t.Setenv("CRAWLER_ENRICHMENT_RETIRING_JOBS_QUEUE_NAME", "test_retire_queue")

	jobID := primitive.NewObjectID().Hex()
	identityID := primitive.NewObjectID().Hex()

	body := bytes.NewBufferString(`{"identity_id":"` + identityID + `"}`)
	req, _ := http.NewRequest(http.MethodPost, "/api/job-descriptions/"+jobID+"/retire-check", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req
	c.Params = append(c.Params, gin.Param{Key: "id", Value: jobID})

	EnqueueJobRetireCheck(c)
	require.Equal(t, http.StatusAccepted, w.Code)

	queueValues, err := rclient.LRange(context.Background(), "test_retire_queue", 0, -1).Result()
	require.NoError(t, err)
	require.Len(t, queueValues, 1)

	var payload map[string]interface{}
	require.NoError(t, json.Unmarshal([]byte(queueValues[0]), &payload))
	require.Equal(t, jobID, payload["job_id"])
	require.Equal(t, identityID, payload["identity_id"])
	require.Equal(t, "enrichment_retiring_jobs", payload["workflow_id"])
	require.NotEmpty(t, payload["run_id"])
	require.NotEmpty(t, payload["workflow_run_id"])

	var resp map[string]interface{}
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &resp))
	require.Equal(t, jobID, resp["job_id"])
}

func TestStreamJobRetireNotifications_StreamsSSEEvent(t *testing.T) {
	resetJobRetireStateForTests()

	req, _ := http.NewRequest(http.MethodGet, "/api/job-retire-notifications/stream", nil)
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req

	go StreamJobRetireNotifications(c)
	time.Sleep(50 * time.Millisecond)

	jobRetireHub.publish(&models.JobRetireNotification{
		JobId:   "abc123",
		IsOpen:  false,
		Deleted: false,
	})
	time.Sleep(50 * time.Millisecond)

	body := w.Body.String()
	require.Contains(t, body, "event: job-retire")
	require.Contains(t, body, "data: {")
	require.Contains(t, body, `"job_id":"abc123"`)
	require.Contains(t, w.Header().Get("Content-Type"), "text/event-stream")
}
