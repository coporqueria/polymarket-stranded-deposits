# Polymarket stranded deposits

Since June 29, 2026 (block 89,353,733), Polymarket's deposit wallet factory only creates new-type wallets on its normal path. Accounts created before then still show their old wallet address, and money sent there sits at an address with no contract until Polymarket calls `deployLegacy` on the factory. This repository measures that: who is stuck right now, and how long the people Polymarket has freed had to wait.

`index.html` only shows what is in `data/`. Its "Check every wallet live" button re-reads every stuck wallet straight from a public Polygon node.

## Data

- `data/stuck-wallets.csv` and `data/stuck-wallets.json`: every stuck wallet, with balance, Polymarket user ID, account creation date, first deposit, and the block and time of the snapshot on every row.
- `data/rescued.json`: every wallet freed since the switch, with the `deployLegacy` transaction that freed it and its first deposit.
- `data/data.json`: everything the page draws.

## Reproduce

Python 3 standard library only, against free public Polygon nodes. The scans take hours because the free nodes rate-limit.

```
python3 scan/scan.py 84902320 <latest block> out             # every pUSD deposit since pUSD launched, then stuck candidates
python3 scan/enrich.py out                                     # re-check at one block, Polymarket profile, first deposit
python3 scan/factory_scan.py 84900000 <latest block> out/factory.json
cd out && python3 ../scan/fixed.py && python3 ../scan/rescued2.py 86272144 && cd ..
PYTHONPATH=scan python3 scan/build.py out                      # writes data/
```

- `scan.py` keeps a deposit receiver when it holds pUSD, has no contract code, has never sent a transaction, and Polymarket's profile service lists it as an account's wallet.
- `build.py` then keeps only accounts created before the switch, adds wallets their owners reported publicly (checking that the factory's `predictLegacyWalletAddress(owner)` returns that exact address), and simulates the batch `deployLegacy` call for the known owners.
- `factory_scan.py`, `fixed.py` and `rescued2.py` list every old-type wallet the factory created after the switch, keep those that already held pUSD, USDC or USDC.e one block before, and date each one's first deposit. A wallet with no contract can't send anything, so its balance only grows, and the first funded block is found by binary search on historical balances.

## Known limits

The stuck list is a floor. A wallet funded without a pUSD deposit, or not linked to a public Polymarket profile, only appears if its owner reported it. Waits are counted from the later of the first deposit and the switch.
