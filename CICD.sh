#!/bin/bash -xe

#############
# Environment

if [ $(uname -m) = "x86_64" ] ; then
  arch="x86_64"
else
  arch="armv7hf"
fi

DOCKER_ORG="${DOCKER_ORG:-fabrizio2210}"
CANDIDATE_TAG="${CANDIDATE_TAG:-candidate-${CI_COMMIT_SHA:-$(git rev-parse --short HEAD)}}"
PROMOTE_TAGS="${PROMOTE_TAGS:-$arch}"
STACK_NAME="${STACK_NAME:-coverletter}"
STACK_FILE="${STACK_FILE:-docker/prod/stack.yml}"
DEPLOY_TAG="${DEPLOY_TAG:-${PROMOTE_TAGS%% *}}"

if [ -z "$DEPLOY_TAG" ] ; then
  DEPLOY_TAG="$arch"
fi

declare -A CANDIDATE_DIGESTS

######################
# Supporting functions

build_candidate_image() {
  local image="$1"
  local dockerfile="$2"
  local tag="$DOCKER_ORG/$image:$CANDIDATE_TAG-$arch"
  docker buildx build --cache-from type=registry,ref=$DOCKER_ORG/$image:$arch --push -t "$tag" -f "$dockerfile" .

  local digest
  digest="$(docker buildx imagetools inspect "$tag" | awk '/^Digest:/ {print $2; exit}')"

  if [ -z "$digest" ]; then
    echo "Failed to resolve digest for candidate image: $tag"
    exit 2
  fi

  CANDIDATE_DIGESTS["$image"]="$digest"
  echo "[ci] candidate image built: $tag@$digest"
}

promote_candidate_image() {
  local image="$1"
  local digest="$2"
  local promote_tag

  for promote_tag in $PROMOTE_TAGS; do
    docker buildx imagetools create -t "$DOCKER_ORG/$image:$promote_tag" "$DOCKER_ORG/$image@$digest"
    echo "[ci] promoted $DOCKER_ORG/$image@$digest -> $DOCKER_ORG/$image:$promote_tag"
  done
}

assert_swarm_secret_exists() {
  local secret_name="$1"

  if ! docker secret inspect "$secret_name" >/dev/null 2>&1; then
    echo "Missing required Docker Swarm secret: $secret_name"
    exit 2
  fi
}

################
# Login creation

if [ ! -f ~/.docker/config.json ] ; then 
  mkdir -p ~/.docker/

  if [ -z "$DOCKER_LOGIN" ] ; then
	  echo "Docker login not found in the environment, set DOCKER_LOGIN"
  else
    cat << EOF > ~/.docker/config.json
{
  "experimental": "enabled",
        "auths": {
                "https://index.docker.io/v1/": {
                        "auth": "$DOCKER_LOGIN"
                }
        },
        "HttpHeaders": {
                "User-Agent": "Docker-Client/17.12.1-ce (linux)"
        }
}
EOF
  fi
fi

##################
# BUILD BASE IMAGE
if [ "$MANUAL_TRIGGER" == "1" ]; then
  docker builder prune --all -f
  docker buildx build -t fabrizio2210/docker_light-cover_letter:$arch --push -f docker/x86_64/Dockerfile-container .
fi


########################
# BUILD CANDIDATE IMAGES (always-on)
export E2E_COMPOSE_FILE="${E2E_COMPOSE_FILE:-tests/e2e/docker-compose.candidate.yml}"
export E2E_WORKFLOW1_COMPOSE_FILE="${E2E_WORKFLOW1_COMPOSE_FILE:-tests/e2e/docker-compose.workflow1.yml}"

# Service images
build_candidate_image "coverletter-api" "docker/x86_64/Dockerfile-api"
build_candidate_image "coverletter-ai" "docker/x86_64/Dockerfile-ai"
build_candidate_image "coverletter-ai-scorer" "docker/x86_64/Dockerfile-ai-scorer"
build_candidate_image "coverletter-web-crawler" "docker/x86_64/Dockerfile-web-crawler"
build_candidate_image "coverletter-frontend" "docker/x86_64/Dockerfile-frontend"
build_candidate_image "coverletter-telegram-bot" "docker/x86_64/Dockerfile-bot"
build_candidate_image "coverletter-crawler-4dayweek" "docker/x86_64/Dockerfile-crawler-4dayweek"
build_candidate_image "coverletter-crawler-ats-job-extraction" "docker/x86_64/Dockerfile-crawler-ats-job-extraction"
build_candidate_image "coverletter-crawler-hackernews" "docker/x86_64/Dockerfile-crawler-hackernews"
build_candidate_image "coverletter-crawler-levelsfyi" "docker/x86_64/Dockerfile-crawler-levelsfyi"
build_candidate_image "coverletter-crawler-ycombinator" "docker/x86_64/Dockerfile-crawler-ycombinator"
build_candidate_image "coverletter-enrichment-ats-enrichment" "docker/x86_64/Dockerfile-enrichment-ats-enrichment"
build_candidate_image "coverletter-enrichment-retiring-jobs" "docker/x86_64/Dockerfile-enrichment-retiring-jobs"

# Export E2E image digests for harness services
export E2E_IMAGE_API="$DOCKER_ORG/coverletter-api@${CANDIDATE_DIGESTS[coverletter-api]}"
export E2E_IMAGE_AI_QUERIER="$DOCKER_ORG/coverletter-ai@${CANDIDATE_DIGESTS[coverletter-ai]}"
export E2E_IMAGE_AI_SCORER="$DOCKER_ORG/coverletter-ai-scorer@${CANDIDATE_DIGESTS[coverletter-ai-scorer]}"
export E2E_IMAGE_WEB_CRAWLER="$DOCKER_ORG/coverletter-web-crawler@${CANDIDATE_DIGESTS[coverletter-web-crawler]}"


########################
# PYTHON DEPENDENCY SAFE NET
pip_cmd=""
if [ -x "/opt/venv/bin/pip" ] ; then
  pip_cmd="/opt/venv/bin/pip"
elif [ -x "$PWD/.venv/bin/pip" ] ; then
  pip_cmd="$PWD/.venv/bin/pip"
elif [ -n "$VIRTUAL_ENV" ] && [ -x "$VIRTUAL_ENV/bin/pip" ] ; then
  pip_cmd="$VIRTUAL_ENV/bin/pip"
fi

if [ -n "$pip_cmd" ] ; then
  "$pip_cmd" install --upgrade pip
  "$pip_cmd" install -r src/python/ai_querier/requirements.txt
  "$pip_cmd" install -r src/python/ai_scorer/requirements.txt
  "$pip_cmd" install -r src/python/web_crawler/requirements.txt
else
  echo "Skipping Python dependency bootstrap: no virtualenv pip found"
fi

#######
# TESTS
bash scripts/test-gate.sh --mode full

########################
# PROMOTE ALL CANDIDATES (unconditional, after tests pass)
echo "[ci] Promoting all candidate images to $PROMOTE_TAGS"
promote_candidate_image "coverletter-api" "${CANDIDATE_DIGESTS[coverletter-api]}"
promote_candidate_image "coverletter-ai" "${CANDIDATE_DIGESTS[coverletter-ai]}"
promote_candidate_image "coverletter-ai-scorer" "${CANDIDATE_DIGESTS[coverletter-ai-scorer]}"
promote_candidate_image "coverletter-web-crawler" "${CANDIDATE_DIGESTS[coverletter-web-crawler]}"
promote_candidate_image "coverletter-frontend" "${CANDIDATE_DIGESTS[coverletter-frontend]}"
promote_candidate_image "coverletter-telegram-bot" "${CANDIDATE_DIGESTS[coverletter-telegram-bot]}"
promote_candidate_image "coverletter-crawler-4dayweek" "${CANDIDATE_DIGESTS[coverletter-crawler-4dayweek]}"
promote_candidate_image "coverletter-crawler-ats-job-extraction" "${CANDIDATE_DIGESTS[coverletter-crawler-ats-job-extraction]}"
promote_candidate_image "coverletter-crawler-hackernews" "${CANDIDATE_DIGESTS[coverletter-crawler-hackernews]}"
promote_candidate_image "coverletter-crawler-levelsfyi" "${CANDIDATE_DIGESTS[coverletter-crawler-levelsfyi]}"
promote_candidate_image "coverletter-crawler-ycombinator" "${CANDIDATE_DIGESTS[coverletter-crawler-ycombinator]}"
promote_candidate_image "coverletter-enrichment-ats-enrichment" "${CANDIDATE_DIGESTS[coverletter-enrichment-ats-enrichment]}"
promote_candidate_image "coverletter-enrichment-retiring-jobs" "${CANDIDATE_DIGESTS[coverletter-enrichment-retiring-jobs]}"

######################
# SWARM STACK DEPLOY

if [ "${DEPLOY:-0}" == "1" ]; then
  if [ "$(docker info --format '{{.Swarm.LocalNodeState}}')" != "active" ]; then
    echo "Docker Swarm is not active on this node"
    exit 2
  fi

  if [ "$(docker info --format '{{.Swarm.ControlAvailable}}')" != "true" ]; then
    echo "Current node is not a Swarm manager"
    exit 2
  fi

  for required_secret in COVERLETTER_BOT_TOKEN COVERLETTER_GEMINI_TOKEN COVERLETTER_ADMIN_PASSWORD COVERLETTER_AUTH_USERS_JSON COVERLETTER_ADMIN_JWT_SECRET COVERLETTER_JWT_SECRET COVERLETTER_SERPER_API_KEY COVERLETTER_MONGO_PASSWORD; do
    assert_swarm_secret_exists "$required_secret"
  done

  export DOCKER_ORG
  export DEPLOY_TAG

  docker stack deploy --with-registry-auth -c "$STACK_FILE" "$STACK_NAME"
  docker stack services "$STACK_NAME"
else
  echo "Skipping stack deployment: DEPLOY is not set to 1"
fi

######################
# Cleanup
docker container prune -f
docker volume prune -f