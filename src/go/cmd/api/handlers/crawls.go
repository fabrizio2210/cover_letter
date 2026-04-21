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
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/bson/primitive"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/timestamppb"
)

const (
	defaultCrawlerTriggerQueue    = "crawler_trigger_queue"
	defaultCrawlerProgressChannel = "crawler_progress_channel"
	defaultScoringProgressChannel = "scoring_progress_channel"
)

type crawlSubscriber chan *models.CrawlProgress
type scoringSubscriber chan *models.ScoringProgress

type crawlProgressHub struct {
	mu          sync.RWMutex
	snapshots   map[string]*models.CrawlProgress
	subscribers map[int]crawlSubscriber
	nextID      int
	bridgeOnce  sync.Once
}

type scoringProgressHub struct {
	mu          sync.RWMutex
	snapshots   map[string]*models.ScoringProgress
	subscribers map[int]scoringSubscriber
	nextID      int
	bridgeOnce  sync.Once
}

var crawlHub = &crawlProgressHub{
	snapshots:   make(map[string]*models.CrawlProgress),
	subscribers: make(map[int]crawlSubscriber),
}

var scoringHub = &scoringProgressHub{
	snapshots:   make(map[string]*models.ScoringProgress),
	subscribers: make(map[int]scoringSubscriber),
}

func TriggerCrawl(c *gin.Context) {
	ensureCrawlProgressBridge()

	var req models.CrawlTriggerRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}

	identityID := req.IdentityId
	identityOID, err := primitive.ObjectIDFromHex(identityID)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid identity_id"})
		return
	}

	{
		dbName := os.Getenv("DB_NAME")
		if dbName == "" {
			dbName = "cover_letter"
		}
		collection := GetMongoClient().Database(dbName).Collection("identities")
		var identity bson.M
		if err := collection.FindOne(context.Background(), bson.M{"_id": identityOID}).Decode(&identity); err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": "Identity not found"})
			return
		}
		roles, _ := identity["roles"].(bson.A)
		if len(roles) == 0 {
			c.JSON(http.StatusBadRequest, gin.H{"error": "Identity has no roles configured; crawl refused"})
			return
		}
	}

	if active, ok := crawlHub.findActiveByIdentity(identityID); ok {
		c.JSON(http.StatusConflict, gin.H{
			"error":       "A crawl is already running for this identity",
			"run_id":      active.RunId,
			"identity_id": active.IdentityId,
			"status":      active.Status,
		})
		return
	}

	now := time.Now().UTC()
	runID := primitive.NewObjectID().Hex()
	queuedSnapshot := &models.CrawlProgress{
		RunId:          runID,
		IdentityId:     identityID,
		Status:         "queued",
		Workflow:       "queued",
		Message:        "Waiting for worker pickup",
		EstimatedTotal: 4,
		Completed:      0,
		Percent:        0,
		UpdatedAt:      timestampPtr(now),
		StartedAt:      nil,
		FinishedAt:     nil,
		Reason:         "",
	}

	payload := &models.CrawlTriggerQueuePayload{
		RunId:       runID,
		IdentityId:  identityID,
		RequestedAt: timestamppb.New(now),
	}

	payloadBytes, err := json.Marshal(payload)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to queue crawl"})
		return
	}

	queueName := os.Getenv("CRAWLER_TRIGGER_QUEUE_NAME")
	if queueName == "" {
		queueName = defaultCrawlerTriggerQueue
	}

	if err := rdb.RPush(context.Background(), queueName, payloadBytes).Err(); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to queue crawl"})
		return
	}

	crawlHub.publish(queuedSnapshot)
	c.JSON(http.StatusAccepted, gin.H{
		"message":     "Crawl queued successfully",
		"run_id":      runID,
		"identity_id": identityID,
		"status":      "queued",
	})
}

func GetActiveCrawls(c *gin.Context) {
	ensureCrawlProgressBridge()
	identityID := c.Query("identity_id")
	c.JSON(http.StatusOK, crawlHub.listSnapshots(identityID))
}

func StreamCrawlProgress(c *gin.Context) {
	ensureCrawlProgressBridge()

	c.Writer.Header().Set("Content-Type", "text/event-stream")
	c.Writer.Header().Set("Cache-Control", "no-cache")
	c.Writer.Header().Set("Connection", "keep-alive")
	c.Writer.Header().Set("X-Accel-Buffering", "no")

	subscriberID, subscriber := crawlHub.subscribe()
	defer crawlHub.unsubscribe(subscriberID)

	ctx := c.Request.Context()
	for {
		select {
		case <-ctx.Done():
			return
		case snapshot, ok := <-subscriber:
			if !ok {
				return
			}
			payload, err := json.Marshal(snapshot)
			if err != nil {
				continue
			}
			if _, err := c.Writer.Write([]byte("event: crawl-progress\n")); err != nil {
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

func GetActiveScoring(c *gin.Context) {
	ensureScoringProgressBridge()
	identityID := c.Query("identity_id")
	c.JSON(http.StatusOK, scoringHub.listSnapshots(identityID))
}

func StreamScoringProgress(c *gin.Context) {
	ensureScoringProgressBridge()

	c.Writer.Header().Set("Content-Type", "text/event-stream")
	c.Writer.Header().Set("Cache-Control", "no-cache")
	c.Writer.Header().Set("Connection", "keep-alive")
	c.Writer.Header().Set("X-Accel-Buffering", "no")

	subscriberID, subscriber := scoringHub.subscribe()
	defer scoringHub.unsubscribe(subscriberID)

	ctx := c.Request.Context()
	for {
		select {
		case <-ctx.Done():
			return
		case snapshot, ok := <-subscriber:
			if !ok {
				return
			}
			payload, err := json.Marshal(snapshot)
			if err != nil {
				continue
			}
			if _, err := c.Writer.Write([]byte("event: scoring-progress\n")); err != nil {
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

func ensureCrawlProgressBridge() {
	crawlHub.bridgeOnce.Do(func() {
		go func() {
			for {
				channelName := os.Getenv("CRAWLER_PROGRESS_CHANNEL_NAME")
				if channelName == "" {
					channelName = defaultCrawlerProgressChannel
				}

				pubsub := rdb.Subscribe(context.Background(), channelName)
				channel := pubsub.Channel()
				for message := range channel {
					var snapshot models.CrawlProgress
					if err := json.Unmarshal([]byte(message.Payload), &snapshot); err != nil {
						log.Printf("failed to decode crawl progress event: %v", err)
						continue
					}
					crawlHub.publish(&snapshot)
				}

				if err := pubsub.Close(); err != nil && err != redis.Nil {
					log.Printf("failed to close crawl progress subscription: %v", err)
				}
				time.Sleep(500 * time.Millisecond)
			}
		}()
	})
}

func ensureScoringProgressBridge() {
	scoringHub.bridgeOnce.Do(func() {
		go func() {
			for {
				channelName := os.Getenv("SCORING_PROGRESS_CHANNEL_NAME")
				if channelName == "" {
					channelName = defaultScoringProgressChannel
				}

				pubsub := rdb.Subscribe(context.Background(), channelName)
				channel := pubsub.Channel()
				for message := range channel {
					var snapshot models.ScoringProgress
					if err := json.Unmarshal([]byte(message.Payload), &snapshot); err != nil {
						log.Printf("failed to decode scoring progress event: %v", err)
						continue
					}
					scoringHub.publish(&snapshot)
				}

				if err := pubsub.Close(); err != nil && err != redis.Nil {
					log.Printf("failed to close scoring progress subscription: %v", err)
				}
				time.Sleep(500 * time.Millisecond)
			}
		}()
	})
}

func (h *crawlProgressHub) publish(snapshot *models.CrawlProgress) {
	normalized := normalizeCrawlProgress(cloneCrawlProgress(snapshot))

	h.mu.Lock()
	h.snapshots[normalized.RunId] = normalized
	subscribers := make([]crawlSubscriber, 0, len(h.subscribers))
	for _, subscriber := range h.subscribers {
		subscribers = append(subscribers, subscriber)
	}
	h.mu.Unlock()

	for _, subscriber := range subscribers {
		broadcast := cloneCrawlProgress(normalized)
		select {
		case subscriber <- broadcast:
		default:
		}
	}
}

func (h *crawlProgressHub) subscribe() (int, crawlSubscriber) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.nextID++
	id := h.nextID
	channel := make(crawlSubscriber, 16)
	h.subscribers[id] = channel
	return id, channel
}

func (h *scoringProgressHub) publish(snapshot *models.ScoringProgress) {
	normalized := normalizeScoringProgress(cloneScoringProgress(snapshot))

	h.mu.Lock()
	h.snapshots[normalized.RunId] = normalized
	subscribers := make([]scoringSubscriber, 0, len(h.subscribers))
	for _, subscriber := range h.subscribers {
		subscribers = append(subscribers, subscriber)
	}
	h.mu.Unlock()

	for _, subscriber := range subscribers {
		broadcast := cloneScoringProgress(normalized)
		select {
		case subscriber <- broadcast:
		default:
		}
	}
}

func (h *scoringProgressHub) subscribe() (int, scoringSubscriber) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.nextID++
	id := h.nextID
	channel := make(scoringSubscriber, 16)
	h.subscribers[id] = channel
	return id, channel
}

func (h *scoringProgressHub) unsubscribe(id int) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if subscriber, ok := h.subscribers[id]; ok {
		delete(h.subscribers, id)
		close(subscriber)
	}
}

func (h *scoringProgressHub) listSnapshots(identityID string) []*models.ScoringProgress {
	h.mu.RLock()
	defer h.mu.RUnlock()
	result := make([]*models.ScoringProgress, 0, len(h.snapshots))
	for _, snapshot := range h.snapshots {
		if identityID != "" && snapshot.IdentityId != identityID {
			continue
		}
		result = append(result, cloneScoringProgress(snapshot))
	}
	return result
}

func (h *crawlProgressHub) unsubscribe(id int) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if subscriber, ok := h.subscribers[id]; ok {
		delete(h.subscribers, id)
		close(subscriber)
	}
}

func (h *crawlProgressHub) listSnapshots(identityID string) []*models.CrawlProgress {
	h.mu.RLock()
	defer h.mu.RUnlock()
	result := make([]*models.CrawlProgress, 0, len(h.snapshots))
	for _, snapshot := range h.snapshots {
		if identityID != "" && snapshot.IdentityId != identityID {
			continue
		}
		result = append(result, cloneCrawlProgress(snapshot))
	}
	return result
}

func (h *crawlProgressHub) findActiveByIdentity(identityID string) (*models.CrawlProgress, bool) {
	h.mu.RLock()
	defer h.mu.RUnlock()
	for _, snapshot := range h.snapshots {
		if snapshot.IdentityId != identityID {
			continue
		}
		if snapshot.Status == "queued" || snapshot.Status == "running" {
			return cloneCrawlProgress(snapshot), true
		}
	}
	return nil, false
}

func normalizeCrawlProgress(snapshot *models.CrawlProgress) *models.CrawlProgress {
	if snapshot.Percent < 0 {
		snapshot.Percent = 0
	}
	if snapshot.Percent > 100 {
		snapshot.Percent = 100
	}
	if snapshot.UpdatedAt == nil {
		snapshot.UpdatedAt = timestampPtr(time.Now().UTC())
	}
	if snapshot.Status == "running" && snapshot.StartedAt == nil {
		snapshot.StartedAt = snapshot.UpdatedAt
	}
	if (snapshot.Status == "completed" || snapshot.Status == "failed" || snapshot.Status == "rejected") && snapshot.FinishedAt == nil {
		snapshot.FinishedAt = snapshot.UpdatedAt
	}
	return snapshot
}

func cloneCrawlProgress(snapshot *models.CrawlProgress) *models.CrawlProgress {
	if snapshot == nil {
		return nil
	}
	clone, ok := proto.Clone(snapshot).(*models.CrawlProgress)
	if !ok {
		return &models.CrawlProgress{}
	}
	return clone
}

func normalizeScoringProgress(snapshot *models.ScoringProgress) *models.ScoringProgress {
	if snapshot.Percent < 0 {
		snapshot.Percent = 0
	}
	if snapshot.Percent > 100 {
		snapshot.Percent = 100
	}
	if snapshot.UpdatedAt == nil {
		snapshot.UpdatedAt = timestampPtr(time.Now().UTC())
	}
	if snapshot.Status == "running" && snapshot.StartedAt == nil {
		snapshot.StartedAt = snapshot.UpdatedAt
	}
	if (snapshot.Status == "completed" || snapshot.Status == "failed") && snapshot.FinishedAt == nil {
		snapshot.FinishedAt = snapshot.UpdatedAt
	}
	return snapshot
}

func cloneScoringProgress(snapshot *models.ScoringProgress) *models.ScoringProgress {
	if snapshot == nil {
		return nil
	}
	clone, ok := proto.Clone(snapshot).(*models.ScoringProgress)
	if !ok {
		return &models.ScoringProgress{}
	}
	return clone
}

func timestampPtr(now time.Time) *timestamppb.Timestamp {
	return timestamppb.New(now)
}

func resetCrawlStateForTests() {
	crawlHub.mu.Lock()
	defer crawlHub.mu.Unlock()
	crawlHub.snapshots = make(map[string]*models.CrawlProgress)
	for id, subscriber := range crawlHub.subscribers {
		delete(crawlHub.subscribers, id)
		close(subscriber)
	}
	crawlHub.nextID = 0
}
