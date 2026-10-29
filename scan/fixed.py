"""For every old-type wallet the factory created after the June 29 switch: did it already hold money just before?"""
import json, time, urllib.request, random
from concurrent.futures import ThreadPoolExecutor
RPCS = ["https://rpc-mainnet.matic.quiknode.pro", "https://polygon.gateway.tenderly.co"]
TOK = {"pUSD": "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB", "USDC.e": "0x2791Bca1f2de4661ED88A30C99A7a9449aa84174", "USDC": "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359"}
def rpc(m, p):
    for i in range(20):
        try:
            r = urllib.request.Request(random.choice(RPCS), json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(), {"content-type": "application/json", "user-agent": "Mozilla/5.0"})
            j = json.load(urllib.request.urlopen(r, timeout=40))
            if "result" in j: return j["result"]
        except Exception: pass
        time.sleep(min(30, 2 ** min(i, 5)))
    raise RuntimeError(m)
d = json.load(open("factory.json"))
L = d["legacy"]
# The factory created its first new-type wallet in this block; any old-type wallet after it came from deployLegacy.
SWITCH_BLOCK = 89353733
after = [x for x in L if x["block"] > SWITCH_BLOCK]
def check(x):
    b = hex(x["block"] - 1)
    bal = {k: int(rpc("eth_call", [{"to": t, "data": "0x70a08231" + x["wallet"][2:].rjust(64, "0")}, b]), 16) / 1e6 for k, t in TOK.items()}
    return {**{k: x[k] for k in ("wallet", "block", "time", "tx")}, "before": bal}
with ThreadPoolExecutor(6) as ex:
    rows = list(ex.map(check, after))
json.dump(rows, open("fixed.json", "w"), indent=0)
print(f"legacy deploys after block {SWITCH_BLOCK}: {len(rows)}; with money already inside: {sum(1 for r in rows if any(v > 0 for v in r['before'].values()))}")
