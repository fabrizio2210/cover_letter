package handlers

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"os"

	"github.com/fabrizio2210/cover_letter/src/go/cmd/api/models"

	"github.com/gin-gonic/gin"
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/bson/primitive"
	"go.mongodb.org/mongo-driver/mongo"
)

// GetRecipients fetches all recipients from the database.
func GetRecipients(c *gin.Context) {
	client := GetMongoClient()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		log.Println("Warning: DB_NAME environment variable not set. Using default 'cover_letter'.")
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("recipients")

	// Aggregation pipeline to join with the 'companies' collection
	pipeline := mongo.Pipeline{
		{{Key: "$lookup", Value: bson.D{
			{Key: "from", Value: "companies"},
			{Key: "localField", Value: "company_id"},
			{Key: "foreignField", Value: "_id"},
			{Key: "as", Value: "companyInfo"},
		}}},
		{{Key: "$unwind", Value: bson.D{{Key: "path", Value: "$companyInfo"}, {Key: "preserveNullAndEmptyArrays", Value: true}}}},
	}

	cursor, err := collection.Aggregate(context.Background(), pipeline)
	if err != nil {
		log.Printf("Error aggregating recipients: %v", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to fetch recipients"})
		return
	}
	defer cursor.Close(context.Background())

	var recipients []models.Recipient
	if err = cursor.All(context.Background(), &recipients); err != nil {
		log.Printf("Error decoding recipients: %v", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to decode recipients"})
		return
	}

	if recipients == nil {
		recipients = []models.Recipient{}
	}

	c.JSON(http.StatusOK, recipients)
}

// CreateRecipient creates a new recipient.
func CreateRecipient(c *gin.Context) {
	var req models.Recipient
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}

	recipient := models.Recipient{
		Email:       req.Email,
		Name:        req.Name,
		Description: req.Description,
		CompanyId:   req.CompanyId,
	}

	var insertDoc bson.M
	rawRecipient, err := bson.Marshal(&recipient)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to create recipient"})
		return
	}
	if err := bson.Unmarshal(rawRecipient, &insertDoc); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to create recipient"})
		return
	}

	if recipient.CompanyId != "" {
		companyObjID, err := primitive.ObjectIDFromHex(recipient.CompanyId)
		if err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid company_id"})
			return
		}
		insertDoc["company_id"] = companyObjID
	}

	client := GetMongoClient()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("recipients")

	result, err := collection.InsertOne(context.Background(), insertDoc)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to create recipient"})
		return
	}

	insertedID, ok := result.InsertedID.(primitive.ObjectID)
	if !ok {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to fetch created recipient"})
		return
	}

	pipeline := mongo.Pipeline{
		{{Key: "$match", Value: bson.D{{Key: "_id", Value: insertedID}}}},
		{{Key: "$lookup", Value: bson.D{
			{Key: "from", Value: "companies"},
			{Key: "localField", Value: "company_id"},
			{Key: "foreignField", Value: "_id"},
			{Key: "as", Value: "companyInfo"},
		}}},
		{{Key: "$unwind", Value: bson.D{{Key: "path", Value: "$companyInfo"}, {Key: "preserveNullAndEmptyArrays", Value: true}}}},
	}

	cursor, err := collection.Aggregate(context.Background(), pipeline)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to fetch created recipient"})
		return
	}
	defer cursor.Close(context.Background())

	if cursor.Next(context.Background()) {
		var created models.Recipient
		if err := cursor.Decode(&created); err == nil {
			c.JSON(http.StatusCreated, &created)
			return
		}
	}
	c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to fetch created recipient"})
}

// DeleteRecipient deletes a recipient by its ID.
func DeleteRecipient(c *gin.Context) {
	id := c.Param("id")
	objID, err := primitive.ObjectIDFromHex(id)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid ID"})
		return
	}

	client := GetMongoClient()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("recipients")

	result, err := collection.DeleteOne(context.Background(), bson.M{"_id": objID})
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to delete recipient"})
		return
	}

	if result.DeletedCount == 0 {
		c.JSON(http.StatusNotFound, gin.H{"error": "Recipient not found"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Recipient deleted successfully"})
}

// UpdateRecipientDescription updates the description of a recipient.
func UpdateRecipientDescription(c *gin.Context) {
	id := c.Param("id")
	objID, err := primitive.ObjectIDFromHex(id)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid ID"})
		return
	}

	var req struct {
		Description string `json:"description"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}

	client := GetMongoClient()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("recipients")

	result, err := collection.UpdateOne(
		context.Background(),
		bson.M{"_id": objID},
		bson.M{"$set": bson.M{"description": req.Description}},
	)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to update recipient"})
		return
	}

	if result.ModifiedCount == 0 {
		c.JSON(http.StatusNotFound, gin.H{"error": "Recipient not found or description unchanged"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Recipient description updated successfully"})
}

// UpdateRecipientName updates the name of a recipient.
func UpdateRecipientName(c *gin.Context) {
	id := c.Param("id")
	objID, err := primitive.ObjectIDFromHex(id)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid ID"})
		return
	}

	var req struct {
		Name string `json:"name"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}

	client := GetMongoClient()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("recipients")

	result, err := collection.UpdateOne(
		context.Background(),
		bson.M{"_id": objID},
		bson.M{"$set": bson.M{"name": req.Name}},
	)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to update recipient"})
		return
	}

	if result.ModifiedCount == 0 {
		c.JSON(http.StatusNotFound, gin.H{"error": "Recipient not found or name unchanged"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Recipient name updated successfully"})
}

// GenerateCoverLetterForRecipient triggers the cover letter generation for a recipient.
func GenerateCoverLetterForRecipient(c *gin.Context) {
	id := c.Param("id")
	objID, err := primitive.ObjectIDFromHex(id)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid ID"})
		return
	}

	client := GetMongoClient()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("recipients")

	var recipient models.Recipient
	if err := collection.FindOne(context.Background(), bson.M{"_id": objID}).Decode(&recipient); err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "Recipient not found"})
		return
	}

	queueName := os.Getenv("REDIS_QUEUE_GENERATE_COVER_LETTER_NAME")
	if queueName == "" {
		queueName = "cover_letter_generation_queue"
	}

	payload := map[string]interface{}{
		"recipient": recipient.Email,
	}
	payloadBytes, err := json.Marshal(payload)
	if err != nil {
		log.Printf("Error marshaling payload: %v", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to create payload"})
		return
	}

	if err := rdb.RPush(context.Background(), queueName, payloadBytes).Err(); err != nil {
		log.Printf("Error pushing to queue: %v", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to queue generation"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Generation queued successfully"})
}

// AssociateCompanyWithRecipient associates a company with a recipient.
func AssociateCompanyWithRecipient(c *gin.Context) {
	id := c.Param("id")
	objID, err := primitive.ObjectIDFromHex(id)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid ID"})
		return
	}

	var req struct {
		CompanyID *string `json:"companyId"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}

	client := GetMongoClient()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("recipients")

	var update bson.M
	if req.CompanyID == nil {
		update = bson.M{"$unset": bson.M{"company_id": ""}}
	} else {
		companyObjID, err := primitive.ObjectIDFromHex(*req.CompanyID)
		if err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid Company ID"})
			return
		}
		update = bson.M{"$set": bson.M{"company_id": companyObjID}}
	}

	result, err := collection.UpdateOne(context.Background(), bson.M{"_id": objID}, update)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to associate company"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Company associated successfully", "modifiedCount": result.ModifiedCount})
}

// Removed duplicated field-related handlers (GetFields, CreateField, UpdateField, DeleteField).
// Those handlers are declared and implemented in fields.go to avoid duplication.
