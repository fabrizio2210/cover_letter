package recipients

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"testing"

	"github.com/fabrizio2210/cover_letter/src/go/cmd/api/models"
	apptest "github.com/fabrizio2210/cover_letter/src/go/cmd/api/testing"
	"github.com/gin-gonic/gin"
	"go.mongodb.org/mongo-driver/bson/primitive"
	"go.mongodb.org/mongo-driver/mongo"
)

// ---- Mock implementations ----

type mockMongoClient struct {
	collection *mockMongoCollection
}

func (m *mockMongoClient) Database(_ string) MongoDatabaseIface {
	return &mockMongoDatabase{collection: m.collection}
}

type mockMongoDatabase struct {
	collection *mockMongoCollection
}

func (m *mockMongoDatabase) Collection(_ string) MongoCollectionIface {
	return m.collection
}

type mockMongoCollection struct {
	aggregateCursor MongoCursorIface
	aggregateErr    error
	insertResult    *mongo.InsertOneResult
	insertErr       error
	findOneResult   MongoSingleResultIface
	updateResult    *mongo.UpdateResult
	updateErr       error
	deleteResult    *mongo.DeleteResult
	deleteErr       error
}

func (m *mockMongoCollection) Aggregate(_ context.Context, _ interface{}) (MongoCursorIface, error) {
	return m.aggregateCursor, m.aggregateErr
}

func (m *mockMongoCollection) InsertOne(_ context.Context, _ interface{}) (*mongo.InsertOneResult, error) {
	return m.insertResult, m.insertErr
}

func (m *mockMongoCollection) FindOne(_ context.Context, _ interface{}) MongoSingleResultIface {
	return m.findOneResult
}

func (m *mockMongoCollection) UpdateOne(_ context.Context, _ interface{}, _ interface{}) (*mongo.UpdateResult, error) {
	return m.updateResult, m.updateErr
}

func (m *mockMongoCollection) DeleteOne(_ context.Context, _ interface{}) (*mongo.DeleteResult, error) {
	return m.deleteResult, m.deleteErr
}

type mockMongoCursor struct {
	recipients []models.Recipient
	index      int
	allErr     error
}

func (m *mockMongoCursor) All(_ context.Context, result interface{}) error {
	if m.allErr != nil {
		return m.allErr
	}
	if r, ok := result.(*[]models.Recipient); ok {
		*r = m.recipients
	}
	return nil
}

func (m *mockMongoCursor) Next(_ context.Context) bool {
	return m.index < len(m.recipients)
}

func (m *mockMongoCursor) Decode(v interface{}) error {
	if m.index >= len(m.recipients) {
		return errors.New("no more items")
	}
	if r, ok := v.(*models.Recipient); ok {
		*r = m.recipients[m.index]
		m.index++
		return nil
	}
	return errors.New("unsupported decode target type")
}

func (m *mockMongoCursor) Close(_ context.Context) error { return nil }

type mockMongoSingleResult struct {
	recipient *models.Recipient
	err       error
}

func (m *mockMongoSingleResult) Decode(v interface{}) error {
	if m.err != nil {
		return m.err
	}
	if r, ok := v.(*models.Recipient); ok {
		*r = *m.recipient
	}
	return nil
}

// ---- Helpers ----

func setMockClient(col *mockMongoCollection) func() {
	orig := getMongoClient
	SetMongoClientProvider(func() MongoClientIface {
		return &mockMongoClient{collection: col}
	})
	return func() { getMongoClient = orig }
}

// ---- GetRecipients ----

func TestGetRecipients_Success(t *testing.T) {
	recipients := []models.Recipient{{Email: "a@b.com", Name: "Alice"}}
	col := &mockMongoCollection{
		aggregateCursor: &mockMongoCursor{recipients: recipients},
	}
	defer setMockClient(col)()

	c, w := apptest.CreateGinTestContext(http.MethodGet, "/api/recipients", nil)
	GetRecipients(c)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var got []models.Recipient
	if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0].Email != "a@b.com" {
		t.Fatalf("unexpected body: %s", w.Body.String())
	}
}

func TestGetRecipients_AggregateError(t *testing.T) {
	col := &mockMongoCollection{
		aggregateErr: errors.New("db error"),
	}
	defer setMockClient(col)()

	c, w := apptest.CreateGinTestContext(http.MethodGet, "/api/recipients", nil)
	GetRecipients(c)

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("expected 500, got %d", w.Code)
	}
}

func TestGetRecipients_Empty(t *testing.T) {
	col := &mockMongoCollection{
		aggregateCursor: &mockMongoCursor{recipients: []models.Recipient{}},
	}
	defer setMockClient(col)()

	c, w := apptest.CreateGinTestContext(http.MethodGet, "/api/recipients", nil)
	GetRecipients(c)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
	var got []models.Recipient
	if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
		t.Fatal(err)
	}
	if len(got) != 0 {
		t.Fatalf("expected empty slice, got: %s", w.Body.String())
	}
}

// ---- CreateRecipient ----

func TestCreateRecipient_Valid(t *testing.T) {
	insertedID := primitive.NewObjectID()
	created := models.Recipient{Email: "test@test.com", Name: "Test"}
	col := &mockMongoCollection{
		insertResult:    &mongo.InsertOneResult{InsertedID: insertedID},
		aggregateCursor: &mockMongoCursor{recipients: []models.Recipient{created}},
	}
	defer setMockClient(col)()

	body, _ := json.Marshal(map[string]string{"email": "test@test.com", "name": "Test"})
	req, _ := http.NewRequest(http.MethodPost, "/api/recipients", bytes.NewBuffer(body))
	req.Header.Set("Content-Type", "application/json")
	c, w := apptest.CreateGinTestContext(http.MethodPost, "/api/recipients", req)
	CreateRecipient(c)

	if w.Code != http.StatusCreated {
		t.Fatalf("expected 201, got %d: %s", w.Code, w.Body.String())
	}
}

func TestCreateRecipient_InvalidJSON(t *testing.T) {
	col := &mockMongoCollection{}
	defer setMockClient(col)()

	req, _ := http.NewRequest(http.MethodPost, "/api/recipients", bytes.NewBufferString("bad json"))
	req.Header.Set("Content-Type", "application/json")
	c, w := apptest.CreateGinTestContext(http.MethodPost, "/api/recipients", req)
	CreateRecipient(c)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", w.Code)
	}
}

func TestCreateRecipient_InvalidCompanyID(t *testing.T) {
	col := &mockMongoCollection{}
	defer setMockClient(col)()

	body, _ := json.Marshal(map[string]string{"email": "a@b.com", "company_id": "not-valid-hex"})
	req, _ := http.NewRequest(http.MethodPost, "/api/recipients", bytes.NewBuffer(body))
	req.Header.Set("Content-Type", "application/json")
	c, w := apptest.CreateGinTestContext(http.MethodPost, "/api/recipients", req)
	CreateRecipient(c)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

func TestCreateRecipient_InsertError(t *testing.T) {
	col := &mockMongoCollection{
		insertErr: errors.New("insert failed"),
	}
	defer setMockClient(col)()

	body, _ := json.Marshal(map[string]string{"email": "a@b.com"})
	req, _ := http.NewRequest(http.MethodPost, "/api/recipients", bytes.NewBuffer(body))
	req.Header.Set("Content-Type", "application/json")
	c, w := apptest.CreateGinTestContext(http.MethodPost, "/api/recipients", req)
	CreateRecipient(c)

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("expected 500, got %d", w.Code)
	}
}

func TestCreateRecipient_PostInsertAggregateError(t *testing.T) {
	insertedID := primitive.NewObjectID()
	col := &mockMongoCollection{
		insertResult: &mongo.InsertOneResult{InsertedID: insertedID},
		aggregateErr: errors.New("aggregate failed"),
	}
	defer setMockClient(col)()

	body, _ := json.Marshal(map[string]string{"email": "a@b.com"})
	req, _ := http.NewRequest(http.MethodPost, "/api/recipients", bytes.NewBuffer(body))
	req.Header.Set("Content-Type", "application/json")
	c, w := apptest.CreateGinTestContext(http.MethodPost, "/api/recipients", req)
	CreateRecipient(c)

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("expected 500, got %d", w.Code)
	}
}

// ---- DeleteRecipient ----

func TestDeleteRecipient_Success(t *testing.T) {
	col := &mockMongoCollection{
		deleteResult: &mongo.DeleteResult{DeletedCount: 1},
	}
	defer setMockClient(col)()

	c, w := apptest.CreateGinTestContext(http.MethodDelete, "/api/recipients/id", nil)
	c.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}
	DeleteRecipient(c)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
}

func TestDeleteRecipient_InvalidID(t *testing.T) {
	col := &mockMongoCollection{}
	defer setMockClient(col)()

	c, w := apptest.CreateGinTestContext(http.MethodDelete, "/api/recipients/bad", nil)
	c.Params = gin.Params{{Key: "id", Value: "not-a-valid-id"}}
	DeleteRecipient(c)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", w.Code)
	}
}

func TestDeleteRecipient_NotFound(t *testing.T) {
	col := &mockMongoCollection{
		deleteResult: &mongo.DeleteResult{DeletedCount: 0},
	}
	defer setMockClient(col)()

	c, w := apptest.CreateGinTestContext(http.MethodDelete, "/api/recipients/id", nil)
	c.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}
	DeleteRecipient(c)

	if w.Code != http.StatusNotFound {
		t.Fatalf("expected 404, got %d", w.Code)
	}
}

func TestDeleteRecipient_DBError(t *testing.T) {
	col := &mockMongoCollection{
		deleteErr: errors.New("db error"),
	}
	defer setMockClient(col)()

	c, w := apptest.CreateGinTestContext(http.MethodDelete, "/api/recipients/id", nil)
	c.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}
	DeleteRecipient(c)

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("expected 500, got %d", w.Code)
	}
}

// ---- UpdateRecipientDescription ----

func TestUpdateRecipientDescription_Success(t *testing.T) {
	col := &mockMongoCollection{
		updateResult: &mongo.UpdateResult{ModifiedCount: 1},
	}
	defer setMockClient(col)()

	body, _ := json.Marshal(map[string]string{"description": "new desc"})
	req, _ := http.NewRequest(http.MethodPut, "/api/recipients/id/description", bytes.NewBuffer(body))
	req.Header.Set("Content-Type", "application/json")
	c, w := apptest.CreateGinTestContext(http.MethodPut, "/api/recipients/id/description", req)
	c.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}
	UpdateRecipientDescription(c)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
}

func TestUpdateRecipientDescription_InvalidID(t *testing.T) {
	col := &mockMongoCollection{}
	defer setMockClient(col)()

	c, w := apptest.CreateGinTestContext(http.MethodPut, "/api/recipients/bad/description", nil)
	c.Params = gin.Params{{Key: "id", Value: "not-valid"}}
	UpdateRecipientDescription(c)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", w.Code)
	}
}

func TestUpdateRecipientDescription_NotFound(t *testing.T) {
	col := &mockMongoCollection{
		updateResult: &mongo.UpdateResult{ModifiedCount: 0},
	}
	defer setMockClient(col)()

	body, _ := json.Marshal(map[string]string{"description": "new desc"})
	req, _ := http.NewRequest(http.MethodPut, "/api/recipients/id/description", bytes.NewBuffer(body))
	req.Header.Set("Content-Type", "application/json")
	c, w := apptest.CreateGinTestContext(http.MethodPut, "/api/recipients/id/description", req)
	c.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}
	UpdateRecipientDescription(c)

	if w.Code != http.StatusNotFound {
		t.Fatalf("expected 404, got %d", w.Code)
	}
}

func TestUpdateRecipientDescription_DBError(t *testing.T) {
	col := &mockMongoCollection{
		updateErr: errors.New("db error"),
	}
	defer setMockClient(col)()

	body, _ := json.Marshal(map[string]string{"description": "new desc"})
	req, _ := http.NewRequest(http.MethodPut, "/api/recipients/id/description", bytes.NewBuffer(body))
	req.Header.Set("Content-Type", "application/json")
	c, w := apptest.CreateGinTestContext(http.MethodPut, "/api/recipients/id/description", req)
	c.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}
	UpdateRecipientDescription(c)

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("expected 500, got %d", w.Code)
	}
}

// ---- UpdateRecipientName ----

func TestUpdateRecipientName_Success(t *testing.T) {
	col := &mockMongoCollection{
		updateResult: &mongo.UpdateResult{ModifiedCount: 1},
	}
	defer setMockClient(col)()

	body, _ := json.Marshal(map[string]string{"name": "New Name"})
	req, _ := http.NewRequest(http.MethodPut, "/api/recipients/id/name", bytes.NewBuffer(body))
	req.Header.Set("Content-Type", "application/json")
	c, w := apptest.CreateGinTestContext(http.MethodPut, "/api/recipients/id/name", req)
	c.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}
	UpdateRecipientName(c)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
}

func TestUpdateRecipientName_InvalidID(t *testing.T) {
	col := &mockMongoCollection{}
	defer setMockClient(col)()

	c, w := apptest.CreateGinTestContext(http.MethodPut, "/api/recipients/bad/name", nil)
	c.Params = gin.Params{{Key: "id", Value: "not-valid"}}
	UpdateRecipientName(c)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", w.Code)
	}
}

func TestUpdateRecipientName_NotFound(t *testing.T) {
	col := &mockMongoCollection{
		updateResult: &mongo.UpdateResult{ModifiedCount: 0},
	}
	defer setMockClient(col)()

	body, _ := json.Marshal(map[string]string{"name": "New Name"})
	req, _ := http.NewRequest(http.MethodPut, "/api/recipients/id/name", bytes.NewBuffer(body))
	req.Header.Set("Content-Type", "application/json")
	c, w := apptest.CreateGinTestContext(http.MethodPut, "/api/recipients/id/name", req)
	c.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}
	UpdateRecipientName(c)

	if w.Code != http.StatusNotFound {
		t.Fatalf("expected 404, got %d", w.Code)
	}
}

func TestUpdateRecipientName_DBError(t *testing.T) {
	col := &mockMongoCollection{
		updateErr: errors.New("db error"),
	}
	defer setMockClient(col)()

	body, _ := json.Marshal(map[string]string{"name": "New Name"})
	req, _ := http.NewRequest(http.MethodPut, "/api/recipients/id/name", bytes.NewBuffer(body))
	req.Header.Set("Content-Type", "application/json")
	c, w := apptest.CreateGinTestContext(http.MethodPut, "/api/recipients/id/name", req)
	c.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}
	UpdateRecipientName(c)

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("expected 500, got %d", w.Code)
	}
}

// ---- AssociateCompanyWithRecipient ----

func TestAssociateCompany_SetCompanyID(t *testing.T) {
	col := &mockMongoCollection{
		updateResult: &mongo.UpdateResult{ModifiedCount: 1},
	}
	defer setMockClient(col)()

	companyID := primitive.NewObjectID().Hex()
	body, _ := json.Marshal(map[string]string{"companyId": companyID})
	req, _ := http.NewRequest(http.MethodPut, "/api/recipients/id/company", bytes.NewBuffer(body))
	req.Header.Set("Content-Type", "application/json")
	c, w := apptest.CreateGinTestContext(http.MethodPut, "/api/recipients/id/company", req)
	c.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}
	AssociateCompanyWithRecipient(c)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestAssociateCompany_UnsetCompanyID(t *testing.T) {
	col := &mockMongoCollection{
		updateResult: &mongo.UpdateResult{ModifiedCount: 1},
	}
	defer setMockClient(col)()

	body, _ := json.Marshal(map[string]*string{"companyId": nil})
	req, _ := http.NewRequest(http.MethodPut, "/api/recipients/id/company", bytes.NewBuffer(body))
	req.Header.Set("Content-Type", "application/json")
	c, w := apptest.CreateGinTestContext(http.MethodPut, "/api/recipients/id/company", req)
	c.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}
	AssociateCompanyWithRecipient(c)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestAssociateCompany_InvalidRecipientID(t *testing.T) {
	col := &mockMongoCollection{}
	defer setMockClient(col)()

	c, w := apptest.CreateGinTestContext(http.MethodPut, "/api/recipients/bad/company", nil)
	c.Params = gin.Params{{Key: "id", Value: "bad-id"}}
	AssociateCompanyWithRecipient(c)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", w.Code)
	}
}

func TestAssociateCompany_InvalidCompanyID(t *testing.T) {
	col := &mockMongoCollection{}
	defer setMockClient(col)()

	badID := "not-valid-hex"
	body, _ := json.Marshal(map[string]*string{"companyId": &badID})
	req, _ := http.NewRequest(http.MethodPut, "/api/recipients/id/company", bytes.NewBuffer(body))
	req.Header.Set("Content-Type", "application/json")
	c, w := apptest.CreateGinTestContext(http.MethodPut, "/api/recipients/id/company", req)
	c.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}
	AssociateCompanyWithRecipient(c)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

func TestAssociateCompany_DBError(t *testing.T) {
	col := &mockMongoCollection{
		updateErr: errors.New("db error"),
	}
	defer setMockClient(col)()

	companyID := primitive.NewObjectID().Hex()
	body, _ := json.Marshal(map[string]string{"companyId": companyID})
	req, _ := http.NewRequest(http.MethodPut, "/api/recipients/id/company", bytes.NewBuffer(body))
	req.Header.Set("Content-Type", "application/json")
	c, w := apptest.CreateGinTestContext(http.MethodPut, "/api/recipients/id/company", req)
	c.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}
	AssociateCompanyWithRecipient(c)

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("expected 500, got %d", w.Code)
	}
}

// ---- GenerateCoverLetterForRecipient ----

func TestGenerateCoverLetter_Success(t *testing.T) {
	recipient := &models.Recipient{Email: "test@example.com"}
	col := &mockMongoCollection{
		findOneResult: &mockMongoSingleResult{recipient: recipient},
	}
	defer setMockClient(col)()

	origQueue := queuePush
	defer func() { queuePush = origQueue }()
	SetQueuePushProvider(func(_ context.Context, _ string, _ []byte) error { return nil })

	c, w := apptest.CreateGinTestContext(http.MethodPost, "/api/recipients/id/generate-cover-letter", nil)
	c.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}
	GenerateCoverLetterForRecipient(c)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestGenerateCoverLetter_InvalidID(t *testing.T) {
	col := &mockMongoCollection{}
	defer setMockClient(col)()

	c, w := apptest.CreateGinTestContext(http.MethodPost, "/api/recipients/bad/generate-cover-letter", nil)
	c.Params = gin.Params{{Key: "id", Value: "bad-id"}}
	GenerateCoverLetterForRecipient(c)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", w.Code)
	}
}

func TestGenerateCoverLetter_RecipientNotFound(t *testing.T) {
	col := &mockMongoCollection{
		findOneResult: &mockMongoSingleResult{err: errors.New("not found")},
	}
	defer setMockClient(col)()

	c, w := apptest.CreateGinTestContext(http.MethodPost, "/api/recipients/id/generate-cover-letter", nil)
	c.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}
	GenerateCoverLetterForRecipient(c)

	if w.Code != http.StatusNotFound {
		t.Fatalf("expected 404, got %d", w.Code)
	}
}

func TestGenerateCoverLetter_QueueError(t *testing.T) {
	recipient := &models.Recipient{Email: "test@example.com"}
	col := &mockMongoCollection{
		findOneResult: &mockMongoSingleResult{recipient: recipient},
	}
	defer setMockClient(col)()

	origQueue := queuePush
	defer func() { queuePush = origQueue }()
	SetQueuePushProvider(func(_ context.Context, _ string, _ []byte) error {
		return errors.New("queue error")
	})

	c, w := apptest.CreateGinTestContext(http.MethodPost, "/api/recipients/id/generate-cover-letter", nil)
	c.Params = gin.Params{{Key: "id", Value: primitive.NewObjectID().Hex()}}
	GenerateCoverLetterForRecipient(c)

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("expected 500, got %d", w.Code)
	}
}
