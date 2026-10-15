"""Submit reviewed task proposals only; never approve or dispatch."""
from pathlib import Path
import json
import sys

BASE=Path(__file__).resolve().parent
ROOT=BASE.parent.parent
sys.path.insert(0,str(ROOT/"items_registry"))
import cloud_client
sys.stdout.reconfigure(encoding="utf-8")
cloud_client._TIMEOUT_S=30
assert cloud_client.enabled()
requests=json.loads((BASE/"intake_requests_v2.json").read_text(encoding="utf-8"))
receipt_path=BASE/"intake_receipts_v2.json"
receipts=json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.exists() else []
snapshot=cloud_client.request("GET","/api/agent/snapshot")
for request in requests:
    if any(r.get("title")==request["title"] for r in receipts):
        continue
    existing=[p for p in snapshot["inbox"] if p.get("title")==request["title"]]
    reply={"entry":existing[-1]} if existing else cloud_client.request("POST","/api/intake/submit",request)
    row={"title":request["title"],"requested_owner":request["owner"],"reply":reply}
    receipts.append(row)
    receipt_path.write_text(json.dumps(receipts,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(row,ensure_ascii=False),flush=True)
snapshot=cloud_client.request("GET","/api/agent/snapshot")
ids={r["reply"]["entry"]["id"] for r in receipts}
readback=[p for p in snapshot["inbox"] if p["id"] in ids]
(BASE/"intake_readback_v2.json").write_text(json.dumps({"generated_at":snapshot["generated_at"],"entries":readback},ensure_ascii=False,indent=2),encoding="utf-8")
assert len(readback)==len(receipts)
for p in readback:
    req=next(r for r in requests if r["title"]==p["title"])
    for k in ["title","goal_context","owner","criteria","close_criterion","note"]:
        assert p.get(k)==req.get(k),(p["id"],k)
    assert all(r["verdict"]=="pass" for r in p["lint"]["rules"]),p["lint"]
print("readback_fields_match",len(readback),"at",snapshot["generated_at"])
