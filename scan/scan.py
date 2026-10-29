"""Find funded Polymarket deposit wallets that were never deployed.

Pass 1: every pUSD Wrapped event (each deposit into Polymarket's currency), recipient = topic[3].
Pass 2: per unique recipient, getCode + pUSD balance + nonce.
Pass 3: candidates (no code, balance > 0) checked against Polymarket's profile API.
Usage: python3 -I -u scan.py START_BLOCK END_BLOCK OUTDIR
"""
import gzip, json, os, random, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

RPCS = ["https://polygon.gateway.tenderly.co", "https://polygon.gateway.tenderly.co", "https://rpc-mainnet.matic.quiknode.pro"]
# Current-state reads (no history needed) go to a fast node without archive logs.
STATE_RPCS = ["https://polygon-bor-rpc.publicnode.com"]
PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
WRAPPED = "0xc00a5c84859ae82a7f5e6a2773283fb525335d5b3195f61174aa1ecc7e15dd84"
STEP = 1000


def rpc(method, params, tries=40, pool=None):
    for i in range(tries):
        url = random.choice(pool or RPCS)
        try:
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
            req = urllib.request.Request(url, body, {"content-type": "application/json", "user-agent": "Mozilla/5.0", "accept-encoding": "gzip"})
            resp = urllib.request.urlopen(req, timeout=30)
            raw = resp.read()
            r = json.loads(gzip.decompress(raw) if resp.headers.get("content-encoding") == "gzip" else raw)
            if "result" in r:
                return r["result"]
        except Exception:
            pass
        time.sleep(min(60, 2 ** min(i, 6)))
    raise RuntimeError(f"{method} failed {params}")


def get_logs(a, b):
    try:
        return rpc("eth_getLogs", [{"address": PUSD, "topics": [WRAPPED], "fromBlock": hex(a), "toBlock": hex(b)}], tries=4 if b - a >= 50 else 40)
    except RuntimeError:
        if b - a < 50:
            raise
        m = (a + b) // 2
        return get_logs(a, m) + get_logs(m + 1, b)


def http_json(url):
    for i in range(4):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"user-agent": "scan"}), timeout=30))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
        except Exception:
            pass
        time.sleep(1 + i)
    return None


def main():
    start, end, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    os.makedirs(out, exist_ok=True)
    ckpt = f"{out}/recipients.json"
    recipients, next_block = {}, start
    if os.path.exists(ckpt):
        saved = json.load(open(ckpt)); recipients, next_block = saved["recipients"], saved["next_block"]
    chunks = [(a, min(a + STEP - 1, end)) for a in range(next_block, end + 1, STEP)]
    print(f"pass1: {len(chunks)} chunks to scan from block {next_block}", flush=True)
    t0 = time.time()
    def windowed(ex):
        # Bounded look-ahead so finished chunks don't pile up in memory behind a slow one.
        for i in range(0, len(chunks), 32):
            part = chunks[i:i + 32]
            yield from zip(part, ex.map(lambda c: get_logs(*c), part))

    with ThreadPoolExecutor(5) as ex:
        for n, ((a, b), logs) in enumerate(windowed(ex)):
            for l in logs:
                r = "0x" + l["topics"][3][-40:]
                blk = int(l["blockNumber"], 16)
                d = recipients.get(r)
                if d is None:
                    recipients[r] = [1, int(l["data"][:66], 16) / 1e6, blk, blk]
                else:
                    d[0] += 1; d[1] += int(l["data"][:66], 16) / 1e6; d[3] = blk
            if n % 200 == 199 or n == len(chunks) - 1:
                json.dump({"recipients": recipients, "next_block": b + 1}, open(ckpt + ".tmp", "w")); os.replace(ckpt + ".tmp", ckpt)
                rate = (n + 1) / (time.time() - t0)
                print(f"  {n + 1}/{len(chunks)} chunks, block {b}, {len(recipients)} recipients, eta {(len(chunks) - n - 1) / rate / 60:.0f} min", flush=True)
    recipients = {k: {"n": v[0], "total": v[1], "first": v[2], "last": v[3]} for k, v in recipients.items()}
    print(f"pass2: {len(recipients)} unique recipients", flush=True)

    helper = "0x000000000000000000000000000000000000c0de"
    sizes_code = "0x60005b80361115601557803 53b81526020016002565b366000f3".replace(" ", "")
    bal_code = ("0x6000" + "5b" + "80361115" + "605b57" + "7f" + "70a08231" + "00" * 28 + "600052" + "8035" + "600452"
                + "6020" + "81" + "6040" + "01" + "6024" + "6000" + "73" + PUSD[2:].lower() + "5a" + "fa" + "50" + "602001" + "600256" + "5b" + "366040f3")

    def words(code, addrs):
        data = "0x" + "".join(x[2:].rjust(64, "0") for x in addrs)
        res = rpc("eth_call", [{"to": helper, "data": data, "gas": "0x3b9aca00"}, "latest", {helper: {"code": code}}], pool=STATE_RPCS)
        return [int(res[2 + 64 * i:66 + 64 * i], 16) for i in range(len(addrs))]

    def batch(addrs):
        sizes = words(sizes_code, addrs)
        empty = [x for x, sz in zip(addrs, sizes) if sz == 0]
        bals = words(bal_code, empty) if empty else []
        return [(x, bl / 1e6) for x, bl in zip(empty, bals) if bl > 0]

    addrs = list(recipients)
    groups = [addrs[i:i + 1000] for i in range(0, len(addrs), 1000)]
    funded_empty = []
    with ThreadPoolExecutor(6) as ex:
        for n, res in enumerate(ex.map(batch, groups)):
            funded_empty += res
            if n % 100 == 0:
                print(f"  checked {n * 1000}/{len(addrs)}, funded with no contract {len(funded_empty)}", flush=True)
    print(f"  funded with no contract: {len(funded_empty)}", flush=True)

    def nonce(item):
        x, bal = item
        return x, bal, int(rpc("eth_getTransactionCount", [x, "latest"], pool=STATE_RPCS), 16)
    candidates = {}
    with ThreadPoolExecutor(8) as ex:
        for x, bal, nc in ex.map(nonce, funded_empty):
            if nc == 0:
                candidates[x] = {**recipients[x], "bal": bal, "nonce": nc}
    print(f"pass3: {len(candidates)} candidates (no code, nonce 0, pUSD > 0)", flush=True)

    def profile(addr):
        p = http_json(f"https://gamma-api.polymarket.com/public-profile?address={addr}")
        return addr, p
    with ThreadPoolExecutor(6) as ex:
        for addr, p in ex.map(profile, list(candidates)):
            c = candidates[addr]
            c["profile_wallet_match"] = bool(p and (p.get("proxyWallet") or "").lower() == addr.lower())
            c["profile_created"] = (p or {}).get("createdAt")
    json.dump(candidates, open(f"{out}/candidates.json", "w"), indent=1)
    conf = {a: c for a, c in candidates.items() if c["profile_wallet_match"]}
    print(f"done: confirmed web-user stuck wallets {len(conf)}, total {sum(c['bal'] for c in conf.values()):.2f} pUSD; "
          f"unconfirmed candidates {len(candidates) - len(conf)}, total {sum(c['bal'] for a, c in candidates.items() if a not in conf):.2f}", flush=True)


if __name__ == "__main__":
    main()
