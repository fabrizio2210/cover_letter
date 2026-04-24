package handlers

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/bson/primitive"
)

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

func jobPreferenceScoresCollection() (MongoCollectionIface, string) {
	client := GetMongoClient()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
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

	scoreCollection, _ := jobPreferenceScoresCollection()
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
	client := GetMongoClient()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
	jobDescriptions := client.Database(dbName).Collection("job-descriptions")
	legacyJobs := client.Database(dbName).Collection("jobs")

	if collectionHasDocuments(jobDescriptions) {
		return jobDescriptions, client, dbName
	}
	if collectionHasDocuments(legacyJobs) {
		return legacyJobs, client, dbName
	}

	// Keep canonical collection as default write target when neither has data yet.
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
	if _, err := primitive.ObjectIDFromHex(id); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid ID"})
		return
	}

	collection, _, _ := jobDescriptionsCollection()
	var jobDoc bson.M
	if err := collection.FindOne(context.Background(), bson.M{"_id": mustObjectID(id)}).Decode(&jobDoc); err != nil {
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

	if err := rdb.RPush(context.Background(), queueName, payloadBytes).Err(); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to queue check"})
		return
	}

	c.JSON(http.StatusAccepted, gin.H{"message": "Check queued successfully"})
}

func mustObjectID(hex string) primitive.ObjectID {
	oid, _ := primitive.ObjectIDFromHex(hex)
	return oid
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

	if err := rdb.RPush(context.Background(), queueName, payloadBytes).Err(); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to queue scoring"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Scoring queued successfully"})
}
