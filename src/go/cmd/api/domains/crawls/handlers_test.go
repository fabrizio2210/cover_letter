package crawls

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/fabrizio2210/cover_letter/src/go/cmd/api/models"
	testctx "github.com/fabrizio2210/cover_letter/src/go/cmd/api/testing"
	"github.com/gin-gonic/gin"
	"github.com/go-redis/redis/v8"
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/bson/primitive"
	"go.mongodb.org/mongo-driver/mongo"
	"google.golang.org/protobuf/types/known/timestamppb"
)

type mockMongoClient struct {
	db MongoDatabaseIface
}

func (m *mockMongoClient) Database(name string) MongoDatabaseIface {
	return m.db
}

type mockMongoDatabase struct {
	collections map[string]MongoCollectionIface
}

func (m *mockMongoDatabase) Collection(name string) MongoCollectionIface {
	if col, ok := m.collections[name]; ok {
		return col
	}
	return &mockMongoCollection{}
}

type mockMongoCollection struct {
	findOneFn func(ctx context.Context, filter interface{}) MongoSingleResultIface
}

func (m *mockMongoCollection) Aggregate(ctx context.Context, pipeline interface{}) (MongoCursorIface, error) {
	return &mockMongoCursor{}, nil
}

func (m *mockMongoCollection) FindOne(ctx context.Context, filter interface{}) MongoSingleResultIface {
	if m.findOneFn != nil {
		return m.findOneFn(ctx, filter)
	}
	return &mockMongoSingleResult{}
}

type mockMongoSingleResult struct {
	decodeFn func(v interface{}) error
}

func (m *mockMongoSingleResult) Decode(v interface{}) error {
	if m.decodeFn != nil {
		return m.decodeFn(v)
	}
	return nil
}

type mockMongoCursor struct{}

func (m *mockMongoCursor) Next(ctx context.Context) bool   { return false }
func (m *mockMongoCursor) Decode(v interface{}) error      { return nil }
func (m *mockMongoCursor) Close(ctx context.Context) error { return nil }

type mockRedisClient struct{}

func (m *mockRedisClient) LLen(ctx context.Context, key string) *redis.IntCmd {
	return nil // Placeholder; will be handled in getQueueDepths
}

func setTestGlobals(t *testing.T) {
	t.Helper()

	origMongoProvider := getMongoClient
	origQueuePush := queuePush
	origSubscribeChannel := subscribeChannel
	origRedisClient := getRedisClient
	origGetQueueDepths := getQueueDepths
	origDBName := os.Getenv("DB_NAME")

	t.Cleanup(func() {
		getMongoClient = origMongoProvider
		queuePush = origQueuePush
		subscribeChannel = origSubscribeChannel
		getRedisClient = origRedisClient
		getQueueDepths = origGetQueueDepths
		if origDBName == "" {
			_ = os.Unsetenv("DB_NAME")
		} else {
			_ = os.Setenv("DB_NAME", origDBName)
		}
	})

	// Mock Redis client that doesn't connect to real Redis
	SetRedisClientProvider(func() *redis.Client {
		return nil
	})

	// Mock queue depths to return 0 for all queues (tests don't need actual Redis)
	SetQueueDepthsProvider(func(queueNames map[string]string) map[string]int64 {
		depths := make(map[string]int64)
		for key := range queueNames {
			depths[key] = 0
		}
		return depths
	})

	resetHubs()
}

func resetHubs() {
	crawlHub = &crawlProgressHub{
		snapshots:             make(map[string]*models.CrawlProgress),
		latestStatsByWorkflow: make(map[string]lastRunWorkflowStatsItem),
		subscribers:           make(map[int]crawlSubscriber),
	}
	scoringHub = &scoringProgressHub{
		snapshots:   make(map[string]*models.ScoringProgress),
		subscribers: make(map[int]scoringSubscriber),
	}

	// Disable bridge goroutine startup in unit tests.
	crawlHub.bridgeOnce.Do(func() {})
	scoringHub.bridgeOnce.Do(func() {})
}

func waitForCrawlSubscribers(t *testing.T, expected int) {
	t.Helper()
	deadline := time.Now().Add(250 * time.Millisecond)
	for time.Now().Before(deadline) {
		crawlHub.mu.RLock()
		n := len(crawlHub.subscribers)
		crawlHub.mu.RUnlock()
		if n >= expected {
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for crawl subscribers >= %d", expected)
}

func waitForScoringSubscribers(t *testing.T, expected int) {
	t.Helper()
	deadline := time.Now().Add(250 * time.Millisecond)
	for time.Now().Before(deadline) {
		scoringHub.mu.RLock()
		n := len(scoringHub.subscribers)
		scoringHub.mu.RUnlock()
		if n >= expected {
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for scoring subscribers >= %d", expected)
}

func TestClampNonNegative(t *testing.T) {
	cases := []struct {
		name string
		in   int32
		want int32
	}{
		{name: "negative", in: -1, want: 0},
		{name: "zero", in: 0, want: 0},
		{name: "positive", in: 12, want: 12},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := clampNonNegative(tc.in)
			if got != tc.want {
				t.Fatalf("clampNonNegative(%d)=%d want %d", tc.in, got, tc.want)
			}
		})
	}
}

func TestToInt32(t *testing.T) {
	cases := []struct {
		name string
		in   interface{}
		want int32
	}{
		{name: "int", in: int(7), want: 7},
		{name: "int32", in: int32(8), want: 8},
		{name: "int64", in: int64(9), want: 9},
		{name: "float64", in: float64(10.9), want: 10},
		{name: "float32", in: float32(11.9), want: 11},
		{name: "string valid", in: "12", want: 12},
		{name: "string invalid", in: "x", want: 0},
		{name: "unsupported", in: true, want: 0},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := toInt32(tc.in)
			if got != tc.want {
				t.Fatalf("toInt32(%v)=%d want %d", tc.in, got, tc.want)
			}
		})
	}
}

func TestCrawlSnapshotKey(t *testing.T) {
	cases := []struct {
		name     string
		snapshot *models.CrawlProgress
		want     string
	}{
		{name: "nil", snapshot: nil, want: ""},
		{name: "workflow run id preferred", snapshot: &models.CrawlProgress{WorkflowRunId: "wr1", RunId: "r1"}, want: "workflow-run:wr1"},
		{name: "run id fallback", snapshot: &models.CrawlProgress{RunId: "r2"}, want: "run:r2:lifecycle"},
		{name: "identity workflow status", snapshot: &models.CrawlProgress{IdentityId: "id1", Workflow: "queued", Status: "running"}, want: "identity:id1:workflow:queued:status:running"},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := crawlSnapshotKey(tc.snapshot)
			if got != tc.want {
				t.Fatalf("crawlSnapshotKey()=%q want %q", got, tc.want)
			}
		})
	}
}

func TestIsCrawlerWorkflow(t *testing.T) {
	if !isCrawlerWorkflow(workflowCrawlerYCombinator) {
		t.Fatal("ycombinator should be a crawler workflow")
	}
	if !isCrawlerWorkflow(workflowCrawlerLevelsfyi) {
		t.Fatal("levelsfyi should be a crawler workflow")
	}
	if !isCrawlerWorkflow(workflowCrawler4DayWeek) {
		t.Fatal("4dayweek should be a crawler workflow")
	}
	if !isCrawlerWorkflow(workflowCrawlerATSExtraction) {
		t.Fatal("ats extraction should be a crawler workflow")
	}
	if isCrawlerWorkflow("unknown") {
		t.Fatal("unknown should not be crawler workflow")
	}
}

func TestWorkflowCountersForSnapshot(t *testing.T) {
	snapshot := &models.CrawlProgress{Completed: 5}

	item := workflowCountersForSnapshot(workflowCrawlerYCombinator, snapshot)
	if item.DiscoveredJobs != 0 || item.DiscoveredCompanies != 5 {
		t.Fatalf("ycombinator counters mismatch: %+v", item)
	}

	item = workflowCountersForSnapshot(workflowCrawlerATSExtraction, snapshot)
	if item.DiscoveredJobs != 5 || item.DiscoveredCompanies != 0 {
		t.Fatalf("ats extraction counters mismatch: %+v", item)
	}

	item = workflowCountersForSnapshot(workflowCrawlerLevelsfyi, snapshot)
	if item.DiscoveredJobs != 5 || item.DiscoveredCompanies != 5 {
		t.Fatalf("levelsfyi counters mismatch: %+v", item)
	}

	item = workflowCountersForSnapshot(workflowCrawler4DayWeek, snapshot)
	if item.DiscoveredJobs != 5 || item.DiscoveredCompanies != 5 {
		t.Fatalf("4dayweek counters mismatch: %+v", item)
	}

	item = workflowCountersForSnapshot("unknown", snapshot)
	if item.DiscoveredJobs != 0 || item.DiscoveredCompanies != 0 {
		t.Fatalf("unknown counters mismatch: %+v", item)
	}
}

func TestNormalizeCrawlProgress(t *testing.T) {
	now := time.Now().UTC()

	negative := normalizeCrawlProgress(&models.CrawlProgress{Percent: -1})
	if negative.Percent != 0 {
		t.Fatalf("expected percent clamped to 0, got %d", negative.Percent)
	}
	if negative.UpdatedAt == nil {
		t.Fatal("expected UpdatedAt to be populated")
	}

	over := normalizeCrawlProgress(&models.CrawlProgress{Percent: 101})
	if over.Percent != 100 {
		t.Fatalf("expected percent clamped to 100, got %d", over.Percent)
	}

	running := normalizeCrawlProgress(&models.CrawlProgress{
		Status:    "running",
		UpdatedAt: timestamppb.New(now),
	})
	if running.StartedAt == nil {
		t.Fatal("expected StartedAt set for running snapshot")
	}

	completed := normalizeCrawlProgress(&models.CrawlProgress{
		Status:    "completed",
		UpdatedAt: timestamppb.New(now),
	})
	if completed.FinishedAt == nil {
		t.Fatal("expected FinishedAt set for completed snapshot")
	}
}

func TestNormalizeScoringProgress(t *testing.T) {
	now := time.Now().UTC()

	negative := normalizeScoringProgress(&models.ScoringProgress{Percent: -1})
	if negative.Percent != 0 {
		t.Fatalf("expected percent clamped to 0, got %d", negative.Percent)
	}
	if negative.UpdatedAt == nil {
		t.Fatal("expected UpdatedAt to be populated")
	}

	over := normalizeScoringProgress(&models.ScoringProgress{Percent: 101})
	if over.Percent != 100 {
		t.Fatalf("expected percent clamped to 100, got %d", over.Percent)
	}

	running := normalizeScoringProgress(&models.ScoringProgress{
		Status:    "running",
		UpdatedAt: timestamppb.New(now),
	})
	if running.StartedAt == nil {
		t.Fatal("expected StartedAt set for running snapshot")
	}

	completed := normalizeScoringProgress(&models.ScoringProgress{
		Status:    "completed",
		UpdatedAt: timestamppb.New(now),
	})
	if completed.FinishedAt == nil {
		t.Fatal("expected FinishedAt set for completed snapshot")
	}
}

func TestCrawlHubPublishAndListSnapshots(t *testing.T) {
	setTestGlobals(t)

	t1 := timestamppb.New(time.Now().UTC().Add(-1 * time.Minute))
	t2 := timestamppb.New(time.Now().UTC())

	crawlHub.publish(&models.CrawlProgress{RunId: "r1", IdentityId: "id1", Status: "running", UpdatedAt: t1})
	crawlHub.publish(&models.CrawlProgress{RunId: "r2", IdentityId: "id2", Status: "running", UpdatedAt: t2})

	all := crawlHub.listSnapshots("")
	if len(all) != 2 {
		t.Fatalf("expected 2 snapshots, got %d", len(all))
	}
	if all[0].RunId != "r2" {
		t.Fatalf("expected newest first, got first run_id=%s", all[0].RunId)
	}

	filtered := crawlHub.listSnapshots("id1")
	if len(filtered) != 1 || filtered[0].IdentityId != "id1" {
		t.Fatalf("unexpected filtered snapshots: %+v", filtered)
	}
}

func TestCrawlHubFindActiveByIdentity(t *testing.T) {
	setTestGlobals(t)

	now := time.Now().UTC()
	crawlHub.publish(&models.CrawlProgress{RunId: "r-completed", IdentityId: "id1", Status: "completed", UpdatedAt: timestamppb.New(now.Add(-2 * time.Minute))})
	crawlHub.publish(&models.CrawlProgress{RunId: "r-running", IdentityId: "id1", Status: "running", UpdatedAt: timestamppb.New(now.Add(-1 * time.Minute))})
	crawlHub.publish(&models.CrawlProgress{RunId: "r-queued", IdentityId: "id2", Status: "queued", UpdatedAt: timestamppb.New(now)})

	active, ok := crawlHub.findActiveByIdentity("id1")
	if !ok || active.RunId != "r-running" {
		t.Fatalf("expected running active crawl, got ok=%v active=%+v", ok, active)
	}

	active, ok = crawlHub.findActiveByIdentity("id2")
	if !ok || active.RunId != "r-queued" {
		t.Fatalf("expected queued active crawl, got ok=%v active=%+v", ok, active)
	}

	_, ok = crawlHub.findActiveByIdentity("missing")
	if ok {
		t.Fatal("expected no active crawl for unknown identity")
	}
}

func TestCrawlHubFindActiveByIdentity_IgnoresSupersededQueuedLifecycle(t *testing.T) {
	setTestGlobals(t)

	now := time.Now().UTC()
	crawlHub.publish(&models.CrawlProgress{
		RunId:      "r1",
		IdentityId: "id1",
		Status:     "queued",
		Workflow:   "queued",
		UpdatedAt:  timestamppb.New(now.Add(-2 * time.Minute)),
	})
	crawlHub.publish(&models.CrawlProgress{
		RunId:         "r1",
		WorkflowRunId: "wr1",
		WorkflowId:    workflowCrawlerYCombinator,
		IdentityId:    "id1",
		Status:        "completed",
		Workflow:      workflowCrawlerYCombinator,
		UpdatedAt:     timestamppb.New(now.Add(-1 * time.Minute)),
	})

	active, ok := crawlHub.findActiveByIdentity("id1")
	if ok {
		t.Fatalf("expected no active crawl, got %+v", active)
	}
}

func TestCrawlHubLastRunWorkflowStats(t *testing.T) {
	setTestGlobals(t)

	completedAt, items := crawlHub.lastRunWorkflowStats()
	if completedAt != nil || len(items) != 0 {
		t.Fatalf("expected empty stats, got completedAt=%v items=%v", completedAt, items)
	}

	now := time.Now().UTC()
	crawlHub.publish(&models.CrawlProgress{
		RunId:      "r1",
		Status:     "completed",
		WorkflowId: workflowCrawlerATSExtraction,
		Completed:  11,
		UpdatedAt:  timestamppb.New(now),
	})
	crawlHub.publish(&models.CrawlProgress{
		RunId:      "r2",
		Status:     "completed",
		WorkflowId: workflowCrawlerYCombinator,
		Completed:  3,
		UpdatedAt:  timestamppb.New(now.Add(1 * time.Second)),
	})

	completedAt, items = crawlHub.lastRunWorkflowStats()
	if completedAt == nil {
		t.Fatal("expected completedAt to be set")
	}
	if len(items) != 2 {
		t.Fatalf("expected 2 workflow stats items, got %d", len(items))
	}

	gotIDs := []string{items[0].WorkflowID, items[1].WorkflowID}
	wantOrder := []string{workflowCrawlerYCombinator, workflowCrawlerATSExtraction}
	if strings.Join(gotIDs, ",") != strings.Join(wantOrder, ",") {
		t.Fatalf("unexpected workflow order: got=%v want=%v", gotIDs, wantOrder)
	}
}

func TestCrawlHubSubscribeUnsubscribe(t *testing.T) {
	setTestGlobals(t)

	id, ch := crawlHub.subscribe()

	crawlHub.publish(&models.CrawlProgress{RunId: "r1", Status: "running"})
	select {
	case msg := <-ch:
		if msg == nil || msg.RunId != "r1" {
			t.Fatalf("unexpected published message: %+v", msg)
		}
	case <-time.After(300 * time.Millisecond):
		t.Fatal("timed out waiting for publish")
	}

	crawlHub.unsubscribe(id)
	_, ok := <-ch
	if ok {
		t.Fatal("expected channel closed after unsubscribe")
	}
}

func TestScoringHubPublishAndListSnapshots(t *testing.T) {
	setTestGlobals(t)

	scoringHub.publish(&models.ScoringProgress{RunId: "s1", IdentityId: "id1", Status: "running"})
	scoringHub.publish(&models.ScoringProgress{RunId: "s2", IdentityId: "id2", Status: "running"})

	all := scoringHub.listSnapshots("")
	if len(all) != 2 {
		t.Fatalf("expected 2 scoring snapshots, got %d", len(all))
	}

	filtered := scoringHub.listSnapshots("id2")
	if len(filtered) != 1 || filtered[0].RunId != "s2" {
		t.Fatalf("unexpected filtered scoring snapshots: %+v", filtered)
	}
}

func TestTriggerCrawl(t *testing.T) {
	setTestGlobals(t)

	identityID := primitive.NewObjectID().Hex()
	validReqBody := []byte(`{"identity_id":"` + identityID + `"}`)

	makeReq := func(body []byte) *http.Request {
		req, _ := http.NewRequest(http.MethodPost, "/api/crawls", bytes.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		return req
	}

	baseDB := &mockMongoDatabase{collections: map[string]MongoCollectionIface{}}
	baseCollection := &mockMongoCollection{}
	baseDB.collections["identities"] = baseCollection

	SetMongoClientProvider(func() MongoClientIface {
		return &mockMongoClient{db: baseDB}
	})

	SetQueuePushProvider(func(ctx context.Context, queueName string, payload []byte) error {
		return nil
	})

	t.Run("invalid json", func(t *testing.T) {
		resetHubs()
		c, w := testctx.CreateGinTestContext(http.MethodPost, "/api/crawls", makeReq([]byte("{")))
		TriggerCrawl(c)
		if w.Code != http.StatusBadRequest {
			t.Fatalf("expected 400, got %d", w.Code)
		}
	})

	t.Run("invalid identity id", func(t *testing.T) {
		resetHubs()
		c, w := testctx.CreateGinTestContext(http.MethodPost, "/api/crawls", makeReq([]byte(`{"identity_id":"invalid"}`)))
		TriggerCrawl(c)
		if w.Code != http.StatusBadRequest {
			t.Fatalf("expected 400, got %d", w.Code)
		}
	})

	t.Run("identity not found", func(t *testing.T) {
		resetHubs()
		baseCollection.findOneFn = func(ctx context.Context, filter interface{}) MongoSingleResultIface {
			return &mockMongoSingleResult{decodeFn: func(v interface{}) error { return errors.New("not found") }}
		}

		c, w := testctx.CreateGinTestContext(http.MethodPost, "/api/crawls", makeReq(validReqBody))
		TriggerCrawl(c)
		if w.Code != http.StatusBadRequest {
			t.Fatalf("expected 400, got %d", w.Code)
		}
	})

	t.Run("identity with no roles", func(t *testing.T) {
		resetHubs()
		baseCollection.findOneFn = func(ctx context.Context, filter interface{}) MongoSingleResultIface {
			return &mockMongoSingleResult{decodeFn: func(v interface{}) error {
				m, ok := v.(*bson.M)
				if !ok {
					return errors.New("unexpected decode target")
				}
				*m = bson.M{"_id": primitive.NewObjectID(), "roles": bson.A{}}
				return nil
			}}
		}

		c, w := testctx.CreateGinTestContext(http.MethodPost, "/api/crawls", makeReq(validReqBody))
		TriggerCrawl(c)
		if w.Code != http.StatusBadRequest {
			t.Fatalf("expected 400, got %d", w.Code)
		}
	})

	t.Run("active crawl conflict", func(t *testing.T) {
		resetHubs()
		crawlHub.publish(&models.CrawlProgress{RunId: "existing", IdentityId: identityID, Status: "running"})
		baseCollection.findOneFn = func(ctx context.Context, filter interface{}) MongoSingleResultIface {
			return &mockMongoSingleResult{decodeFn: func(v interface{}) error {
				m, ok := v.(*bson.M)
				if !ok {
					return errors.New("unexpected decode target")
				}
				*m = bson.M{"_id": primitive.NewObjectID(), "roles": bson.A{"backend"}}
				return nil
			}}
		}

		c, w := testctx.CreateGinTestContext(http.MethodPost, "/api/crawls", makeReq(validReqBody))
		TriggerCrawl(c)
		if w.Code != http.StatusConflict {
			t.Fatalf("expected 409, got %d", w.Code)
		}
	})

	t.Run("queue push failure", func(t *testing.T) {
		resetHubs()
		baseCollection.findOneFn = func(ctx context.Context, filter interface{}) MongoSingleResultIface {
			return &mockMongoSingleResult{decodeFn: func(v interface{}) error {
				m, ok := v.(*bson.M)
				if !ok {
					return errors.New("unexpected decode target")
				}
				*m = bson.M{"_id": primitive.NewObjectID(), "roles": bson.A{"backend"}}
				return nil
			}}
		}
		SetQueuePushProvider(func(ctx context.Context, queueName string, payload []byte) error {
			return errors.New("redis down")
		})

		c, w := testctx.CreateGinTestContext(http.MethodPost, "/api/crawls", makeReq(validReqBody))
		TriggerCrawl(c)
		if w.Code != http.StatusInternalServerError {
			t.Fatalf("expected 500, got %d", w.Code)
		}
		SetQueuePushProvider(func(ctx context.Context, queueName string, payload []byte) error { return nil })
	})

	t.Run("happy path", func(t *testing.T) {
		resetHubs()
		baseCollection.findOneFn = func(ctx context.Context, filter interface{}) MongoSingleResultIface {
			return &mockMongoSingleResult{decodeFn: func(v interface{}) error {
				m, ok := v.(*bson.M)
				if !ok {
					return errors.New("unexpected decode target")
				}
				*m = bson.M{"_id": primitive.NewObjectID(), "roles": bson.A{"backend"}}
				return nil
			}}
		}

		c, w := testctx.CreateGinTestContext(http.MethodPost, "/api/crawls", makeReq(validReqBody))
		TriggerCrawl(c)
		if w.Code != http.StatusAccepted {
			t.Fatalf("expected 202, got %d body=%s", w.Code, w.Body.String())
		}

		var body map[string]interface{}
		if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
			t.Fatalf("invalid response JSON: %v", err)
		}
		if body["identity_id"] != identityID {
			t.Fatalf("expected identity_id=%s got %v", identityID, body["identity_id"])
		}
		if body["status"] != "queued" {
			t.Fatalf("expected status queued got %v", body["status"])
		}
		if body["run_id"] == "" {
			t.Fatal("expected non-empty run_id")
		}
	})
}

func TestGetActiveCrawls(t *testing.T) {
	setTestGlobals(t)

	c, w := testctx.CreateGinTestContext(http.MethodGet, "/api/crawls/active", nil)
	GetActiveCrawls(c)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}

	var empty []*models.CrawlProgress
	if err := json.Unmarshal(w.Body.Bytes(), &empty); err != nil {
		t.Fatalf("invalid response JSON: %v", err)
	}
	if len(empty) != 0 {
		t.Fatalf("expected empty list, got %d", len(empty))
	}

	crawlHub.publish(&models.CrawlProgress{RunId: "r1", IdentityId: "id1", Status: "running"})
	crawlHub.publish(&models.CrawlProgress{RunId: "r2", IdentityId: "id2", Status: "running"})

	req, _ := http.NewRequest(http.MethodGet, "/api/crawls/active?identity_id=id2", nil)
	c2, w2 := testctx.CreateGinTestContext(http.MethodGet, "/api/crawls/active?identity_id=id2", req)
	GetActiveCrawls(c2)
	if w2.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w2.Code)
	}

	var filtered []*models.CrawlProgress
	if err := json.Unmarshal(w2.Body.Bytes(), &filtered); err != nil {
		t.Fatalf("invalid response JSON: %v", err)
	}
	if len(filtered) != 1 || filtered[0].IdentityId != "id2" {
		t.Fatalf("unexpected filtered response: %+v", filtered)
	}
}

func TestGetActiveCrawls_OmitsSupersededQueuedLifecycleSnapshots(t *testing.T) {
	setTestGlobals(t)

	now := time.Now().UTC()
	crawlHub.publish(&models.CrawlProgress{
		RunId:      "r1",
		IdentityId: "id1",
		Status:     "queued",
		Workflow:   "queued",
		UpdatedAt:  timestamppb.New(now.Add(-2 * time.Minute)),
	})
	crawlHub.publish(&models.CrawlProgress{
		RunId:         "r1",
		WorkflowRunId: "wr1",
		WorkflowId:    workflowCrawlerYCombinator,
		IdentityId:    "id1",
		Status:        "completed",
		Workflow:      workflowCrawlerYCombinator,
		UpdatedAt:     timestamppb.New(now.Add(-1 * time.Minute)),
	})

	req, _ := http.NewRequest(http.MethodGet, "/api/crawls/active?identity_id=id1", nil)
	c, w := testctx.CreateGinTestContext(http.MethodGet, "/api/crawls/active?identity_id=id1", req)
	GetActiveCrawls(c)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}

	var snapshots []*models.CrawlProgress
	if err := json.Unmarshal(w.Body.Bytes(), &snapshots); err != nil {
		t.Fatalf("invalid response JSON: %v", err)
	}
	if len(snapshots) != 1 {
		t.Fatalf("expected 1 snapshot, got %d", len(snapshots))
	}
	if snapshots[0].Status != "completed" || snapshots[0].WorkflowRunId != "wr1" {
		t.Fatalf("unexpected snapshot returned: %+v", snapshots[0])
	}
}

func TestGetLastRunWorkflowStats(t *testing.T) {
	setTestGlobals(t)

	c, w := testctx.CreateGinTestContext(http.MethodGet, "/api/crawls/last-run/workflow-stats", nil)
	GetLastRunWorkflowStats(c)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}

	var empty lastRunWorkflowStatsResponse
	if err := json.Unmarshal(w.Body.Bytes(), &empty); err != nil {
		t.Fatalf("invalid response JSON: %v", err)
	}
	if len(empty.Workflows) != 0 {
		t.Fatalf("expected empty workflows, got %d", len(empty.Workflows))
	}

	now := time.Now().UTC()
	crawlHub.publish(&models.CrawlProgress{
		RunId:      "r-ycombinator",
		Status:     "completed",
		WorkflowId: workflowCrawlerYCombinator,
		Completed:  2,
		UpdatedAt:  timestamppb.New(now),
	})
	crawlHub.publish(&models.CrawlProgress{
		RunId:      "r-ats",
		Status:     "completed",
		WorkflowId: workflowCrawlerATSExtraction,
		Completed:  4,
		UpdatedAt:  timestamppb.New(now),
	})

	c2, w2 := testctx.CreateGinTestContext(http.MethodGet, "/api/crawls/last-run/workflow-stats", nil)
	GetLastRunWorkflowStats(c2)
	if w2.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w2.Code)
	}

	var response lastRunWorkflowStatsResponse
	if err := json.Unmarshal(w2.Body.Bytes(), &response); err != nil {
		t.Fatalf("invalid response JSON: %v", err)
	}

	if len(response.Workflows) != 2 {
		t.Fatalf("expected 2 workflows, got %d", len(response.Workflows))
	}
	ids := []string{response.Workflows[0].WorkflowID, response.Workflows[1].WorkflowID}
	if strings.Join(ids, ",") != strings.Join([]string{workflowCrawlerYCombinator, workflowCrawlerATSExtraction}, ",") {
		t.Fatalf("unexpected workflow order: %v", ids)
	}
}

func TestGetWorkflowCumulativeJobs(t *testing.T) {
	setTestGlobals(t)
	_ = os.Setenv("DB_NAME", "testdb")

	buildCtx := func() (*gin.Context, *httptest.ResponseRecorder) {
		return testctx.CreateGinTestContext(http.MethodGet, "/api/crawls/workflow-cumulative-jobs", nil)
	}

	t.Run("no documents", func(t *testing.T) {
		SetMongoClientProvider(func() MongoClientIface {
			return &mockMongoClient{db: &mockMongoDatabase{collections: map[string]MongoCollectionIface{
				statsCollectionName: &mockMongoCollection{findOneFn: func(ctx context.Context, filter interface{}) MongoSingleResultIface {
					return &mockMongoSingleResult{decodeFn: func(v interface{}) error { return mongo.ErrNoDocuments }}
				}},
			}}}
		})

		c, w := buildCtx()
		GetWorkflowCumulativeJobs(c)
		if w.Code != http.StatusOK {
			t.Fatalf("expected 200, got %d", w.Code)
		}

		var response workflowCumulativeJobsResponse
		if err := json.Unmarshal(w.Body.Bytes(), &response); err != nil {
			t.Fatalf("invalid response JSON: %v", err)
		}
		if len(response.Workflows) != len(dashboardWorkflowOrder) {
			t.Fatalf("expected %d workflows, got %d", len(dashboardWorkflowOrder), len(response.Workflows))
		}
		for i, wf := range response.Workflows {
			if wf.WorkflowID != dashboardWorkflowOrder[i] {
				t.Fatalf("unexpected workflow ordering at %d: got=%s want=%s", i, wf.WorkflowID, dashboardWorkflowOrder[i])
			}
			if wf.DiscoveredJobsCumulative != 0 {
				t.Fatalf("expected zero counters for no docs, got %+v", wf)
			}
		}
	})

	t.Run("with counters document", func(t *testing.T) {
		SetMongoClientProvider(func() MongoClientIface {
			return &mockMongoClient{db: &mockMongoDatabase{collections: map[string]MongoCollectionIface{
				statsCollectionName: &mockMongoCollection{findOneFn: func(ctx context.Context, filter interface{}) MongoSingleResultIface {
					return &mockMongoSingleResult{decodeFn: func(v interface{}) error {
						target, ok := v.(*bson.M)
						if !ok {
							return errors.New("unexpected decode target")
						}
						*target = bson.M{
							"_id": workflowCountersDocID,
							workflowCountersField: bson.M{
								workflowCrawlerYCombinator:   int32(1),
								workflowCrawlerATSExtraction: int64(2),
								workflowCrawler4DayWeek:      float64(3),
								workflowCrawlerLevelsfyi:     "4",
							},
						}
						return nil
					}}
				}},
			}}}
		})

		c, w := buildCtx()
		GetWorkflowCumulativeJobs(c)
		if w.Code != http.StatusOK {
			t.Fatalf("expected 200, got %d", w.Code)
		}

		var response workflowCumulativeJobsResponse
		if err := json.Unmarshal(w.Body.Bytes(), &response); err != nil {
			t.Fatalf("invalid response JSON: %v", err)
		}

		if len(response.Workflows) != len(dashboardWorkflowOrder) {
			t.Fatalf("expected %d workflows, got %d", len(dashboardWorkflowOrder), len(response.Workflows))
		}

		want := map[string]int32{
			workflowCrawlerYCombinator:   1,
			workflowCrawlerLevelsfyi:     4,
			workflowCrawler4DayWeek:      3,
			workflowCrawlerATSExtraction: 2,
		}
		for _, wf := range response.Workflows {
			if wf.DiscoveredJobsCumulative != want[wf.WorkflowID] {
				t.Fatalf("unexpected cumulative count for %s: got=%d want=%d", wf.WorkflowID, wf.DiscoveredJobsCumulative, want[wf.WorkflowID])
			}
		}
	})

	t.Run("unexpected mongo error", func(t *testing.T) {
		SetMongoClientProvider(func() MongoClientIface {
			return &mockMongoClient{db: &mockMongoDatabase{collections: map[string]MongoCollectionIface{
				statsCollectionName: &mockMongoCollection{findOneFn: func(ctx context.Context, filter interface{}) MongoSingleResultIface {
					return &mockMongoSingleResult{decodeFn: func(v interface{}) error { return errors.New("mongo offline") }}
				}},
			}}}
		})

		c, w := buildCtx()
		GetWorkflowCumulativeJobs(c)
		if w.Code != http.StatusInternalServerError {
			t.Fatalf("expected 500, got %d", w.Code)
		}
	})
}

func TestStreamCrawlProgress(t *testing.T) {
	setTestGlobals(t)

	req, _ := http.NewRequest(http.MethodGet, "/api/crawls/stream", nil)
	ctx, cancel := context.WithCancel(req.Context())
	defer cancel()
	req = req.WithContext(ctx)
	c, w := testctx.CreateGinTestContext(http.MethodGet, "/api/crawls/stream", req)

	done := make(chan struct{})
	go func() {
		StreamCrawlProgress(c)
		close(done)
	}()

	waitForCrawlSubscribers(t, 1)
	crawlHub.publish(&models.CrawlProgress{RunId: "run-1", IdentityId: "id-1", Status: "running", Percent: 10})

	time.Sleep(20 * time.Millisecond)
	cancel()

	select {
	case <-done:
	case <-time.After(500 * time.Millisecond):
		t.Fatal("stream handler did not return after context cancellation")
	}

	body := w.Body.String()
	if !strings.Contains(body, "event: crawl-progress\n") {
		t.Fatalf("expected crawl-progress event in body, got: %s", body)
	}
	if !strings.Contains(body, "\ndata: ") {
		t.Fatalf("expected data line in body, got: %s", body)
	}
}

func TestGetActiveScoring(t *testing.T) {
	setTestGlobals(t)

	scoringHub.publish(&models.ScoringProgress{RunId: "s1", IdentityId: "id1", Status: "running"})
	scoringHub.publish(&models.ScoringProgress{RunId: "s2", IdentityId: "id2", Status: "running"})

	req, _ := http.NewRequest(http.MethodGet, "/api/scoring/active?identity_id=id1", nil)
	c, w := testctx.CreateGinTestContext(http.MethodGet, "/api/scoring/active?identity_id=id1", req)
	GetActiveScoring(c)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}

	var snapshots []*models.ScoringProgress
	if err := json.Unmarshal(w.Body.Bytes(), &snapshots); err != nil {
		t.Fatalf("invalid response JSON: %v", err)
	}
	if len(snapshots) != 1 || snapshots[0].IdentityId != "id1" {
		t.Fatalf("unexpected scoring response: %+v", snapshots)
	}
}

func TestStreamScoringProgress(t *testing.T) {
	setTestGlobals(t)

	req, _ := http.NewRequest(http.MethodGet, "/api/scoring/stream", nil)
	ctx, cancel := context.WithCancel(req.Context())
	defer cancel()
	req = req.WithContext(ctx)
	c, w := testctx.CreateGinTestContext(http.MethodGet, "/api/scoring/stream", req)

	done := make(chan struct{})
	go func() {
		StreamScoringProgress(c)
		close(done)
	}()

	waitForScoringSubscribers(t, 1)
	scoringHub.publish(&models.ScoringProgress{RunId: "score-1", IdentityId: "id-1", Status: "running", Percent: 10})

	time.Sleep(20 * time.Millisecond)
	cancel()

	select {
	case <-done:
	case <-time.After(500 * time.Millisecond):
		t.Fatal("stream handler did not return after context cancellation")
	}

	body := w.Body.String()
	if !strings.Contains(body, "event: scoring-progress\n") {
		t.Fatalf("expected scoring-progress event in body, got: %s", body)
	}
	if !strings.Contains(body, "\ndata: ") {
		t.Fatalf("expected data line in body, got: %s", body)
	}
}

func TestDefaultWorkflowCumulativeJobsOrder(t *testing.T) {
	items := defaultWorkflowCumulativeJobs()
	if len(items) != len(dashboardWorkflowOrder) {
		t.Fatalf("expected %d items got %d", len(dashboardWorkflowOrder), len(items))
	}

	ids := make([]string, 0, len(items))
	for _, item := range items {
		ids = append(ids, item.WorkflowID)
		if item.DiscoveredJobsCumulative != 0 {
			t.Fatalf("expected zero value, got %+v", item)
		}
	}
	if strings.Join(ids, ",") != strings.Join(dashboardWorkflowOrder, ",") {
		t.Fatalf("unexpected order: got=%v want=%v", ids, dashboardWorkflowOrder)
	}
}

func TestCrawlHubLastRunWorkflowStatsFallbackFromSnapshots(t *testing.T) {
	setTestGlobals(t)

	// Build a hub with snapshots but no pre-computed latestStatsByWorkflow to exercise fallback branch.
	h := &crawlProgressHub{
		snapshots:             map[string]*models.CrawlProgress{},
		subscribers:           map[int]crawlSubscriber{},
		latestStatsByWorkflow: map[string]lastRunWorkflowStatsItem{},
	}
	now := time.Now().UTC()
	h.snapshots["a"] = &models.CrawlProgress{
		RunId:      "a",
		Status:     "completed",
		WorkflowId: workflowCrawlerLevelsfyi,
		Completed:  7,
		UpdatedAt:  timestamppb.New(now),
	}
	h.snapshots["b"] = &models.CrawlProgress{
		RunId:      "b",
		Status:     "completed",
		WorkflowId: workflowCrawlerYCombinator,
		Completed:  4,
		UpdatedAt:  timestamppb.New(now.Add(1 * time.Second)),
	}

	completedAt, items := h.lastRunWorkflowStats()
	if completedAt == nil {
		t.Fatal("expected completedAt in fallback")
	}

	if len(items) != 2 {
		t.Fatalf("expected 2 items got %d", len(items))
	}

	ids := make([]string, 0, len(items))
	for _, item := range items {
		ids = append(ids, item.WorkflowID)
	}
	want := []string{workflowCrawlerYCombinator, workflowCrawlerLevelsfyi}
	if strings.Join(ids, ",") != strings.Join(want, ",") {
		t.Fatalf("unexpected order got=%v want=%v", ids, want)
	}
}

func TestListSnapshotsStableTieBreak(t *testing.T) {
	setTestGlobals(t)

	ts := timestamppb.New(time.Now().UTC())
	crawlHub.publish(&models.CrawlProgress{RunId: "a", IdentityId: "id", Status: "running", UpdatedAt: ts})
	crawlHub.publish(&models.CrawlProgress{RunId: "b", IdentityId: "id", Status: "running", UpdatedAt: ts})

	result := crawlHub.listSnapshots("")
	if len(result) != 2 {
		t.Fatalf("expected 2 snapshots got %d", len(result))
	}

	got := []string{result[0].RunId, result[1].RunId}
	want := append([]string{}, got...)
	sort.Strings(want)
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Fatalf("expected lexical tiebreak ordering, got=%v", got)
	}
}

func TestSetProviderNilNoop(t *testing.T) {
	setTestGlobals(t)

	SetMongoClientProvider(nil)
	SetQueuePushProvider(nil)
	SetSubscribeChannelProvider(nil)

	if getMongoClient == nil || queuePush == nil || subscribeChannel == nil {
		t.Fatal("providers should stay non-nil when setting nil provider")
	}
}

func TestSubscribeProviderSetterIsApplied(t *testing.T) {
	setTestGlobals(t)

	called := false
	SetSubscribeChannelProvider(func(ctx context.Context, channelName string) (<-chan *redis.Message, func() error) {
		called = true
		ch := make(chan *redis.Message)
		close(ch)
		return ch, func() error { return nil }
	})

	ch, closeFn := subscribeChannel(context.Background(), "x")
	if !called {
		t.Fatal("expected custom subscribe provider to be used")
	}
	if ch == nil {
		t.Fatal("expected non-nil channel")
	}
	if closeFn == nil {
		t.Fatal("expected non-nil close function")
	}
}

func TestGetActivitySummary(t *testing.T) {
	setTestGlobals(t)

	// Test with empty state
	c, w := testctx.CreateGinTestContext(http.MethodGet, "/api/crawls/activity-summary", nil)
	GetActivitySummary(c)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}

	var summary activitySummaryResponse
	if err := json.Unmarshal(w.Body.Bytes(), &summary); err != nil {
		t.Fatalf("invalid response JSON: %v", err)
	}

	if len(summary.ActiveWorkflows) != 0 {
		t.Fatalf("expected empty active workflows, got %d", len(summary.ActiveWorkflows))
	}

	// Add some active crawls
	crawlHub.publish(&models.CrawlProgress{
		RunId:      "r1",
		IdentityId: "id1",
		Status:     "running",
		WorkflowId: workflowCrawlerYCombinator,
		Message:    "Processing companies",
	})

	// Verify the crawl was stored in the hub
	allSnapshots := crawlHub.listSnapshots("")
	if len(allSnapshots) == 0 {
		t.Fatalf("crawl was not stored in hub")
	}

	// Test with identity filter
	req, _ := http.NewRequest(http.MethodGet, "/api/crawls/activity-summary?identity_id=id1", nil)
	c2, w2 := testctx.CreateGinTestContext(http.MethodGet, "/api/crawls/activity-summary?identity_id=id1", req)
	GetActivitySummary(c2)

	if w2.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w2.Code)
	}

	if err := json.Unmarshal(w2.Body.Bytes(), &summary); err != nil {
		t.Fatalf("invalid response JSON: %v", err)
	}

	if summary.IdentityID != "id1" {
		t.Fatalf("expected identity_id=id1, got %s", summary.IdentityID)
	}

	if len(summary.ActiveWorkflows) != 1 {
		t.Fatalf("expected 1 active workflow for id1, got %d", len(summary.ActiveWorkflows))
	}

	if summary.ActiveWorkflows[0].WorkflowID != workflowCrawlerYCombinator {
		t.Fatalf("expected workflow_id=%s, got %s", workflowCrawlerYCombinator, summary.ActiveWorkflows[0].WorkflowID)
	}
}

func TestApplyQueueDefaults(t *testing.T) {
	queues := map[string]string{
		queueCrawlerTrigger:       "",
		queueCrawlerYCombinator:   "custom_queue",
		queueCrawlerATSExtraction: "",
	}

	result := applyQueueDefaults(queues)

	if result[queueCrawlerTrigger] != defaultCrawlerTriggerQueue {
		t.Fatalf("expected default for trigger queue, got %s", result[queueCrawlerTrigger])
	}

	if result[queueCrawlerYCombinator] != "custom_queue" {
		t.Fatalf("expected custom_queue to be preserved, got %s", result[queueCrawlerYCombinator])
	}

	if result[queueCrawlerATSExtraction] != defaultCrawlerATSExtractionQueue {
		t.Fatalf("expected default for ATS extraction queue, got %s", result[queueCrawlerATSExtraction])
	}
}
