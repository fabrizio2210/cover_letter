#!/bin/bash

# Supposing to deploy on x86_64 architecture
docker compose -f docker/lib/stack-dev.yml up --force-recreate --remove-orphans --renew-anon-volumes
