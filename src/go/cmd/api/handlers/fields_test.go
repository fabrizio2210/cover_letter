package handlers

import (
	"bytes"
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

func TestGetFields(t *testing.T) {
	fc := &fakeCollection{docs: []bson.M{{"field": "dev"}}}
	fakeDB := &fakeDatabase{cols: map[string]*fakeCollection{"fields": fc}}
	fakeClient := &fakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	ctx, w := thelpers.CreateGinTestContext(http.MethodGet, "/api/fields", nil)
	GetFields(ctx)

	require.Equal(t, http.StatusOK, w.Code)
	var got []map[string]interface{}
	err := json.Unmarshal(w.Body.Bytes(), &got)
	require.NoError(t, err)
	require.Len(t, got, 1)
	require.Equal(t, "dev", got[0]["field"])
}

func TestCreateField_BadRequest(t *testing.T) {
	req, _ := http.NewRequest(http.MethodPost, "/api/fields", nil)
	ctx, w := thelpers.CreateGinTestContext(http.MethodPost, "/api/fields", req)
	CreateField(ctx)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

func TestCreateField_Success(t *testing.T) {
	inserted := primitive.NewObjectID()
	fc := &fakeCollection{insertRes: &mongo.InsertOneResult{InsertedID: inserted}, findOneDoc: bson.M{"_id": inserted, "field": "Engineering"}}
	fakeDB := &fakeDatabase{cols: map[string]*fakeCollection{"fields": fc}}
	fakeClient := &fakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	body := bytes.NewBufferString(`{"field":"Engineering"}`)
	req, _ := http.NewRequest(http.MethodPost, "/api/fields", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req

	CreateField(c)

	require.Equal(t, http.StatusCreated, w.Code)
}

func TestDeleteField_InvalidID(t *testing.T) {
	ctx, w := thelpers.CreateGinTestContext(http.MethodDelete, "/api/fields/INVALID", nil)
	ctx.Params = append(ctx.Params, gin.Param{Key: "id", Value: "INVALID"})
	DeleteField(ctx)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

func TestDeleteField_NotFound(t *testing.T) {
	rc := &fakeCollection{deleteRes: &mongo.DeleteResult{DeletedCount: 0}}
	fakeDB := &fakeDatabase{cols: map[string]*fakeCollection{"fields": rc}}
	fakeClient := &fakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	id := primitive.NewObjectID().Hex()
	req, _ := http.NewRequest(http.MethodDelete, "/api/fields/"+id, nil)
	ctx, w := thelpers.CreateGinTestContext(http.MethodDelete, "/api/fields/"+id, req)
	ctx.Params = append(ctx.Params, gin.Param{Key: "id", Value: id})
	DeleteField(ctx)

	require.Equal(t, http.StatusNotFound, w.Code)
}

func TestUpdateField_InvalidID(t *testing.T) {
	body := bytes.NewBufferString(`{"field":"X"}`)
	req, _ := http.NewRequest(http.MethodPut, "/api/fields/INVALID", body)
	ctx, w := thelpers.CreateGinTestContext(http.MethodPut, "/api/fields/INVALID", req)
	ctx.Params = append(ctx.Params, gin.Param{Key: "id", Value: "INVALID"})
	UpdateField(ctx)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

func TestUpdateField_NoModification(t *testing.T) {
	fc := &fakeCollection{updateRes: &mongo.UpdateResult{MatchedCount: 1, ModifiedCount: 0}}
	fakeDB := &fakeDatabase{cols: map[string]*fakeCollection{"fields": fc}}
	fakeClient := &fakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	id := primitive.NewObjectID().Hex()
	body := bytes.NewBufferString(`{"field":"same"}`)
	req, _ := http.NewRequest(http.MethodPut, "/api/fields/"+id, body)
	ctx, w := thelpers.CreateGinTestContext(http.MethodPut, "/api/fields/"+id, req)
	ctx.Params = append(ctx.Params, gin.Param{Key: "id", Value: id})
	UpdateField(ctx)

	require.Equal(t, http.StatusNotFound, w.Code)
}
