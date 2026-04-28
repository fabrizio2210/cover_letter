package jobs

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/fabrizio2210/cover_letter/src/go/cmd/api/db"
	"github.com/fabrizio2210/cover_letter/src/go/cmd/api/models"
	"github.com/gin-gonic/gin"
	"github.com/go-redis/redis/v8"
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/bson/primitive"
	"go.mongodb.org/mongo-driver/mongo"
	"google.golang.org/protobuf/proto"
)

const defaultJobUpdateChannel = "job_update_channel"

type MongoClientIface interface {
	Database(name string) MongoDatabaseIface
}

type MongoDatabaseIface interface {
	Collection(name string) MongoCollectionIface
}

type MongoCollectionIface interface {
	Aggregate(ctx context.Context, pipeline interface{}) (MongoCursorIface, error)
	InsertOne(ctx context.Context, doc interface{}) (*mongo.InsertOneResult, error)
	FindOne(ctx context.Context, filter interface{}) MongoSingleResultIface
	UpdateOne(ctx context.Context, filter interface{}, update interface{}) (*mongo.UpdateResult, error)
	DeleteOne(ctx context.Context, filter interface{}) (*mongo.DeleteResult, error)
}

type MongoCursorIface interface {
	All(ctx context.Context, result interface{}) error
	Next(ctx context.Context) bool
	Decode(v interface{}) error
	Close(ctx context.Context) error
}

type MongoSingleResultIface interface {
	Decode(v interface{}) error
}

type jobUpdateSubscriber chan *models.JobUpdateEvent

type jobUpdateHub struct {
	mu          sync.RWMutex
	subscribers map[int]jobUpdateSubscriber
	nextID      int
	bridgeOnce  sync.Once
}

var jobUpdateHub_ = &jobUpdateHub{subscribers: make(map[int]jobUpdateSubscriber)}

var getMongoClient = func() MongoClientIface {
	return &realMongoClient{client: db.GetDB()}
}

var queuePush = func(ctx context.Context, queueName string, payload []byte) error {
	return defaultRedisClient().RPush(ctx, queueName, payload).Err()
}

var subscribeChannel = func(ctx context.Context, channelName string) (<-chan *redis.Message, func() error) {
	pubsub := defaultRedisClient().Subscribe(ctx, channelName)
	return pubsub.Channel(), pubsub.Close
}

// SetMongoClientProvider allows wrappers/tests to inject custom clients.
func SetMongoClientProvider(provider func() MongoClientIface) {
	if provider == nil {
		return
	}
	getMongoClient = provider
}

// SetQueuePushProvider allows wrappers/tests to inject queue behavior.
func SetQueuePushProvider(provider func(ctx context.Context, queueName string, payload []byte) error) {
	if provider == nil {
		return
	}
	queuePush = provider
}

// SetSubscribeChannelProvider allows wrappers/tests to inject pub/sub behavior.
func SetSubscribeChannelProvider(provider func(ctx context.Context, channelName string) (<-chan *redis.Message, func() error)) {
	if provider == nil {
		return
	}
	subscribeChannel = provider
}

func defaultRedisClient() *redis.Client {
	redisHost := os.Getenv("REDIS_HOST")
	if redisHost == "" {
		redisHost = "localhost"
	}
	redisPort := os.Getenv("REDIS_PORT")
	if redisPort == "" {
		redisPort = "6379"
	}
	return redis.NewClient(&redis.Options{Addr: redisHost + ":" + redisPort})
}

type realMongoClient struct{ client *mongo.Client }

func (r *realMongoClient) Database(name string) MongoDatabaseIface {
	return &realMongoDatabase{db: r.client.Database(name)}
}

type realMongoDatabase struct{ db *mongo.Database }

func (r *realMongoDatabase) Collection(name string) MongoCollectionIface {
	return &realMongoCollection{col: r.db.Collection(name)}
}

type realMongoCollection struct{ col *mongo.Collection }

func (r *realMongoCollection) Aggregate(ctx context.Context, pipeline interface{}) (MongoCursorIface, error) {
	cur, err := r.col.Aggregate(ctx, pipeline)
	if err != nil {
		return nil, err
	}
	return &realMongoCursor{cur: cur}, nil
}

func (r *realMongoCollection) InsertOne(ctx context.Context, doc interface{}) (*mongo.InsertOneResult, error) {
	return r.col.InsertOne(ctx, doc)
}

func (r *realMongoCollection) FindOne(ctx context.Context, filter interface{}) MongoSingleResultIface {
	return r.col.FindOne(ctx, filter)
}

func (r *realMongoCollection) UpdateOne(ctx context.Context, filter interface{}, update interface{}) (*mongo.UpdateResult, error) {
	return r.col.UpdateOne(ctx, filter, update)
}

func (r *realMongoCollection) DeleteOne(ctx context.Context, filter interface{}) (*mongo.DeleteResult, error) {
	return r.col.DeleteOne(ctx, filter)
}

type realMongoCursor struct{ cur *mongo.Cursor }

func (r *realMongoCursor) All(ctx context.Context, result interface{}) error {
	return r.cur.All(ctx, result)
}
func (r *realMongoCursor) Next(ctx context.Context) bool   { return r.cur.Next(ctx) }
func (r *realMongoCursor) Decode(v interface{}) error      { return r.cur.Decode(v) }
func (r *realMongoCursor) Close(ctx context.Context) error { return r.cur.Close(ctx) }

type timestampObject struct {
	Seconds int64 `bson:"seconds" json:"seconds"`
	Nanos   int32 `bson:"nanos" json:"nanos"`
}

func nowTimestampObject() timestampObject {
	now := time.Now().UTC()
	return timestampObject{Seconds: now.Unix(), Nanos: int32(now.Nanosecond())}
}

func normalizeObjectIDValue(value interface{}) (string, bool) {
	switch v := value.(type) {
	case primitive.ObjectID:
		return v.Hex(), true
	case string:
		return v, true
	default:
		return "", false
	}
}

func normalizeFieldInfoMap(raw interface{}) (bson.M, bool) {
	fieldInfo, ok := raw.(bson.M)
	if !ok {
		return nil, false
	}
	if id, ok := normalizeObjectIDValue(fieldInfo["_id"]); ok {
		fieldInfo["id"] = id
		delete(fieldInfo, "_id")
	}
	return fieldInfo, true
}

func normalizeCompanyInfoMap(raw interface{}) (bson.M, bool) {
	companyInfo, ok := raw.(bson.M)
	if !ok {
		return nil, false
	}

	if id, ok := normalizeObjectIDValue(companyInfo["_id"]); ok {
		companyInfo["id"] = id
		delete(companyInfo, "_id")
	}
	if fieldID, ok := normalizeObjectIDValue(companyInfo["field_id"]); ok {
		companyInfo["field_id"] = fieldID
	}
	if fieldInfo, ok := normalizeFieldInfoMap(companyInfo["fieldInfo"]); ok {
		companyInfo["field_info"] = fieldInfo
		delete(companyInfo, "fieldInfo")
	}
	return companyInfo, true
}

func normalizeScoreDoc(score bson.M) {
	if id, ok := normalizeObjectIDValue(score["_id"]); ok {
		score["id"] = id
		delete(score, "_id")
	}
	if jobID, ok := normalizeObjectIDValue(score["job_id"]); ok {
		score["job_id"] = jobID
	}
	if identityID, ok := normalizeObjectIDValue(score["identity_id"]); ok {
		score["identity_id"] = identityID
	}

	var rawPreferenceScores []interface{}
	switch v := score["preference_scores"].(type) {
	case bson.A:
		rawPreferenceScores = v
	case []interface{}:
		rawPreferenceScores = v
	default:
		return
	}

	preferenceScores := make([]bson.M, 0, len(rawPreferenceScores))
	for _, raw := range rawPreferenceScores {
		pref, ok := raw.(bson.M)
		if !ok {
			continue
		}
		preferenceScores = append(preferenceScores, pref)
	}

	score["preference_scores"] = preferenceScores
}

func normalizeJobDoc(doc bson.M) bson.M {
	if id, ok := normalizeObjectIDValue(doc["_id"]); ok {
		doc["id"] = id
		delete(doc, "_id")
	}

	if companyID, ok := normalizeObjectIDValue(doc["company_id"]); ok {
		doc["company_id"] = companyID
	}

	if companyInfo, ok := normalizeCompanyInfoMap(doc["companyInfo"]); ok {
		doc["company_info"] = companyInfo
		delete(doc, "companyInfo")
	}

	return doc
}

func jobPreferenceScoresCollection(c *gin.Context) (MongoCollectionIface, string) {
	userID, _ := c.Get("userId")
	userIDStr, _ := userID.(string)
	client := getMongoClient()
	dbName := db.GetDatabaseName("job-preference-scores", userIDStr)
	return client.Database(dbName).Collection("job-preference-scores"), dbName
}

func loadNormalizedScoreDocs(scoreCollection MongoCollectionIface, match bson.M) ([]bson.M, error) {
	pipeline := bson.A{}
	if len(match) > 0 {
		pipeline = append(pipeline, bson.M{"$match": match})
	}

	cursor, err := scoreCollection.Aggregate(context.Background(), pipeline)
	if err != nil {
		return nil, err
	}
	defer cursor.Close(context.Background())

	scores := []bson.M{}
	for cursor.Next(context.Background()) {
		var score bson.M
		if err := cursor.Decode(&score); err != nil {
			return nil, err
		}
		normalizeScoreDoc(score)
		scores = append(scores, score)
	}

	if scores == nil {
		scores = []bson.M{}
	}

	return scores, nil
}

// GetJobPreferenceScores fetches score documents, optionally filtered by job_id and identity_id.
func GetJobPreferenceScores(c *gin.Context) {
	jobID := strings.TrimSpace(c.Query("job_id"))
	identityID := strings.TrimSpace(c.Query("identity_id"))

	match := bson.M{}
	if jobID != "" {
		if _, err := primitive.ObjectIDFromHex(jobID); err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid job_id"})
			return
		}
		match["job_id"] = jobID
	}
	if identityID != "" {
		if _, err := primitive.ObjectIDFromHex(identityID); err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid identity_id"})
			return
		}
		match["identity_id"] = identityID
	}

	scoreCollection, _ := jobPreferenceScoresCollection(c)
	scores, err := loadNormalizedScoreDocs(scoreCollection, match)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to fetch job preference scores"})
		return
	}

	c.JSON(http.StatusOK, scores)
}

func collectionHasDocuments(collection MongoCollectionIface) bool {
	cursor, err := collection.Aggregate(context.Background(), bson.A{bson.M{"$limit": 1}})
	if err != nil {
		return false
	}
	defer cursor.Close(context.Background())
	return cursor.Next(context.Background())
}

func jobDescriptionsCollection() (MongoCollectionIface, MongoClientIface, string) {
	client := getMongoClient()
	dbName := db.GetDatabaseName("job-descriptions", "")
	jobDescriptions := client.Database(dbName).Collection("job-descriptions")
	legacyJobs := client.Database(dbName).Collection("jobs")

	if collectionHasDocuments(jobDescriptions) {
		return jobDescriptions, client, dbName
	}
	if collectionHasDocuments(legacyJobs) {
		return legacyJobs, client, dbName
	}

	return jobDescriptions, client, dbName
}

// GetJobDescriptions fetches all job descriptions and enriches them with company info.
func GetJobDescriptions(c *gin.Context) {
	collection, _, _ := jobDescriptionsCollection()

	pipeline := bson.A{
		bson.M{"$lookup": bson.M{"from": "companies", "localField": "company_id", "foreignField": "_id", "as": "companyInfo"}},
		bson.M{"$unwind": bson.M{"path": "$companyInfo", "preserveNullAndEmptyArrays": true}},
	}

	cursor, err := collection.Aggregate(context.Background(), pipeline)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to fetch job descriptions"})
		return
	}
	defer cursor.Close(context.Background())

	jobs := []bson.M{}
	for cursor.Next(context.Background()) {
		var doc bson.M
		if err := cursor.Decode(&doc); err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to decode job descriptions"})
			return
		}
		jobs = append(jobs, normalizeJobDoc(doc))
	}

	if jobs == nil {
		jobs = []bson.M{}
	}

	c.JSON(http.StatusOK, jobs)
}

// GetJobDescription fetches one job description by ID.
func GetJobDescription(c *gin.Context) {
	id := c.Param("id")
	objID, err := primitive.ObjectIDFromHex(id)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid ID"})
		return
	}

	collection, _, _ := jobDescriptionsCollection()
	pipeline := bson.A{
		bson.M{"$match": bson.M{"_id": objID}},
		bson.M{"$lookup": bson.M{"from": "companies", "localField": "company_id", "foreignField": "_id", "as": "companyInfo"}},
		bson.M{"$unwind": bson.M{"path": "$companyInfo", "preserveNullAndEmptyArrays": true}},
	}

	cursor, err := collection.Aggregate(context.Background(), pipeline)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to fetch job description"})
		return
	}
	defer cursor.Close(context.Background())

	if !cursor.Next(context.Background()) {
		c.JSON(http.StatusNotFound, gin.H{"error": "Job description not found"})
		return
	}

	var doc bson.M
	if err := cursor.Decode(&doc); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to decode job description"})
		return
	}

	c.JSON(http.StatusOK, normalizeJobDoc(doc))
}

type createJobRequest struct {
	CompanyID     string `json:"company_id"`
	CompanyName   string `json:"company_name"`
	Title         string `json:"title"`
	Description   string `json:"description"`
	Location      string `json:"location"`
	Platform      string `json:"platform"`
	ExternalJobID string `json:"external_job_id"`
	SourceURL     string `json:"source_url"`
}

func resolveOrCreateCompanyID(client MongoClientIface, dbName string, companyID, companyName string) (*primitive.ObjectID, error) {
	if companyID != "" {
		parsed, err := primitive.ObjectIDFromHex(companyID)
		if err != nil {
			return nil, err
		}
		return &parsed, nil
	}

	if strings.TrimSpace(companyName) == "" {
		return nil, nil
	}

	companies := client.Database(dbName).Collection("companies")
	var existing bson.M
	err := companies.FindOne(context.Background(), bson.M{"name": companyName}).Decode(&existing)
	if err == nil {
		if id, ok := existing["_id"].(primitive.ObjectID); ok {
			return &id, nil
		}
	}

	insertDoc := bson.M{"name": companyName, "description": ""}
	insertRes, insertErr := companies.InsertOne(context.Background(), insertDoc)
	if insertErr != nil {
		return nil, insertErr
	}

	insertedID, ok := insertRes.InsertedID.(primitive.ObjectID)
	if !ok {
		return nil, nil
	}

	return &insertedID, nil
}

// CreateJobDescription creates a new job description.
func CreateJobDescription(c *gin.Context) {
	var req createJobRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}
	if strings.TrimSpace(req.Title) == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Title is required"})
		return
	}

	collection, client, dbName := jobDescriptionsCollection()
	companyObjID, err := resolveOrCreateCompanyID(client, dbName, req.CompanyID, req.CompanyName)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid company_id"})
		return
	}

	now := nowTimestampObject()
	insertDoc := bson.M{
		"title":           req.Title,
		"description":     req.Description,
		"location":        req.Location,
		"platform":        req.Platform,
		"external_job_id": req.ExternalJobID,
		"source_url":      req.SourceURL,
		"created_at":      now,
		"updated_at":      now,
	}
	if companyObjID != nil {
		insertDoc["company_id"] = *companyObjID
	}

	result, err := collection.InsertOne(context.Background(), insertDoc)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to create job description"})
		return
	}

	insertedID, ok := result.InsertedID.(primitive.ObjectID)
	if !ok {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to fetch created job description"})
		return
	}

	pipeline := bson.A{
		bson.M{"$match": bson.M{"_id": insertedID}},
		bson.M{"$lookup": bson.M{"from": "companies", "localField": "company_id", "foreignField": "_id", "as": "companyInfo"}},
		bson.M{"$unwind": bson.M{"path": "$companyInfo", "preserveNullAndEmptyArrays": true}},
	}
	cursor, err := collection.Aggregate(context.Background(), pipeline)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to fetch created job description"})
		return
	}
	defer cursor.Close(context.Background())

	if !cursor.Next(context.Background()) {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to fetch created job description"})
		return
	}

	var doc bson.M
	if err := cursor.Decode(&doc); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to decode created job description"})
		return
	}

	c.JSON(http.StatusCreated, normalizeJobDoc(doc))
}

type updateJobRequest struct {
	CompanyID     string `json:"company_id"`
	Title         string `json:"title"`
	Description   string `json:"description"`
	Location      string `json:"location"`
	Platform      string `json:"platform"`
	ExternalJobID string `json:"external_job_id"`
	SourceURL     string `json:"source_url"`
}

// UpdateJobDescription updates an existing job description.
func UpdateJobDescription(c *gin.Context) {
	id := c.Param("id")
	objID, err := primitive.ObjectIDFromHex(id)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid ID"})
		return
	}

	var req updateJobRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}

	collection, _, _ := jobDescriptionsCollection()
	updateSet := bson.M{
		"title":           req.Title,
		"description":     req.Description,
		"location":        req.Location,
		"platform":        req.Platform,
		"external_job_id": req.ExternalJobID,
		"source_url":      req.SourceURL,
		"updated_at":      nowTimestampObject(),
	}

	if req.CompanyID != "" {
		companyObjID, err := primitive.ObjectIDFromHex(req.CompanyID)
		if err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid company_id"})
			return
		}
		updateSet["company_id"] = companyObjID
	}

	result, err := collection.UpdateOne(context.Background(), bson.M{"_id": objID}, bson.M{"$set": updateSet})
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to update job description"})
		return
	}
	if result.MatchedCount == 0 {
		c.JSON(http.StatusNotFound, gin.H{"error": "Job description not found"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Job description updated successfully"})
}

// DeleteJobDescription deletes a job description.
func DeleteJobDescription(c *gin.Context) {
	id := c.Param("id")
	objID, err := primitive.ObjectIDFromHex(id)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid ID"})
		return
	}

	collection, _, _ := jobDescriptionsCollection()
	result, err := collection.DeleteOne(context.Background(), bson.M{"_id": objID})
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to delete job description"})
		return
	}
	if result.DeletedCount == 0 {
		c.JSON(http.StatusNotFound, gin.H{"error": "Job description not found"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Job description deleted successfully"})
}

// CheckJobDescription enqueues a job for the enrichment_retiring_jobs workflow.
func CheckJobDescription(c *gin.Context) {
	id := c.Param("id")
	objID, err := primitive.ObjectIDFromHex(id)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid ID"})
		return
	}

	collection, _, _ := jobDescriptionsCollection()
	var jobDoc bson.M
	if err := collection.FindOne(context.Background(), bson.M{"_id": objID}).Decode(&jobDoc); err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "Job description not found"})
		return
	}

	queueName := os.Getenv("CRAWLER_ENRICHMENT_RETIRING_JOBS_QUEUE_NAME")
	if queueName == "" {
		queueName = "enrichment_retiring_jobs_queue"
	}

	payloadBytes, err := json.Marshal(map[string]string{"job_id": id})
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to create payload"})
		return
	}

	if err := queuePush(context.Background(), queueName, payloadBytes); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to queue check"})
		return
	}

	c.JSON(http.StatusAccepted, gin.H{"message": "Check queued successfully"})
}

// ScoreJobDescription enqueues a job for scoring.
func ScoreJobDescription(c *gin.Context) {
	id := c.Param("id")
	objID, err := primitive.ObjectIDFromHex(id)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid ID"})
		return
	}

	collection, _, _ := jobDescriptionsCollection()
	var jobDoc bson.M
	if err := collection.FindOne(context.Background(), bson.M{"_id": objID}).Decode(&jobDoc); err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "Job description not found"})
		return
	}

	queueName := os.Getenv("JOB_SCORING_QUEUE_NAME")
	if queueName == "" {
		queueName = "job_scoring_queue"
	}

	payloadBytes, err := json.Marshal(map[string]string{"job_id": id})
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to create payload"})
		return
	}

	if err := queuePush(context.Background(), queueName, payloadBytes); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to queue scoring"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Scoring queued successfully"})
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

				channel, closeFn := subscribeChannel(context.Background(), channelName)
				for message := range channel {
					var event models.JobUpdateEvent
					if err := json.Unmarshal([]byte(message.Payload), &event); err != nil {
						log.Printf("failed to decode job update event: %v", err)
						continue
					}
					jobUpdateHub_.publish(&event)
				}

				if closeFn != nil {
					if err := closeFn(); err != nil && err != redis.Nil {
						log.Printf("failed to close job update subscription: %v", err)
					}
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

// PublishJobUpdateForTests injects a synthetic event into the in-memory hub.
func PublishJobUpdateForTests(event *models.JobUpdateEvent) {
	jobUpdateHub_.publish(event)
}

// ResetJobUpdateStateForTests clears subscribers and state for deterministic tests.
func ResetJobUpdateStateForTests() {
	jobUpdateHub_.mu.Lock()
	defer jobUpdateHub_.mu.Unlock()
	for id, subscriber := range jobUpdateHub_.subscribers {
		delete(jobUpdateHub_.subscribers, id)
		close(subscriber)
	}
	jobUpdateHub_.nextID = 0
}
