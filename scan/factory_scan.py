"""Every wallet the deposit wallet factory created, with its type and block.

Usage: python3 -u factory_scan.py START END OUT.json
"""
import collections, json, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

RPCS = ["https://rpc-mainnet.matic.quiknode.pro", "https://poly.api.pocket.network"]
FACTORY = "0x00000000000Fb5C9ADea0298D729A0CB3823Cc07"
DEPLOYED = "0x7441de0ad639fe5d2bf1c22447715a0528b682385736bb40ae8dd92555eb8276"
LEGACY_IMPL = "58ca52ebe0dadfdf531cde7062e76746de4db1eb"
STEP = 5000


def get_logs(a, b, depth=0):
    for i in range(12):
        url = RPCS[i % len(RPCS)]
        try:
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_getLogs", "params": [{"address": FACTORY, "topics": [DEPLOYED], "fromBlock": hex(a), "toBlock": hex(b)}]}).encode()
            j = json.load(urllib.request.urlopen(urllib.request.Request(url, body, {"content-type": "application/json", "user-agent": "Mozilla/5.0"}), timeout=60))
            if "result" in j:
                return j["result"]
        except urllib.error.HTTPError as e:
            if e.code == 413 and b > a:
                m = (a + b) // 2
                return get_logs(a, m) + get_logs(m + 1, b)
        except Exception:
            pass
        time.sleep(min(30, 2 ** min(i, 5)))
    raise RuntimeError(f"logs {a}-{b}")


start, end, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
chunks = [(a, min(a + STEP - 1, end)) for a in range(start, end + 1, STEP)]
legacy, per_day = [], collections.Counter()
with ThreadPoolExecutor(4) as ex:
    for n, logs in enumerate(ex.map(lambda c: get_logs(*c), chunks)):
        for l in logs:
            kind = "legacy" if l["data"][-40:] == LEGACY_IMPL else "beacon"
            ts = int(l["blockTimestamp"], 16) if "blockTimestamp" in l else None
            day = time.strftime("%Y-%m-%d", time.gmtime(ts)) if ts else str(int(l["blockNumber"], 16) // 43200)
            per_day[(day, kind)] += 1
            if kind == "legacy":
                legacy.append({"wallet": "0x" + l["topics"][1][-40:], "block": int(l["blockNumber"], 16), "time": ts, "tx": l["transactionHash"], "topics": l["topics"]})
        if n % 100 == 0:
            print(f"{n}/{len(chunks)} legacy={len(legacy)}", flush=True)
json.dump({"legacy": legacy, "per_day": {f"{d}|{k}": v for (d, k), v in sorted(per_day.items())}}, open(out, "w"))
print(f"done legacy={len(legacy)}", flush=True)
