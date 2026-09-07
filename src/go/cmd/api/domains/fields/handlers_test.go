package fields

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"reflect"
	"strings"
	"sync"
	"testing"

	apitesting "github.com/fabrizio2210/cover_letter/src/go/cmd/api/testing"
	"github.com/gin-gonic/gin"
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/bson/primitive"
	"go.mongodb.org/mongo-driver/mongo"
	"go.mongodb.org/mongo-driver/mongo/options"
)

type mockClient struct {
	db MongoDatabaseIface
}

func (m *mockClient) Database(_ string) MongoDatabaseIface {
	return m.db
}

type mockDatabase struct {
	col MongoCollectionIface
}

func (m *mockDatabase) Collection(_ string) MongoCollectionIface {
	return m.col
}

type mockCollection struct {
	aggregateFn      func(ctx context.Context, pipeline interface{}) (MongoCursorIface, error)
	bulkWriteFn      func(ctx context.Context, models []mongo.WriteModel, opts ...*options.BulkWriteOptions) (*mongo.BulkWriteResult, error)
	countDocumentsFn func(ctx context.Context, filter interface{}, opts ...*options.CountOptions) (int64, error)
	insertOneFn      func(ctx context.Context, doc interface{}) (*mongo.InsertOneResult, error)
	findOneFn        func(ctx context.Context, filter interface{}) MongoSingleResultIface
	updateOneFn      func(ctx context.Context, filter interface{}, update interface{}) (*mongo.UpdateResult, error)
	deleteOneFn      func(ctx context.Context, filter interface{}) (*mongo.DeleteResult, error)
}

func (m *mockCollection) Aggregate(ctx context.Context, pipeline interface{}) (MongoCursorIface, error) {
	if m.aggregateFn != nil {
		return m.aggregateFn(ctx, pipeline)
	}
	return &mockCursor{}, nil
}

func (m *mockCollection) BulkWrite(ctx context.Context, models []mongo.WriteModel, opts ...*options.BulkWriteOptions) (*mongo.BulkWriteResult, error) {
	if m.bulkWriteFn != nil {
		return m.bulkWriteFn(ctx, models, opts...)
	}
	return &mongo.BulkWriteResult{}, nil
}

func (m *mockCollection) CountDocuments(ctx context.Context, filter interface{}, opts ...*options.CountOptions) (int64, error) {
	if m.countDocumentsFn != nil {
		return m.countDocumentsFn(ctx, filter, opts...)
	}
	return 0, nil
}

func (m *mockCollection) InsertOne(ctx context.Context, doc interface{}) (*mongo.InsertOneResult, error) {
	if m.insertOneFn != nil {
		return m.insertOneFn(ctx, doc)
	}
	return &mongo.InsertOneResult{InsertedID: primitive.NewObjectID()}, nil
}

func (m *mockCollection) FindOne(ctx context.Context, filter interface{}) MongoSingleResultIface {
	if m.findOneFn != nil {
		return m.findOneFn(ctx, filter)
	}
	return &mockSingleResult{err: mongo.ErrNoDocuments}
}

func (m *mockCollection) UpdateOne(ctx context.Context, filter interface{}, update interface{}) (*mongo.UpdateResult, error) {
	if m.updateOneFn != nil {
		return m.updateOneFn(ctx, filter, update)
	}
	return &mongo.UpdateResult{}, nil
}

func (m *mockCollection) DeleteOne(ctx context.Context, filter interface{}) (*mongo.DeleteResult, error) {
	if m.deleteOneFn != nil {
		return m.deleteOneFn(ctx, filter)
	}
	return &mongo.DeleteResult{}, nil
}

type mockCursor struct {
	docs      []bson.M
	idx       int
	decodeErr error
}

func (m *mockCursor) Next(_ context.Context) bool {
	return m.idx < len(m.docs)
}

func (m *mockCursor) Decode(v interface{}) error {
	if m.decodeErr != nil {
		return m.decodeErr
	}
	out, ok := v.(*bson.M)
	if !ok {
		return errors.New("Decode target must be *bson.M")
	}
	if m.idx >= len(m.docs) {
		return errors.New("Decode called with no remaining docs")
	}
	copied := bson.M{}
	for k, val := range m.docs[m.idx] {
		copied[k] = val
	}
	*out = copied
	m.idx++
	return nil
}

func (m *mockCursor) Close(_ context.Context) error {
	return nil
}

type mockSingleResult struct {
	err  error
	doc  bson.M
	docF func(v interface{}) error
}

func (m *mockSingleResult) Decode(v interface{}) error {
	if m.docF != nil {
		return m.docF(v)
	}
	if m.err != nil {
		return m.err
	}
	out, ok := v.(*bson.M)
	if !ok {
		return errors.New("Decode target must be *bson.M")
	}
	copied := bson.M{}
	for k, val := range m.doc {
		copied[k] = val
	}
	*out = copied
	return nil
}

func setMockProvider(t *testing.T, col *mockCollection) {
	t.Helper()
	previous := getMongoClient
	SetMongoClientProvider(func() MongoClientIface {
		return &mockClient{db: &mockDatabase{col: col}}
	})
	t.Cleanup(func() {
		getMongoClient = previous
	})
}

func newJSONRequest(t *testing.T, method, path, payload string) *http.Request {
	t.Helper()
	req, err := http.NewRequest(method, path, strings.NewReader(payload))
	if err != nil {
		t.Fatalf("failed to create request: %v", err)
	}
	req.Header.Set("Content-Type", "application/json")
	return req
}

func decodeJSONMap(t *testing.T, raw string) map[string]interface{} {
	t.Helper()
	var out map[string]interface{}
	if err := json.Unmarshal([]byte(raw), &out); err != nil {
		t.Fatalf("failed to decode JSON map: %v", err)
	}
	return out
}

func decodeJSONArray(t *testing.T, raw string) []map[string]interface{} {
	t.Helper()
	var out []map[string]interface{}
	if err := json.Unmarshal([]byte(raw), &out); err != nil {
		t.Fatalf("failed to decode JSON array: %v", err)
	}
	return out
}

func bootstrapModelDetails(t *testing.T, model mongo.WriteModel) (primitive.ObjectID, string) {
	t.Helper()
	updateModel, ok := model.(*mongo.UpdateOneModel)
	if !ok {
		t.Fatalf("expected *mongo.UpdateOneModel, got %T", model)
	}
	if updateModel.Upsert == nil || !*updateModel.Upsert {
		t.Fatal("expected bootstrap update to enable upsert")
	}

	filter, ok := updateModel.Filter.(bson.M)
	if !ok {
		t.Fatalf("expected bson.M filter, got %T", updateModel.Filter)
	}
	id, ok := filter["_id"].(primitive.ObjectID)
	if !ok {
		t.Fatalf("expected ObjectID filter, got %T", filter["_id"])
	}

	update, ok := updateModel.Update.(bson.M)
	if !ok {
		t.Fatalf("expected bson.M update, got %T", updateModel.Update)
	}
	setOnInsert, ok := update["$setOnInsert"].(bson.M)
	if !ok {
		t.Fatalf("expected $setOnInsert document, got %T", update["$setOnInsert"])
	}
	name, ok := setOnInsert["field"].(string)
	if !ok {
		t.Fatalf("expected string field name, got %T", setOnInsert["field"])
	}
	return id, name
}

func TestDefaultFieldPreset(t *testing.T) {
	expected := []string{
		"Technology",
		"Financial Services",
		"Healthcare",
		"Education",
		"Retail & E-commerce",
		"Manufacturing",
		"Construction",
		"Real Estate",
		"Energy & Utilities",
		"Transportation & Logistics",
		"Telecommunications",
		"Media & Entertainment",
		"Advertising & Marketing",
		"Professional Services",
		"Legal Services",
		"Government & Public Sector",
		"Nonprofit & Charities",
		"Hospitality & Tourism",
		"Food & Beverage",
		"Agriculture",
		"Automotive",
		"Aerospace & Defense",
		"Pharmaceuticals & Biotechnology",
		"Insurance",
		"Consumer Goods",
		"Fashion & Apparel",
		"Sports & Fitness",
		"Environmental Services",
		"Arts & Culture",
		"Other",
	}
	if !reflect.DeepEqual(defaultFieldNames[:], expected) {
		t.Fatalf("unexpected default field preset:\nwant: %#v\n got: %#v", expected, defaultFieldNames)
	}

	ids := make(map[primitive.ObjectID]string, len(defaultFieldNames))
	for _, name := range defaultFieldNames {
		id := defaultFieldObjectID(name)
		if existingName, exists := ids[id]; exists {
			t.Fatalf("default fields %q and %q share ID %s", existingName, name, id.Hex())
		}
		ids[id] = name
	}

	stableIDs := map[string]string{
		"Technology": "8dea0bfcf9d91b5e52f9a5f6",
		"Other":      "797a7f1426d9dea29bbee388",
	}
	for name, expectedID := range stableIDs {
		if id := defaultFieldObjectID(name).Hex(); id != expectedID {
			t.Fatalf("default field %q has ID %s, want %s", name, id, expectedID)
		}
	}
}

func TestBootstrapDefaults(t *testing.T) {
	t.Run("non_empty_collection_is_unchanged", func(t *testing.T) {
		bulkWriteCalled := false
		setMockProvider(t, &mockCollection{
			countDocumentsFn: func(_ context.Context, filter interface{}, _ ...*options.CountOptions) (int64, error) {
				if !reflect.DeepEqual(filter, bson.D{}) {
					t.Fatalf("expected empty count filter, got %#v", filter)
				}
				return 1, nil
			},
			bulkWriteFn: func(_ context.Context, _ []mongo.WriteModel, _ ...*options.BulkWriteOptions) (*mongo.BulkWriteResult, error) {
				bulkWriteCalled = true
				return &mongo.BulkWriteResult{}, nil
			},
		})

		if err := BootstrapDefaults(context.Background()); err != nil {
			t.Fatalf("BootstrapDefaults returned error: %v", err)
		}
		if bulkWriteCalled {
			t.Fatal("expected no bootstrap write for a non-empty collection")
		}
	})

	t.Run("empty_collection_receives_preset", func(t *testing.T) {
		var capturedModels []mongo.WriteModel
		var capturedOptions []*options.BulkWriteOptions
		setMockProvider(t, &mockCollection{
			countDocumentsFn: func(_ context.Context, _ interface{}, _ ...*options.CountOptions) (int64, error) {
				return 0, nil
			},
			bulkWriteFn: func(_ context.Context, models []mongo.WriteModel, opts ...*options.BulkWriteOptions) (*mongo.BulkWriteResult, error) {
				capturedModels = models
				capturedOptions = opts
				return &mongo.BulkWriteResult{UpsertedCount: int64(len(models))}, nil
			},
		})

		if err := BootstrapDefaults(context.Background()); err != nil {
			t.Fatalf("BootstrapDefaults returned error: %v", err)
		}
		if len(capturedModels) != len(defaultFieldNames) {
			t.Fatalf("expected %d writes, got %d", len(defaultFieldNames), len(capturedModels))
		}
		if len(capturedOptions) != 1 || capturedOptions[0].Ordered == nil || *capturedOptions[0].Ordered {
			t.Fatalf("expected one unordered bulk-write option, got %#v", capturedOptions)
		}

		for i, model := range capturedModels {
			id, name := bootstrapModelDetails(t, model)
			if name != defaultFieldNames[i] {
				t.Fatalf("write %d has field %q, want %q", i, name, defaultFieldNames[i])
			}
			if id != defaultFieldObjectID(name) {
				t.Fatalf("write %d has ID %s, want %s", i, id.Hex(), defaultFieldObjectID(name).Hex())
			}
		}
	})

	t.Run("count_error_is_returned", func(t *testing.T) {
		expectedErr := errors.New("count failed")
		bulkWriteCalled := false
		setMockProvider(t, &mockCollection{
			countDocumentsFn: func(_ context.Context, _ interface{}, _ ...*options.CountOptions) (int64, error) {
				return 0, expectedErr
			},
			bulkWriteFn: func(_ context.Context, _ []mongo.WriteModel, _ ...*options.BulkWriteOptions) (*mongo.BulkWriteResult, error) {
				bulkWriteCalled = true
				return &mongo.BulkWriteResult{}, nil
			},
		})

		err := BootstrapDefaults(context.Background())
		if !errors.Is(err, expectedErr) {
			t.Fatalf("expected wrapped count error, got %v", err)
		}
		if bulkWriteCalled {
			t.Fatal("expected no bootstrap write after count failure")
		}
	})

	t.Run("bulk_write_error_is_returned", func(t *testing.T) {
		expectedErr := errors.New("bulk write failed")
		setMockProvider(t, &mockCollection{
			countDocumentsFn: func(_ context.Context, _ interface{}, _ ...*options.CountOptions) (int64, error) {
				return 0, nil
			},
			bulkWriteFn: func(_ context.Context, _ []mongo.WriteModel, _ ...*options.BulkWriteOptions) (*mongo.BulkWriteResult, error) {
				return nil, expectedErr
			},
		})

		if err := BootstrapDefaults(context.Background()); !errors.Is(err, expectedErr) {
			t.Fatalf("expected wrapped bulk-write error, got %v", err)
		}
	})
}

func TestConcurrentBootstrapCallsUseSameUpsertIDs(t *testing.T) {
	var mu sync.Mutex
	var batches [][]mongo.WriteModel
	setMockProvider(t, &mockCollection{
		countDocumentsFn: func(_ context.Context, _ interface{}, _ ...*options.CountOptions) (int64, error) {
			return 0, nil
		},
		bulkWriteFn: func(_ context.Context, models []mongo.WriteModel, _ ...*options.BulkWriteOptions) (*mongo.BulkWriteResult, error) {
			mu.Lock()
			batches = append(batches, append([]mongo.WriteModel(nil), models...))
			mu.Unlock()
			return &mongo.BulkWriteResult{}, nil
		},
	})

	start := make(chan struct{})
	errs := make(chan error, 2)
	for i := 0; i < 2; i++ {
		go func() {
			<-start
			errs <- BootstrapDefaults(context.Background())
		}()
	}
	close(start)
	for i := 0; i < 2; i++ {
		if err := <-errs; err != nil {
			t.Fatalf("BootstrapDefaults returned error: %v", err)
		}
	}

	if len(batches) != 2 {
		t.Fatalf("expected two bootstrap batches, got %d", len(batches))
	}
	for i := range batches[0] {
		firstID, firstName := bootstrapModelDetails(t, batches[0][i])
		secondID, secondName := bootstrapModelDetails(t, batches[1][i])
		if firstID != secondID || firstName != secondName {
			t.Fatalf("bootstrap callers targeted different documents at index %d", i)
		}
	}
}

func TestCreateField(t *testing.T) {
	t.Run("invalid_json", func(t *testing.T) {
		req := newJSONRequest(t, http.MethodPost, "/api/fields", "{")
		c, w := apitesting.CreateGinTestContext(http.MethodPost, "/api/fields", req)

		CreateField(c)

		if w.Code != http.StatusBadRequest {
			t.Fatalf("expected status %d, got %d", http.StatusBadRequest, w.Code)
		}
	})

	t.Run("insert_error", func(t *testing.T) {
		setMockProvider(t, &mockCollection{
			insertOneFn: func(_ context.Context, _ interface{}) (*mongo.InsertOneResult, error) {
				return nil, errors.New("insert failed")
			},
		})

		req := newJSONRequest(t, http.MethodPost, "/api/fields", `{"field":"Backend"}`)
		c, w := apitesting.CreateGinTestContext(http.MethodPost, "/api/fields", req)

		CreateField(c)

		if w.Code != http.StatusInternalServerError {
			t.Fatalf("expected status %d, got %d", http.StatusInternalServerError, w.Code)
		}
	})

	t.Run("create_returns_created_document", func(t *testing.T) {
		oid := primitive.NewObjectID()
		setMockProvider(t, &mockCollection{
			insertOneFn: func(_ context.Context, _ interface{}) (*mongo.InsertOneResult, error) {
				return &mongo.InsertOneResult{InsertedID: oid}, nil
			},
			findOneFn: func(_ context.Context, _ interface{}) MongoSingleResultIface {
				return &mockSingleResult{doc: bson.M{"_id": oid, "field": "Backend"}}
			},
		})

		req := newJSONRequest(t, http.MethodPost, "/api/fields", `{"field":"Backend"}`)
		c, w := apitesting.CreateGinTestContext(http.MethodPost, "/api/fields", req)

		CreateField(c)

		if w.Code != http.StatusCreated {
			t.Fatalf("expected status %d, got %d", http.StatusCreated, w.Code)
		}
		body := decodeJSONMap(t, w.Body.String())
		if body["_id"] != oid.Hex() {
			t.Fatalf("expected _id %q, got %#v", oid.Hex(), body["_id"])
		}
		if body["field"] != "Backend" {
			t.Fatalf("expected field %q, got %#v", "Backend", body["field"])
		}
	})

	t.Run("findone_fallback_with_objectid", func(t *testing.T) {
		oid := primitive.NewObjectID()
		setMockProvider(t, &mockCollection{
			insertOneFn: func(_ context.Context, _ interface{}) (*mongo.InsertOneResult, error) {
				return &mongo.InsertOneResult{InsertedID: oid}, nil
			},
			findOneFn: func(_ context.Context, _ interface{}) MongoSingleResultIface {
				return &mockSingleResult{err: errors.New("not found")}
			},
		})

		req := newJSONRequest(t, http.MethodPost, "/api/fields", `{"field":"Backend"}`)
		c, w := apitesting.CreateGinTestContext(http.MethodPost, "/api/fields", req)

		CreateField(c)

		if w.Code != http.StatusCreated {
			t.Fatalf("expected status %d, got %d", http.StatusCreated, w.Code)
		}
		body := decodeJSONMap(t, w.Body.String())
		if body["_id"] != oid.Hex() {
			t.Fatalf("expected _id %q, got %#v", oid.Hex(), body["_id"])
		}
	})

	t.Run("findone_fallback_with_generic_id", func(t *testing.T) {
		setMockProvider(t, &mockCollection{
			insertOneFn: func(_ context.Context, _ interface{}) (*mongo.InsertOneResult, error) {
				return &mongo.InsertOneResult{InsertedID: "custom-id"}, nil
			},
			findOneFn: func(_ context.Context, _ interface{}) MongoSingleResultIface {
				return &mockSingleResult{err: errors.New("not found")}
			},
		})

		req := newJSONRequest(t, http.MethodPost, "/api/fields", `{"field":"Backend"}`)
		c, w := apitesting.CreateGinTestContext(http.MethodPost, "/api/fields", req)

		CreateField(c)

		if w.Code != http.StatusCreated {
			t.Fatalf("expected status %d, got %d", http.StatusCreated, w.Code)
		}
		body := decodeJSONMap(t, w.Body.String())
		if body["insertedId"] != "custom-id" {
			t.Fatalf("expected insertedId %q, got %#v", "custom-id", body["insertedId"])
		}
	})
}

func TestGetFields(t *testing.T) {
	t.Run("aggregate_error", func(t *testing.T) {
		setMockProvider(t, &mockCollection{
			aggregateFn: func(_ context.Context, _ interface{}) (MongoCursorIface, error) {
				return nil, errors.New("aggregate failed")
			},
		})

		c, w := apitesting.CreateGinTestContext(http.MethodGet, "/api/fields", nil)
		GetFields(c)

		if w.Code != http.StatusInternalServerError {
			t.Fatalf("expected status %d, got %d", http.StatusInternalServerError, w.Code)
		}
	})

	t.Run("empty_list", func(t *testing.T) {
		setMockProvider(t, &mockCollection{
			aggregateFn: func(_ context.Context, _ interface{}) (MongoCursorIface, error) {
				return &mockCursor{docs: []bson.M{}}, nil
			},
		})

		c, w := apitesting.CreateGinTestContext(http.MethodGet, "/api/fields", nil)
		GetFields(c)

		if w.Code != http.StatusOK {
			t.Fatalf("expected status %d, got %d", http.StatusOK, w.Code)
		}
		items := decodeJSONArray(t, w.Body.String())
		if len(items) != 0 {
			t.Fatalf("expected empty array, got length %d", len(items))
		}
	})

	t.Run("objectid_is_normalized_to_id", func(t *testing.T) {
		oid := primitive.NewObjectID()
		setMockProvider(t, &mockCollection{
			aggregateFn: func(_ context.Context, _ interface{}) (MongoCursorIface, error) {
				return &mockCursor{docs: []bson.M{{"_id": oid, "field": "Backend"}}}, nil
			},
		})

		c, w := apitesting.CreateGinTestContext(http.MethodGet, "/api/fields", nil)
		GetFields(c)

		if w.Code != http.StatusOK {
			t.Fatalf("expected status %d, got %d", http.StatusOK, w.Code)
		}
		items := decodeJSONArray(t, w.Body.String())
		if len(items) != 1 {
			t.Fatalf("expected one item, got %d", len(items))
		}
		if items[0]["id"] != oid.Hex() {
			t.Fatalf("expected id %q, got %#v", oid.Hex(), items[0]["id"])
		}
		if _, exists := items[0]["_id"]; exists {
			t.Fatalf("expected _id to be removed, got %#v", items[0]["_id"])
		}
	})

	t.Run("string_id_is_normalized_to_id", func(t *testing.T) {
		setMockProvider(t, &mockCollection{
			aggregateFn: func(_ context.Context, _ interface{}) (MongoCursorIface, error) {
				return &mockCursor{docs: []bson.M{{"_id": "legacy-id", "field": "Backend"}}}, nil
			},
		})

		c, w := apitesting.CreateGinTestContext(http.MethodGet, "/api/fields", nil)
		GetFields(c)

		if w.Code != http.StatusOK {
			t.Fatalf("expected status %d, got %d", http.StatusOK, w.Code)
		}
		items := decodeJSONArray(t, w.Body.String())
		if len(items) != 1 {
			t.Fatalf("expected one item, got %d", len(items))
		}
		if items[0]["id"] != "legacy-id" {
			t.Fatalf("expected id %q, got %#v", "legacy-id", items[0]["id"])
		}
		if _, exists := items[0]["_id"]; exists {
			t.Fatalf("expected _id to be removed, got %#v", items[0]["_id"])
		}
	})

	t.Run("decode_error", func(t *testing.T) {
		setMockProvider(t, &mockCollection{
			aggregateFn: func(_ context.Context, _ interface{}) (MongoCursorIface, error) {
				return &mockCursor{docs: []bson.M{{"_id": primitive.NewObjectID()}}, decodeErr: errors.New("decode failed")}, nil
			},
		})

		c, w := apitesting.CreateGinTestContext(http.MethodGet, "/api/fields", nil)
		GetFields(c)

		if w.Code != http.StatusInternalServerError {
			t.Fatalf("expected status %d, got %d", http.StatusInternalServerError, w.Code)
		}
	})
}

func TestDeleteField(t *testing.T) {
	t.Run("invalid_id", func(t *testing.T) {
		c, w := apitesting.CreateGinTestContext(http.MethodDelete, "/api/fields/bad", nil)
		c.Params = gin.Params{{Key: "id", Value: "not-hex"}}

		DeleteField(c)

		if w.Code != http.StatusBadRequest {
			t.Fatalf("expected status %d, got %d", http.StatusBadRequest, w.Code)
		}
	})

	t.Run("delete_error", func(t *testing.T) {
		setMockProvider(t, &mockCollection{
			deleteOneFn: func(_ context.Context, _ interface{}) (*mongo.DeleteResult, error) {
				return nil, errors.New("delete failed")
			},
		})
		id := primitive.NewObjectID().Hex()
		c, w := apitesting.CreateGinTestContext(http.MethodDelete, "/api/fields/"+id, nil)
		c.Params = gin.Params{{Key: "id", Value: id}}

		DeleteField(c)

		if w.Code != http.StatusInternalServerError {
			t.Fatalf("expected status %d, got %d", http.StatusInternalServerError, w.Code)
		}
	})

	t.Run("not_found", func(t *testing.T) {
		setMockProvider(t, &mockCollection{
			deleteOneFn: func(_ context.Context, _ interface{}) (*mongo.DeleteResult, error) {
				return &mongo.DeleteResult{DeletedCount: 0}, nil
			},
		})
		id := primitive.NewObjectID().Hex()
		c, w := apitesting.CreateGinTestContext(http.MethodDelete, "/api/fields/"+id, nil)
		c.Params = gin.Params{{Key: "id", Value: id}}

		DeleteField(c)

		if w.Code != http.StatusNotFound {
			t.Fatalf("expected status %d, got %d", http.StatusNotFound, w.Code)
		}
	})

	t.Run("deleted", func(t *testing.T) {
		setMockProvider(t, &mockCollection{
			deleteOneFn: func(_ context.Context, _ interface{}) (*mongo.DeleteResult, error) {
				return &mongo.DeleteResult{DeletedCount: 1}, nil
			},
		})
		id := primitive.NewObjectID().Hex()
		c, w := apitesting.CreateGinTestContext(http.MethodDelete, "/api/fields/"+id, nil)
		c.Params = gin.Params{{Key: "id", Value: id}}

		DeleteField(c)

		if w.Code != http.StatusOK {
			t.Fatalf("expected status %d, got %d", http.StatusOK, w.Code)
		}
		body := decodeJSONMap(t, w.Body.String())
		if body["message"] != "Field deleted successfully" {
			t.Fatalf("expected success message, got %#v", body["message"])
		}
	})
}

func TestUpdateField(t *testing.T) {
	t.Run("invalid_id", func(t *testing.T) {
		req := newJSONRequest(t, http.MethodPut, "/api/fields/bad", `{"field":"Backend"}`)
		c, w := apitesting.CreateGinTestContext(http.MethodPut, "/api/fields/bad", req)
		c.Params = gin.Params{{Key: "id", Value: "not-hex"}}

		UpdateField(c)

		if w.Code != http.StatusBadRequest {
			t.Fatalf("expected status %d, got %d", http.StatusBadRequest, w.Code)
		}
	})

	t.Run("invalid_json", func(t *testing.T) {
		id := primitive.NewObjectID().Hex()
		req := newJSONRequest(t, http.MethodPut, "/api/fields/"+id, "{")
		c, w := apitesting.CreateGinTestContext(http.MethodPut, "/api/fields/"+id, req)
		c.Params = gin.Params{{Key: "id", Value: id}}

		UpdateField(c)

		if w.Code != http.StatusBadRequest {
			t.Fatalf("expected status %d, got %d", http.StatusBadRequest, w.Code)
		}
	})

	t.Run("update_error", func(t *testing.T) {
		setMockProvider(t, &mockCollection{
			updateOneFn: func(_ context.Context, _ interface{}, _ interface{}) (*mongo.UpdateResult, error) {
				return nil, errors.New("update failed")
			},
		})
		id := primitive.NewObjectID().Hex()
		req := newJSONRequest(t, http.MethodPut, "/api/fields/"+id, `{"field":"Backend"}`)
		c, w := apitesting.CreateGinTestContext(http.MethodPut, "/api/fields/"+id, req)
		c.Params = gin.Params{{Key: "id", Value: id}}

		UpdateField(c)

		if w.Code != http.StatusInternalServerError {
			t.Fatalf("expected status %d, got %d", http.StatusInternalServerError, w.Code)
		}
	})

	t.Run("not_found_or_unchanged", func(t *testing.T) {
		setMockProvider(t, &mockCollection{
			updateOneFn: func(_ context.Context, _ interface{}, _ interface{}) (*mongo.UpdateResult, error) {
				return &mongo.UpdateResult{ModifiedCount: 0}, nil
			},
		})
		id := primitive.NewObjectID().Hex()
		req := newJSONRequest(t, http.MethodPut, "/api/fields/"+id, `{"field":"Backend"}`)
		c, w := apitesting.CreateGinTestContext(http.MethodPut, "/api/fields/"+id, req)
		c.Params = gin.Params{{Key: "id", Value: id}}

		UpdateField(c)

		if w.Code != http.StatusNotFound {
			t.Fatalf("expected status %d, got %d", http.StatusNotFound, w.Code)
		}
	})

	t.Run("updated", func(t *testing.T) {
		setMockProvider(t, &mockCollection{
			updateOneFn: func(_ context.Context, _ interface{}, _ interface{}) (*mongo.UpdateResult, error) {
				return &mongo.UpdateResult{ModifiedCount: 1}, nil
			},
		})
		id := primitive.NewObjectID().Hex()
		req := newJSONRequest(t, http.MethodPut, "/api/fields/"+id, `{"field":"Backend"}`)
		c, w := apitesting.CreateGinTestContext(http.MethodPut, "/api/fields/"+id, req)
		c.Params = gin.Params{{Key: "id", Value: id}}

		UpdateField(c)

		if w.Code != http.StatusOK {
			t.Fatalf("expected status %d, got %d", http.StatusOK, w.Code)
		}
		body := decodeJSONMap(t, w.Body.String())
		if body["message"] != "Field updated successfully" {
			t.Fatalf("expected success message, got %#v", body["message"])
		}
	})
}
