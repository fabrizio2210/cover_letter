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
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/bson/primitive"
	"go.mongodb.org/mongo-driver/mongo"
)

// GetRecipients fetches all recipients from the database.
func GetRecipients(c *gin.Context) {
	client := db.GetDB()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		log.Println("Warning: DB_NAME environment variable not set. Using default 'cover_letter'.")
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("recipients")

	// Aggregation pipeline to join with the 'fields' collection
	pipeline := mongo.Pipeline{
		{{"$lookup", bson.D{
			{"from", "fields"},
			{"localField", "field"},
			{"foreignField", "_id"},
			{"as", "fieldInfo"},
		}}},
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
	var recipient models.Recipient
	if err := c.ShouldBindJSON(&recipient); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}

	client := db.GetDB()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("recipients")

	result, err := collection.InsertOne(context.Background(), recipient)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to create recipient"})
		return
	}

	// Fetch the created recipient so we can return the document with its _id as hex string
	var created bson.M
	if err := collection.FindOne(context.Background(), bson.M{"_id": result.InsertedID}).Decode(&created); err != nil {
		// If fetching fails, still return InsertedID as hex if possible
		if oid, ok := result.InsertedID.(primitive.ObjectID); ok {
			c.JSON(http.StatusCreated, gin.H{"_id": oid.Hex()})
			return
		}
		c.JSON(http.StatusCreated, gin.H{"insertedId": result.InsertedID})
		return
	}

	// convert _id to hex string
	if idVal, ok := created["_id"].(primitive.ObjectID); ok {
		created["_id"] = idVal.Hex()
	}
	c.JSON(http.StatusCreated, created)
}

// DeleteRecipient deletes a recipient by its ID.
func DeleteRecipient(c *gin.Context) {
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

	client := db.GetDB()
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

	client := db.GetDB()
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

// AssociateFieldWithRecipient associates a field with a recipient.
func AssociateFieldWithRecipient(c *gin.Context) {
	id := c.Param("id")
	objID, err := primitive.ObjectIDFromHex(id)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid ID"})
		return
	}

	var req struct {
		FieldID string `json:"fieldId"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}

	fieldObjID, err := primitive.ObjectIDFromHex(req.FieldID)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid Field ID"})
		return
	}

	client := db.GetDB()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("recipients")

	result, err := collection.UpdateOne(
		context.Background(),
		bson.M{"_id": objID},
		bson.M{"$set": bson.M{"field": fieldObjID}},
	)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to associate field"})
		return
	}

	if result.ModifiedCount == 0 {
		c.JSON(http.StatusNotFound, gin.H{"error": "Recipient not found or field unchanged"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Field associated successfully"})
}

// GenerateCoverLetterForRecipient triggers the cover letter generation for a recipient.
func GenerateCoverLetterForRecipient(c *gin.Context) {
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
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to create payload"})
		return
	}

	if err := rdb.RPush(context.Background(), queueName, payloadBytes).Err(); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to queue generation"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Generation queued successfully"})
}

// GetFields returns all fields.
func GetFields(c *gin.Context) {
	client := db.GetDB()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("fields")

	cursor, err := collection.Find(context.Background(), bson.D{})
	if err != nil {
		log.Printf("Error finding fields: %v", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to fetch fields"})
		return
	}
	defer cursor.Close(context.Background())

	var rawFields []bson.M
	if err := cursor.All(context.Background(), &rawFields); err != nil {
		log.Printf("Error decoding fields: %v", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to decode fields"})
		return
	}

	// convert each _id to hex string for frontend simplicity
	out := make([]map[string]interface{}, 0, len(rawFields))
	for _, f := range rawFields {
		m := make(map[string]interface{})
		if idVal, ok := f["_id"].(primitive.ObjectID); ok {
			m["_id"] = idVal.Hex()
		} else {
			m["_id"] = f["_id"]
		}
		if fieldName, ok := f["field"]; ok {
			m["field"] = fieldName
		} else {
			m["field"] = ""
		}
		out = append(out, m)
	}

	c.JSON(http.StatusOK, out)
}

// CreateField creates a new field document.
func CreateField(c *gin.Context) {
	var req struct {
		Field string `json:"field"`
	}
	if err := c.ShouldBindJSON(&req); err != nil || req.Field == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}

	client := db.GetDB()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("fields")

	res, err := collection.InsertOne(context.Background(), bson.M{"field": req.Field})
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to create field"})
		return
	}

	// Return the created document with _id as hex string
	var created bson.M
	if err := collection.FindOne(context.Background(), bson.M{"_id": res.InsertedID}).Decode(&created); err != nil {
		if oid, ok := res.InsertedID.(primitive.ObjectID); ok {
			c.JSON(http.StatusCreated, gin.H{"_id": oid.Hex(), "field": req.Field})
			return
		}
		c.JSON(http.StatusCreated, gin.H{"insertedId": res.InsertedID})
		return
	}

	if idVal, ok := created["_id"].(primitive.ObjectID); ok {
		created["_id"] = idVal.Hex()
	}
	c.JSON(http.StatusCreated, created)
}

// UpdateField updates an existing field document.
func UpdateField(c *gin.Context) {
	id := c.Param("id")
	objID, err := primitive.ObjectIDFromHex(id)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid ID"})
		return
	}

	var req struct {
		Field string `json:"field"`
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
	collection := client.Database(dbName).Collection("fields")

	result, err := collection.UpdateOne(
		context.Background(),
		bson.M{"_id": objID},
		bson.M{"$set": bson.M{"field": req.Field}},
	)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to update field"})
		return
	}

	if result.ModifiedCount == 0 {
		c.JSON(http.StatusNotFound, gin.H{"error": "Field not found or value unchanged"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Field updated successfully"})
}

// DeleteField deletes a field document.
func DeleteField(c *gin.Context) {
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
	collection := client.Database(dbName).Collection("fields")

	result, err := collection.DeleteOne(context.Background(), bson.M{"_id": objID})
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to delete field"})
		return
	}

	if result.DeletedCount == 0 {
		c.JSON(http.StatusNotFound, gin.H{"error": "Field not found"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Field deleted successfully"})
}
