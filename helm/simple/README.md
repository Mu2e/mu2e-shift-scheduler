# Mu2e Shift Scheduler Helm Chart

This chart deploys the Flask web app to OKD with:

- a single Gunicorn-backed web Deployment
- a ClusterIP Service
- an OKD Route for `mu2e-shifts.fnal.gov`, labeled `ingresscontroller=public-proxy`
- a PVC mounted at `/app/data` for saved preference submissions
- a PVC mounted at `/app/csv` for durable CSV inputs
- cert-manager Certificate support for `mu2e-shifts.fnal.gov`

Create a private values file before deploying:

```yaml
flask:
  secretKey: "<generate-a-long-random-value>"

auth:
  initialAdminUsername: mu2e-admin
  initialAdminEmail: mu2e-admin@fnal.gov
  initialAdminPassword: "<set-a-local-admin-password>"

oidc:
  providerUrl: "https://<fermilab-oidc-provider>/.well-known/openid-configuration"
  clientId: "<oidc-client-id>"
  clientSecret: "<oidc-client-secret>"

preferences:
  shiftsCsv: /app/csv/shifts.csv
```

The app-relative `./csv` path maps to `/app/csv` in the container and is backed
by the `{{ release }}-csv` PVC. Place the production shifts CSV at
`/app/csv/shifts.csv`, or override `preferences.shiftsCsv`.

The SQLite user database lives at `/app/data/users.sqlite` on the data PVC.
Register `https://mu2e-shifts.fnal.gov/oidc/callback` as the OIDC redirect URI.

The "Administrator Login" password form on `/login` is hidden by default
(`auth.showAdminLogin: "0"`), leaving Fermilab SSO as the only advertised login.
Set it to `"1"` to show the form again. Note this only controls what the login
page *displays* — the `POST /login/local` endpoint stays active either way, so
the seeded administrator can still authenticate. Do not hide the form unless
OIDC is configured, or the login page will offer no way in.

The Route carries the label `ingresscontroller: public-proxy` so the cluster's
`public-proxy` IngressController shard admits it. Override with
`--set route.ingressController=<shard>`, or set it to `""` to drop the label and
fall back to the default router.

Install or upgrade:

```sh
helm upgrade --install mu2e-shifts ./helm/simple \
  --namespace mu2e-shifts \
  --values my-values.yaml
```
