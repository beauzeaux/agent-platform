"""Scoped factory deployment; never replace the existing coding pilots."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import ssl
import subprocess
import sys
import time
import urllib.request

import yaml
from backend import ENV, KUBE, ROOT, SPACE, kube
from gateway_store import Store
from otel_exporter import hex_id
from swarm_roles import ROLES, gateway_config

STATE = Path("/home/nibeauli/kagent-lab/factory-coding")
SOURCE = ROOT / "factory-coding"
OWNER = "factory-coding"


def write(path, value):
    path.write_text(value)
    path.chmod(0o600)


def owned_apply(document):
    metadata = document["metadata"]
    namespace = metadata.get("namespace")
    arguments = ["get", document["kind"], metadata["name"], "-o", "json"]
    if namespace:
        arguments += ["-n",namespace]
    existing = kube(*arguments, optional=True)
    if existing and existing["metadata"].get("labels",{}).get("app.kubernetes.io/managed-by") != OWNER:
        raise RuntimeError("refusing to replace unowned "+document["kind"]+"/"+metadata["name"])
    metadata.setdefault("labels",{})["app.kubernetes.io/managed-by"] = OWNER
    return kube("apply","--server-side","--field-manager=factory-coding","-f","-","-o","json",body=document)


def prepare():
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE.chmod(0o700)
    for filename in ["controller-token","operator-token","other-token","swarm-token",
                     "owner-password","readwrite-password"]:
        path = STATE / filename
        if not path.exists():
            write(path, secrets.token_urlsafe(40))
    proxy = Path("/home/nibeauli/kagent-lab/proxy-access/certificates")
    certificate, key = STATE / "service.crt", STATE / "service.key"
    if not certificate.exists():
        write(STATE / "certificate.ext", "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=IP:10.82.0.2,DNS:factory-coding.internal\n")
        subprocess.run(["openssl","req","-new","-newkey","rsa:3072","-nodes",
            "-keyout",str(key),"-out",str(STATE/"service.csr"),"-subj","/CN=factory-coding.internal"],
            check=True,capture_output=True)
        subprocess.run(["openssl","x509","-req","-in",str(STATE/"service.csr"),
            "-CA",str(proxy/"ca.crt"),"-CAkey",str(proxy/"ca.key"),"-CAcreateserial",
            "-out",str(certificate),"-days","90","-extfile",str(STATE/"certificate.ext")],
            check=True,capture_output=True)
        key.chmod(0o600)
        (STATE/"service.csr").unlink()
        (STATE/"certificate.ext").unlink()
    subprocess.run(["openssl","x509","-checkend","86400","-noout","-in",str(certificate)],check=True,capture_output=True)
    shutil.copyfile(proxy/"ca.crt",STATE/"ca.crt")
    for namespace in [SPACE]:
        owned_apply({"apiVersion":"v1","kind":"Namespace","metadata":{"name":namespace,
            "labels":{"ate.dev/api-access":"true"}}})
    runtime_space()
    print("Prepared protected factory credentials and identity scope.")


def runtime_space():
    command = ["/home/nibeauli/kagent-lab/artifacts/kubectl-ate","--preview"]
    marker = STATE/"space-owned.json"
    current = subprocess.run([*command,"get","atespace",SPACE,"-o","json"],env=ENV,capture_output=True,timeout=120)
    if current.returncode:
        if b"NotFound" not in current.stderr:
            raise RuntimeError("cannot inspect isolated runtime space")
        current = subprocess.run([*command,"create","atespace",SPACE,"-o","json"],env=ENV,capture_output=True,check=True,timeout=120)
        write(marker,current.stdout.decode())
    elif not marker.exists():
        raise RuntimeError("refusing to adopt an unowned runtime space")
    elif json.loads(current.stdout)["metadata"]["uid"] != json.loads(marker.read_text())["metadata"]["uid"]:
        raise RuntimeError("runtime space ownership changed")
    policy = {"bindings":[{"role":"owner","members":[
        "user:spiffe://cluster.local/ns/factory-executions/sa/factory-controller",
        "user:system:serviceaccount:ate-system:ate-client"]}]}
    result = subprocess.run([*command,"get","access-policy","-a",SPACE,"-o","json"],env=ENV,capture_output=True,check=True,timeout=120)
    if not result.stdout.strip():
        subprocess.run([*command,"create","access-policy","-a",SPACE,"-f","-"],
            env=ENV,input=json.dumps(policy).encode(),capture_output=True,check=True,timeout=120)
    else:
        observed = json.loads(result.stdout)
        if observed.get("bindings") != policy["bindings"]:
            old = json.loads(json.dumps(policy).replace("spiffe://","spiffe%3A//"))
            if observed.get("bindings") != old["bindings"]:
                raise RuntimeError("runtime access policy differs; require explicit review")
            policy["metadata"] = observed["metadata"]
            subprocess.run([*command,"update","access-policy","-a",SPACE,"-f","-"],
                env=ENV,input=json.dumps(policy).encode(),capture_output=True,check=True,timeout=120)


def database():
    script = Path("/home/nibeauli/kagent-lab/source/kagent/go/core/pkg/postgressetup/setup.sql").read_text()
    variables = {"kagent_schema":"factory", "kagent_owner_role":"factory_owner",
        "kagent_owner_user":"factory_owner_user", "kagent_owner_password":(STATE/"owner-password").read_text(),
        "kagent_readwrite_role":"factory_readwrite", "kagent_readwrite_user":"factory_readwrite_user",
        "kagent_readwrite_password":(STATE/"readwrite-password").read_text()}
    command = [*KUBE,"exec","-i","-n","kagent","deployment/kagent-postgresql","-c","postgresql",
        "--","psql","--no-psqlrc","--set=ON_ERROR_STOP=1","--single-transaction",
        "--username","postgres","--dbname","kagent"]
    secret_names = {"kagent_owner_password","kagent_readwrite_password"}
    command += ["--set="+key+"="+value for key,value in variables.items() if key not in secret_names]
    secret_input = "".join("\\set "+key+" '"+variables[key]+"'\n" for key in sorted(secret_names))
    gateway = """
SET ROLE :"kagent_owner_role";
SET search_path TO :"kagent_schema";
CREATE TABLE IF NOT EXISTS gateway_tasks (
    gateway text NOT NULL DEFAULT 'coding',
    id text PRIMARY KEY,
    owner text NOT NULL,
    request_id text NOT NULL,
    context_id text NOT NULL,
    digest text NOT NULL,
    request jsonb NOT NULL,
    state text NOT NULL CHECK (state IN ('queued','dispatching','active','uncertain','completed','failed','canceled')),
    session text,
    actor_uid text,
    native_task text,
    attempted boolean NOT NULL DEFAULT false,
    cancel_requested boolean NOT NULL DEFAULT false,
    result jsonb,
    diagnostic text,
    available_at timestamptz NOT NULL DEFAULT now(),
    created timestamptz NOT NULL DEFAULT now(),
    updated timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE gateway_tasks ADD COLUMN IF NOT EXISTS gateway text NOT NULL DEFAULT 'coding';
ALTER TABLE gateway_tasks DROP CONSTRAINT IF EXISTS gateway_tasks_owner_request_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS gateway_tasks_request
    ON gateway_tasks(gateway,owner,request_id);
DROP INDEX IF EXISTS gateway_tasks_schedule;
CREATE INDEX gateway_tasks_schedule
    ON gateway_tasks(gateway,state,available_at,created);
DROP INDEX IF EXISTS gateway_tasks_context;
CREATE INDEX gateway_tasks_context
    ON gateway_tasks(gateway,owner,context_id,created);
GRANT SELECT,INSERT,UPDATE,DELETE ON gateway_tasks TO :"kagent_readwrite_role";
RESET ROLE;
"""
    result = subprocess.run(command,env=ENV,input=(secret_input+script+gateway).encode(),capture_output=True,timeout=120)
    if result.returncode:
        raise RuntimeError("factory database identity setup failed")
    config = kube("get","configmap","kagent-postgresql","-n","kagent","-o","json")
    rule = "hostssl kagent factory_owner_user,factory_readwrite_user all scram-sha-256\n"
    if rule not in config["data"]["pg_hba.conf"]:
        config["data"]["pg_hba.conf"] += rule
        kube("replace","-f","-","-o","json",body=config)
    for name,user,password,key in [
        ("factory-postgres-owner","factory_owner_user","owner-password","ownerConnectionString"),
        ("factory-postgres-readwrite","factory_readwrite_user","readwrite-password","readWriteConnectionString")]:
        url = "postgresql://"+user+":"+(STATE/password).read_text()+"@kagent-postgresql.kagent.svc:5432/kagent?sslmode=verify-full&sslrootcert=/run/substrate-servicedns/trust-bundle.pem&channel_binding=disable"
        owned_apply({"apiVersion":"v1","kind":"Secret","metadata":{"name":name,"namespace":SPACE},
            "stringData":{key:url}})
    print("Prepared isolated factory schema/logins in the existing TLS database.")


def router_access():
    sys.path.insert(0,str(ROOT/"bin"))
    from post_render import allow_factory_router
    document = kube("get","configmap","atenet-router-agentgateway-config","-n","ate-system","-o","json")
    config = yaml.safe_load(document["data"]["config.yaml"])
    changed = allow_factory_router(config)
    if changed:
        kube("patch","configmap","atenet-router-agentgateway-config","-n","ate-system",
            "--type=json","-p",json.dumps([
                {"op":"test","path":"/metadata/resourceVersion","value":document["metadata"]["resourceVersion"]},
                {"op":"replace","path":"/data/config.yaml","value":yaml.safe_dump(config,sort_keys=False)}
            ]),"-o","json")
    write(STATE/"router-owned.json",json.dumps({
        "subject":"spiffe://cluster.local/ns/factory-executions/sa/factory-controller"}))
    if changed:
        subprocess.run([*KUBE,"rollout","restart","deployment/atenet-router","-n","ate-system"],env=ENV,check=True)
    subprocess.run([*KUBE,"rollout","status","deployment/atenet-router","-n","ate-system","--timeout=180s"],
        env=ENV,check=True,timeout=210)
    print("Exact factory controller router identity admitted; existing TLS/policies retained.")


def control_plane():
    prepare()
    router_access()
    database()
    descriptor = json.loads((STATE/"image.json").read_text())
    registry = kube("get","service","registry","-n","kagent","-o","json")["spec"]["clusterIP"]+":5000"
    image = registry+"/kagent-lab/factory-coder@"+descriptor["digest"]
    owned_apply({"apiVersion":"v1","kind":"Secret","metadata":{"name":"factory-controller-auth","namespace":SPACE},
        "stringData":{"controller-token":(STATE/"controller-token").read_text()}})
    previous_values = STATE / "controller-values.json"
    watched = (json.loads(previous_values.read_text()).get("global", {}).get("watchNamespaces", [SPACE])
               if previous_values.exists() else [SPACE])
    if (not isinstance(watched, list) or SPACE not in watched
            or any(not isinstance(name, str) or not name or name == "*" for name in watched)):
        raise RuntimeError("refusing to replace an invalid explicit controller watch list")
    values = {
        "fullnameOverride":"factory","global":{"watchNamespaces":list(dict.fromkeys(watched))},
        "controller":{"replicas":1,"substrateClientCertificate":{"enabled":True},
            "volumes":[{"name":"factory-auth","secret":{"secretName":"factory-controller-auth","defaultMode":292}}],
            "volumeMounts":[{"name":"factory-auth","mountPath":"/run/factory","readOnly":True}],
            "resources":{"requests":{"cpu":"100m","memory":"256Mi"},"limits":{"cpu":"2","memory":"1Gi"}}},
        "ui":{"replicas":1},
        "config":{"server":{"auth":{"mode":"insecure"},"grpcReflection":False},
            "database":{"schema":"factory","owner":{"role":"factory_owner"},"readWrite":{"role":"factory_readwrite"}},
            "runtimeDefaults":{"cpu":"1","memory":"2Gi"}},
        "database":{"postgres":{"ownerConnectionStringSecretRef":{"name":"factory-postgres-owner","key":"ownerConnectionString"},
            "readWriteConnectionStringSecretRef":{"name":"factory-postgres-readwrite","key":"readWriteConnectionString"},
            "clientCertificate":{"enabled":False}}},
        "substrateWorkerPool":{"create":True,"replicas":2,"workerImage":"ghcr.io/kagent-dev/substrate/ateworker-gvisor:v1.0.0-rc1",
            "sandboxClass":"gvisor","template":{"resources":{"requests":{"cpu":"1","memory":"2Gi"},
                "limits":{"cpu":"4","memory":"8Gi"}}}},
        "kagent-tools":{"enabled":False},"providers":None,
    }
    write(STATE/"controller-values.json",json.dumps(values))
    rendered = subprocess.run(["/snap/bin/microk8s","helm3","template","factory",
        "/home/nibeauli/kagent-lab/artifacts/kagent-1.0.0-rc1.tgz","-n",SPACE,"-f",str(STATE/"controller-values.json")],
        env=ENV,capture_output=True,check=True,timeout=120).stdout
    documents = list(yaml.safe_load_all(rendered))
    for document in documents:
        if not document:
            continue
        if document["kind"] == "Deployment" and document["metadata"]["name"] == "factory-controller":
            document["spec"]["template"]["spec"]["containers"][0]["image"] = image
            document["spec"]["template"]["spec"]["containers"][0]["imagePullPolicy"] = "Never"
            document["spec"]["template"]["spec"]["containers"][0].setdefault("env",[]).extend(
                {"name":name,"value":""} for name in ["HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy"])
            document["spec"]["template"]["metadata"].setdefault("annotations",{})["factory.copilot/database"] = hashlib.sha256(
                (STATE/"owner-password").read_bytes()+(STATE/"readwrite-password").read_bytes()+b"verify-full-scram-v1").hexdigest()
        owned_apply(document)
    subprocess.run([*KUBE,"rollout","status","deployment/factory-controller","-n",SPACE,"--timeout=300s"],
        env=ENV,check=True)
    owned_apply({"apiVersion":"api.kagent.dev/v1alpha3","kind":"Harness",
        "metadata":{"name":"factory-coding","namespace":SPACE},
        "spec":{"byo":{},"workload":{"image":"localhost:15000/kagent-lab/factory-coder@"+descriptor["digest"],
            "command":["/app/harness"]},
            "substrate":{"workerPoolRef":{"name":"kagent-default"},"snapshotPolicy":{"location":"gs://ate-snapshots/factory-coding/"}}}})
    owned_apply({"apiVersion":"api.kagent.dev/v1alpha3","kind":"AgentTemplate",
        "metadata":{"name":"factory-coding","namespace":SPACE},
        "spec":{"description":"Isolated host-backed Agency contributor","systemPrompt":"Execute the approved structured coding request."}})
    owned_apply({"apiVersion":"api.kagent.dev/v1alpha3","kind":"Agent",
        "metadata":{"name":"factory-coder","namespace":SPACE},
        "spec":{"harnessRef":{"name":"factory-coding"},"template":{"ref":{"name":"factory-coding"}}}})
    for name,role in ROLES.items():
        if name == "coding":
            continue
        owned_apply({"apiVersion":"api.kagent.dev/v1alpha3","kind":"AgentTemplate",
            "metadata":{"name":role["backendAgent"],"namespace":SPACE},
            "spec":{"description":role["description"],"systemPrompt":role["instruction"]}})
        owned_apply({"apiVersion":"api.kagent.dev/v1alpha3","kind":"Agent",
            "metadata":{"name":role["backendAgent"],"namespace":SPACE},
            "spec":{"harnessRef":{"name":"factory-coding"},
                    "template":{"ref":{"name":role["backendAgent"]}}}})
    write(STATE/"runtime.json",json.dumps({"image":image,"digest":descriptor["digest"]}))
    print("Dedicated authenticated controller deployed; original Kagent fixture unchanged.")


def proxy_bindings():
    sys.path.insert(0,str(ROOT/"proxy-access"))
    from test_actors import forward, request
    pilots = []
    for name,port in [("engsys-coder",18095),("engsys-agency-coder",18096)]:
        observed = subprocess.run(["/home/nibeauli/kagent-lab/artifacts/kubectl-ate","--preview","get","actor",name,
            "-a","proxy-access","-o","json"],env=ENV,capture_output=True,check=True,timeout=120)
        actor = json.loads(observed.stdout)
        node = kube("get","node",actor["status"]["assignedNode"],"-o","json")
        ready = any(condition["type"] == "Ready" and condition["status"] == "True" for condition in node["status"]["conditions"])
        pilots.append({"name":name,"uid":actor["metadata"]["uid"],"node":node["metadata"]["name"],"nodeReady":ready})
        if not ready:
            print("Preserving unreachable original pilot on its pre-existing NotReady node: "+name,flush=True)
            continue
        with forward(name,port) as origin:
            if request(origin,"/status").get("activeTask"):
                raise RuntimeError("existing coding pilot is active; defer shared gateway rollout")
    write(STATE/"preserved-pilots.json",json.dumps(pilots))
    bindings = json.loads((ROOT/"agency-broker/public/actors.json").read_text())
    owned_apply({"apiVersion":"v1","kind":"ConfigMap","metadata":{"name":"factory-agency-bindings","namespace":"proxy-access"},
                 "data":{"actors.json":json.dumps(bindings)}})
    source = Path("/home/nibeauli/kagent-lab/proxy-access/values.json")
    values = json.loads(source.read_text())
    values.setdefault("broker",{})["dynamicActorsFile"] = "/app/agency-bindings/actors.json"
    values["broker"]["dynamicActorsConfigMap"] = "factory-agency-bindings"
    values["auth"]["allowedAtespaces"] = sorted(set(values["auth"]["allowedAtespaces"])|{SPACE})
    write(STATE/"proxy-values.json",json.dumps(values))
    subprocess.run(["/snap/bin/microk8s","helm3","upgrade","agent-proxies",str(ROOT/"proxy-access/chart"),
        "-n","proxy-access","-f",str(STATE/"proxy-values.json"),"--wait","--timeout","180s"],
        env=ENV,check=True,timeout=210)
    # One version activation restart, never one restart per new coding actor.
    activation = hashlib.sha256((ROOT/"agency-broker/broker.py").read_bytes()+
        (ROOT/"proxy-access/chart/files/agency-transport.js").read_bytes()).hexdigest()
    marker = STATE/"gateway-activation"
    if not marker.exists() or marker.read_text() != activation:
        subprocess.run(["/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe","-NoProfile","-NonInteractive",
            "-Command","$ErrorActionPreference='Stop'; Stop-ScheduledTask -TaskName 'Kagent Agency Broker'; Start-Sleep -Seconds 2; Start-ScheduledTask -TaskName 'Kagent Agency Broker'; for($i=0;$i -lt 30;$i++){try{if((Invoke-RestMethod http://127.0.0.1:15442/health -TimeoutSec 2).ready){exit 0}}catch{};Start-Sleep -Seconds 1};throw 'Broker readiness failed'"],
            env=dict(ENV,WSL_INTEROP="/run/WSL/1_interop"),check=True,capture_output=True,timeout=90)
        write(marker,activation)
    print("Activated dynamic identity bindings while retaining one shared gateway.")


def observability():
    helm=["/snap/bin/microk8s","helm3"]
    subprocess.run([*helm,"repo","add","grafana",
        "https://grafana.github.io/helm-charts","--force-update"],
        check=True,capture_output=True,timeout=120)
    subprocess.run([*helm,"repo","add","prometheus-community",
        "https://prometheus-community.github.io/helm-charts","--force-update"],
        check=True,capture_output=True,timeout=120)
    subprocess.run([*helm,"repo","update"],check=True,capture_output=True,timeout=180)
    subprocess.run([*helm,"upgrade","tempo","grafana/tempo","--version","1.24.1",
        "-n","observability","--reuse-values","-f",str(SOURCE/"tempo-values.yaml"),
        "--wait","--timeout","300s"],check=True,timeout=360)
    subprocess.run([*helm,"upgrade","kube-prom-stack",
        "prometheus-community/kube-prometheus-stack","--version","77.6.2",
        "-n","observability","--reuse-values","-f",str(SOURCE/"observability-values.yaml"),
        "--wait","--timeout","300s"],check=True,timeout=360)
    old=kube("get","configmap","slopmop-grafana-datasources",
        "-n","observability","-o","json",optional=True)
    if old:
        if old["metadata"].get("labels",{}).get("app.kubernetes.io/managed-by")!=OWNER:
            raise RuntimeError("refusing to delete unowned Grafana datasource ConfigMap")
        subprocess.run([*KUBE,"delete","configmap","slopmop-grafana-datasources",
            "-n","observability","--ignore-not-found=true"],env=ENV,check=True,
            capture_output=True,timeout=60)
    latest_trace=""
    manager_path=STATE/"service-swarm-manager.json"
    if manager_path.exists():
        manager_config=json.loads(manager_path.read_text())
        manager_store=Store(manager_config["postgres"],1,20,"swarm-manager","campaign")
        campaigns=manager_store.list()
        manager_store.close()
        if campaigns:
            latest_trace=hex_id(campaigns[-1]["id"],length=32)
    dashboard={"id":None,"uid":"slopmop-swarm","title":"SlopMop Autonomous Swarm",
        "tags":["slopmop","swarm","a2a"],"timezone":"browser","schemaVersion":39,
        "version":1,"refresh":"10s","time":{"from":"now-6h","to":"now"},
        "templating":{"list":[{"name":"traceId","label":"Campaign trace ID",
            "type":"textbox","query":latest_trace,
            "current":{"text":latest_trace,"value":latest_trace}}]},"panels":[
            {"id":1,"type":"text","title":"Swarm telemetry","gridPos":{"h":4,"w":24,"x":0,"y":0},
             "options":{"mode":"markdown","content":
                "Campaign and child lifecycle spans use `service.name=slopmop-swarm-manager`. "
                "Attributes include campaign, stage, role, task, context, and outcome; prompts and tool payloads are excluded."}},
            {"id":2,"type":"table","title":"Campaign traces (open a row for child spans)",
             "datasource":{"type":"tempo","uid":"tempo"},"gridPos":{"h":8,"w":24,"x":0,"y":4},
             "targets":[{"refId":"A","queryType":"traceql",
                 "query":"{ resource.service.name = \"slopmop-swarm-manager\" }",
                 "tableType":"traces","limit":100}]},
            {"id":5,"type":"traces","title":"Selected campaign child-span waterfall",
             "datasource":{"type":"tempo","uid":"tempo"},"gridPos":{"h":12,"w":24,"x":0,"y":12},
             "targets":[{"refId":"A","queryType":"traceId","query":"$traceId"}]},
            {"id":3,"type":"logs","title":"Kagent, Substrate, and actor infrastructure logs",
             "datasource":{"type":"loki","uid":"loki"},"gridPos":{"h":10,"w":16,"x":0,"y":24},
             "targets":[{"refId":"A","expr":
                 "{namespace=~\"factory-executions|ate-system|kagent\"}"}],
             "options":{"showTime":True,"wrapLogMessage":True,"prettifyLogMessage":False}},
            {"id":4,"type":"timeseries","title":"Observed control-plane targets",
             "datasource":{"type":"prometheus","uid":"prometheus"},
             "gridPos":{"h":10,"w":8,"x":16,"y":24},
             "targets":[{"refId":"A","expr":
                 "up{namespace=~\"factory-executions|ate-system|kagent\"}"}]},
        ]}
    owned_apply({"apiVersion":"v1","kind":"ConfigMap",
        "metadata":{"name":"slopmop-swarm-dashboard","namespace":"observability",
            "labels":{"grafana_dashboard":"1"}},
        "data":{"slopmop-swarm.json":json.dumps(dashboard)}})
    unit=Path("/etc/systemd/system/slopmop-grafana-host.service")
    if unit.exists() and "SlopMop Grafana host access" not in unit.read_text():
        raise RuntimeError("unowned service unit slopmop-grafana-host.service")
    unit.write_text("[Unit]\nDescription=SlopMop Grafana host access\n"
        "After=snap.microk8s.daemon-kubelite.service\n"
        "[Service]\nType=simple\n"
        "Environment=KUBECONFIG=/var/snap/microk8s/current/credentials/client.config\n"
        "ExecStart=/snap/microk8s/current/kubectl port-forward -n observability --address 127.0.0.1 "
        "service/kube-prom-stack-grafana 13000:80\nRestart=always\nRestartSec=5\nKillMode=control-group\n"
        "[Install]\nWantedBy=multi-user.target\n")
    unit.chmod(0o644)
    subprocess.run(["systemctl","daemon-reload"],check=True)
    subprocess.run(["systemctl","enable","--now","slopmop-grafana-host.service"],check=True)
    for _ in range(30):
        try:
            with urllib.request.urlopen("http://127.0.0.1:13000/login",timeout=3) as response:
                if response.status==200:
                    break
        except OSError:
            time.sleep(1)
    else:
        raise RuntimeError("Grafana host access did not become ready")
    print("Grafana swarm datasources, dashboard, and host-local access enabled.")


def host_access():
    ui_unit=Path("/etc/systemd/system/kagent-ui-host.service")
    if ui_unit.exists() and "Kagent UI host access" not in ui_unit.read_text():
        raise RuntimeError("unowned service unit kagent-ui-host.service")
    ui_unit.write_text("[Unit]\nDescription=Kagent UI host access\n"
        "After=snap.microk8s.daemon-kubelite.service\n"
        "[Service]\nType=simple\n"
        "Environment=KUBECONFIG=/var/snap/microk8s/current/credentials/client.config\n"
        "ExecStart=/snap/microk8s/current/kubectl port-forward -n factory-executions --address 127.0.0.1 "
        "service/factory-ui 18080:8080\nRestart=always\nRestartSec=5\nKillMode=control-group\n"
        "[Install]\nWantedBy=multi-user.target\n")
    ui_unit.chmod(0o644)
    subprocess.run(["systemctl","daemon-reload"],check=True)
    subprocess.run(["systemctl","enable","kagent-ui-host.service"],check=True)
    subprocess.run(["systemctl","restart","kagent-ui-host.service"],check=True)
    for _ in range(30):
        try:
            with urllib.request.urlopen("http://127.0.0.1:18080/",timeout=3) as response:
                if response.status==200:
                    break
        except OSError:
            time.sleep(1)
    else:
        raise RuntimeError("Kagent UI host access did not become ready")
    print("Kagent UI host-local access enabled.")


def factory_ui():
    source=kube("get","configmap","factory-ui-config","-n",SPACE,"-o","json")
    nginx=source["data"]["nginx.conf"]
    token=(STATE/"controller-token").read_text().strip()
    if not token or "\n" in token or '"' in token:
        raise RuntimeError("invalid protected factory controller token")
    nginx=nginx.replace("proxy_set_header Host $host;",
        "proxy_set_header Host $host;\n                  proxy_set_header Authorization \"Bearer "+token+"\";")
    owned_apply({"apiVersion":"v1","kind":"Secret",
        "metadata":{"name":"factory-ui-auth-config","namespace":SPACE},
        "stringData":{"nginx.conf":nginx}})
    deployment=kube("get","deployment","factory-ui","-n",SPACE,"-o","json")
    if deployment["metadata"].get("labels",{}).get("app.kubernetes.io/managed-by")!=OWNER:
        raise RuntimeError("refusing to patch unowned factory UI")
    digest=hashlib.sha256(nginx.encode()).hexdigest()
    volumes=deployment["spec"]["template"]["spec"]["volumes"]
    indexes=[index for index,volume in enumerate(volumes) if volume["name"]=="ui-config"]
    if len(indexes)!=1 or "configMap" not in volumes[indexes[0]]:
        raise RuntimeError("unexpected factory UI config volume")
    index=indexes[0]
    patch=[
        {"op":"test","path":"/metadata/resourceVersion",
         "value":deployment["metadata"]["resourceVersion"]},
        {"op":"replace","path":"/spec/replicas","value":1},
        {"op":"add","path":"/spec/template/metadata/annotations/factory.copilot~1ui-auth",
         "value":digest},
        {"op":"remove","path":"/spec/template/spec/volumes/"+str(index)+"/configMap"},
        {"op":"add","path":"/spec/template/spec/volumes/"+str(index)+"/secret",
         "value":{"secretName":"factory-ui-auth-config","defaultMode":420}},
    ]
    subprocess.run([*KUBE,"patch","deployment","factory-ui","-n",SPACE,
        "--type=json","-p",json.dumps(patch)],env=ENV,check=True,
        capture_output=True,timeout=60)
    subprocess.run([*KUBE,"rollout","status","deployment/factory-ui","-n",SPACE,
        "--timeout=180s"],env=ENV,check=True,timeout=210)
    host_access()
    print("Factory Kagent UI authenticated to the isolated controller.")


def service():
    runtime = json.loads((STATE/"runtime.json").read_text())
    trust = subprocess.run([*KUBE,"exec","-n",SPACE,"deployment/factory-controller","--",
        "cat","/run/substrate-servicedns/trust-bundle.pem"],env=ENV,capture_output=True,check=True,timeout=60).stdout
    (STATE/"postgres-ca.pem").write_bytes(trust)
    (STATE/"postgres-ca.pem").chmod(0o600)
    postgres = kube("get","service","kagent-postgresql","-n","kagent","-o","json")
    tempo = kube("get","service","tempo","-n","observability","-o","json")
    common = {"backlog":100,"runtimeIdentity":runtime,
        "clientBinary":str(STATE/"client"),"controllerToken":str(STATE/"controller-token"),
        "windowsPython":"/mnt/c/ProgramData/Tools/Python/3.12.10/x64/python.exe",
        "bindingScript":r"Q:\tools\kagent-lab\agency-broker\configure.py",
        "certificate":str(STATE/"service.crt"),"key":str(STATE/"service.key"),"ca":str(STATE/"ca.crt"),
        "postgres":{"host":"kagent-postgresql.kagent.svc",
            "hostaddr":postgres["spec"]["clusterIP"],"port":5432,"dbname":"kagent",
            "user":"factory_readwrite_user","password":(STATE/"readwrite-password").read_text(),
            "role":"factory_readwrite",
            "sslmode":"verify-full","sslrootcert":str(STATE/"postgres-ca.pem"),
            "channel_binding":"disable","connect_timeout":10},
        "clients":{}}
    for owner,file in [("operator","operator-token"),("acceptance-other","other-token"),
                       ("swarm-manager","swarm-token")]:
        token = (STATE/file).read_text()
        common["clients"][hashlib.sha256(token.encode()).hexdigest()] = {
            "owner":owner,"fixture":owner!="swarm-manager"}
    units = []
    for name,role in ROLES.items():
        config = {**common,**gateway_config(name,role)}
        filename = "service.json" if name == "coding" else "service-"+name+".json"
        config_path = STATE/filename
        write(config_path,json.dumps(config))
        unit_name = "factory-coding.service" if name == "coding" else "factory-"+name+".service"
        unit = Path("/etc/systemd/system")/unit_name
        if unit.exists() and "factory-coding" not in unit.read_text() and "SlopMop" not in unit.read_text():
            raise RuntimeError("unowned service unit "+unit_name)
        unit.write_text("[Unit]\nDescription=SlopMop "+name+" A2A gateway\n"
            "After=network.target snap.microk8s.daemon-kubelite.service\n"
            "[Service]\nType=simple\nEnvironment=KUBECONFIG=/var/snap/microk8s/current/credentials/client.config\n"
            "Environment=WSL_INTEROP=/run/WSL/1_interop\nEnvironment=WSL_DISTRO_NAME=Ubuntu-26.04\n"
            "ExecStart=/usr/bin/python3 "+str(SOURCE/"gateway_service.py")+" --config "+str(config_path)+
            "\nRestart=on-failure\nRestartSec=10\nUMask=0077\nKillMode=control-group\n"
            "[Install]\nWantedBy=multi-user.target\n")
        unit.chmod(0o644)
        units.append((unit_name,role["port"]))
    manager = {**common,
        "listenPort":18451,
        "hostDashboardAddress":"127.0.0.1",
        "hostDashboardPort":18452,
        "deploymentProjection":True,
        "otelHttpEndpoint":"http://"+tempo["spec"]["clusterIP"]+":4318/v1/traces",
        "roleToken":str(STATE/"swarm-token"),
        "roleEndpoints":{name:"https://10.82.0.2:"+str(role["port"]) for name,role in ROLES.items()},
        "agentCard":{
            "name":"slopmop-swarm-manager",
            "description":"Coordinate evidence, planning, review, implementation, PR completion, and documentation through A2A.",
            "url":"https://10.82.0.2:18451/a2a",
            "skill":{"id":"slopmop-campaign","name":"SlopMop Campaign",
                "description":"Run a bounded autonomous SlopMop campaign.",
                "tags":["slopmop","swarm","orchestration"]},
        }}
    manager_path=STATE/"service-swarm-manager.json"
    write(manager_path,json.dumps(manager))
    manager_unit=Path("/etc/systemd/system/factory-swarm-manager.service")
    if manager_unit.exists() and "SlopMop Swarm Manager" not in manager_unit.read_text():
        raise RuntimeError("unowned service unit factory-swarm-manager.service")
    manager_unit.write_text("[Unit]\nDescription=SlopMop Swarm Manager A2A agent\n"
        "After=network.target snap.microk8s.daemon-kubelite.service\n"
        "[Service]\nType=simple\nEnvironment=KUBECONFIG=/var/snap/microk8s/current/credentials/client.config\n"
        "Environment=WSL_INTEROP=/run/WSL/1_interop\nEnvironment=WSL_DISTRO_NAME=Ubuntu-26.04\n"
        "ExecStart=/usr/bin/python3 "+str(SOURCE/"swarm_manager.py")+" --config "+str(manager_path)+
        "\nRestart=on-failure\nRestartSec=10\nUMask=0077\nKillMode=control-group\n"
        "[Install]\nWantedBy=multi-user.target\n")
    manager_unit.chmod(0o644)
    units.append(("factory-swarm-manager.service",18451))
    autonomous_unit=Path("/etc/systemd/system/factory-swarm-autonomous.service")
    if autonomous_unit.exists() and "SlopMop autonomous evidence crawl" not in autonomous_unit.read_text():
        raise RuntimeError("unowned service unit factory-swarm-autonomous.service")
    autonomous_unit.write_text("[Unit]\nDescription=SlopMop autonomous evidence crawl\n"
        "After=factory-swarm-manager.service\nRequires=factory-swarm-manager.service\n"
        "[Service]\nType=oneshot\nUMask=0077\n"
        "ExecStart=/usr/bin/python3 "+str(SOURCE/"swarm_autonomous.py")+
        " --config "+str(manager_path)+" --lookback-days 7 --source-limit 12"+
        " --candidate-limit 3 --implementation-limit 1 --window-hours 12 --execute\n")
    autonomous_unit.chmod(0o644)
    autonomous_timer=Path("/etc/systemd/system/factory-swarm-autonomous.timer")
    if autonomous_timer.exists() and "SlopMop bounded autonomous discovery cadence" not in autonomous_timer.read_text():
        raise RuntimeError("unowned timer factory-swarm-autonomous.timer")
    autonomous_timer.write_text("[Unit]\nDescription=SlopMop bounded autonomous discovery cadence\n"
        "[Timer]\nOnBootSec=20min\nOnUnitActiveSec=12h\nRandomizedDelaySec=10min\n"
        "Persistent=true\nUnit=factory-swarm-autonomous.service\n"
        "[Install]\nWantedBy=timers.target\n")
    autonomous_timer.chmod(0o644)
    subprocess.run(["systemctl","daemon-reload"],check=True)
    for unit_name,_ in units:
        subprocess.run(["systemctl","enable","--now",unit_name],check=True)
        subprocess.run(["systemctl","restart",unit_name],check=True)
    context = ssl.create_default_context(cafile=config["ca"])
    for unit_name,port in units:
        for _ in range(60):
            try:
                with urllib.request.urlopen("https://10.82.0.2:"+str(port)+"/readyz",
                    context=context,timeout=5) as response:
                    if json.load(response).get("ready"):
                        break
            except OSError:
                time.sleep(1)
        else:
            raise RuntimeError(unit_name+" HTTPS readiness did not succeed")
    subprocess.run(["systemctl","enable","--now","factory-swarm-autonomous.timer"],check=True)
    with urllib.request.urlopen("http://127.0.0.1:18452/readyz",timeout=5) as response:
        if not json.load(response).get("ready"):
            raise RuntimeError("host dashboard readiness did not succeed")
    host_access()
    print("Authenticated single-role A2A swarm gateways enabled.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action",choices=["prepare","control-plane","proxy-bindings",
        "observability","host-access","factory-ui","service"])
    args = parser.parse_args()
    {"prepare":prepare,"control-plane":control_plane,"proxy-bindings":proxy_bindings,
     "observability":observability,"host-access":host_access,"factory-ui":factory_ui,
     "service":service}[args.action]()
