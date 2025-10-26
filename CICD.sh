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

#############
# BUILD IMAGE

if [ "$MANUAL_TRIGGER" == "1" ] || grep -q "Dockerfile-container" <<< "$changedFiles"; then
  docker buildx build -t fabrizio2210/docker_light-cover_letter:$arch --push -f docker/x86_64/Dockerfile-container .
fi
if [ "$MANUAL_TRIGGER" == "1" ] || grep -q "Dockerfile-bot\|telegram_bot" <<< "$changedFiles"; then
  docker buildx build -t fabrizio2210/coverletter-telegram-bot:$arch --push -f docker/x86_64/Dockerfile-bot .
fi
if [ "$MANUAL_TRIGGER" == "1" ] || grep -q "Dockerfile-ai\|ai_querier" <<< "$changedFiles"; then
  docker buildx build -t fabrizio2210/coverletter-ai:$arch --push -f docker/x86_64/Dockerfile-ai .
fi
if [ "$MANUAL_TRIGGER" == "1" ] || grep -qE "Dockerfile-frontend|src/js/coverletter-frontend" <<< "$changedFiles"; then
  docker buildx build -t fabrizio2210/coverletter-frontend:$arch --push -f docker/x86_64/Dockerfile-frontend .
fi
if [ "$MANUAL_TRIGGER" == "1" ] || grep -qE "Dockerfile-api|src/go" <<< "$changedFiles"; then
  docker buildx build -t fabrizio2210/coverletter-api:$arch --push -f docker/x86_64/Dockerfile-api .
fi
