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

// Reuse fake types from other tests to mock mongo interactions

func TestGetIdentities(t *testing.T) {
	fc := &fakeCollection{docs: []bson.M{{"identity": "dev", "roles": bson.A{"software engineer", "platform engineer"}}}}
	fakeDB := &fakeDatabase{cols: map[string]*fakeCollection{"identities": fc}}
	fakeClient := &fakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	ctx, w := thelpers.CreateGinTestContext(http.MethodGet, "/api/identities", nil)
	GetIdentities(ctx)
	require.Equal(t, http.StatusOK, w.Code)
	var got []map[string]interface{}
	err := json.Unmarshal(w.Body.Bytes(), &got)
	require.NoError(t, err)
	require.Len(t, got, 1)
	require.Equal(t, []interface{}{"software engineer", "platform engineer"}, got[0]["roles"])
}

func TestCreateIdentity_BadRequest(t *testing.T) {
	req, _ := http.NewRequest(http.MethodPost, "/api/identities", nil)
	ctx, w := thelpers.CreateGinTestContext(http.MethodPost, "/api/identities", req)
	CreateIdentity(ctx)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

func TestCreateIdentity_Success(t *testing.T) {
	inserted := primitive.NewObjectID()
	fc := &fakeCollection{insertRes: &mongo.InsertOneResult{InsertedID: inserted}, findOneDoc: bson.M{"_id": inserted, "identity": "Dev", "roles": bson.A{"software engineer", "platform engineer"}}}
	fakeDB := &fakeDatabase{cols: map[string]*fakeCollection{"identities": fc}}
	fakeClient := &fakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	body := bytes.NewBufferString(`{"identity":"Dev","name":"Developer","roles":["software engineer","platform engineer"]}`)
	req, _ := http.NewRequest(http.MethodPost, "/api/identities", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req

	CreateIdentity(c)
	require.Equal(t, http.StatusCreated, w.Code)

	encodedDoc, err := bson.Marshal(fc.insertDoc)
	require.NoError(t, err)
	var insertedDoc bson.M
	require.NoError(t, bson.Unmarshal(encodedDoc, &insertedDoc))
	require.Equal(t, bson.A{"software engineer", "platform engineer"}, insertedDoc["roles"])

	var resp map[string]interface{}
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &resp))
	require.Equal(t, []interface{}{"software engineer", "platform engineer"}, resp["roles"])
}

func TestDeleteIdentity_InvalidID(t *testing.T) {
	ctx, w := thelpers.CreateGinTestContext(http.MethodDelete, "/api/identities/INVALID", nil)
	ctx.Params = append(ctx.Params, gin.Param{Key: "id", Value: "INVALID"})
	DeleteIdentity(ctx)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

func TestDeleteIdentity_NotFound(t *testing.T) {
	rc := &fakeCollection{deleteRes: &mongo.DeleteResult{DeletedCount: 0}}
	fakeDB := &fakeDatabase{cols: map[string]*fakeCollection{"identities": rc}}
	fakeClient := &fakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	id := primitive.NewObjectID().Hex()
	req, _ := http.NewRequest(http.MethodDelete, "/api/identities/"+id, nil)
	ctx, w := thelpers.CreateGinTestContext(http.MethodDelete, "/api/identities/"+id, req)
	ctx.Params = append(ctx.Params, gin.Param{Key: "id", Value: id})
	DeleteIdentity(ctx)
	require.Equal(t, http.StatusNotFound, w.Code)
}

func TestUpdateIdentity_MatchedNotModified(t *testing.T) {
	fc := &fakeCollection{updateRes: &mongo.UpdateResult{MatchedCount: 1, ModifiedCount: 0}}
	fakeDB := &fakeDatabase{cols: map[string]*fakeCollection{"identities": fc}}
	fakeClient := &fakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	id := primitive.NewObjectID().Hex()
	body := bytes.NewBufferString(`{"name":"Same"}`)
	req, _ := http.NewRequest(http.MethodPut, "/api/identities/"+id, body)
	ctx, w := thelpers.CreateGinTestContext(http.MethodPut, "/api/identities/"+id, req)
	ctx.Params = append(ctx.Params, gin.Param{Key: "id", Value: id})
	UpdateIdentityGeneric(ctx, bson.M{"name": "Same"})

	require.Equal(t, http.StatusOK, w.Code)
}

func TestUpdateIdentitySignature_TooLarge(t *testing.T) {
	// signature bigger than 64KB
	longSig := make([]byte, 65*1024)
	// using UpdateIdentitySignature wrapper which parses JSON; build JSON body
	jsonBody := bytes.NewBufferString(`{"html_signature":"` + string(longSig) + `"}`)
	req2, _ := http.NewRequest(http.MethodPut, "/api/identities/invalid/signature", jsonBody)
	ctx2, w2 := thelpers.CreateGinTestContext(http.MethodPut, "/api/identities/invalid/signature", req2)
	UpdateIdentitySignature(ctx2)
	require.Equal(t, http.StatusBadRequest, w2.Code)
}
