package handlers

import (
	"context"
	"coverletter/db"
	"coverletter/models"
	"log"
	"net/http"
	"os"

	"github.com/gin-gonic/gin"
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/bson/primitive"
	"go.mongodb.org/mongo-driver/mongo"
)

// CreateIdentity creates a new identity.
func CreateIdentity(c *gin.Context) {
	var identity models.Identity
	if err := c.ShouldBindJSON(&identity); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}

	client := db.GetDB()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("identities")

	result, err := collection.InsertOne(context.Background(), identity)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to create identity"})
		return
	}

	c.JSON(http.StatusCreated, result)
}

// GetIdentities fetches all identities from the database.
func GetIdentities(c *gin.Context) {
	client := db.GetDB()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("identities")

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
		log.Printf("Error aggregating identities: %v", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to fetch identities"})
		return
	}
	defer cursor.Close(context.Background())

	var identities []models.Identity
	if err = cursor.All(context.Background(), &identities); err != nil {
		log.Printf("Error decoding identities: %v", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to decode identities"})
		return
	}

	if identities == nil {
		identities = []models.Identity{}
	}

	c.JSON(http.StatusOK, identities)
}

// DeleteIdentity deletes an identity by its ID.
func DeleteIdentity(c *gin.Context) {
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
	collection := client.Database(dbName).Collection("identities")

	result, err := collection.DeleteOne(context.Background(), bson.M{"_id": objID})
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to delete identity"})
		return
	}

	if result.DeletedCount == 0 {
		c.JSON(http.StatusNotFound, gin.H{"error": "Identity not found"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Identity deleted successfully"})
}

// UpdateIdentityDescription updates the description of an identity.
func UpdateIdentityDescription(c *gin.Context) {
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
	collection := client.Database(dbName).Collection("identities")

	result, err := collection.UpdateOne(
		context.Background(),
		bson.M{"_id": objID},
		bson.M{"$set": bson.M{"description": req.Description}},
	)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to update identity"})
		return
	}

	if result.ModifiedCount == 0 {
		c.JSON(http.StatusNotFound, gin.H{"error": "Identity not found or description unchanged"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Identity description updated successfully"})
}

// UpdateIdentityName updates the name of an identity.
func UpdateIdentityName(c *gin.Context) {
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
	collection := client.Database(dbName).Collection("identities")

	result, err := collection.UpdateOne(
		context.Background(),
		bson.M{"_id": objID},
		bson.M{"$set": bson.M{"name": req.Name}},
	)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to update identity"})
		return
	}

	if result.ModifiedCount == 0 {
		c.JSON(http.StatusNotFound, gin.H{"error": "Identity not found or name unchanged"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Identity name updated successfully"})
}

// UpdateIdentitySignature updates the HTML signature of an identity.
func UpdateIdentitySignature(c *gin.Context) {
	id := c.Param("id")
	objID, err := primitive.ObjectIDFromHex(id)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid ID"})
		return
	}

	var req struct {
		HtmlSignature string `json:"html_signature"`
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
	collection := client.Database(dbName).Collection("identities")

	// Basic safeguard: limit signature size to reasonable length (e.g., 64KB)
	if len(req.HtmlSignature) > 64*1024 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Signature too large"})
		return
	}

	result, err := collection.UpdateOne(
		context.Background(),
		bson.M{"_id": objID},
		bson.M{"$set": bson.M{"html_signature": req.HtmlSignature}},
	)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to update identity template"})
		return
	}

	if result.ModifiedCount == 0 {
		c.JSON(http.StatusNotFound, gin.H{"error": "Identity not found or template unchanged"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Identity template updated successfully"})
}

// AssociateFieldWithIdentity associates a field with an identity.
func AssociateFieldWithIdentity(c *gin.Context) {
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
	collection := client.Database(dbName).Collection("identities")

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
		c.JSON(http.StatusNotFound, gin.H{"error": "Identity not found or field unchanged"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Field associated successfully"})
}
