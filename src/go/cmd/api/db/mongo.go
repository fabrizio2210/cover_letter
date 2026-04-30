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

// globalDBName returns the shared database name for global collections.
func globalDBName() string {
	if name := os.Getenv("DB_NAME"); name != "" {
		return name
	}
	return "cover_letter_global"
}

// GetDatabaseName returns the appropriate database name for a given collection and user.
//
// Global collections (jobs, companies, fields, stats) are stored in the shared
// database. All other collections are stored in a per-user database named
// cover_letter_<userID>, where userID is the JWT sub claim (a SHA-256–derived
// hex string set at login time — never raw user input).
func GetDatabaseName(collectionName string, userID string) string {
	globalCollections := map[string]bool{
		"jobs":             true,
		"job-descriptions": true,
		"companies":        true,
		"fields":           true,
		"stats":            true,
	}

	if globalCollections[collectionName] {
		return globalDBName()
	}

	// Per-user collections require a userID. Fall back to the global DB only when
	// userID is absent (e.g., unauthenticated dev tooling).
	if userID == "" {
		return globalDBName()
	}

	return "cover_letter_" + userID
}
