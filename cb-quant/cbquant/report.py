"""Render the end-of-run report as Markdown."""

from __future__ import annotations


def _decision_label(decision: dict | None) -> str:
    if decision is None:
        return "승인 대기"
    return "승인" if decision["approved"] else "반려"


def render_report(valuations: list[dict], tickets: list[dict], decision: dict | None) -> str:
    lines = [
        "# 전환사채 워크플로 리포트",
        "",
        "> 표본(가상) 데이터로 계산한 결과입니다. 실제 주문은 전송되지 않았습니다.",
        "",
        "## 가격평가",
        "",
        "| 종목 | 시장가 | 모델가 | 괴리율 | 패리티 | 채권가치 | 델타 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for v in valuations:
        gap = v["model_price"] / v["market_price"] - 1.0
        lines.append(
            f"| {v['bond_id']} | {v['market_price']:.2f} | {v['model_price']:.2f} | {gap:+.1%} "
            f"| {v['parity']:.2f} | {v['bond_floor']:.2f} | {v['delta_pct']:.0%} |"
        )

    lines += ["", "## 매매 티켓", ""]
    if not tickets:
        lines.append("매매 대상 없음: 저평가 기준을 넘는 종목이 없거나 리스크 한도에서 모두 제외되었습니다.")
    else:
        lines += [
            f"결정: **{_decision_label(decision)}**",
            "",
            "| 종목 | 구분 | 액면 | 평가금액 | 저평가 | 헤지 주식수 |",
            "|---|---|---:|---:|---:|---:|",
        ]
        for t in tickets:
            lines.append(
                f"| {t['bond_id']} | {t['action']} | {t['face_amount']:,.0f} | {t['market_value']:,.0f} "
                f"| {t['cheapness']:+.1%} | {t['hedge_shares']:,} |"
            )
        if decision and decision.get("note"):
            lines += ["", f"메모: {decision['note']}"]
    return "\n".join(lines) + "\n"
