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
