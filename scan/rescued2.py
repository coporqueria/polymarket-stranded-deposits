"""For each rescued wallet: who sent the rescue, and the first incoming pUSD / USDC.e / USDC transfer.

Deposit wallets only exist since the factory went live (first creation block), so the search starts there.
Usage (inside the scan folder): python3 -u rescued2.py FIRST_FACTORY_BLOCK
"""
import json, sys, time, urllib.request, random
from concurrent.futures import ThreadPoolExecutor
RPCS = ["https://rpc-mainnet.matic.quiknode.pro", "https://polygon.gateway.tenderly.co"]
TOKENS = ["0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB", "0x2791Bca1f2de4661ED88A30C99A7a9449aa84174", "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359"]
NAMES = {t.lower(): n for t, n in zip(TOKENS, ["pUSD", "USDC.e", "USDC"])}
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
STEP = 100000


def rpc(m, p, tries=25):
    for i in range(tries):
        try:
            r = urllib.request.Request(random.choice(RPCS), json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(), {"content-type": "application/json", "user-agent": "Mozilla/5.0"})
            j = json.load(urllib.request.urlopen(r, timeout=40))
            if "result" in j:
                return j["result"]
        except Exception:
            pass
        time.sleep(min(30, 2 ** min(i, 5)))
    raise RuntimeError(f"{m} {p}")


def balances_at(wallet, block):
    """Sum of pUSD, USDC.e and USDC held at a block, in one batched request."""
    data = "0x70a08231" + wallet[2:].rjust(64, "0")
    body = [{"jsonrpc": "2.0", "id": i, "method": "eth_call", "params": [{"to": t, "data": data}, hex(block)]} for i, t in enumerate(TOKENS)]
    for i in range(25):
        try:
            r = urllib.request.Request(RPCS[0], json.dumps(body).encode(), {"content-type": "application/json", "user-agent": "Mozilla/5.0"})
            j = json.load(urllib.request.urlopen(r, timeout=40))
            if isinstance(j, list) and all("result" in x for x in j):
                return sum(int(x["result"], 16) for x in j)
        except Exception:
            pass
        time.sleep(min(30, 2 ** min(i, 5)))
    raise RuntimeError(f"balances {wallet} {block}")


def first_in(wallet, start, end):
    # A wallet with no contract cannot send anything, so its balance only grows: binary search the first funded block.
    if balances_at(wallet, end) == 0:
        return None
    lo, hi = start, end
    while lo < hi:
        mid = (lo + hi) // 2
        if balances_at(wallet, mid) > 0:
            hi = mid
        else:
            lo = mid + 1
    topic = "0x" + wallet[2:].rjust(64, "0")
    logs = rpc("eth_getLogs", [{"address": TOKENS, "topics": [TRANSFER, None, topic], "fromBlock": hex(lo), "toBlock": hex(lo)}])
    l = min(logs, key=lambda x: int(x["logIndex"], 16)) if logs else None
    ts = int(rpc("eth_getBlockByNumber", [hex(lo), False])["timestamp"], 16)
    return {"block": lo, "time": ts, "tx": l and l["transactionHash"], "token": l and NAMES[l["address"].lower()], "amount": l and int(l["data"], 16) / 1e6}


def one(r):
    tx = rpc("eth_getTransactionByHash", [r["tx"]])
    return {**r, "rescuer": tx["from"], "selector": tx["input"][:10], "first_in": first_in(r["wallet"], START, r["block"] - 1)}


START = int(sys.argv[1])
rows = [r for r in json.load(open("fixed.json")) if any(v > 0 for v in r["before"].values())]
done = {}
try:
    for line in open("rescued2.jsonl"):
        o = json.loads(line); done[o["wallet"]] = o
except FileNotFoundError:
    pass
todo = [r for r in rows if r["wallet"] not in done]
print(f"{len(done)} already done, {len(todo)} to go", flush=True)
with ThreadPoolExecutor(6) as ex, open("rescued2.jsonl", "a") as f:
    for o in ex.map(one, todo):
        f.write(json.dumps(o) + "\n"); f.flush(); done[o["wallet"]] = o
out = [done[r["wallet"]] for r in rows]
json.dump(out, open("rescued2.json", "w"), indent=0)
print(f"rescued {len(out)}: dated {sum(1 for o in out if o['first_in'])}, deployLegacy calls {sum(1 for o in out if o['selector'] == '0xef8493cd')}, rescuers {len({o['rescuer'] for o in out})}", flush=True)
