package companies

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
	dbs    map[string]*fakeMongoDatabase
	lastDB string
}

func (f *fakeMongoClient) Database(name string) MongoDatabaseIface {
	f.lastDB = name
	if f.dbs == nil {
		f.dbs = map[string]*fakeMongoDatabase{}
	}
	if db, ok := f.dbs[name]; ok {
		return db
	}
	db := &fakeMongoDatabase{collections: map[string]*fakeMongoCollection{}}
	f.dbs[name] = db
	return db
}

type fakeMongoDatabase struct {
	collections     map[string]*fakeMongoCollection
	lastCollection  string
}

func (f *fakeMongoDatabase) Collection(name string) MongoCollectionIface {
	f.lastCollection = name
	if f.collections == nil {
		f.collections = map[string]*fakeMongoCollection{}
	}
	if c, ok := f.collections[name]; ok {
		return c
	}
	c := &fakeMongoCollection{}
	f.collections[name] = c
	return c
}

type fakeMongoCollection struct {
	aggregateCursor MongoCursorIface
	aggregateErr    error
	insertResult    *mongo.InsertOneResult
	insertErr       error
	findOneResult   MongoSingleResultIface
	updateResult    *mongo.UpdateResult
	updateErr       error
	deleteResult    *mongo.DeleteResult
	deleteErr       error

	lastAggregatePipeline interface{}
	lastInsertDoc         interface{}
	lastFindOneFilter     interface{}
	lastUpdateFilter      interface{}
	lastUpdateDoc         interface{}
	lastDeleteFilter      interface{}
}

func (f *fakeMongoCollection) Aggregate(ctx context.Context, pipeline interface{}) (MongoCursorIface, error) {
	f.lastAggregatePipeline = pipeline
	if f.aggregateErr != nil {
		return nil, f.aggregateErr
	}
	if f.aggregateCursor != nil {
		return f.aggregateCursor, nil
	}
	return &fakeMongoCursor{}, nil
}

func (f *fakeMongoCollection) InsertOne(ctx context.Context, doc interface{}) (*mongo.InsertOneResult, error) {
	f.lastInsertDoc = doc
	if f.insertErr != nil {
		return nil, f.insertErr
	}
	if f.insertResult != nil {
		return f.insertResult, nil
	}
	return &mongo.InsertOneResult{InsertedID: primitive.NewObjectID()}, nil
}

func (f *fakeMongoCollection) FindOne(ctx context.Context, filter interface{}) MongoSingleResultIface {
	f.lastFindOneFilter = filter
	if f.findOneResult != nil {
		return f.findOneResult
	}
	return &fakeMongoSingleResult{err: mongo.ErrNoDocuments}
}

func (f *fakeMongoCollection) UpdateOne(ctx context.Context, filter interface{}, update interface{}) (*mongo.UpdateResult, error) {
	f.lastUpdateFilter = filter
	f.lastUpdateDoc = update
	if f.updateErr != nil {
		return nil, f.updateErr
	}
	if f.updateResult != nil {
		return f.updateResult, nil
	}
	return &mongo.UpdateResult{MatchedCount: 1, ModifiedCount: 1}, nil
}

func (f *fakeMongoCollection) DeleteOne(ctx context.Context, filter interface{}) (*mongo.DeleteResult, error) {
	f.lastDeleteFilter = filter
	if f.deleteErr != nil {
		return nil, f.deleteErr
	}
	if f.deleteResult != nil {
		return f.deleteResult, nil
	}
	return &mongo.DeleteResult{DeletedCount: 1}, nil
}

type fakeMongoCursor struct {
	docs      []bson.M
	index     int
	decodeErr error
	closed    bool
}

func (f *fakeMongoCursor) Next(ctx context.Context) bool {
	return f.index < len(f.docs)
}

func (f *fakeMongoCursor) Decode(v interface{}) error {
	if f.decodeErr != nil {
		return f.decodeErr
	}
	m, ok := v.(*bson.M)
	if !ok {
		return errors.New("decode target must be *bson.M")
	}
	*m = f.docs[f.index]
	f.index++
	return nil
}

func (f *fakeMongoCursor) Close(ctx context.Context) error {
	f.closed = true
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
	m, ok := v.(*bson.M)
	if !ok {
		return errors.New("decode target must be *bson.M")
	}
	*m = f.doc
	return nil
}

func withMongoClient(t *testing.T, client MongoClientIface) {
	t.Helper()
	orig := getMongoClient
	SetMongoClientProvider(func() MongoClientIface {
		return client
	})
	t.Cleanup(func() {
		getMongoClient = orig
	})
}

func makeJSONRequest(t *testing.T, method, path string, payload string) *http.Request {
	t.Helper()
	req, err := http.NewRequest(method, path, bytes.NewBufferString(payload))
	if err != nil {
		t.Fatalf("failed to create request: %v", err)
	}
	req.Header.Set("Content-Type", "application/json")
	return req
}

func responseMapFromRecorder(t *testing.T, body []byte) map[string]interface{} {
	t.Helper()
	var out map[string]interface{}
	if err := json.Unmarshal(body, &out); err != nil {
		t.Fatalf("failed to decode map response: %v", err)
	}
	return out
}

func responseArrayFromRecorder(t *testing.T, body []byte) []map[string]interface{} {
	t.Helper()
	var out []map[string]interface{}
	if err := json.Unmarshal(body, &out); err != nil {
		t.Fatalf("failed to decode array response: %v", err)
	}
	return out
}

func TestGetCompanies_AggregateError(t *testing.T) {
	companiesColl := &fakeMongoCollection{aggregateErr: errors.New("boom")}
	client := &fakeMongoClient{dbs: map[string]*fakeMongoDatabase{
		"cover_letter_global": {collections: map[string]*fakeMongoCollection{"companies": companiesColl}},
	}}
	withMongoClient(t, client)
	t.Setenv("DB_NAME", "cover_letter_global")

	c, w := apitesting.CreateGinTestContext(http.MethodGet, "/api/companies", nil)

	GetCompanies(c)

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("expected status %d, got %d", http.StatusInternalServerError, w.Code)
	}
	resp := responseMapFromRecorder(t, w.Body.Bytes())
	if got := resp["error"]; got != "Failed to fetch companies" {
		t.Fatalf("expected aggregate error message, got %v", got)
	}
}

func TestGetCompanies_DecodeError(t *testing.T) {
	cursor := &fakeMongoCursor{docs: []bson.M{{"_id": primitive.NewObjectID()}}, decodeErr: errors.New("decode")}
	companiesColl := &fakeMongoCollection{aggregateCursor: cursor}
	client := &fakeMongoClient{dbs: map[string]*fakeMongoDatabase{
		"cover_letter_global": {collections: map[string]*fakeMongoCollection{"companies": companiesColl}},
	}}
	withMongoClient(t, client)

	c, w := apitesting.CreateGinTestContext(http.MethodGet, "/api/companies", nil)

	GetCompanies(c)

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("expected status %d, got %d", http.StatusInternalServerError, w.Code)
	}
	resp := responseMapFromRecorder(t, w.Body.Bytes())
	if got := resp["error"]; got != "Failed to decode companies" {
		t.Fatalf("expected decode error message, got %v", got)
	}
}

func TestGetCompanies_SuccessNormalizesObjectIDs(t *testing.T) {
	companyID := primitive.NewObjectID()
	fieldID := primitive.NewObjectID()
	cursor := &fakeMongoCursor{docs: []bson.M{{
		"_id":      companyID,
		"name":     "Acme",
		"field_id": fieldID,
		"fieldInfo": bson.M{
			"_id":   fieldID,
			"field": "AI",
		},
	}}}
	companiesColl := &fakeMongoCollection{aggregateCursor: cursor}
	client := &fakeMongoClient{dbs: map[string]*fakeMongoDatabase{
		"cover_letter_global": {collections: map[string]*fakeMongoCollection{"companies": companiesColl}},
	}}
	withMongoClient(t, client)

	c, w := apitesting.CreateGinTestContext(http.MethodGet, "/api/companies", nil)

	GetCompanies(c)

	if w.Code != http.StatusOK {
		t.Fatalf("expected status %d, got %d", http.StatusOK, w.Code)
	}
	arr := responseArrayFromRecorder(t, w.Body.Bytes())
	if len(arr) != 1 {
		t.Fatalf("expected 1 company, got %d", len(arr))
	}
	got := arr[0]
	if got["id"] != companyID.Hex() {
		t.Fatalf("expected id %s, got %v", companyID.Hex(), got["id"])
	}
	if got["field_id"] != fieldID.Hex() {
		t.Fatalf("expected field_id %s, got %v", fieldID.Hex(), got["field_id"])
	}
	if _, ok := got["_id"]; ok {
		t.Fatalf("expected _id to be removed")
	}
	if _, ok := got["fieldInfo"]; ok {
		t.Fatalf("expected fieldInfo to be removed")
	}
	fieldInfo, ok := got["field_info"].(map[string]interface{})
	if !ok {
		t.Fatalf("expected field_info object, got %T", got["field_info"])
	}
	if fieldInfo["id"] != fieldID.Hex() {
		t.Fatalf("expected field_info.id %s, got %v", fieldID.Hex(), fieldInfo["id"])
	}
}

func TestGetCompanies_SuccessStringIDsPassThrough(t *testing.T) {
	cursor := &fakeMongoCursor{docs: []bson.M{{
		"_id":      "company-1",
		"name":     "Acme",
		"field_id": "field-1",
		"fieldInfo": bson.M{
			"_id":   "field-1",
			"field": "AI",
		},
	}}}
	companiesColl := &fakeMongoCollection{aggregateCursor: cursor}
	client := &fakeMongoClient{dbs: map[string]*fakeMongoDatabase{
		"cover_letter_global": {collections: map[string]*fakeMongoCollection{"companies": companiesColl}},
	}}
	withMongoClient(t, client)

	c, w := apitesting.CreateGinTestContext(http.MethodGet, "/api/companies", nil)

	GetCompanies(c)

	if w.Code != http.StatusOK {
		t.Fatalf("expected status %d, got %d", http.StatusOK, w.Code)
	}
	arr := responseArrayFromRecorder(t, w.Body.Bytes())
	if len(arr) != 1 {
		t.Fatalf("expected 1 company, got %d", len(arr))
	}
	got := arr[0]
	if got["id"] != "company-1" {
		t.Fatalf("expected id company-1, got %v", got["id"])
	}
	if got["field_id"] != "field-1" {
		t.Fatalf("expected field_id field-1, got %v", got["field_id"])
	}
	fieldInfo := got["field_info"].(map[string]interface{})
	if fieldInfo["id"] != "field-1" {
		t.Fatalf("expected field_info.id field-1, got %v", fieldInfo["id"])
	}
}

func TestGetCompanies_EmptyReturnsArray(t *testing.T) {
	companiesColl := &fakeMongoCollection{aggregateCursor: &fakeMongoCursor{docs: []bson.M{}}}
	client := &fakeMongoClient{dbs: map[string]*fakeMongoDatabase{
		"cover_letter_global": {collections: map[string]*fakeMongoCollection{"companies": companiesColl}},
	}}
	withMongoClient(t, client)

	c, w := apitesting.CreateGinTestContext(http.MethodGet, "/api/companies", nil)

	GetCompanies(c)

	if w.Code != http.StatusOK {
		t.Fatalf("expected status %d, got %d", http.StatusOK, w.Code)
	}
	arr := responseArrayFromRecorder(t, w.Body.Bytes())
	if len(arr) != 0 {
		t.Fatalf("expected empty array, got %d items", len(arr))
	}
}

func TestCreateCompany_InvalidJSON(t *testing.T) {
	req := makeJSONRequest(t, http.MethodPost, "/api/companies", "{")
	c, w := apitesting.CreateGinTestContext(http.MethodPost, "/api/companies", req)

	CreateCompany(c)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected status %d, got %d", http.StatusBadRequest, w.Code)
	}
	resp := responseMapFromRecorder(t, w.Body.Bytes())
	if got := resp["error"]; got != "Invalid request" {
		t.Fatalf("expected invalid request message, got %v", got)
	}
}

func TestCreateCompany_InvalidFieldID(t *testing.T) {
	req := makeJSONRequest(t, http.MethodPost, "/api/companies", `{"name":"Acme","description":"desc","field_id":"bad"}`)
	c, w := apitesting.CreateGinTestContext(http.MethodPost, "/api/companies", req)

	CreateCompany(c)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected status %d, got %d", http.StatusBadRequest, w.Code)
	}
	resp := responseMapFromRecorder(t, w.Body.Bytes())
	if got := resp["error"]; got != "Invalid field_id" {
		t.Fatalf("expected invalid field_id message, got %v", got)
	}
}

func TestCreateCompany_InsertError(t *testing.T) {
	companiesColl := &fakeMongoCollection{insertErr: errors.New("insert")}
	client := &fakeMongoClient{dbs: map[string]*fakeMongoDatabase{
		"cover_letter_global": {collections: map[string]*fakeMongoCollection{"companies": companiesColl}},
	}}
	withMongoClient(t, client)

	req := makeJSONRequest(t, http.MethodPost, "/api/companies", `{"name":"Acme","description":"desc"}`)
	c, w := apitesting.CreateGinTestContext(http.MethodPost, "/api/companies", req)

	CreateCompany(c)

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("expected status %d, got %d", http.StatusInternalServerError, w.Code)
	}
	resp := responseMapFromRecorder(t, w.Body.Bytes())
	if got := resp["error"]; got != "Failed to create company" {
		t.Fatalf("expected create error message, got %v", got)
	}
}

func TestCreateCompany_SuccessWithoutField(t *testing.T) {
	insertID := primitive.NewObjectID()
	companiesColl := &fakeMongoCollection{insertResult: &mongo.InsertOneResult{InsertedID: insertID}}
	client := &fakeMongoClient{dbs: map[string]*fakeMongoDatabase{
		"cover_letter_global": {collections: map[string]*fakeMongoCollection{"companies": companiesColl}},
	}}
	withMongoClient(t, client)

	req := makeJSONRequest(t, http.MethodPost, "/api/companies", `{"name":"Acme","description":"desc"}`)
	c, w := apitesting.CreateGinTestContext(http.MethodPost, "/api/companies", req)

	CreateCompany(c)

	if w.Code != http.StatusCreated {
		t.Fatalf("expected status %d, got %d", http.StatusCreated, w.Code)
	}
	resp := responseMapFromRecorder(t, w.Body.Bytes())
	if resp["id"] != insertID.Hex() {
		t.Fatalf("expected id %s, got %v", insertID.Hex(), resp["id"])
	}
	if _, ok := resp["field_info"]; ok {
		t.Fatalf("did not expect field_info in response")
	}
	insertedDoc := companiesColl.lastInsertDoc.(bson.M)
	if _, ok := insertedDoc["field_id"]; ok {
		t.Fatalf("did not expect field_id in inserted doc")
	}
}

func TestCreateCompany_SuccessWithFieldInfo(t *testing.T) {
	insertID := primitive.NewObjectID()
	fieldID := primitive.NewObjectID()
	companiesColl := &fakeMongoCollection{insertResult: &mongo.InsertOneResult{InsertedID: insertID}}
	fieldsColl := &fakeMongoCollection{findOneResult: &fakeMongoSingleResult{doc: bson.M{"field": "AI"}}}
	client := &fakeMongoClient{dbs: map[string]*fakeMongoDatabase{
		"cover_letter_global": {
			collections: map[string]*fakeMongoCollection{
				"companies": companiesColl,
				"fields":    fieldsColl,
			},
		},
	}}
	withMongoClient(t, client)

	payload := `{"name":"Acme","description":"desc","field_id":"` + fieldID.Hex() + `"}`
	req := makeJSONRequest(t, http.MethodPost, "/api/companies", payload)
	c, w := apitesting.CreateGinTestContext(http.MethodPost, "/api/companies", req)

	CreateCompany(c)

	if w.Code != http.StatusCreated {
		t.Fatalf("expected status %d, got %d", http.StatusCreated, w.Code)
	}
	resp := responseMapFromRecorder(t, w.Body.Bytes())
	fieldInfo := resp["field_info"].(map[string]interface{})
	if fieldInfo["id"] != fieldID.Hex() {
		t.Fatalf("expected field_info.id %s, got %v", fieldID.Hex(), fieldInfo["id"])
	}
	if fieldInfo["field"] != "AI" {
		t.Fatalf("expected field_info.field AI, got %v", fieldInfo["field"])
	}
	insertedDoc := companiesColl.lastInsertDoc.(bson.M)
	if insertedDoc["field_id"] != fieldID {
		t.Fatalf("expected inserted field_id %v, got %v", fieldID, insertedDoc["field_id"])
	}
}

func TestCreateCompany_FieldLookupFallback(t *testing.T) {
	insertID := primitive.NewObjectID()
	fieldID := primitive.NewObjectID()
	companiesColl := &fakeMongoCollection{insertResult: &mongo.InsertOneResult{InsertedID: insertID}}
	fieldsColl := &fakeMongoCollection{findOneResult: &fakeMongoSingleResult{err: mongo.ErrNoDocuments}}
	client := &fakeMongoClient{dbs: map[string]*fakeMongoDatabase{
		"cover_letter_global": {
			collections: map[string]*fakeMongoCollection{
				"companies": companiesColl,
				"fields":    fieldsColl,
			},
		},
	}}
	withMongoClient(t, client)

	payload := `{"name":"Acme","description":"desc","field_id":"` + fieldID.Hex() + `"}`
	req := makeJSONRequest(t, http.MethodPost, "/api/companies", payload)
	c, w := apitesting.CreateGinTestContext(http.MethodPost, "/api/companies", req)

	CreateCompany(c)

	if w.Code != http.StatusCreated {
		t.Fatalf("expected status %d, got %d", http.StatusCreated, w.Code)
	}
	resp := responseMapFromRecorder(t, w.Body.Bytes())
	fieldInfo := resp["field_info"].(map[string]interface{})
	if fieldInfo["id"] != fieldID.Hex() {
		t.Fatalf("expected fallback field_info.id %s, got %v", fieldID.Hex(), fieldInfo["id"])
	}
	if _, ok := fieldInfo["field"]; ok {
		t.Fatalf("did not expect field key when lookup fails")
	}
}

func TestUpdateCompany_InvalidID(t *testing.T) {
	req := makeJSONRequest(t, http.MethodPut, "/api/companies/bad", `{"name":"A","description":"d","field_id":"bad"}`)
	c, w := apitesting.CreateGinTestContext(http.MethodPut, "/api/companies/bad", req)
	c.Params = gin.Params{{Key: "id", Value: "bad"}}

	UpdateCompany(c)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected status %d, got %d", http.StatusBadRequest, w.Code)
	}
	resp := responseMapFromRecorder(t, w.Body.Bytes())
	if got := resp["error"]; got != "Invalid ID" {
		t.Fatalf("expected invalid id message, got %v", got)
	}
}

func TestUpdateCompany_InvalidJSON(t *testing.T) {
	companyID := primitive.NewObjectID()
	req := makeJSONRequest(t, http.MethodPut, "/api/companies/"+companyID.Hex(), "{")
	c, w := apitesting.CreateGinTestContext(http.MethodPut, "/api/companies/"+companyID.Hex(), req)
	c.Params = gin.Params{{Key: "id", Value: companyID.Hex()}}

	UpdateCompany(c)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected status %d, got %d", http.StatusBadRequest, w.Code)
	}
	resp := responseMapFromRecorder(t, w.Body.Bytes())
	if got := resp["error"]; got != "Invalid request" {
		t.Fatalf("expected invalid request message, got %v", got)
	}
}

func TestUpdateCompany_InvalidFieldID(t *testing.T) {
	companyID := primitive.NewObjectID()
	req := makeJSONRequest(t, http.MethodPut, "/api/companies/"+companyID.Hex(), `{"name":"A","description":"d","field_id":"bad"}`)
	c, w := apitesting.CreateGinTestContext(http.MethodPut, "/api/companies/"+companyID.Hex(), req)
	c.Params = gin.Params{{Key: "id", Value: companyID.Hex()}}

	UpdateCompany(c)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected status %d, got %d", http.StatusBadRequest, w.Code)
	}
	resp := responseMapFromRecorder(t, w.Body.Bytes())
	if got := resp["error"]; got != "Invalid field_id" {
		t.Fatalf("expected invalid field_id message, got %v", got)
	}
}

func TestUpdateCompany_UpdateError(t *testing.T) {
	companyID := primitive.NewObjectID()
	fieldID := primitive.NewObjectID()
	companiesColl := &fakeMongoCollection{updateErr: errors.New("update")}
	client := &fakeMongoClient{dbs: map[string]*fakeMongoDatabase{
		"cover_letter_global": {collections: map[string]*fakeMongoCollection{"companies": companiesColl}},
	}}
	withMongoClient(t, client)

	payload := `{"name":"A","description":"d","field_id":"` + fieldID.Hex() + `"}`
	req := makeJSONRequest(t, http.MethodPut, "/api/companies/"+companyID.Hex(), payload)
	c, w := apitesting.CreateGinTestContext(http.MethodPut, "/api/companies/"+companyID.Hex(), req)
	c.Params = gin.Params{{Key: "id", Value: companyID.Hex()}}

	UpdateCompany(c)

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("expected status %d, got %d", http.StatusInternalServerError, w.Code)
	}
	resp := responseMapFromRecorder(t, w.Body.Bytes())
	if got := resp["error"]; got != "Failed to update company" {
		t.Fatalf("expected update error message, got %v", got)
	}
}

func TestUpdateCompany_NotFound(t *testing.T) {
	companyID := primitive.NewObjectID()
	fieldID := primitive.NewObjectID()
	companiesColl := &fakeMongoCollection{updateResult: &mongo.UpdateResult{MatchedCount: 0}}
	client := &fakeMongoClient{dbs: map[string]*fakeMongoDatabase{
		"cover_letter_global": {collections: map[string]*fakeMongoCollection{"companies": companiesColl}},
	}}
	withMongoClient(t, client)

	payload := `{"name":"A","description":"d","field_id":"` + fieldID.Hex() + `"}`
	req := makeJSONRequest(t, http.MethodPut, "/api/companies/"+companyID.Hex(), payload)
	c, w := apitesting.CreateGinTestContext(http.MethodPut, "/api/companies/"+companyID.Hex(), req)
	c.Params = gin.Params{{Key: "id", Value: companyID.Hex()}}

	UpdateCompany(c)

	if w.Code != http.StatusNotFound {
		t.Fatalf("expected status %d, got %d", http.StatusNotFound, w.Code)
	}
	resp := responseMapFromRecorder(t, w.Body.Bytes())
	if got := resp["error"]; got != "Company not found" {
		t.Fatalf("expected not found message, got %v", got)
	}
}

func TestUpdateCompany_SuccessAndUpdatePayload(t *testing.T) {
	companyID := primitive.NewObjectID()
	fieldID := primitive.NewObjectID()
	companiesColl := &fakeMongoCollection{updateResult: &mongo.UpdateResult{MatchedCount: 1, ModifiedCount: 1}}
	client := &fakeMongoClient{dbs: map[string]*fakeMongoDatabase{
		"cover_letter_global": {collections: map[string]*fakeMongoCollection{"companies": companiesColl}},
	}}
	withMongoClient(t, client)

	payload := `{"name":"A","description":"d","field_id":"` + fieldID.Hex() + `"}`
	req := makeJSONRequest(t, http.MethodPut, "/api/companies/"+companyID.Hex(), payload)
	c, w := apitesting.CreateGinTestContext(http.MethodPut, "/api/companies/"+companyID.Hex(), req)
	c.Params = gin.Params{{Key: "id", Value: companyID.Hex()}}

	UpdateCompany(c)

	if w.Code != http.StatusOK {
		t.Fatalf("expected status %d, got %d", http.StatusOK, w.Code)
	}
	resp := responseMapFromRecorder(t, w.Body.Bytes())
	if resp["message"] != "Company updated successfully" {
		t.Fatalf("expected success message, got %v", resp["message"])
	}

	filter := companiesColl.lastUpdateFilter.(bson.M)
	if filter["_id"] != companyID {
		t.Fatalf("expected filter _id %v, got %v", companyID, filter["_id"])
	}
	update := companiesColl.lastUpdateDoc.(bson.M)
	set := update["$set"].(bson.M)
	if set["name"] != "A" || set["description"] != "d" {
		t.Fatalf("unexpected set payload: %v", set)
	}
	if set["field_id"] != fieldID {
		t.Fatalf("expected field_id %v, got %v", fieldID, set["field_id"])
	}
}

func TestAssociateFieldWithCompany_InvalidCompanyID(t *testing.T) {
	req := makeJSONRequest(t, http.MethodPut, "/api/companies/bad/field", `{"field_id":null}`)
	c, w := apitesting.CreateGinTestContext(http.MethodPut, "/api/companies/bad/field", req)
	c.Params = gin.Params{{Key: "id", Value: "bad"}}

	AssociateFieldWithCompany(c)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected status %d, got %d", http.StatusBadRequest, w.Code)
	}
	resp := responseMapFromRecorder(t, w.Body.Bytes())
	if got := resp["error"]; got != "Invalid company ID" {
		t.Fatalf("expected invalid company id message, got %v", got)
	}
}

func TestAssociateFieldWithCompany_InvalidJSON(t *testing.T) {
	companyID := primitive.NewObjectID()
	req := makeJSONRequest(t, http.MethodPut, "/api/companies/"+companyID.Hex()+"/field", "{")
	c, w := apitesting.CreateGinTestContext(http.MethodPut, "/api/companies/"+companyID.Hex()+"/field", req)
	c.Params = gin.Params{{Key: "id", Value: companyID.Hex()}}

	AssociateFieldWithCompany(c)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected status %d, got %d", http.StatusBadRequest, w.Code)
	}
	resp := responseMapFromRecorder(t, w.Body.Bytes())
	if got := resp["error"]; got != "Invalid request" {
		t.Fatalf("expected invalid request message, got %v", got)
	}
}

func TestAssociateFieldWithCompany_InvalidFieldID(t *testing.T) {
	companyID := primitive.NewObjectID()
	companiesColl := &fakeMongoCollection{}
	client := &fakeMongoClient{dbs: map[string]*fakeMongoDatabase{
		"cover_letter_global": {collections: map[string]*fakeMongoCollection{"companies": companiesColl}},
	}}
	withMongoClient(t, client)

	req := makeJSONRequest(t, http.MethodPut, "/api/companies/"+companyID.Hex()+"/field", `{"field_id":"bad"}`)
	c, w := apitesting.CreateGinTestContext(http.MethodPut, "/api/companies/"+companyID.Hex()+"/field", req)
	c.Params = gin.Params{{Key: "id", Value: companyID.Hex()}}

	AssociateFieldWithCompany(c)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected status %d, got %d", http.StatusBadRequest, w.Code)
	}
	resp := responseMapFromRecorder(t, w.Body.Bytes())
	if got := resp["error"]; got != "Invalid field_id" {
		t.Fatalf("expected invalid field_id message, got %v", got)
	}
}

func TestAssociateFieldWithCompany_UpdateError(t *testing.T) {
	companyID := primitive.NewObjectID()
	companiesColl := &fakeMongoCollection{updateErr: errors.New("update")}
	client := &fakeMongoClient{dbs: map[string]*fakeMongoDatabase{
		"cover_letter_global": {collections: map[string]*fakeMongoCollection{"companies": companiesColl}},
	}}
	withMongoClient(t, client)

	req := makeJSONRequest(t, http.MethodPut, "/api/companies/"+companyID.Hex()+"/field", `{"field_id":null}`)
	c, w := apitesting.CreateGinTestContext(http.MethodPut, "/api/companies/"+companyID.Hex()+"/field", req)
	c.Params = gin.Params{{Key: "id", Value: companyID.Hex()}}

	AssociateFieldWithCompany(c)

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("expected status %d, got %d", http.StatusInternalServerError, w.Code)
	}
	resp := responseMapFromRecorder(t, w.Body.Bytes())
	if got := resp["error"]; got != "Failed to associate field with company" {
		t.Fatalf("expected associate error message, got %v", got)
	}
}

func TestAssociateFieldWithCompany_UnsetWhenNull(t *testing.T) {
	companyID := primitive.NewObjectID()
	companiesColl := &fakeMongoCollection{updateResult: &mongo.UpdateResult{ModifiedCount: 2}}
	client := &fakeMongoClient{dbs: map[string]*fakeMongoDatabase{
		"cover_letter_global": {collections: map[string]*fakeMongoCollection{"companies": companiesColl}},
	}}
	withMongoClient(t, client)

	req := makeJSONRequest(t, http.MethodPut, "/api/companies/"+companyID.Hex()+"/field", `{"field_id":null}`)
	c, w := apitesting.CreateGinTestContext(http.MethodPut, "/api/companies/"+companyID.Hex()+"/field", req)
	c.Params = gin.Params{{Key: "id", Value: companyID.Hex()}}

	AssociateFieldWithCompany(c)

	if w.Code != http.StatusOK {
		t.Fatalf("expected status %d, got %d", http.StatusOK, w.Code)
	}
	resp := responseMapFromRecorder(t, w.Body.Bytes())
	if resp["message"] != "Field associated successfully" {
		t.Fatalf("expected success message, got %v", resp["message"])
	}
	if resp["modifiedCount"] != float64(2) {
		t.Fatalf("expected modifiedCount 2, got %v", resp["modifiedCount"])
	}
	update := companiesColl.lastUpdateDoc.(bson.M)
	unset := update["$unset"].(bson.M)
	if unset["field_id"] != "" {
		t.Fatalf("expected unset field_id empty string, got %v", unset["field_id"])
	}
}

func TestAssociateFieldWithCompany_UnsetWhenEmptyString(t *testing.T) {
	companyID := primitive.NewObjectID()
	companiesColl := &fakeMongoCollection{updateResult: &mongo.UpdateResult{ModifiedCount: 1}}
	client := &fakeMongoClient{dbs: map[string]*fakeMongoDatabase{
		"cover_letter_global": {collections: map[string]*fakeMongoCollection{"companies": companiesColl}},
	}}
	withMongoClient(t, client)

	req := makeJSONRequest(t, http.MethodPut, "/api/companies/"+companyID.Hex()+"/field", `{"field_id":""}`)
	c, w := apitesting.CreateGinTestContext(http.MethodPut, "/api/companies/"+companyID.Hex()+"/field", req)
	c.Params = gin.Params{{Key: "id", Value: companyID.Hex()}}

	AssociateFieldWithCompany(c)

	if w.Code != http.StatusOK {
		t.Fatalf("expected status %d, got %d", http.StatusOK, w.Code)
	}
	update := companiesColl.lastUpdateDoc.(bson.M)
	if _, ok := update["$unset"]; !ok {
		t.Fatalf("expected $unset update, got %v", update)
	}
}

func TestAssociateFieldWithCompany_SetWhenValidFieldID(t *testing.T) {
	companyID := primitive.NewObjectID()
	fieldID := primitive.NewObjectID()
	companiesColl := &fakeMongoCollection{updateResult: &mongo.UpdateResult{ModifiedCount: 1}}
	client := &fakeMongoClient{dbs: map[string]*fakeMongoDatabase{
		"cover_letter_global": {collections: map[string]*fakeMongoCollection{"companies": companiesColl}},
	}}
	withMongoClient(t, client)

	req := makeJSONRequest(t, http.MethodPut, "/api/companies/"+companyID.Hex()+"/field", `{"field_id":"`+fieldID.Hex()+`"}`)
	c, w := apitesting.CreateGinTestContext(http.MethodPut, "/api/companies/"+companyID.Hex()+"/field", req)
	c.Params = gin.Params{{Key: "id", Value: companyID.Hex()}}

	AssociateFieldWithCompany(c)

	if w.Code != http.StatusOK {
		t.Fatalf("expected status %d, got %d", http.StatusOK, w.Code)
	}
	update := companiesColl.lastUpdateDoc.(bson.M)
	set := update["$set"].(bson.M)
	if set["field_id"] != fieldID {
		t.Fatalf("expected set field_id %v, got %v", fieldID, set["field_id"])
	}
}

func TestDeleteCompany_InvalidID(t *testing.T) {
	c, w := apitesting.CreateGinTestContext(http.MethodDelete, "/api/companies/bad", nil)
	c.Params = gin.Params{{Key: "id", Value: "bad"}}

	DeleteCompany(c)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected status %d, got %d", http.StatusBadRequest, w.Code)
	}
	resp := responseMapFromRecorder(t, w.Body.Bytes())
	if got := resp["error"]; got != "Invalid ID" {
		t.Fatalf("expected invalid id message, got %v", got)
	}
}

func TestDeleteCompany_DeleteError(t *testing.T) {
	companyID := primitive.NewObjectID()
	companiesColl := &fakeMongoCollection{deleteErr: errors.New("delete")}
	client := &fakeMongoClient{dbs: map[string]*fakeMongoDatabase{
		"cover_letter_global": {collections: map[string]*fakeMongoCollection{"companies": companiesColl}},
	}}
	withMongoClient(t, client)

	c, w := apitesting.CreateGinTestContext(http.MethodDelete, "/api/companies/"+companyID.Hex(), nil)
	c.Params = gin.Params{{Key: "id", Value: companyID.Hex()}}

	DeleteCompany(c)

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("expected status %d, got %d", http.StatusInternalServerError, w.Code)
	}
	resp := responseMapFromRecorder(t, w.Body.Bytes())
	if got := resp["error"]; got != "Failed to delete company" {
		t.Fatalf("expected delete error message, got %v", got)
	}
}

func TestDeleteCompany_NotFound(t *testing.T) {
	companyID := primitive.NewObjectID()
	companiesColl := &fakeMongoCollection{deleteResult: &mongo.DeleteResult{DeletedCount: 0}}
	client := &fakeMongoClient{dbs: map[string]*fakeMongoDatabase{
		"cover_letter_global": {collections: map[string]*fakeMongoCollection{"companies": companiesColl}},
	}}
	withMongoClient(t, client)

	c, w := apitesting.CreateGinTestContext(http.MethodDelete, "/api/companies/"+companyID.Hex(), nil)
	c.Params = gin.Params{{Key: "id", Value: companyID.Hex()}}

	DeleteCompany(c)

	if w.Code != http.StatusNotFound {
		t.Fatalf("expected status %d, got %d", http.StatusNotFound, w.Code)
	}
	resp := responseMapFromRecorder(t, w.Body.Bytes())
	if got := resp["error"]; got != "Company not found" {
		t.Fatalf("expected not found message, got %v", got)
	}
}

func TestDeleteCompany_Success(t *testing.T) {
	companyID := primitive.NewObjectID()
	companiesColl := &fakeMongoCollection{deleteResult: &mongo.DeleteResult{DeletedCount: 1}}
	client := &fakeMongoClient{dbs: map[string]*fakeMongoDatabase{
		"cover_letter_global": {collections: map[string]*fakeMongoCollection{"companies": companiesColl}},
	}}
	withMongoClient(t, client)

	c, w := apitesting.CreateGinTestContext(http.MethodDelete, "/api/companies/"+companyID.Hex(), nil)
	c.Params = gin.Params{{Key: "id", Value: companyID.Hex()}}

	DeleteCompany(c)

	if w.Code != http.StatusOK {
		t.Fatalf("expected status %d, got %d", http.StatusOK, w.Code)
	}
	resp := responseMapFromRecorder(t, w.Body.Bytes())
	if resp["message"] != "Company deleted successfully" {
		t.Fatalf("expected success message, got %v", resp["message"])
	}
}