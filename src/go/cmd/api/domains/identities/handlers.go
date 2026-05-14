package identities

import (
	"context"
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"os"

	"github.com/fabrizio2210/cover_letter/src/go/cmd/api/db"
	"github.com/fabrizio2210/cover_letter/src/go/cmd/api/models"
	"github.com/gin-gonic/gin"
	"github.com/go-redis/redis/v8"
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

var queuePush = func(ctx context.Context, queueName string, payload []byte) error {
	return defaultRedisClient().RPush(ctx, queueName, payload).Err()
}

// SetQueuePushProvider allows wrappers/tests to inject queue behaviour.
func SetQueuePushProvider(provider func(ctx context.Context, queueName string, payload []byte) error) {
	if provider == nil {
		return
	}
	queuePush = provider
}

func defaultRedisClient() *redis.Client {
	redisHost := os.Getenv("REDIS_HOST")
	if redisHost == "" {
		redisHost = "localhost"
	}
	redisPort := os.Getenv("REDIS_PORT")
	if redisPort == "" {
		redisPort = "6379"
	}
	return redis.NewClient(&redis.Options{Addr: redisHost + ":" + redisPort})
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

	userID, _ := c.Get("userId")
	userIDStr, _ := userID.(string)
	client := getMongoClient()
	dbName := db.GetDatabaseName("identities", userIDStr)
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
	userID, _ := c.Get("userId")
	userIDStr, _ := userID.(string)
	client := getMongoClient()
	dbName := db.GetDatabaseName("identities", userIDStr)
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

	userID, _ := c.Get("userId")
	userIDStr, _ := userID.(string)
	client := getMongoClient()
	dbName := db.GetDatabaseName("identities", userIDStr)
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

	userID, _ := c.Get("userId")
	userIDStr, _ := userID.(string)
	client := getMongoClient()
	dbName := db.GetDatabaseName("identities", userIDStr)
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

// UpdateIdentityPreferences replaces the identity preference list and propagates
// scoring lifecycle changes to job-preference-scores documents.
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
	newPrefs := make([]bson.M, 0, len(req.Preferences))
	newPrefMap := make(map[string]struct {
		Guidance string
		Weight   float64
		Enabled  bool
	}, len(req.Preferences))
	for _, p := range req.Preferences {
		if p.Key == "" {
			c.JSON(http.StatusBadRequest, gin.H{"error": "Preference key is required"})
			return
		}
		if _, ok := seen[p.Key]; ok {
			c.JSON(http.StatusBadRequest, gin.H{"error": "Duplicate preference key"})
			return
		}
		seen[p.Key] = struct{}{}
		newPrefs = append(newPrefs, bson.M{
			"key":      p.Key,
			"weight":   p.Weight,
			"enabled":  p.Enabled,
			"guidance": p.Guidance,
		})
		newPrefMap[p.Key] = struct {
			Guidance string
			Weight   float64
			Enabled  bool
		}{p.Guidance, p.Weight, p.Enabled}
	}

	id := c.Param("id")
	objID, err := primitive.ObjectIDFromHex(id)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid ID"})
		return
	}

	userID, _ := c.Get("userId")
	userIDStr, _ := userID.(string)
	client := getMongoClient()
	identDbName := db.GetDatabaseName("identities", userIDStr)
	identCollection := client.Database(identDbName).Collection("identities")

	// Step 2: fetch existing preferences.
	var oldDoc bson.M
	fetchErr := identCollection.FindOne(context.Background(), bson.M{"_id": objID}).Decode(&oldDoc)
	if fetchErr != nil && !errors.Is(fetchErr, mongo.ErrNoDocuments) {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to fetch identity"})
		return
	}

	// Step 3: compute diff.
	removedKeys := map[string]bool{}
	guidanceChangedKeys := map[string]bool{}
	weightChangedKeys := map[string]bool{}

	if fetchErr == nil {
		if oldPrefsRaw, ok := oldDoc["preferences"]; ok {
			switch oldPrefsList := oldPrefsRaw.(type) {
			case bson.A:
				for _, item := range oldPrefsList {
					entry, ok := item.(bson.M)
					if !ok {
						continue
					}
					oldKey, _ := entry["key"].(string)
					if oldKey == "" {
						continue
					}
					newPref, exists := newPrefMap[oldKey]
					if !exists {
						removedKeys[oldKey] = true
						continue
					}
					oldGuidance, _ := entry["guidance"].(string)
					if oldGuidance != newPref.Guidance {
						guidanceChangedKeys[oldKey] = true
					} else {
						oldWeight, _ := entry["weight"].(float64)
						oldEnabled, _ := entry["enabled"].(bool)
						if oldWeight != newPref.Weight || oldEnabled != newPref.Enabled {
							weightChangedKeys[oldKey] = true
						}
					}
				}
			}
		}
	}

	// Step 4: persist new preferences.
	updateResult, err := identCollection.UpdateOne(
		context.Background(),
		bson.M{"_id": objID},
		bson.M{"$set": bson.M{"preferences": newPrefs}},
	)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to update identity"})
		return
	}
	if updateResult.MatchedCount == 0 {
		c.JSON(http.StatusNotFound, gin.H{"error": "Identity not found"})
		return
	}

	// Step 5: load all job-preference-scores docs for this identity.
	scoreDbName := db.GetDatabaseName("job-preference-scores", userIDStr)
	scoreCollection := client.Database(scoreDbName).Collection("job-preference-scores")

	pipeline := bson.A{bson.M{"$match": bson.M{"identity_id": id}}}
	cursor, err := scoreCollection.Aggregate(context.Background(), pipeline)
	if err != nil {
		log.Printf("UpdateIdentityPreferences: aggregate score docs error: %v", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to load score documents"})
		return
	}
	defer cursor.Close(context.Background())

	// Step 6 + 7: mutate each score doc, then enqueue re-score if needed.
	needRescore := len(guidanceChangedKeys) > 0
	queueName := os.Getenv("JOB_SCORING_QUEUE_NAME")
	if queueName == "" {
		queueName = "job_scoring_queue"
	}

	for cursor.Next(context.Background()) {
		var scoreDoc bson.M
		if err := cursor.Decode(&scoreDoc); err != nil {
			log.Printf("UpdateIdentityPreferences: decode score doc error: %v", err)
			c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to decode score document"})
			return
		}

		docID, _ := scoreDoc["_id"].(primitive.ObjectID)

		// Rebuild preference_scores slice.
		var rawEntries bson.A
		if v, ok := scoreDoc["preference_scores"]; ok {
			rawEntries, _ = v.(bson.A)
		}

		var newEntries bson.A
		var weightedSum, weightSum float64
		for _, item := range rawEntries {
			entry, ok := item.(bson.M)
			if !ok {
				continue
			}
			prefKey, _ := entry["preference_key"].(string)
			if removedKeys[prefKey] || guidanceChangedKeys[prefKey] {
				continue
			}
			if weightChangedKeys[prefKey] {
				if np, exists := newPrefMap[prefKey]; exists {
					entry["preference_weight"] = np.Weight
				}
			}
			newEntries = append(newEntries, entry)
			score, _ := entry["score"].(int32)
			weight, _ := entry["preference_weight"].(float64)
			weightedSum += float64(score) * weight
			weightSum += weight
		}

		var ws float64
		if weightSum > 0 {
			ws = weightedSum / weightSum
		}

		setDoc := bson.M{
			"preference_scores": newEntries,
			"weighted_score":    ws,
		}
		if len(newEntries) == 0 {
			setDoc["scoring_status"] = "skipped"
		}

		scoreFilter := bson.M{"_id": docID}
		if _, err := scoreCollection.UpdateOne(context.Background(), scoreFilter, bson.M{"$set": setDoc}); err != nil {
			log.Printf("UpdateIdentityPreferences: update score doc error: %v", err)
			c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to update score document"})
			return
		}

		// Step 7: enqueue re-score for guidance-changed preferences.
		if needRescore {
			jobID, _ := scoreDoc["job_id"].(string)
			log.Printf("UpdateIdentityPreferences: enqueue scoring requested for job_id=%s identity_id=%s queue=%s", jobID, id, queueName)
			payload := map[string]string{
				"job_id":      jobID,
				"user_id":     userIDStr,
				"identity_id": id,
			}
			payloadBytes, err := json.Marshal(payload)
			if err != nil {
				log.Printf("UpdateIdentityPreferences: marshal queue payload error: %v", err)
				continue
			}
			if err := queuePush(context.Background(), queueName, payloadBytes); err != nil {
				log.Printf("UpdateIdentityPreferences: queue push error: %v", err)
			} else {
				log.Printf("UpdateIdentityPreferences: enqueue scoring succeeded for job_id=%s identity_id=%s queue=%s", jobID, id, queueName)
			}
		}
	}

	c.JSON(http.StatusOK, gin.H{"message": "Identity updated successfully"})
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
