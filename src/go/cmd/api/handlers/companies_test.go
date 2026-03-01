package handlers

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	thelpers "github.com/fabrizio2210/cover_letter/src/go/cmd/api/testing"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/bson/primitive"
	"go.mongodb.org/mongo-driver/mongo"
)

func TestCreateCompany_InvalidFieldID(t *testing.T) {
	body := bytes.NewBufferString(`{"name":"Acme","description":"X","field_id":"bad"}`)
	req, _ := http.NewRequest(http.MethodPost, "/api/companies", body)
	req.Header.Set("Content-Type", "application/json")
	ctx, w := thelpers.CreateGinTestContext(http.MethodPost, "/api/companies", req)

	CreateCompany(ctx)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

func TestUpdateCompany_InvalidID(t *testing.T) {
	body := bytes.NewBufferString(`{"name":"A","description":"B","field_id":""}`)
	req, _ := http.NewRequest(http.MethodPut, "/api/companies/INVALID", body)
	req.Header.Set("Content-Type", "application/json")
	ctx, w := thelpers.CreateGinTestContext(http.MethodPut, "/api/companies/INVALID", req)
	ctx.Params = append(ctx.Params, gin.Param{Key: "id", Value: "INVALID"})

	UpdateCompany(ctx)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

func TestAssociateFieldWithCompany_InvalidCompanyID(t *testing.T) {
	body := bytes.NewBufferString(`{"field_id":""}`)
	req, _ := http.NewRequest(http.MethodPost, "/api/companies/INVALID/associate-field", body)
	req.Header.Set("Content-Type", "application/json")
	ctx, w := thelpers.CreateGinTestContext(http.MethodPost, "/api/companies/INVALID/associate-field", req)
	ctx.Params = append(ctx.Params, gin.Param{Key: "id", Value: "INVALID"})

	AssociateFieldWithCompany(ctx)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

// --- DB-backed tests and fakes ---

// Fake implementations to satisfy mongo adapter interfaces for tests.
type fakeCollection struct {
	name       string
	insertRes  *mongo.InsertOneResult
	updateRes  *mongo.UpdateResult
	deleteRes  *mongo.DeleteResult
	findOneDoc bson.M
	docs       []bson.M
}

func (f *fakeCollection) Aggregate(ctx context.Context, pipeline interface{}) (MongoCursorIface, error) {
	return &fakeCursor{docs: f.docs}, nil
}
func (f *fakeCollection) InsertOne(ctx context.Context, doc interface{}) (*mongo.InsertOneResult, error) {
	return f.insertRes, nil
}
func (f *fakeCollection) FindOne(ctx context.Context, filter interface{}) MongoSingleResultIface {
	return &fakeSingleResult{doc: f.findOneDoc}
}
func (f *fakeCollection) UpdateOne(ctx context.Context, filter interface{}, update interface{}) (*mongo.UpdateResult, error) {
	return f.updateRes, nil
}
func (f *fakeCollection) DeleteOne(ctx context.Context, filter interface{}) (*mongo.DeleteResult, error) {
	return f.deleteRes, nil
}

type fakeCursor struct {
	docs []bson.M
	idx  int
}

func (f *fakeCursor) All(ctx context.Context, result interface{}) error {
	b, _ := bson.Marshal(f.docs)
	return bson.Unmarshal(b, result)
}
func (f *fakeCursor) Next(ctx context.Context) bool {
	return f.idx < len(f.docs)
}
func (f *fakeCursor) Decode(v interface{}) error {
	if f.idx >= len(f.docs) {
		return mongo.ErrNoDocuments
	}
	b, _ := bson.Marshal(f.docs[f.idx])
	f.idx++
	return bson.Unmarshal(b, v)
}
func (f *fakeCursor) Close(ctx context.Context) error { return nil }

type fakeSingleResult struct{ doc bson.M }

func (f *fakeSingleResult) Decode(v interface{}) error {
	if f.doc == nil {
		return mongo.ErrNoDocuments
	}
	b, _ := bson.Marshal(f.doc)
	return bson.Unmarshal(b, v)
}

type fakeDatabase struct{ cols map[string]*fakeCollection }

func (d *fakeDatabase) Collection(name string) MongoCollectionIface {
	if c, ok := d.cols[name]; ok {
		return c
	}
	return &fakeCollection{name: name}
}

type fakeClient struct{ db *fakeDatabase }

func (c *fakeClient) Database(name string) MongoDatabaseIface { return c.db }

func TestCreateCompany_WithFieldLookup(t *testing.T) {
	// setup fake client
	fieldID := primitive.NewObjectID()
	insertedID := primitive.NewObjectID()
	fc := &fakeCollection{
		insertRes:  &mongo.InsertOneResult{InsertedID: insertedID},
		findOneDoc: bson.M{"_id": fieldID, "field": "Engineering"},
	}
	fakeDB := &fakeDatabase{cols: map[string]*fakeCollection{"companies": fc, "fields": fc}}
	fakeClient := &fakeClient{db: fakeDB}

	// override GetMongoClient and restore after
	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	// create request
	body := bytes.NewBufferString(`{"name":"ACME","description":"X","field_id":"` + fieldID.Hex() + `"}`)
	req, _ := http.NewRequest(http.MethodPost, "/api/companies", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req

	CreateCompany(c)

	require.Equal(t, http.StatusCreated, w.Code)
	var resp map[string]interface{}
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &resp))
	// check field_info present
	fi, ok := resp["field_info"].(map[string]interface{})
	require.True(t, ok)
	require.Equal(t, "Engineering", fi["field"])
}

func TestUpdateCompany_DBFlows(t *testing.T) {
	// prepare fake update results
	updateOk := &mongo.UpdateResult{MatchedCount: 1, ModifiedCount: 1}
	updateNotFound := &mongo.UpdateResult{MatchedCount: 0, ModifiedCount: 0}

	fc := &fakeCollection{updateRes: updateOk}
	fakeDB := &fakeDatabase{cols: map[string]*fakeCollection{"companies": fc}}
	fakeClient := &fakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	// success
	id := primitive.NewObjectID().Hex()
	body := bytes.NewBufferString(`{"name":"A","description":"B","field_id":"` + primitive.NewObjectID().Hex() + `"}`)
	req, _ := http.NewRequest(http.MethodPut, "/api/companies/"+id, body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req
	c.Params = append(c.Params, gin.Param{Key: "id", Value: id})

	UpdateCompany(c)
	require.Equal(t, http.StatusOK, w.Code)

	// not found
	fc.updateRes = updateNotFound
	// create a fresh body for second request (previous body reader was consumed)
	body2 := bytes.NewBufferString(`{"name":"A","description":"B","field_id":"` + primitive.NewObjectID().Hex() + `"}`)
	req2, _ := http.NewRequest(http.MethodPut, "/api/companies/"+id, body2)
	req2.Header.Set("Content-Type", "application/json")
	w2 := httptest.NewRecorder()
	c2, _ := gin.CreateTestContext(w2)
	c2.Request = req2
	c2.Params = append(c2.Params, gin.Param{Key: "id", Value: id})

	UpdateCompany(c2)
	require.Equal(t, http.StatusNotFound, w2.Code)
}
