"""Verify one scoped engsys pull request without exporting host credentials."""
import argparse
import json
import urllib.parse
import urllib.request

from broker import PROJECT, REPOSITORY_ID, watch_scope
from prepare_runtime import token


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("pull_request")
    args=parser.parse_args()
    repository={"organization":"msasg","project":PROJECT,"name":"engsys"}
    watch_scope("finish_pull_request",{"pull_request":args.pull_request},repository)
    segments=[urllib.parse.unquote(part) for part in urllib.parse.urlparse(args.pull_request).path.split("/") if part]
    identifier=int(segments[-1])
    url=("https://dev.azure.com/msasg/"+urllib.parse.quote(PROJECT,safe="")+
        "/_apis/git/repositories/"+REPOSITORY_ID+"/pullRequests/"+str(identifier)+"?api-version=7.1")
    request=urllib.request.Request(url,headers={"Authorization":"Bearer "+token()})
    with urllib.request.urlopen(request,timeout=30) as response:
        value=json.load(response)
    print(json.dumps({"id":value["pullRequestId"],"status":value["status"],
        "sourceRefName":value["sourceRefName"],"targetRefName":value["targetRefName"],
        "lastMergeSourceCommit":value.get("lastMergeSourceCommit",{}).get("commitId"),
        "lastMergeCommit":value.get("lastMergeCommit",{}).get("commitId")}))


if __name__=="__main__":
    main()
