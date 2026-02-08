package handlers

import (
	"context"
	"log"
	"net/http"
	"os"

	"github.com/fabrizio2210/cover_letter/src/go/cmd/api/db"
	"github.com/fabrizio2210/cover_letter/src/go/cmd/api/models"

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

	var companies []models.Company
	if err = cursor.All(context.Background(), &companies); err != nil {
		log.Printf("Error decoding companies: %v", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to decode companies"})
		return
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
		log.Printf("Invalid request: %v", err)
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}
	var (
		fieldObjID primitive.ObjectID
		hasField   bool
	)
	if req.FieldID != "" {
		oid, err := primitive.ObjectIDFromHex(req.FieldID)
		if err != nil {
			log.Printf("Invalid field_id: %v", err)
			c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid field_id"})
			return
		}
		fieldObjID = oid
		hasField = true
	}

	company := bson.M{
		"name":        req.Name,
		"description": req.Description,
	}
	if hasField {
		company["field"] = fieldObjID
	}

	client := db.GetDB()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("companies")

	result, err := collection.InsertOne(context.Background(), company)
	if err != nil {
		log.Printf("Error inserting company: %v", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to create company"})
		return
	}

	// Build response: include id and field_info (if available)
	resp := bson.M{
		"id":          result.InsertedID.(primitive.ObjectID).Hex(),
		"name":        req.Name,
		"description": req.Description,
	}
	if hasField {
		// try to fetch the field document to return the name
		fieldsColl := client.Database(dbName).Collection("fields")
		var fieldDoc bson.M
		err = fieldsColl.FindOne(context.Background(), bson.M{"_id": fieldObjID}).Decode(&fieldDoc)
		if err == nil {
			resp["field_info"] = bson.M{"id": fieldObjID.Hex(), "field": fieldDoc["field"]}
		} else {
			// fallback to returning the provided field id
			resp["field_info"] = bson.M{"id": req.FieldID}
		}
	}

	c.JSON(http.StatusCreated, resp)
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

// AssociateFieldWithCompany associates a field with a company.
func AssociateFieldWithCompany(c *gin.Context) {
	id := c.Param("id")
	objID, err := primitive.ObjectIDFromHex(id)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid company ID"})
		return
	}

	var req struct {
		FieldID *string `json:"field_id"`
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
	collection := client.Database(dbName).Collection("companies")

	var update bson.M
	if req.FieldID == nil || *req.FieldID == "" {
		update = bson.M{"$unset": bson.M{"field": ""}}
	} else {
		fieldObjID, err := primitive.ObjectIDFromHex(*req.FieldID)
		if err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid field_id"})
			return
		}
		update = bson.M{"$set": bson.M{"field": fieldObjID}}
	}

	result, err := collection.UpdateOne(context.Background(), bson.M{"_id": objID}, update)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to associate field with company"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Field associated successfully", "modifiedCount": result.ModifiedCount})
}
