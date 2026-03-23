import subprocess, json, urllib.request, os, sys

bw_master = os.environ["BW_MASTER_PASS"]
bw_server = os.environ["BW_SERVER"]
bw_clientid = os.environ["BW_CLIENTID"]
bw_clientsecret = os.environ["BW_CLIENTSECRET"]

env = {**os.environ, "BW_CLIENTID": bw_clientid, "BW_CLIENTSECRET": bw_clientsecret, "BW_MASTER_PASS": bw_master}
subprocess.run(["bw", "config", "server", bw_server], capture_output=True, env=env)
subprocess.run(["bw", "login", "--apikey"], capture_output=True, env=env)
result = subprocess.run(["bw", "unlock", "--passwordenv", "BW_MASTER_PASS", "--raw"], capture_output=True, text=True, env=env)
session = result.stdout.strip()

items_result = subprocess.run(["bw", "list", "items", "--session", session], capture_output=True, text=True, env=env)
items = json.loads(items_result.stdout)

password = None
for item in items:
    if item.get("name") == "Keycloak SSO":
        password = item["login"]["password"]
        break

if not password:
    print("ERROR: Keycloak SSO item not found or has no password")
    sys.exit(1)

payload = json.dumps({"username": "relder@sovereignadvisory.ai", "password": password}).encode()
req = urllib.request.Request("http://localhost:8777/update-keycloak", data=payload, method="POST")
req.add_header("Content-Type", "application/json")
with urllib.request.urlopen(req) as r:
    result = json.loads(r.read())
    print(json.dumps(result))
