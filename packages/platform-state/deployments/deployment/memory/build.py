"""Extend the exact PG18 Alpine base and reuse the private OCI publisher."""
import importlib.util
import json
import sys
import tarfile
from common import PINS, SOURCE, STATE, forward, kube, prepare_state, run
from prepare import digest as artifact_digest


def main():
    prepare_state()
    if artifact_digest(STATE / "build/pgvector.tar.gz") != PINS["pgvectorArchiveSha256"]:
        raise RuntimeError("pinned pgvector archive SHA-256 mismatch")
    image = "host-capabilities-memory-postgres:canary"
    run(["docker", "build", "--pull=false", "-t", image, str(STATE / "build")],
        timeout=1200, name="postgres-build")
    base = json.loads(run(["docker", "image", "inspect", PINS["postgresBase"]]))[0]
    built = json.loads(run(["docker", "image", "inspect", image]))[0]
    for key in ["User", "Entrypoint", "Cmd", "Env", "Volumes", "ExposedPorts"]:
        if base["Config"].get(key) != built["Config"].get(key):
            raise RuntimeError("extended PostgreSQL changed inherited " + key)
    artifact = "/mnt/q/tools/kagent-lab/proxy-access/build_image.py"
    sys.path.insert(0, "/mnt/q/tools/kagent-lab/proxy-access")
    spec = importlib.util.spec_from_file_location("oci_helpers", artifact)
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    archive = STATE / "postgres.docker.tar"
    run(["docker", "save", "-o", str(archive), image], timeout=300, name="docker-save")
    layout = STATE / "oci"
    (layout / "blobs/sha256").mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive) as source:
            manifest = json.load(source.extractfile("manifest.json"))[0]
            config = helpers.blob(layout, source.extractfile(manifest["Config"]).read(),
                                  "application/vnd.oci.image.config.v1+json")
            layers = []
            for index, name in enumerate(manifest["Layers"]):
                path = STATE / ("layer-" + str(index) + ".gz")
                with source.extractfile(name) as stream:
                    helpers.copy_layer(stream, path)
                digest, size = helpers.sha(path), path.stat().st_size
                path.replace(layout / "blobs/sha256" / digest)
                layers.append({"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                               "digest": "sha256:" + digest, "size": size})
        descriptor = helpers.blob(layout, json.dumps({
            "schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": config, "layers": layers}, separators=(",", ":")).encode(),
            "application/vnd.oci.image.manifest.v1+json")
        (layout / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
        (layout / "index.json").write_text(json.dumps({"schemaVersion": 2, "manifests": [descriptor]}))
        with forward("kagent", "registry", 5000) as origin:
            run(["python3", "/home/nibeauli/kagent-lab/byo-build/artifacts/push_oci.py",
                 "--layout", str(layout), "--registry", origin,
                 "--repository", "kagent-lab/memory-postgres", "--reference", "canary",
                 "--timeout", "120", "--expected-digest", descriptor["digest"]],
                timeout=600, name="publish")
        address = json.loads(kube("get", "service", "registry", "-n", "kagent", "-o", "json"))["spec"]["clusterIP"]
        repository = address + ":5000/kagent-lab/memory-postgres"
        (STATE / "image.json").write_text(json.dumps({"repository": repository, **descriptor}))
        spec = importlib.util.spec_from_file_location("private_cache", "/mnt/q/tools/kagent-lab/proxy-access/manage.py")
        cache = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cache)
        cache.STATE = STATE
        values = STATE / "values.json"
        values.write_text(json.dumps({"image": {"repository": repository, "digest": descriptor["digest"]}}))
        cache.cache_images(hosts=())
        print("Published private pinned PG18 canary:", repository + "@" + descriptor["digest"])
    finally:
        archive.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
