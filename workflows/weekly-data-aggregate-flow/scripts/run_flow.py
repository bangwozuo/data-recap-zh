# -*- coding: utf-8 -*-
"""
周度数据汇总工作流 —— 端到端编排脚本。

流程（与 SKILL.md 的 DAG 一致）：
  S1 数据看板解读（dashboard-interpret）  多源数据归一 + 口径核对
  S2 本步（脚本承担）                      周度汇总：统一周表 + 交叉核对 + 质量结论
  S3 人工确认                              周会前过一遍口径差异

本脚本承担 S2 的确定性部分：指标归一映射（播放量/观看次数/阅读量 → 播放量）、
跨来源交叉核对（相对差异 ≤5% 吻合 / ≤15% 接近 / >15% 冲突）、
周度环比（有上期才算，缺失记「—」不补零）。

用法：
  python run_flow.py --input input.json --outdir out
  python run_flow.py --demo

产物：
  out/周度汇总表.csv    统一指标 × 各来源 × 口径说明
  out/weekly_flow.json  机器可读结果
  out/周报摘要.md       执行摘要 + 统一数据表（Markdown）
"""
from __future__ import annotations

import argparse
import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
WF_DIR = os.path.dirname(HERE)
DEFAULT_OUT = os.path.join(WF_DIR, "out")

MATCH_TOL = 5.0     # S1 归一核对容差：≤5% 吻合
NEAR_TOL = 15.0     # ≤15% 接近，>15% 冲突

DEMO = {
    "period": "2026.9.29-10.5",
    "account": "小红书账号「阿满的出租屋改造」",
    "prev": {"曝光": 71200, "阅读/播放": 30800, "互动": 3510, "涨粉": 640},
    "sources": [
        {"source": "小红书专业号后台", "kind": "官方后台",
         "metrics": {"曝光量": 87300, "阅读量": 36150, "互动量": 4280, "净涨粉": 780},
         "calibers": {"阅读量": "阅读=点进笔记"}},
        {"source": "巨量算数", "kind": "第三方监测",
         "metrics": {"去重触达": 88100, "去重互动": 4305},
         "calibers": {"去重触达": "仅小红书渠道去重"}},
    ],
    "target_metrics": ["曝光", "阅读/播放", "互动", "涨粉"],
}

ALIASES = {
    "曝光": ["曝光量", "去重触达"],
    "阅读/播放": ["阅读量", "播放量", "观看次数"],
    "互动": ["互动量", "去重互动"],
    "涨粉": ["净涨粉", "净增粉丝", "关注页净增"],
}


def pct(new, old):
    if not old:
        return None
    return round((new - old) / old * 100, 1)


def normalize(d):
    rows = []
    for m in d["target_metrics"]:
        row = {"指标": m, "本期": None, "口径": "", "来源": ""}
        for s in d["sources"]:
            for al in ALIASES.get(m, [m]):
                if al in s["metrics"]:
                    row["本期"] = s["metrics"][al]
                    row["口径"] = f'{s["source"]}·{s["calibers"].get(al, "原始口径")}'
                    row["来源"] = s["source"]
                    break
            if row["本期"] is not None:
                break
        prev = (d.get("prev") or {}).get(m)
        row["上期"] = prev
        row["环比"] = f'{pct(row["本期"], prev):+}%' if (row["本期"] is not None and prev) else "—（无上期或本期缺失）"
        rows.append(row)
    return rows


def cross_checks(d):
    checks = []
    bk = {s["source"]: s["metrics"] for s in d["sources"]}
    off = next((s["metrics"] for s in d["sources"] if s["kind"] == "官方后台"), {})
    third = next((s["metrics"] for s in d["sources"] if s["kind"] == "第三方监测"), {})
    if "曝光量" in off and "去重触达" in third:
        gap = abs(off["曝光量"] - third["去重触达"]) / off["曝光量"] * 100
        checks.append({"核对项": "后台曝光 vs 第三方去重触达", "差异": f"{gap:.1f}%",
                       "判定": ("口径吻合" if gap <= MATCH_TOL else
                                ("口径接近，需注明差异" if gap <= NEAR_TOL else "口径冲突")),
                       "说明": f'后台 {off["曝光量"]:,} vs 去重 {third["去重触达"]:,}'})
    if "互动量" in off and "去重互动" in third:
        gap = abs(off["互动量"] - third["去重互动"]) / off["互动量"] * 100
        checks.append({"核对项": "后台互动 vs 第三方去重互动", "差异": f"{gap:.1f}%",
                       "判定": ("口径吻合" if gap <= MATCH_TOL else
                                ("口径接近，需注明差异" if gap <= NEAR_TOL else "口径冲突")),
                       "说明": f'后台 {off["互动量"]:,} vs 去重 {third["去重互动"]:,}'})
    return checks


def build(d, outdir):
    if not d.get("sources"):
        raise SystemExit("[错误] sources 为空：缺数据不估算，请先补齐各来源周数据。")
    rows = normalize(d)
    checks = cross_checks(d)
    os.makedirs(outdir, exist_ok=True)

    cols = ["指标", "本期", "上期", "环比", "来源", "口径"]
    csv_path = os.path.join(outdir, "周度汇总表.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: (f"{r[c]:,}" if c in ("本期", "上期") and isinstance(r[c], int) else r[c])
                        for c in cols})
    files = [csv_path]

    conflicts = [c for c in checks if c["判定"] == "口径冲突"]
    js = {"account": d.get("account", ""), "period": d.get("period", ""),
          "unified": rows, "cross_checks": checks,
          "verdict": "存在口径冲突，需人工裁决" if conflicts else "口径核对通过",
          "tolerances": {"match_pct": MATCH_TOL, "near_pct": NEAR_TOL},
          "note": "数值由脚本归一；业务解读由模型按 prompt.txt 完成"}
    js_path = os.path.join(outdir, "weekly_flow.json")
    with open(js_path, "w", encoding="utf-8") as f:
        json.dump(js, f, ensure_ascii=False, indent=2)
    files.append(js_path)

    md = ["# 周报摘要（脚本实跑产物）", "",
          f"- 账号：{d.get('account','')}", f"- 周期：{d.get('period','')}",
          f"- 结论：{js['verdict']}", "",
          "| 指标 | 本期 | 上期 | 环比 | 口径说明 |", "|---|---|---|---|---|"]
    for r in rows:
        cur = f'{r["本期"]:,}' if isinstance(r["本期"], int) else (r["本期"] or "—")
        prev = f'{r["上期"]:,}' if isinstance(r["上期"], int) else (r["上期"] or "—")
        md.append(f'| {r["指标"]} | {cur} | {prev} | {r["环比"]} | {r["口径"]} |')
    md += ["", "## 交叉核对", "", "| 核对项 | 差异 | 判定 | 说明 |", "|---|---|---|---|"]
    for c in checks:
        md.append(f'| {c["核对项"]} | {c["差异"]} | {c["判定"]} | {c["说明"]} |')
    md_path = os.path.join(outdir, "周报摘要.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    files.append(md_path)

    print(f'周度汇总完成 —— 指标 {len(rows)} 项，核对 {len(checks)} 组：{js["verdict"]}')
    for p in files:
        print(" 产物:", p, f"({os.path.getsize(p)/1024:.1f} KB)")
    return files


def main():
    ap = argparse.ArgumentParser(description="周度数据汇总工作流 —— 归一 + 交叉核对")
    ap.add_argument("--input", help="输入 JSON（period/account/sources/target_metrics/prev）")
    ap.add_argument("--outdir", default=DEFAULT_OUT)
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    d = DEMO if a.demo else (_read_json(a.input) if a.input else ap.error("需要 --input 或 --demo"))
    build(d, a.outdir)


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    main()
