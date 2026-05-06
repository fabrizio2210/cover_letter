#!/usr/bin/env bash
set -euo pipefail

E2E_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
E2E_ARTIFACT_DIR="${E2E_ARTIFACT_DIR:-$E2E_REPO_ROOT/tests/e2e/.artifacts}"
E2E_RUN_ID_FILE="${E2E_RUN_ID_FILE:-$E2E_ARTIFACT_DIR/crawl_run_id}"

e2e_docker_host() {
  if [[ -n "${E2E_DOCKER_HOST:-}" ]]; then
    printf '%s\n' "$E2E_DOCKER_HOST"
    return 0
  fi

  if [[ ! -f /.dockerenv ]]; then
    printf '127.0.0.1\n'
    return 0
  fi

  local gateway
  gateway="$(ip route show default 2>/dev/null | awk '/default/ {print $3; exit}')"
  if [[ -n "$gateway" ]]; then
    printf '%s\n' "$gateway"
    return 0
  fi

  if getent hosts host.docker.internal >/dev/null 2>&1; then
    printf 'host.docker.internal\n'
    return 0
  fi

  echo "[e2e] ERROR: unable to resolve Docker host gateway" >&2
  return 1
}

e2e_compose_port() {
  local service="$1"
  local container_port="$2"
  local mapping

  mapping="$(docker compose -f "$COMPOSE_FILE" port "$service" "$container_port" | head -n1)"
  if [[ -z "$mapping" ]]; then
    echo "[e2e] ERROR: no published port for $service:$container_port in $COMPOSE_FILE" >&2
    return 1
  fi

  printf '%s\n' "${mapping##*:}"
}

e2e_wait_compose_port() {
  local service="$1"
  local container_port="$2"
  local timeout_seconds="${3:-20}"
  local elapsed=0
  local port

  while (( elapsed < timeout_seconds )); do
    if port="$(e2e_compose_port "$service" "$container_port" 2>/dev/null)"; then
      printf '%s\n' "$port"
      return 0
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done

  echo "[e2e] ERROR: no published port for $service:$container_port after ${timeout_seconds}s in $COMPOSE_FILE" >&2
  return 1
}

e2e_compose_has_service() {
  local service="$1"
  docker compose -f "$COMPOSE_FILE" config --services | grep -qx "$service"
}

e2e_wait_tcp() {
  local host="$1"
  local port="$2"
  local timeout_seconds="${3:-30}"
  local elapsed=0

  while (( elapsed < timeout_seconds )); do
    if (echo >"/dev/tcp/$host/$port") >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done

  echo "[e2e] ERROR: TCP endpoint $host:$port not reachable after ${timeout_seconds}s" >&2
  return 1
}

e2e_wait_api_ready() {
  local api_host="$1"
  local host_port
  local host
  local port

  host_port="${api_host#http://}"
  host_port="${host_port#https://}"
  host="${host_port%%:*}"
  if [[ "$host_port" == *:* ]]; then
    port="${host_port##*:}"
  else
    port="80"
  fi

  e2e_wait_tcp "$host" "$port" 30
}

e2e_attach_service_to_network() {
  local service="$1"
  local container_id

  if [[ -z "${LIGHTCICD_ATTACHABLE_NETWORK:-}" ]]; then
    return 0
  fi

  container_id="$(docker compose -f "$COMPOSE_FILE" ps -q "$service" | head -n1)"
  if [[ -z "$container_id" ]]; then
    echo "[e2e] ERROR: unable to find running container for service '$service' in $COMPOSE_FILE" >&2
    return 1
  fi

  # Idempotent attach: no-op if container is already connected.
  docker network connect --alias "$service" "$LIGHTCICD_ATTACHABLE_NETWORK" "$container_id" >/dev/null 2>&1 || true
}

e2e_prepare_artifacts() {
  mkdir -p "$E2E_ARTIFACT_DIR"
  rm -f "$E2E_RUN_ID_FILE"
}

e2e_export_stack_env() {
  local docker_host
  local mongo_port
  local redis_port
  local api_port
  export PYTHONPATH="$E2E_REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
  export E2E_RUN_ID_FILE

  if [[ -n "${LIGHTCICD_ATTACHABLE_NETWORK:-}" ]]; then
    e2e_attach_service_to_network mongo
    export MONGO_HOST="mongodb://mongo:27017/"

    if e2e_compose_has_service redis; then
      e2e_attach_service_to_network redis
      export REDIS_HOST="redis"
      export REDIS_PORT="6379"
    fi

    if e2e_compose_has_service api; then
      e2e_attach_service_to_network api
      export API_HOST="http://api:8080"
      e2e_wait_api_ready "$API_HOST"
    fi

    if [[ "${E2E_DEBUG:-0}" = "1" ]]; then
      echo "[e2e] endpoints MONGO_HOST=$MONGO_HOST REDIS_HOST=${REDIS_HOST:-unset} REDIS_PORT=${REDIS_PORT:-unset} API_HOST=${API_HOST:-unset}"
    fi
    return 0
  fi

  docker_host="$(e2e_docker_host)"

  export E2E_DOCKER_HOST="$docker_host"

  mongo_port="$(e2e_wait_compose_port mongo 27017 30)"

  export MONGO_HOST="mongodb://$docker_host:$mongo_port/"

  if e2e_compose_has_service redis; then
    redis_port="$(e2e_wait_compose_port redis 6379 30)"
    export REDIS_HOST="$docker_host"
    export REDIS_PORT="$redis_port"
  fi

  if e2e_compose_has_service api; then
    if api_port="$(e2e_wait_compose_port api 8080 10 2>/dev/null)"; then
      export API_HOST="http://$docker_host:$api_port"
      e2e_wait_api_ready "$API_HOST"
    fi
  fi

  if [[ "${E2E_DEBUG:-0}" = "1" ]]; then
    echo "[e2e] endpoints MONGO_HOST=$MONGO_HOST REDIS_HOST=${REDIS_HOST:-unset} REDIS_PORT=${REDIS_PORT:-unset} API_HOST=${API_HOST:-unset}"
  fi
}

e2e_run_python() {
  local script_path="$1"
  shift

  (
    cd "$E2E_REPO_ROOT"
    python3 "$script_path" "$@"
  )
}
