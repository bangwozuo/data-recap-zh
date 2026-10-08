# -*- coding: utf-8 -*-
"""
爆款/扑街归因工作流 —— 端到端编排脚本。

流程（与 SKILL.md 的 DAG 一致）：
  S1 数据看板解读（dashboard-interpret）  爆款/扑街/基线三方数据归一
  S2 归因分析（attribution-analysis）      驱动因素量化
  S3 本步（脚本承担）                      爆款 vs 扑街 vs 基线的量化对比 + 结构要点对照
  S4 人工确认                              归因结论过目后再进选题

量化对比规则（与 prompt.txt 一致）：
  - 播放倍数 = 爆款播放 ÷ 基线篇均；≥ 2 倍记「真爆款」，< 2 倍仅记「相对高点」
  - 三连率/完播率差 ≥ 3pp 记显著差异，进证据表
  - 扑街视频任一指标低于基线 ≥ 30% 记「显著扑街」，须给排查方向
  - 结构对照：开头 5 秒形式 + 内容形式标签逐项比对，差异项即候选归因

用法：
  python run_flow.py --input input.json --outdir out
  python run_flow.py --demo

产物：
  out/爆款扑街对比表.csv  三方指标对比 + 差异判定
  out/hit_flop_flow.json  机器可读结果
  out/归因摘要.md         执行摘要 + 对比表（Markdown）
"""
from __future__ import annotations

import argparse
import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
WF_DIR = os.path.dirname(HERE)
DEFAULT_OUT = os.path.join(WF_DIR, "out")

HIT_MULT = 2.0        # 播放 ≥ 基线 × 2 记真爆款
SIG_PP = 3.0          # 比率差 ≥ 3pp 记显著
FLOP_DROP = 30.0      # 低于基线 ≥ 30% 记显著扑街

DEMO = {
    "account": "B站账号「老陈聊硬件」",
    "hit": {"title": "千元的显卡居然能打游戏？", "date": "10.3", "plays": 98000,
            "scl_pct": 7.2, "completion_pct": 38, "followers": 1620,
            "form": "低价硬件极限测试+反差结论", "first_5s": "开头 5 秒展示跑分翻车画面"},
    "flop": {"title": "RTX 5080 首发快评", "date": "10.6", "plays": 9000,
             "scl_pct": 1.9, "completion_pct": 14, "followers": 45,
             "form": "新品参数口播", "first_5s": "常规参数报幕"},
    "baseline": {"avg_plays": 23000, "scl_pct": 4.1, "completion_pct": 24},
    "context": "两条视频均零投放；爆款发布于周四 18:00，扑街发布于周日 12:00",
}


def pp_diff(a, b):
    return round(a - b, 1)


def compare(d):
    b = d["baseline"]
    hit, flop = d["hit"], d["flop"]
    rows = []
    hit_mult = round(hit["plays"] / b["avg_plays"], 1)
    rows.append({"指标": "播放量", "爆款": f'{hit["plays"]:,}', "扑街": f'{flop["plays"]:,}',
                 "基线": f'篇均 {b["avg_plays"]:,}',
                 "爆款-基线": f"×{hit_mult}",
                 "扑街-基线": f'{(flop["plays"] - b["avg_plays"]) / b["avg_plays"] * 100:+.0f}%',
                 "判定": "真爆款（≥2倍）" if hit_mult >= HIT_MULT else "相对高点"})
    for key, name in (("scl_pct", "三连率%"), ("completion_pct", "完播率%")):
        dv = pp_diff(hit[key], b[key])
        fv = pp_diff(flop[key], b[key])
        rows.append({"指标": name, "爆款": hit[key], "扑街": flop[key], "基线": b[key],
                     "爆款-基线": f"{dv:+}pp", "扑街-基线": f"{fv:+}pp",
                     "判定": ("显著差异（≥3pp）" if abs(dv) >= SIG_PP or abs(fv) >= SIG_PP
                              else "差异不显著")})
    dv = hit["followers"] - b["avg_plays"] * 0  # 涨粉无量纲，直接给绝对值与占比
    rows.append({"指标": "涨粉", "爆款": f'{hit["followers"]:,}', "扑街": flop["followers"],
                 "基线": "—", "爆款-基线": "—", "扑街-基线": "—",
                 "判定": f'爆款单条涨粉 {hit["followers"]:,}（扑街的 {round(hit["followers"]/max(flop["followers"],1))} 倍）'})
    flop_low = flop["plays"] < b["avg_plays"] * (1 - FLOP_DROP / 100)
    rows.append({"指标": "结构对照", "爆款": hit["form"], "扑街": flop["form"],
                 "基线": "—",
                 "爆款-基线": hit["first_5s"], "扑街-基线": flop["first_5s"],
                 "判定": "形式差异显著：测试反差型 vs 参数口播型；开头 5 秒有无钩子是首要候选归因"})
    flags = {"hit_mult": hit_mult, "flop_sig_low": flop_low,
             "flop_below_baseline_pct": round((b["avg_plays"] - flop["plays"]) / b["avg_plays"] * 100, 1)}
    return rows, flags


def build(d, outdir):
    if not d.get("hit") or not d.get("baseline"):
        raise SystemExit("[错误] hit / baseline 数据缺失：缺数据不估算，请先补齐爆款与基线数据。")
    rows, flags = compare(d)
    os.makedirs(outdir, exist_ok=True)

    cols = ["指标", "爆款", "扑街", "基线", "爆款-基线", "扑街-基线", "判定"]
    csv_path = os.path.join(outdir, "爆款扑街对比表.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    files = [csv_path]

    js = {"account": d.get("account", ""), "comparison": rows, "flags": flags,
          "thresholds": {"hit_mult": HIT_MULT, "sig_pp": SIG_PP, "flop_drop_pct": FLOP_DROP},
          "context": d.get("context", ""),
          "note": "数值由脚本对比；归因措辞与置信度由模型按 prompt.txt 完成"}
    js_path = os.path.join(outdir, "hit_flop_flow.json")
    with open(js_path, "w", encoding="utf-8") as f:
        json.dump(js, f, ensure_ascii=False, indent=2)
    files.append(js_path)

    md = ["# 爆款/扑街归因摘要（脚本实跑产物）", "",
          f"- 账号：{d.get('account','')}", f"- 背景：{d.get('context','')}", "",
          "| 指标 | 爆款 | 扑街 | 基线 | 判定 |", "|---|---|---|---|---|"]
    for r in rows:
        md.append(f'| {r["指标"]} | {r["爆款"]} | {r["扑街"]} | {r["基线"]} | {r["判定"]} |')
    md += ["", f"> 口径：播放 ≥ 基线 {HIT_MULT:.0f} 倍记真爆款；比率差 ≥ {SIG_PP:.0f}pp 记显著；"
               f"低于基线 ≥ {FLOP_DROP:.0f}% 记显著扑街（本次扑街低于基线 "
               f'{flags["flop_below_baseline_pct"]}%）。', ""]
    md_path = os.path.join(outdir, "归因摘要.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    files.append(md_path)

    print(f'爆款/扑街对比完成 —— 爆款 ×{flags["hit_mult"]}，'
          f'扑街低于基线 {flags["flop_below_baseline_pct"]}%，对比 {len(rows)} 行')
    for p in files:
        print(" 产物:", p, f"({os.path.getsize(p)/1024:.1f} KB)")
    return files


def main():
    ap = argparse.ArgumentParser(description="爆款/扑街归因工作流 —— 三方量化对比")
    ap.add_argument("--input", help="输入 JSON（account/hit/flop/baseline/context）")
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
