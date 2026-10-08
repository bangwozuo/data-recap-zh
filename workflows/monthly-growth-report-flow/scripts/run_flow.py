# -*- coding: utf-8 -*-
"""
月度成长报告工作流 —— 端到端编排脚本。

流程（与 SKILL.md 的 DAG 一致）：
  S1 报告排版（report-layout）             指标环比 + 系列排名
  S2 本步（脚本承担）                       月度环比计算 + 系列拆解 + 爆款标注 + 成长档案骨架
  S3 人工确认                              对外分享前终审（含版权/投诉事项核对）

量化规则（与 prompt.txt 一致）：
  - 环比 |Δ| ≥ 10% 的指标进「关键指标」表并标方向
  - 系列篇均差距 ≥ 2 倍写「档位拉开」；单篇阅读 ≥ 篇均 2 倍标「爆款」
  - 负面事项（投诉/删除）必须如实列入「风险记录」，不得删除或美化
  - 对外分享版必须带 AI 生成标识，且不含未公开的商业敏感数字（如收入明细可聚合为档位）

用法：
  python run_flow.py --input input.json --outdir out
  python run_flow.py --demo

产物：
  out/月度关键指标.csv   指标 × 本月 × 上月 × 变化
  out/monthly_flow.json  机器可读结果
  out/成长档案报告.md    可对外分享的成长档案骨架（Markdown 表格）
"""
from __future__ import annotations

import argparse
import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
WF_DIR = os.path.dirname(HERE)
DEFAULT_OUT = os.path.join(WF_DIR, "out")

SIGNIFICANT_MOM = 10.0   # 环比 ≥ ±10% 进关键指标表
SERIES_GAP = 2.0         # 系列篇均差 ≥ 2 倍
HIT_MULT = 2.0           # 单篇 ≥ 篇均 2 倍标爆款

DEMO = {
    "title": "公众号「市井食光」2026年10月成长档案",
    "period": "2026年10月",
    "metrics": [
        {"name": "粉丝净增", "cur": 1890, "prev": 1120, "unit": "人"},
        {"name": "篇均阅读", "cur": 4350, "prev": 3610, "unit": "次"},
        {"name": "在看+赞合计率", "cur": 3.8, "prev": 3.1, "unit": "%"},
        {"name": "流量主收入", "cur": 612, "prev": 485, "unit": "元"},
        {"name": "发文数", "cur": 9, "prev": 8, "unit": "篇"},
    ],
    "series": [
        {"name": "街坊小店探访", "videos": 4, "avg_reads": 5820},
        {"name": "家常菜谱", "videos": 3, "avg_reads": 3940},
        {"name": "外卖测评", "videos": 2, "avg_reads": 1980},
    ],
    "hit": {"title": "开了 23 年的肠粉店，老板娘只收现金", "date": "10.14",
            "reads": 16000, "followers": 610, "extra": "被 3 个本地生活号转载"},
    "risk_log": ["10.20 因图片版权问题被投诉 1 次，已删除处理（对外版保留此记录）"],
    "publish_note": "对外分享版收入类数字聚合为档位（如 500~1000元）",
}


def mom(cur, prev, unit):
    if unit == "%":
        return f"{round(cur - prev, 1)}pp"
    return f"{round((cur - prev) / prev * 100, 1):+}%" if prev else "—"


def build(d, outdir):
    if not d.get("metrics"):
        raise SystemExit("[错误] metrics 为空：缺数据不估算，请先补齐月度指标。")
    rows = []
    for m in d["metrics"]:
        pct = round((m["cur"] - m["prev"]) / m["prev"] * 100, 1) if m["prev"] else None
        rows.append({**m, "mom_text": mom(m["cur"], m["prev"], m["unit"]),
                     "significant": pct is not None and abs(pct) >= SIGNIFICANT_MOM})

    avgs = [s["avg_reads"] for s in d["series"]]
    top, bot = max(avgs), min(avgs)
    overall_avg = sum(avgs) / len(avgs)
    srows = []
    for s in sorted(d["series"], key=lambda x: -x["avg_reads"]):
        srows.append({**s, "share_of_top": round(s["avg_reads"] / top * 100, 1),
                      "tier": "高档" if top / max(s["avg_reads"], 1) <= SERIES_GAP else "低档"})

    hit = d.get("hit") or {}
    hit_on = bool(hit and hit.get("reads", 0) >= overall_avg * HIT_MULT)

    os.makedirs(outdir, exist_ok=True)
    files = []

    csv_path = os.path.join(outdir, "月度关键指标.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["指标", "本月", "上月", "变化", "是否显著(≥10%)"])
        for m in rows:
            w.writerow([m["name"], f'{m["cur"]:,}{m["unit"]}', f'{m["prev"]:,}{m["unit"]}',
                        m["mom_text"], "是" if m["significant"] else "否"])
    files.append(csv_path)

    js = {"title": d.get("title", ""), "period": d.get("period", ""),
          "metrics": rows, "series": srows,
          "flags": {"series_gap_x": round(top / max(bot, 1), 1), "hit": hit_on,
                    "overall_avg_reads": int(overall_avg)},
          "risk_log": d.get("risk_log", []),
          "publish_note": d.get("publish_note", ""),
          "thresholds": {"significant_mom_pct": SIGNIFICANT_MOM,
                         "series_gap_x": SERIES_GAP, "hit_mult": HIT_MULT},
          "note": "数值由脚本计算；成长叙事措辞由模型按 prompt.txt 完成"}
    js_path = os.path.join(outdir, "monthly_flow.json")
    with open(js_path, "w", encoding="utf-8") as f:
        json.dump(js, f, ensure_ascii=False, indent=2)
    files.append(js_path)

    md = [f"# {d.get('title','')}（脚本实跑骨架）", "",
          f"- 周期：{d.get('period','')}", f"- 对外口径：{d.get('publish_note','')}", "",
          "## 成长速览", "",
          "| 指标 | 本月 | 上月 | 变化 |", "|---|---|---|---|"]
    for m in rows:
        md.append(f'| {m["name"]} | {m["cur"]:,}{m["unit"]} | {m["prev"]:,}{m["unit"]} | {m["mom_text"]} |')
    md += ["", "## 内容系列（篇均阅读降序）", "",
           "| 系列 | 篇数 | 篇均阅读 | 占最高档 | 档位 |", "|---|---|---|---|---|"]
    for s in srows:
        md.append(f'| {s["name"]} | {s["videos"]} | {s["avg_reads"]:,} | {s["share_of_top"]}% | {s["tier"]} |')
    if hit_on:
        md += ["", f'## 月度爆款：{hit["date"]}《{hit["title"]}》', "",
               f'阅读 {hit["reads"]:,}（≥ 篇均 {HIT_MULT:.0f} 倍），涨粉 {hit["followers"]:,}，'
               f'{hit.get("extra","")}。复盘要点：小城人情故事 + 反常识细节。']
    if d.get("risk_log"):
        md += ["", "## 风险记录（如实保留）", ""]
        md += [f"- {r}" for r in d["risk_log"]]
    md += ["", "> AI 生成内容 · 需人工复核后对外分享", ""]
    md_path = os.path.join(outdir, "成长档案报告.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    files.append(md_path)

    sig = sum(1 for m in rows if m["significant"])
    print(f'月度档案完成 —— 指标 {len(rows)} 项（显著 {sig}），'
          f'系列档位差 {round(top/max(bot,1),1)} 倍，爆款标注 {"是" if hit_on else "否"}')
    for p in files:
        print(" 产物:", p, f"({os.path.getsize(p)/1024:.1f} KB)")
    return files


def main():
    ap = argparse.ArgumentParser(description="月度成长报告工作流 —— 环比 + 档案骨架")
    ap.add_argument("--input", help="输入 JSON（title/period/metrics/series/hit/risk_log...）")
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
