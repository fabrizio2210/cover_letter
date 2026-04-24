package handlers

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/fabrizio2210/cover_letter/src/go/cmd/api/models"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestStreamJobUpdates_StreamsSSEEvent(t *testing.T) {
	resetJobUpdateStateForTests()
	req, _ := http.NewRequest(http.MethodGet, "/api/job-descriptions/stream", nil)
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req

	go StreamJobUpdates(c)
	time.Sleep(50 * time.Millisecond)
	jobUpdateHub_.publish(&models.JobUpdateEvent{
		JobId:         "6746ebcee9c2aec88b5d64f7",
		WorkflowId:    "enrichment_retiring_jobs",
		WorkflowRunId: "run-abc123",
	})
	time.Sleep(50 * time.Millisecond)

	body := w.Body.String()
	require.Contains(t, body, "event: job-update")
	require.Contains(t, body, "data: {")
	require.Contains(t, body, `"job_id":"6746ebcee9c2aec88b5d64f7"`)
	require.Contains(t, body, `"workflow_id":"enrichment_retiring_jobs"`)
	require.Contains(t, w.Header().Get("Content-Type"), "text/event-stream")
}
