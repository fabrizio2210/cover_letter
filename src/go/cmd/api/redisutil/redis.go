package redisutil

import (
	"os"
	"strings"

	"github.com/go-redis/redis/v8"
)

// NewClient returns a Redis client. When REDIS_SENTINELS is set to a
// comma-separated list of "host:port" sentinel addresses and REDIS_MASTER_NAME
// is set, it returns a Sentinel-backed failover client that tracks the current
// primary. Otherwise it falls back to a single-node client addressed by
// REDIS_HOST and REDIS_PORT.
func NewClient() *redis.Client {
	sentinels := parseSentinels(os.Getenv("REDIS_SENTINELS"))
	masterName := os.Getenv("REDIS_MASTER_NAME")
	if len(sentinels) > 0 && masterName != "" {
		return redis.NewFailoverClient(&redis.FailoverOptions{
			MasterName:    masterName,
			SentinelAddrs: sentinels,
		})
	}

	redisHost := os.Getenv("REDIS_HOST")
	if redisHost == "" {
		redisHost = "localhost"
	}
	redisPort := os.Getenv("REDIS_PORT")
	if redisPort == "" {
		redisPort = "6379"
	}
	return redis.NewClient(&redis.Options{Addr: redisHost + ":" + redisPort})
}

func parseSentinels(raw string) []string {
	if raw == "" {
		return nil
	}
	var addrs []string
	for _, entry := range strings.Split(raw, ",") {
		entry = strings.TrimSpace(entry)
		if entry == "" || !strings.Contains(entry, ":") {
			continue
		}
		addrs = append(addrs, entry)
	}
	return addrs
}
