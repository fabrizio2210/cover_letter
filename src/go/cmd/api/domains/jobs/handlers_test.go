package jobs

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"testing"
	"time"

	"github.com/fabrizio2210/cover_letter/src/go/cmd/api/models"
	apptest "github.com/fabrizio2210/cover_letter/src/go/cmd/api/testing"
	"github.com/gin-gonic/gin"
	"github.com/go-redis/redis/v8"
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/bson/primitive"
	"go.mongodb.org/mongo-driver/mongo"
	"google.golang.org/protobuf/types/known/timestamppb"
)

type aggregateResult struct {
	cursor MongoCursorIface
	err    error
}

type mockMongoClient struct {
	collections map[string]*mockMongoCollection
}

func (m *mockMongoClient) Database(name string) MongoDatabaseIface {
	return &mockMongoDatabase{collections: m.collections}
}

type mockMongoDatabase struct {
	collections map[string]*mockMongoCollection
}

func (m *mockMongoDatabase) Collection(name string) MongoCollectionIface {
	if col, ok := m.collections[name]; ok {
		return col
	}
	col := &mockMongoCollection{}
	m.collections[name] = col
	return col
}

type mockMongoCollection struct {
	aggregateResults []aggregateResult
	aggregateCursor  MongoCursorIface
	aggregateErr     error
	insertResult     *mongo.InsertOneResult
	insertErr        error
	findOneResult    MongoSingleResultIface
	updateResult     *mongo.UpdateResult
	updateErr        error
	deleteResult     *mongo.DeleteResult
	deleteErr        error

	aggregateCalls int

	lastAggregatePipeline interface{}
	lastInsertDoc         interface{}
	lastFindOneFilter     interface{}
	lastUpdateFilter      interface{}
	lastUpdateDoc         interface{}
	lastDeleteFilter      interface{}
}

func (m *mockMongoCollection) Aggregate(_ context.Context, pipeline interface{}) (MongoCursorIface, error) {
	m.aggregateCalls++
	m.lastAggregatePipeline = pipeline
	if len(m.aggregateResults) > 0 {
		res := m.aggregateResults[0]
		m.aggregateResults = m.aggregateResults[1:]
		return res.cursor, res.err
	}
	return m.aggregateCursor, m.aggregateErr
}

func (m *mockMongoCollection) InsertOne(_ context.Context, doc interface{}) (*mongo.InsertOneResult, error) {
	m.lastInsertDoc = doc
	return m.insertResult, m.insertErr
}

func (m *mockMongoCollection) FindOne(_ context.Context, filter interface{}) MongoSingleResultIface {
	m.lastFindOneFilter = filter
	return m.findOneResult
}

func (m *mockMongoCollection) UpdateOne(_ context.Context, filter interface{}, update interface{}) (*mongo.UpdateResult, error) {
	m.lastUpdateFilter = filter
	m.lastUpdateDoc = update
	return m.updateResult, m.updateErr
}

func (m *mockMongoCollection) DeleteOne(_ context.Context, filter interface{}) (*mongo.DeleteResult, error) {
	m.lastDeleteFilter = filter
	return m.deleteResult, m.deleteErr
}

type mockMongoCursor struct {
	docs      []bson.M
	index     int
	decodeErr error
	allErr    error
}

func (m *mockMongoCursor) All(_ context.Context, result interface{}) error {
	if m.allErr != nil {
		return m.allErr
	}
	target, ok := result.(*[]bson.M)
	if ok {
		*target = append((*target)[:0], m.docs...)
	}
	return nil
}

func (m *mockMongoCursor) Next(_ context.Context) bool {
	return m.index < len(m.docs)
}

func (m *mockMongoCursor) Decode(v interface{}) error {
	if m.decodeErr != nil {
		return m.decodeErr
	}
	if m.index >= len(m.docs) {
		return errors.New("no more items")
	}
	target, ok := v.(*bson.M)
	if !ok {
		return errors.New("unsupported decode target type")
	}
	*target = m.docs[m.index]
	m.index++
	return nil
}

func (m *mockMongoCursor) Close(_ context.Context) error { return nil }

type mockMongoSingleResult struct {
	doc bson.M
	err error
}

func (m *mockMongoSingleResult) Decode(v interface{}) error {
	if m.err != nil {
		return m.err
	}
	target, ok := v.(*bson.M)
	if !ok {
		return errors.New("unsupported decode target type")
	}
	*target = m.doc
	return nil
}

func setMockClient(col map[string]*mockMongoCollection) func() {
	orig := getMongoClient
	SetMongoClientProvider(func() MongoClientIface {
		return &mockMongoClient{collections: col}
	})
	return func() { getMongoClient = orig }
}

func setQueuePushProviderForTest(provider func(ctx context.Context, queueName string, payload []byte) error) func() {
	orig := queuePush
	SetQueuePushProvider(provider)
	return func() { queuePush = orig }
}

func setSubscribeChannelProviderForTest(provider func(ctx context.Context, channelName string) (<-chan *redis.Message, func() error)) func() {
	orig := subscribeChannel
	SetSubscribeChannelProvider(provider)
	return func() { subscribeChannel = orig }
}

func newJSONRequest(t *testing.T, method, path string, body interface{}) *http.Request {
	t.Helper()
	b, err := json.Marshal(body)
	if err != nil {
		t.Fatalf("marshal body: %v", err)
	}
	req, err := http.NewRequest(method, path, bytes.NewBuffer(b))
	if err != nil {
		t.Fatalf("new request: %v", err)
	}
	req.Header.Set("Content-Type", "application/json")
	return req
}

func decodeBodyMap(t *testing.T, payload []byte) map[string]interface{} {
	t.Helper()
	var out map[string]interface{}
	if err := json.Unmarshal(payload, &out); err != nil {
		t.Fatalf("decode body map: %v", err)
	}
	return out
}

func decodeBodySliceMap(t *testing.T, payload []byte) []map[string]interface{} {
	t.Helper()
	var out []map[string]interface{}
	if err := json.Unmarshal(payload, &out); err != nil {
		t.Fatalf("decode body slice: %v", err)
	}
	return out
}

func waitUntil(timeout time.Duration, fn func() bool) bool {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if fn() {
			return true
		}
		time.Sleep(10 * time.Millisecond)
	}
	return fn()
}

func TestNormalizeObjectIDValue(t *testing.T) {
	oid := primitive.NewObjectID()
	if got, ok := normalizeObjectIDValue(oid); !ok || got != oid.Hex() {
		t.Fatalf("expected object id hex, got ok=%v value=%q", ok, got)
	}
	if got, ok := normalizeObjectIDValue("abc"); !ok || got != "abc" {
		t.Fatalf("expected string passthrough, got ok=%v value=%q", ok, got)
	}
	if got, ok := normalizeObjectIDValue(10); ok || got != "" {
		t.Fatalf("expected unsupported type to fail, got ok=%v value=%q", ok, got)
	}
}

func TestNormalizeCompanyInfoMap(t *testing.T) {
	companyID := primitive.NewObjectID()
	fieldID := primitive.NewObjectID()
	fieldInfoID := primitive.NewObjectID()
	raw := bson.M{
		"_id":      companyID,
		"field_id": fieldID,
		"fieldInfo": bson.M{
			"_id": fieldInfoID,
		},
	}
	got, ok := normalizeCompanyInfoMap(raw)
	if !ok {
		t.Fatal("expected normalizeCompanyInfoMap to succeed")
	}
	if got["id"] != companyID.Hex() {
		t.Fatalf("expected company id normalization, got %v", got["id"])
	}
	if got["field_id"] != fieldID.Hex() {
		t.Fatalf("expected field_id normalization, got %v", got["field_id"])
	}
	fieldInfo, ok := got["field_info"].(bson.M)
	if !ok {
		t.Fatalf("expected field_info bson.M, got %T", got["field_info"])
	}
	if fieldInfo["id"] != fieldInfoID.Hex() {
		t.Fatalf("expected nested field id normalization, got %v", fieldInfo["id"])
	}
}

func TestNormalizeScoreDoc(t *testing.T) {
	scoreID := primitive.NewObjectID()
	jobID := primitive.NewObjectID()
	identityID := primitive.NewObjectID()
	doc := bson.M{
		"_id":         scoreID,
		"job_id":      jobID,
		"identity_id": identityID,
		"preference_scores": bson.A{
			bson.M{"preference_key": "remote"},
		},
	}

	normalizeScoreDoc(doc)

	if doc["id"] != scoreID.Hex() {
		t.Fatalf("expected score id hex, got %v", doc["id"])
	}
	if doc["job_id"] != jobID.Hex() {
		t.Fatalf("expected job id hex, got %v", doc["job_id"])
	}
	if doc["identity_id"] != identityID.Hex() {
		t.Fatalf("expected identity id hex, got %v", doc["identity_id"])
	}
	list, ok := doc["preference_scores"].([]bson.M)
	if !ok || len(list) != 1 {
		t.Fatalf("expected normalized preference_scores list, got %#v", doc["preference_scores"])
	}
}

func TestNormalizeJobDoc(t *testing.T) {
	jobID := primitive.NewObjectID()
	companyID := primitive.NewObjectID()
	doc := bson.M{
		"_id":     jobID,
		"company": companyID,
		"companyInfo": bson.M{
			"_id": primitive.NewObjectID(),
		},
	}

	normalizeJobDoc(doc)

	if doc["id"] != jobID.Hex() {
		t.Fatalf("expected job id to be normalized, got %v", doc["id"])
	}
	if doc["company_id"] != companyID.Hex() {
		t.Fatalf("expected company_id to be normalized, got %v", doc["company_id"])
	}
	if _, ok := doc["company_info"]; !ok {
		t.Fatalf("expected company_info to be present: %#v", doc)
	}
}

func TestCollectionHasDocuments(t *testing.T) {
	col := &mockMongoCollection{
		aggregateCursor: &mockMongoCursor{docs: []bson.M{{"_id": primitive.NewObjectID()}}},
	}
	if !collectionHasDocuments(col) {
		t.Fatal("expected collection to have documents")
	}

	colEmpty := &mockMongoCollection{
		aggregateCursor: &mockMongoCursor{docs: []bson.M{}},
	}
	if collectionHasDocuments(colEmpty) {
		t.Fatal("expected collection to be empty")
	}

	colErr := &mockMongoCollection{aggregateErr: errors.New("boom")}
	if collectionHasDocuments(colErr) {
		t.Fatal("expected aggregate error to return false")
	}
}

func TestJobDescriptionsCollection_ReturnsPrimary(t *testing.T) {
	jobDescriptions := &mockMongoCollection{aggregateCursor: &mockMongoCursor{docs: []bson.M{{"_id": primitive.NewObjectID()}}}}
	cleanup := setMockClient(map[string]*mockMongoCollection{
		"job-descriptions": jobDescriptions,
	})
	defer cleanup()

	col, _, _ := jobDescriptionsCollection()
	if col != jobDescriptions {
		t.Fatal("expected job-descriptions collection to be returned")
	}
}

func TestLoadNormalizedScoreDocs(t *testing.T) {
	jobID := primitive.NewObjectID()
	identityID := primitive.NewObjectID()
	col := &mockMongoCollection{
		aggregateCursor: &mockMongoCursor{docs: []bson.M{{
			"_id":               primitive.NewObjectID(),
			"job_id":            jobID,
			"identity_id":       identityID,
			"preference_scores": bson.A{bson.M{"preference_key": "remote"}},
		}}},
	}

	out, err := loadNormalizedScoreDocs(col, bson.M{"job_id": jobID.Hex()})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(out) != 1 {
		t.Fatalf("expected one score doc, got %d", len(out))
	}
	if out[0]["job_id"] != jobID.Hex() {
		t.Fatalf("expected normalized job_id, got %v", out[0]["job_id"])
	}

	pipeline, ok := col.lastAggregatePipeline.(bson.A)
	if !ok || len(pipeline) != 1 {
		t.Fatalf("expected one-stage pipeline with match, got %#v", col.lastAggregatePipeline)
	}
}

func TestGetJobPreferenceScores_InvalidIDs(t *testing.T) {
	c, w := apptest.CreateGinTestContext(http.MethodGet, "/api/job-preference-scores?job_id=bad", nil)
	GetJobPreferenceScores(c)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for invalid job_id, got %d", w.Code)
	}

	c2, w2 := apptest.CreateGinTestContext(http.MethodGet, "/api/job-preference-scores?identity_id=bad", nil)
	GetJobPreferenceScores(c2)
	if w2.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for invalid identity_id, got %d", w2.Code)
	}
}

func TestGetJobPreferenceScores_SuccessAndMatchPipeline(t *testing.T) {
	jobID := primitive.NewObjectID()
	identityID := primitive.NewObjectID()
	scoreCol := &mockMongoCollection{
		aggregateCursor: &mockMongoCursor{docs: []bson.M{{
			"_id":               primitive.NewObjectID(),
			"job_id":            jobID,
			"identity_id":       identityID,
			"preference_scores": bson.A{bson.M{"preference_key": "impact"}},
		}}},
	}
	cleanup := setMockClient(map[string]*mockMongoCollection{"job-preference-scores": scoreCol})
	defer cleanup()

	url := "/api/job-preference-scores?job_id=" + jobID.Hex() + "&identity_id=" + identityID.Hex()
	c, w := apptest.CreateGinTestContext(http.MethodGet, url, nil)
	GetJobPreferenceScores(c)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	out := decodeBodySliceMap(t, w.Body.Bytes())
	if len(out) != 1 {
		t.Fatalf("expected one item, got %d", len(out))
	}
	if out[0]["job_id"] != jobID.Hex() {
		t.Fatalf("expected response to include normalized job_id, got %v", out[0]["job_id"])
	}

	pipeline, ok := scoreCol.lastAggregatePipeline.(bson.A)
	if !ok || len(pipeline) != 1 {
		t.Fatalf("expected aggregate match pipeline, got %#v", scoreCol.lastAggregatePipeline)
	}
	matchStage, ok := pipeline[0].(bson.M)
	if !ok {
		t.Fatalf("expected bson.M match stage, got %T", pipeline[0])
	}
	match, ok := matchStage["$match"].(bson.M)
	if !ok {
		t.Fatalf("expected $match bson.M, got %#v", matchStage)
	}
	if match["job_id"] != jobID.Hex() || match["identity_id"] != identityID.Hex() {
		t.Fatalf("unexpected match stage: %#v", match)
	}
}

func TestGetJobPreferenceScores_AggregateError(t *testing.T) {
	scoreCol := &mockMongoCollection{aggregateErr: errors.New("aggregate failed")}
	cleanup := setMockClient(map[string]*mockMongoCollection{"job-preference-scores": scoreCol})
	defer cleanup()

	c, w := apptest.CreateGinTestContext(http.MethodGet, "/api/job-preference-scores", nil)
	GetJobPreferenceScores(c)

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("expected 500, got %d", w.Code)
	}
}

func TestGetJobDescriptions_Success(t *testing.T) {
	jobID := primitive.NewObjectID()
	companyID := primitive.NewObjectID()
	jobDescriptions := &mockMongoCollection{
		aggregateResults: []aggregateResult{
			{cursor: &mockMongoCursor{docs: []bson.M{{
				"_id":     jobID,
				"company": companyID,
				"title":   "Backend Engineer",
			}}}},
		},
	}
	cleanup := setMockClient(map[string]*mockMongoCollection{
		"job-descriptions": jobDescriptions,
	})
	defer cleanup()

	c, w := apptest.CreateGinTestContext(http.MethodGet, "/api/job-descriptions", nil)
	GetJobDescriptions(c)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	out := decodeBodySliceMap(t, w.Body.Bytes())
	if len(out) != 1 {
		t.Fatalf("expected one job, got %d", len(out))
	}
	if out[0]["id"] != jobID.Hex() {
		t.Fatalf("expected normalized id, got %v", out[0]["id"])
	}
	if out[0]["company_id"] != companyID.Hex() {
		t.Fatalf("expected normalized company_id, got %v", out[0]["company_id"])
	}

	pipeline, ok := jobDescriptions.lastAggregatePipeline.(bson.A)
	if !ok || len(pipeline) != 2 {
		t.Fatalf("expected lookup+unwind pipeline, got %#v", jobDescriptions.lastAggregatePipeline)
	}
	lookupStage, ok := pipeline[0].(bson.M)
	if !ok {
		t.Fatalf("expected bson.M lookup stage, got %T", pipeline[0])
	}
	lookupDoc, ok := lookupStage["$lookup"].(bson.M)
	if !ok {
		t.Fatalf("expected $lookup document, got %#v", lookupStage)
	}
	if lookupDoc["localField"] != "company" {
		t.Fatalf("expected lookup localField=company, got %#v", lookupDoc["localField"])
	}
}

func TestGetJobDescriptions_AggregateAndDecodeErrors(t *testing.T) {
	jobDescriptionsAggErr := &mockMongoCollection{
		aggregateResults: []aggregateResult{
			{err: errors.New("aggregate failed")},
		},
	}
	cleanupAgg := setMockClient(map[string]*mockMongoCollection{
		"job-descriptions": jobDescriptionsAggErr,
	})
	defer cleanupAgg()

	c1, w1 := apptest.CreateGinTestContext(http.MethodGet, "/api/job-descriptions", nil)
	GetJobDescriptions(c1)
	if w1.Code != http.StatusInternalServerError {
		t.Fatalf("expected 500 on aggregate error, got %d", w1.Code)
	}

	cleanupAgg()

	jobDescriptionsDecodeErr := &mockMongoCollection{
		aggregateResults: []aggregateResult{
			{cursor: &mockMongoCursor{docs: []bson.M{{"_id": primitive.NewObjectID()}}, decodeErr: errors.New("decode failed")}},
		},
	}
	cleanupDecode := setMockClient(map[string]*mockMongoCollection{
		"job-descriptions": jobDescriptionsDecodeErr,
	})
	defer cleanupDecode()

	c2, w2 := apptest.CreateGinTestContext(http.MethodGet, "/api/job-descriptions", nil)
	GetJobDescriptions(c2)
	if w2.Code != http.StatusInternalServerError {
		t.Fatalf("expected 500 on decode error, got %d", w2.Code)
	}
}

func TestGetJobDescription_NotFound(t *testing.T) {
	jobDescriptions := &mockMongoCollection{
		aggregateResults: []aggregateResult{
			{cursor: &mockMongoCursor{docs: []bson.M{}}},
		},
	}
	cleanup := setMockClient(map[string]*mockMongoCollection{
		"job-descriptions": jobDescriptions,
	})
	defer cleanup()

	c, w := apptest.CreateGinTestContext(http.MethodGet, "/api/job-descriptions/id", nil)
	c.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}
	GetJobDescription(c)

	if w.Code != http.StatusNotFound {
		t.Fatalf("expected 404, got %d", w.Code)
	}
}

func TestGetJobDescription_InvalidID(t *testing.T) {
	c, w := apptest.CreateGinTestContext(http.MethodGet, "/api/job-descriptions/bad", nil)
	c.Params = gin.Params{{Key: "id", Value: "bad-id"}}
	GetJobDescription(c)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", w.Code)
	}
}

func TestCreateJobDescription_SuccessWithExistingCompany(t *testing.T) {
	insertedID := primitive.NewObjectID()
	companyID := primitive.NewObjectID()
	jobDescriptions := &mockMongoCollection{
		insertResult: &mongo.InsertOneResult{InsertedID: insertedID},
		aggregateResults: []aggregateResult{
			{cursor: &mockMongoCursor{docs: []bson.M{{
				"_id":     insertedID,
				"company": companyID,
				"title":   "Platform Engineer",
			}}}},
		},
	}
	companies := &mockMongoCollection{
		findOneResult: &mockMongoSingleResult{doc: bson.M{"_id": companyID, "name": "Acme"}},
	}

	cleanup := setMockClient(map[string]*mockMongoCollection{
		"job-descriptions": jobDescriptions,
		"companies":        companies,
	})
	defer cleanup()

	req := newJSONRequest(t, http.MethodPost, "/api/job-descriptions", map[string]interface{}{
		"title":        "Platform Engineer",
		"company_name": "Acme",
	})
	c, w := apptest.CreateGinTestContext(http.MethodPost, "/api/job-descriptions", req)
	CreateJobDescription(c)

	if w.Code != http.StatusCreated {
		t.Fatalf("expected 201, got %d: %s", w.Code, w.Body.String())
	}
	out := decodeBodyMap(t, w.Body.Bytes())
	if out["id"] != insertedID.Hex() {
		t.Fatalf("expected normalized created id, got %v", out["id"])
	}

	insertDoc, ok := jobDescriptions.lastInsertDoc.(bson.M)
	if !ok {
		t.Fatalf("expected bson.M insert doc, got %T", jobDescriptions.lastInsertDoc)
	}
	if insertDoc["company"] != companyID {
		t.Fatalf("expected objectid company in insert doc, got %#v", insertDoc["company"])
	}
	if _, ok := insertDoc["created_at"].(timestampObject); !ok {
		t.Fatalf("expected created_at timestampObject, got %T", insertDoc["created_at"])
	}
}

func TestCreateJobDescription_ValidationAndErrors(t *testing.T) {
	jobDescriptions := &mockMongoCollection{
		aggregateResults: []aggregateResult{
			{cursor: &mockMongoCursor{docs: []bson.M{{"_id": primitive.NewObjectID()}}}},
		},
	}
	cleanup := setMockClient(map[string]*mockMongoCollection{
		"job-descriptions": jobDescriptions,
		"companies":        &mockMongoCollection{},
	})
	defer cleanup()

	badReq, _ := http.NewRequest(http.MethodPost, "/api/job-descriptions", bytes.NewBufferString("bad json"))
	badReq.Header.Set("Content-Type", "application/json")
	c1, w1 := apptest.CreateGinTestContext(http.MethodPost, "/api/job-descriptions", badReq)
	CreateJobDescription(c1)
	if w1.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for invalid json, got %d", w1.Code)
	}

	reqNoTitle := newJSONRequest(t, http.MethodPost, "/api/job-descriptions", map[string]interface{}{"description": "x"})
	c2, w2 := apptest.CreateGinTestContext(http.MethodPost, "/api/job-descriptions", reqNoTitle)
	CreateJobDescription(c2)
	if w2.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for missing title, got %d", w2.Code)
	}

	reqBadCompany := newJSONRequest(t, http.MethodPost, "/api/job-descriptions", map[string]interface{}{
		"title":      "Role",
		"company_id": "bad-objectid",
	})
	c3, w3 := apptest.CreateGinTestContext(http.MethodPost, "/api/job-descriptions", reqBadCompany)
	CreateJobDescription(c3)
	if w3.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for invalid company_id, got %d", w3.Code)
	}

	cleanup()

	jobDescriptionsInsertErr := &mockMongoCollection{
		insertErr: errors.New("insert failed"),
		aggregateResults: []aggregateResult{
			{cursor: &mockMongoCursor{docs: []bson.M{{"_id": primitive.NewObjectID()}}}},
		},
	}
	cleanupInsertErr := setMockClient(map[string]*mockMongoCollection{
		"job-descriptions": jobDescriptionsInsertErr,
		"companies":        &mockMongoCollection{},
	})
	defer cleanupInsertErr()

	reqInsertErr := newJSONRequest(t, http.MethodPost, "/api/job-descriptions", map[string]interface{}{"title": "Role"})
	c4, w4 := apptest.CreateGinTestContext(http.MethodPost, "/api/job-descriptions", reqInsertErr)
	CreateJobDescription(c4)
	if w4.Code != http.StatusInternalServerError {
		t.Fatalf("expected 500 for insert error, got %d", w4.Code)
	}
}

func TestUpdateJobDescription_Success(t *testing.T) {
	jobDescriptions := &mockMongoCollection{
		updateResult: &mongo.UpdateResult{MatchedCount: 1},
		aggregateResults: []aggregateResult{
			{cursor: &mockMongoCursor{docs: []bson.M{{"_id": primitive.NewObjectID()}}}},
		},
	}
	cleanup := setMockClient(map[string]*mockMongoCollection{
		"job-descriptions": jobDescriptions,
	})
	defer cleanup()

	companyID := primitive.NewObjectID()
	req := newJSONRequest(t, http.MethodPut, "/api/job-descriptions/id", map[string]interface{}{
		"company_id":      companyID.Hex(),
		"title":           "Senior Go Engineer",
		"description":     "desc",
		"location":        "Remote",
		"platform":        "greenhouse",
		"external_job_id": "123",
		"source_url":      "https://example.com",
	})
	c, w := apptest.CreateGinTestContext(http.MethodPut, "/api/job-descriptions/id", req)
	c.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}
	UpdateJobDescription(c)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	updateDoc, ok := jobDescriptions.lastUpdateDoc.(bson.M)
	if !ok {
		t.Fatalf("expected update bson.M doc, got %T", jobDescriptions.lastUpdateDoc)
	}
	setDoc, ok := updateDoc["$set"].(bson.M)
	if !ok {
		t.Fatalf("expected $set doc, got %#v", updateDoc)
	}
	if setDoc["company"] != companyID {
		t.Fatalf("expected company objectid in update set, got %#v", setDoc["company"])
	}
	if _, ok := setDoc["updated_at"].(timestampObject); !ok {
		t.Fatalf("expected updated_at timestampObject, got %T", setDoc["updated_at"])
	}
}

func TestUpdateJobDescription_ValidationAndNotFound(t *testing.T) {
	cBadID, wBadID := apptest.CreateGinTestContext(http.MethodPut, "/api/job-descriptions/bad", nil)
	cBadID.Params = gin.Params{{Key: "id", Value: "bad-id"}}
	UpdateJobDescription(cBadID)
	if wBadID.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for invalid id, got %d", wBadID.Code)
	}

	jobDescriptions := &mockMongoCollection{
		updateResult:    &mongo.UpdateResult{MatchedCount: 0},
		aggregateCursor: &mockMongoCursor{docs: []bson.M{{"_id": primitive.NewObjectID()}}},
	}
	cleanup := setMockClient(map[string]*mockMongoCollection{
		"job-descriptions": jobDescriptions,
	})
	defer cleanup()

	badReq, _ := http.NewRequest(http.MethodPut, "/api/job-descriptions/id", bytes.NewBufferString("bad json"))
	badReq.Header.Set("Content-Type", "application/json")
	cBadJSON, wBadJSON := apptest.CreateGinTestContext(http.MethodPut, "/api/job-descriptions/id", badReq)
	cBadJSON.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}
	UpdateJobDescription(cBadJSON)
	if wBadJSON.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for bad json, got %d", wBadJSON.Code)
	}

	reqBadCompany := newJSONRequest(t, http.MethodPut, "/api/job-descriptions/id", map[string]interface{}{"company_id": "bad"})
	cBadCompany, wBadCompany := apptest.CreateGinTestContext(http.MethodPut, "/api/job-descriptions/id", reqBadCompany)
	cBadCompany.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}
	UpdateJobDescription(cBadCompany)
	if wBadCompany.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for invalid company_id, got %d", wBadCompany.Code)
	}

	reqNotFound := newJSONRequest(t, http.MethodPut, "/api/job-descriptions/id", map[string]interface{}{"title": "x"})
	cNotFound, wNotFound := apptest.CreateGinTestContext(http.MethodPut, "/api/job-descriptions/id", reqNotFound)
	cNotFound.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}
	UpdateJobDescription(cNotFound)
	if wNotFound.Code != http.StatusNotFound {
		t.Fatalf("expected 404 for unmatched update, got %d", wNotFound.Code)
	}
}

func TestDeleteJobDescription_NotFound(t *testing.T) {
	jobDescriptions := &mockMongoCollection{
		deleteResult: &mongo.DeleteResult{DeletedCount: 0},
		aggregateResults: []aggregateResult{
			{cursor: &mockMongoCursor{docs: []bson.M{{"_id": primitive.NewObjectID()}}}},
		},
	}
	cleanup := setMockClient(map[string]*mockMongoCollection{
		"job-descriptions": jobDescriptions,
	})
	defer cleanup()

	c, w := apptest.CreateGinTestContext(http.MethodDelete, "/api/job-descriptions/id", nil)
	c.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}
	DeleteJobDescription(c)

	if w.Code != http.StatusNotFound {
		t.Fatalf("expected 404, got %d", w.Code)
	}
}

func TestDeleteJobDescription_InvalidIDAndSuccess(t *testing.T) {
	cBadID, wBadID := apptest.CreateGinTestContext(http.MethodDelete, "/api/job-descriptions/bad", nil)
	cBadID.Params = gin.Params{{Key: "id", Value: "bad-id"}}
	DeleteJobDescription(cBadID)
	if wBadID.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for invalid id, got %d", wBadID.Code)
	}

	jobDescriptions := &mockMongoCollection{
		deleteResult: &mongo.DeleteResult{DeletedCount: 1},
		aggregateResults: []aggregateResult{
			{cursor: &mockMongoCursor{docs: []bson.M{{"_id": primitive.NewObjectID()}}}},
		},
	}
	cleanup := setMockClient(map[string]*mockMongoCollection{
		"job-descriptions": jobDescriptions,
	})
	defer cleanup()

	c, w := apptest.CreateGinTestContext(http.MethodDelete, "/api/job-descriptions/id", nil)
	c.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}
	DeleteJobDescription(c)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
}

func TestCheckJobDescription_Success_DefaultQueueAndPayload(t *testing.T) {
	jobDescriptions := &mockMongoCollection{
		findOneResult: &mockMongoSingleResult{doc: bson.M{"_id": primitive.NewObjectID()}},
		aggregateResults: []aggregateResult{
			{cursor: &mockMongoCursor{docs: []bson.M{{"_id": primitive.NewObjectID()}}}},
		},
	}
	cleanup := setMockClient(map[string]*mockMongoCollection{
		"job-descriptions": jobDescriptions,
	})
	defer cleanup()

	t.Setenv("CRAWLER_ENRICHMENT_RETIRING_JOBS_QUEUE_NAME", "")

	var gotQueue string
	var gotPayload []byte
	restoreQueue := setQueuePushProviderForTest(func(_ context.Context, queueName string, payload []byte) error {
		gotQueue = queueName
		gotPayload = payload
		return nil
	})
	defer restoreQueue()

	jobID := primitive.NewObjectID().Hex()
	identityID := primitive.NewObjectID().Hex()
	req := newJSONRequest(t, http.MethodPost, "/api/job-descriptions/id/check", map[string]string{"identity_id": identityID})
	c, w := apptest.CreateGinTestContext(http.MethodPost, "/api/job-descriptions/id/check", req)
	c.Params = gin.Params{{Key: "id", Value: jobID}}
	c.Set("userId", "user-42")
	CheckJobDescription(c)

	if w.Code != http.StatusAccepted {
		t.Fatalf("expected 202, got %d: %s", w.Code, w.Body.String())
	}
	if gotQueue != "enrichment_retiring_jobs_queue" {
		t.Fatalf("expected default queue name, got %q", gotQueue)
	}
	var payload map[string]string
	if err := json.Unmarshal(gotPayload, &payload); err != nil {
		t.Fatalf("decode queued payload: %v", err)
	}
	if payload["job_id"] != jobID {
		t.Fatalf("expected queued payload to contain job_id=%s, got %#v", jobID, payload)
	}
	if payload["user_id"] != "user-42" {
		t.Fatalf("expected queued payload to contain user_id=user-42, got %#v", payload)
	}
	if payload["identity_id"] != identityID {
		t.Fatalf("expected queued payload to contain identity_id=%s, got %#v", identityID, payload)
	}
}

func TestCheckJobDescription_RequiresIdentityID(t *testing.T) {
	jobDescriptions := &mockMongoCollection{
		findOneResult: &mockMongoSingleResult{doc: bson.M{"_id": primitive.NewObjectID()}},
	}
	cleanup := setMockClient(map[string]*mockMongoCollection{
		"job-descriptions": jobDescriptions,
	})
	defer cleanup()

	jobID := primitive.NewObjectID().Hex()

	// Missing identity_id
	req := newJSONRequest(t, http.MethodPost, "/api/job-descriptions/id/check", map[string]string{})
	c, w := apptest.CreateGinTestContext(http.MethodPost, "/api/job-descriptions/id/check", req)
	c.Params = gin.Params{{Key: "id", Value: jobID}}
	CheckJobDescription(c)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for missing identity_id, got %d: %s", w.Code, w.Body.String())
	}
}

func TestCheckJobDescription_InvalidIdentityID(t *testing.T) {
	jobDescriptions := &mockMongoCollection{
		findOneResult: &mockMongoSingleResult{doc: bson.M{"_id": primitive.NewObjectID()}},
	}
	cleanup := setMockClient(map[string]*mockMongoCollection{
		"job-descriptions": jobDescriptions,
	})
	defer cleanup()

	jobID := primitive.NewObjectID().Hex()

	req := newJSONRequest(t, http.MethodPost, "/api/job-descriptions/id/check", map[string]string{"identity_id": "not-a-valid-hex"})
	c, w := apptest.CreateGinTestContext(http.MethodPost, "/api/job-descriptions/id/check", req)
	c.Params = gin.Params{{Key: "id", Value: jobID}}
	CheckJobDescription(c)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for invalid identity_id, got %d: %s", w.Code, w.Body.String())
	}
}

func TestCheckJobDescription_InvalidIDAndQueueFailure(t *testing.T) {
	cBadID, wBadID := apptest.CreateGinTestContext(http.MethodPost, "/api/job-descriptions/bad/check", nil)
	cBadID.Params = gin.Params{{Key: "id", Value: "bad-id"}}
	CheckJobDescription(cBadID)
	if wBadID.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for invalid id, got %d", wBadID.Code)
	}

	jobDescriptions := &mockMongoCollection{
		findOneResult: &mockMongoSingleResult{doc: bson.M{"_id": primitive.NewObjectID()}},
		aggregateResults: []aggregateResult{
			{cursor: &mockMongoCursor{docs: []bson.M{{"_id": primitive.NewObjectID()}}}},
		},
	}
	cleanup := setMockClient(map[string]*mockMongoCollection{
		"job-descriptions": jobDescriptions,
	})
	defer cleanup()

	restoreQueue := setQueuePushProviderForTest(func(_ context.Context, _ string, _ []byte) error {
		return errors.New("queue failed")
	})
	defer restoreQueue()

	identityID := primitive.NewObjectID().Hex()
	req := newJSONRequest(t, http.MethodPost, "/api/job-descriptions/id/check", map[string]string{"identity_id": identityID})
	c, w := apptest.CreateGinTestContext(http.MethodPost, "/api/job-descriptions/id/check", req)
	c.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}
	CheckJobDescription(c)

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("expected 500 for queue failure, got %d", w.Code)
	}
}

func TestScoreJobDescription_QueueOverrideAndFailure(t *testing.T) {
	jobDescriptions := &mockMongoCollection{
		findOneResult: &mockMongoSingleResult{doc: bson.M{"_id": primitive.NewObjectID()}},
		aggregateResults: []aggregateResult{
			{cursor: &mockMongoCursor{docs: []bson.M{{"_id": primitive.NewObjectID()}}}},
		},
	}
	cleanup := setMockClient(map[string]*mockMongoCollection{
		"job-descriptions": jobDescriptions,
	})
	defer cleanup()

	t.Setenv("JOB_SCORING_QUEUE_NAME", "custom_score_queue")

	var gotQueue string
	restoreQueue := setQueuePushProviderForTest(func(_ context.Context, queueName string, _ []byte) error {
		gotQueue = queueName
		return errors.New("queue down")
	})
	defer restoreQueue()

	req := newJSONRequest(t, http.MethodPost, "/api/job-descriptions/id/score", map[string]string{"identity_id": primitive.NewObjectID().Hex()})
	c, w := apptest.CreateGinTestContext(http.MethodPost, "/api/job-descriptions/id/score", req)
	c.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}
	ScoreJobDescription(c)

	if gotQueue != "custom_score_queue" {
		t.Fatalf("expected env override queue name, got %q", gotQueue)
	}
	if w.Code != http.StatusInternalServerError {
		t.Fatalf("expected 500 on queue error, got %d", w.Code)
	}
}

func TestScoreJobDescription_IncludesIdentityIDInQueuedPayload(t *testing.T) {
	jobDescriptions := &mockMongoCollection{
		findOneResult: &mockMongoSingleResult{doc: bson.M{"_id": primitive.NewObjectID()}},
		aggregateResults: []aggregateResult{
			{cursor: &mockMongoCursor{docs: []bson.M{{"_id": primitive.NewObjectID()}}}},
		},
	}
	cleanup := setMockClient(map[string]*mockMongoCollection{
		"job-descriptions": jobDescriptions,
	})
	defer cleanup()

	var gotQueue string
	var gotPayload []byte
	restoreQueue := setQueuePushProviderForTest(func(_ context.Context, queueName string, payload []byte) error {
		gotQueue = queueName
		gotPayload = payload
		return nil
	})
	defer restoreQueue()

	jobID := primitive.NewObjectID().Hex()
	identityID := primitive.NewObjectID().Hex()
	req := newJSONRequest(t, http.MethodPost, "/api/job-descriptions/id/score", map[string]string{"identity_id": identityID})
	c, w := apptest.CreateGinTestContext(http.MethodPost, "/api/job-descriptions/id/score", req)
	c.Params = gin.Params{{Key: "id", Value: jobID}}
	c.Set("userId", "user-1")
	ScoreJobDescription(c)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	if gotQueue != "job_scoring_queue" {
		t.Fatalf("expected default queue name, got %q", gotQueue)
	}

	var payload map[string]string
	if err := json.Unmarshal(gotPayload, &payload); err != nil {
		t.Fatalf("decode queued payload: %v", err)
	}
	if payload["job_id"] != jobID {
		t.Fatalf("expected queued payload to contain job_id=%s, got %#v", jobID, payload)
	}
	if payload["user_id"] != "user-1" {
		t.Fatalf("expected queued payload to contain user_id=user-1, got %#v", payload)
	}
	if payload["identity_id"] != identityID {
		t.Fatalf("expected queued payload to contain identity_id=%s, got %#v", identityID, payload)
	}
}

func TestScoreJobDescription_RequiresIdentityID(t *testing.T) {
	queueCalled := false
	restoreQueue := setQueuePushProviderForTest(func(_ context.Context, _ string, _ []byte) error {
		queueCalled = true
		return nil
	})
	defer restoreQueue()

	req := newJSONRequest(t, http.MethodPost, "/api/job-descriptions/id/score", map[string]string{})
	c, w := apptest.CreateGinTestContext(http.MethodPost, "/api/job-descriptions/id/score", req)
	c.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}
	ScoreJobDescription(c)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for missing identity_id, got %d", w.Code)
	}
	if queueCalled {
		t.Fatal("expected queue not to be called for missing identity_id")
	}
}

func TestScoreJobDescription_InvalidIdentityID(t *testing.T) {
	queueCalled := false
	restoreQueue := setQueuePushProviderForTest(func(_ context.Context, _ string, _ []byte) error {
		queueCalled = true
		return nil
	})
	defer restoreQueue()

	req := newJSONRequest(t, http.MethodPost, "/api/job-descriptions/id/score", map[string]string{"identity_id": "bad-id"})
	c, w := apptest.CreateGinTestContext(http.MethodPost, "/api/job-descriptions/id/score", req)
	c.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}
	ScoreJobDescription(c)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for invalid identity_id, got %d", w.Code)
	}
	if queueCalled {
		t.Fatal("expected queue not to be called for invalid identity_id")
	}
}

func TestScoreJobDescription_InvalidIDAndNotFound(t *testing.T) {
	cBadID, wBadID := apptest.CreateGinTestContext(http.MethodPost, "/api/job-descriptions/bad/score", nil)
	cBadID.Params = gin.Params{{Key: "id", Value: "bad-id"}}
	ScoreJobDescription(cBadID)
	if wBadID.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for invalid id, got %d", wBadID.Code)
	}

	jobDescriptions := &mockMongoCollection{
		findOneResult: &mockMongoSingleResult{err: errors.New("not found")},
		aggregateResults: []aggregateResult{
			{cursor: &mockMongoCursor{docs: []bson.M{{"_id": primitive.NewObjectID()}}}},
		},
	}
	cleanup := setMockClient(map[string]*mockMongoCollection{
		"job-descriptions": jobDescriptions,
	})
	defer cleanup()

	reqNotFound := newJSONRequest(t, http.MethodPost, "/api/job-descriptions/id/score", map[string]string{"identity_id": primitive.NewObjectID().Hex()})
	cNotFound, wNotFound := apptest.CreateGinTestContext(http.MethodPost, "/api/job-descriptions/id/score", reqNotFound)
	cNotFound.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}
	ScoreJobDescription(cNotFound)

	if wNotFound.Code != http.StatusNotFound {
		t.Fatalf("expected 404 when job is missing, got %d", wNotFound.Code)
	}
}

func TestStreamJobUpdates_WritesSSEEventAndHeaders(t *testing.T) {
	origHub := jobUpdateHub_
	jobUpdateHub_ = &jobUpdateHub{subscribers: make(map[int]jobUpdateSubscriber)}
	defer func() { jobUpdateHub_ = origHub }()

	// Prevent StreamJobUpdates from spawning the long-running Redis bridge loop.
	jobUpdateHub_.bridgeOnce.Do(func() {})

	req, err := http.NewRequest(http.MethodGet, "/api/job-descriptions/stream", nil)
	if err != nil {
		t.Fatalf("new request: %v", err)
	}
	ctx, cancel := context.WithCancel(req.Context())
	defer cancel()
	req = req.WithContext(ctx)

	c, w := apptest.CreateGinTestContext(http.MethodGet, "/api/job-descriptions/stream", req)
	done := make(chan struct{})
	go func() {
		StreamJobUpdates(c)
		close(done)
	}()

	ok := waitUntil(500*time.Millisecond, func() bool {
		jobUpdateHub_.mu.RLock()
		defer jobUpdateHub_.mu.RUnlock()
		return len(jobUpdateHub_.subscribers) == 1
	})
	if !ok {
		t.Fatal("expected stream handler to subscribe")
	}

	PublishJobUpdateForTests(&models.JobUpdateEvent{JobId: "job-42"})
	time.Sleep(30 * time.Millisecond)

	if ct := w.Header().Get("Content-Type"); ct != "text/event-stream" {
		t.Fatalf("expected text/event-stream content type, got %q", ct)
	}

	cancel()
	select {
	case <-done:
	case <-time.After(1 * time.Second):
		t.Fatal("stream handler did not terminate after context cancel")
	}

	if !bytes.Contains(w.Body.Bytes(), []byte("event: job-update")) {
		t.Fatalf("expected SSE event marker in response body, got %q", w.Body.String())
	}
}

func TestJobUpdateHub_SubscribePublishAndUnsubscribe(t *testing.T) {
	h := &jobUpdateHub{subscribers: make(map[int]jobUpdateSubscriber)}
	id, ch := h.subscribe()
	event := &models.JobUpdateEvent{
		JobId:         "job-1",
		WorkflowId:    "wf",
		WorkflowRunId: "run-1",
		EmittedAt:     timestamppb.New(time.Unix(1700000000, 0)),
	}
	h.publish(event)

	select {
	case got := <-ch:
		if got.GetJobId() != "job-1" {
			t.Fatalf("expected event job id job-1, got %q", got.GetJobId())
		}
		if got == event {
			t.Fatal("expected publish to clone event")
		}
	case <-time.After(500 * time.Millisecond):
		t.Fatal("timed out waiting for published event")
	}

	h.unsubscribe(id)
	if len(h.subscribers) != 0 {
		t.Fatalf("expected no subscribers after unsubscribe, got %d", len(h.subscribers))
	}
}

func TestSetProviders_NilNoop(t *testing.T) {
	origMongo := getMongoClient
	origQueue := queuePush
	origSub := subscribeChannel
	defer func() {
		getMongoClient = origMongo
		queuePush = origQueue
		subscribeChannel = origSub
	}()

	SetMongoClientProvider(nil)
	SetQueuePushProvider(nil)
	SetSubscribeChannelProvider(nil)

	if getMongoClient == nil || queuePush == nil || subscribeChannel == nil {
		t.Fatal("expected nil providers to be ignored")
	}
}

func TestResetJobUpdateStateForTests(t *testing.T) {
	origHub := jobUpdateHub_
	jobUpdateHub_ = &jobUpdateHub{subscribers: make(map[int]jobUpdateSubscriber)}
	defer func() { jobUpdateHub_ = origHub }()

	_, _ = jobUpdateHub_.subscribe()
	_, _ = jobUpdateHub_.subscribe()
	if len(jobUpdateHub_.subscribers) != 2 {
		t.Fatalf("expected two subscribers, got %d", len(jobUpdateHub_.subscribers))
	}

	ResetJobUpdateStateForTests()

	if len(jobUpdateHub_.subscribers) != 0 {
		t.Fatalf("expected subscribers to be cleared, got %d", len(jobUpdateHub_.subscribers))
	}
	if jobUpdateHub_.nextID != 0 {
		t.Fatalf("expected nextID reset to 0, got %d", jobUpdateHub_.nextID)
	}
}
