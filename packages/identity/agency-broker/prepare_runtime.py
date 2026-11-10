"""Acquire the pinned Linux Agency package using host-only authentication."""
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request
from urllib.parse import urlsplit
import zipfile

VERSION = "2026.10.29.2"
ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "runtime"
RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"


class PackageRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme != "https" or not target.hostname or not (
                target.hostname.endswith(".vsblob.vsassets.io")
                or target.hostname.endswith(".blob.core.windows.net")):
            raise RuntimeError("Unapproved package redirect.")
        return urllib.request.Request(newurl)


def token():
    request = urllib.request.Request(
        "http://127.0.0.1:5443/metadata/identity/oauth2/token?"
        "api-version=2018-02-01&resource=" + RESOURCE, headers={"Metadata": "true"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)["access_token"]
    except Exception:
        result = subprocess.run([
            r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd",
            "account", "get-access-token", "--resource", RESOURCE, "-o", "json"],
            capture_output=True, timeout=45)
        if result.returncode:
            raise RuntimeError("Host ADO authentication unavailable; no token was exported.")
        return json.loads(result.stdout)["accessToken"]


def main():
    TARGET.mkdir(parents=True, exist_ok=True)
    package = TARGET / f"agency.linux-x64.{VERSION}.nupkg"
    if not package.exists():
        url = ("https://pkgs.dev.azure.com/1esgitops/_packaging/"
               "Guardian1ESPTUpstreamOrgFeed/nuget/v3/flat2/agency.linux-x64/"
               f"{VERSION}/agency.linux-x64.{VERSION}.nupkg")
        request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token()})
        temporary = package.with_suffix(".partial")
        try:
            opener = urllib.request.build_opener(PackageRedirect())
            with opener.open(request, timeout=180) as response, temporary.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            temporary.replace(package)
        finally:
            temporary.unlink(missing_ok=True)
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    extracted = TARGET / "linux"
    extracted.mkdir(exist_ok=True)
    with zipfile.ZipFile(package) as archive:
        for entry in archive.infolist():
            destination = (extracted / entry.filename).resolve()
            if not destination.is_relative_to(extracted.resolve()):
                raise RuntimeError("Invalid package path.")
        archive.extractall(extracted)
    binaries = [path for path in extracted.rglob("agency") if path.is_file()]
    if len(binaries) != 1:
        raise RuntimeError("Linux package has no unique Agency executable.")
    (TARGET / "descriptor.json").write_text(json.dumps({
        "version": VERSION, "packageSha256": digest,
        "binary": str(binaries[0].relative_to(ROOT)),
    }, indent=2))
    print("Pinned Linux Agency acquired:", VERSION, digest)


if __name__ == "__main__":
    main()
