package identities

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"testing"

	apitesting "github.com/fabrizio2210/cover_letter/src/go/cmd/api/testing"
	"github.com/gin-gonic/gin"
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/bson/primitive"
	"go.mongodb.org/mongo-driver/mongo"
)

type fakeMongoClient struct {
	db *fakeMongoDatabase
}

func (f *fakeMongoClient) Database(_ string) MongoDatabaseIface {
	return f.db
}

type fakeMongoDatabase struct {
	collections map[string]MongoCollectionIface
}

func (f *fakeMongoDatabase) Collection(name string) MongoCollectionIface {
	if col, ok := f.collections[name]; ok {
		return col
	}
	return &fakeMongoCollection{}
}

type fakeMongoCollection struct {
	aggregatePipeline interface{}
	aggregateErr      error
	cursor            MongoCursorIface

	insertDoc  interface{}
	insertErr  error
	insertedID interface{}

	findOneFilter interface{}
	singleResult  MongoSingleResultIface

	updateFilter interface{}
	updateDoc    interface{}
	updateErr    error
	updateResult *mongo.UpdateResult

	deleteFilter interface{}
	deleteErr    error
	deleteResult *mongo.DeleteResult
}

func (f *fakeMongoCollection) Aggregate(_ context.Context, pipeline interface{}) (MongoCursorIface, error) {
	f.aggregatePipeline = pipeline
	if f.aggregateErr != nil {
		return nil, f.aggregateErr
	}
	if f.cursor != nil {
		return f.cursor, nil
	}
	return &fakeMongoCursor{}, nil
}

func (f *fakeMongoCollection) InsertOne(_ context.Context, doc interface{}) (*mongo.InsertOneResult, error) {
	f.insertDoc = doc
	if f.insertErr != nil {
		return nil, f.insertErr
	}
	insertedID := f.insertedID
	if insertedID == nil {
		insertedID = primitive.NewObjectID()
	}
	return &mongo.InsertOneResult{InsertedID: insertedID}, nil
}

func (f *fakeMongoCollection) FindOne(_ context.Context, filter interface{}) MongoSingleResultIface {
	f.findOneFilter = filter
	if f.singleResult != nil {
		return f.singleResult
	}
	return &fakeMongoSingleResult{err: mongo.ErrNoDocuments}
}

func (f *fakeMongoCollection) UpdateOne(_ context.Context, filter interface{}, update interface{}) (*mongo.UpdateResult, error) {
	f.updateFilter = filter
	f.updateDoc = update
	if f.updateErr != nil {
		return nil, f.updateErr
	}
	if f.updateResult != nil {
		return f.updateResult, nil
	}
	return &mongo.UpdateResult{MatchedCount: 1, ModifiedCount: 1}, nil
}

func (f *fakeMongoCollection) DeleteOne(_ context.Context, filter interface{}) (*mongo.DeleteResult, error) {
	f.deleteFilter = filter
	if f.deleteErr != nil {
		return nil, f.deleteErr
	}
	if f.deleteResult != nil {
		return f.deleteResult, nil
	}
	return &mongo.DeleteResult{DeletedCount: 1}, nil
}

type fakeMongoCursor struct {
	docs        []bson.M
	decodeErrAt map[int]error
	idx         int
}

func (f *fakeMongoCursor) Next(_ context.Context) bool {
	return f.idx < len(f.docs)
}

func (f *fakeMongoCursor) Decode(v interface{}) error {
	if err := f.decodeErrAt[f.idx]; err != nil {
		return err
	}
	doc := f.docs[f.idx]
	f.idx++

	ptr, ok := v.(*bson.M)
	if !ok {
		return errors.New("expected *bson.M decode target")
	}
	*ptr = doc
	return nil
}

func (f *fakeMongoCursor) Close(_ context.Context) error {
	return nil
}

type fakeMongoSingleResult struct {
	doc bson.M
	err error
}

func (f *fakeMongoSingleResult) Decode(v interface{}) error {
	if f.err != nil {
		return f.err
	}
	ptr, ok := v.(*bson.M)
	if !ok {
		return errors.New("expected *bson.M decode target")
	}
	*ptr = f.doc
	return nil
}

func withFakeMongo(t *testing.T, col MongoCollectionIface) {
	t.Helper()
	prev := getMongoClient
	client := &fakeMongoClient{db: &fakeMongoDatabase{collections: map[string]MongoCollectionIface{"identities": col}}}
	SetMongoClientProvider(func() MongoClientIface { return client })
	t.Cleanup(func() {
		getMongoClient = prev
	})
}

func decodeResponseMap(t *testing.T, body []byte) map[string]interface{} {
	t.Helper()
	var out map[string]interface{}
	if err := json.Unmarshal(body, &out); err != nil {
		t.Fatalf("failed to decode response map: %v", err)
	}
	return out
}

func decodeResponseSlice(t *testing.T, body []byte) []map[string]interface{} {
	t.Helper()
	var out []map[string]interface{}
	if err := json.Unmarshal(body, &out); err != nil {
		t.Fatalf("failed to decode response slice: %v", err)
	}
	return out
}

func TestGetIdentities_AggregateError(t *testing.T) {
	col := &fakeMongoCollection{aggregateErr: errors.New("aggregate failed")}
	withFakeMongo(t, col)
	ctx, rec := apitesting.CreateGinTestContext(http.MethodGet, "/api/identities", nil)

	GetIdentities(ctx)

	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("expected status 500, got %d", rec.Code)
	}
	payload := decodeResponseMap(t, rec.Body.Bytes())
	if payload["error"] != "Failed to fetch identities" {
		t.Fatalf("unexpected error response: %#v", payload)
	}
}

func TestGetIdentities_DecodeError(t *testing.T) {
	col := &fakeMongoCollection{cursor: &fakeMongoCursor{
		docs:        []bson.M{{"_id": primitive.NewObjectID()}},
		decodeErrAt: map[int]error{0: errors.New("decode failed")},
	}}
	withFakeMongo(t, col)
	ctx, rec := apitesting.CreateGinTestContext(http.MethodGet, "/api/identities", nil)

	GetIdentities(ctx)

	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("expected status 500, got %d", rec.Code)
	}
	payload := decodeResponseMap(t, rec.Body.Bytes())
	if payload["error"] != "Failed to decode identities" {
		t.Fatalf("unexpected error response: %#v", payload)
	}
}

func TestGetIdentities_EmptyList(t *testing.T) {
	col := &fakeMongoCollection{cursor: &fakeMongoCursor{docs: []bson.M{}}}
	withFakeMongo(t, col)
	ctx, rec := apitesting.CreateGinTestContext(http.MethodGet, "/api/identities", nil)

	GetIdentities(ctx)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", rec.Code)
	}
	items := decodeResponseSlice(t, rec.Body.Bytes())
	if len(items) != 0 {
		t.Fatalf("expected empty list, got %d items", len(items))
	}
}

func TestGetIdentities_NormalizesObjectIDsAndFieldInfo(t *testing.T) {
	identityID := primitive.NewObjectID()
	fieldID := primitive.NewObjectID()
	col := &fakeMongoCollection{cursor: &fakeMongoCursor{docs: []bson.M{{
		"_id":      identityID,
		"identity": "id-1",
		"fieldInfo": bson.M{
			"_id":   fieldID,
			"field": "Engineering",
		},
	}}}}
	withFakeMongo(t, col)
	ctx, rec := apitesting.CreateGinTestContext(http.MethodGet, "/api/identities", nil)

	GetIdentities(ctx)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", rec.Code)
	}
	items := decodeResponseSlice(t, rec.Body.Bytes())
	if len(items) != 1 {
		t.Fatalf("expected one item, got %d", len(items))
	}
	item := items[0]
	if item["id"] != identityID.Hex() {
		t.Fatalf("unexpected id: %#v", item["id"])
	}
	if _, ok := item["_id"]; ok {
		t.Fatalf("_id should be removed: %#v", item)
	}
	fieldInfo, ok := item["field_info"].(map[string]interface{})
	if !ok {
		t.Fatalf("field_info missing or wrong type: %#v", item)
	}
	if fieldInfo["id"] != fieldID.Hex() {
		t.Fatalf("unexpected field_info.id: %#v", fieldInfo["id"])
	}
	if _, ok := fieldInfo["_id"]; ok {
		t.Fatalf("field_info._id should be removed: %#v", fieldInfo)
	}
}

func TestGetIdentities_PreservesStringIDs(t *testing.T) {
	col := &fakeMongoCollection{cursor: &fakeMongoCursor{docs: []bson.M{{
		"_id":      "identity-string-id",
		"identity": "id-2",
		"fieldInfo": bson.M{
			"_id":   "field-string-id",
			"field": "Design",
		},
	}}}}
	withFakeMongo(t, col)
	ctx, rec := apitesting.CreateGinTestContext(http.MethodGet, "/api/identities", nil)

	GetIdentities(ctx)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", rec.Code)
	}
	items := decodeResponseSlice(t, rec.Body.Bytes())
	if len(items) != 1 {
		t.Fatalf("expected one item, got %d", len(items))
	}
	if items[0]["id"] != "identity-string-id" {
		t.Fatalf("unexpected id: %#v", items[0]["id"])
	}
	fieldInfo, ok := items[0]["field_info"].(map[string]interface{})
	if !ok {
		t.Fatalf("field_info missing or wrong type: %#v", items[0])
	}
	if fieldInfo["id"] != "field-string-id" {
		t.Fatalf("unexpected field id: %#v", fieldInfo["id"])
	}
}

func TestCreateIdentity_InvalidJSON(t *testing.T) {
	col := &fakeMongoCollection{}
	withFakeMongo(t, col)
	req, _ := http.NewRequest(http.MethodPost, "/api/identities", bytes.NewBufferString("{"))
	req.Header.Set("Content-Type", "application/json")
	ctx, rec := apitesting.CreateGinTestContext(http.MethodPost, "/api/identities", req)

	CreateIdentity(ctx)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d", rec.Code)
	}
	payload := decodeResponseMap(t, rec.Body.Bytes())
	if payload["error"] != "Invalid request" {
		t.Fatalf("unexpected response: %#v", payload)
	}
}

func TestCreateIdentity_InsertError(t *testing.T) {
	col := &fakeMongoCollection{insertErr: errors.New("insert failed")}
	withFakeMongo(t, col)
	req, _ := http.NewRequest(http.MethodPost, "/api/identities", bytes.NewBufferString(`{"identity":"id-1"}`))
	req.Header.Set("Content-Type", "application/json")
	ctx, rec := apitesting.CreateGinTestContext(http.MethodPost, "/api/identities", req)

	CreateIdentity(ctx)

	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("expected status 500, got %d", rec.Code)
	}
	payload := decodeResponseMap(t, rec.Body.Bytes())
	if payload["error"] != "Failed to create identity" {
		t.Fatalf("unexpected response: %#v", payload)
	}
}

func TestCreateIdentity_ReturnsCreatedDocumentWithNormalizedID(t *testing.T) {
	id := primitive.NewObjectID()
	col := &fakeMongoCollection{
		insertedID: id,
		singleResult: &fakeMongoSingleResult{doc: bson.M{
			"_id":      id,
			"identity": "id-1",
		}},
	}
	withFakeMongo(t, col)
	req, _ := http.NewRequest(http.MethodPost, "/api/identities", bytes.NewBufferString(`{"identity":"id-1"}`))
	req.Header.Set("Content-Type", "application/json")
	ctx, rec := apitesting.CreateGinTestContext(http.MethodPost, "/api/identities", req)

	CreateIdentity(ctx)

	if rec.Code != http.StatusCreated {
		t.Fatalf("expected status 201, got %d", rec.Code)
	}
	payload := decodeResponseMap(t, rec.Body.Bytes())
	if payload["_id"] != id.Hex() {
		t.Fatalf("unexpected _id: %#v", payload["_id"])
	}
	if payload["identity"] != "id-1" {
		t.Fatalf("unexpected identity: %#v", payload["identity"])
	}
}

func TestCreateIdentity_FallbackToInsertedObjectID(t *testing.T) {
	id := primitive.NewObjectID()
	col := &fakeMongoCollection{
		insertedID:   id,
		singleResult: &fakeMongoSingleResult{err: mongo.ErrNoDocuments},
	}
	withFakeMongo(t, col)
	req, _ := http.NewRequest(http.MethodPost, "/api/identities", bytes.NewBufferString(`{"identity":"id-1"}`))
	req.Header.Set("Content-Type", "application/json")
	ctx, rec := apitesting.CreateGinTestContext(http.MethodPost, "/api/identities", req)

	CreateIdentity(ctx)

	if rec.Code != http.StatusCreated {
		t.Fatalf("expected status 201, got %d", rec.Code)
	}
	payload := decodeResponseMap(t, rec.Body.Bytes())
	if payload["_id"] != id.Hex() {
		t.Fatalf("unexpected _id: %#v", payload["_id"])
	}
}

func TestCreateIdentity_FallbackToInsertedIDValue(t *testing.T) {
	col := &fakeMongoCollection{
		insertedID:   "inserted-custom-id",
		singleResult: &fakeMongoSingleResult{err: errors.New("decode failed")},
	}
	withFakeMongo(t, col)
	req, _ := http.NewRequest(http.MethodPost, "/api/identities", bytes.NewBufferString(`{"identity":"id-1"}`))
	req.Header.Set("Content-Type", "application/json")
	ctx, rec := apitesting.CreateGinTestContext(http.MethodPost, "/api/identities", req)

	CreateIdentity(ctx)

	if rec.Code != http.StatusCreated {
		t.Fatalf("expected status 201, got %d", rec.Code)
	}
	payload := decodeResponseMap(t, rec.Body.Bytes())
	if payload["insertedId"] != "inserted-custom-id" {
		t.Fatalf("unexpected insertedId: %#v", payload["insertedId"])
	}
}

func TestDeleteIdentity_InvalidID(t *testing.T) {
	col := &fakeMongoCollection{}
	withFakeMongo(t, col)
	ctx, rec := apitesting.CreateGinTestContext(http.MethodDelete, "/api/identities/bad-id", nil)
	ctx.Params = gin.Params{{Key: "id", Value: "bad-id"}}

	DeleteIdentity(ctx)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d", rec.Code)
	}
	payload := decodeResponseMap(t, rec.Body.Bytes())
	if payload["error"] != "Invalid ID" {
		t.Fatalf("unexpected response: %#v", payload)
	}
}

func TestDeleteIdentity_DeleteError(t *testing.T) {
	id := primitive.NewObjectID().Hex()
	col := &fakeMongoCollection{deleteErr: errors.New("delete failed")}
	withFakeMongo(t, col)
	ctx, rec := apitesting.CreateGinTestContext(http.MethodDelete, "/api/identities/"+id, nil)
	ctx.Params = gin.Params{{Key: "id", Value: id}}

	DeleteIdentity(ctx)

	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("expected status 500, got %d", rec.Code)
	}
	payload := decodeResponseMap(t, rec.Body.Bytes())
	if payload["error"] != "Failed to delete identity" {
		t.Fatalf("unexpected response: %#v", payload)
	}
}

func TestDeleteIdentity_NotFound(t *testing.T) {
	id := primitive.NewObjectID().Hex()
	col := &fakeMongoCollection{deleteResult: &mongo.DeleteResult{DeletedCount: 0}}
	withFakeMongo(t, col)
	ctx, rec := apitesting.CreateGinTestContext(http.MethodDelete, "/api/identities/"+id, nil)
	ctx.Params = gin.Params{{Key: "id", Value: id}}

	DeleteIdentity(ctx)

	if rec.Code != http.StatusNotFound {
		t.Fatalf("expected status 404, got %d", rec.Code)
	}
	payload := decodeResponseMap(t, rec.Body.Bytes())
	if payload["error"] != "Identity not found" {
		t.Fatalf("unexpected response: %#v", payload)
	}
}

func TestDeleteIdentity_Success(t *testing.T) {
	id := primitive.NewObjectID().Hex()
	col := &fakeMongoCollection{deleteResult: &mongo.DeleteResult{DeletedCount: 1}}
	withFakeMongo(t, col)
	ctx, rec := apitesting.CreateGinTestContext(http.MethodDelete, "/api/identities/"+id, nil)
	ctx.Params = gin.Params{{Key: "id", Value: id}}

	DeleteIdentity(ctx)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", rec.Code)
	}
	payload := decodeResponseMap(t, rec.Body.Bytes())
	if payload["message"] != "Identity deleted successfully" {
		t.Fatalf("unexpected response: %#v", payload)
	}
}

func TestUpdateIdentityGeneric_InvalidID(t *testing.T) {
	col := &fakeMongoCollection{}
	withFakeMongo(t, col)
	ctx, rec := apitesting.CreateGinTestContext(http.MethodPut, "/api/identities/bad-id/name", nil)
	ctx.Params = gin.Params{{Key: "id", Value: "bad-id"}}

	UpdateIdentityGeneric(ctx, bson.M{"name": "new"})

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d", rec.Code)
	}
	payload := decodeResponseMap(t, rec.Body.Bytes())
	if payload["error"] != "Invalid ID" {
		t.Fatalf("unexpected response: %#v", payload)
	}
}

func TestUpdateIdentityGeneric_UpdateError(t *testing.T) {
	id := primitive.NewObjectID().Hex()
	col := &fakeMongoCollection{updateErr: errors.New("update failed")}
	withFakeMongo(t, col)
	ctx, rec := apitesting.CreateGinTestContext(http.MethodPut, "/api/identities/"+id+"/name", nil)
	ctx.Params = gin.Params{{Key: "id", Value: id}}

	UpdateIdentityGeneric(ctx, bson.M{"name": "new"})

	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("expected status 500, got %d", rec.Code)
	}
	payload := decodeResponseMap(t, rec.Body.Bytes())
	if payload["error"] != "Failed to update identity" {
		t.Fatalf("unexpected response: %#v", payload)
	}
}

func TestUpdateIdentityGeneric_NotFound(t *testing.T) {
	id := primitive.NewObjectID().Hex()
	col := &fakeMongoCollection{updateResult: &mongo.UpdateResult{MatchedCount: 0, ModifiedCount: 0}}
	withFakeMongo(t, col)
	ctx, rec := apitesting.CreateGinTestContext(http.MethodPut, "/api/identities/"+id+"/name", nil)
	ctx.Params = gin.Params{{Key: "id", Value: id}}

	UpdateIdentityGeneric(ctx, bson.M{"name": "new"})

	if rec.Code != http.StatusNotFound {
		t.Fatalf("expected status 404, got %d", rec.Code)
	}
	payload := decodeResponseMap(t, rec.Body.Bytes())
	if payload["error"] != "Identity not found" {
		t.Fatalf("unexpected response: %#v", payload)
	}
}

func TestUpdateIdentityGeneric_NoChanges(t *testing.T) {
	id := primitive.NewObjectID().Hex()
	col := &fakeMongoCollection{updateResult: &mongo.UpdateResult{MatchedCount: 1, ModifiedCount: 0}}
	withFakeMongo(t, col)
	ctx, rec := apitesting.CreateGinTestContext(http.MethodPut, "/api/identities/"+id+"/name", nil)
	ctx.Params = gin.Params{{Key: "id", Value: id}}

	UpdateIdentityGeneric(ctx, bson.M{"name": "same"})

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", rec.Code)
	}
	payload := decodeResponseMap(t, rec.Body.Bytes())
	if payload["message"] != "Identity found; no changes made" {
		t.Fatalf("unexpected response: %#v", payload)
	}
}

func TestUpdateIdentityGeneric_SuccessAndSetPayload(t *testing.T) {
	idHex := primitive.NewObjectID().Hex()
	col := &fakeMongoCollection{updateResult: &mongo.UpdateResult{MatchedCount: 1, ModifiedCount: 1}}
	withFakeMongo(t, col)
	ctx, rec := apitesting.CreateGinTestContext(http.MethodPut, "/api/identities/"+idHex+"/name", nil)
	ctx.Params = gin.Params{{Key: "id", Value: idHex}}

	UpdateIdentityGeneric(ctx, bson.M{"name": "updated"})

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", rec.Code)
	}
	payload := decodeResponseMap(t, rec.Body.Bytes())
	if payload["message"] != "Identity updated successfully" {
		t.Fatalf("unexpected response: %#v", payload)
	}
	filter, ok := col.updateFilter.(bson.M)
	if !ok {
		t.Fatalf("update filter type mismatch: %T", col.updateFilter)
	}
	if _, ok := filter["_id"].(primitive.ObjectID); !ok {
		t.Fatalf("expected ObjectID filter, got %#v", filter)
	}
	updateDoc, ok := col.updateDoc.(bson.M)
	if !ok {
		t.Fatalf("update doc type mismatch: %T", col.updateDoc)
	}
	setDoc, ok := updateDoc["$set"].(bson.M)
	if !ok {
		t.Fatalf("$set payload missing or wrong type: %#v", updateDoc)
	}
	if setDoc["name"] != "updated" {
		t.Fatalf("unexpected set payload: %#v", setDoc)
	}
}

func TestUpdateIdentityDescription_InvalidJSON(t *testing.T) {
	col := &fakeMongoCollection{}
	withFakeMongo(t, col)
	req, _ := http.NewRequest(http.MethodPut, "/api/identities/x/description", bytes.NewBufferString("{"))
	req.Header.Set("Content-Type", "application/json")
	ctx, rec := apitesting.CreateGinTestContext(http.MethodPut, "/api/identities/x/description", req)
	ctx.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}

	UpdateIdentityDescription(ctx)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d", rec.Code)
	}
}

func TestUpdateIdentityDescription_SetsDescription(t *testing.T) {
	id := primitive.NewObjectID().Hex()
	col := &fakeMongoCollection{updateResult: &mongo.UpdateResult{MatchedCount: 1, ModifiedCount: 1}}
	withFakeMongo(t, col)
	req, _ := http.NewRequest(http.MethodPut, "/api/identities/"+id+"/description", bytes.NewBufferString(`{"description":"about me"}`))
	req.Header.Set("Content-Type", "application/json")
	ctx, rec := apitesting.CreateGinTestContext(http.MethodPut, "/api/identities/"+id+"/description", req)
	ctx.Params = gin.Params{{Key: "id", Value: id}}

	UpdateIdentityDescription(ctx)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", rec.Code)
	}
	updateDoc := col.updateDoc.(bson.M)
	setDoc := updateDoc["$set"].(bson.M)
	if setDoc["description"] != "about me" {
		t.Fatalf("unexpected set payload: %#v", setDoc)
	}
}

func TestUpdateIdentityName_InvalidJSON(t *testing.T) {
	col := &fakeMongoCollection{}
	withFakeMongo(t, col)
	req, _ := http.NewRequest(http.MethodPut, "/api/identities/x/name", bytes.NewBufferString("{"))
	req.Header.Set("Content-Type", "application/json")
	ctx, rec := apitesting.CreateGinTestContext(http.MethodPut, "/api/identities/x/name", req)
	ctx.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}

	UpdateIdentityName(ctx)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d", rec.Code)
	}
}

func TestUpdateIdentityName_SetsName(t *testing.T) {
	id := primitive.NewObjectID().Hex()
	col := &fakeMongoCollection{updateResult: &mongo.UpdateResult{MatchedCount: 1, ModifiedCount: 1}}
	withFakeMongo(t, col)
	req, _ := http.NewRequest(http.MethodPut, "/api/identities/"+id+"/name", bytes.NewBufferString(`{"name":"Fab"}`))
	req.Header.Set("Content-Type", "application/json")
	ctx, rec := apitesting.CreateGinTestContext(http.MethodPut, "/api/identities/"+id+"/name", req)
	ctx.Params = gin.Params{{Key: "id", Value: id}}

	UpdateIdentityName(ctx)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", rec.Code)
	}
	updateDoc := col.updateDoc.(bson.M)
	setDoc := updateDoc["$set"].(bson.M)
	if setDoc["name"] != "Fab" {
		t.Fatalf("unexpected set payload: %#v", setDoc)
	}
}

func TestUpdateIdentityRoles_InvalidJSON(t *testing.T) {
	col := &fakeMongoCollection{}
	withFakeMongo(t, col)
	req, _ := http.NewRequest(http.MethodPut, "/api/identities/x/roles", bytes.NewBufferString("{"))
	req.Header.Set("Content-Type", "application/json")
	ctx, rec := apitesting.CreateGinTestContext(http.MethodPut, "/api/identities/x/roles", req)
	ctx.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}

	UpdateIdentityRoles(ctx)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d", rec.Code)
	}
}

func TestUpdateIdentityRoles_SetsRoles(t *testing.T) {
	id := primitive.NewObjectID().Hex()
	col := &fakeMongoCollection{updateResult: &mongo.UpdateResult{MatchedCount: 1, ModifiedCount: 1}}
	withFakeMongo(t, col)
	req, _ := http.NewRequest(http.MethodPut, "/api/identities/"+id+"/roles", bytes.NewBufferString(`{"roles":["backend","platform"]}`))
	req.Header.Set("Content-Type", "application/json")
	ctx, rec := apitesting.CreateGinTestContext(http.MethodPut, "/api/identities/"+id+"/roles", req)
	ctx.Params = gin.Params{{Key: "id", Value: id}}

	UpdateIdentityRoles(ctx)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", rec.Code)
	}
	updateDoc := col.updateDoc.(bson.M)
	setDoc := updateDoc["$set"].(bson.M)
	roles, ok := setDoc["roles"].([]string)
	if !ok {
		t.Fatalf("roles type mismatch: %T", setDoc["roles"])
	}
	if len(roles) != 2 || roles[0] != "backend" || roles[1] != "platform" {
		t.Fatalf("unexpected roles payload: %#v", roles)
	}
}

func TestUpdateIdentitySignature_InvalidJSON(t *testing.T) {
	col := &fakeMongoCollection{}
	withFakeMongo(t, col)
	req, _ := http.NewRequest(http.MethodPut, "/api/identities/x/signature", bytes.NewBufferString("{"))
	req.Header.Set("Content-Type", "application/json")
	ctx, rec := apitesting.CreateGinTestContext(http.MethodPut, "/api/identities/x/signature", req)
	ctx.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}

	UpdateIdentitySignature(ctx)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d", rec.Code)
	}
}

func TestUpdateIdentitySignature_RejectsTooLarge(t *testing.T) {
	id := primitive.NewObjectID().Hex()
	col := &fakeMongoCollection{}
	withFakeMongo(t, col)
	tooLarge := bytes.Repeat([]byte("a"), 64*1024+1)
	payload := map[string]string{"html_signature": string(tooLarge)}
	raw, _ := json.Marshal(payload)
	req, _ := http.NewRequest(http.MethodPut, "/api/identities/"+id+"/signature", bytes.NewReader(raw))
	req.Header.Set("Content-Type", "application/json")
	ctx, rec := apitesting.CreateGinTestContext(http.MethodPut, "/api/identities/"+id+"/signature", req)
	ctx.Params = gin.Params{{Key: "id", Value: id}}

	UpdateIdentitySignature(ctx)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d", rec.Code)
	}
	response := decodeResponseMap(t, rec.Body.Bytes())
	if response["error"] != "Signature too large" {
		t.Fatalf("unexpected response: %#v", response)
	}
}

func TestUpdateIdentitySignature_AcceptsBoundarySize(t *testing.T) {
	id := primitive.NewObjectID().Hex()
	col := &fakeMongoCollection{updateResult: &mongo.UpdateResult{MatchedCount: 1, ModifiedCount: 1}}
	withFakeMongo(t, col)
	boundary := bytes.Repeat([]byte("a"), 64*1024)
	payload := map[string]string{"html_signature": string(boundary)}
	raw, _ := json.Marshal(payload)
	req, _ := http.NewRequest(http.MethodPut, "/api/identities/"+id+"/signature", bytes.NewReader(raw))
	req.Header.Set("Content-Type", "application/json")
	ctx, rec := apitesting.CreateGinTestContext(http.MethodPut, "/api/identities/"+id+"/signature", req)
	ctx.Params = gin.Params{{Key: "id", Value: id}}

	UpdateIdentitySignature(ctx)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", rec.Code)
	}
	updateDoc := col.updateDoc.(bson.M)
	setDoc := updateDoc["$set"].(bson.M)
	signature, ok := setDoc["html_signature"].(string)
	if !ok || len(signature) != 64*1024 {
		t.Fatalf("unexpected signature payload length: %d", len(signature))
	}
}

func TestUpdateIdentityPreferences_InvalidJSON(t *testing.T) {
	col := &fakeMongoCollection{}
	withFakeMongo(t, col)
	req, _ := http.NewRequest(http.MethodPut, "/api/identities/x/preferences", bytes.NewBufferString("{"))
	req.Header.Set("Content-Type", "application/json")
	ctx, rec := apitesting.CreateGinTestContext(http.MethodPut, "/api/identities/x/preferences", req)
	ctx.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}

	UpdateIdentityPreferences(ctx)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d", rec.Code)
	}
}

func TestUpdateIdentityPreferences_RequiresNonEmptyKey(t *testing.T) {
	id := primitive.NewObjectID().Hex()
	col := &fakeMongoCollection{}
	withFakeMongo(t, col)
	req, _ := http.NewRequest(http.MethodPut, "/api/identities/"+id+"/preferences", bytes.NewBufferString(`{"preferences":[{"key":"","weight":1,"enabled":true}]}`))
	req.Header.Set("Content-Type", "application/json")
	ctx, rec := apitesting.CreateGinTestContext(http.MethodPut, "/api/identities/"+id+"/preferences", req)
	ctx.Params = gin.Params{{Key: "id", Value: id}}

	UpdateIdentityPreferences(ctx)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d", rec.Code)
	}
	response := decodeResponseMap(t, rec.Body.Bytes())
	if response["error"] != "Preference key is required" {
		t.Fatalf("unexpected response: %#v", response)
	}
}

func TestUpdateIdentityPreferences_RejectsDuplicateKeys(t *testing.T) {
	id := primitive.NewObjectID().Hex()
	col := &fakeMongoCollection{}
	withFakeMongo(t, col)
	reqBody := `{"preferences":[{"key":"remote","weight":1,"enabled":true},{"key":"remote","weight":2,"enabled":false}]}`
	req, _ := http.NewRequest(http.MethodPut, "/api/identities/"+id+"/preferences", bytes.NewBufferString(reqBody))
	req.Header.Set("Content-Type", "application/json")
	ctx, rec := apitesting.CreateGinTestContext(http.MethodPut, "/api/identities/"+id+"/preferences", req)
	ctx.Params = gin.Params{{Key: "id", Value: id}}

	UpdateIdentityPreferences(ctx)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d", rec.Code)
	}
	response := decodeResponseMap(t, rec.Body.Bytes())
	if response["error"] != "Duplicate preference key" {
		t.Fatalf("unexpected response: %#v", response)
	}
}

func TestUpdateIdentityPreferences_ForwardsNormalizedPreferences(t *testing.T) {
	id := primitive.NewObjectID().Hex()
	col := &fakeMongoCollection{updateResult: &mongo.UpdateResult{MatchedCount: 1, ModifiedCount: 1}}
	withFakeMongo(t, col)
	reqBody := `{"preferences":[{"key":"remote","weight":2.5,"enabled":true,"guidance":"Prefer remote"},{"key":"backend","weight":1,"enabled":false,"guidance":""}]}`
	req, _ := http.NewRequest(http.MethodPut, "/api/identities/"+id+"/preferences", bytes.NewBufferString(reqBody))
	req.Header.Set("Content-Type", "application/json")
	ctx, rec := apitesting.CreateGinTestContext(http.MethodPut, "/api/identities/"+id+"/preferences", req)
	ctx.Params = gin.Params{{Key: "id", Value: id}}

	UpdateIdentityPreferences(ctx)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", rec.Code)
	}
	updateDoc := col.updateDoc.(bson.M)
	setDoc := updateDoc["$set"].(bson.M)
	prefs, ok := setDoc["preferences"].([]bson.M)
	if !ok {
		t.Fatalf("preferences type mismatch: %T", setDoc["preferences"])
	}
	if len(prefs) != 2 {
		t.Fatalf("expected 2 preferences, got %d", len(prefs))
	}
	if prefs[0]["key"] != "remote" || prefs[0]["guidance"] != "Prefer remote" {
		t.Fatalf("unexpected first preference: %#v", prefs[0])
	}
}

func TestAssociateFieldWithIdentity_InvalidJSON(t *testing.T) {
	col := &fakeMongoCollection{}
	withFakeMongo(t, col)
	req, _ := http.NewRequest(http.MethodPut, "/api/identities/x/field", bytes.NewBufferString("{"))
	req.Header.Set("Content-Type", "application/json")
	ctx, rec := apitesting.CreateGinTestContext(http.MethodPut, "/api/identities/x/field", req)
	ctx.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}

	AssociateFieldWithIdentity(ctx)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d", rec.Code)
	}
}

func TestAssociateFieldWithIdentity_InvalidFieldID(t *testing.T) {
	id := primitive.NewObjectID().Hex()
	col := &fakeMongoCollection{}
	withFakeMongo(t, col)
	req, _ := http.NewRequest(http.MethodPut, "/api/identities/"+id+"/field", bytes.NewBufferString(`{"fieldId":"bad-field-id"}`))
	req.Header.Set("Content-Type", "application/json")
	ctx, rec := apitesting.CreateGinTestContext(http.MethodPut, "/api/identities/"+id+"/field", req)
	ctx.Params = gin.Params{{Key: "id", Value: id}}

	AssociateFieldWithIdentity(ctx)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d", rec.Code)
	}
	response := decodeResponseMap(t, rec.Body.Bytes())
	if response["error"] != "Invalid Field ID" {
		t.Fatalf("unexpected response: %#v", response)
	}
}

func TestAssociateFieldWithIdentity_ConvertsFieldIDToObjectID(t *testing.T) {
	identityID := primitive.NewObjectID().Hex()
	fieldID := primitive.NewObjectID().Hex()
	col := &fakeMongoCollection{updateResult: &mongo.UpdateResult{MatchedCount: 1, ModifiedCount: 1}}
	withFakeMongo(t, col)
	req, _ := http.NewRequest(http.MethodPut, "/api/identities/"+identityID+"/field", bytes.NewBufferString(`{"fieldId":"`+fieldID+`"}`))
	req.Header.Set("Content-Type", "application/json")
	ctx, rec := apitesting.CreateGinTestContext(http.MethodPut, "/api/identities/"+identityID+"/field", req)
	ctx.Params = gin.Params{{Key: "id", Value: identityID}}

	AssociateFieldWithIdentity(ctx)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", rec.Code)
	}
	updateDoc := col.updateDoc.(bson.M)
	setDoc := updateDoc["$set"].(bson.M)
	if _, ok := setDoc["field_id"].(primitive.ObjectID); !ok {
		t.Fatalf("field_id should be ObjectID, got %T", setDoc["field_id"])
	}
}
