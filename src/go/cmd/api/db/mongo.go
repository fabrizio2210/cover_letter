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
