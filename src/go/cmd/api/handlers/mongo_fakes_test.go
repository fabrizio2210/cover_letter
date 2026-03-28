package handlers

import (
	"context"

	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/mongo"
)

// fakeCollection, fakeCursor, fakeSingleResult, fakeDatabase, and fakeClient
// are shared test doubles used across handler tests.
type fakeCollection struct {
	name       string
	insertRes  *mongo.InsertOneResult
	insertDoc  interface{}
	updateRes  *mongo.UpdateResult
	deleteRes  *mongo.DeleteResult
	findOneDoc bson.M
	docs       []bson.M
}

func (f *fakeCollection) Aggregate(ctx context.Context, pipeline interface{}) (MongoCursorIface, error) {
	return &fakeCursor{docs: f.docs}, nil
}

func (f *fakeCollection) InsertOne(ctx context.Context, doc interface{}) (*mongo.InsertOneResult, error) {
	f.insertDoc = doc
	return f.insertRes, nil
}

func (f *fakeCollection) FindOne(ctx context.Context, filter interface{}) MongoSingleResultIface {
	return &fakeSingleResult{doc: f.findOneDoc}
}

func (f *fakeCollection) UpdateOne(ctx context.Context, filter interface{}, update interface{}) (*mongo.UpdateResult, error) {
	return f.updateRes, nil
}

func (f *fakeCollection) DeleteOne(ctx context.Context, filter interface{}) (*mongo.DeleteResult, error) {
	return f.deleteRes, nil
}

type fakeCursor struct {
	docs []bson.M
	idx  int
}

func (f *fakeCursor) All(ctx context.Context, result interface{}) error {
	b, _ := bson.Marshal(f.docs)
	return bson.Unmarshal(b, result)
}

func (f *fakeCursor) Next(ctx context.Context) bool {
	return f.idx < len(f.docs)
}

func (f *fakeCursor) Decode(v interface{}) error {
	if f.idx >= len(f.docs) {
		return mongo.ErrNoDocuments
	}
	b, _ := bson.Marshal(f.docs[f.idx])
	f.idx++
	return bson.Unmarshal(b, v)
}

func (f *fakeCursor) Close(ctx context.Context) error { return nil }

type fakeSingleResult struct{ doc bson.M }

func (f *fakeSingleResult) Decode(v interface{}) error {
	if f.doc == nil {
		return mongo.ErrNoDocuments
	}
	b, _ := bson.Marshal(f.doc)
	return bson.Unmarshal(b, v)
}

type fakeDatabase struct{ cols map[string]*fakeCollection }

func (d *fakeDatabase) Collection(name string) MongoCollectionIface {
	if c, ok := d.cols[name]; ok {
		return c
	}
	return &fakeCollection{name: name}
}

type fakeClient struct{ db *fakeDatabase }

func (c *fakeClient) Database(name string) MongoDatabaseIface { return c.db }
