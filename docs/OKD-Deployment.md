# OKD Deployment

This repo includes Docker and Helm assets adapted from the `mu2e-talks` deployment pattern.

## Required details

Before deploying, decide:

- the container registry/repository to push to, if not `docker.io/normanajn/mu2e-shift-scheduler-web`
- preference collection uses the durable app-relative `./csv` path, mounted in OKD as `/app/csv`
- cert-manager is enabled by default for `mu2e-shifts.fnal.gov` using the `incommon-acme` ClusterIssuer

## Private values

Create `my-values.yaml` and keep it out of git:

```yaml
flask:
  secretKey: "<generate-a-long-random-value>"

preferences:
  csvDir: /app/csv
  shiftsCsv: /app/csv/shifts.csv

auth:
  initialAdminUsername: mu2e-admin
  initialAdminEmail: mu2e-admin@fnal.gov
  initialAdminPassword: "<set-a-local-admin-password>"

oidc:
  providerUrl: "https://<fermilab-oidc-provider>/.well-known/openid-configuration"
  clientId: "<oidc-client-id>"
  clientSecret: "<oidc-client-secret>"
```

The SQLite user database is stored at `/app/data/users.sqlite`, backed by the
`mu2e-shifts-data` PVC. The initial admin account is seeded or updated on
application startup from the `auth.initialAdmin*` values.

Register this OIDC redirect URI for production:

```text
https://mu2e-shifts.fnal.gov/oidc/callback
```

## Build

For native macOS development, run the Python app directly with a virtual
environment. Docker Desktop on macOS runs Linux containers, so Docker builds
should still target Linux platforms.

To rebuild and run the local Docker container in one step:

```sh
./scripts/run-docker-local.sh
```

The script detects `linux/arm64` versus `linux/amd64`, builds with `--load`,
creates local `data/` and `csv/` directories, seeds `csv/shifts.csv` from the
small sample data if needed, seeds a local admin account, and starts the app on
`http://127.0.0.1:8000/`.

Local admin credentials for that script:

```text
mu2e-admin@fnal.gov
local-admin-password
```

For a local Docker test image on Apple Silicon macOS:

```sh
./scripts/build-docker.sh -t local-test --load --platforms linux/arm64
```

For a local Docker test image on Intel/AMD Linux or Intel macOS:

```sh
./scripts/build-docker.sh -t local-test --load --platforms linux/amd64
```

For an OKD release image targeting both AMD64 and ARM64:

```sh
./scripts/build-docker.sh \
  -r docker.io/normanajn/mu2e-shift-scheduler-web \
  -t v0.1.0 \
  --platforms linux/amd64,linux/arm64 \
  --push
```

The build script defaults to `linux/amd64,linux/arm64`, so this shorter command
is equivalent:

```sh
./scripts/build-docker.sh \
  -r docker.io/normanajn/mu2e-shift-scheduler-web \
  -t v0.1.0 \
  --push
```

## Deploy

The default OKD namespace/project and release are `mu2e-shifts`; the default route host is `mu2e-shifts.fnal.gov`.
The default image repository is `docker.io/normanajn/mu2e-shift-scheduler-web`.
The default preference shifts CSV path is `/app/csv/shifts.csv`, with `/app/csv`
backed by the `mu2e-shifts-csv` PVC.
The default long-request timeout is 1800 seconds on both the OKD Route and
Gunicorn worker.
The default web pod resource limit is 2 CPU and 4Gi memory because large solve
requests can exhaust smaller containers and produce fast 502 responses when the
pod is OOM-killed.

```sh
./scripts/deploy-okd.sh -f my-values.yaml
```

Override the image repository if needed:

```sh
./scripts/deploy-okd.sh \
  -r registry.example.org/mu2e/mu2e-shift-scheduler-web \
  -t v0.1.0 \
  -f my-values.yaml
```

## Helm-only deployment

```sh
helm upgrade --install mu2e-shifts ./helm/simple \
  --namespace mu2e-shifts \
  --values my-values.yaml \
  --set-string image.repository=docker.io/normanajn/mu2e-shift-scheduler-web \
  --set-string image.tag=v0.1.0
```

## Test instance (mu2e-test)

The `mu2e-test` project runs a second, independent instance alongside production.
Nothing is shared: separate namespace, PVCs, database, TLS certificate, and
route host. Production keeps running untouched throughout.

### One-time setup

Create the private test values file and give it credentials that differ from
production:

```sh
cp my-values-test.example.yaml my-values-test.yaml
$EDITOR my-values-test.yaml
```

### Deploy

```sh
./scripts/deploy-okd.sh \
  -n mu2e-test \
  --release mu2e-test \
  -t test \
  --tls-secret mu2e-test-tls \
  -f helm/simple/values-test.yaml \
  -f my-values-test.yaml
```

To run the test instance on exactly the image production is serving, rather than
building a new one, reuse its tag and skip the build:

```sh
./scripts/deploy-okd.sh -n mu2e-test --release mu2e-test \
  -t latest --no-build --tls-secret mu2e-test-tls \
  -f helm/simple/values-test.yaml -f my-values-test.yaml
```

`-f` may be repeated; later files override earlier ones, and both layer on top
of `helm/simple/values.yaml`. The committed `helm/simple/values-test.yaml` holds
the non-secret test overrides (route host, TLS secret name, smaller resource
limits, debug logging); `my-values-test.yaml` holds only secrets.

**Always pass `-t test`** (or another test-only tag). The image repository is
shared with production, so building without a distinct tag would overwrite the
tag production pulls — and `image.pullPolicy` is `Always`, so production would
pick up the test image on its next restart.

`--release mu2e-test` makes every resource self-identifying
(`mu2e-test-data`, `mu2e-test-csv`, `mu2e-test-config`, `mu2e-test-secret`) so a
`helm list -A` never leaves any doubt which instance you are looking at.

### Route hostname

The test instance is served at **`https://mu2e-okd-test.fnal.gov`**. That name is
a CNAME to `okdprod1-mu2e-test.fnal.gov` → `131.225.163.71` (the OKD ingress VIP),
the same shape as `mu2e-shifts.fnal.gov` → `okdprod1-mu2e-shifts.fnal.gov`.

Do **not** use `mu2e-test.fnal.gov`. It is an A record for `131.225.113.180`, an
unrelated non-cluster host, and the cluster's OPA admission webhook rejects
Certificates for it in this namespace:

```
admission webhook "validating-webhook.openpolicyagent.org" denied the request:
Certificate denied: DNS name "mu2e-test.fnal.gov" is not permitted in
namespace "mu2e-test"
```

That webhook allowlists Certificate DNS names per namespace, and `mu2e-test` is
registered for `mu2e-okd-test.fnal.gov` only — so the correct name needs no
admin request, and a wrong one fails fast.

### Changing the route hostname later

OpenShift allows a custom `spec.host` when a Route is **created** but not when it
is **updated**, so editing `route.hostname` on an existing release fails with:

```
The Route "web" is invalid: spec.host: Invalid value: "...":
you do not have permission to set the host field of the route
```

Delete the Route first and let Helm recreate it:

```sh
oc delete route web -n mu2e-test
./scripts/deploy-okd.sh -n mu2e-test --release mu2e-test -t latest --no-build \
  --tls-secret mu2e-test-tls \
  -f helm/simple/values-test.yaml -f my-values-test.yaml
```

### Checking before you deploy

A server-side dry run validates against the real admission webhooks and mutates
nothing — it is the fastest way to catch a policy or permission problem:

```sh
helm template mu2e-test ./helm/simple -n mu2e-test \
  -f helm/simple/values-test.yaml -f my-values-test.yaml \
  | oc apply --dry-run=server -n mu2e-test -f -
```

### Deploying without a certificate

`--no-cert` requests no `Certificate` and lets the Route fall back to the
router's own wildcard certificate. Not needed for `mu2e-okd-test.fnal.gov`, but
useful when bringing up a namespace that is not yet allowlisted for its name.
Browsers will warn about the name mismatch.

```sh
./scripts/deploy-okd.sh -n mu2e-test --release mu2e-test -t test --no-cert \
  -f helm/simple/values-test.yaml -f my-values-test.yaml
```

To reach an instance whose DNS is not yet in place, bypass the route entirely:

```sh
oc port-forward -n mu2e-test deployment/web 18000:8000
# then browse http://127.0.0.1:18000/
```

### Known issue on a first deploy

`seed_admin` in `app/auth.py` does a check-then-insert against an empty
`users.sqlite`. Every Gunicorn worker runs it at boot, so on a brand-new PVC two
workers can both find no admin and both insert, and the loser dies with
`sqlite3.IntegrityError: UNIQUE constraint failed: users.email`, exiting the
container with code 3. Kubernetes restarts it, the row now exists, every worker
takes the UPDATE branch, and the pod comes up healthy. Expect exactly one restart
on the very first rollout into a fresh namespace.

### Logging in

SSO on the test instance needs its own redirect URI registered with the Fermilab
OIDC provider — `https://mu2e-okd-test.fnal.gov/oidc/callback`, alongside
production's `https://mu2e-shifts.fnal.gov/oidc/callback`. Until that is
registered, the test values file keeps the local administrator form visible
(`auth.showAdminLogin: "1"`) so there is still a way in. Production remains
SSO-only.

### Seeding test data with a copy of production (optional)

The test instance starts with empty PVCs. To populate it from production, scale
production down first so SQLite is not copied mid-write:

```sh
PROD_POD=$(oc get pod -n mu2e-shifts -l app.kubernetes.io/component=web -o name | head -1 | cut -d/ -f2)
TEST_POD=$(oc get pod -n mu2e-test   -l app.kubernetes.io/component=web -o name | head -1 | cut -d/ -f2)

oc scale deployment/web --replicas=0 -n mu2e-shifts   # production outage starts
oc rsync -n mu2e-shifts "${PROD_POD}:/app/data/" ./migrate/data/
oc rsync -n mu2e-shifts "${PROD_POD}:/app/csv/"  ./migrate/csv/
oc scale deployment/web --replicas=1 -n mu2e-shifts   # production outage ends

oc rsync -n mu2e-test ./migrate/data/ "${TEST_POD}:/app/data/"
oc rsync -n mu2e-test ./migrate/csv/  "${TEST_POD}:/app/csv/"
oc rollout restart deployment/web -n mu2e-test
```

Copying `/app/data` brings across `users.sqlite`, so test accounts and admin
state become a snapshot of production. Treat the test instance's access controls
as production-sensitive afterward.

### Tearing the test instance down

```sh
helm uninstall mu2e-test --namespace mu2e-test
```

That also deletes `mu2e-test-data` and `mu2e-test-csv`: both PVCs are
Helm-managed with no `helm.sh/resource-policy: keep`, so the uploaded CSVs and
`users.sqlite` go with the release. Back them up first if they matter:

```sh
POD=$(oc get pod -n mu2e-test -l app.kubernetes.io/component=web -o name | head -1)
oc rsync -n mu2e-test "${POD#pod/}:/app/data/" ./backup-data/
oc rsync -n mu2e-test "${POD#pod/}:/app/csv/"  ./backup-csv/
```

### Sharing mu2e-test with mu2e-talks

`mu2e-test` is also the test namespace for the **mu2e-talks** project, whose
chart names its Deployment, Service, and Route `web` too. Only one of the two
apps can occupy the namespace at a time — Helm will not install over the other,
and the Deployment's label selector is immutable. Check who holds the slot with
`helm list -n mu2e-test`, and uninstall the incumbent (losing its data, per
above) before deploying the other.
