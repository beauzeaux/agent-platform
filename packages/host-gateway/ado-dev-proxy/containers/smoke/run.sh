#!/bin/bash
set -euo pipefail
source_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
work=$(mktemp -d /tmp/ado-dev-proxy-smoke.XXXXXXXX)
printf 'Client versions: '; git --version; node --version; npm --version; dotnet --version
if env | grep -Eqi '^(AZURE_CLIENT_SECRET|AZURE_ACCESS_TOKEN|VSS_NUGET_EXTERNAL_FEED_ENDPOINTS|NuGetPackageSourceCredentials_[^=]+|NPM_TOKEN)='; then
    echo "Client credentials must not be present." >&2
    exit 1
fi
mkdir "$work/npm" "$work/shrinkwrap" "$work/nuget"
cp "$source_dir/package.json" "$source_dir/.npmrc" "$work/npm/"
cd "$work/npm"
npm install --ignore-scripts --no-audit --no-fund --cache "$work/npm-cache"
./node_modules/.bin/tsc --version
sha256sum package-lock.json > "$work/lock.before"
npm ci --ignore-scripts --no-audit --no-fund --cache "$work/ci-cache"
./node_modules/.bin/tsc --version
sha256sum package-lock.json > "$work/lock.after"
cmp "$work/lock.before" "$work/lock.after"
node -e 'const l=require("./package-lock.json"); for(const p of Object.values(l.packages)) if(p.resolved && !p.resolved.startsWith("https://pkgs.dev.azure.com/msasg/")) throw Error("Noncanonical resolved URL");'
cp package.json package-lock.json .npmrc "$work/shrinkwrap/"
cd "$work/shrinkwrap"
npm shrinkwrap --ignore-scripts --no-audit --no-fund
sha256sum npm-shrinkwrap.json > "$work/shrinkwrap.before"
npm ci --ignore-scripts --no-audit --no-fund --cache "$work/shrinkwrap-cache"
sha256sum npm-shrinkwrap.json > "$work/shrinkwrap.after"
cmp "$work/shrinkwrap.before" "$work/shrinkwrap.after"
npm pack '@types/node' --ignore-scripts --cache "$work/scoped-cache" --silent
cp "$source_dir/Restore.csproj" "$source_dir/NuGet.Config" "$work/nuget/"
cd "$work/nuget"
dotnet restore Restore.csproj --configfile NuGet.Config --packages "$work/nuget-cache" --verbosity minimal
echo "npm install/ci/shrinkwrap/scoped download and NuGet cold restore passed."
if [[ -n "${ADO_PROXY_GIT_URL:-}" ]]; then
    git clone --depth 1 --filter=blob:none --no-checkout "$ADO_PROXY_GIT_URL" "$work/git"
    [[ "$(git -C "$work/git" remote get-url origin)" == "$ADO_PROXY_GIT_URL" ]]
    git -C "$work/git" fetch origin
    git -C "$work/git" pull --ff-only
    if [[ "${ADO_PROXY_GIT_PUSH:-0}" == 1 ]]; then
        branch="users/ado-dev-proxy/smoke-$(cat /proc/sys/kernel/random/uuid)"
        git -C "$work/git" push origin "HEAD:refs/heads/$branch"
        git -C "$work/git" fetch origin "$branch"
        git -C "$work/git" push origin --delete "$branch"
        [[ -z "$(git -C "$work/git" ls-remote --heads origin "refs/heads/$branch")" ]]
        echo "Disposable-branch Git push and cleanup passed."
    fi
    echo "Git clone/fetch/pull with the unchanged remote passed."
fi
