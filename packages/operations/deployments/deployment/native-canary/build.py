"""Package only the public probe using a verified cached factory base and private OCI publisher."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tarfile

SOURCE = Path(__file__).resolve().parent
STATE = SOURCE / ".build"
FACTORY_OCI = Path("/home/nibeauli/kagent-lab/factory-coding/oci")
ROOT = Path("/mnt/q/tools/kagent-lab")
FILES = ["Dockerfile", ".dockerignore", "public-probe.mjs", "probe-worker.mjs", "pins.json"]
DOCKER = ["sudo", "-n", "docker"]


def source_hash(source=SOURCE):
    digest = hashlib.sha256()
    for name in FILES:
        digest.update(name.encode() + b"\0" + (source / name).read_bytes())
    return digest.hexdigest()


def verify_base(layout, pinned, image=None):
    manifest_path = layout / "blobs/sha256" / pinned.removeprefix("sha256:")
    raw = manifest_path.read_bytes()
    if "sha256:" + hashlib.sha256(raw).hexdigest() != pinned:
        raise ValueError("Pinned factory OCI manifest differs.")
    manifest = json.loads(raw)
    config = manifest["config"]["digest"]
    config_raw = (layout / "blobs/sha256" / config.removeprefix("sha256:")).read_bytes()
    expected = json.loads(config_raw)
    matches = image is None or (
        image.get("Architecture") == expected.get("architecture")
        and image.get("Os") == expected.get("os")
        and image.get("RootFS", {}).get("Layers") == expected["rootfs"]["diff_ids"]
        and all(image.get("Config", {}).get(key) == value for key, value in expected.get("config", {}).items())
        and all(key in expected.get("config", {}) or value in (None, "", False, [], {})
                for key, value in image.get("Config", {}).items())
    )
    if "sha256:" + hashlib.sha256(config_raw).hexdigest() != config or not matches:
        raise ValueError("Local Docker factory image does not match the pinned published config/rootfs identity.")
    return config


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true", help="Publish/cache only the new private canary image.")
    args = parser.parse_args()
    pins = json.loads((SOURCE / "pins.json").read_text())
    STATE.mkdir(mode=0o700, exist_ok=True)
    cached_id = verify_base(FACTORY_OCI, pins["baseManifest"])
    image = json.loads(subprocess.check_output([*DOCKER, "image", "inspect", "kagent-factory-coder:local"], timeout=30))[0]
    config = verify_base(FACTORY_OCI, pins["baseManifest"], image)
    fingerprint = source_hash()
    base_tag = "host-capabilities-native-canary-base:" + pins["baseManifest"][7:19]
    target = "host-capabilities-native-public-canary:local"
    subprocess.run([*DOCKER, "tag", image["Id"], base_tag], check=True, timeout=30)
    subprocess.run([*DOCKER, "build", "--pull=false", "--network=none",
                    "--build-arg", "BASE_IMAGE=" + base_tag,
                    "--build-arg", "BASE_MANIFEST=" + pins["baseManifest"],
                    "--build-arg", "SOURCE_SHA256=" + fingerprint,
                    "-t", target, str(SOURCE)], check=True, timeout=300)
    built = json.loads(subprocess.check_output([*DOCKER, "image", "inspect", target], timeout=30))[0]
    if source_hash() != fingerprint or built["Config"]["Labels"]["io.host-capabilities.source-sha256"] != fingerprint:
        raise ValueError("Public probe source label differs.")
    (STATE / "source.json").write_text(json.dumps({"baseManifest": pins["baseManifest"], "baseConfig": config,
        "sourceSha256": fingerprint, "imageId": built["Id"],
        "files": {name: hashlib.sha256((SOURCE / name).read_bytes()).hexdigest() for name in FILES}}, indent=2))
    sys.path.insert(0, str(ROOT / "proxy-access"))
    helpers = load("public_canary_oci_helpers", ROOT / "proxy-access/build_image.py")
    archive = STATE / "public-canary.docker.tar"
    layout = STATE / "oci"
    (layout / "blobs/sha256").mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run([*DOCKER, "save", "-o", str(archive), target], check=True, timeout=300)
        with tarfile.open(archive) as source:
            manifest = json.load(source.extractfile("manifest.json"))[0]
            config_descriptor = helpers.blob(layout, source.extractfile(manifest["Config"]).read(),
                                             "application/vnd.oci.image.config.v1+json")
            layers = []
            for index, name in enumerate(manifest["Layers"]):
                compressed = STATE / f"layer-{index}.gz"
                with source.extractfile(name) as stream:
                    helpers.copy_layer(stream, compressed)
                digest, size = helpers.sha(compressed), compressed.stat().st_size
                compressed.replace(layout / "blobs/sha256" / digest)
                layers.append({"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                               "digest": "sha256:" + digest, "size": size})
        descriptor = helpers.blob(layout, json.dumps({"schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json", "config": config_descriptor,
            "layers": layers}, separators=(",", ":")).encode(), "application/vnd.oci.image.manifest.v1+json")
        (layout / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
        (layout / "index.json").write_text(json.dumps({"schemaVersion": 2, "manifests": [descriptor]}))
        descriptor.update(sourceDigest=fingerprint, baseManifest=pins["baseManifest"])
        (STATE / "image.json").write_text(json.dumps(descriptor, indent=2))
        if args.publish:
            publisher = load("public_canary_private_publisher", ROOT / "engsys-coder/build.py")
            publisher.STATE = STATE
            publisher.IMAGE_REPOSITORY = "kagent-lab/native-public-canary"
            publisher.UPLOAD_TIMEOUT = 300
            cache = publisher.proxy_manage.cache_images
            publisher.proxy_manage.cache_images = lambda: cache(hosts=())
            publisher.publish_and_cache(layout, descriptor)
        print("Public probe OCI digest:", descriptor["digest"])
        print("Public probe source SHA-256:", fingerprint)
    finally:
        archive.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
