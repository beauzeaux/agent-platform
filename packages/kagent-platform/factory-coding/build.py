"""Build private factory binaries/images from prepared, pinned source."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path("/mnt/q/tools/kagent-lab")
SOURCE = ROOT / "factory-coding"
STATE = Path("/home/nibeauli/kagent-lab/factory-coding")
CACHE = Path("/home/nibeauli/kagent-lab/byo-build")


def build():
    binaries_only = "--binaries-only" in sys.argv
    sys.argv = [argument for argument in sys.argv if argument != "--binaries-only"]
    if set(sys.argv[1:]) - {"managed", "controller", "client", "image"}:
        raise RuntimeError("Build targets are managed, controller, client, or image; --binaries-only skips image packaging.")
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.environ["PATH"] = str(CACHE / "toolchain/go/bin") + ":/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    env = dict(os.environ, PATH=str(CACHE / "toolchain/go/bin")+":/usr/bin:/bin",
               GOPATH=str(CACHE / "gopath"), GOMODCACHE=str(CACHE / "gomodcache"),
               GOCACHE=str(CACHE / "gocache"), GOTOOLCHAIN="local", GOPROXY="off")
    for name, files in [
        ("managed", [*ROOT.joinpath("byo-harness").glob("*.go"), *SOURCE.glob("managed*_test.go"),
                     *SOURCE.glob("memory*.go"), *SOURCE.glob("archive*.go")]),
        ("controller", [SOURCE / "controller.go", SOURCE / "controller_test.go", *SOURCE.glob("archive*.go")]),
        ("client", [SOURCE / "session-client.go"]),
    ]:
        directory = STATE / "build" / name
        directory.mkdir(parents=True, exist_ok=True)
        if sys.argv[1:] and name not in sys.argv[1:]:
            continue
        for path in files:
            if name == "managed" and path.name == "main.go":
                continue
            shutil.copyfile(path, directory / path.name)
        if name == "managed":
            shutil.copyfile(SOURCE / "managed-main.go", directory / "main.go")
        for filename in ["go.mod", "go.sum"]:
            shutil.copyfile(ROOT / "byo-harness" / filename, directory / filename)
        subprocess.run(["gofmt", "-w", "."], cwd=directory, env=env, check=True)
        subprocess.run(["go", "mod", "tidy"], cwd=directory, env=env, check=True)
        subprocess.run(["go", "test", "-count=1", "./..."], cwd=directory, env=env, check=True)
        flags = ["-ldflags=-X github.com/kagent-dev/kagent/go/core/internal/version.Version=1.0.0-rc1"] if name == "controller" else []
        subprocess.run(["go", "build", "-trimpath", *flags, "-o", str(STATE / name), "."],
                       cwd=directory, env=dict(env, CGO_ENABLED="0"), check=True)
    if sys.argv[1:] == ["client"] or binaries_only:
        return
    context = STATE / "image-context"
    context.mkdir(exist_ok=True)
    for name in ["managed", "controller"]:
        shutil.copyfile(STATE / name, context / name)
    shutil.copyfile(ROOT / "engsys-agency-coder/agent.mjs", context / "agent.mjs")
    shutil.copyfile(ROOT / "engsys-agency-coder/agency.mjs", context / "agency.mjs")
    for name in ["contribution-tools.mjs","repo-tools.mjs","audit-ui.mjs",
                 "native-config.mjs","session-archive.mjs","native-memory.mjs"]:
        shutil.copyfile(ROOT/"engsys-agency-coder"/name,context/name)
    shutil.copyfile(SOURCE / "Dockerfile", context / "Dockerfile")
    spec = importlib.util.spec_from_file_location("factory_image", ROOT / "engsys-coder/build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Reuse the verified OCI publisher/cache path without changing pilot state.
    module.SOURCE, module.STATE = context, STATE
    module.IMAGE_NAME, module.IMAGE_REPOSITORY = "kagent-factory-coder:local", "kagent-lab/factory-coder"
    module.FILES = ["Dockerfile","managed","controller","agent.mjs","agency.mjs",
                    "contribution-tools.mjs","repo-tools.mjs","audit-ui.mjs",
                    "native-config.mjs","session-archive.mjs","native-memory.mjs"]
    module.UPLOAD_TIMEOUT = 300
    cache_images = module.proxy_manage.cache_images
    nodes = json.loads(subprocess.check_output(["/snap/bin/microk8s", "kubectl", "get", "nodes", "-o", "json"], timeout=30))
    hosts = []
    for node in nodes["items"]:
        if not any(condition["type"] == "Ready" and condition["status"] == "True" for condition in node["status"]["conditions"]):
            continue
        hosts.extend(address["address"] for address in node["status"]["addresses"]
                     if address["type"] == "InternalIP" and address["address"] in {"10.82.0.3", "10.82.0.4"})
    module.proxy_manage.cache_images = lambda: cache_images(hosts=tuple(sorted(hosts)))
    (STATE / "image-cache-targets.json").write_text(json.dumps({"readyRemoteHosts": sorted(hosts)}))
    for filename in ["package.json", "package-lock.json"]:
        shutil.copyfile(ROOT / "engsys-agency-coder" / filename, context / filename)
    module.main()


if __name__ == "__main__":
    build()
