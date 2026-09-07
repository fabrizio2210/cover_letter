package fields

import (
	"context"
	"crypto/sha256"
	"fmt"

	"github.com/fabrizio2210/cover_letter/src/go/cmd/api/db"
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/bson/primitive"
	"go.mongodb.org/mongo-driver/mongo"
	"go.mongodb.org/mongo-driver/mongo/options"
)

const defaultFieldIDNamespace = "cover-letter/default-field/v1:"

var defaultFieldNames = [...]string{
	"Technology",
	"Financial Services",
	"Healthcare",
	"Education",
	"Retail & E-commerce",
	"Manufacturing",
	"Construction",
	"Real Estate",
	"Energy & Utilities",
	"Transportation & Logistics",
	"Telecommunications",
	"Media & Entertainment",
	"Advertising & Marketing",
	"Professional Services",
	"Legal Services",
	"Government & Public Sector",
	"Nonprofit & Charities",
	"Hospitality & Tourism",
	"Food & Beverage",
	"Agriculture",
	"Automotive",
	"Aerospace & Defense",
	"Pharmaceuticals & Biotechnology",
	"Insurance",
	"Consumer Goods",
	"Fashion & Apparel",
	"Sports & Fitness",
	"Environmental Services",
	"Arts & Culture",
	"Other",
}

// BootstrapDefaults adds the preset fields when the global fields collection is empty.
func BootstrapDefaults(ctx context.Context) error {
	client := getMongoClient()
	dbName := db.GetDatabaseName("fields", "")
	collection := client.Database(dbName).Collection("fields")

	count, err := collection.CountDocuments(ctx, bson.D{})
	if err != nil {
		return fmt.Errorf("count fields before bootstrap: %w", err)
	}
	if count > 0 {
		return nil
	}

	models := make([]mongo.WriteModel, 0, len(defaultFieldNames))
	for _, name := range defaultFieldNames {
		models = append(models, mongo.NewUpdateOneModel().
			SetFilter(bson.M{"_id": defaultFieldObjectID(name)}).
			SetUpdate(bson.M{"$setOnInsert": bson.M{"field": name}}).
			SetUpsert(true))
	}

	if _, err := collection.BulkWrite(ctx, models, options.BulkWrite().SetOrdered(false)); err != nil {
		return fmt.Errorf("insert default fields: %w", err)
	}
	return nil
}

func defaultFieldObjectID(name string) primitive.ObjectID {
	hash := sha256.Sum256([]byte(defaultFieldIDNamespace + name))
	var id primitive.ObjectID
	copy(id[:], hash[:len(id)])
	return id
}
