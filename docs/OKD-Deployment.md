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
