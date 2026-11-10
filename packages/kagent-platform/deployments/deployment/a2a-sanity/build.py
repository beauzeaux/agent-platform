"""Offline fixture image packaging with the existing private OCI publisher/cache."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

SOURCE = Path(__file__).resolve().parent
STATE = SOURCE.parent.parent / ".artifacts" / "a2a-sanity"
LAB = Path("/mnt/q/tools/kagent-lab")
DOCKER = ["sudo", "-n", "docker"]
FILES = ["Dockerfile", "entry.mjs", "worker.mjs", "pins.json"]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fingerprint():
    values = {name: (SOURCE / name).read_bytes() for name in FILES}
    values["public-sdk.mjs"] = (SOURCE.parent / "native-canary" / "probe-worker.mjs").read_bytes()
    return hashlib.sha256(b"".join(name.encode() + b"\0" + content
                                  for name, content in sorted(values.items()))).hexdigest()


def build(publish=False):
    STATE.mkdir(parents=True, mode=0o700, exist_ok=True)
    native = load("sanity_native_packaging", SOURCE.parent / "native-canary" / "build.py")
    pinned = json.loads((SOURCE / "pins.json").read_text())["baseManifest"]
    image = json.loads(subprocess.check_output(
        [*DOCKER, "image", "inspect", "host-capabilities-native-public-canary:local"], timeout=30))[0]
    native.verify_base(SOURCE.parent / "native-canary" / ".build" / "oci", pinned, image)
    digest = fingerprint()
    descriptor_file = STATE / "image.json"
    previous = json.loads(descriptor_file.read_text()) if descriptor_file.exists() else {}
    if previous.get("sourceDigest") != digest:
        context = STATE / "context"
        context.mkdir(exist_ok=True)
        for name in FILES:
            shutil.copyfile(SOURCE / name, context / name)
        shutil.copyfile(SOURCE.parent / "native-canary" / "probe-worker.mjs", context / "public-sdk.mjs")
        base = "a2a-sanity-base:" + pinned[7:19]
        subprocess.run([*DOCKER, "tag", image["Id"], base], check=True, timeout=30)
        subprocess.run([*DOCKER, "build", "--pull=false", "--network=none",
                        "--build-arg", "BASE_IMAGE=" + base, "--build-arg", "SOURCE_SHA256=" + digest,
                        "-t", "host-capabilities-a2a-sanity:local", str(context)], check=True, timeout=300)
        sys.path.insert(0, str(LAB / "proxy-access"))
        helper = load("sanity_oci", LAB / "proxy-access" / "build_image.py")
        archive, layout = STATE / "image.tar", STATE / "oci"
        (layout / "blobs/sha256").mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run([*DOCKER, "save", "-o", str(archive), "host-capabilities-a2a-sanity:local"],
                           check=True, timeout=300)
            with tarfile.open(archive) as source:
                manifest = json.load(source.extractfile("manifest.json"))[0]
                config = helper.blob(layout, source.extractfile(manifest["Config"]).read(),
                                     "application/vnd.oci.image.config.v1+json")
                layers = []
                for index, name in enumerate(manifest["Layers"]):
                    compressed = STATE / f"layer-{index}.gz"
                    with source.extractfile(name) as stream:
                        helper.copy_layer(stream, compressed)
                    checksum, size = helper.sha(compressed), compressed.stat().st_size
                    compressed.replace(layout / "blobs/sha256" / checksum)
                    layers.append({"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                                   "digest": "sha256:" + checksum, "size": size})
            previous = helper.blob(layout, json.dumps({"schemaVersion": 2,
                "mediaType": "application/vnd.oci.image.manifest.v1+json", "config": config,
                "layers": layers}, separators=(",", ":")).encode(),
                "application/vnd.oci.image.manifest.v1+json")
            (layout / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
            (layout / "index.json").write_text(json.dumps({"schemaVersion": 2, "manifests": [previous]}))
            previous.update(sourceDigest=digest, baseManifest=pinned)
            descriptor_file.write_text(json.dumps(previous, indent=2))
        finally:
            archive.unlink(missing_ok=True)
    if publish:
        sys.path.insert(0, str(LAB / "proxy-access"))
        publisher = load("sanity_private_publish", LAB / "engsys-coder" / "build.py")
        publisher.STATE, publisher.IMAGE_REPOSITORY, publisher.UPLOAD_TIMEOUT = STATE, "kagent-lab/a2a-sanity", 300
        publisher.publish_and_cache(STATE / "oci", previous)
    return previous


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true")
    print(json.dumps(build(parser.parse_args().publish)))
