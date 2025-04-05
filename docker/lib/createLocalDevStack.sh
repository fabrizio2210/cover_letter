#!/bin/bash

docker build -t fabrizio2210/coverletter-telegram-bot-dev -f docker/x86_64/Dockerfile-bot-dev .

# Supposing to deploy on x86_64 architecture
docker compose  --env-file ~/.docker/coverletter-dev.env  -f docker/lib/stack-dev.yml up --force-recreate --remove-orphans --renew-anon-volumes
