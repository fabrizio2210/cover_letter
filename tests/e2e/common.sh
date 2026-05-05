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

e2e_prepare_artifacts() {
  mkdir -p "$E2E_ARTIFACT_DIR"
  rm -f "$E2E_RUN_ID_FILE"
}

e2e_export_stack_env() {
  local docker_host
  docker_host="$(e2e_docker_host)"

  export E2E_DOCKER_HOST="$docker_host"
  export PYTHONPATH="$E2E_REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
  export E2E_RUN_ID_FILE

  if docker compose -f "$COMPOSE_FILE" ps --services --status running | grep -qx mongo; then
    export MONGO_HOST="mongodb://$docker_host:$(e2e_compose_port mongo 27017)/"
  fi

  if docker compose -f "$COMPOSE_FILE" ps --services --status running | grep -qx redis; then
    export REDIS_HOST="$docker_host"
    export REDIS_PORT="$(e2e_compose_port redis 6379)"
  fi

  if docker compose -f "$COMPOSE_FILE" ps --services --status running | grep -qx api; then
    export API_HOST="http://$docker_host:$(e2e_compose_port api 8080)"
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
