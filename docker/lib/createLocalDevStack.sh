#!/bin/bash

docker build -t fabrizio2210/coverletter-telegram-bot-dev -f docker/x86_64/Dockerfile-bot-dev .
docker build -t fabrizio2210/coverletter-ai-querier-dev -f docker/x86_64/Dockerfile-ai-dev .
docker build -t fabrizio2210/coverletter-api-dev -f docker/x86_64/Dockerfile-api-dev .
docker build -t fabrizio2210/coverletter-frontend-dev -f docker/x86_64/Dockerfile-frontend-dev .

# Supposing to deploy on x86_64 architecture
docker compose  --env-file ~/.docker/coverletter-dev.env  -f docker/lib/stack-dev.yml up --force-recreate --remove-orphans --renew-anon-volumes
