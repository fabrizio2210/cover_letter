set -eux

docker compose -f tests/e2e/docker-compose.test.yml up -d mongo redis api
docker compose -f tests/e2e/docker-compose.test.yml run --rm seeder
docker compose -f tests/e2e/docker-compose.test.yml up -d ai_querier
docker compose -f tests/e2e/docker-compose.test.yml run --rm pusher
# poll checker until FOUND
docker compose -f tests/e2e/docker-compose.test.yml run --rm checker
docker compose -f tests/e2e/docker-compose.test.yml down --remove-orphans