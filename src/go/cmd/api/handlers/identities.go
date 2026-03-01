package handlers

import (
	"context"
	"log"
	"net/http"
	"os"

	"github.com/fabrizio2210/cover_letter/src/go/cmd/api/models"
	"github.com/gin-gonic/gin"
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/bson/primitive"
)

// CreateIdentity creates a new identity.
func CreateIdentity(c *gin.Context) {
	var identity models.Identity
	if err := c.ShouldBindJSON(&identity); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}

	client := GetMongoClient()
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

	var created bson.M
	if err := collection.FindOne(context.Background(), bson.M{"_id": result.InsertedID}).Decode(&created); err != nil {
		if oid, ok := result.InsertedID.(primitive.ObjectID); ok {
			c.JSON(http.StatusCreated, gin.H{"_id": oid.Hex()})
			return
		}
		c.JSON(http.StatusCreated, gin.H{"insertedId": result.InsertedID})
		return
	}
	if idVal, ok := created["_id"].(primitive.ObjectID); ok {
		created["_id"] = idVal.Hex()
	}
	c.JSON(http.StatusCreated, created)
}

// GetIdentities fetches all identities from the database.
func GetIdentities(c *gin.Context) {
	client := GetMongoClient()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		log.Println("Warning: DB_NAME environment variable not set. Using default 'cover_letter'.")
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("identities")

	pipeline := bson.A{
		bson.D{{"$lookup", bson.D{{"from", "fields"}, {"localField", "field"}, {"foreignField", "_id"}, {"as", "fieldInfo"}}}},
		bson.D{{"$unwind", bson.D{{"path", "$fieldInfo"}, {"preserveNullAndEmptyArrays", true}}}},
	}

	cursor, err := collection.Aggregate(context.Background(), pipeline)
	if err != nil {
		log.Printf("Error aggregating identities: %v", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to fetch identities"})
		return
	}
	defer cursor.Close(context.Background())

	var docs []bson.M
	for cursor.Next(context.Background()) {
		var d bson.M
		if err := cursor.Decode(&d); err != nil {
			log.Printf("Error decoding identity document: %v", err)
			c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to decode identities"})
			return
		}
		if idVal, ok := d["_id"].(primitive.ObjectID); ok {
			d["_id"] = idVal.Hex()
		}
		docs = append(docs, d)
	}
	if docs == nil {
		docs = []bson.M{}
	}
	c.JSON(http.StatusOK, docs)
}

// DeleteIdentity deletes an identity by its ID.
func DeleteIdentity(c *gin.Context) {
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

// UpdateIdentityGeneric updates identity using arbitrary update map.
func UpdateIdentityGeneric(c *gin.Context, update bson.M) {
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
	collection := client.Database(dbName).Collection("identities")

	result, err := collection.UpdateOne(context.Background(), bson.M{"_id": objID}, bson.M{"$set": update})
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to update identity"})
		return
	}
	if result.MatchedCount == 0 {
		c.JSON(http.StatusNotFound, gin.H{"error": "Identity not found"})
		return
	}
	if result.ModifiedCount == 0 {
		c.JSON(http.StatusOK, gin.H{"message": "Identity found; no changes made"})
		return
	}
	c.JSON(http.StatusOK, gin.H{"message": "Identity updated successfully"})
}

// UpdateIdentityDescription wrapper
func UpdateIdentityDescription(c *gin.Context) {
	var req struct {
		Description string `json:"description"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}
	UpdateIdentityGeneric(c, bson.M{"description": req.Description})
}

// UpdateIdentityName wrapper
func UpdateIdentityName(c *gin.Context) {
	var req struct {
		Name string `json:"name"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}
	UpdateIdentityGeneric(c, bson.M{"name": req.Name})
}

// UpdateIdentitySignature wrapper
func UpdateIdentitySignature(c *gin.Context) {
	var req struct {
		HtmlSignature string `json:"html_signature"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}
	if len(req.HtmlSignature) > 64*1024 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Signature too large"})
		return
	}
	UpdateIdentityGeneric(c, bson.M{"html_signature": req.HtmlSignature})
}

// AssociateFieldWithIdentity associates a field with an identity.
func AssociateFieldWithIdentity(c *gin.Context) {
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
	UpdateIdentityGeneric(c, bson.M{"field": fieldObjID})
}
