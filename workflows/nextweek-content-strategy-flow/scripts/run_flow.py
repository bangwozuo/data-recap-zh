# -*- coding: utf-8 -*-
"""
下周内容策略建议工作流 —— 端到端编排脚本。

流程（与 SKILL.md 的 DAG 一致）：
  S1 策略建议生成（strategy-advice-generate）  目标拆解 + 验证线
  S2 选题知识库（topic-knowledge-base）         选题计划写回字段映射
  S3 本步（脚本承担）                           上周策略验证 + 目标缺口拆解 + 选题计划表
  S4 人工确认                                   选题计划确认后再写回选题库

量化规则（与 prompt.txt 一致）：
  - 策略验证：上周实际单条涨粉 ≥ 验证线（默认 1,500）记「复现成功」，否则记「未过线」并触发回退
  - 缺口拆解：缺口 = 周均目标 − 当前周均；按「条数 × 单条验证线」校验可达性，
    不足的缺口须给出加密条数或提升单条上限的动作
  - 选题配比：已验证形式 ≥ 60%（不少于 2/3 条），新形式 ≤ 1 条/周（小步试错）

用法：
  python run_flow.py --input input.json --outdir out
  python run_flow.py --demo

产物：
  out/策略验证表.csv    上周动作 × 验证线 × 实际 × 判定
  out/选题计划表.csv    未来两周选题 × 形式 × 发布日 × 负责人
  out/nextweek_flow.json 机器可读结果
  out/策略摘要.md       执行摘要（Markdown）
"""
from __future__ import annotations

import argparse
import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
WF_DIR = os.path.dirname(HERE)
DEFAULT_OUT = os.path.join(WF_DIR, "out")

DEFAULT_VERIFY_LINE = 1500   # 单条涨粉验证线
PROVEN_MIN_SHARE = 60.0      # 已验证形式条数占比 ≥ 60%
WEEKS_AHEAD = 2              # 选题计划排 2 周

DEMO = {
    "account": "抖音账号「职场老王说」",
    "period": "10.5-10.11 复盘",
    "goal": {"weekly_target": 9000, "weekly_actual": 5005},
    "verified": [{"action": "「情境对话+字幕卡点」新形式话术视频",
                  "verify_line": 1500, "videos": 3,
                  "avg_gain": 1540, "actual_total": 4620,
                  "note": "无话题页收录、零投放，效果复现"}],
    "others": [{"action": "行业观察（常规形式）", "videos": 1, "gain": 385}],
    "resources": "主理人 1 人每周约 20 小时，外包剪辑预算 800元/周",
    "proven_form": "情境对话+字幕卡点",
    "plan": [
        {"week": "第1周", "title": "领导问『忙不忙』怎么接", "form": "情境对话+字幕卡点",
         "date": "周二 18:00", "owner": "主理人"},
        {"week": "第1周", "title": "同事甩锅三句反杀", "form": "情境对话+字幕卡点",
         "date": "周四 18:00", "owner": "主理人"},
        {"week": "第1周", "title": "周报里的升职暗语", "form": "情境对话+字幕卡点",
         "date": "周六 12:00", "owner": "主理人"},
        {"week": "第2周", "title": "开会坐哪个位置有讲究", "form": "情境对话+字幕卡点",
         "date": "周二 18:00", "owner": "主理人"},
        {"week": "第2周", "title": "领导朋友圈点赞潜规则", "form": "情境对话+字幕卡点",
         "date": "周四 18:00", "owner": "主理人"},
        {"week": "第2周", "title": "跨部门要资源话术（新形式试水）", "form": "白板讲解（试水）",
         "date": "周六 12:00", "owner": "主理人"},
    ],
}


def pct(a, b):
    return round((a - b) / b * 100, 1) if b else None


def build(d, outdir):
    goal = d.get("goal") or {}
    target, actual = goal.get("weekly_target", 0), goal.get("weekly_actual", 0)
    if not target:
        raise SystemExit("[错误] goal.weekly_target 缺失：无目标不拆解，请先补齐目标数据。")
    gap = target - actual
    gap_pct = pct(target, actual)

    # S3-1 策略验证
    vrows = []
    for v in d.get("verified", []):
        ok = v["avg_gain"] >= v["verify_line"]
        vrows.append({"上周动作": v["action"], "验证线": f'单条涨粉 ≥ {v["verify_line"]:,}',
                      "实际": f'单条均值 {v["avg_gain"]:,} / 合计 {v["actual_total"]:,}',
                      "判定": "复现成功（过线）" if ok else "未过线，触发回退",
                      "说明": v.get("note", "")})
    for o in d.get("others", []):
        vrows.append({"上周动作": o["action"], "验证线": "—（常规形式不设线）",
                      "实际": f'{o["videos"]} 条涨粉 {o["gain"]:,}',
                      "判定": "维持", "说明": ""})

    # S3-2 缺口拆解
    proven_total = sum(v["actual_total"] for v in d.get("verified", []))
    proven_n = sum(v["videos"] for v in d.get("verified", []))
    per_video = round(proven_total / proven_n) if proven_n else 0
    need_per_week = -(-gap // 1)  # 每周需补的涨粉
    extra_videos = max(0, -(-need_per_week // max(per_video, 1)) - 0)
    gap_rows = [
        {"项": "周均目标", "值": f"{target:,}"},
        {"项": "本周实际", "值": f"{actual:,}（{gap_pct:+}% vs 目标）"},
        {"项": "缺口", "值": f"{gap:,}/周"},
        {"项": "已验证形式单条产出", "值": f"{per_video:,}（{'≥' if per_video >= DEFAULT_VERIFY_LINE else '<'} 验证线 {DEFAULT_VERIFY_LINE:,}）"},
        {"项": "按 3 条/周外推", "值": f"约 {per_video * 3:,}（覆盖缺口的 {round(per_video * 3 / max(gap, 1) * 100)}%）"},
        {"项": "建议加密", "值": (f"话术 3→{3 + extra_videos} 条/周（人力允许时），"
                                  f"或把单条验证线提到 {int((gap / 3)):,} 再评估")},
    ]

    # S3-3 选题计划核对
    prows = []
    proven_cnt = sum(1 for p in d["plan"] if p["form"] == d.get("proven_form"))
    share = round(proven_cnt / len(d["plan"]) * 100, 1)
    for p in d["plan"]:
        prows.append({**p, "定位": "已验证形式" if p["form"] == d.get("proven_form") else "试水（≤1 条/周）"})
    plan_ok = share >= PROVEN_MIN_SHARE

    os.makedirs(outdir, exist_ok=True)
    files = []

    csv_path = os.path.join(outdir, "策略验证表.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["上周动作", "验证线", "实际", "判定", "说明"])
        w.writeheader()
        w.writerows(vrows)
    files.append(csv_path)

    csv2 = os.path.join(outdir, "选题计划表.csv")
    with open(csv2, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["week", "title", "form", "date", "owner", "定位"])
        w.writeheader()
        w.writerows(prows)
    files.append(csv2)

    js = {"account": d.get("account", ""), "period": d.get("period", ""),
          "verification": vrows, "gap": gap_rows, "plan": prows,
          "plan_share_proven_pct": share, "plan_ok": plan_ok,
          "thresholds": {"verify_line": DEFAULT_VERIFY_LINE,
                         "proven_min_share_pct": PROVEN_MIN_SHARE,
                         "weeks_ahead": WEEKS_AHEAD},
          "resources": d.get("resources", ""),
          "writeback_target": "飞书多维表格「内容选题库」选题池表（字段：标题/形式/计划发布/负责人/状态）",
          "note": "数值由脚本拆解；策略措辞与写回执行由模型/用户按 prompt.txt 完成"}
    js_path = os.path.join(outdir, "nextweek_flow.json")
    with open(js_path, "w", encoding="utf-8") as f:
        json.dump(js, f, ensure_ascii=False, indent=2)
    files.append(js_path)

    md = ["# 下周策略摘要（脚本实跑产物）", "",
          f"- 账号：{d.get('account','')}", f"- 依据：{d.get('period','')}", "",
          "## 策略验证", "",
          "| 上周动作 | 验证线 | 实际 | 判定 |", "|---|---|---|---|"]
    for r in vrows:
        md.append(f'| {r["上周动作"]} | {r["验证线"]} | {r["实际"]} | {r["判定"]} |')
    md += ["", "## 缺口拆解", "", "| 项 | 值 |", "|---|---|"]
    for r in gap_rows:
        md.append(f'| {r["项"]} | {r["值"]} |')
    md += ["", f"## 选题计划（已验证形式占比 {share}%，"
               f'{"达标 ≥60%" if plan_ok else "未达 60% 下限，需调整配比"}）', "",
           "| 周 | 选题 | 形式 | 计划发布 | 负责人 |", "|---|---|---|---|---|"]
    for p in prows:
        md.append(f'| {p["week"]} | {p["title"]} | {p["form"]} | {p["date"]} | {p["owner"]} |')
    md_path = os.path.join(outdir, "策略摘要.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    files.append(md_path)

    print(f"策略拆解完成 —— 缺口 {gap:,}/周，已验证形式占比 {share}%，"
          f"计划 {len(prows)} 条（{WEEKS_AHEAD} 周）")
    for p in files:
        print(" 产物:", p, f"({os.path.getsize(p)/1024:.1f} KB)")
    return files


def main():
    ap = argparse.ArgumentParser(description="下周内容策略工作流 —— 验证 + 缺口拆解 + 选题计划")
    ap.add_argument("--input", help="输入 JSON（account/period/goal/verified/others/plan...）")
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
