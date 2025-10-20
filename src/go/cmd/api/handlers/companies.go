package handlers

import (
	"context"
	"coverletter/db"
	"log"
	"net/http"
	"os"

	"github.com/gin-gonic/gin"
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/bson/primitive"
	"go.mongodb.org/mongo-driver/mongo"
)

// GetCompanies fetches all companies with their field info.
func GetCompanies(c *gin.Context) {
	client := db.GetDB()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("companies")

	pipeline := mongo.Pipeline{
		{{"$lookup", bson.D{
			{"from", "fields"},
			{"localField", "field"},
			{"foreignField", "_id"},
			{"as", "fieldInfo"},
		}}},
		{{"$unwind", bson.D{
			{"path", "$fieldInfo"},
			{"preserveNullAndEmptyArrays", true},
		}}},
	}

	cursor, err := collection.Aggregate(context.Background(), pipeline)
	if err != nil {
		log.Printf("Error aggregating companies: %v", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to fetch companies"})
		return
	}
	defer cursor.Close(context.Background())

	var companies []bson.M
	if err = cursor.All(context.Background(), &companies); err != nil {
		log.Printf("Error decoding companies: %v", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to decode companies"})
		return
	}

	// Convert _id and fieldInfo._id to hex strings for frontend
	for _, company := range companies {
		if id, ok := company["_id"].(primitive.ObjectID); ok {
			company["id"] = id.Hex()
			delete(company, "_id")
		}
		if fieldInfo, ok := company["fieldInfo"].(bson.M); ok {
			if fid, ok := fieldInfo["_id"].(primitive.ObjectID); ok {
				fieldInfo["id"] = fid.Hex()
				delete(fieldInfo, "_id")
			}
			company["field"] = fieldInfo
			delete(company, "fieldInfo")
		} else {
			company["field"] = nil
			delete(company, "fieldInfo")
		}
	}

	c.JSON(http.StatusOK, companies)
}

// CreateCompany creates a new company.
func CreateCompany(c *gin.Context) {
	var req struct {
		Name        string `json:"name"`
		Description string `json:"description"`
		FieldID     string `json:"field_id"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}
	fieldObjID, err := primitive.ObjectIDFromHex(req.FieldID)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid field_id"})
		return
	}

	company := bson.M{
		"name":        req.Name,
		"description": req.Description,
		"field":       fieldObjID,
	}

	client := db.GetDB()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("companies")

	result, err := collection.InsertOne(context.Background(), company)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to create company"})
		return
	}

	// Return the created company with id as hex string
	company["id"] = result.InsertedID.(primitive.ObjectID).Hex()
	delete(company, "field")
	company["field"] = req.FieldID
	c.JSON(http.StatusCreated, company)
}

// UpdateCompany updates a company's name, description, or field.
func UpdateCompany(c *gin.Context) {
	id := c.Param("id")
	objID, err := primitive.ObjectIDFromHex(id)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid ID"})
		return
	}
	var req struct {
		Name        string `json:"name"`
		Description string `json:"description"`
		FieldID     string `json:"field_id"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}
	fieldObjID, err := primitive.ObjectIDFromHex(req.FieldID)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid field_id"})
		return
	}

	update := bson.M{
		"$set": bson.M{
			"name":        req.Name,
			"description": req.Description,
			"field":       fieldObjID,
		},
	}

	client := db.GetDB()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("companies")

	result, err := collection.UpdateOne(context.Background(), bson.M{"_id": objID}, update)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to update company"})
		return
	}
	if result.MatchedCount == 0 {
		c.JSON(http.StatusNotFound, gin.H{"error": "Company not found"})
		return
	}
	c.JSON(http.StatusOK, gin.H{"message": "Company updated successfully"})
}

// DeleteCompany deletes a company by its ID.
func DeleteCompany(c *gin.Context) {
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
	collection := client.Database(dbName).Collection("companies")

	result, err := collection.DeleteOne(context.Background(), bson.M{"_id": objID})
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to delete company"})
		return
	}
	if result.DeletedCount == 0 {
		c.JSON(http.StatusNotFound, gin.H{"error": "Company not found"})
		return
	}
	c.JSON(http.StatusOK, gin.H{"message": "Company deleted successfully"})
}
