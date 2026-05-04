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

########
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

############################
# E2E DOCKER-IN-DOCKER SETUP
# When CICD runs inside a Docker container that shares the host Docker socket,
# compose bind mounts must reference HOST-SIDE paths — the in-container path is
# invisible to the host daemon.  We detect this via /.dockerenv, parse
# PROJECTS_VOLUME_STRING to learn which named volume is mounted and where, then
# copy the workspace into a subdir of that volume.  docker inspect gives us the
# host-side source path for the volume so we can expose it as E2E_WORKSPACE_ROOT.
if [ -f /.dockerenv ] && [ -n "${PROJECTS_VOLUME_STRING:-}" ]; then
  _vol_mount="${PROJECTS_VOLUME_STRING#*:}"   # e.g. /opt/data
  _cid=$(grep -oE '[0-9a-f]{64}' /proc/self/cgroup 2>/dev/null | head -1 || true)
  [ -z "$_cid" ] && _cid=$(hostname)
  _host_vol_src=$(VOL_MOUNT="$_vol_mount" docker inspect "$_cid" 2>/dev/null \
    | python3 -c "
import sys, json, os
d = json.load(sys.stdin)
for m in d[0].get('Mounts', []):
    if m.get('Destination') == os.environ['VOL_MOUNT']:
        print(m.get('Source', ''))
        break
" 2>/dev/null | head -1 || true)
  if [ -n "$_host_vol_src" ]; then
    _e2e_subdir="e2e_workspace_$$"
    cp -r "$PWD/." "$_vol_mount/$_e2e_subdir/"
    export E2E_WORKSPACE_ROOT="$_host_vol_src/$_e2e_subdir"
    echo "[ci] DinD mode: E2E_WORKSPACE_ROOT=$E2E_WORKSPACE_ROOT"
    # shellcheck disable=SC2064
    trap "rm -rf '${_vol_mount}/${_e2e_subdir}'" EXIT
  else
    echo "[ci] WARNING: could not resolve host path for volume mount '$_vol_mount'; E2E bind mounts may fail"
  fi
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
