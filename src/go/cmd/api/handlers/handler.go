package handlers

import "github.com/go-redis/redis/v8"

// SetRedisClientForTests allows tests to inject a redis client into the handlers
// package so tests can use an in-memory redis (e.g., miniredis).
func SetRedisClientForTests(client *redis.Client) {
	rdb = client
}
