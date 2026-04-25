package companies

import (
	"context"
	"log"
	"net/http"
	"os"

	"github.com/fabrizio2210/cover_letter/src/go/cmd/api/db"
	"github.com/gin-gonic/gin"
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

// SetMongoClientProvider allows wrappers/tests to inject custom clients.
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

// GetCompanies fetches all companies with their field info.
func GetCompanies(c *gin.Context) {
	client := getMongoClient()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("companies")

	pipeline := mongo.Pipeline{
		{{"$lookup", bson.D{
			{"from", "fields"},
			{"localField", "field_id"},
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

	var docs []bson.M
	for cursor.Next(context.Background()) {
		var d bson.M
		if err := cursor.Decode(&d); err != nil {
			log.Printf("Error decoding company document: %v", err)
			c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to decode companies"})
			return
		}

		if idVal, ok := d["_id"].(primitive.ObjectID); ok {
			d["id"] = idVal.Hex()
		} else if idStr, ok := d["_id"].(string); ok {
			d["id"] = idStr
		}
		delete(d, "_id")

		fieldRaw := d["field_id"]
		switch v := fieldRaw.(type) {
		case primitive.ObjectID:
			d["field_id"] = v.Hex()
		case string:
			d["field_id"] = v
		}

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
		company["field_id"] = fieldObjID
	}

	client := getMongoClient()
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

	resp := bson.M{
		"id":          result.InsertedID.(primitive.ObjectID).Hex(),
		"name":        req.Name,
		"description": req.Description,
	}
	if hasField {
		fieldsColl := client.Database(dbName).Collection("fields")
		var fieldDoc bson.M
		err = fieldsColl.FindOne(context.Background(), bson.M{"_id": fieldObjID}).Decode(&fieldDoc)
		if err == nil {
			resp["field_info"] = bson.M{"id": fieldObjID.Hex(), "field": fieldDoc["field"]}
		} else {
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
			"field_id":    fieldObjID,
		},
	}

	client := getMongoClient()
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

	client := getMongoClient()
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

	client := getMongoClient()
	dbName := os.Getenv("DB_NAME")
	if dbName == "" {
		dbName = "cover_letter"
	}
	collection := client.Database(dbName).Collection("companies")

	var update bson.M
	if req.FieldID == nil || *req.FieldID == "" {
		update = bson.M{"$unset": bson.M{"field_id": ""}}
	} else {
		fieldObjID, err := primitive.ObjectIDFromHex(*req.FieldID)
		if err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid field_id"})
			return
		}
		update = bson.M{"$set": bson.M{"field_id": fieldObjID}}
	}

	result, err := collection.UpdateOne(context.Background(), bson.M{"_id": objID}, update)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to associate field with company"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Field associated successfully", "modifiedCount": result.ModifiedCount})
}
