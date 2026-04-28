package db

import (
	"context"
	"log"
	"os"
	"sync"

	"go.mongodb.org/mongo-driver/mongo"
	"go.mongodb.org/mongo-driver/mongo/options"
)

var (
	clientInstance *mongo.Client
	clientOnce     sync.Once
)

// GetDB returns a singleton instance of the MongoDB client.
func GetDB() *mongo.Client {
	if clientInstance != nil {
		return clientInstance
	}

	clientOnce.Do(func() {
		mongoURI := os.Getenv("MONGO_HOST")
		if mongoURI == "" {
			log.Fatal("MONGO_HOST environment variable not set")
		}
		clientOptions := options.Client().ApplyURI(mongoURI)
		client, err := mongo.Connect(context.Background(), clientOptions)
		if err != nil {
			log.Fatal(err)
		}
		clientInstance = client
	})
	return clientInstance
}

// SetTestClient overrides the package mongo client for tests.
// Call this from tests before any call to db.GetDB() to avoid
// connecting to a real Mongo instance.
func SetTestClient(c *mongo.Client) {
	clientInstance = c
}

// GetDatabaseName returns the appropriate database name for a given collection and user.
func GetDatabaseName(collectionName string, userID string) string {
	globalCollections := map[string]bool{
		"jobs":             true,
		"job-descriptions": true,
		"companies":        true,
		"fields":           true,
		"stats":            true,
	}

	if globalCollections[collectionName] {
		dbName := os.Getenv("DB_NAME")
		if dbName == "" {
			return "cover_letter"
		}
		return dbName
	}

	if collectionName == "settings" {
		if userID == "" {
			dbName := os.Getenv("DB_NAME")
			if dbName == "" {
				return "cover_letter"
			}
			return dbName
		}
		return "user_" + userID
	}

	// For per-user collections, userID should ideally be present.
	if userID == "" {
		// Log warning or handle strictly in production.
		// Fallback to default for now to avoid breaking existing dev workflows if any.
		dbName := os.Getenv("DB_NAME")
		if dbName == "" {
			return "cover_letter"
		}
		return dbName
	}

	return "user_" + userID
}
