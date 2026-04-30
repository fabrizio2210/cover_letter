package coverletters

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/fabrizio2210/cover_letter/src/go/cmd/api/models"
	apitesting "github.com/fabrizio2210/cover_letter/src/go/cmd/api/testing"
	"github.com/gin-gonic/gin"
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/bson/primitive"
	"go.mongodb.org/mongo-driver/mongo"
)

type fakeMongoClient struct {
	dbName string
	db     *fakeMongoDatabase
}

func (f *fakeMongoClient) Database(name string) MongoDatabaseIface {
	f.dbName = name
	return f.db
}

type fakeMongoDatabase struct {
	collectionNames []string
	collections     map[string]*fakeMongoCollection
}

func (f *fakeMongoDatabase) Collection(name string) MongoCollectionIface {
	f.collectionNames = append(f.collectionNames, name)
	if f.collections == nil {
		f.collections = map[string]*fakeMongoCollection{}
	}
	col, ok := f.collections[name]
	if !ok {
		col = &fakeMongoCollection{}
		f.collections[name] = col
	}
	return col
}

type fakeMongoCollection struct {
	aggregatePipeline interface{}
	aggregateCursor   MongoCursorIface
	aggregateErr      error

	findFilter interface{}
	findOne    MongoSingleResultIface

	updateFilter interface{}
	updateDoc    interface{}
	updateResult *mongo.UpdateResult
	updateErr    error

	deleteFilter interface{}
	deleteResult *mongo.DeleteResult
	deleteErr    error
}

func (f *fakeMongoCollection) Aggregate(ctx context.Context, pipeline interface{}) (MongoCursorIface, error) {
	f.aggregatePipeline = pipeline
	if f.aggregateErr != nil {
		return nil, f.aggregateErr
	}
	if f.aggregateCursor == nil {
		f.aggregateCursor = &fakeMongoCursor{}
	}
	return f.aggregateCursor, nil
}

func (f *fakeMongoCollection) FindOne(ctx context.Context, filter interface{}) MongoSingleResultIface {
	f.findFilter = filter
	if f.findOne == nil {
		return &fakeSingleResult{err: mongo.ErrNoDocuments}
	}
	return f.findOne
}

func (f *fakeMongoCollection) UpdateOne(ctx context.Context, filter interface{}, update interface{}) (*mongo.UpdateResult, error) {
	f.updateFilter = filter
	f.updateDoc = update
	if f.updateErr != nil {
		return nil, f.updateErr
	}
	if f.updateResult == nil {
		f.updateResult = &mongo.UpdateResult{}
	}
	return f.updateResult, nil
}

func (f *fakeMongoCollection) DeleteOne(ctx context.Context, filter interface{}) (*mongo.DeleteResult, error) {
	f.deleteFilter = filter
	if f.deleteErr != nil {
		return nil, f.deleteErr
	}
	if f.deleteResult == nil {
		f.deleteResult = &mongo.DeleteResult{}
	}
	return f.deleteResult, nil
}

type fakeMongoCursor struct {
	allFn      func(result interface{}) error
	allErr     error
	nextValues []bool
	nextIdx    int
	decodeFn   func(v interface{}) error
	decodeErr  error
	closed     bool
}

func (f *fakeMongoCursor) All(ctx context.Context, result interface{}) error {
	if f.allFn != nil {
		return f.allFn(result)
	}
	return f.allErr
}

func (f *fakeMongoCursor) Next(ctx context.Context) bool {
	if len(f.nextValues) == 0 {
		return false
	}
	if f.nextIdx >= len(f.nextValues) {
		return false
	}
	v := f.nextValues[f.nextIdx]
	f.nextIdx++
	return v
}

func (f *fakeMongoCursor) Decode(v interface{}) error {
	if f.decodeFn != nil {
		return f.decodeFn(v)
	}
	return f.decodeErr
}

func (f *fakeMongoCursor) Close(ctx context.Context) error {
	f.closed = true
	return nil
}

type fakeSingleResult struct {
	decodeFn func(v interface{}) error
	err      error
}

func (f *fakeSingleResult) Decode(v interface{}) error {
	if f.decodeFn != nil {
		return f.decodeFn(v)
	}
	return f.err
}

func setProvidersForTest(t *testing.T, client MongoClientIface, pushFn func(ctx context.Context, queueName string, payload []byte) error) {
	t.Helper()
	oldClientProvider := getMongoClient
	oldQueuePush := queuePush

	if client != nil {
		getMongoClient = func() MongoClientIface { return client }
	}
	if pushFn != nil {
		queuePush = pushFn
	}

	t.Cleanup(func() {
		getMongoClient = oldClientProvider
		queuePush = oldQueuePush
	})
}

func newJSONRequest(method, path, body string) *http.Request {
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	return req
}

func newContextWithID(t *testing.T, method, path, id string, req *http.Request) (*gin.Context, *httptest.ResponseRecorder) {
	t.Helper()
	c, w := apitesting.CreateGinTestContext(method, path, req)
	c.Params = gin.Params{{Key: "id", Value: id}}
	return c, w
}

func decodeBodyMap(t *testing.T, w *httptest.ResponseRecorder) map[string]interface{} {
	t.Helper()
	var body map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("failed to decode response body: %v", err)
	}
	return body
}

func TestGetCoverLetters_SuccessNilSliceReturnsEmptyArray(t *testing.T) {
	coverLettersCol := &fakeMongoCollection{
		aggregateCursor: &fakeMongoCursor{
			allFn: func(result interface{}) error {
				out, ok := result.(*[]models.CoverLetter)
				if !ok {
					return errors.New("unexpected result target")
				}
				*out = nil
				return nil
			},
		},
	}

	fakeDB := &fakeMongoDatabase{collections: map[string]*fakeMongoCollection{"cover-letters": coverLettersCol}}
	setProvidersForTest(t, &fakeMongoClient{db: fakeDB}, nil)

	c, w := apitesting.CreateGinTestContext(http.MethodGet, "/api/cover-letters", nil)
	GetCoverLetters(c)

	if w.Code != http.StatusOK {
		t.Fatalf("expected status %d, got %d", http.StatusOK, w.Code)
	}
	if strings.TrimSpace(w.Body.String()) != "[]" {
		t.Fatalf("expected empty JSON array, got %s", w.Body.String())
	}
	if coverLettersCol.aggregatePipeline == nil {
		t.Fatal("expected aggregate pipeline to be captured")
	}
}

func TestGetCoverLetters_AggregateError(t *testing.T) {
	coverLettersCol := &fakeMongoCollection{aggregateErr: errors.New("aggregate failed")}
	fakeDB := &fakeMongoDatabase{collections: map[string]*fakeMongoCollection{"cover-letters": coverLettersCol}}
	setProvidersForTest(t, &fakeMongoClient{db: fakeDB}, nil)

	c, w := apitesting.CreateGinTestContext(http.MethodGet, "/api/cover-letters", nil)
	GetCoverLetters(c)

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("expected status %d, got %d", http.StatusInternalServerError, w.Code)
	}
	body := decodeBodyMap(t, w)
	if body["error"] != "Failed to fetch cover letters" {
		t.Fatalf("unexpected error body: %#v", body)
	}
}

func TestGetCoverLetters_DecodeError(t *testing.T) {
	coverLettersCol := &fakeMongoCollection{
		aggregateCursor: &fakeMongoCursor{allErr: errors.New("decode failed")},
	}
	fakeDB := &fakeMongoDatabase{collections: map[string]*fakeMongoCollection{"cover-letters": coverLettersCol}}
	setProvidersForTest(t, &fakeMongoClient{db: fakeDB}, nil)

	c, w := apitesting.CreateGinTestContext(http.MethodGet, "/api/cover-letters", nil)
	GetCoverLetters(c)

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("expected status %d, got %d", http.StatusInternalServerError, w.Code)
	}
	body := decodeBodyMap(t, w)
	if body["error"] != "Failed to decode cover letters" {
		t.Fatalf("unexpected error body: %#v", body)
	}
}

func TestGetCoverLetter_InvalidID(t *testing.T) {
	c, w := newContextWithID(t, http.MethodGet, "/api/cover-letters/bad", "bad", nil)
	GetCoverLetter(c)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected status %d, got %d", http.StatusBadRequest, w.Code)
	}
	body := decodeBodyMap(t, w)
	if body["error"] != "Invalid ID" {
		t.Fatalf("unexpected error body: %#v", body)
	}
}

func TestGetCoverLetter_NotFoundWhenCursorEmpty(t *testing.T) {
	id := primitive.NewObjectID().Hex()
	coverLettersCol := &fakeMongoCollection{
		aggregateCursor: &fakeMongoCursor{nextValues: []bool{false}},
	}
	fakeDB := &fakeMongoDatabase{collections: map[string]*fakeMongoCollection{"cover-letters": coverLettersCol}}
	setProvidersForTest(t, &fakeMongoClient{db: fakeDB}, nil)

	c, w := newContextWithID(t, http.MethodGet, "/api/cover-letters/"+id, id, nil)
	GetCoverLetter(c)

	if w.Code != http.StatusNotFound {
		t.Fatalf("expected status %d, got %d", http.StatusNotFound, w.Code)
	}
	body := decodeBodyMap(t, w)
	if body["error"] != "Cover letter not found" {
		t.Fatalf("unexpected error body: %#v", body)
	}
}

func TestGetCoverLetter_DecodeError(t *testing.T) {
	id := primitive.NewObjectID().Hex()
	coverLettersCol := &fakeMongoCollection{
		aggregateCursor: &fakeMongoCursor{
			nextValues: []bool{true},
			decodeErr:  errors.New("decode failed"),
		},
	}
	fakeDB := &fakeMongoDatabase{collections: map[string]*fakeMongoCollection{"cover-letters": coverLettersCol}}
	setProvidersForTest(t, &fakeMongoClient{db: fakeDB}, nil)

	c, w := newContextWithID(t, http.MethodGet, "/api/cover-letters/"+id, id, nil)
	GetCoverLetter(c)

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("expected status %d, got %d", http.StatusInternalServerError, w.Code)
	}
	body := decodeBodyMap(t, w)
	if body["error"] != "Failed to fetch cover letter" {
		t.Fatalf("unexpected error body: %#v", body)
	}
}

func TestGetCoverLetter_Success(t *testing.T) {
	id := primitive.NewObjectID().Hex()
	coverLettersCol := &fakeMongoCollection{
		aggregateCursor: &fakeMongoCursor{
			nextValues: []bool{true},
			decodeFn: func(v interface{}) error {
				out, ok := v.(*models.CoverLetter)
				if !ok {
					return errors.New("unexpected decode target")
				}
				out.Id = id
				out.CoverLetter = "hello"
				return nil
			},
		},
	}
	fakeDB := &fakeMongoDatabase{collections: map[string]*fakeMongoCollection{"cover-letters": coverLettersCol}}
	setProvidersForTest(t, &fakeMongoClient{db: fakeDB}, nil)

	c, w := newContextWithID(t, http.MethodGet, "/api/cover-letters/"+id, id, nil)
	GetCoverLetter(c)

	if w.Code != http.StatusOK {
		t.Fatalf("expected status %d, got %d", http.StatusOK, w.Code)
	}
	body := decodeBodyMap(t, w)
	if body["id"] != id {
		t.Fatalf("expected id %s, got %#v", id, body["id"])
	}
}

func TestDeleteCoverLetter_InvalidID(t *testing.T) {
	c, w := newContextWithID(t, http.MethodDelete, "/api/cover-letters/bad", "bad", nil)
	DeleteCoverLetter(c)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected status %d, got %d", http.StatusBadRequest, w.Code)
	}
	body := decodeBodyMap(t, w)
	if body["error"] != "Invalid ID" {
		t.Fatalf("unexpected error body: %#v", body)
	}
}

func TestDeleteCoverLetter_ErrorAndSuccess(t *testing.T) {
	t.Run("delete error returns 500", func(t *testing.T) {
		id := primitive.NewObjectID().Hex()
		coverLettersCol := &fakeMongoCollection{deleteErr: errors.New("delete failed")}
		fakeDB := &fakeMongoDatabase{collections: map[string]*fakeMongoCollection{"cover-letters": coverLettersCol}}
		setProvidersForTest(t, &fakeMongoClient{db: fakeDB}, nil)

		c, w := newContextWithID(t, http.MethodDelete, "/api/cover-letters/"+id, id, nil)
		DeleteCoverLetter(c)

		if w.Code != http.StatusInternalServerError {
			t.Fatalf("expected status %d, got %d", http.StatusInternalServerError, w.Code)
		}
	})

	t.Run("missing document returns 404", func(t *testing.T) {
		id := primitive.NewObjectID().Hex()
		coverLettersCol := &fakeMongoCollection{deleteResult: &mongo.DeleteResult{DeletedCount: 0}}
		fakeDB := &fakeMongoDatabase{collections: map[string]*fakeMongoCollection{"cover-letters": coverLettersCol}}
		setProvidersForTest(t, &fakeMongoClient{db: fakeDB}, nil)

		c, w := newContextWithID(t, http.MethodDelete, "/api/cover-letters/"+id, id, nil)
		DeleteCoverLetter(c)

		if w.Code != http.StatusNotFound {
			t.Fatalf("expected status %d, got %d", http.StatusNotFound, w.Code)
		}
	})

	t.Run("success captures object id filter", func(t *testing.T) {
		id := primitive.NewObjectID().Hex()
		coverLettersCol := &fakeMongoCollection{deleteResult: &mongo.DeleteResult{DeletedCount: 1}}
		fakeDB := &fakeMongoDatabase{collections: map[string]*fakeMongoCollection{"cover-letters": coverLettersCol}}
		setProvidersForTest(t, &fakeMongoClient{db: fakeDB}, nil)

		c, w := newContextWithID(t, http.MethodDelete, "/api/cover-letters/"+id, id, nil)
		DeleteCoverLetter(c)

		if w.Code != http.StatusOK {
			t.Fatalf("expected status %d, got %d", http.StatusOK, w.Code)
		}
		filter, ok := coverLettersCol.deleteFilter.(bson.M)
		if !ok {
			t.Fatalf("expected bson.M delete filter, got %T", coverLettersCol.deleteFilter)
		}
		gotID, ok := filter["_id"].(primitive.ObjectID)
		if !ok {
			t.Fatalf("expected _id ObjectID, got %T", filter["_id"])
		}
		if gotID.Hex() != id {
			t.Fatalf("expected _id %s, got %s", id, gotID.Hex())
		}
	})
}

func TestUpdateCoverLetter_InvalidID(t *testing.T) {
	req := newJSONRequest(http.MethodPut, "/api/cover-letters/bad", `{"content":"new"}`)
	c, w := newContextWithID(t, http.MethodPut, "/api/cover-letters/bad", "bad", req)
	UpdateCoverLetter(c)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected status %d, got %d", http.StatusBadRequest, w.Code)
	}
}

func TestUpdateCoverLetter_InvalidJSON(t *testing.T) {
	id := primitive.NewObjectID().Hex()
	req := newJSONRequest(http.MethodPut, "/api/cover-letters/"+id, `{`)
	c, w := newContextWithID(t, http.MethodPut, "/api/cover-letters/"+id, id, req)
	UpdateCoverLetter(c)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected status %d, got %d", http.StatusBadRequest, w.Code)
	}
	body := decodeBodyMap(t, w)
	if body["error"] != "Invalid request" {
		t.Fatalf("unexpected error body: %#v", body)
	}
}

func TestUpdateCoverLetter_ErrorNotModifiedAndSuccess(t *testing.T) {
	t.Run("update error returns 500", func(t *testing.T) {
		id := primitive.NewObjectID().Hex()
		coverLettersCol := &fakeMongoCollection{updateErr: errors.New("update failed")}
		fakeDB := &fakeMongoDatabase{collections: map[string]*fakeMongoCollection{"cover-letters": coverLettersCol}}
		setProvidersForTest(t, &fakeMongoClient{db: fakeDB}, nil)

		req := newJSONRequest(http.MethodPut, "/api/cover-letters/"+id, `{"content":"new content"}`)
		c, w := newContextWithID(t, http.MethodPut, "/api/cover-letters/"+id, id, req)
		UpdateCoverLetter(c)

		if w.Code != http.StatusInternalServerError {
			t.Fatalf("expected status %d, got %d", http.StatusInternalServerError, w.Code)
		}
	})

	t.Run("not modified returns 404", func(t *testing.T) {
		id := primitive.NewObjectID().Hex()
		coverLettersCol := &fakeMongoCollection{updateResult: &mongo.UpdateResult{ModifiedCount: 0}}
		fakeDB := &fakeMongoDatabase{collections: map[string]*fakeMongoCollection{"cover-letters": coverLettersCol}}
		setProvidersForTest(t, &fakeMongoClient{db: fakeDB}, nil)

		req := newJSONRequest(http.MethodPut, "/api/cover-letters/"+id, `{"content":"same"}`)
		c, w := newContextWithID(t, http.MethodPut, "/api/cover-letters/"+id, id, req)
		UpdateCoverLetter(c)

		if w.Code != http.StatusNotFound {
			t.Fatalf("expected status %d, got %d", http.StatusNotFound, w.Code)
		}
	})

	t.Run("success captures update payload", func(t *testing.T) {
		id := primitive.NewObjectID().Hex()
		coverLettersCol := &fakeMongoCollection{updateResult: &mongo.UpdateResult{ModifiedCount: 1}}
		fakeDB := &fakeMongoDatabase{collections: map[string]*fakeMongoCollection{"cover-letters": coverLettersCol}}
		setProvidersForTest(t, &fakeMongoClient{db: fakeDB}, nil)

		req := newJSONRequest(http.MethodPut, "/api/cover-letters/"+id, `{"content":"updated"}`)
		c, w := newContextWithID(t, http.MethodPut, "/api/cover-letters/"+id, id, req)
		UpdateCoverLetter(c)

		if w.Code != http.StatusOK {
			t.Fatalf("expected status %d, got %d", http.StatusOK, w.Code)
		}

		updateDoc, ok := coverLettersCol.updateDoc.(bson.M)
		if !ok {
			t.Fatalf("expected bson.M update doc, got %T", coverLettersCol.updateDoc)
		}
		setPart, ok := updateDoc["$set"].(bson.M)
		if !ok {
			t.Fatalf("expected bson.M $set payload, got %T", updateDoc["$set"])
		}
		if setPart["cover_letter"] != "updated" {
			t.Fatalf("expected cover_letter update 'updated', got %#v", setPart["cover_letter"])
		}
	})
}

func TestRefineCoverLetter_ValidationAndQueue(t *testing.T) {
	t.Run("invalid id returns 400", func(t *testing.T) {
		req := newJSONRequest(http.MethodPost, "/api/cover-letters/bad/refine", `{"prompt":"p"}`)
		c, w := newContextWithID(t, http.MethodPost, "/api/cover-letters/bad/refine", "bad", req)
		RefineCoverLetter(c)

		if w.Code != http.StatusBadRequest {
			t.Fatalf("expected status %d, got %d", http.StatusBadRequest, w.Code)
		}
	})

	t.Run("invalid json returns 400", func(t *testing.T) {
		id := primitive.NewObjectID().Hex()
		req := newJSONRequest(http.MethodPost, "/api/cover-letters/"+id+"/refine", `{`)
		c, w := newContextWithID(t, http.MethodPost, "/api/cover-letters/"+id+"/refine", id, req)
		RefineCoverLetter(c)

		if w.Code != http.StatusBadRequest {
			t.Fatalf("expected status %d, got %d", http.StatusBadRequest, w.Code)
		}
	})

	t.Run("cover letter not found returns 404", func(t *testing.T) {
		id := primitive.NewObjectID().Hex()
		coverLettersCol := &fakeMongoCollection{findOne: &fakeSingleResult{err: mongo.ErrNoDocuments}}
		fakeDB := &fakeMongoDatabase{collections: map[string]*fakeMongoCollection{"cover-letters": coverLettersCol}}
		setProvidersForTest(t, &fakeMongoClient{db: fakeDB}, nil)

		req := newJSONRequest(http.MethodPost, "/api/cover-letters/"+id+"/refine", `{"prompt":"p"}`)
		c, w := newContextWithID(t, http.MethodPost, "/api/cover-letters/"+id+"/refine", id, req)
		RefineCoverLetter(c)

		if w.Code != http.StatusNotFound {
			t.Fatalf("expected status %d, got %d", http.StatusNotFound, w.Code)
		}
	})

	t.Run("queue error returns 500", func(t *testing.T) {
		id := primitive.NewObjectID().Hex()
		recipientID := primitive.NewObjectID().Hex()
		coverLettersCol := &fakeMongoCollection{
			findOne: &fakeSingleResult{decodeFn: func(v interface{}) error {
				out := v.(*bson.M)
				*out = bson.M{"recipient_id": recipientID, "conversation_id": "conv-1"}
				return nil
			}},
		}
		recipientsCol := &fakeMongoCollection{
			findOne: &fakeSingleResult{decodeFn: func(v interface{}) error {
				out := v.(*bson.M)
				*out = bson.M{"email": "hr@example.com"}
				return nil
			}},
		}
		fakeDB := &fakeMongoDatabase{collections: map[string]*fakeMongoCollection{"cover-letters": coverLettersCol, "recipients": recipientsCol}}
		setProvidersForTest(t, &fakeMongoClient{db: fakeDB}, func(ctx context.Context, queueName string, payload []byte) error {
			return errors.New("queue unavailable")
		})

		req := newJSONRequest(http.MethodPost, "/api/cover-letters/"+id+"/refine", `{"prompt":"Improve"}`)
		c, w := newContextWithID(t, http.MethodPost, "/api/cover-letters/"+id+"/refine", id, req)
		RefineCoverLetter(c)

		if w.Code != http.StatusInternalServerError {
			t.Fatalf("expected status %d, got %d", http.StatusInternalServerError, w.Code)
		}
	})

	t.Run("success uses queue env and payload", func(t *testing.T) {
		t.Setenv("REDIS_QUEUE_GENERATE_COVER_LETTER_NAME", "custom_refine_queue")

		id := primitive.NewObjectID().Hex()
		recipientID := primitive.NewObjectID().Hex()
		coverLettersCol := &fakeMongoCollection{
			findOne: &fakeSingleResult{decodeFn: func(v interface{}) error {
				out := v.(*bson.M)
				*out = bson.M{"recipient_id": recipientID, "conversation_id": "conv-42"}
				return nil
			}},
		}
		recipientsCol := &fakeMongoCollection{
			findOne: &fakeSingleResult{decodeFn: func(v interface{}) error {
				out := v.(*bson.M)
				*out = bson.M{"email": "target@example.com"}
				return nil
			}},
		}
		fakeDB := &fakeMongoDatabase{collections: map[string]*fakeMongoCollection{"cover-letters": coverLettersCol, "recipients": recipientsCol}}

		var capturedQueue string
		var capturedPayload []byte
		setProvidersForTest(t, &fakeMongoClient{db: fakeDB}, func(ctx context.Context, queueName string, payload []byte) error {
			capturedQueue = queueName
			capturedPayload = payload
			return nil
		})

		req := newJSONRequest(http.MethodPost, "/api/cover-letters/"+id+"/refine", `{"prompt":"Tighten the tone"}`)
		c, w := newContextWithID(t, http.MethodPost, "/api/cover-letters/"+id+"/refine", id, req)
		c.Set("userId", "user-123")
		RefineCoverLetter(c)

		if w.Code != http.StatusOK {
			t.Fatalf("expected status %d, got %d", http.StatusOK, w.Code)
		}
		if capturedQueue != "custom_refine_queue" {
			t.Fatalf("expected queue custom_refine_queue, got %s", capturedQueue)
		}
		var payload map[string]interface{}
		if err := json.Unmarshal(capturedPayload, &payload); err != nil {
			t.Fatalf("failed to decode queue payload: %v", err)
		}
		if payload["recipient"] != "target@example.com" {
			t.Fatalf("unexpected recipient: %#v", payload["recipient"])
		}
		if payload["user_id"] != "user-123" {
			t.Fatalf("unexpected user_id: %#v", payload["user_id"])
		}
		if payload["conversation_id"] != "conv-42" {
			t.Fatalf("unexpected conversation_id: %#v", payload["conversation_id"])
		}
		if payload["prompt"] != "Tighten the tone" {
			t.Fatalf("unexpected prompt: %#v", payload["prompt"])
		}
	})
}

func TestSendCoverLetter_ValidationAndQueue(t *testing.T) {
	t.Run("invalid id returns 400", func(t *testing.T) {
		c, w := newContextWithID(t, http.MethodPost, "/api/cover-letters/bad/send", "bad", nil)
		SendCoverLetter(c)

		if w.Code != http.StatusBadRequest {
			t.Fatalf("expected status %d, got %d", http.StatusBadRequest, w.Code)
		}
	})

	t.Run("cover letter not found returns 404", func(t *testing.T) {
		id := primitive.NewObjectID().Hex()
		coverLettersCol := &fakeMongoCollection{findOne: &fakeSingleResult{err: mongo.ErrNoDocuments}}
		fakeDB := &fakeMongoDatabase{collections: map[string]*fakeMongoCollection{"cover-letters": coverLettersCol}}
		setProvidersForTest(t, &fakeMongoClient{db: fakeDB}, nil)

		c, w := newContextWithID(t, http.MethodPost, "/api/cover-letters/"+id+"/send", id, nil)
		c.Set("userId", "user-123")
		SendCoverLetter(c)

		if w.Code != http.StatusNotFound {
			t.Fatalf("expected status %d, got %d", http.StatusNotFound, w.Code)
		}
	})

	t.Run("queue error returns 500", func(t *testing.T) {
		id := primitive.NewObjectID().Hex()
		recipientID := primitive.NewObjectID().Hex()
		coverLettersCol := &fakeMongoCollection{
			findOne: &fakeSingleResult{decodeFn: func(v interface{}) error {
				out := v.(*bson.M)
				*out = bson.M{"recipient_id": recipientID, "cover_letter": "Body"}
				return nil
			}},
		}
		recipientsCol := &fakeMongoCollection{
			findOne: &fakeSingleResult{decodeFn: func(v interface{}) error {
				out := v.(*bson.M)
				*out = bson.M{"email": "sendto@example.com"}
				return nil
			}},
		}
		fakeDB := &fakeMongoDatabase{collections: map[string]*fakeMongoCollection{"cover-letters": coverLettersCol, "recipients": recipientsCol}}
		setProvidersForTest(t, &fakeMongoClient{db: fakeDB}, func(ctx context.Context, queueName string, payload []byte) error {
			return errors.New("queue unavailable")
		})

		c, w := newContextWithID(t, http.MethodPost, "/api/cover-letters/"+id+"/send", id, nil)
		c.Set("userId", "user-123")
		SendCoverLetter(c)

		if w.Code != http.StatusInternalServerError {
			t.Fatalf("expected status %d, got %d", http.StatusInternalServerError, w.Code)
		}
	})

	t.Run("success uses queue env and payload", func(t *testing.T) {
		t.Setenv("EMAILS_TO_SEND_QUEUE", "custom_email_queue")

		id := primitive.NewObjectID().Hex()
		recipientID := primitive.NewObjectID().Hex()
		coverLettersCol := &fakeMongoCollection{
			findOne: &fakeSingleResult{decodeFn: func(v interface{}) error {
				out := v.(*bson.M)
				*out = bson.M{"recipient_id": recipientID, "cover_letter": "Email body"}
				return nil
			}},
		}
		recipientsCol := &fakeMongoCollection{
			findOne: &fakeSingleResult{decodeFn: func(v interface{}) error {
				out := v.(*bson.M)
				*out = bson.M{"email": "hr@example.com"}
				return nil
			}},
		}
		fakeDB := &fakeMongoDatabase{collections: map[string]*fakeMongoCollection{"cover-letters": coverLettersCol, "recipients": recipientsCol}}

		var capturedQueue string
		var capturedPayload []byte
		setProvidersForTest(t, &fakeMongoClient{db: fakeDB}, func(ctx context.Context, queueName string, payload []byte) error {
			capturedQueue = queueName
			capturedPayload = payload
			return nil
		})

		c, w := newContextWithID(t, http.MethodPost, "/api/cover-letters/"+id+"/send", id, nil)
		c.Set("userId", "user-123")
		SendCoverLetter(c)

		if w.Code != http.StatusOK {
			t.Fatalf("expected status %d, got %d", http.StatusOK, w.Code)
		}
		if capturedQueue != "custom_email_queue" {
			t.Fatalf("expected queue custom_email_queue, got %s", capturedQueue)
		}
		var payload map[string]interface{}
		if err := json.Unmarshal(capturedPayload, &payload); err != nil {
			t.Fatalf("failed to decode queue payload: %v", err)
		}
		if payload["recipient"] != "hr@example.com" {
			t.Fatalf("unexpected recipient: %#v", payload["recipient"])
		}
		if payload["user_id"] != "user-123" {
			t.Fatalf("unexpected user_id: %#v", payload["user_id"])
		}
		if payload["cover_letter"] != "Email body" {
			t.Fatalf("unexpected cover_letter: %#v", payload["cover_letter"])
		}
	})
}