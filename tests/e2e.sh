set -eux

docker compose -f tests/e2e/docker-compose.test.yml up -d mongo redis api
docker compose -f tests/e2e/docker-compose.test.yml run --rm seeder
docker compose -f tests/e2e/docker-compose.test.yml up -d ai_querier
sleep 2 # wait for ai_querier to start listening
docker compose -f tests/e2e/docker-compose.test.yml run --rm pusher
sleep 2 # wait for ai_querier to process the message
# poll checker until FOUND
docker compose -f tests/e2e/docker-compose.test.yml run --rm checker

echo "****** API logs ******"
docker compose -f tests/e2e/docker-compose.test.yml logs api
echo "**********************"

echo "****** AI Querier logs ******"
docker compose -f tests/e2e/docker-compose.test.yml logs ai_querier
echo "***************************"


if docker compose -f tests/e2e/docker-compose.test.yml logs ai_querier | grep "error"; then
  echo "BUG DETECTED: ai_querier failed to process message"
  docker compose -f tests/e2e/docker-compose.test.yml down --remove-orphans
  exit 1
else
  echo "No bug detected."
fi

docker compose -f tests/e2e/docker-compose.test.yml down --remove-orphans