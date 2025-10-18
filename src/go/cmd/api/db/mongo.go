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
