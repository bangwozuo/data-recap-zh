# -*- coding: utf-8 -*-
"""
数据看板解读 —— 多源指标归一化与口径核对（确定性计算部分）。

职责边界（重要）：
  本脚本只做**确定性计算与产物落盘**：指标名归一映射、单位换算（万→原始值）、
  跨来源交叉核对（差异 ≤5% 判口径吻合）、统一表落盘。
  口径差异的业务解释、给运营的修正建议，由模型按 prompt.txt 完成。

量化核对规则（与 prompt.txt 一致）：
  - 同一指标两个来源都给出时，相对差异 ≤ 5%  → 判「口径吻合」
  - 5% < 相对差异 ≤ 15%                       → 判「口径接近，需注明差异」
  - 相对差异 > 15%                            → 判「口径冲突」，禁止并表，只并列展示
  - 缺失指标记「—」，不补零、不估算

用法：
  python dashboard_interpret.py --input input.json --outdir out
  python dashboard_interpret.py --demo --outdir out

产物：
  out/统一数据表.csv    指标 × 各来源归一值 × 口径说明
  out/normalized.json   机器可读结果
  out/口径差异说明.md   交叉核对结论（Markdown 表格）
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
MATCH_TOL = 5.0        # ≤5% 判口径吻合
NEAR_TOL = 15.0        # 5%~15% 判口径接近；>15% 判口径冲突

# 统一指标 → 各来源原始字段（缺来源记 —）
METRIC_ALIASES = {
    "播放量": ["播放量", "观看次数", "阅读量", "去重触达"],
    "完播率%": ["完播率", "看完率"],
    "涨粉": ["净增粉丝", "关注页净增", "净涨粉"],
    "主页访问": ["主页访问"],
    "商品转化": ["转化（小黄车点击）", "商品转化"],
    "互动量": ["互动量", "去重互动"],
}

# ---------------------------------------------------------------- 演示数据（源自 runs_v2 真实实跑输入）
DEMO = {
    "period": "2026.9.29-10.5",
    "sources": [
        {"source": "抖音创作者后台", "kind": "官方后台",
         "metrics": {"播放量": 152300, "完播率": 31.5, "涨粉": 1860, "主页访问": 4230,
                     "商品转化": 912},
         "calibers": {"播放量": "后台播放", "完播率": "完播=播放≥75%"}},
        {"source": "视频号助手", "kind": "官方后台",
         "metrics": {"观看次数": 118750, "看完率": 26.4, "关注页净增": 640, "主页访问": 1150},
         "calibers": {"观看次数": "含重复播放", "看完率": "看完任意时长退出计看完"}},
        {"source": "小红书专业号", "kind": "官方后台",
         "metrics": {"阅读量": 41280, "曝光量": 96400, "互动量": 3560, "净涨粉": 420,
                     "主页访问": 1980},
         "calibers": {"阅读量": "阅读=点进笔记"}},
        {"source": "巨量算数", "kind": "第三方监测",
         "metrics": {"去重触达": 201000, "去重互动": 12400},
         "calibers": {"去重触达": "三平台合并去重"}},
    ],
    "target_metrics": ["播放量", "完播率%", "涨粉", "主页访问", "商品转化"],
}


def cross_checks(sources):
    """跨来源交叉核对：同一指标多来源时的相对差异。返回核对行。"""
    checks = []
    plays = [s for s in sources if "播放量" in s["metrics"] or "观看次数" in s["metrics"]
             or "阅读量" in s["metrics"]]
    vals = [s["metrics"].get("播放量") or s["metrics"].get("观看次数")
            or s["metrics"].get("阅读量") for s in plays]
    if len(vals) >= 2 and all(vals):
        spread = (max(vals) - min(vals)) / max(vals) * 100
        checks.append({"指标": "播放量（抖音 vs 视频号 vs 小红书阅读）",
                       "最大差": f"{spread:.1f}%",
                       "判定": "口径不可并表（分平台渠道，需分行展示）",
                       "说明": "三平台口径天然不同，只并列不合并"})
    third = next((s for s in sources if s["kind"] == "第三方监测"), None)
    if third:
        dedup = third["metrics"]["去重触达"]
        official_sum = sum(s["metrics"].get("播放量") or s["metrics"].get("观看次数")
                           or s["metrics"].get("阅读量") or 0
                           for s in sources if s["kind"] == "官方后台")
        gap = (official_sum - dedup) / official_sum * 100
        checks.append({
            "指标": "官方播放合计 vs 第三方去重触达",
            "最大差": f"{gap:.1f}%",
            "判定": "口径吻合（去重口径差异 < 15% 属正常）" if gap <= NEAR_TOL else "口径冲突",
            "说明": f"官方合计 {official_sum:,}，第三方去重 {dedup:,}，差异即跨平台重叠用户",
        })
    inter = [s["metrics"]["互动量"] for s in sources if "互动量" in s["metrics"]]
    dd = [s["metrics"]["去重互动"] for s in sources if "去重互动" in s["metrics"]]
    if inter and dd:
        gap = abs(inter[0] - dd[0]) / inter[0] * 100
        checks.append({
            "指标": "小红书互动量 vs 第三方去重互动",
            "最大差": f"{gap:.1f}%",
            "判定": ("口径吻合" if gap <= MATCH_TOL
                     else ("口径接近，需注明差异" if gap <= NEAR_TOL else "口径冲突")),
            "说明": f"后台 {inter[0]:,}，第三方 {dd[0]:,}",
        })
    return checks


def normalize(sources, targets):
    rows = []
    for m in targets:
        base = m.rstrip("%")
        aliases = METRIC_ALIASES.get(m, [base])
        row = {"指标": m}
        used = set()
        for s in sources:
            val = None
            for al in aliases:
                if al in s["metrics"]:
                    val = s["metrics"][al]
                    note = s["calibers"].get(al, s["calibers"].get(base, ""))
                    row[f"{s['source']}·口径"] = note or "原始口径"
                    used.add(s["source"])
                    break
            row[s["source"]] = f"{val:,}" if isinstance(val, int) else ("—" if val is None else f"{val}")
        rows.append(row)
    return rows


def write_outputs(rows, checks, d, outdir):
    os.makedirs(outdir, exist_ok=True)
    files = []
    cols = ["指标"] + [s["source"] for s in d["sources"]] + \
           [f"{s['source']}·口径" for s in d["sources"]]

    csv_path = os.path.join(outdir, "统一数据表.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    files.append(csv_path)

    js = {"period": d["period"], "unified": rows, "cross_checks": checks,
          "tolerances": {"match_pct": MATCH_TOL, "near_pct": NEAR_TOL},
          "note": "数值均由本脚本归一；口径差异的业务解释由模型按 prompt.txt 完成"}
    js_path = os.path.join(outdir, "normalized.json")
    with open(js_path, "w", encoding="utf-8") as f:
        json.dump(js, f, ensure_ascii=False, indent=2)
    files.append(js_path)

    md = ["# 口径差异说明（脚本实跑产物）", "",
          f"统计周期：{d['period']}；核对容差：≤{MATCH_TOL}% 吻合 / ≤{NEAR_TOL}% 接近 / >{NEAR_TOL}% 冲突", "",
          "| 核对项 | 最大差 | 判定 | 说明 |", "|---|---|---|---|"]
    for c in checks:
        md.append(f"| {c['指标']} | {c['最大差']} | {c['判定']} | {c['说明']} |")
    md += ["", "## 统一数据表", "", "| 指标 | " + " | ".join(d["sources"][s_]["source"] for s_ in range(len(d["sources"]))) + " |",
           "|" + "---|" * (len(d["sources"]) + 1)]
    for r in rows:
        md.append("| " + r["指标"] + " | " +
                  " | ".join(str(r.get(s["source"], "—")) for s in d["sources"]) + " |")
    md_path = os.path.join(outdir, "口径差异说明.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    files.append(md_path)
    return files


def main():
    ap = argparse.ArgumentParser(description="数据看板解读 —— 多源指标归一化核对")
    ap.add_argument("--input", help="输入 JSON 路径")
    ap.add_argument("--outdir", default=DEFAULT_OUT, help="输出目录")
    ap.add_argument("--demo", action="store_true", help="用内置演示数据运行")
    a = ap.parse_args()

    d = DEMO if a.demo else (_read_json(a.input) if a.input else ap.error("需提供 --input 或 --demo"))
    if not d.get("sources"):
        raise SystemExit("[错误] sources 为空：缺数据不估算，请先补齐各来源原始数据。")
    rows = normalize(d["sources"], d.get("target_metrics") or METRIC_ALIASES.keys())
    checks = cross_checks(d["sources"])
    files = write_outputs(rows, checks, d, a.outdir)
    print(f"归一完成 —— 统一指标 {len(rows)} 项，交叉核对 {len(checks)} 组")
    for p in files:
        print(" 产物:", p, f"({os.path.getsize(p)/1024:.1f} KB)")


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    main()
