"""Download only public pinned artifacts; never submit inference payloads."""
import hashlib
import json
import shutil
import subprocess
from common import PINS, SOURCE, STATE, prepare_state


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            result.update(chunk)
    return result.hexdigest()


def download(url, target, expected=None):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and (expected is None or digest(target) == expected):
        return digest(target)
    partial = target.with_name(target.name + ".partial")
    try:
        subprocess.run(["curl", "--fail", "--silent", "--show-error", "--location",
                        "--proto", "=https", "--proto-redir", "=https",
                        "--connect-timeout", "20", "--max-time", "600",
                        "--output", str(partial), url], check=True, timeout=620)
        actual = digest(partial)
        if expected and actual != expected:
            raise RuntimeError("pinned artifact SHA-256 mismatch")
        partial.replace(target)
        return actual
    finally:
        partial.unlink(missing_ok=True)


def main():
    prepare_state()
    manifest = {}
    context = STATE / "build"
    context.mkdir(exist_ok=True)
    shutil.copyfile(SOURCE / "Dockerfile", context / "Dockerfile")
    manifest["pgvector.tar.gz"] = download(
        "https://codeload.github.com/pgvector/pgvector/tar.gz/" + PINS["pgvectorCommit"],
        context / "pgvector.tar.gz", PINS["pgvectorArchiveSha256"])
    for name, expected in PINS["modelFiles"].items():
        manifest[name] = download("https://huggingface.co/" + PINS["model"] + "/resolve/" +
                                  PINS["modelCommit"] + "/" + name, STATE / "model" / name, expected)
    config = json.loads((STATE / "model/config.json").read_text())
    if config["hidden_size"] != 768:
        raise RuntimeError("model dimension is not the native 768")
    (STATE / "downloads.json").write_text(json.dumps(manifest, indent=2))
    print("Prepared pinned public model/pgvector artifacts; no inference or credential access.")


if __name__ == "__main__":
    main()
