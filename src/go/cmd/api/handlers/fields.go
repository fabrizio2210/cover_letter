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
	"go.mongodb.org/mongo-driver/mongo"
)

// CreateField creates a new field.
func CreateField(c *gin.Context) {
	var field models.Field
	if err := c.ShouldBindJSON(&field); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}

	client := GetMongoClient()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("fields")

	result, err := collection.InsertOne(context.Background(), field)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to create field"})
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

// GetFields fetches all fields.
func GetFields(c *gin.Context) {
	client := GetMongoClient()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		log.Println("Warning: DB_NAME environment variable not set. Using default 'cover_letter'.")
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("fields")

	cursor, err := collection.Aggregate(context.Background(), mongo.Pipeline{})
	if err != nil {
		log.Printf("Error aggregating fields: %v", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to fetch fields"})
		return
	}
	defer cursor.Close(context.Background())

	var docs []bson.M
	for cursor.Next(context.Background()) {
		var d bson.M
		if err := cursor.Decode(&d); err != nil {
			log.Printf("Error decoding field document: %v", err)
			c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to decode fields"})
			return
		}
		// normalize _id → id
		if idVal, ok := d["_id"].(primitive.ObjectID); ok {
			d["id"] = idVal.Hex()
		} else if idStr, ok := d["_id"].(string); ok {
			d["id"] = idStr
		}
		delete(d, "_id")
		docs = append(docs, d)
	}
	if docs == nil {
		docs = []bson.M{}
	}
	c.JSON(http.StatusOK, docs)
}

// DeleteField deletes a field by id.
func DeleteField(c *gin.Context) {
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

// UpdateField updates existing field.
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

	client := GetMongoClient()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("fields")

	result, err := collection.UpdateOne(context.Background(), bson.M{"_id": objID}, bson.M{"$set": bson.M{"field": req.Field}})
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
