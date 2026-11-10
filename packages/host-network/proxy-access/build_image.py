"""Publish an offline-built coding runtime through the existing private registry."""

import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

from manage import SOURCE, STATE


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def blob(layout, data, media_type):
    digest = hashlib.sha256(data).hexdigest()
    (layout / "blobs/sha256" / digest).write_bytes(data)
    return {"mediaType": media_type, "digest": "sha256:" + digest, "size": len(data)}


def copy_layer(layer, target_path):
    already_compressed = layer.read(2) == b"\x1f\x8b"
    layer.seek(0)
    with target_path.open("wb") as target:
        if already_compressed:
            shutil.copyfileobj(layer, target)
        else:
            with gzip.GzipFile(filename="", mode="wb", fileobj=target, mtime=0, compresslevel=1) as output:
                shutil.copyfileobj(layer, output)
    with tarfile.open(target_path, "r:gz") as validation:
        validation.next()


def main():
    files = [SOURCE / name for name in ("Dockerfile", "gateway.js", "client.js", "actor_probe.js",
                                       "public/gateway-ca.crt", "public/ado-ca.crt")]
    files += [SOURCE / "chart/files" / name for name in ("auth.js", "transport.js", "gateway.js", "client.js", "agency-transport.js")]
    source_digest = hashlib.sha256(b"oci-layer-format-v2" + b"".join(path.read_bytes() for path in files)).hexdigest()
    descriptor_path = STATE / "image.json"
    if descriptor_path.exists():
        previous = json.loads(descriptor_path.read_text())
        if previous.get("sourceDigest") == source_digest:
            sys.path.insert(0, "/mnt/q/tools/kagent-lab")
            from lab_transport import port_forward
            import urllib.request
            with port_forward("kagent", "registry", 15000, 5000):
                url = "http://localhost:15000/v2/kagent-lab/proxy-runtime/manifests/" + previous["digest"]
                request = urllib.request.Request(url, headers={"Accept": "application/vnd.oci.image.manifest.v1+json"})
                with urllib.request.urlopen(request, timeout=10) as response:
                    manifest = response.read()
                if "sha256:" + hashlib.sha256(manifest).hexdigest() != previous["digest"]:
                    raise RuntimeError("Cached private manifest differs.")
            print("Reusing verified unchanged runtime:", previous["digest"])
            return
    subprocess.run(["docker", "build", "--pull=false", "-t", "kagent-proxy-runtime:local", str(SOURCE)], check=True)
    archive = STATE / "runtime.docker.tar"
    subprocess.run(["docker", "save", "-o", str(archive), "kagent-proxy-runtime:local"], check=True)
    layout = STATE / "oci"
    (layout / "blobs/sha256").mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as source:
        manifest = json.load(source.extractfile("manifest.json"))[0]
        config = blob(layout, source.extractfile(manifest["Config"]).read(), "application/vnd.oci.image.config.v1+json")
        layers = []
        for index, name in enumerate(manifest["Layers"]):
            compressed = STATE / f"layer-{index}.gz"
            with source.extractfile(name) as layer:
                copy_layer(layer, compressed)
            digest = sha(compressed)
            size = compressed.stat().st_size
            compressed.replace(layout / "blobs/sha256" / digest)
            layers.append({"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                           "digest": "sha256:" + digest, "size": size})
    image_manifest = blob(layout, json.dumps({
        "schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": config, "layers": layers,
    }, separators=(",", ":")).encode(), "application/vnd.oci.image.manifest.v1+json")
    (layout / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
    (layout / "index.json").write_text(json.dumps({"schemaVersion": 2, "manifests": [image_manifest]}))
    sys.path.insert(0, "/mnt/q/tools/kagent-lab")
    from lab_transport import port_forward
    with port_forward("kagent", "registry", 15000, 5000):
        subprocess.run([
            "python3", "/home/nibeauli/kagent-lab/byo-build/artifacts/push_oci.py",
            "--layout", str(layout), "--registry", "http://localhost:15000",
            "--repository", "kagent-lab/proxy-runtime", "--reference", "0.1.0",
            "--expected-digest", image_manifest["digest"],
        ], check=True, timeout=600)
    image_manifest["sourceDigest"] = source_digest
    (STATE / "image.json").write_text(json.dumps(image_manifest))
    archive.unlink()
    print("Published immutable runtime:", image_manifest["digest"])


if __name__ == "__main__":
    main()
