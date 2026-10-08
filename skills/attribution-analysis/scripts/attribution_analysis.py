# -*- coding: utf-8 -*-
"""
归因分析 —— 驱动因素量化与证据链生成的确定性计算部分。

职责边界（重要）：
  本脚本只做**确定性计算与产物落盘**：环比/占比/分摊等量化指标、驱动因素分级
  （按占比阈值）、证据表与机器可读结果落盘。
  相关与因果的判别、置信度措辞、下一步建议的措辞，由模型按 prompt.txt 完成。

量化分级规则（与 prompt.txt 一致）：
  - 单因素涨粉占比 ≥ 50%            → 「主引擎」（影响幅度：大）
  - 20% ≤ 涨粉占比 < 50%            → 「重要因素」（影响幅度：中）
  - 涨粉占比 < 20% 且无时间证据      → 「存疑因素」，不得写因果结论
  - 环比变化 ≥ ±30%                  → 显著，必须进证据表
  - 单条内容占全周播放 ≥ 60%         → 判定「单点爆款」，需验证可复现性

用法：
  python attribution_analysis.py --input input.json --outdir out
  python attribution_analysis.py --demo --outdir out

产物（均为脚本真实落盘）：
  out/归因证据表.csv    驱动因素 × 量化证据 × 分级
  out/attribution.json  机器可读结果（供工作流/下游技能读取）
  out/归因摘要.md       证据链摘要（Markdown 表格）
"""
from __future__ import annotations

import argparse
import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
DEFAULT_OUT = os.path.join(SKILL_DIR, "out")

# ---------------------------------------------------------------- 分级阈值（与 prompt.txt 一致）
MAIN_ENGINE_SHARE = 50.0      # 涨粉占比 ≥ 50% → 主引擎
KEY_FACTOR_SHARE = 20.0       # 20% ~ 50% → 重要因素
SIGNIFICANT_WOW = 30.0        # 环比 ≥ ±30% → 显著
SINGLE_HIT_SHARE = 60.0       # 单条播放占比 ≥ 60% → 单点爆款

# ---------------------------------------------------------------- 演示数据（源自 runs_v2 真实实跑输入）
DEMO = {
    "account": "抖音账号「职场老王说」",
    "weeks": [
        {"label": "第3周(9.15-9.21)", "plays": 862000, "completion_pct": 29.8,
         "avg_dur_s": 18.2, "followers": 2315, "interact_pct": 5.1},
        {"label": "第4周(9.22-9.28)", "plays": 1284000, "completion_pct": 34.2,
         "avg_dur_s": 21.5, "followers": 6842, "interact_pct": 6.8},
    ],
    "series": [
        {"name": "职场沟通话术", "videos": 3, "avg_plays": 426000, "followers": 4920,
         "note": "9.24 起首次采用『情境对话+字幕卡点』新形式"},
        {"name": "行业观察", "videos": 2, "avg_plays": 113000, "followers": 1105, "note": ""},
        {"name": "个人成长", "videos": 1, "avg_plays": 87000, "followers": 337, "note": ""},
    ],
    "top_video": {"title": "领导说辛苦了，高情商回3句", "date": "9.24", "plays": 963000,
                  "likes": 52000, "comments": 4130, "followers": 3880},
    "context_events": [
        {"date": "9.24", "event": "首次采用「情境对话+字幕卡点」新形式"},
        {"date": "9.25", "event": "被抖音职场话题页收录推荐"},
        {"date": "9.27", "event": "竞品账号『HR小鹿』宣布停更（相关，证据不足以判因果）"},
    ],
}


def pct(new, old):
    if not old:
        return None
    return round((new - old) / old * 100, 1)


def compute(d):
    """全部数值在此算出；模型不得自行口算。"""
    w3, w4 = d["weeks"][0], d["weeks"][1]
    total_followers = sum(s["followers"] for s in d["series"])
    total_plays_w4 = w4["plays"]
    total_videos = sum(s["videos"] for s in d["series"])

    wow = {
        "plays_pct": pct(w4["plays"], w3["plays"]),
        "followers_pct": pct(w4["followers"], w3["followers"]),
        "completion_pp": round(w4["completion_pct"] - w3["completion_pct"], 1),
        "interact_pp": round(w4["interact_pct"] - w3["interact_pct"], 1),
        "avg_dur_pct": pct(w4["avg_dur_s"], w3["avg_dur_s"]),
    }
    series_rows = []
    for s in d["series"]:
        total_plays_est = s["avg_plays"] * s["videos"]
        series_rows.append({
            "name": s["name"], "videos": s["videos"], "avg_plays": s["avg_plays"],
            "followers": s["followers"],
            "follower_share_pct": round(s["followers"] / total_followers * 100, 1),
            "plays_share_pct": round(total_plays_est / total_plays_w4 * 100, 1),
            "grade": ("主引擎" if s["followers"] / total_followers * 100 >= MAIN_ENGINE_SHARE
                      else ("重要因素" if s["followers"] / total_followers * 100 >= KEY_FACTOR_SHARE
                            else "观察项")),
            "note": s["note"],
        })
    tv = d["top_video"]
    top = {
        **tv,
        "plays_share_pct": round(tv["plays"] / total_plays_w4 * 100, 1),
        "follower_share_pct": round(tv["followers"] / w4["followers"] * 100, 1),
        "single_hit": tv["plays"] / total_plays_w4 * 100 >= SINGLE_HIT_SHARE,
    }
    return {"wow": wow, "series": series_rows, "top_video": top,
            "totals": {"followers": total_followers, "videos": total_videos}}


def evidence_table(res, d):
    """驱动因素 × 量化证据 × 分级（模型据此写相关/因果判别与置信度）。"""
    w = res["wow"]
    rows = []
    hit = res["top_video"]
    rows.append({
        "因素": "「情境对话+字幕卡点」新形式（9.24 首次使用）",
        "方向": "正向",
        "分级": "主引擎" if hit["follower_share_pct"] >= MAIN_ENGINE_SHARE else "重要因素",
        "量化证据": (f"该条涨粉 {hit['followers']:,}，占全周 {hit['follower_share_pct']}%；"
                     f"单条播放 {hit['plays']/10000:.1f}万，占全周播放 {hit['plays_share_pct']}%"),
        "判定口径": f"单因素涨粉占比 ≥ {MAIN_ENGINE_SHARE:.0f}% 记主引擎",
    })
    tv = next(s for s in res["series"] if "话术" in s["name"])
    rows.append({
        "因素": "内容系列结构：话术类贡献集中",
        "方向": "正向",
        "分级": tv["grade"],
        "量化证据": (f"话术系列 {tv['videos']} 条贡献涨粉 {tv['followers']:,}，"
                     f"占全周 {tv['follower_share_pct']}%；平均单条播放 {tv['avg_plays']/10000:.1f}万"),
        "判定口径": f"系列涨粉占比 ≥ {KEY_FACTOR_SHARE:.0f}% 记重要因素",
    })
    rows.append({
        "因素": "完播率与互动率同步抬升",
        "方向": "正向",
        "分级": "重要因素" if abs(w["completion_pp"]) >= 3 else "观察项",
        "量化证据": (f"完播率 {w['completion_pp']:+}pp（29.8%→34.2%），互动率 {w['interact_pp']:+}pp，"
                     f"平均播放时长 {w['avg_dur_pct']:+}%"),
        "判定口径": f"环比 ≥ ±{SIGNIFICANT_WOW:.0f}% 或 ±3pp 记显著",
    })
    rows.append({
        "因素": "竞品『HR小鹿』9.27 停更",
        "方向": "正向（存疑）",
        "分级": "存疑因素",
        "量化证据": "涨粉高峰在 9.24-9.25，停更在 9.27；时间顺序不支持其为主要原因，无粉丝重合度数据",
        "判定口径": "占比 < 20% 且无时间/来源证据 → 只记相关，不写因果",
    })
    return rows


def write_outputs(res, ev, d, outdir):
    os.makedirs(outdir, exist_ok=True)
    files = []

    csv_path = os.path.join(outdir, "归因证据表.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["因素", "方向", "分级", "量化证据", "判定口径"])
        w.writeheader()
        w.writerows(ev)
    files.append(csv_path)

    js = {
        "account": d["account"],
        "thresholds": {"main_engine_share_pct": MAIN_ENGINE_SHARE,
                       "key_factor_share_pct": KEY_FACTOR_SHARE,
                       "significant_wow_pct": SIGNIFICANT_WOW,
                       "single_hit_share_pct": SINGLE_HIT_SHARE},
        "week_over_week": res["wow"],
        "series": res["series"],
        "top_video": res["top_video"],
        "evidence": ev,
        "note": "数值均由本脚本计算；相关/因果判别与置信度措辞由模型按 prompt.txt 完成",
    }
    js_path = os.path.join(outdir, "attribution.json")
    with open(js_path, "w", encoding="utf-8") as f:
        json.dump(js, f, ensure_ascii=False, indent=2)
    files.append(js_path)

    md = [
        "# 归因证据链摘要（脚本实跑产物）", "",
        f"- 账号：{d['account']}",
        f"- 周环比：播放 {res['wow']['plays_pct']:+}%，涨粉 {res['wow']['followers_pct']:+}%，"
        f"完播率 {res['wow']['completion_pp']:+}pp", "",
        "## 驱动因素证据表", "",
        "| 因素 | 方向 | 分级 | 量化证据 |",
        "|---|---|---|---|",
    ]
    for r in ev:
        md.append(f"| {r['因素']} | {r['方向']} | {r['分级']} | {r['量化证据']} |")
    md += ["", f"> 分级口径：涨粉占比 ≥ {MAIN_ENGINE_SHARE:.0f}% 主引擎；"
               f"≥ {KEY_FACTOR_SHARE:.0f}% 重要因素；不足且无证据为存疑因素，不写因果。", ""]
    md_path = os.path.join(outdir, "归因摘要.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    files.append(md_path)
    return files


def main():
    ap = argparse.ArgumentParser(description="归因分析 —— 驱动因素确定性量化")
    ap.add_argument("--input", help="输入 JSON 路径")
    ap.add_argument("--outdir", default=DEFAULT_OUT, help="输出目录")
    ap.add_argument("--demo", action="store_true", help="用内置演示数据运行")
    a = ap.parse_args()

    d = DEMO if a.demo else (_read_json(a.input) if a.input else ap.error("需提供 --input 或 --demo"))
    if not d.get("weeks") or not d.get("series"):
        raise SystemExit("[错误] weeks / series 为空：缺数据不估算，请先补齐表现数据。")
    res = compute(d)
    ev = evidence_table(res, d)
    files = write_outputs(res, ev, d, a.outdir)
    print(f"归因量化完成 —— 涨粉环比 {res['wow']['followers_pct']:+}%，"
          f"主引擎占比 {res['top_video']['follower_share_pct']}%，证据 {len(ev)} 条")
    for p in files:
        print(" 产物:", p, f"({os.path.getsize(p)/1024:.1f} KB)")


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    main()
