package coverletters

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"os"

	"github.com/fabrizio2210/cover_letter/src/go/cmd/api/db"
	"github.com/fabrizio2210/cover_letter/src/go/cmd/api/models"
	"github.com/gin-gonic/gin"
	"github.com/go-redis/redis/v8"
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/bson/primitive"
	"go.mongodb.org/mongo-driver/mongo"
)

type MongoClientIface interface {
	Database(name string) MongoDatabaseIface
}

type MongoDatabaseIface interface {
	Collection(name string) MongoCollectionIface
}

type MongoCollectionIface interface {
	Aggregate(ctx context.Context, pipeline interface{}) (MongoCursorIface, error)
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

var getMongoClient = func() MongoClientIface {
	return &realMongoClient{client: db.GetDB()}
}

var queuePush = func(ctx context.Context, queueName string, payload []byte) error {
	return defaultRedisClient().RPush(ctx, queueName, payload).Err()
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

// GetCoverLetters fetches all cover letters from the database.
func GetCoverLetters(c *gin.Context) {
	userID, _ := c.Get("userId")
	userIDStr, _ := userID.(string)
	client := getMongoClient()
	dbName := db.GetDatabaseName("cover-letters", userIDStr)
	collection := client.Database(dbName).Collection("cover-letters")

	pipeline := mongo.Pipeline{
		{{Key: "$addFields", Value: bson.D{{Key: "recipientObjId", Value: bson.D{{Key: "$toObjectId", Value: "$recipient_id"}}}}}},
		{{Key: "$lookup", Value: bson.D{{Key: "from", Value: "recipients"}, {Key: "localField", Value: "recipientObjId"}, {Key: "foreignField", Value: "_id"}, {Key: "as", Value: "recipientInfo"}}}},
		{{Key: "$unwind", Value: bson.D{{Key: "path", Value: "$recipientInfo"}, {Key: "preserveNullAndEmptyArrays", Value: true}}}},
	}

	cursor, err := collection.Aggregate(context.Background(), pipeline)
	if err != nil {
		log.Printf("Error aggregating cover letters: %v", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to fetch cover letters"})
		return
	}
	defer cursor.Close(context.Background())

	var coverLetters []models.CoverLetter
	if err = cursor.All(context.Background(), &coverLetters); err != nil {
		log.Printf("Error decoding cover letters: %v", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to decode cover letters"})
		return
	}

	if coverLetters == nil {
		coverLetters = []models.CoverLetter{}
	}

	c.JSON(http.StatusOK, coverLetters)
}

// GetCoverLetter fetches a single cover letter by its ID.
func GetCoverLetter(c *gin.Context) {
	id := c.Param("id")
	objID, err := primitive.ObjectIDFromHex(id)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid ID"})
		return
	}

	userID, _ := c.Get("userId")
	userIDStr, _ := userID.(string)
	client := getMongoClient()
	dbName := db.GetDatabaseName("cover-letters", userIDStr)
	collection := client.Database(dbName).Collection("cover-letters")

	pipeline := mongo.Pipeline{
		{{Key: "$match", Value: bson.D{{Key: "_id", Value: objID}}}},
		{{Key: "$addFields", Value: bson.D{{Key: "recipientObjId", Value: bson.D{{Key: "$toObjectId", Value: "$recipient_id"}}}}}},
		{{Key: "$lookup", Value: bson.D{{Key: "from", Value: "recipients"}, {Key: "localField", Value: "recipientObjId"}, {Key: "foreignField", Value: "_id"}, {Key: "as", Value: "recipientInfo"}}}},
		{{Key: "$unwind", Value: bson.D{{Key: "path", Value: "$recipientInfo"}, {Key: "preserveNullAndEmptyArrays", Value: true}}}},
	}

	cursor, err := collection.Aggregate(context.Background(), pipeline)
	if err != nil {
		log.Printf("Error aggregating cover letters: %v", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to fetch cover letters"})
		return
	}
	defer cursor.Close(context.Background())

	var coverLetter models.CoverLetter
	if cursor.Next(context.Background()) {
		if err = cursor.Decode(&coverLetter); err != nil {
			if err == mongo.ErrNoDocuments {
				c.JSON(http.StatusNotFound, gin.H{"error": "Cover letter not found"})
				return
			}
			log.Printf("Error fetching cover letter: %v", err)
			c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to fetch cover letter"})
			return
		}
	} else {
		c.JSON(http.StatusNotFound, gin.H{"error": "Cover letter not found"})
		return
	}

	c.JSON(http.StatusOK, &coverLetter)
}

// DeleteCoverLetter deletes a cover letter by its ID.
func DeleteCoverLetter(c *gin.Context) {
	id := c.Param("id")
	objID, err := primitive.ObjectIDFromHex(id)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid ID"})
		return
	}

	userID, _ := c.Get("userId")
	userIDStr, _ := userID.(string)
	client := getMongoClient()
	dbName := db.GetDatabaseName("cover-letters", userIDStr)
	collection := client.Database(dbName).Collection("cover-letters")

	result, err := collection.DeleteOne(context.Background(), bson.M{"_id": objID})
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to delete cover letter"})
		return
	}

	if result.DeletedCount == 0 {
		c.JSON(http.StatusNotFound, gin.H{"error": "Cover letter not found"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Cover letter deleted successfully"})
}

// UpdateCoverLetter updates the content of a cover letter.
func UpdateCoverLetter(c *gin.Context) {
	id := c.Param("id")
	objID, err := primitive.ObjectIDFromHex(id)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid ID"})
		return
	}

	var req struct {
		Content string `json:"content"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}

	userID, _ := c.Get("userId")
	userIDStr, _ := userID.(string)
	client := getMongoClient()
	dbName := db.GetDatabaseName("cover-letters", userIDStr)
	collection := client.Database(dbName).Collection("cover-letters")

	result, err := collection.UpdateOne(
		context.Background(),
		bson.M{"_id": objID},
		bson.M{"$set": bson.M{"cover_letter": req.Content}},
	)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to update cover letter"})
		return
	}

	if result.ModifiedCount == 0 {
		c.JSON(http.StatusNotFound, gin.H{"error": "Cover letter not found or content unchanged"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Cover letter updated successfully"})
}

// RefineCoverLetter sends a prompt to refine a cover letter.
func RefineCoverLetter(c *gin.Context) {
	id := c.Param("id")
	objID, err := primitive.ObjectIDFromHex(id)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid ID"})
		return
	}

	var req struct {
		Prompt string `json:"prompt"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}

	userID, _ := c.Get("userId")
	userIDStr, _ := userID.(string)
	client := getMongoClient()
	dbName := db.GetDatabaseName("cover-letters", userIDStr)
	collection := client.Database(dbName).Collection("cover-letters")

	var doc bson.M
	if err := collection.FindOne(context.Background(), bson.M{"_id": objID}).Decode(&doc); err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "Cover letter not found"})
		return
	}

	var recipientEmail string
	if ridStr, ok := doc["recipient_id"].(string); ok {
		recCol := client.Database(dbName).Collection("recipients")
		var recipient bson.M
		if oid, err := primitive.ObjectIDFromHex(ridStr); err == nil {
			if err := recCol.FindOne(context.Background(), bson.M{"_id": oid}).Decode(&recipient); err == nil {
				if em, ok := recipient["email"].(string); ok {
					recipientEmail = em
				}
			}
		} else {
			if err := recCol.FindOne(context.Background(), bson.M{"_id": ridStr}).Decode(&recipient); err == nil {
				if em, ok := recipient["email"].(string); ok {
					recipientEmail = em
				}
			}
		}
	}

	var conversationID string
	if v, ok := doc["conversation_id"].(string); ok {
		conversationID = v
	}

	queueName := os.Getenv("REDIS_QUEUE_GENERATE_COVER_LETTER_NAME")
	if queueName == "" {
		queueName = "cover_letter_generation_queue"
	}

	payload := map[string]interface{}{
		"user_id":         userIDStr,
		"recipient":       recipientEmail,
		"conversation_id": conversationID,
		"prompt":          req.Prompt,
	}
	payloadBytes, err := json.Marshal(payload)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to create payload"})
		return
	}

	if err := queuePush(context.Background(), queueName, payloadBytes); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to queue refinement"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Refinement queued successfully"})
}

// SendCoverLetter sends a cover letter to the email queue.
func SendCoverLetter(c *gin.Context) {
	id := c.Param("id")
	objID, err := primitive.ObjectIDFromHex(id)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid ID"})
		return
	}

	userID, _ := c.Get("userId")
	userIDStr, _ := userID.(string)
	client := getMongoClient()
	dbName := db.GetDatabaseName("cover-letters", userIDStr)
	collection := client.Database(dbName).Collection("cover-letters")

	var doc bson.M
	if err := collection.FindOne(context.Background(), bson.M{"_id": objID}).Decode(&doc); err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "Cover letter not found"})
		return
	}

	var recipientEmail string
	if ridStr, ok := doc["recipient_id"].(string); ok {
		recCol := client.Database(dbName).Collection("recipients")
		var recipient bson.M
		if oid, err := primitive.ObjectIDFromHex(ridStr); err == nil {
			if err := recCol.FindOne(context.Background(), bson.M{"_id": oid}).Decode(&recipient); err == nil {
				if em, ok := recipient["email"].(string); ok {
					recipientEmail = em
				}
			}
		} else {
			if err := recCol.FindOne(context.Background(), bson.M{"_id": ridStr}).Decode(&recipient); err == nil {
				if em, ok := recipient["email"].(string); ok {
					recipientEmail = em
				}
			}
		}
	}

	queueName := os.Getenv("EMAILS_TO_SEND_QUEUE")
	if queueName == "" {
		queueName = "emails_to_send"
	}

	coverLetterText := ""
	if cl, ok := doc["cover_letter"].(string); ok {
		coverLetterText = cl
	}

	payload := map[string]interface{}{
		"user_id":     userIDStr,
		"recipient":    recipientEmail,
		"cover_letter": coverLetterText,
	}
	payloadBytes, err := json.Marshal(payload)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to create payload"})
		return
	}

	if err := queuePush(context.Background(), queueName, payloadBytes); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to queue email"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Email queued successfully"})
}
