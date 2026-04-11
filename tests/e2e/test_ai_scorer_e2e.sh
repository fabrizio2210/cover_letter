set -eux

docker compose -f tests/e2e/docker-compose.test.yml up -d mongo redis api
docker compose -f tests/e2e/docker-compose.test.yml run --rm seeder
docker compose -f tests/e2e/docker-compose.test.yml up -d ai_scorer
sleep 2
docker compose -f tests/e2e/docker-compose.test.yml run --rm scorer_pusher
docker compose -f tests/e2e/docker-compose.test.yml run --rm scorer_checker

echo "****** API logs ******"
docker compose -f tests/e2e/docker-compose.test.yml logs api
echo "**********************"

echo "****** AI Scorer logs ******"
docker compose -f tests/e2e/docker-compose.test.yml logs ai_scorer
echo "****************************"

if docker compose -f tests/e2e/docker-compose.test.yml logs ai_scorer | grep "error"; then
  echo "BUG DETECTED: ai_scorer failed to process message"
  docker compose -f tests/e2e/docker-compose.test.yml down --remove-orphans
  exit 1
else
  echo "No bug detected."
fi

docker compose -f tests/e2e/docker-compose.test.yml down --remove-orphans