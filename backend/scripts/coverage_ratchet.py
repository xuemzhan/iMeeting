"""后端覆盖率棘轮（只保证不倒退，不设绝对阈值）。

前端已经用同一套办法落地了两轮（`frontend/scripts/coverage-ratchet.mjs`）：直接设阈值
会让 CI 长期红灯，改成「比基线低超过容差才失败」。后端现在 81% 左右，同理不需要为了
凑某个数字去写无意义测试，只需要防止悄悄退化。

用法（在 backend/ 下）::

    python -m pytest -q --cov=imeeting --cov-report=json:coverage.json
    python scripts/coverage_ratchet.py            # 检查
    python scripts/coverage_ratchet.py --update   # 显式抬高门槛

退出码：0 通过 / 1 倒退 / 2 缺少输入。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SUMMARY = BACKEND_ROOT / "coverage.json"
BASELINE = BACKEND_ROOT / "coverage-baseline.json"

#: coverage.py 在不同解释器/平台上会有零点几个百分点的抖动，留一点余量。
TOLERANCE = 0.2


def _load(path: Path, hint: str) -> dict:
    if not path.exists():
        print(f"读不到 {path}：{hint}", file=sys.stderr)
        raise SystemExit(2)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - 报告损坏时给出人话
        print(f"{path} 不是合法 JSON：{exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def _worst_files(summary: dict, limit: int = 5) -> list[tuple[str, float, int]]:
    rows = []
    for name, info in summary.get("files", {}).items():
        stats = info.get("summary", {})
        rows.append((name, float(stats.get("percent_covered", 0.0)), int(stats.get("missing_lines", 0))))
    rows.sort(key=lambda row: (-row[2], row[1]))
    return rows[:limit]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--update", action="store_true", help="把当前覆盖率写成新基线")
    args = parser.parse_args(argv)

    summary = _load(SUMMARY, "请先跑 `python -m pytest -q --cov=imeeting --cov-report=json:coverage.json`")
    totals = summary.get("totals") or {}
    current = float(totals.get("percent_covered") or 0.0)
    statements = int(totals.get("num_statements") or 0)

    if args.update:
        payload = {
            "percent": round(current, 2),
            "statements": statements,
            "note": "后端覆盖率基线：低于此值（减去容差）CI 失败；抬高请重跑 --update",
        }
        BASELINE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已写入基线 {BASELINE}：{current:.2f}%（{statements} 条语句）")
        return 0

    baseline = _load(BASELINE, "还没有基线：先跑 `python scripts/coverage_ratchet.py --update`")
    floor = float(baseline.get("percent") or 0.0)
    delta = current - floor

    print("后端覆盖率棘轮（基线 -> 当前，下降超过 " + f"{TOLERANCE}" + " 个百分点即失败）")
    # 用 ASCII 标记：Windows 控制台默认 GBK，输出 ✓/✗ 会 UnicodeEncodeError。
    status = "FAIL" if delta < -TOLERANCE else "OK"
    print(f"  [{status}] 语句覆盖 {floor:.2f}% -> {current:.2f}%  ({delta:+.2f})")

    if delta < -TOLERANCE:
        print("\n缺口最大的文件（缺失行数 / 覆盖率）：")
        for name, percent, missing in _worst_files(summary):
            print(f"  {missing:5d} 行  {percent:5.1f}%  {name}")
        print("\n覆盖率出现倒退。要么补测试，要么在评审里说明并用 --update 明确下调基线。")
        return 1

    if delta > TOLERANCE:
        print("\n覆盖率高于基线；可以考虑跑 --update 把门槛抬上去。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
