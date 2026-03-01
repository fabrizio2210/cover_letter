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
	thelpers "github.com/fabrizio2210/cover_letter/src/go/cmd/api/testing"
	"github.com/gin-gonic/gin"
	"github.com/go-redis/redis/v8"
	"github.com/stretchr/testify/require"
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/bson/primitive"
	"go.mongodb.org/mongo-driver/mongo"
)

func TestGetCoverLetter_InvalidID(t *testing.T) {
	ctx, w := thelpers.CreateGinTestContext(http.MethodGet, "/api/cover-letters/INVALID", nil)
	ctx.Params = append(ctx.Params, gin.Param{Key: "id", Value: "INVALID"})
	GetCoverLetter(ctx)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

func TestRefineCoverLetter_BadRequest(t *testing.T) {
	// invalid JSON body
	body := bytes.NewBufferString("notjson")
	req, _ := http.NewRequest(http.MethodPost, "/api/cover-letters/invalid/refine", body)
	req.Header.Set("Content-Type", "application/json")
	ctx, w := thelpers.CreateGinTestContext(http.MethodPost, "/api/cover-letters/invalid/refine", req)
	ctx.Params = append(ctx.Params, gin.Param{Key: "id", Value: "invalid"})

	RefineCoverLetter(ctx)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

func TestSendCoverLetter_InvalidID(t *testing.T) {
	ctx, w := thelpers.CreateGinTestContext(http.MethodPost, "/api/cover-letters/INVALID/send", nil)
	ctx.Params = append(ctx.Params, gin.Param{Key: "id", Value: "INVALID"})
	SendCoverLetter(ctx)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

// DB + Redis backed tests

type clFakeCollection struct {
	findOneDoc bson.M
	aggErr     error
}

func (f *clFakeCollection) Aggregate(ctx context.Context, pipeline interface{}) (MongoCursorIface, error) {
	if f.aggErr != nil {
		return nil, f.aggErr
	}
	return &fakeCursor{docs: []bson.M{}}, nil
}
func (f *clFakeCollection) InsertOne(ctx context.Context, doc interface{}) (*mongo.InsertOneResult, error) {
	return nil, nil
}
func (f *clFakeCollection) FindOne(ctx context.Context, filter interface{}) MongoSingleResultIface {
	return &fakeSingleResult{doc: f.findOneDoc}
}
func (f *clFakeCollection) UpdateOne(ctx context.Context, filter interface{}, update interface{}) (*mongo.UpdateResult, error) {
	return nil, nil
}
func (f *clFakeCollection) DeleteOne(ctx context.Context, filter interface{}) (*mongo.DeleteResult, error) {
	return nil, nil
}

type clFakeDB struct{ cols map[string]*clFakeCollection }

func (d *clFakeDB) Collection(name string) MongoCollectionIface {
	if c, ok := d.cols[name]; ok {
		return c
	}
	return &clFakeCollection{}
}

type clFakeClient struct{ db *clFakeDB }

func (c *clFakeClient) Database(name string) MongoDatabaseIface { return c.db }

func TestRefineCoverLetter_QueuesPayload(t *testing.T) {
	m, err := miniredis.Run()
	require.NoError(t, err)
	defer m.Close()

	rclient := redis.NewClient(&redis.Options{Addr: m.Addr()})
	SetRedisClientForTests(rclient)

	clID := primitive.NewObjectID()
	coverDoc := bson.M{"_id": clID, "recipient_id": "rec-1", "conversation_id": "conv-1"}
	recipDoc := bson.M{"_id": "rec-1", "email": "to@example.com"}

	clCol := &clFakeCollection{findOneDoc: coverDoc}
	recCol := &clFakeCollection{findOneDoc: recipDoc}
	fakeDB := &clFakeDB{cols: map[string]*clFakeCollection{"cover-letters": clCol, "recipients": recCol}}
	fakeClient := &clFakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	body := bytes.NewBufferString(`{"prompt":"improve"}`)
	req, _ := http.NewRequest(http.MethodPost, "/api/cover-letters/"+clID.Hex()+"/refine", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req
	c.Params = append(c.Params, gin.Param{Key: "id", Value: clID.Hex()})

	RefineCoverLetter(c)
	require.Equal(t, http.StatusOK, w.Code)

	q := os.Getenv("REDIS_QUEUE_GENERATE_COVER_LETTER_NAME")
	if q == "" {
		q = "cover_letter_generation_queue"
	}
	llen := rclient.LLen(context.Background(), q)
	require.EqualValues(t, 1, llen.Val())
	val, err := rclient.LIndex(context.Background(), q, 0).Result()
	require.NoError(t, err)
	var payload map[string]interface{}
	require.NoError(t, json.Unmarshal([]byte(val), &payload))
	require.Equal(t, "to@example.com", payload["recipient"])
}

func TestGetCoverLetter_AggregateError(t *testing.T) {
	clID := primitive.NewObjectID()
	aggErr := errors.New("agg fail")
	clCol := &clFakeCollection{aggErr: aggErr}
	fakeDB := &clFakeDB{cols: map[string]*clFakeCollection{"cover-letters": clCol}}
	fakeClient := &clFakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	req, _ := http.NewRequest(http.MethodGet, "/api/cover-letters/"+clID.Hex(), nil)
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req
	c.Params = append(c.Params, gin.Param{Key: "id", Value: clID.Hex()})

	GetCoverLetter(c)
	require.Equal(t, http.StatusInternalServerError, w.Code)
}
