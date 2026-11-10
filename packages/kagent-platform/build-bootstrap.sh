#!/usr/bin/env bash
set -euo pipefail
base=/home/nibeauli/kagent-lab
source_dir=/mnt/q/tools/kagent-lab/bootstrap-platform
build_dir="$base/bootstrap-build"
test -x "$base/byo-build/toolchain/go/bin/go"
test "$(git -C "$base/source/kagent" rev-parse HEAD)" = eed27237a715fdbc7664aab9e1089c9a05097e71
test "$(git -C "$base/source/substrate" rev-parse HEAD)" = 71b1f9ec80848f8fd46f5d5d7e6b84ba36a80966
mkdir -p "$build_dir"
cp "$source_dir/main.go" "$source_dir/go.mod" "$source_dir/go.sum" "$build_dir/"
sed -i 's/\r$//' "$build_dir/main.go" "$build_dir/go.mod" "$build_dir/go.sum"
cd "$build_dir"
export GOTOOLCHAIN=local
export GOPROXY=off
"$base/byo-build/toolchain/go/bin/go" build -mod=readonly -trimpath -o "$base/artifacts/bootstrap-platform" .
