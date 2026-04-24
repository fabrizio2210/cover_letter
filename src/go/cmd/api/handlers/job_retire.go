package handlers

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"sync"
	"time"

	"github.com/fabrizio2210/cover_letter/src/go/cmd/api/models"
	"github.com/gin-gonic/gin"
	"github.com/go-redis/redis/v8"
	"go.mongodb.org/mongo-driver/bson/primitive"
	"google.golang.org/protobuf/types/known/timestamppb"
)

const (
	defaultEnrichmentRetiringJobsQueue      = "enrichment_retiring_jobs_queue"
	defaultJobRetireNotificationChannel     = "job_retire_notification_channel"
)

type jobRetireSubscriber chan *models.JobRetireNotification

type jobRetireNotificationHub struct {
	mu          sync.RWMutex
	subscribers map[int]jobRetireSubscriber
	nextID      int
	bridgeOnce  sync.Once
}

var jobRetireHub = &jobRetireNotificationHub{
	subscribers: make(map[int]jobRetireSubscriber),
}

// EnqueueJobRetireCheck pushes a single job onto the enrichment_retiring_jobs
// queue so the worker can probe its source URL and retire it if necessary.
//
// POST /api/job-descriptions/:id/retire-check
func EnqueueJobRetireCheck(c *gin.Context) {
	ensureJobRetireBridge()

	id := c.Param("id")
	if _, err := primitive.ObjectIDFromHex(id); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid job ID"})
		return
	}

	var req struct {
		IdentityId string `json:"identity_id"`
	}
	// Ignore binding errors — identity_id is optional.
	_ = c.ShouldBindJSON(&req)

	runID := primitive.NewObjectID().Hex()
	workflowRunID := primitive.NewObjectID().Hex()
	now := time.Now().UTC()

	payload := map[string]interface{}{
		"run_id":          runID,
		"workflow_run_id": workflowRunID,
		"workflow_id":     "enrichment_retiring_jobs",
		"identity_id":     req.IdentityId,
		"job_id":          id,
		"emitted_at":      map[string]int64{"seconds": now.Unix(), "nanos": 0},
	}

	payloadBytes, err := json.Marshal(payload)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to create payload"})
		return
	}

	queueName := os.Getenv("CRAWLER_ENRICHMENT_RETIRING_JOBS_QUEUE_NAME")
	if queueName == "" {
		queueName = defaultEnrichmentRetiringJobsQueue
	}

	if err := rdb.RPush(context.Background(), queueName, payloadBytes).Err(); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to queue retire check"})
		return
	}

	c.JSON(http.StatusAccepted, gin.H{"message": "Retire check queued", "job_id": id})
}

// StreamJobRetireNotifications opens a server-sent events stream that forwards
// job_retire_notification_channel messages to the browser.
//
// GET /api/job-retire-notifications/stream
func StreamJobRetireNotifications(c *gin.Context) {
	ensureJobRetireBridge()

	c.Writer.Header().Set("Content-Type", "text/event-stream")
	c.Writer.Header().Set("Cache-Control", "no-cache")
	c.Writer.Header().Set("Connection", "keep-alive")
	c.Writer.Header().Set("X-Accel-Buffering", "no")

	subscriberID, subscriber := jobRetireHub.subscribe()
	defer jobRetireHub.unsubscribe(subscriberID)

	ctx := c.Request.Context()
	for {
		select {
		case <-ctx.Done():
			return
		case notification, ok := <-subscriber:
			if !ok {
				return
			}
			payload, err := json.Marshal(notification)
			if err != nil {
				continue
			}
			if _, err := c.Writer.Write([]byte("event: job-retire\n")); err != nil {
				return
			}
			if _, err := c.Writer.Write([]byte("data: ")); err != nil {
				return
			}
			if _, err := c.Writer.Write(payload); err != nil {
				return
			}
			if _, err := c.Writer.Write([]byte("\n\n")); err != nil {
				return
			}
			c.Writer.Flush()
		}
	}
}

func ensureJobRetireBridge() {
	jobRetireHub.bridgeOnce.Do(func() {
		go func() {
			for {
				channelName := os.Getenv("JOB_RETIRE_NOTIFICATION_CHANNEL_NAME")
				if channelName == "" {
					channelName = defaultJobRetireNotificationChannel
				}

				pubsub := rdb.Subscribe(context.Background(), channelName)
				channel := pubsub.Channel()
				for message := range channel {
					var notification models.JobRetireNotification
					if err := json.Unmarshal([]byte(message.Payload), &notification); err != nil {
						log.Printf("failed to decode job retire notification: %v", err)
						continue
					}
					jobRetireHub.publish(&notification)
				}

				if err := pubsub.Close(); err != nil && err != redis.Nil {
					log.Printf("failed to close job retire notification subscription: %v", err)
				}
				time.Sleep(500 * time.Millisecond)
			}
		}()
	})
}

func (h *jobRetireNotificationHub) publish(notification *models.JobRetireNotification) {
	if notification.EmittedAt == nil {
		notification.EmittedAt = timestamppb.New(time.Now().UTC())
	}

	h.mu.Lock()
	subscribers := make([]jobRetireSubscriber, 0, len(h.subscribers))
	for _, subscriber := range h.subscribers {
		subscribers = append(subscribers, subscriber)
	}
	h.mu.Unlock()

	for _, subscriber := range subscribers {
		clone := &models.JobRetireNotification{
			JobId:     notification.JobId,
			IsOpen:    notification.IsOpen,
			Deleted:   notification.Deleted,
			EmittedAt: notification.EmittedAt,
		}
		select {
		case subscriber <- clone:
		default:
			log.Printf("job retire notification dropped for job %s: subscriber channel full", notification.JobId)
		}
	}
}

func (h *jobRetireNotificationHub) subscribe() (int, jobRetireSubscriber) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.nextID++
	id := h.nextID
	ch := make(jobRetireSubscriber, 16)
	h.subscribers[id] = ch
	return id, ch
}

func (h *jobRetireNotificationHub) unsubscribe(id int) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if subscriber, ok := h.subscribers[id]; ok {
		delete(h.subscribers, id)
		close(subscriber)
	}
}

func resetJobRetireStateForTests() {
	jobRetireHub.mu.Lock()
	defer jobRetireHub.mu.Unlock()
	for id, subscriber := range jobRetireHub.subscribers {
		delete(jobRetireHub.subscribers, id)
		close(subscriber)
	}
	jobRetireHub.nextID = 0
}
