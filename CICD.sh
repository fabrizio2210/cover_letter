#!/bin/bash -xe

#############
# Environment

if [ $(uname -m) = "x86_64" ] ; then
  arch="x86_64"
  dockerArch="x86_64"
else
  arch="armv7hf"
  dockerArch="armhf"
fi

changedFiles="$(git diff --name-only HEAD^1 HEAD)"

DOCKER_ORG="${DOCKER_ORG:-fabrizio2210}"
CANDIDATE_TAG="${CANDIDATE_TAG:-candidate-${CI_COMMIT_SHA:-$(git rev-parse --short HEAD)}}"
ENABLE_CANDIDATE_BUILD="${ENABLE_CANDIDATE_BUILD:-0}"
PROMOTE_CANDIDATE="${PROMOTE_CANDIDATE:-0}"
PROMOTE_TAGS="${PROMOTE_TAGS:-$arch}"

declare -A CANDIDATE_DIGESTS

######################
# Supporting functions

build_candidate_image() {
  local image="$1"
  local dockerfile="$2"
  local tag="$DOCKER_ORG/$image:$CANDIDATE_TAG-$arch"
  docker buildx build --push -t "$tag" -f "$dockerfile" .

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


# E2E execution mode:
# - source (default): existing source-mounted compose flow
# - candidate: runtime services come from candidate images via compose overrides
E2E_MODE="${E2E_MODE:-source}"

case "$E2E_MODE" in
  source)
    ;;
  candidate)
    export E2E_COMPOSE_FILE="${E2E_COMPOSE_FILE:-tests/e2e/docker-compose.candidate.yml}"
    export E2E_WORKFLOW1_COMPOSE_FILE="${E2E_WORKFLOW1_COMPOSE_FILE:-tests/e2e/docker-compose.workflow1.yml}"

    if [ "$ENABLE_CANDIDATE_BUILD" = "1" ]; then
      build_candidate_image "coverletter-api" "docker/x86_64/Dockerfile-api"
      build_candidate_image "coverletter-ai" "docker/x86_64/Dockerfile-ai"
      build_candidate_image "coverletter-ai-scorer" "docker/x86_64/Dockerfile-ai-scorer"
      build_candidate_image "coverletter-web-crawler" "docker/x86_64/Dockerfile-web-crawler"

      export E2E_IMAGE_API="$DOCKER_ORG/coverletter-api@${CANDIDATE_DIGESTS[coverletter-api]}"
      export E2E_IMAGE_AI_QUERIER="$DOCKER_ORG/coverletter-ai@${CANDIDATE_DIGESTS[coverletter-ai]}"
      export E2E_IMAGE_AI_SCORER="$DOCKER_ORG/coverletter-ai-scorer@${CANDIDATE_DIGESTS[coverletter-ai-scorer]}"
      export E2E_IMAGE_WEB_CRAWLER="$DOCKER_ORG/coverletter-web-crawler@${CANDIDATE_DIGESTS[coverletter-web-crawler]}"
    fi

    for required_var in E2E_IMAGE_API E2E_IMAGE_AI_QUERIER E2E_IMAGE_AI_SCORER E2E_IMAGE_WEB_CRAWLER; do
      if [ -z "${!required_var:-}" ]; then
        echo "Missing required candidate image variable: $required_var"
        echo "Set ENABLE_CANDIDATE_BUILD=1 or provide explicit E2E_IMAGE_* values"
        exit 2
      fi
    done
    ;;
  *)
    echo "Unsupported E2E_MODE: $E2E_MODE"
    echo "Supported values: source, candidate"
    exit 2
    ;;
esac

##################
# BUILD BASE IMAGE
if [ "$MANUAL_TRIGGER" == "1" ] || grep -q "Dockerfile-container" <<< "$changedFiles"; then
  docker buildx build -t fabrizio2210/docker_light-cover_letter:$arch --push -f docker/x86_64/Dockerfile-container .
fi

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

#############
# BUILD IMAGE

if [ "$MANUAL_TRIGGER" == "1" ] || grep -q "Dockerfile-bot\|telegram_bot" <<< "$changedFiles"; then
  docker buildx build -t fabrizio2210/coverletter-telegram-bot:$arch --push -f docker/x86_64/Dockerfile-bot .
fi
if [ "$MANUAL_TRIGGER" == "1" ] || grep -q "Dockerfile-ai\|ai_querier" <<< "$changedFiles"; then
  docker buildx build -t fabrizio2210/coverletter-ai:$arch --push -f docker/x86_64/Dockerfile-ai .
fi
if [ "$MANUAL_TRIGGER" == "1" ] || grep -qE "Dockerfile-frontend|src/js/coverletter-frontend" <<< "$changedFiles"; then
  docker buildx build -t fabrizio2210/coverletter-frontend:$arch --push -f docker/x86_64/Dockerfile-frontend .
fi
if [ "$MANUAL_TRIGGER" == "1" ] || grep -qE "Dockerfile-api|src/go/cmd/api" <<< "$changedFiles"; then
  docker buildx build -t fabrizio2210/coverletter-api:$arch --push -f docker/x86_64/Dockerfile-api .
fi
if [ "$MANUAL_TRIGGER" == "1" ] || grep -qE "Dockerfile-ai-scorer|ai_scorer" <<< "$changedFiles"; then
  docker buildx build -t fabrizio2210/coverletter-ai-scorer:$arch --push -f docker/x86_64/Dockerfile-ai-scorer .
fi
if [ "$MANUAL_TRIGGER" == "1" ] || grep -qE "Dockerfile-web-crawler|web_crawler" <<< "$changedFiles"; then
  docker buildx build -t fabrizio2210/coverletter-web-crawler:$arch --push -f docker/x86_64/Dockerfile-web-crawler .
fi
if [ "$MANUAL_TRIGGER" == "1" ] || grep -qE "Dockerfile-crawler-4dayweek|crawler_4dayweek" <<< "$changedFiles"; then
  docker buildx build -t fabrizio2210/coverletter-crawler-4dayweek:$arch --push -f docker/x86_64/Dockerfile-crawler-4dayweek .
fi
if [ "$MANUAL_TRIGGER" == "1" ] || grep -qE "Dockerfile-crawler-ats-job-extraction|crawler_ats_job_extraction" <<< "$changedFiles"; then
  docker buildx build -t fabrizio2210/coverletter-crawler-ats-job-extraction:$arch --push -f docker/x86_64/Dockerfile-crawler-ats-job-extraction .
fi
if [ "$MANUAL_TRIGGER" == "1" ] || grep -qE "Dockerfile-crawler-hackernews|crawler_hackernews" <<< "$changedFiles"; then
  docker buildx build -t fabrizio2210/coverletter-crawler-hackernews:$arch --push -f docker/x86_64/Dockerfile-crawler-hackernews .
fi
if [ "$MANUAL_TRIGGER" == "1" ] || grep -qE "Dockerfile-crawler-levelsfyi|crawler_levelsfyi" <<< "$changedFiles"; then
  docker buildx build -t fabrizio2210/coverletter-crawler-levelsfyi:$arch --push -f docker/x86_64/Dockerfile-crawler-levelsfyi .
fi
if [ "$MANUAL_TRIGGER" == "1" ] || grep -qE "Dockerfile-crawler-ycombinator|crawler_ycombinator" <<< "$changedFiles"; then
  docker buildx build -t fabrizio2210/coverletter-crawler-ycombinator:$arch --push -f docker/x86_64/Dockerfile-crawler-ycombinator .
fi
if [ "$MANUAL_TRIGGER" == "1" ] || grep -qE "Dockerfile-enrichment-ats-enrichment|enrichment_ats_enrichment" <<< "$changedFiles"; then
  docker buildx build -t fabrizio2210/coverletter-enrichment-ats-enrichment:$arch --push -f docker/x86_64/Dockerfile-enrichment-ats-enrichment .
fi
if [ "$MANUAL_TRIGGER" == "1" ] || grep -qE "Dockerfile-enrichment-retiring-jobs|enrichment_retiring_jobs" <<< "$changedFiles"; then
  docker buildx build -t fabrizio2210/coverletter-enrichment-retiring-jobs:$arch --push -f docker/x86_64/Dockerfile-enrichment-retiring-jobs .
fi

if [ "$E2E_MODE" = "candidate" ] && [ "$ENABLE_CANDIDATE_BUILD" = "1" ] && [ "$PROMOTE_CANDIDATE" = "1" ]; then
  promote_candidate_image "coverletter-api" "${CANDIDATE_DIGESTS[coverletter-api]}"
  promote_candidate_image "coverletter-ai" "${CANDIDATE_DIGESTS[coverletter-ai]}"
  promote_candidate_image "coverletter-ai-scorer" "${CANDIDATE_DIGESTS[coverletter-ai-scorer]}"
  promote_candidate_image "coverletter-web-crawler" "${CANDIDATE_DIGESTS[coverletter-web-crawler]}"
fi
