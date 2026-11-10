#!/bin/sh
set -eu
ca=/usr/local/share/ca-certificates/ado-dev-proxy.crt
test -r "$ca" || { echo "Mount only the public proxy CA at $ca" >&2; exit 1; }
if grep -q "PRIVATE KEY" "$ca"; then
    echo "Refusing a CA file containing a private key." >&2
    exit 1
fi
test -n "${HTTPS_PROXY:-}" || { echo "Set HTTPS_PROXY to the container-facing proxy." >&2; exit 1; }
if test -n "${ADO_PROXY_CA_SHA256:-}"; then
    actual=$(openssl x509 -in "$ca" -outform DER | sha256sum | cut -d' ' -f1)
    test "$(printf '%s' "$ADO_PROXY_CA_SHA256" | tr 'A-F' 'a-f')" = "$actual" ||
        { echo "Proxy CA fingerprint mismatch." >&2; exit 1; }
fi
update-ca-certificates >/dev/null
exec "$@"
