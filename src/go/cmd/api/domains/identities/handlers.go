package identities

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

// Mongo abstractions are local to this domain so handlers can delegate here
// while still injecting fakes from tests through SetMongoClientProvider.
type MongoClientIface interface {
	Database(name string) MongoDatabaseIface
}

type MongoDatabaseIface interface {
	Collection(name string) MongoCollectionIface
}

type MongoCollectionIface interface {
	Aggregate(ctx context.Context, pipeline interface{}) (MongoCursorIface, error)
	InsertOne(ctx context.Context, doc interface{}) (*mongo.InsertOneResult, error)
	FindOne(ctx context.Context, filter interface{}) MongoSingleResultIface
	UpdateOne(ctx context.Context, filter interface{}, update interface{}) (*mongo.UpdateResult, error)
	DeleteOne(ctx context.Context, filter interface{}) (*mongo.DeleteResult, error)
}

type MongoCursorIface interface {
	Next(ctx context.Context) bool
	Decode(v interface{}) error
	Close(ctx context.Context) error
}

type MongoSingleResultIface interface {
	Decode(v interface{}) error
}

var getMongoClient = func() MongoClientIface {
	return &realMongoClient{client: db.GetDB()}
}

// SetMongoClientProvider allows wrapper packages/tests to provide a custom client.
func SetMongoClientProvider(provider func() MongoClientIface) {
	if provider == nil {
		return
	}
	getMongoClient = provider
}

type realMongoClient struct{ client *mongo.Client }

func (r *realMongoClient) Database(name string) MongoDatabaseIface {
	return &realMongoDatabase{db: r.client.Database(name)}
}

type realMongoDatabase struct{ db *mongo.Database }

func (r *realMongoDatabase) Collection(name string) MongoCollectionIface {
	return &realMongoCollection{col: r.db.Collection(name)}
}

type realMongoCollection struct{ col *mongo.Collection }

func (r *realMongoCollection) Aggregate(ctx context.Context, pipeline interface{}) (MongoCursorIface, error) {
	cur, err := r.col.Aggregate(ctx, pipeline)
	if err != nil {
		return nil, err
	}
	return &realMongoCursor{cur: cur}, nil
}

func (r *realMongoCollection) InsertOne(ctx context.Context, doc interface{}) (*mongo.InsertOneResult, error) {
	return r.col.InsertOne(ctx, doc)
}

func (r *realMongoCollection) FindOne(ctx context.Context, filter interface{}) MongoSingleResultIface {
	return r.col.FindOne(ctx, filter)
}

func (r *realMongoCollection) UpdateOne(ctx context.Context, filter interface{}, update interface{}) (*mongo.UpdateResult, error) {
	return r.col.UpdateOne(ctx, filter, update)
}

func (r *realMongoCollection) DeleteOne(ctx context.Context, filter interface{}) (*mongo.DeleteResult, error) {
	return r.col.DeleteOne(ctx, filter)
}

type realMongoCursor struct{ cur *mongo.Cursor }

func (r *realMongoCursor) Next(ctx context.Context) bool   { return r.cur.Next(ctx) }
func (r *realMongoCursor) Decode(v interface{}) error      { return r.cur.Decode(v) }
func (r *realMongoCursor) Close(ctx context.Context) error { return r.cur.Close(ctx) }

// CreateIdentity creates a new identity.
func CreateIdentity(c *gin.Context) {
	var identity models.Identity
	if err := c.ShouldBindJSON(&identity); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}

	client := getMongoClient()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("identities")

	result, err := collection.InsertOne(context.Background(), &identity)
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
	client := getMongoClient()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		log.Println("Warning: DB_NAME environment variable not set. Using default 'cover_letter'.")
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("identities")

	pipeline := bson.A{
		bson.M{"$lookup": bson.M{"from": "fields", "localField": "field_id", "foreignField": "_id", "as": "fieldInfo"}},
		bson.M{"$unwind": bson.M{"path": "$fieldInfo", "preserveNullAndEmptyArrays": true}},
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
			d["id"] = idVal.Hex()
		} else if idStr, ok := d["_id"].(string); ok {
			d["id"] = idStr
		}
		delete(d, "_id")
		if fi, ok := d["fieldInfo"]; ok {
			if fiMap, ok := fi.(bson.M); ok {
				if idVal, ok := fiMap["_id"].(primitive.ObjectID); ok {
					fiMap["id"] = idVal.Hex()
					delete(fiMap, "_id")
				} else if idStr, ok := fiMap["_id"].(string); ok {
					fiMap["id"] = idStr
					delete(fiMap, "_id")
				}
				d["field_info"] = fiMap
			}
			delete(d, "fieldInfo")
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

	client := getMongoClient()
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

	client := getMongoClient()
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

// UpdateIdentityDescription wrapper.
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

// UpdateIdentityName wrapper.
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

// UpdateIdentitySignature wrapper.
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

// UpdateIdentityRoles wrapper.
func UpdateIdentityRoles(c *gin.Context) {
	var req struct {
		Roles []string `json:"roles"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}
	UpdateIdentityGeneric(c, bson.M{"roles": req.Roles})
}

// UpdateIdentityPreferences replaces the identity preference list.
func UpdateIdentityPreferences(c *gin.Context) {
	var req struct {
		Preferences []struct {
			Key      string  `json:"key"`
			Weight   float64 `json:"weight"`
			Enabled  bool    `json:"enabled"`
			Guidance string  `json:"guidance"`
		} `json:"preferences"`
	}

	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}

	seen := map[string]struct{}{}
	preferences := make([]bson.M, 0, len(req.Preferences))
	for _, preference := range req.Preferences {
		if preference.Key == "" {
			c.JSON(http.StatusBadRequest, gin.H{"error": "Preference key is required"})
			return
		}
		if _, ok := seen[preference.Key]; ok {
			c.JSON(http.StatusBadRequest, gin.H{"error": "Duplicate preference key"})
			return
		}
		seen[preference.Key] = struct{}{}
		preferences = append(preferences, bson.M{
			"key":      preference.Key,
			"weight":   preference.Weight,
			"enabled":  preference.Enabled,
			"guidance": preference.Guidance,
		})
	}

	UpdateIdentityGeneric(c, bson.M{"preferences": preferences})
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
	UpdateIdentityGeneric(c, bson.M{"field_id": fieldObjID})
}
