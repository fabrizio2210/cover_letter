package handlers

import (
	"context"
	"coverletter/db"
	"coverletter/models"
	"encoding/json"
	"log"
	"net/http"
	"os"

	"github.com/gin-gonic/gin"
	"github.com/go-redis/redis/v8"
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/bson/primitive"
	"go.mongodb.org/mongo-driver/mongo"
)

var rdb *redis.Client

func init() {
	redisHost := os.Getenv("REDIS_HOST")
	if redisHost == "" {
		redisHost = "localhost"
	}
	redisPort := os.Getenv("REDIS_PORT")
	if redisPort == "" {
		redisPort = "6379"
	}
	rdb = redis.NewClient(&redis.Options{
		Addr: redisHost + ":" + redisPort,
	})
}

// GetCoverLetters fetches all cover letters from the database.
func GetCoverLetters(c *gin.Context) {
	client := db.GetDB()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("cover-letters")

	pipeline := mongo.Pipeline{
		{{"$lookup", bson.D{
			{"from", "recipients"},
			{"localField", "recipient_id"},
			{"foreignField", "_id"},
			{"as", "recipientInfo"},
		}}},
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

	client := db.GetDB()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("cover-letters")

	var coverLetter models.CoverLetter
	if err := collection.FindOne(context.Background(), bson.M{"_id": objID}).Decode(&coverLetter); err != nil {
		if err == mongo.ErrNoDocuments {
			c.JSON(http.StatusNotFound, gin.H{"error": "Cover letter not found"})
			return
		}
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to fetch cover letter"})
		return
	}

	c.JSON(http.StatusOK, coverLetter)
}

// DeleteCoverLetter deletes a cover letter by its ID.
func DeleteCoverLetter(c *gin.Context) {
	id := c.Param("id")
	objID, err := primitive.ObjectIDFromHex(id)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid ID"})
		return
	}

	client := db.GetDB()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
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

	client := db.GetDB()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
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

	client := db.GetDB()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("cover-letters")

	var coverLetter models.CoverLetter
	if err := collection.FindOne(context.Background(), bson.M{"_id": objID}).Decode(&coverLetter); err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "Cover letter not found"})
		return
	}

	queueName := os.Getenv("REDIS_QUEUE_GENERATE_COVER_LETTER_NAME")
	if queueName == "" {
		queueName = "cover_letter_generation_queue"
	}

	payload := map[string]interface{}{
		"recipient":       coverLetter.RecipientInfo[0].Email,
		"conversation_id": coverLetter.ConversationID,
		"prompt":          req.Prompt,
	}
	payloadBytes, err := json.Marshal(payload)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to create payload"})
		return
	}

	if err := rdb.RPush(context.Background(), queueName, payloadBytes).Err(); err != nil {
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

	client := db.GetDB()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("cover-letters")

	var coverLetter models.CoverLetter
	if err := collection.FindOne(context.Background(), bson.M{"_id": objID}).Decode(&coverLetter); err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "Cover letter not found"})
		return
	}

	queueName := os.Getenv("EMAILS_TO_SEND_QUEUE")
	if queueName == "" {
		queueName = "emails_to_send"
	}

	payload := map[string]interface{}{
		"recipient":    coverLetter.RecipientInfo[0].Email,
		"cover_letter": coverLetter.CoverLetter,
	}
	payloadBytes, err := json.Marshal(payload)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to create payload"})
		return
	}

	if err := rdb.RPush(context.Background(), queueName, payloadBytes).Err(); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to queue email"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Email queued successfully"})
}
