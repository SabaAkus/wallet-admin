# Assignment sample-data review

The 12 source rows were extracted from `data/Task.pdf` and manually compared against
the rendered PDF pages. Negative source amounts were normalized to positive minor-unit
inputs plus explicit `DEBIT`; all other displayed values were preserved.

| ID | Player | Country | Operator | Type | PDF amount | Direction | CSV amount | Currency | Status | Balance after | Date |
|---|---|---|---|---|---:|---|---:|---|---|---:|---|
| TX10001 | PLR1001 | Georgia | Viper Direct | DEPOSIT | 50.00 | CREDIT | 50.00 | EUR | APPROVED | 50.00 | 2026-08-20 10:15 |
| TX10002 | PLR1002 | Turkey | Operator A | DEPOSIT | 25.00 | CREDIT | 25.00 | EUR | APPROVED | 25.00 | 2026-08-20 10:22 |
| TX10003 | PLR1001 | Georgia | Viper Direct | GAME_ENTRY | -5.00 | DEBIT | 5.00 | EUR | APPROVED | 45.00 | 2026-08-20 10:30 |
| TX10004 | PLR1003 | Armenia | Operator B | DEPOSIT | 100.00 | CREDIT | 100.00 | EUR | PENDING | 0.00 | 2026-08-20 10:45 |
| TX10005 | PLR1001 | Georgia | Viper Direct | GAME_WIN | 18.50 | CREDIT | 18.50 | EUR | APPROVED | 63.50 | 2026-08-20 10:48 |
| TX10006 | PLR1004 | Spain | Operator A | WITHDRAWAL | -30.00 | DEBIT | 30.00 | EUR | APPROVED | 72.00 | 2026-08-20 11:05 |
| TX10007 | PLR1005 | Germany | Operator C | DEPOSIT | 75.00 | CREDIT | 75.00 | EUR | FAILED | 0.00 | 2026-08-20 11:20 |
| TX10008 | PLR1002 | Turkey | Operator A | GAME_ENTRY | -10.00 | DEBIT | 10.00 | EUR | APPROVED | 15.00 | 2026-08-20 11:31 |
| TX10009 | PLR1002 | Turkey | Operator A | GAME_WIN | 27.40 | CREDIT | 27.40 | EUR | APPROVED | 42.40 | 2026-08-20 11:35 |
| TX10010 | PLR1006 | Italy | Operator C | DEPOSIT | 40.00 | CREDIT | 40.00 | EUR | APPROVED | 40.00 | 2026-08-20 11:52 |
| TX10011 | PLR1003 | Armenia | Operator B | DEPOSIT | 100.00 | CREDIT | 100.00 | EUR | APPROVED | 100.00 | 2026-08-20 12:01 |
| TX10012 | PLR1003 | Armenia | Operator B | WITHDRAWAL | -60.00 | DEBIT | 60.00 | EUR | PENDING | 100.00 | 2026-08-20 12:20 |

The PDF supplies no timezone, so the CSV retains naive timestamps and the importer
reports that it interprets them as UTC. TX10012 is the latest PLR1003 row and is
pending; its unchanged balance-after value of EUR 100.00 is used as the wallet anchor
under the documented historical-snapshot assumption. It creates no posting.
