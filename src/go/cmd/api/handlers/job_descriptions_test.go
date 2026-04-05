package handlers

import (
	"bytes"
	"encoding/json"
	"net/http"
	"testing"

	thelpers "github.com/fabrizio2210/cover_letter/src/go/cmd/api/testing"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/bson/primitive"
	"go.mongodb.org/mongo-driver/mongo"
)

func TestGetJobDescriptions_NormalizesIDsAndScores(t *testing.T) {
	jobID := primitive.NewObjectID()
	companyID := primitive.NewObjectID()
	scoreID := primitive.NewObjectID()
	identityID := primitive.NewObjectID()

	jobsCollection := &fakeCollection{docs: []bson.M{{
		"_id":            jobID,
		"company_id":     companyID,
		"title":          "Platform Engineer",
		"weighted_score": 4.5,
		"companyInfo": bson.M{
			"_id":   companyID,
			"name":  "Acme",
			"field": "engineering",
		},
	}}}
	scoresCollection := &fakeCollection{docs: []bson.M{{
		"_id":              scoreID,
		"job_id":           jobID,
		"identity_id":      identityID,
		"preference_key":   "remote_work",
		"preference_label": "Remote work",
		"score":            5,
	}}}

	fakeDB := &fakeDatabase{cols: map[string]*fakeCollection{
		"job-descriptions":      jobsCollection,
		"job-preference-scores": scoresCollection,
	}}
	fakeClient := &fakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	ctx, w := thelpers.CreateGinTestContext(http.MethodGet, "/api/job-descriptions", nil)
	GetJobDescriptions(ctx)

	require.Equal(t, http.StatusOK, w.Code)
	var resp []map[string]interface{}
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &resp))
	require.Len(t, resp, 1)
	require.Equal(t, jobID.Hex(), resp[0]["id"])
	require.Equal(t, companyID.Hex(), resp[0]["company_id"])

	companyInfo, ok := resp[0]["company_info"].(map[string]interface{})
	require.True(t, ok)
	require.Equal(t, companyID.Hex(), companyInfo["id"])

	scores, ok := resp[0]["scores"].([]interface{})
	require.True(t, ok)
	require.Len(t, scores, 1)
	score, ok := scores[0].(map[string]interface{})
	require.True(t, ok)
	require.Equal(t, scoreID.Hex(), score["id"])
	require.Equal(t, identityID.Hex(), score["identity_id"])
}

func TestGetJobDescription_NotFound(t *testing.T) {
	jobsCollection := &fakeCollection{docs: []bson.M{}}
	fakeDB := &fakeDatabase{cols: map[string]*fakeCollection{"job-descriptions": jobsCollection}}
	fakeClient := &fakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	id := primitive.NewObjectID().Hex()
	req, _ := http.NewRequest(http.MethodGet, "/api/job-descriptions/"+id, nil)
	ctx, w := thelpers.CreateGinTestContext(http.MethodGet, "/api/job-descriptions/"+id, req)
	ctx.Params = append(ctx.Params, gin.Param{Key: "id", Value: id})

	GetJobDescription(ctx)
	require.Equal(t, http.StatusNotFound, w.Code)
}

func TestGetJobDescriptions_FallsBackToLegacyJobsCollection(t *testing.T) {
	jobID := primitive.NewObjectID()
	companyID := primitive.NewObjectID()

	jobDescriptionsCollection := &fakeCollection{docs: []bson.M{}}
	legacyJobsCollection := &fakeCollection{docs: []bson.M{{
		"_id":            jobID,
		"company_id":     companyID,
		"title":          "Legacy Job",
		"weighted_score": 0.0,
	}}}

	fakeDB := &fakeDatabase{cols: map[string]*fakeCollection{
		"job-descriptions":      jobDescriptionsCollection,
		"jobs":                  legacyJobsCollection,
		"job-preference-scores": &fakeCollection{docs: []bson.M{}},
	}}
	fakeClient := &fakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	ctx, w := thelpers.CreateGinTestContext(http.MethodGet, "/api/job-descriptions", nil)
	GetJobDescriptions(ctx)

	require.Equal(t, http.StatusOK, w.Code)
	var resp []map[string]interface{}
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &resp))
	require.Len(t, resp, 1)
	require.Equal(t, "Legacy Job", resp[0]["title"])
}

func TestCreateJobDescription_InvalidCompanyID(t *testing.T) {
	fakeDB := &fakeDatabase{cols: map[string]*fakeCollection{"job-descriptions": &fakeCollection{}}}
	fakeClient := &fakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	body := bytes.NewBufferString(`{"company_id":"bad","title":"Engineer"}`)
	req, _ := http.NewRequest(http.MethodPost, "/api/job-descriptions", body)
	req.Header.Set("Content-Type", "application/json")
	ctx, w := thelpers.CreateGinTestContext(http.MethodPost, "/api/job-descriptions", req)

	CreateJobDescription(ctx)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

func TestUpdateJobDescription_InvalidID(t *testing.T) {
	body := bytes.NewBufferString(`{"title":"Updated"}`)
	req, _ := http.NewRequest(http.MethodPut, "/api/job-descriptions/invalid", body)
	req.Header.Set("Content-Type", "application/json")
	ctx, w := thelpers.CreateGinTestContext(http.MethodPut, "/api/job-descriptions/invalid", req)
	ctx.Params = append(ctx.Params, gin.Param{Key: "id", Value: "invalid"})

	UpdateJobDescription(ctx)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

func TestDeleteJobDescription_NotFound(t *testing.T) {
	jobsCollection := &fakeCollection{deleteRes: &mongo.DeleteResult{DeletedCount: 0}}
	fakeDB := &fakeDatabase{cols: map[string]*fakeCollection{"job-descriptions": jobsCollection}}
	fakeClient := &fakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	id := primitive.NewObjectID().Hex()
	req, _ := http.NewRequest(http.MethodDelete, "/api/job-descriptions/"+id, nil)
	ctx, w := thelpers.CreateGinTestContext(http.MethodDelete, "/api/job-descriptions/"+id, req)
	ctx.Params = append(ctx.Params, gin.Param{Key: "id", Value: id})

	DeleteJobDescription(ctx)
	require.Equal(t, http.StatusNotFound, w.Code)
}

func TestScoreJobDescription_InvalidID(t *testing.T) {
	ctx, w := thelpers.CreateGinTestContext(http.MethodPost, "/api/job-descriptions/invalid/score", nil)
	ctx.Params = append(ctx.Params, gin.Param{Key: "id", Value: "invalid"})

	ScoreJobDescription(ctx)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

func TestUpdateIdentityPreferences_DuplicateKey(t *testing.T) {
	id := primitive.NewObjectID().Hex()
	body := bytes.NewBufferString(`{"preferences":[{"key":"remote","weight":1,"enabled":true},{"key":"remote","weight":2,"enabled":true}]}`)
	req, _ := http.NewRequest(http.MethodPut, "/api/identities/"+id+"/preferences", body)
	req.Header.Set("Content-Type", "application/json")
	ctx, w := thelpers.CreateGinTestContext(http.MethodPut, "/api/identities/"+id+"/preferences", req)
	ctx.Params = append(ctx.Params, gin.Param{Key: "id", Value: id})

	UpdateIdentityPreferences(ctx)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

func TestUpdateIdentityPreferences_Success(t *testing.T) {
	fc := &fakeCollection{updateRes: &mongo.UpdateResult{MatchedCount: 1, ModifiedCount: 1}}
	fakeDB := &fakeDatabase{cols: map[string]*fakeCollection{"identities": fc}}
	fakeClient := &fakeClient{db: fakeDB}

	old := GetMongoClient
	GetMongoClient = func() MongoClientIface { return fakeClient }
	defer func() { GetMongoClient = old }()

	id := primitive.NewObjectID().Hex()
	body := bytes.NewBufferString(`{"preferences":[{"key":"remote_work","label":"Remote work","weight":2,"enabled":true,"guidance":"Prefer remote"}]}`)
	req, _ := http.NewRequest(http.MethodPut, "/api/identities/"+id+"/preferences", body)
	req.Header.Set("Content-Type", "application/json")
	ctx, w := thelpers.CreateGinTestContext(http.MethodPut, "/api/identities/"+id+"/preferences", req)
	ctx.Params = append(ctx.Params, gin.Param{Key: "id", Value: id})

	UpdateIdentityPreferences(ctx)
	require.Equal(t, http.StatusOK, w.Code)

	updateDoc, ok := fc.updateDoc.(bson.M)
	require.True(t, ok)
	setDoc, ok := updateDoc["$set"].(bson.M)
	require.True(t, ok)
	_, ok = setDoc["preferences"]
	require.True(t, ok)
}
