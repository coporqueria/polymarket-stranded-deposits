"""Merge a scan of recent blocks into the published data, so a refresh doesn't need the full scan.

Run after scan.py, enrich.py, factory_scan.py, fixed.py and rescued2.py have covered only the blocks since
the last snapshot. Every wallet in data/ is re-checked at the new snapshot block, then evidence.json and
rescued2.json are rewritten for build.py. The count of receiving wallets can't be merged, so it is dropped.
Usage: PYTHONPATH=scan python3 scan/update.py OUTDIR
"""
import calendar, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
import scan
from scan import PUSD, rpc, http_json

scan.RPCS = ["https://poly.api.pocket.network", "https://polygon.drpc.org", "https://polygon.gateway.tenderly.co"]
DEPLOY_LEGACY = "0xef8493cd"

out = sys.argv[1]
ev = json.load(open(f"{out}/evidence.json"))
pinned = ev["checked_at_block"]
stamp = lambda s: calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))


def recheck(w):
    addr = w["wallet"]
    pusd = int(rpc("eth_call", [{"to": PUSD, "data": "0x70a08231" + addr[2:].rjust(64, "0")}, hex(pinned)]), 16)
    code = rpc("eth_getCode", [addr, hex(pinned)])
    nonce = int(rpc("eth_getTransactionCount", [addr, hex(pinned)]), 16)
    p = http_json(f"https://gamma-api.polymarket.com/public-profile?address={addr}")
    return {
        "wallet": addr, "balance_pusd": pusd / 1e6, "has_contract": code != "0x", "nonce": nonce,
        "polymarket_profile_wallet": (p or {}).get("proxyWallet"), "profile_created": w["profile_created"],
        "polymarket_user_ids": w["polymarket_user_ids"],
        "confirmed": bool(p and (p.get("proxyWallet") or "").lower() == addr.lower()) and code == "0x" and pusd > 0 and nonce == 0,
        "deposits": w["deposits"],
    }


seen = {r["wallet"].lower() for r in ev["wallets"]}
old = [w for w in json.load(open("data/data.json"))["wallets"] if w.get("profile_created") and w["wallet"] not in seen]
with ThreadPoolExecutor(6) as ex:
    checked = list(ex.map(recheck, old))
ev["wallets"] = sorted(ev["wallets"] + checked, key=lambda r: -r["balance_pusd"])
json.dump(ev, open(f"{out}/evidence.json", "w"), indent=1)
gone = [r for r in checked if not r["confirmed"]]
print(f"rechecked {len(checked)}: {sum(r['has_contract'] for r in gone)} now have a contract, {sum(not r['has_contract'] for r in gone)} dropped for another reason")

published = json.load(open("data/rescued.json"))


def tx_block(tx):
    # Some nodes answer null for older transactions, so ask each until one knows it.
    for node in scan.RPCS:
        r = rpc("eth_getTransactionByHash", [tx], pool=[node])
        if r:
            return int(r["blockNumber"], 16)
    raise RuntimeError(f"no node knows {tx}")


txs = sorted({r["created_tx"] for r in published})
with ThreadPoolExecutor(6) as ex:
    blocks = dict(zip(txs, ex.map(tx_block, txs)))
rows = [{
    "wallet": r["wallet"], "block": blocks[r["created_tx"]], "time": stamp(r["created_at"]), "tx": r["created_tx"], "before": r["held_before"],
    "rescuer": r["rescuer"], "selector": DEPLOY_LEGACY,
    "first_in": r["first_deposit_at"] and {"time": stamp(r["first_deposit_at"]), "tx": r["first_deposit_tx"]},
} for r in published]
known = {r["wallet"] for r in rows}
new = [r for r in json.load(open(f"{out}/rescued2.json")) if r["wallet"] not in known]
json.dump(rows + new, open(f"{out}/rescued2.json", "w"), indent=0)
print(f"rescued {len(rows)} published + {len(new)} new")

if os.path.exists(f"{out}/recipients.json"):
    os.remove(f"{out}/recipients.json")
