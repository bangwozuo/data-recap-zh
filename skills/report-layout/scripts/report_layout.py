# -*- coding: utf-8 -*-
"""
报告排版 —— 报告骨架与关键指标计算（确定性计算部分）。

职责边界（重要）：
  本脚本只做**确定性计算与产物落盘**：环比计算、系列排名、峰值/低谷识别、
  报告草稿（结论骨架 + 关键指标表）落盘。
  核心结论措辞、驱动因素的语境解释、行动建议的取舍，由模型按 prompt.txt 完成。

量化规则（与 prompt.txt 一致）：
  - 环比 |Δ| ≥ 10% 的指标必须进「关键指标」表并标方向
  - 系列篇均播放差距 ≥ 2 倍 → 高低档拉开，资源向高档倾斜
  - 单期峰值播放 ≥ 篇均 × 2 → 标注「爆款，建议复盘」
  - 单期最低播放 < 篇均 × 0.5 → 标注「低谷，建议排查」

用法：
  python report_layout.py --input input.json --outdir out
  python report_layout.py --demo --outdir out

产物：
  out/关键指标表.csv    指标 × 本期 × 上期 × 变化
  out/report.json       机器可读结果
  out/报告草稿.md       结论先行的报告骨架（Markdown 表格）
"""
from __future__ import annotations

import argparse
import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
DEFAULT_OUT = os.path.join(SKILL_DIR, "out")

# ---------------------------------------------------------------- 阈值（与 prompt.txt 一致）
SIGNIFICANT_MOM = 10.0     # 环比 ≥ ±10% 必须进关键指标表
SERIES_GAP = 2.0           # 系列篇均差距 ≥ 2 倍 → 拉开档位
HIT_MULT = 2.0             # 峰值 ≥ 篇均 × 2 → 爆款标注
LOW_MULT = 0.5             # 最低 < 篇均 × 0.5 → 低谷标注

# ---------------------------------------------------------------- 演示数据（源自 runs_v2 真实实跑输入）
DEMO = {
    "title": "B站账号「老陈聊硬件」2026年9月数据报告",
    "period": "2026年9月1日-9月30日",
    "metrics": [
        {"name": "总播放量", "cur": 386000, "prev": 291000, "unit": "次"},
        {"name": "充电计划收入", "cur": 1240, "prev": 980, "unit": "元"},
        {"name": "粉丝净增", "cur": 2150, "prev": 1430, "unit": "人"},
        {"name": "投稿数", "cur": 12, "prev": 11, "unit": "条"},
        {"name": "互动率(三连/播放)", "cur": 4.9, "prev": 4.2, "unit": "%"},
    ],
    "series": [
        {"name": "装机实录", "videos": 4, "avg_plays": 52000},
        {"name": "硬件避坑指南", "videos": 3, "avg_plays": 38000},
        {"name": "新品快评", "videos": 5, "avg_plays": 21000},
    ],
    "peak": {"title": "千元的显卡居然能打游戏？", "date": "9.16", "plays": 98000},
    "low": {"title": "新品快评（9.28）", "date": "9.28", "plays": 9000},
}


def mom(cur, prev, unit):
    if prev in (None, 0):
        return "—"
    d = round((cur - prev) / prev * 100, 1)
    if unit == "%":
        return f"{round(cur - prev, 1)}pp"
    return f"{d:+}%"


def compute(d):
    rows = []
    for m in d["metrics"]:
        if m["prev"]:
            pct = round((m["cur"] - m["prev"]) / m["prev"] * 100, 1)
        else:
            pct = None
        rows.append({**m, "mom_pct": pct,
                     "significant": pct is not None and abs(pct) >= SIGNIFICANT_MOM,
                     "mom_text": mom(m["cur"], m["prev"], m["unit"])})
    avgs = [s["avg_plays"] for s in d["series"]]
    top, bot = max(avgs), min(avgs)
    series_rows = []
    for s in sorted(d["series"], key=lambda x: -x["avg_plays"]):
        series_rows.append({**s, "share_of_top": round(s["avg_plays"] / top * 100, 1),
                            "tier": "高档" if top / max(s["avg_plays"], 1) <= SERIES_GAP
                            else ("中档" if s["avg_plays"] / max(bot, 1) > 1.5 else "低档")})
    overall_avg = sum(avgs) / len(avgs)
    flags = {
        "peak_hit": d["peak"]["plays"] >= overall_avg * HIT_MULT,
        "low_worry": d["low"]["plays"] < overall_avg * LOW_MULT,
        "series_gap_x": round(top / max(bot, 1), 1),
        "overall_avg_plays": int(overall_avg),
    }
    return {"metrics": rows, "series": series_rows, "flags": flags}


def write_outputs(res, d, outdir):
    os.makedirs(outdir, exist_ok=True)
    files = []

    csv_path = os.path.join(outdir, "关键指标表.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["指标", "本期", "上期", "变化", "是否显著(≥10%)"])
        for m in res["metrics"]:
            w.writerow([m["name"], f'{m["cur"]:,}{m["unit"]}', f'{m["prev"]:,}{m["unit"]}',
                        m["mom_text"], "是" if m["significant"] else "否"])
    files.append(csv_path)

    js = {"title": d["title"], "period": d["period"], "metrics": res["metrics"],
          "series": res["series"], "flags": res["flags"],
          "thresholds": {"significant_mom_pct": SIGNIFICANT_MOM, "series_gap_x": SERIES_GAP,
                         "hit_mult": HIT_MULT, "low_mult": LOW_MULT},
          "note": "数值均由本脚本计算；结论措辞与建议取舍由模型按 prompt.txt 完成"}
    js_path = os.path.join(outdir, "report.json")
    with open(js_path, "w", encoding="utf-8") as f:
        json.dump(js, f, ensure_ascii=False, indent=2)
    files.append(js_path)

    best = res["series"][0]
    md = [
        "# 报告草稿（脚本实跑骨架，结论措辞由模型润色）", "",
        f"- 报告：{d['title']}", f"- 周期：{d['period']}", "",
        "## 核心结论", "",
        f"（骨架）总播放 {d['metrics'][0]['cur']:,}（{res['metrics'][0]['mom_text']}），"
        f"粉丝净增 {d['metrics'][2]['cur']:,}（{res['metrics'][2]['mom_text']}）；"
        f"『{best['name']}』篇均 {best['avg_plays']:,} 领跑，系列间差距 {res['flags']['series_gap_x']} 倍。", "",
        "## 关键指标", "",
        "| 指标 | 本期 | 上期 | 变化 | 说明 |", "|---|---|---|---|---|",
    ]
    for m in res["metrics"]:
        note = "显著变化" if m["significant"] else ""
        md.append(f"| {m['name']} | {m['cur']:,}{m['unit']} | {m['prev']:,}{m['unit']} | "
                  f"{m['mom_text']} | {note} |")
    md += ["", "## 内容系列（篇均播放降序）", "",
           "| 系列 | 条数 | 篇均播放 | 占最高档 | 档位 |", "|---|---|---|---|---|"]
    for s in res["series"]:
        md.append(f"| {s['name']} | {s['videos']} | {s['avg_plays']:,} | "
                  f"{s['share_of_top']}% | {s['tier']} |")
    if res["flags"]["peak_hit"]:
        md += ["", f"- 峰值标注：{d['peak']['date']}《{d['peak']['title']}》"
               f"播放 {d['peak']['plays']:,}（≥ 篇均 {HIT_MULT:.0f} 倍）→ 爆款，建议复盘"]
    if res["flags"]["low_worry"]:
        md += [f"- 低谷标注：{d['low']['date']} {d['low']['title']} 播放 {d['low']['plays']:,}"
               f"（< 篇均 {LOW_MULT:.0f} 倍）→ 低谷，建议排查"]
    md_path = os.path.join(outdir, "报告草稿.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    files.append(md_path)
    return files


def main():
    ap = argparse.ArgumentParser(description="报告排版 —— 指标环比与报告骨架")
    ap.add_argument("--input", help="输入 JSON 路径")
    ap.add_argument("--outdir", default=DEFAULT_OUT, help="输出目录")
    ap.add_argument("--demo", action="store_true", help="用内置演示数据运行")
    a = ap.parse_args()

    d = DEMO if a.demo else (_read_json(a.input) if a.input else ap.error("需提供 --input 或 --demo"))
    if not d.get("metrics"):
        raise SystemExit("[错误] metrics 为空：缺数据不估算，请先补齐指标数据。")
    res = compute(d)
    files = write_outputs(res, d, a.outdir)
    sig = sum(1 for m in res["metrics"] if m["significant"])
    print(f"报告骨架完成 —— 指标 {len(res['metrics'])} 项（显著 {sig} 项），"
          f"系列档位差 {res['flags']['series_gap_x']} 倍")
    for p in files:
        print(" 产物:", p, f"({os.path.getsize(p)/1024:.1f} KB)")


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    main()
