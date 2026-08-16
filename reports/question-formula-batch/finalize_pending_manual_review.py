#!/usr/bin/env python3
"""Persist the human decisions for the final 216 formula review candidates."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
EXPECTED_PENDING = 216

# Only entries whose automatic candidate differed from the source image are listed
# here. Every other pending entry was compared against its source and confirmed.
CORRECTIONS = {
    "qi_00ed9269052bcd7c137cb494": r"\text{期望}=\text{概率}\times\text{次数}",
    "qi_12ff4ce6f6abcca152bdeadc": r"\frac{98.0-6.3}{6.3}=\frac{91.7}{6.3}<15\text{倍}",
    "qi_1cd6cb3ff63dfac84332b3ba": r"=r_1+r_2+r_1\times r_2=2.9\%+9.3\%+2.9\%\times9.3\%\approx12.2\%+0.3\%=12.5\%",
    "qi_1f02cb22c631558a6b2e8c48": r"=[(1+50\%)V_{\text{甲}}+V_{\text{乙}}]\times(x-2)",
    "qi_272d3bcd110ac5db88544b45": r"(1+10\%)\times\text{2015武汉A网站无重复目标人数}",
    "qi_35b0331202a7484d65142821": r"\frac{1}{16}=2^{-4}",
    "qi_4985e8ff2ad3c90522d47435": r"\frac{325.4-304.7}{304.7}\approx\frac{20.7}{305}\approx6\%^{+}",
    "qi_4d140045a6e0ef60ff106a07": r"\frac{1}{1+2+3+\cdots+2012}+\frac{1}{1+2+3+\cdots+2013}=\frac{1}{\frac{(1+2012)2012}{2}}+\frac{1}{\frac{(1+2013)2013}{2}}=\frac{1}{2013\times1006}+\frac{1}{2013\times1007}=\frac{1006+1007}{2013\times1006\times1007}=\frac{1}{1006\times1007}",
    "qi_59b68196ee93664d6a2b39a2": r"V_{\text{乙}}'=4\times(1-10\%)=3.6",
    "qi_5ae4de22f1e8a160ddc58310": r"=1-P_{\text{3个选送节目均不来自小张所在省的概率}}",
    "qi_6be5c8887dcdcdc749ac82a8": r"\text{至少有一个黑子情况数}=\text{总情况数}-\text{没有黑子情况数}=C_{12}^{3}-C_{8}^{3}=220-56=164",
    "qi_6ff91566d5d67d8e9ebc0211": r"r_{\text{间}}=r_1+r_2",
    "qi_792bdd3ed3512f79eece1047": r"S=(V_{\text{王}}+V_{\text{李}})\times30=(4+5)\times30=270",
    "qi_7b94da5adc25432de361cb42": r"=\frac{\text{部分}}{\text{整体}}=\frac{195.32}{195.32+116.70}",
    "qi_82be32d953fe59221d0373cb": r"\frac{853-511}{511}\approx\frac{342}{510}=60\%^{+}",
    "qi_839fd923569f04e864096bca": r"\frac{108.0-92.9}{92.9}\approx\frac{15.1}{93}=16\%^{+}",
    "qi_95ca6f3912e63cfd344bf477": r"(1+12.3\%)^2\approx1.261=1+r_{\text{间}}",
    "qi_a499d3026160604c156d6991": r"a_5",
    "qi_bfb9f50972552bb0cc897b47": r"=(134735-134091)\times9\%+(134735+134091)\times0.1\%",
    "qi_d0ddf5b25a11a6b0ef180e1c": r"\text{原式}\approx1\div30-108\div3600\approx0.003",
    "qi_d726c659e94c74c17d0ed6e1": r"12.95\text{‰}-10.48\text{‰}=2.47\text{‰}",
    "qi_df3df61ddd747c71b5e83c1f": r"\frac{68.15-64.56}{64.56}\approx\frac{3.59}{64.6}=5\%^{+}",
    "qi_e3cc005ff2eb977dc1f17e84": r"\frac{58.22-57.04}{57.04}\approx\frac{1.18}{57}=2\%^{+}",
    "qi_e67a5876ebefc493fd44eff5": r"=(V_{\text{甲}}+V_{\text{乙}})\times x",
    "qi_ed5e944b0acb0754e400da16": r"\frac{70\text{人}}{100\text{人}}=70\%",
    "qi_f438eace034e7f179ff7367e": r"\left|\frac{-278}{3423+278}\right|=\frac{278}{3701}\approx\frac{278}{3700}=7\%^{+}",
    "qi_f814d0f01e43bf6be07da7c3": r"\frac{27}{417-27}=\frac{27}{390}=6\%^{+}",
    "qi_f976f80b9ab82ad1a215b0b2": r"\text{则}\ \frac{a-b}{1+b}=\frac{-23.7\%-(-22.7\%)}{1+(-22.7\%)}",
    "qi_fc65e847e12fbbdba5edfe53": r"\frac{79.49-74.96}{74.96}\approx\frac{4.53}{75}=6\%^{+}",
    "qi_fcea8b78087e53a62e17bdd3": r"39\times(1+1.5+2.5)+5\times2=205\text{米}",
    "qi_ff99538cdc845944e9df51d4": r"6\times(V_{\text{甲}}+V_{\text{乙}})=6\times32=192\text{km}",
}


def main() -> None:
    pending = {}
    for path in sorted((ROOT / "results").glob("*.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        if result.get("status") == "needs_manual_review":
            pending[result["asset_id"]] = result["blocks"][0]["expression"].strip()

    if len(pending) != EXPECTED_PENDING:
        raise RuntimeError(f"expected {EXPECTED_PENDING} pending formulas, found {len(pending)}")
    unknown = sorted(set(CORRECTIONS) - set(pending))
    if unknown:
        raise RuntimeError(f"correction ids are not pending: {unknown}")

    assets = {asset_id: CORRECTIONS.get(asset_id, latex) for asset_id, latex in pending.items()}
    output = ROOT / "manual-pending-overrides.json"
    output.write_text(
        json.dumps(
            {
                "schema_version": "formula-manual-pending-overrides-v1",
                "reviewed_count": len(assets),
                "corrected_count": len(CORRECTIONS),
                "assets": assets,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"reviewed": len(assets), "corrected": len(CORRECTIONS), "output": str(output)},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
