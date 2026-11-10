import subprocess

from lab_transport import BASE, port_forward

DIGEST = "sha256:9dbe4365efa1657aecbc51faf293512b6695b0a8c2e36bb14942f2194333f9e0"

with port_forward("kagent", "registry", 15000, 5000):
    subprocess.run(
        [
            "python3",
            str(BASE / "byo-build/artifacts/push_oci.py"),
            "--layout", str(BASE / "byo-build/artifacts/oci"),
            "--registry", "http://localhost:15000",
            "--repository", "kagent-lab/deterministic-byo",
            "--reference", "1.0.0-rc1",
            "--expected-digest", DIGEST,
        ],
        check=True,
        timeout=180,
    )
