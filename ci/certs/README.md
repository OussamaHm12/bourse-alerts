# Optional build-time CA certificates

Empty by design, and it must stay that way in git.

## Why this directory exists

The Dockerfile does `COPY ci/certs/ /usr/local/share/ca-certificates/`. That COPY
has to succeed on every builder, so the directory must exist even when there is
nothing to install — hence this file.

## When you need it

Only when building behind a TLS-inspecting proxy (a corporate MITM appliance).
Such a proxy re-signs pub.dev and PyPI with its own CA, which the host trusts and
a container does not, so `flutter pub get` and `pip install` fail with TLS errors.

Drop the CA here and rebuild:

```bash
powershell -ExecutionPolicy Bypass -File scripts/export-corporate-ca.ps1
cp /c/tmp/ca/corporate-ca.crt ci/certs/
docker build -t bourse .
```

`*.crt` and `*.pem` are gitignored here: a CA baked into an image layer is
shipped to everyone who pulls that image.

## Why not a build secret

`--mount=type=secret` is the better mechanism and was used first. Railway's
builder rejects it outright:

```
dockerfile invalid: flag '--mount=type=secret,id=ca,required=false' is missing a
type=cache argument (other mount types are not supported)
```

A directory COPY works on every builder. It costs two things, both worth stating:

1. **this placeholder file**, so the COPY has something to copy; and
2. **a real downside** — with a secret mount the CA never entered any layer,
   whereas a COPY puts it in the image. So a locally-built image *does* contain
   the certificate, where a secret-mounted one did not.

That only affects images built on a machine that has a CA here. Railway builds
from git, `*.crt` is gitignored, so the deployed image never contains one. If you
ever publish a locally-built image, rebuild it with this directory emptied first.

Certificate verification is never disabled anywhere in this build. The container
is taught to trust exactly what its host already trusts, or nothing at all.
