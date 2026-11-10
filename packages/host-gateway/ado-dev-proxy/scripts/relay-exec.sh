#!/bin/sh
set -eu
dotnet=$(wslpath -u "$ADO_PROXY_DOTNET")
exec "$dotnet" "$ADO_PROXY_APP" relay --config "$ADO_PROXY_CONFIG"
