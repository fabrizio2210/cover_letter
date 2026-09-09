# Production stack

The production stack runs on the three-node Docker Swarm formed by
`raspberrypi5`, `raspberrypi6`, and `raspberrypi7`.

## MongoDB replica set

MongoDB runs as the `coverletter-rs` replica set. Each member is constrained to
one node and stores its data in the node-local directory:

```text
/mnt/disk-usb1/docker/coverletter/mongo
```

The directory must exist before deploying the stack, must be owned by UID and
GID 999, and must have mode `0700`. The `mongo-rs-init` replicated job waits for
all members and initializes a new replica set. It does not reconfigure an
existing replica set.

MongoDB authentication is intentionally disabled. The database is not
published on a host port and is reachable only from services attached to the
stack-managed `coverletter_backend` network. Any container on that network has
unrestricted database access.

The old NFS-backed Docker volume is retained outside the active stack as a
rollback copy. Do not remove it until the local replica set and a controlled
failover have been verified.

## MongoDB migration runbook

Run the cutover from a Swarm manager. Do not deploy the replica-set stack until
the offline copy and its checksum verification have completed. The old volume
is `coverletter_mongo_data`, mounted by the old service as `/data/db`.

### 1. Preflight

Confirm the old service and volume, record the current replica counts, and
confirm that all three target mounts are SSD-backed:

```sh
docker service inspect coverletter_mongo \
  --format '{{range .Spec.TaskTemplate.ContainerSpec.Mounts}}{{println .Source .Target}}{{end}}'
docker service ps coverletter_mongo --filter desired-state=running \
  --format 'node={{.Node}} task={{.Name}}'
docker stack services coverletter
findmnt -no SOURCE,FSTYPE,TARGET /mnt/disk-usb1
```

Log in to the node reported for the running old MongoDB task and inspect the
volume there:

```sh
docker volume inspect coverletter_mongo_data \
  --format 'driver={{.Driver}} type={{index .Options "type"}} options={{index .Options "o"}} device={{index .Options "device"}}'
```

Require driver `local`, type `nfs`, NFS server `192.168.100.1`, and device
`:/mnt/HDD/docker/coverletter/mongo_data`. A volume with absent or different
options is not the source database.

On the current MongoDB node, open an authenticated shell without putting the
password on the command line:

```sh
docker exec -it "$(docker ps -q --filter name=coverletter_mongo)" \
  mongo --authenticationDatabase admin --username root --password
```

In that shell, record the aggregate database, collection, and document counts:

```javascript
var excluded = {admin: 1, config: 1, local: 1};
var names = db.adminCommand({listDatabases: 1}).databases
  .map(function (entry) { return entry.name; })
  .filter(function (name) { return !excluded[name]; });
var collections = 0;
var documents = 0;
names.forEach(function (name) {
  var target = db.getSiblingDB(name);
  var collectionNames = target.getCollectionNames();
  collections += collectionNames.length;
  collectionNames.forEach(function (collection) {
    documents += target.getCollection(collection).count({});
  });
});
printjson({databases: names.length, collections: collections, documents: documents});
```

### 2. Quiesce writers and stop MongoDB

Scale every old service except MongoDB to zero first. Wait until no application
task remains, then stop MongoDB so WiredTiger closes cleanly:

```sh
docker stack services coverletter --format '{{.Name}}' |
  while read -r service; do
    if [ "$service" != coverletter_mongo ]; then
      docker service scale "$service=0"
    fi
  done

while docker stack ps coverletter --filter desired-state=running \
  --format '{{.Name}}' | grep -vq '^coverletter_mongo\.'; do
  sleep 2
done

docker service scale coverletter_mongo=0
while docker stack ps coverletter --filter desired-state=running -q | grep -q .; do
  sleep 2
done
```

Do not copy live WiredTiger files. Confirm the old MongoDB task is stopped
before continuing.

### 3. Prepare local storage

Run these commands on `raspberrypi5`, `raspberrypi6`, and `raspberrypi7`:

```sh
sudo install -d -o root -g root -m 0755 /mnt/disk-usb1/docker
sudo install -d -o root -g root -m 0755 /mnt/disk-usb1/docker/coverletter
sudo install -d -o 999 -g 999 -m 0700 \
  /mnt/disk-usb1/docker/coverletter/mongo
test -z "$(sudo find /mnt/disk-usb1/docker/coverletter/mongo \
  -mindepth 1 -maxdepth 1 -print -quit)"
```

The empty-directory check must pass on all three nodes.

### 4. Copy and verify the stopped data

Run this only on `raspberrypi5`. Create a uniquely named, explicitly configured
read-only mount of the known NFS export. Abort if that volume name already
exists, because Docker otherwise keeps its existing options:

```sh
if docker volume inspect coverletter_mongo_nfs_migration_source >/dev/null 2>&1; then
  echo 'Migration source volume already exists; inspect it and abort this run.' >&2
  exit 1
fi
docker volume create --driver local \
  --opt type=nfs \
  --opt o=addr=192.168.100.1,nolock,soft,ro \
  --opt device=:/mnt/HDD/docker/coverletter/mongo_data \
  coverletter_mongo_nfs_migration_source
docker volume inspect coverletter_mongo_nfs_migration_source \
  --format 'driver={{.Driver}} type={{index .Options "type"}} options={{index .Options "o"}} device={{index .Options "device"}}'
```

Require the printed options to match the command exactly. Then copy the stopped
database to the new primary directory, compare the complete regular-file lists,
and verify every copied file by SHA-256:

```sh
docker run --rm --entrypoint /bin/sh \
  -v coverletter_mongo_nfs_migration_source:/source:ro \
  -v /mnt/disk-usb1/docker/coverletter/mongo:/dest \
  mongo:4.4.18 -c '
    set -eu
    test -z "$(find /dest -mindepth 1 -maxdepth 1 -print -quit)"
    test -f /source/WiredTiger
    test -s /source/WiredTiger.wt
    cp -a /source/. /dest/
    chown -R 999:999 /dest
    chmod 0700 /dest
    (cd /source && find . -type f -print0 | sort -z | xargs -0 sha256sum) > /tmp/source.sha256
    (cd /dest && find . -type f -print0 | sort -z | xargs -0 sha256sum) > /tmp/dest.sha256
    cmp /tmp/source.sha256 /tmp/dest.sha256
    printf "verified_files=%s\n" "$(wc -l < /tmp/source.sha256)"
  '
```

Leave the directories on `raspberrypi6` and `raspberrypi7` empty. They receive
their data through MongoDB initial sync.

### 5. Deploy the migration stage and verify

Deploy the base stack together with `mongo-migration.yml` only after the copy
succeeds. The override keeps every API, worker, frontend, Redis, Ollama, and
mongo-express service at zero replicas while starting the three MongoDB members
and initializer:

```sh
DOCKER_ORG=<deployment-org> DEPLOY_TAG=<deployment-tag> \
  docker stack deploy --prune --with-registry-auth \
  -c docker/prod/stack.yml -c docker/prod/mongo-migration.yml coverletter
```

Confirm that no non-Mongo task is running. The command must print nothing:

```sh
docker stack ps coverletter --filter desired-state=running \
  --format '{{.Name}}' |
  grep -Ev '^coverletter_(mongo5|mongo6|mongo7)\.' || true
```

The `mongo-rs-init` job initializes the replica set after all three members
answer health checks.

From the Swarm manager, verify the job and member services:

```sh
docker service logs coverletter_mongo-rs-init
docker service ps coverletter_mongo5
docker service ps coverletter_mongo6
docker service ps coverletter_mongo7
```

On `raspberrypi5`, inspect replica status:

```sh
docker exec "$(docker ps -q --filter name=coverletter_mongo5)" \
  mongo --quiet --eval '
    var status = rs.status();
    printjson({
      set: status.set,
      ok: status.ok,
      members: status.members.map(function (member) {
        return {name: member.name, state: member.stateStr, health: member.health};
      })
    });
  '
```

Proceed only when there is one `PRIMARY`, two `SECONDARY` members, and all
three report health `1`. Run the aggregate-count JavaScript from preflight
against the new primary without authentication and compare the three totals.

Only after replica health, file integrity, and logical parity pass may the
normal stack be deployed. The CI/CD pipeline normally performs that deployment
by running `CICD.sh` with `DEPLOY=1`; unlike the migration override, it restores
the configured client replica counts.

### 6. Controlled failover

After the normal client services are running, stop the original primary from
the Swarm manager:

```sh
docker service scale coverletter_mongo5=0
```

On `raspberrypi6` and `raspberrypi7`, query the local MongoDB task until one
reports `ismaster: true`. Confirm the application remains usable, then restore
the member and wait for it to become a healthy secondary:

```sh
docker service scale coverletter_mongo5=1
```

Repeat the replica-status check and require three healthy members before the
migration is considered complete.

### 7. Rollback

The NFS volume is a point-in-time rollback copy from the cutover. A direct
rollback discards writes accepted by the replica set after the copy. If those
writes must be retained, stop all writers and reconcile or export them before
continuing.

Stop the new stack tasks, render the known-good pre-migration stack, and deploy
it with the same image variables used by the previous deployment:

```sh
docker service rm coverletter_mongo-rs-init 2>/dev/null || true
docker stack services coverletter --format '{{.Name}}' |
  while read -r service; do docker service scale "$service=0"; done
while docker stack ps coverletter --filter desired-state=running -q | grep -q .; do
  sleep 2
done

git show 1b29afef39389296ee642c9aa1d26e1f364a06a3:docker/prod/stack.yml \
  > /tmp/coverletter-stack-rollback.yml
DOCKER_ORG=<previous-org> DEPLOY_TAG=<previous-tag> \
  docker stack deploy --prune --with-registry-auth \
  -c /tmp/coverletter-stack-rollback.yml coverletter
```

Verify that `coverletter_mongo` mounts `coverletter_mongo_data`, starts with
authentication enabled, and that the application aggregate counts match the
preflight totals. Do not delete either storage copy during rollback.

## Networks

Backend services use the stack-managed `coverletter_backend` overlay. It is not
an internal Docker network because crawlers and AI services require outbound
Internet access.

The API and mongo-express also join the external `traefik_backends` network.
Their `traefik.docker.network` label explicitly selects that network so Traefik
does not attempt to route through the private overlay. The frontend joins only
`traefik_backends`.

Mongo-express remains exposed through Traefik without dedicated HTTP
authentication. Adding that authentication is tracked as separate work.
