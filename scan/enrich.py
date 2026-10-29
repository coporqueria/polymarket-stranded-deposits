"""Add verifiable evidence to each confirmed candidate from scan.py.

Per wallet: a re-check of code, pUSD balance and nonce at one pinned block, the Polymarket profile
(user IDs, account creation date), and the first deposit (block, time, transaction, amount).
A wallet with no contract cannot send anything, so its balance only grows; the first funded block
is found by binary search on historical balances.
Usage: python3 -u enrich.py OUTDIR
"""
import datetime, json, random, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
import scan
from scan import PUSD, rpc, http_json

# Reads are pinned to one block, so they need a node that keeps old state; this one isn't rate-limited.
scan.RPCS = ["https://poly.api.pocket.network", "https://polygon.gateway.tenderly.co", "https://polygon.drpc.org"]

ARCHIVE = scan.RPCS
TOKENS = [PUSD, "0x2791Bca1f2de4661ED88A30C99A7a9449aa84174", "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359"]
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
FACTORY_START = 86272144  # first wallet the deposit wallet factory ever created

out = sys.argv[1]
cands = {a: c for a, c in json.load(open(f"{out}/candidates.json")).items() if c.get("profile_wallet_match")}
ckpt = f"{out}/evidence.jsonl"
done = {}
try:
    for line in open(ckpt):
        o = json.loads(line); done[o["wallet"]] = o
except FileNotFoundError:
    pass
pinned = int(json.loads(open(f"{out}/pinned.json").read())["block"]) if done else int(rpc("eth_blockNumber", []), 16)
json.dump({"block": pinned}, open(f"{out}/pinned.json", "w"))
pinned_ts = int(rpc("eth_getBlockByNumber", [hex(pinned), False])["timestamp"], 16)
iso = lambda ts: datetime.datetime.utcfromtimestamp(ts).isoformat() + "Z"


def balances_at(wallet, block):
    data = "0x70a08231" + wallet[2:].rjust(64, "0")
    body = [{"jsonrpc": "2.0", "id": i, "method": "eth_call", "params": [{"to": t, "data": data}, hex(block)]} for i, t in enumerate(TOKENS)]
    for i in range(25):
        try:
            r = urllib.request.Request(random.choice(ARCHIVE), json.dumps(body).encode(), {"content-type": "application/json", "user-agent": "Mozilla/5.0"})
            j = json.load(urllib.request.urlopen(r, timeout=40))
            if isinstance(j, list) and all("result" in x for x in j):
                return [int(x["result"], 16) for x in sorted(j, key=lambda x: x["id"])]
        except Exception:
            pass
        time.sleep(min(30, 2 ** min(i, 5)))
    raise RuntimeError(f"balances {wallet} {block}")


def first_deposit(wallet):
    lo, hi = FACTORY_START, pinned
    while lo < hi:
        mid = (lo + hi) // 2
        if sum(balances_at(wallet, mid)) > 0:
            hi = mid
        else:
            lo = mid + 1
    logs = rpc("eth_getLogs", [{"address": TOKENS, "topics": [TRANSFER, None, "0x" + wallet[2:].rjust(64, "0")], "fromBlock": hex(lo), "toBlock": hex(lo)}])
    l = min(logs, key=lambda x: int(x["logIndex"], 16)) if logs else None
    ts = int(rpc("eth_getBlockByNumber", [hex(lo), False])["timestamp"], 16)
    return {"tx": l and l["transactionHash"], "block": lo, "time": iso(ts), "amount": l and int(l["data"], 16) / 1e6}


def enrich(item):
    addr, c = item
    pusd, usdce, usdc = balances_at(addr, pinned)
    code = rpc("eth_getCode", [addr, hex(pinned)])
    nonce = int(rpc("eth_getTransactionCount", [addr, hex(pinned)]), 16)
    p = http_json(f"https://gamma-api.polymarket.com/public-profile?address={addr}")
    return addr, {
        "wallet": addr, "balance_pusd": pusd / 1e6, "other_balances": {"USDC.e": usdce / 1e6, "USDC": usdc / 1e6}, "has_contract": code != "0x", "nonce": nonce,
        "polymarket_profile_wallet": (p or {}).get("proxyWallet"), "profile_created": (p or {}).get("createdAt"),
        "polymarket_user_ids": [u.get("id") for u in (p or {}).get("users") or []],
        "confirmed": bool(p and (p.get("proxyWallet") or "").lower() == addr.lower()) and code == "0x" and pusd > 0 and nonce == 0,
        "deposits": [first_deposit(addr)],
    }


rows = dict(done)
todo = [(a, c) for a, c in cands.items() if a not in done]
with ThreadPoolExecutor(6) as ex, open(ckpt, "a") as f:
    for addr, row in ex.map(enrich, todo):
        rows[addr] = row
        f.write(json.dumps(row) + "\n"); f.flush()
        if len(rows) % 50 == 0:
            print(f"  {len(rows)}/{len(cands)}", flush=True)
result = {"checked_at_block": pinned, "checked_at": iso(pinned_ts), "wallets": sorted(rows.values(), key=lambda r: -r["balance_pusd"])}
json.dump(result, open(f"{out}/evidence.json", "w"), indent=1)
conf = [r for r in result["wallets"] if r["confirmed"]]
print(f"evidence: {len(conf)} confirmed, {sum(r['balance_pusd'] for r in conf):.2f} pUSD, pinned block {pinned}", flush=True)
