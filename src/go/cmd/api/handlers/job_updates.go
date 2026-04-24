package handlers

import (
	"context"
	"encoding/json"
	"log"
	"os"
	"sync"
	"time"

	"github.com/fabrizio2210/cover_letter/src/go/cmd/api/models"
	"github.com/gin-gonic/gin"
	"github.com/go-redis/redis/v8"
	"google.golang.org/protobuf/proto"
)

const defaultJobUpdateChannel = "job_update_channel"

type jobUpdateSubscriber chan *models.JobUpdateEvent

type jobUpdateHub struct {
	mu          sync.RWMutex
	subscribers map[int]jobUpdateSubscriber
	nextID      int
	bridgeOnce  sync.Once
}

var jobUpdateHub_ = &jobUpdateHub{
	subscribers: make(map[int]jobUpdateSubscriber),
}

// StreamJobUpdates streams job update events as server-sent events to the client.
func StreamJobUpdates(c *gin.Context) {
	ensureJobUpdateBridge()

	c.Writer.Header().Set("Content-Type", "text/event-stream")
	c.Writer.Header().Set("Cache-Control", "no-cache")
	c.Writer.Header().Set("Connection", "keep-alive")
	c.Writer.Header().Set("X-Accel-Buffering", "no")

	subscriberID, subscriber := jobUpdateHub_.subscribe()
	defer jobUpdateHub_.unsubscribe(subscriberID)

	ctx := c.Request.Context()
	for {
		select {
		case <-ctx.Done():
			return
		case event, ok := <-subscriber:
			if !ok {
				return
			}
			payload, err := json.Marshal(event)
			if err != nil {
				continue
			}
			if _, err := c.Writer.Write([]byte("event: job-update\n")); err != nil {
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

func ensureJobUpdateBridge() {
	jobUpdateHub_.bridgeOnce.Do(func() {
		go func() {
			for {
				channelName := os.Getenv("JOB_UPDATE_CHANNEL_NAME")
				if channelName == "" {
					channelName = defaultJobUpdateChannel
				}

				pubsub := rdb.Subscribe(context.Background(), channelName)
				channel := pubsub.Channel()
				for message := range channel {
					var event models.JobUpdateEvent
					if err := json.Unmarshal([]byte(message.Payload), &event); err != nil {
						log.Printf("failed to decode job update event: %v", err)
						continue
					}
					jobUpdateHub_.publish(&event)
				}

				if err := pubsub.Close(); err != nil && err != redis.Nil {
					log.Printf("failed to close job update subscription: %v", err)
				}
				time.Sleep(500 * time.Millisecond)
			}
		}()
	})
}

func (h *jobUpdateHub) publish(event *models.JobUpdateEvent) {
	cloned := cloneJobUpdateEvent(event)

	h.mu.RLock()
	subscribers := make([]jobUpdateSubscriber, 0, len(h.subscribers))
	for _, subscriber := range h.subscribers {
		subscribers = append(subscribers, subscriber)
	}
	h.mu.RUnlock()

	for _, subscriber := range subscribers {
		broadcast := cloneJobUpdateEvent(cloned)
		select {
		case subscriber <- broadcast:
		default:
		}
	}
}

func (h *jobUpdateHub) subscribe() (int, jobUpdateSubscriber) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.nextID++
	id := h.nextID
	channel := make(jobUpdateSubscriber, 16)
	h.subscribers[id] = channel
	return id, channel
}

func (h *jobUpdateHub) unsubscribe(id int) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if subscriber, ok := h.subscribers[id]; ok {
		delete(h.subscribers, id)
		close(subscriber)
	}
}

func cloneJobUpdateEvent(event *models.JobUpdateEvent) *models.JobUpdateEvent {
	if event == nil {
		return nil
	}
	cloned, ok := proto.Clone(event).(*models.JobUpdateEvent)
	if !ok {
		return &models.JobUpdateEvent{}
	}
	return cloned
}

func resetJobUpdateStateForTests() {
	jobUpdateHub_.mu.Lock()
	defer jobUpdateHub_.mu.Unlock()
	for id, subscriber := range jobUpdateHub_.subscribers {
		delete(jobUpdateHub_.subscribers, id)
		close(subscriber)
	}
	jobUpdateHub_.nextID = 0
}
