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

if [ -z "$ACTION" ] && grep -q "Dockerfile" <<< "$changedFiles"; then
  docker buildx build -t fabrizio2210/docker_light-cover_letter:$arch --push -f docker/x86_64/Dockerfile-container .
fi


#############
# Actions

if [ "$ACTION" = "insert_email" ]; then
    python3 src/python/email_extractor/insert_email.py --db_uri ${DB_URI} --db_name cover_letter --email ${EMAIL_TO_INSERT}
fi