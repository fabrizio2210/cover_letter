package recipients

import (
	"context"
	"encoding/json"
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
	All(ctx context.Context, result interface{}) error
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

var queuePush = func(ctx context.Context, queueName string, payload []byte) error {
	return defaultRedisClient().RPush(ctx, queueName, payload).Err()
}

// SetMongoClientProvider allows wrappers/tests to inject custom clients.
func SetMongoClientProvider(provider func() MongoClientIface) {
	if provider == nil {
		return
	}
	getMongoClient = provider
}

// SetQueuePushProvider allows wrappers/tests to inject queue behavior.
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

func (r *realMongoCursor) All(ctx context.Context, result interface{}) error {
	return r.cur.All(ctx, result)
}

func (r *realMongoCursor) Next(ctx context.Context) bool   { return r.cur.Next(ctx) }
func (r *realMongoCursor) Decode(v interface{}) error      { return r.cur.Decode(v) }
func (r *realMongoCursor) Close(ctx context.Context) error { return r.cur.Close(ctx) }

// GetRecipients fetches all recipients from the database.
func GetRecipients(c *gin.Context) {
	userID, _ := c.Get("userId")
	userIDStr, _ := userID.(string)
	client := getMongoClient()
	dbName := db.GetDatabaseName("recipients", userIDStr)
	collection := client.Database(dbName).Collection("recipients")

	pipeline := mongo.Pipeline{
		{{Key: "$lookup", Value: bson.D{
			{Key: "from", Value: "companies"},
			{Key: "localField", Value: "company_id"},
			{Key: "foreignField", Value: "_id"},
			{Key: "as", Value: "companyInfo"},
		}}},
		{{Key: "$unwind", Value: bson.D{{Key: "path", Value: "$companyInfo"}, {Key: "preserveNullAndEmptyArrays", Value: true}}}},
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

// CreateRecipient creates a new recipient.
func CreateRecipient(c *gin.Context) {
	var req models.Recipient
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}

	recipient := models.Recipient{
		Email:       req.Email,
		Name:        req.Name,
		Description: req.Description,
		CompanyId:   req.CompanyId,
	}

	var insertDoc bson.M
	rawRecipient, err := bson.Marshal(&recipient)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to create recipient"})
		return
	}
	if err := bson.Unmarshal(rawRecipient, &insertDoc); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to create recipient"})
		return
	}

	if recipient.CompanyId != "" {
		companyObjID, err := primitive.ObjectIDFromHex(recipient.CompanyId)
		if err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid company_id"})
			return
		}
		insertDoc["company_id"] = companyObjID
	}

	userID, _ := c.Get("userId")
	userIDStr, _ := userID.(string)
	client := getMongoClient()
	dbName := db.GetDatabaseName("recipients", userIDStr)
	collection := client.Database(dbName).Collection("recipients")

	result, err := collection.InsertOne(context.Background(), insertDoc)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to create recipient"})
		return
	}

	insertedID, ok := result.InsertedID.(primitive.ObjectID)
	if !ok {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to fetch created recipient"})
		return
	}

	pipeline := mongo.Pipeline{
		{{Key: "$match", Value: bson.D{{Key: "_id", Value: insertedID}}}},
		{{Key: "$lookup", Value: bson.D{
			{Key: "from", Value: "companies"},
			{Key: "localField", Value: "company_id"},
			{Key: "foreignField", Value: "_id"},
			{Key: "as", Value: "companyInfo"},
		}}},
		{{Key: "$unwind", Value: bson.D{{Key: "path", Value: "$companyInfo"}, {Key: "preserveNullAndEmptyArrays", Value: true}}}},
	}

	cursor, err := collection.Aggregate(context.Background(), pipeline)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to fetch created recipient"})
		return
	}
	defer cursor.Close(context.Background())

	if cursor.Next(context.Background()) {
		var created models.Recipient
		if err := cursor.Decode(&created); err == nil {
			c.JSON(http.StatusCreated, &created)
			return
		}
	}
	c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to fetch created recipient"})
}

// DeleteRecipient deletes a recipient by its ID.
func DeleteRecipient(c *gin.Context) {
	id := c.Param("id")
	objID, err := primitive.ObjectIDFromHex(id)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid ID"})
		return
	}

	userID, _ := c.Get("userId")
	userIDStr, _ := userID.(string)
	client := getMongoClient()
	dbName := db.GetDatabaseName("recipients", userIDStr)
	collection := client.Database(dbName).Collection("recipients")

	result, err := collection.DeleteOne(context.Background(), bson.M{"_id": objID})
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to delete recipient"})
		return
	}

	if result.DeletedCount == 0 {
		c.JSON(http.StatusNotFound, gin.H{"error": "Recipient not found"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Recipient deleted successfully"})
}

// UpdateRecipientDescription updates the description of a recipient.
func UpdateRecipientDescription(c *gin.Context) {
	id := c.Param("id")
	objID, err := primitive.ObjectIDFromHex(id)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid ID"})
		return
	}

	var req struct {
		Description string `json:"description"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}

	userID, _ := c.Get("userId")
	userIDStr, _ := userID.(string)
	client := getMongoClient()
	dbName := db.GetDatabaseName("recipients", userIDStr)
	collection := client.Database(dbName).Collection("recipients")

	result, err := collection.UpdateOne(
		context.Background(),
		bson.M{"_id": objID},
		bson.M{"$set": bson.M{"description": req.Description}},
	)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to update recipient"})
		return
	}

	if result.ModifiedCount == 0 {
		c.JSON(http.StatusNotFound, gin.H{"error": "Recipient not found or description unchanged"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Recipient description updated successfully"})
}

// UpdateRecipientName updates the name of a recipient.
func UpdateRecipientName(c *gin.Context) {
	id := c.Param("id")
	objID, err := primitive.ObjectIDFromHex(id)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid ID"})
		return
	}

	var req struct {
		Name string `json:"name"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}

	userID, _ := c.Get("userId")
	userIDStr, _ := userID.(string)
	client := getMongoClient()
	dbName := db.GetDatabaseName("recipients", userIDStr)
	collection := client.Database(dbName).Collection("recipients")

	result, err := collection.UpdateOne(
		context.Background(),
		bson.M{"_id": objID},
		bson.M{"$set": bson.M{"name": req.Name}},
	)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to update recipient"})
		return
	}

	if result.ModifiedCount == 0 {
		c.JSON(http.StatusNotFound, gin.H{"error": "Recipient not found or name unchanged"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Recipient name updated successfully"})
}

// GenerateCoverLetterForRecipient triggers the cover letter generation for a recipient.
func GenerateCoverLetterForRecipient(c *gin.Context) {
	id := c.Param("id")
	objID, err := primitive.ObjectIDFromHex(id)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid ID"})
		return
	}

	userID, _ := c.Get("userId")
	userIDStr, _ := userID.(string)
	client := getMongoClient()
	dbName := db.GetDatabaseName("recipients", userIDStr)
	collection := client.Database(dbName).Collection("recipients")

	var recipient models.Recipient
	if err := collection.FindOne(context.Background(), bson.M{"_id": objID}).Decode(&recipient); err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "Recipient not found"})
		return
	}

	queueName := os.Getenv("REDIS_QUEUE_GENERATE_COVER_LETTER_NAME")
	if queueName == "" {
		queueName = "cover_letter_generation_queue"
	}

	payload := map[string]interface{}{
		"user_id":   userIDStr,
		"recipient": recipient.Email,
	}
	payloadBytes, err := json.Marshal(payload)
	if err != nil {
		log.Printf("Error marshaling payload: %v", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to create payload"})
		return
	}

	if err := queuePush(context.Background(), queueName, payloadBytes); err != nil {
		log.Printf("Error pushing to queue: %v", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to queue generation"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Generation queued successfully"})
}

// AssociateCompanyWithRecipient associates a company with a recipient.
func AssociateCompanyWithRecipient(c *gin.Context) {
	id := c.Param("id")
	objID, err := primitive.ObjectIDFromHex(id)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid ID"})
		return
	}

	var req struct {
		CompanyID *string `json:"companyId"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}

	userID, _ := c.Get("userId")
	userIDStr, _ := userID.(string)
	client := getMongoClient()
	dbName := db.GetDatabaseName("recipients", userIDStr)
	collection := client.Database(dbName).Collection("recipients")

	var update bson.M
	if req.CompanyID == nil {
		update = bson.M{"$unset": bson.M{"company_id": ""}}
	} else {
		companyObjID, err := primitive.ObjectIDFromHex(*req.CompanyID)
		if err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid Company ID"})
			return
		}
		update = bson.M{"$set": bson.M{"company_id": companyObjID}}
	}

	result, err := collection.UpdateOne(context.Background(), bson.M{"_id": objID}, update)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to associate company"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Company associated successfully", "modifiedCount": result.ModifiedCount})
}
