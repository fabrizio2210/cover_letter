package handlers

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"

	"github.com/alicebob/miniredis/v2"
	"github.com/fabrizio2210/cover_letter/src/go/cmd/api/models"
	thelpers "github.com/fabrizio2210/cover_letter/src/go/cmd/api/testing"
	"github.com/gin-gonic/gin"
	"github.com/go-redis/redis/v8"
	"github.com/stretchr/testify/require"
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/bson/primitive"
	"go.mongodb.org/mongo-driver/mongo"
)

func TestCreateRecipient_BadRequest(t *testing.T) {
	req, _ := http.NewRequest(http.MethodPost, "/api/recipients", bytes.NewBufferString("notjson"))
	req.Header.Set("Content-Type", "application/json")
	ctx, w := thelpers.CreateGinTestContext(http.MethodPost, "/api/recipients", req)

	CreateRecipient(ctx)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

func TestDeleteRecipient_InvalidID(t *testing.T) {
	ctx, w := thelpers.CreateGinTestContext(http.MethodDelete, "/api/recipients/INVALID", nil)
	ctx.Params = append(ctx.Params, gin.Param{Key: "id", Value: "INVALID"})
	DeleteRecipient(ctx)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

func TestGenerateCoverLetterForRecipient_InvalidID(t *testing.T) {
	ctx, w := thelpers.CreateGinTestContext(http.MethodPost, "/api/recipients/INVALID/generate", nil)
	ctx.Params = append(ctx.Params, gin.Param{Key: "id", Value: "INVALID"})
	GenerateCoverLetterForRecipient(ctx)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

func TestAssociateCompanyWithRecipient_InvalidID(t *testing.T) {
	body := bytes.NewBufferString(`{"companyId":null}`)
	req, _ := http.NewRequest(http.MethodPost, "/api/recipients/INVALID/associate-company", body)
	req.Header.Set("Content-Type", "application/json")
	ctx, w := thelpers.CreateGinTestContext(http.MethodPost, "/api/recipients/INVALID/associate-company", req)
	ctx.Params = append(ctx.Params, gin.Param{Key: "id", Value: "INVALID"})

	AssociateCompanyWithRecipient(ctx)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

// DB + Redis backed tests for recipients

type rFakeCollection struct {
	docs           []bson.M
	insertRes      *mongo.InsertOneResult
	insertErr      error
	findOneDoc     bson.M
	findOneErr     error
	updateRes      *mongo.UpdateResult
	updateErr      error
	deleteRes      *mongo.DeleteResult
	deleteErr      error
	aggErr         error
	allErr         error
	decodeErr      error
	forceNextFalse bool
}

func (f *rFakeCollection) Aggregate(ctx context.Context, pipeline interface{}) (MongoCursorIface, error) {
	if f.aggErr != nil {
		return nil, f.aggErr
	}
	return &rFakeCursor{docs: f.docs, allErr: f.allErr, decodeErr: f.decodeErr, forceNextFalse: f.forceNextFalse}, nil
}
func (f *rFakeCollection) InsertOne(ctx context.Context, doc interface{}) (*mongo.InsertOneResult, error) {
	if f.insertErr != nil {
		return nil, f.insertErr
	}
	if f.insertRes != nil {
		return f.insertRes, nil
	}
	return &mongo.InsertOneResult{InsertedID: primitive.NewObjectID()}, nil
}
func (f *rFakeCollection) FindOne(ctx context.Context, filter interface{}) MongoSingleResultIface {
	return &rFakeSingleResult{doc: f.findOneDoc, err: f.findOneErr}
}
func (f *rFakeCollection) UpdateOne(ctx context.Context, filter interface{}, update interface{}) (*mongo.UpdateResult, error) {
	if f.updateErr != nil {
		return nil, f.updateErr
	}
	if f.updateRes == nil {
		return &mongo.UpdateResult{}, nil
	}
	return f.updateRes, nil
}
func (f *rFakeCollection) DeleteOne(ctx context.Context, filter interface{}) (*mongo.DeleteResult, error) {
	if f.deleteErr != nil {
		return nil, f.deleteErr
	}
	if f.deleteRes == nil {
		return &mongo.DeleteResult{}, nil
	}
	return f.deleteRes, nil
}

type rFakeCursor struct {
	docs           []bson.M
	idx            int
	allErr         error
	decodeErr      error
	forceNextFalse bool
}

func (f *rFakeCursor) All(ctx context.Context, result interface{}) error {
	if f.allErr != nil {
		return f.allErr
	}
	if recipients, ok := result.(*[]models.Recipient); ok {
		out := make([]models.Recipient, 0, len(f.docs))
		for _, doc := range f.docs {
			r := models.Recipient{}
			if rawID, ok := doc["_id"]; ok {
				switch id := rawID.(type) {
				case string:
					r.Id = id
				case primitive.ObjectID:
					r.Id = id.Hex()
				}
			}
			if email, ok := doc["email"].(string); ok {
				r.Email = email
			}
			if name, ok := doc["name"].(string); ok {
				r.Name = name
			}
			if description, ok := doc["description"].(string); ok {
				r.Description = description
			}
			if company, ok := doc["company_id"].(string); ok {
				r.CompanyId = company
			}
			out = append(out, r)
		}
		*recipients = out
		return nil
	}
	b, _ := bson.Marshal(f.docs)
	return bson.Unmarshal(b, result)
}
func (f *rFakeCursor) Next(ctx context.Context) bool {
	if f.forceNextFalse {
		return false
	}
	return f.idx < len(f.docs)
}
func (f *rFakeCursor) Decode(v interface{}) error {
	if f.decodeErr != nil {
		return f.decodeErr
	}
	if f.idx >= len(f.docs) {
		return mongo.ErrNoDocuments
	}
	b, _ := bson.Marshal(f.docs[f.idx])
	f.idx++
	return bson.Unmarshal(b, v)
}
func (f *rFakeCursor) Close(ctx context.Context) error { return nil }

type rFakeSingleResult struct {
	doc bson.M
	err error
}

func (f *rFakeSingleResult) Decode(v interface{}) error {
	if f.err != nil {
		return f.err
	}
	if f.doc == nil {
		return mongo.ErrNoDocuments
	}
	b, _ := bson.Marshal(f.doc)
	return bson.Unmarshal(b, v)
}

type rFakeDB struct{ cols map[string]*rFakeCollection }

func (d *rFakeDB) Collection(name string) MongoCollectionIface {
	if c, ok := d.cols[name]; ok {
		return c
	}
	return &rFakeCollection{}
}

type rFakeClient struct{ db *rFakeDB }

func (c *rFakeClient) Database(name string) MongoDatabaseIface { return c.db }

func TestGenerateCoverLetterForRecipient_QueuesPayload(t *testing.T) {
	m, err := miniredis.Run()
	require.NoError(t, err)
	defer m.Close()

	rclient := redis.NewClient(&redis.Options{Addr: m.Addr()})
	SetRedisClientForTests(rclient)

	rid := primitive.NewObjectID()
	recipDoc := bson.M{"_id": rid, "email": "r@example.com"}

	recCol := &rFakeCollection{findOneDoc: recipDoc}
	fakeDB := &rFakeDB{cols: map[string]*rFakeCollection{"recipients": recCol}}
	fakeClient := &rFakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	req, _ := http.NewRequest(http.MethodPost, "/api/recipients/"+rid.Hex()+"/generate", nil)
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req
	c.Params = append(c.Params, gin.Param{Key: "id", Value: rid.Hex()})

	GenerateCoverLetterForRecipient(c)
	require.Equal(t, http.StatusOK, w.Code)

	q := os.Getenv("REDIS_QUEUE_GENERATE_COVER_LETTER_NAME")
	if q == "" {
		q = "cover_letter_generation_queue"
	}
	require.EqualValues(t, 1, rclient.LLen(context.Background(), q).Val())
	val, err := rclient.LIndex(context.Background(), q, 0).Result()
	require.NoError(t, err)
	var payload map[string]interface{}
	require.NoError(t, json.Unmarshal([]byte(val), &payload))
	require.Equal(t, "r@example.com", payload["recipient"])
}

func TestAssociateCompanyWithRecipient_DBFlows(t *testing.T) {
	// success - set company
	rid := primitive.NewObjectID()
	updateOk := &mongo.UpdateResult{MatchedCount: 1, ModifiedCount: 1}
	rc := &rFakeCollection{updateRes: updateOk}
	fakeDB := &rFakeDB{cols: map[string]*rFakeCollection{"recipients": rc}}
	fakeClient := &rFakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	cid := primitive.NewObjectID()
	body := bytes.NewBufferString(`{"companyId":"` + cid.Hex() + `"}`)
	req, _ := http.NewRequest(http.MethodPost, "/api/recipients/"+rid.Hex()+"/associate-company", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req
	c.Params = append(c.Params, gin.Param{Key: "id", Value: rid.Hex()})

	AssociateCompanyWithRecipient(c)
	require.Equal(t, http.StatusOK, w.Code)
	var resp map[string]interface{}
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &resp))
	// modifiedCount should be present
	require.EqualValues(t, float64(1), resp["modifiedCount"])

	// unset company (CompanyID == null) should also call UpdateOne; simulate 0 modified
	rc.updateRes = &mongo.UpdateResult{MatchedCount: 1, ModifiedCount: 0}
	body2 := bytes.NewBufferString(`{"companyId":null}`)
	req2, _ := http.NewRequest(http.MethodPost, "/api/recipients/"+rid.Hex()+"/associate-company", body2)
	req2.Header.Set("Content-Type", "application/json")
	w2 := httptest.NewRecorder()
	c2, _ := gin.CreateTestContext(w2)
	c2.Request = req2
	c2.Params = append(c2.Params, gin.Param{Key: "id", Value: rid.Hex()})

	AssociateCompanyWithRecipient(c2)
	require.Equal(t, http.StatusOK, w2.Code)
}

func TestCreateRecipient_ReturnsCompanyInfoWhenCompanyProvided(t *testing.T) {
	rid := primitive.NewObjectID()
	cid := primitive.NewObjectID()

	rc := &rFakeCollection{
		insertRes: &mongo.InsertOneResult{InsertedID: rid},
		docs: []bson.M{{
			"_id":         rid,
			"email":       "sdfsf",
			"name":        "sdfgsfd",
			"company_id":  cid,
			"companyInfo": bson.M{"_id": cid, "name": "Arriva"},
		}},
	}
	fakeDB := &rFakeDB{cols: map[string]*rFakeCollection{"recipients": rc}}
	fakeClient := &rFakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	body := bytes.NewBufferString(`{"name":"sdfgsfd","email":"sdfsf","description":"","company_id":"` + cid.Hex() + `"}`)
	req, _ := http.NewRequest(http.MethodPost, "/api/recipients", body)
	req.Header.Set("Content-Type", "application/json")
	ctx, w := thelpers.CreateGinTestContext(http.MethodPost, "/api/recipients", req)

	CreateRecipient(ctx)
	require.Equal(t, http.StatusCreated, w.Code)

	var resp map[string]interface{}
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &resp))
	require.Equal(t, "sdfsf", resp["email"])
	ci, ok := resp["company_info"].(map[string]interface{})
	require.True(t, ok)
	require.Equal(t, "Arriva", ci["name"])
	require.Equal(t, cid.Hex(), resp["company_id"])
}

func TestCreateRecipient_InvalidCompanyID(t *testing.T) {
	body := bytes.NewBufferString(`{"name":"x","email":"x@example.com","company_id":"not-a-hex"}`)
	req, _ := http.NewRequest(http.MethodPost, "/api/recipients", body)
	req.Header.Set("Content-Type", "application/json")
	ctx, w := thelpers.CreateGinTestContext(http.MethodPost, "/api/recipients", req)

	CreateRecipient(ctx)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

func TestGetRecipients_Success(t *testing.T) {
	rid := primitive.NewObjectID()
	rc := &rFakeCollection{docs: []bson.M{{"_id": rid.Hex(), "email": "r@example.com", "name": "R"}}}
	fakeDB := &rFakeDB{cols: map[string]*rFakeCollection{"recipients": rc}}
	fakeClient := &rFakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	ctx, w := thelpers.CreateGinTestContext(http.MethodGet, "/api/recipients", nil)
	GetRecipients(ctx)
	require.Equal(t, http.StatusOK, w.Code)

	var resp []map[string]interface{}
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &resp))
	require.Len(t, resp, 1)
	require.Equal(t, "r@example.com", resp[0]["email"])
}

func TestGetRecipients_AggregateError(t *testing.T) {
	rc := &rFakeCollection{aggErr: errors.New("agg fail")}
	fakeDB := &rFakeDB{cols: map[string]*rFakeCollection{"recipients": rc}}
	fakeClient := &rFakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	ctx, w := thelpers.CreateGinTestContext(http.MethodGet, "/api/recipients", nil)
	GetRecipients(ctx)
	require.Equal(t, http.StatusInternalServerError, w.Code)
}

func TestGetRecipients_DecodeError(t *testing.T) {
	rc := &rFakeCollection{allErr: errors.New("decode fail")}
	fakeDB := &rFakeDB{cols: map[string]*rFakeCollection{"recipients": rc}}
	fakeClient := &rFakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	ctx, w := thelpers.CreateGinTestContext(http.MethodGet, "/api/recipients", nil)
	GetRecipients(ctx)
	require.Equal(t, http.StatusInternalServerError, w.Code)
}

func TestDeleteRecipient_DBFlows(t *testing.T) {
	rid := primitive.NewObjectID()
	rc := &rFakeCollection{deleteRes: &mongo.DeleteResult{DeletedCount: 1}}
	fakeDB := &rFakeDB{cols: map[string]*rFakeCollection{"recipients": rc}}
	fakeClient := &rFakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	ctx, w := thelpers.CreateGinTestContext(http.MethodDelete, "/api/recipients/"+rid.Hex(), nil)
	ctx.Params = append(ctx.Params, gin.Param{Key: "id", Value: rid.Hex()})
	DeleteRecipient(ctx)
	require.Equal(t, http.StatusOK, w.Code)

	rc.deleteRes = &mongo.DeleteResult{DeletedCount: 0}
	ctx2, w2 := thelpers.CreateGinTestContext(http.MethodDelete, "/api/recipients/"+rid.Hex(), nil)
	ctx2.Params = append(ctx2.Params, gin.Param{Key: "id", Value: rid.Hex()})
	DeleteRecipient(ctx2)
	require.Equal(t, http.StatusNotFound, w2.Code)

	rc.deleteErr = errors.New("delete failed")
	ctx3, w3 := thelpers.CreateGinTestContext(http.MethodDelete, "/api/recipients/"+rid.Hex(), nil)
	ctx3.Params = append(ctx3.Params, gin.Param{Key: "id", Value: rid.Hex()})
	DeleteRecipient(ctx3)
	require.Equal(t, http.StatusInternalServerError, w3.Code)
}

func TestUpdateRecipientDescription_DBFlows(t *testing.T) {
	rid := primitive.NewObjectID()
	rc := &rFakeCollection{updateRes: &mongo.UpdateResult{MatchedCount: 1, ModifiedCount: 1}}
	fakeDB := &rFakeDB{cols: map[string]*rFakeCollection{"recipients": rc}}
	fakeClient := &rFakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	body := bytes.NewBufferString(`{"description":"new description"}`)
	req, _ := http.NewRequest(http.MethodPut, "/api/recipients/"+rid.Hex()+"/description", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req
	c.Params = append(c.Params, gin.Param{Key: "id", Value: rid.Hex()})
	UpdateRecipientDescription(c)
	require.Equal(t, http.StatusOK, w.Code)

	rc.updateRes = &mongo.UpdateResult{MatchedCount: 1, ModifiedCount: 0}
	body2 := bytes.NewBufferString(`{"description":"same"}`)
	req2, _ := http.NewRequest(http.MethodPut, "/api/recipients/"+rid.Hex()+"/description", body2)
	req2.Header.Set("Content-Type", "application/json")
	w2 := httptest.NewRecorder()
	c2, _ := gin.CreateTestContext(w2)
	c2.Request = req2
	c2.Params = append(c2.Params, gin.Param{Key: "id", Value: rid.Hex()})
	UpdateRecipientDescription(c2)
	require.Equal(t, http.StatusNotFound, w2.Code)

	rc.updateErr = errors.New("update failed")
	body3 := bytes.NewBufferString(`{"description":"x"}`)
	req3, _ := http.NewRequest(http.MethodPut, "/api/recipients/"+rid.Hex()+"/description", body3)
	req3.Header.Set("Content-Type", "application/json")
	w3 := httptest.NewRecorder()
	c3, _ := gin.CreateTestContext(w3)
	c3.Request = req3
	c3.Params = append(c3.Params, gin.Param{Key: "id", Value: rid.Hex()})
	UpdateRecipientDescription(c3)
	require.Equal(t, http.StatusInternalServerError, w3.Code)
}

func TestUpdateRecipientDescription_InvalidJSON(t *testing.T) {
	rid := primitive.NewObjectID()
	body := bytes.NewBufferString(`{"description":123}`)
	req, _ := http.NewRequest(http.MethodPut, "/api/recipients/"+rid.Hex()+"/description", body)
	req.Header.Set("Content-Type", "application/json")
	ctx, w := thelpers.CreateGinTestContext(http.MethodPut, "/api/recipients/"+rid.Hex()+"/description", req)
	ctx.Params = append(ctx.Params, gin.Param{Key: "id", Value: rid.Hex()})

	UpdateRecipientDescription(ctx)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

func TestUpdateRecipientName_DBFlows(t *testing.T) {
	rid := primitive.NewObjectID()
	rc := &rFakeCollection{updateRes: &mongo.UpdateResult{MatchedCount: 1, ModifiedCount: 1}}
	fakeDB := &rFakeDB{cols: map[string]*rFakeCollection{"recipients": rc}}
	fakeClient := &rFakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	body := bytes.NewBufferString(`{"name":"new name"}`)
	req, _ := http.NewRequest(http.MethodPut, "/api/recipients/"+rid.Hex()+"/name", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req
	c.Params = append(c.Params, gin.Param{Key: "id", Value: rid.Hex()})
	UpdateRecipientName(c)
	require.Equal(t, http.StatusOK, w.Code)

	rc.updateRes = &mongo.UpdateResult{MatchedCount: 1, ModifiedCount: 0}
	body2 := bytes.NewBufferString(`{"name":"same"}`)
	req2, _ := http.NewRequest(http.MethodPut, "/api/recipients/"+rid.Hex()+"/name", body2)
	req2.Header.Set("Content-Type", "application/json")
	w2 := httptest.NewRecorder()
	c2, _ := gin.CreateTestContext(w2)
	c2.Request = req2
	c2.Params = append(c2.Params, gin.Param{Key: "id", Value: rid.Hex()})
	UpdateRecipientName(c2)
	require.Equal(t, http.StatusNotFound, w2.Code)

	rc.updateErr = errors.New("update failed")
	body3 := bytes.NewBufferString(`{"name":"x"}`)
	req3, _ := http.NewRequest(http.MethodPut, "/api/recipients/"+rid.Hex()+"/name", body3)
	req3.Header.Set("Content-Type", "application/json")
	w3 := httptest.NewRecorder()
	c3, _ := gin.CreateTestContext(w3)
	c3.Request = req3
	c3.Params = append(c3.Params, gin.Param{Key: "id", Value: rid.Hex()})
	UpdateRecipientName(c3)
	require.Equal(t, http.StatusInternalServerError, w3.Code)
}

func TestUpdateRecipientName_InvalidJSON(t *testing.T) {
	rid := primitive.NewObjectID()
	body := bytes.NewBufferString(`{"name":123}`)
	req, _ := http.NewRequest(http.MethodPut, "/api/recipients/"+rid.Hex()+"/name", body)
	req.Header.Set("Content-Type", "application/json")
	ctx, w := thelpers.CreateGinTestContext(http.MethodPut, "/api/recipients/"+rid.Hex()+"/name", req)
	ctx.Params = append(ctx.Params, gin.Param{Key: "id", Value: rid.Hex()})

	UpdateRecipientName(ctx)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

func TestCreateRecipient_FailureBranches(t *testing.T) {
	cid := primitive.NewObjectID()
	body := bytes.NewBufferString(`{"name":"x","email":"x@example.com","description":"","company_id":"` + cid.Hex() + `"}`)

	// InsertOne failure.
	rc := &rFakeCollection{insertErr: errors.New("insert fail")}
	fakeDB := &rFakeDB{cols: map[string]*rFakeCollection{"recipients": rc}}
	fakeClient := &rFakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	req, _ := http.NewRequest(http.MethodPost, "/api/recipients", body)
	req.Header.Set("Content-Type", "application/json")
	ctx, w := thelpers.CreateGinTestContext(http.MethodPost, "/api/recipients", req)
	CreateRecipient(ctx)
	require.Equal(t, http.StatusInternalServerError, w.Code)

	// InsertedID type mismatch.
	rc.insertErr = nil
	rc.insertRes = &mongo.InsertOneResult{InsertedID: "not-object-id"}
	body2 := bytes.NewBufferString(`{"name":"x","email":"x@example.com"}`)
	req2, _ := http.NewRequest(http.MethodPost, "/api/recipients", body2)
	req2.Header.Set("Content-Type", "application/json")
	ctx2, w2 := thelpers.CreateGinTestContext(http.MethodPost, "/api/recipients", req2)
	CreateRecipient(ctx2)
	require.Equal(t, http.StatusInternalServerError, w2.Code)

	// Aggregate failure after insert.
	rid := primitive.NewObjectID()
	rc.insertRes = &mongo.InsertOneResult{InsertedID: rid}
	rc.aggErr = errors.New("agg fail")
	body3 := bytes.NewBufferString(`{"name":"x","email":"x@example.com"}`)
	req3, _ := http.NewRequest(http.MethodPost, "/api/recipients", body3)
	req3.Header.Set("Content-Type", "application/json")
	ctx3, w3 := thelpers.CreateGinTestContext(http.MethodPost, "/api/recipients", req3)
	CreateRecipient(ctx3)
	require.Equal(t, http.StatusInternalServerError, w3.Code)

	// Cursor.Next false.
	rc.aggErr = nil
	rc.docs = nil
	rc.forceNextFalse = true
	body4 := bytes.NewBufferString(`{"name":"x","email":"x@example.com"}`)
	req4, _ := http.NewRequest(http.MethodPost, "/api/recipients", body4)
	req4.Header.Set("Content-Type", "application/json")
	ctx4, w4 := thelpers.CreateGinTestContext(http.MethodPost, "/api/recipients", req4)
	CreateRecipient(ctx4)
	require.Equal(t, http.StatusInternalServerError, w4.Code)

	// Cursor decode failure.
	rc.forceNextFalse = false
	rc.docs = []bson.M{{"_id": rid, "email": "x@example.com"}}
	rc.decodeErr = errors.New("decode fail")
	body5 := bytes.NewBufferString(`{"name":"x","email":"x@example.com"}`)
	req5, _ := http.NewRequest(http.MethodPost, "/api/recipients", body5)
	req5.Header.Set("Content-Type", "application/json")
	ctx5, w5 := thelpers.CreateGinTestContext(http.MethodPost, "/api/recipients", req5)
	CreateRecipient(ctx5)
	require.Equal(t, http.StatusInternalServerError, w5.Code)
}

func TestGenerateCoverLetterForRecipient_RecipientNotFound(t *testing.T) {
	rid := primitive.NewObjectID()
	rc := &rFakeCollection{findOneErr: mongo.ErrNoDocuments}
	fakeDB := &rFakeDB{cols: map[string]*rFakeCollection{"recipients": rc}}
	fakeClient := &rFakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	req, _ := http.NewRequest(http.MethodPost, "/api/recipients/"+rid.Hex()+"/generate", nil)
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req
	c.Params = append(c.Params, gin.Param{Key: "id", Value: rid.Hex()})

	GenerateCoverLetterForRecipient(c)
	require.Equal(t, http.StatusNotFound, w.Code)
}

func TestGenerateCoverLetterForRecipient_RedisFailure(t *testing.T) {
	rid := primitive.NewObjectID()
	rc := &rFakeCollection{findOneDoc: bson.M{"_id": rid, "email": "r@example.com"}}
	fakeDB := &rFakeDB{cols: map[string]*rFakeCollection{"recipients": rc}}
	fakeClient := &rFakeClient{db: fakeDB}

	oldMongo := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = oldMongo }()

	oldRedis := rdb
	badRedis := redis.NewClient(&redis.Options{Addr: "127.0.0.1:1"})
	SetRedisClientForTests(badRedis)
	defer func() {
		SetRedisClientForTests(oldRedis)
		_ = badRedis.Close()
	}()

	req, _ := http.NewRequest(http.MethodPost, "/api/recipients/"+rid.Hex()+"/generate", nil)
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req
	c.Params = append(c.Params, gin.Param{Key: "id", Value: rid.Hex()})

	GenerateCoverLetterForRecipient(c)
	require.Equal(t, http.StatusInternalServerError, w.Code)
}

func TestAssociateCompanyWithRecipient_FailureBranches(t *testing.T) {
	rid := primitive.NewObjectID()
	rc := &rFakeCollection{updateRes: &mongo.UpdateResult{MatchedCount: 1, ModifiedCount: 1}}
	fakeDB := &rFakeDB{cols: map[string]*rFakeCollection{"recipients": rc}}
	fakeClient := &rFakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	// Invalid JSON body.
	body := bytes.NewBufferString(`notjson`)
	req, _ := http.NewRequest(http.MethodPost, "/api/recipients/"+rid.Hex()+"/associate-company", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req
	c.Params = append(c.Params, gin.Param{Key: "id", Value: rid.Hex()})
	AssociateCompanyWithRecipient(c)
	require.Equal(t, http.StatusBadRequest, w.Code)

	// Invalid company ID.
	body2 := bytes.NewBufferString(`{"companyId":"not-a-hex"}`)
	req2, _ := http.NewRequest(http.MethodPost, "/api/recipients/"+rid.Hex()+"/associate-company", body2)
	req2.Header.Set("Content-Type", "application/json")
	w2 := httptest.NewRecorder()
	c2, _ := gin.CreateTestContext(w2)
	c2.Request = req2
	c2.Params = append(c2.Params, gin.Param{Key: "id", Value: rid.Hex()})
	AssociateCompanyWithRecipient(c2)
	require.Equal(t, http.StatusBadRequest, w2.Code)

	// DB update failure.
	rc.updateErr = errors.New("update failed")
	body3 := bytes.NewBufferString(`{"companyId":null}`)
	req3, _ := http.NewRequest(http.MethodPost, "/api/recipients/"+rid.Hex()+"/associate-company", body3)
	req3.Header.Set("Content-Type", "application/json")
	w3 := httptest.NewRecorder()
	c3, _ := gin.CreateTestContext(w3)
	c3.Request = req3
	c3.Params = append(c3.Params, gin.Param{Key: "id", Value: rid.Hex()})
	AssociateCompanyWithRecipient(c3)
	require.Equal(t, http.StatusInternalServerError, w3.Code)
}
