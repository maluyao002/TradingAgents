# NVDA terminal reader issue map (offline)

This workpaper maps the September 24 `reader_repair_run_3` terminal findings to
the frozen evidence. It is a repair specification, not an accepted report or
authorization for another live run. The exact candidate is
`stages/verify_revised_report-4-reader-candidate.json#/output/reader_text`,
SHA-256 `8bcebb1f3c5cdb2c2c0ccf5ee7a125f6bdf9caf167e70ed7981589d283600f3c`.
`reader_report.md` is a diagnostic placeholder. Verification failed at 198/206
required limitation IDs; `exported=false`.

Paths below are relative to the preserved
`reports/RESEARCH_SUBSTANTIVE_20260922/reader_repair_run_3/` packet. Offsets
are zero-based Python string slices into
`stages/evidence.json#/output/sources[id=...]/content`. Source hashes are in
the corresponding source entries. `case_context.json` selected passages are
separately identified where selection, rather than raw-source availability,
matters. “Not established” means not established by *these delivered passages*;
it never means the issuer did not disclose it. Any new passage must be
explicitly delivered and reviewed. The ordinary factual and atomic coverage
checks remain required on the exact rendered reader.

The terminal review lists 18 findings: eight substantive, one retained
invalid source witness, and nine limitation-disposition consequences
(including a missing/foreign ID pair). The source and ID defects need fresh
exact receipts; prose cannot hide them.

## Issue-by-issue map

| Finding and exact issue | Exact source passage / frozen record | Permitted claim | Required reader qualification |
| --- | --- | --- | --- |
| Proxy factual `verify_revised_report-4-proxy_disclosure_scope`; source `nvidia_fy2026_proxy_incentives_governance` | `case_context.json#/operating_scenarios/source_material[8:11]`: proxy `[134296,134584)` says 96% CEO and 48% other NEO target pay performance dependent; `[160715,161582)` describes preset H20 stretch adjustment; `[161583,162446)` lists revenue, non-GAAP operating income and relative TSR metrics. SHA begins `1ce2dd34`. | The *selected* extracts support pay dependence, metrics and the H20 adjustment. Raw proxy passages on vesting `[151700,153100)`, threshold/payout `[162447,164230)`, March certification `[165700,166650)` and the start of a compensation table `[185850,186500)` exist but were **not delivered for this claim**. | Replace the **entire second Management paragraph**, including its adjacent claims about earned outcomes, rules and their disclosure status: “The selected proxy passages establish performance-dependent target pay, incentive metrics and the preset H20 stretch-goal adjustment. They do not by themselves establish full threshold, payout, vesting, compensation-table or later certification details; those require separately supplied exact passages before use.” Achievement, earned outcome and vesting remain distinct concepts, but the selected extracts do not establish the specific outcomes. Do not claim the issuer lacks those other disclosures. |
| Batch 5 `verify_revised_report-4-coverage-5-working-capital-caveat`; `limitation-ade7efbbd067fb030d3c40c2ff5bf991fa2639c8b6b5df4e364ad8ac8478d5ff` | `case_context.json#/supplemental_source_material/passages[0]` = `nvda-q2-filing[29240,29700)`, which lists “Taxes payable 5,206” (USD millions) inside accrued/current liabilities. Filing `[7500,8230)` shows aggregate prepaid and accrued rows. The frozen issue text identifies possible tax/lease/non-operating content. | The aggregate input is mixed; the cited excerpt does not quantify lease content. | “The valuation `working_capital` input is an aggregate-row illustration, not pure operating working capital. Prepaid and accrued rows may contain tax, lease or other non-operating components that the frozen release does not disaggregate.” |
| Batch 12 `verify_revised_report-4-coverage-12_coreweave_exposure_gap`; `limitation-8dac6f10f0095ee5defb57051dabbef4d286ab4f3d6a259680688ec10bccaf2b` | `case_context.json#/operating_scenarios/source_material[11:14]`: CoreWeave `[200824,200968)` says all its infrastructure GPUs are NVIDIA GPUs under current contract obligations; `[177216,177610)` records NVIDIA's roughly $2B January 2026 private placement; `[174642,174826)` lists debt, equity, delayed draw, OEM financing and cash. SHA begins `121323a4`. | GPU dependence and financial link; no amount for NVIDIA sales, receivables, financing share, ultimate collections or independently funded utilization. | “The supplied CoreWeave passages show NVIDIA GPU dependence and NVIDIA equity financing, but do not quantify NVIDIA-to-CoreWeave sales or receivables, complete financing linkage, ultimate-customer collections or independently funded utilization.” |
| Batch 12 `verify_revised_report-4-coverage-12_account_bridge_gap`; `limitation-2ecd3dd81c31672fae2dc5bed9709f04059554a478434afc43743fdd12a7ed3b` | `nvda-q2-filing[12700,13100)` labels cash-flow movements “net of acquisitions” and lists the operating asset/liability rows. Reviewed `case_context.json#/cashflow_bridge/historical_anchor/reconciliation` computes bridge less CFO-minus-asset-purchases ≈ -$3.9504B and three mechanical components; `#/cashflow_bridge/review/limitations[2]` says balance-sheet proxy is narrower. | Source-statement arithmetic and algebraic decomposition only; no account-level attribution. | “The -$3.9504 billion residual is mechanical. An account-level reconciliation separating cash movements, noncash changes and acquisition or other scope differences is missing; this is not normalized or recoverable cash.” |
| `material_counterparty_exposure_caveat_incomplete`; `limitation-d7572fb464d27c0e39cc6d356b1d043ed4174c654ac66545a66a80655126ffff` | `nvda-q2-filing[61198,62418)` gives unnamed direct-customer shares (16% Q2; 16%, 15%, 13% H1) and distinguishes direct from indirect buyers. CoreWeave passages above show GPU use, NVIDIA investment and other funding. | Concentration and financial link separately. No traced investment-to-purchase flow, quantified recipient revenue/receivables, or ultimate payer. | “The retained passages do not quantify NVIDIA-wide revenue or receivables exposure to financially linked intermediaries, ultimate payers or investment recipients, including CoreWeave. Paid utilization, cash returns, independent funding, repeat orders and subsequent receipts remain unverified.” |
| `working_capital_proxy_caveat_incomplete`; `limitation-48a03eb52957e19ea82ed60497d295a4a5498f33f5b5b5c7e89ccc71cb613277` | Filing aggregate rows above. `case_context.json#/financial_case/schedules[id=schedule-operating_working_capital]` has `status=partial` and `prepaid-mixed.effect=unresolved`; `#/financial_case/gaps[id=gap-wc]` says two mixed rows remain unclassified and a reconciled operating value is blocked. | The partial financial-case schedule is separate from narrow cash-bridge and legacy aggregate valuation proxies. | “The legacy aggregate and narrow bridge proxies answer different questions. The separate financial-case working-capital schedule remains partial, with mixed prepaid and other-accrual rows unresolved. These are illustrations, not a complete operating-capital reconciliation.” May share prose with batch 5, but both IDs need new coverage receipts. |
| `historical_cash_measure_caveat_incomplete`; `limitation-592ed11355bea5bd4f5faeeff06b5f3dea1e4560b809b9e592de70ac336427c4` | `nvda-q2-release[10400,11150)` defines issuer FCF as CFO less asset purchases **and** principal payments. Reviewed `case_context.json#/cashflow_bridge/historical_anchor/reconciliation` has CFO-minus-purchases $69.987B, issuer FCF $69.895B, principal difference $92M. | The legacy `reported_free_cash_flow` field is the former arithmetic; issuer FCF is the latter. Neither alone proves future owner cash. | “CFO less asset purchases exceeds issuer-defined FCF by $92 million because the issuer also deducts asset-principal payments. Count them once. Neither historical measure alone establishes normalized, distributable or unlevered cash.” |
| Batch 18 `verify_revised_report-4-coverage-18-coreweave-sales-caveat`; `limitation-a810491682dd7495d42eb3b36053f2637477d59819cf49457e3d1b7ba50b52e9` | `nvda-q2-filing[61198,62418)` gives unnamed direct shares; `[87059,88536)` says indirect revenue is estimated from multiple inputs. CoreWeave passages above give no NVIDIA-to-CoreWeave sales amount. | Direct, estimated indirect and ultimate-payer exposures use different bases and cannot be added or relabeled as CoreWeave sales. | “The supplied disclosures do not quantify NVIDIA sales to CoreWeave or purchasing dependent on NVIDIA financing. Direct-customer concentration is distinct from estimated indirect and ultimate-payer exposure and cannot be added to them.” May share prose with other CoreWeave issues; retain all atomic IDs. |

## Controlled disclosure draft

The reviewed controlled disclosure draft is
`docs/archive/deep-research/DEEP-RESEARCH-NVDA-DISCLOSURE-PACKET.json`.
Its four paragraphs cover only the seven substantive missing coverage IDs;
they do not resolve the proxy factual warning or Q1 truncation ID. The packet
binds the source `case_context.json` byte hash. Three assertions also require
these exact frozen case selectors in factual review, beyond the raw issuer
passages carried as citation witnesses: the partial working-capital schedule
at `#/financial_case/schedules[id=schedule-operating_working_capital]` and
`#/financial_case/gaps[id=gap-wc]`; the negative bridge residual at
`#/cashflow_bridge/historical_anchor/reconciliation/bridge_minus_ocf_minus_capex`;
and the $92 million issuer-FCF difference at
`#/cashflow_bridge/historical_anchor/reconciliation/issuer_fcf_less_ocf_minus_capex`.
The packet's exact-source validator checks identity and passages; it does not
certify these interpretations.

## Exact-receipt defects

* `limitation-44cb422d0d658446383e391f03a80219cbd8ffb953124b69aed1a8ecd28ee2fd`
  is a historical-claim correction. Its source witness cites
  `nvda-q2-filing[70005,70905)` for “one direct customer,” but that slice is
  about Blackwell/Rubin supply. The concentration statement is at
  `[61198,62418)` or `[87059,88536)`; the saved planner claim itself names
  nearby correct offsets. Supply a corrected source witness and revalidated
  claim-change/lifecycle receipt.
  Do not rewrite historical responses or identify the unnamed customer.
* The true open Q1 truncation issue is
  `limitation-150213ee558b54e58ea197079e161a5ee78934ee6365d33a4e0139c2d733bb47`:
  only 3,750 of 26,657 source characters were retained. Coverage batch 8
  instead submitted foreign ID
  `limitation-150213ee558b54e58ea197079e161a5ee78934ee8d60820dae`.
  The Q1 release SHA begins `dcd80338`. A fresh atomic receipt must use the
  exact original ID and say analysis cannot rely on omitted release text.
  Never alias the IDs.

## Saved-candidate and negative-control checks

The saved candidate still asserts the overbroad proxy inventory. It describes
the narrow versus legacy working-capital proxies and computes the residual,
but omits the aggregate tax/lease caveat, partial financial-case schedule and
account-level bridge gap. It discusses CoreWeave financing and concentration
without expressly saying NVIDIA-to-CoreWeave sales and receivables are
unquantified. It distinguishes two historical cash measures but never says
neither establishes distributable or unlevered cash. The proposed sentences
fill specific gaps rather than restate existing paragraphs.

Reject any repaired candidate that (1) treats the raw, unselected proxy
passages as delivered; (2) gives a numeric NVIDIA-to-CoreWeave exposure or
names CoreWeave as an unnamed direct customer; (3) adds direct and indirect
percentages or traces investment proceeds to purchases; (4) treats the cash
residual as an account reconciliation or historical FCF as distributable cash;
(5) retires an issue merely because a controlled paragraph exists without a
fresh factual review and exact-ID coverage; or (6) reuses the wrong Q1 ID or
invalid concentration witness. An offline packet or rehearsal is not report
admission.

An offline assertion audit on September 24 verified the saved reader hash,
198/206 coverage counts, all eight missing IDs, five exact source spans and
their source hashes, the three selected proxy spans, the incorrect/correct
concentration witness contrast, the partial financial-case schedule, and the
$92 million difference between historical cash measures. Negative controls
confirmed that the selected CoreWeave excerpts supply no NVIDIA sales amount
and the saved candidate lacks the specified account-level and distributable
cash qualifications. The audit read preserved artifacts only; it made no
provider calls or historical-file changes.
