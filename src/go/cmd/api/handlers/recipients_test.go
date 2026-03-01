package handlers

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"

	"github.com/alicebob/miniredis/v2"
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
	findOneDoc bson.M
	updateRes  *mongo.UpdateResult
}

func (f *rFakeCollection) Aggregate(ctx context.Context, pipeline interface{}) (MongoCursorIface, error) {
	return &fakeCursor{docs: []bson.M{}}, nil
}
func (f *rFakeCollection) InsertOne(ctx context.Context, doc interface{}) (*mongo.InsertOneResult, error) {
	return nil, nil
}
func (f *rFakeCollection) FindOne(ctx context.Context, filter interface{}) MongoSingleResultIface {
	return &fakeSingleResult{doc: f.findOneDoc}
}
func (f *rFakeCollection) UpdateOne(ctx context.Context, filter interface{}, update interface{}) (*mongo.UpdateResult, error) {
	return f.updateRes, nil
}
func (f *rFakeCollection) DeleteOne(ctx context.Context, filter interface{}) (*mongo.DeleteResult, error) {
	return nil, nil
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
