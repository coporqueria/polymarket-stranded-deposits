"""Build the site's data files from the scan outputs plus publicly reported cases.

Writes data/data.json (page data), data/rescued.json (every rescue),
data/stuck-wallets.csv and data/stuck-wallets.json (the list for a batch fix).
Usage: PYTHONPATH=scan python3 scan/build.py OUTDIR
"""
import csv, datetime, json, statistics, subprocess, sys
from scan import PUSD, rpc

USDC = "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359"
FACTORY = "0x00000000000Fb5C9ADea0298D729A0CB3823Cc07"
# The factory created its first new-type (beacon) wallet in this block; every old-type wallet after it is a manual rescue.
SWITCH_BLOCK, SWITCH_AT = 89353733, "2026-06-29T12:19:55Z"
FACTORY_FIRST_WALLET_AT = "2026-05-01T19:38:00Z"  # block 86272144; older accounts may use an earlier wallet type
SWITCH_TS = int(datetime.datetime(2026, 6, 29, 12, 19, 55, tzinfo=datetime.timezone.utc).timestamp())
DEPLOY_LEGACY = "0xef8493cd"
REPORTS = [
    ("Polymarket/py-builder-relayer-client", 27),
    ("Polymarket/builder-relayer-client", 39),
]
ISSUE27 = "https://github.com/Polymarket/py-builder-relayer-client/issues/27"
ISSUE39 = "https://github.com/Polymarket/builder-relayer-client/issues/39"
# Wallets their owners reported on GitHub, with the owner address so the factory prediction can be checked.
REPORTED = [
    {"wallet": "0xa1e8751b2810b94dc59dba43a8ad9786289e09ba", "owner": "0x7384c05eb0902a7720c8021eb3010f6a38d3e8b8", "token": "USDC", "report_url": ISSUE39,
     "funding_tx": "0x755f95e06da988460d2ef80c19448e4ad76bf90ddcc7486f9807f71039459440"},
    {"wallet": "0x6545127773ef08dffde3fce3810cb01149704c2e", "owner": "0x648afc09ab95dfa69abab86f38a4a3406bae7b45", "token": "pUSD", "report_url": ISSUE27,
     "funding_tx": "0xe6c43485544edceb58497c648937cc8db25b4d798535a529f36aafe55eee8bde"},
    {"wallet": "0x6966adb241c0d644dfa8c345791cb0990067f3d7", "owner": "0xc8b82670f33fc7a90303f2f52d7d5ebaa6b2f681", "token": "pUSD", "report_url": ISSUE27,
     "funding_tx": "0xe77ac7c9a5cf0bea4be06efc389098e5e49a0cbaf935700dbc0623ad38acf241"},
    {"wallet": "0x02d71ba1c7d41072ec3b9e4fc0e0cd3989705684", "owner": "0x5527c18d24a1d07fb8ee6a083c619891cf82e6d5", "token": "pUSD", "report_url": ISSUE27,
     "funding_tx": "0xcccfebe870040b50f633e38d659835a3a3142e011a371bd19a6353e2db632a4b"},
    {"wallet": "0x95306bbca4f7581ffd9a6c5201e7a6f2df092edb", "owner": "0x66bd9caae07b03ac835b928e42379f43627f39bf", "token": "pUSD", "report_url": ISSUE27,
     "funding_tx": "0x01b83344b2ba35361a15a63dc9751a550d33f4d6757978b4c565a640be4f83e7"},
    {"wallet": "0x22b9fa6f28e3b3d12071855d56a514f23c1eefc1", "owner": "0xaa602f01a1c09ba9b96885ef91706f065a825687", "token": "pUSD", "report_url": ISSUE27,
     "funding_tx": "0x7d00b1d66f1d4e0ae657478e982e4a9bd6464e987c229438e7aa40cc93d995d9"},
    {"wallet": "0x1157ad1622c0549d6cf27ba327f5589ab4641f46", "owner": "0x5568765deb05192655792450c14e69a593ea0732", "token": "pUSD", "report_url": ISSUE27},
]
TOKENS = {"pUSD": PUSD, "USDC": USDC}


def iso(ts):
    return datetime.datetime.utcfromtimestamp(ts).isoformat() + "Z"


def predict_legacy(owner):
    return "0x" + rpc("eth_call", [{"to": FACTORY, "data": "0x8becfd88" + owner[2:].rjust(64, "0")}, "latest"])[-40:]


def deploy_legacy_calldata(owners):
    """deployLegacy(address[] owners, bytes32[] ids) with id = owner, as the factory predicts it."""
    n = len(owners)
    word = lambda v: format(v, "064x")
    owners_part = word(n) + "".join(o[2:].lower().rjust(64, "0") for o in owners)
    head = word(64) + word(64 + 32 * (n + 1))
    return DEPLOY_LEGACY + head + owners_part + owners_part


LEGACY_DEPLOYER = "0x225a26a1aD56A918eD9701Bb779DA2E762807427"  # the account that sent most past releases


def simulate(calldata):
    gas = int(rpc("eth_estimateGas", [{"from": LEGACY_DEPLOYER, "to": FACTORY, "data": calldata}]), 16)
    limit = int(rpc("eth_getBlockByNumber", ["latest", False])["gasLimit"], 16)
    return gas, limit


def tx_time(tx):
    r = rpc("eth_getTransactionByHash", [tx])
    return iso(int(rpc("eth_getBlockByNumber", [r["blockNumber"], False])["timestamp"], 16))


def report_meta(repo, n):
    j = json.loads(subprocess.check_output(["gh", "issue", "view", str(n), "-R", repo, "--json", "title,url,createdAt,comments,state"]))
    staff = {"MEMBER", "OWNER", "COLLABORATOR"}
    return {"url": j["url"], "title": j["title"], "opened": j["createdAt"], "state": j["state"], "comments": len(j["comments"]),
            "polymarket_replies": sum(1 for c in j["comments"] if c.get("authorAssociation") in staff)}


def rescued_rows(out):
    rows = []
    for r in json.load(open(f"{out}/rescued2.json")):
        assert r["block"] > SWITCH_BLOCK and r["selector"] == DEPLOY_LEGACY, r["tx"]
        fi = r["first_in"]
        rows.append({
            "wallet": r["wallet"], "created_tx": r["tx"], "created_at": iso(r["time"]), "rescuer": r["rescuer"],
            "held_before": {k: v for k, v in r["before"].items() if v > 0},
            "first_deposit_tx": fi and fi["tx"], "first_deposit_at": fi and iso(fi["time"]),
            # Before the switch an unfunded old wallet could still be created normally, so the wait starts at the later of the two.
            "wait_days": fi and round((r["time"] - max(fi["time"], SWITCH_TS)) / 86400, 2),
        })
    return sorted(rows, key=lambda x: x["created_at"])


def daily_backlog(rescued, stuck, today):
    """Money sitting in wallets that did not exist yet, for each day: rescued wallets between first deposit and rescue, plus stuck ones until today."""
    spans = [(max(x["first_deposit_at"], SWITCH_AT)[:10], x["created_at"][:10], sum(x["held_before"].values())) for x in rescued if x["first_deposit_at"]]
    spans += [(x["since"][:10], today, x["balance"]) for x in stuck]
    day = datetime.date.fromisoformat(SWITCH_AT[:10])
    end = datetime.date.fromisoformat(today)
    series = []
    while day <= end:
        d = day.isoformat()
        live = [(a, b, v) for a, b, v in spans if a <= d < b or (b == today and a <= d)]
        series.append({"day": d, "wallets": len(live), "amount": round(sum(v for _, _, v in live), 2)})
        day += datetime.timedelta(days=1)
    return series


def rescued_summary(rows, today):
    waits = sorted(x["wait_days"] for x in rows if x["wait_days"] is not None)
    per_day = {}
    for x in rows:
        d = per_day.setdefault(x["created_at"][:10], {"count": 0, "amount": 0.0})
        d["count"] += 1; d["amount"] = round(d["amount"] + sum(x["held_before"].values()), 2)
    by_rescuer = {}
    for x in rows:
        by_rescuer[x["rescuer"]] = by_rescuer.get(x["rescuer"], 0) + 1
    main_rescuer = max(by_rescuer, key=by_rescuer.get)
    big_days = sorted(((d, v["count"]) for d, v in per_day.items() if v["count"] >= 100), key=lambda kv: kv[0])
    last_big = big_days[-1][0] if big_days else None
    since_last_big = [x for x in rows if last_big and x["created_at"][:10] > last_big]
    return {
        "count": len(rows), "total": round(sum(sum(x["held_before"].values()) for x in rows), 2),
        "dated": len(waits), "median_wait_days": statistics.median(waits), "p90_wait_days": waits[int(len(waits) * 0.9)],
        "max_wait_days": waits[-1], "over_7_days": sum(1 for w in waits if w > 7), "over_30_days": sum(1 for w in waits if w > 30),
        "within_1_day": sum(1 for w in waits if w < 1), "transactions": len({x["created_tx"] for x in rows}), "last_rescue": rows[-1]["created_at"], "rescuers": len({x["rescuer"] for x in rows}),
        "per_day": per_day, "big_days": [{"day": d, "count": n} for d, n in big_days],
        "main_rescuer": main_rescuer, "main_rescuer_count": by_rescuer[main_rescuer],
        "since_last_big_count": len(since_last_big), "since_last_big_days": (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(last_big)).days if last_big else None,
        "waits": [{"rescued": x["created_at"][:10], "days": x["wait_days"], "amount": round(sum(x["held_before"].values()), 2)} for x in rows if x["wait_days"] is not None],
    }


def batch_info(known):
    if not known:
        return {"known_owners": 0, "calldata": None}
    calldata = deploy_legacy_calldata(known)
    gas, limit = simulate(calldata)
    return {"known_owners": len(known), "calldata": calldata, "simulated_from": LEGACY_DEPLOYER, "simulated_gas": gas,
            "gas_per_wallet": gas // len(known), "block_gas_limit": limit}


def main():
    out = sys.argv[1]
    ev = json.load(open(f"{out}/evidence.json"))
    wallets = {}
    for r in ev["wallets"]:
        # Only accounts created before the switch got an old-type wallet address; newer ones are created on first use.
        # Accounts made before the factory's first wallet may use an older wallet type, so they are left out.
        if not r["confirmed"] or not r["profile_created"] or not (FACTORY_FIRST_WALLET_AT <= r["profile_created"] < SWITCH_AT):
            continue
        wallets[r["wallet"].lower()] = {
            "wallet": r["wallet"].lower(), "balance": r["balance_pusd"], "token": "pUSD", "token_address": PUSD,
            "since": r["deposits"][0]["time"] if r["deposits"] else r["profile_created"],
            "deposits": [{"tx": d["tx"], "time": d["time"], "amount": d["amount"]} for d in r["deposits"]],
            "profile_created": r["profile_created"], "polymarket_user_ids": r.get("polymarket_user_ids") or [], "found_by": "scan",
        }
    for rep in REPORTED:
        addr, tok = rep["wallet"], TOKENS[rep["token"]]
        code = rpc("eth_getCode", [addr, "latest"])
        bal = int(rpc("eth_call", [{"to": tok, "data": "0x70a08231" + addr[2:].rjust(64, "0")}, "latest"]), 16) / 1e6
        predicted = predict_legacy(rep["owner"])
        assert predicted.lower() == addr, f"factory prediction mismatch for {addr}: {predicted}"
        if code != "0x" or bal == 0:
            continue
        w = wallets.setdefault(addr, {"wallet": addr, "balance": bal, "token": rep["token"], "token_address": tok, "deposits": [], "polymarket_user_ids": [], "found_by": "report"})
        w.update({"owner": rep["owner"], "report_url": rep["report_url"], "balance": bal})
        scanned = next((r for r in ev["wallets"] if r["wallet"].lower() == addr), None)
        if not w["deposits"] and scanned and scanned["deposits"]:
            w["deposits"] = [{"tx": d["tx"], "time": d["time"], "amount": d["amount"]} for d in scanned["deposits"]]
        if not w["deposits"] and rep.get("funding_tx"):
            w["deposits"] = [{"tx": rep["funding_tx"], "time": tx_time(rep["funding_tx"])}]
        w.setdefault("since", w["deposits"][0]["time"] if w["deposits"] else None)
    for w in wallets.values():
        w["first_deposit_at"] = w["since"]
        if w.get("since") and w["since"] < SWITCH_AT:
            w["since"] = SWITCH_AT
    rows = sorted(wallets.values(), key=lambda x: -x["balance"])
    missing = [r["wallet"] for r in rows if not r.get("since")]
    assert not missing, f"no date for {missing}"

    today = ev["checked_at"][:10]
    try:
        receivers = len(json.load(open(f"{out}/recipients.json"))["recipients"])
    except FileNotFoundError:
        receivers = None
    rescued = rescued_rows(out)
    known = [r["owner"] for r in rows if r.get("owner")]
    data = {
        "checked_at_block": ev["checked_at_block"], "checked_at": ev["checked_at"], "factory": FACTORY,
        "switch_block": SWITCH_BLOCK, "switch_at": SWITCH_AT, "deposit_receivers_scanned": receivers,
        "wallets": rows, "reports": [report_meta(*r) for r in REPORTS],
        "rescued": rescued_summary(rescued, today), "backlog": daily_backlog(rescued, rows, today),
        "batch": batch_info(known),
    }
    big = data["rescued"]["big_days"]
    data["stuck_funded_after_last_cleanup"] = sum(1 for r in rows if big and r["first_deposit_at"][:10] > big[-1]["day"])
    json.dump(data, open("data/data.json", "w"), indent=1)
    json.dump(rescued, open("data/rescued.json", "w"), indent=1)

    note = f"Read from Polygon block {ev['checked_at_block']} ({ev['checked_at']}). Wallets rescued after that time are still listed."
    json.dump({"checked_at_block": ev["checked_at_block"], "checked_at": ev["checked_at"], "note": note, "factory": FACTORY,
               "fix": "deployLegacy(owners, ids) with ids[i] = bytes32(owners[i]); then check eth_getCode(wallet) != 0x",
               "wallets": [{"wallet": r["wallet"], "balance": r["balance"], "token": r["token"], "polymarket_user_ids": r["polymarket_user_ids"],
                            "owner": r.get("owner"), "account_created": r.get("profile_created"), "first_deposit_at": r["first_deposit_at"], "stuck_since": r["since"],
                            "first_deposit_tx": r["deposits"][0]["tx"] if r["deposits"] else None} for r in rows]},
              open("data/stuck-wallets.json", "w"), indent=1)
    with open("data/stuck-wallets.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["wallet", "balance", "token", "polymarket_user_ids", "owner", "account_created", "first_deposit_at", "stuck_since", "first_deposit_tx", "checked_at", "checked_at_block"])
        for r in rows:
            w.writerow([r["wallet"], r["balance"], r["token"], " ".join(r["polymarket_user_ids"]), r.get("owner") or "", r.get("profile_created") or "",
                        r["first_deposit_at"], r["since"], (r["deposits"][0].get("tx") or "") if r["deposits"] else "", ev["checked_at"], ev["checked_at_block"]])
    print(f"stuck {len(rows)} wallets ${sum(r['balance'] for r in rows):,.2f}; rescued {len(rescued)} ${data['rescued']['total']:,.2f}; known owners {len(known)}")


if __name__ == "__main__":
    main()
