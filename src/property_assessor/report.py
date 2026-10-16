"""Plain-text report: eligibility first, then the money, then what it rests on."""

from __future__ import annotations

from decimal import Decimal

from property_assessor.assessor import Result
from property_assessor.comparables import ComparablesResult


def gbp(d: Decimal) -> str:
    sign = "-" if d < 0 else ""
    return f"{sign}£{abs(d):,.0f}"


def pct(d: Decimal | None) -> str:
    return "n/a" if d is None else f"{d:.1%}"


def comparables_lines(c: ComparablesResult) -> list[str]:
    out = [f"  COMPARABLES ({c.source}, to {c.as_of}, {c.basis}): {len(c.scored)} sales, {c.effective_n} effective after weighting"]
    if c.verdict == "too few comparables":
        out.append("    too few effective comparables to judge the asking price")
    else:
        assert c.weighted_median is not None and c.weighted_q1 is not None and c.weighted_q3 is not None
        out.append(
            f"    weighted median {gbp(c.weighted_median)}, interquartile {gbp(c.weighted_q1)} to {gbp(c.weighted_q3)}; "
            f"asking {gbp(c.asking_price)} is {c.verdict.upper()} ({c.premium_over_median:+.1%} vs median)"
        )
    for sc in c.scored[:5]:
        s = sc.sale
        out.append(
            f"      w={sc.weight:.2f}  {gbp(s.price)}  {s.date}  {s.type.replace('_', ' ')} {s.beds or '?'}-bed  {s.address}"
            f"  [type {sc.type_score} × beds {sc.bedroom_score} × recency {sc.recency_score} × locality {sc.locality_score}]"
        )
    if c.note:
        out.append(f"    note: {c.note}")
    return out


def render(results: list[Result], comps: dict[str, ComparablesResult] | None = None) -> str:
    lines: list[str] = []
    by_candidate: dict[str, list[Result]] = {}
    for r in results:
        by_candidate.setdefault(r.candidate.id, []).append(r)

    for rs in by_candidate.values():
        c = rs[0].candidate
        lines.append("=" * 100)
        lines.append(f"{c.label}  ({c.postcode_district}, {c.town})  {c.listing_url}")
        lines.append(
            f"  {gbp(c.asking_price.value)} {c.asking_price.basis} {c.asking_price.as_of or ''}  |  {c.bedrooms} bed {c.property_type.replace('_', ' ')}, {c.tenure}"
            f"  |  council tax band {c.council_tax_band or '?'}  |  EPC {c.epc_rating or 'not shown'}"
        )
        for n in c.notes:
            lines.append(f"  note: {n}")
        if comps and c.id in comps:
            lines.extend(comparables_lines(comps[c.id]))
        for r in rs:
            e = r.eligibility
            use = next(c for c in e.checks if c.name == "property use")
            if not use.passed:
                # One line is enough: this lender does not do this strategy, whatever the numbers say.
                lines.append(f"  {r.strategy.replace('_', ' ').upper():10} {r.lender.name}  {r.product.code}: excluded, {use.detail}")
                continue
            lines.append("")
            lines.append(f"  {r.strategy.replace('_', ' ').upper():10} {r.lender.name}  {r.product.code} {r.product.name}")
            lines.append(
                f"    loan {gbp(e.loan)} at {r.product.rate.value:.2%} ({r.product.rate.basis}), LTV {e.ltv:.0%}, stress {e.stress_rate:.2%}"
            )
            verdict = ("ELIGIBLE, CONDITIONAL" if e.conditional else "ELIGIBLE") if e.eligible else "NOT ELIGIBLE"
            lines.append(f"    {verdict}")
            for chk in e.checks:
                mark = "cond" if chk.conditional else ("ok  " if chk.passed else "FAIL")
                lines.append(f"      {mark} {chk.name}: {chk.detail}")
            if r.missing:
                lines.append(f"    returns not computed: missing {', '.join(r.missing)}")
                continue
            a = r.assessment
            assert a is not None
            y, ret = a.year, a.returns
            lines.append(
                f"    cash in {gbp(a.cash_in.total)}: " + "; ".join(f"{li.label} {gbp(li.amount)} [{li.basis}]" for li in a.cash_in.lines)
            )
            lines.append(f"    year 1 income {gbp(y.gross_income)}: " + "; ".join(f"{li.label} [{li.basis}]" for li in y.income_lines))
            lines.append(
                f"    operating {gbp(y.operating_costs)}: "
                + "; ".join(f"{li.label} {gbp(li.amount)} [{li.basis}]" for li in y.operating_lines)
            )
            lines.append(
                f"    mortgage interest {gbp(y.mortgage_interest)}  →  pre-tax {gbp(y.pre_tax_cash_flow)}, tax {gbp(y.tax)}, after-tax {gbp(y.after_tax_cash_flow)}"
            )
            lines.extend(returns_lines(r.strategy, "YEAR 1" if r.strategy == "short_let" else "RETURNS", ret))
            if a.sensitivity is not None:
                from property_assessor.sensitivity import Sensitivity

                sens = a.sensitivity
                assert isinstance(sens, Sensitivity)
                lines.append(
                    f"    SENSITIVITY (year 1 after-tax {gbp(sens.base_after_tax)}; one input moved at a time, everything else held)"
                )
                for row in sens.rows:
                    lines.append(f"      {row.input:16} {row.change:26} → {gbp(row.after_tax_cash_flow):>9}  ({row.delta:+,.0f})")
                if sens.varied_costs:
                    lines.append(f"      variable costs = {', '.join(sens.varied_costs)}; held: {', '.join(sens.held_costs)}")
            for sc in a.scenarios:
                ys = sc.year
                lines.append(
                    f"    {sc.name.upper()}: operating {gbp(ys.operating_costs)} → pre-tax {gbp(ys.pre_tax_cash_flow)}, tax {gbp(ys.tax)}, after-tax {gbp(ys.after_tax_cash_flow)}"
                )
                lines.extend(returns_lines(r.strategy, sc.name.upper(), sc.returns))
            for w in a.warnings:
                lines.append(f"    ! {w}")
    lines.append("=" * 100)
    lines.append(rank_table(results))
    return "\n".join(lines)


def returns_lines(strategy: str, label: str, ret) -> list[str]:  # type: ignore[no-untyped-def]
    be = f", pre-tax break-even occupancy {pct(ret.break_even_occupancy)}" if strategy == "short_let" else ""
    out = [f"    {label}  gross yield {pct(ret.gross_yield)}  net yield {pct(ret.net_yield)}  cash-on-cash {pct(ret.cash_on_cash)}{be}"]
    if ret.rates is not None:
        out.append(f"      rates regime: {ret.rates.regime.replace('_', ' ')} ({ret.rates.reason})")
        for c in ret.rates.conditions:
            actual = "no evidence" if c.actual is None else str(c.actual)
            out.append(f"        {c.status:8} {c.name}: {actual} vs {c.required} required [{c.basis}]")
    out.append(f"    basis: {ret.provenance.basis} (weakest input); inputs: " + "; ".join(ret.provenance.inputs))
    return out


def rank_table(results: list[Result]) -> str:
    rows = [r for r in results if r.assessment is not None and next(c for c in r.eligibility.checks if c.name == "property use").passed]
    if not rows:
        return "No returns computed."
    rows.sort(key=lambda r: (not r.eligibility.eligible, -r.assessment.returns.cash_on_cash))  # type: ignore[union-attr]
    out = [
        "RANKED BY YEAR-1 CASH-ON-CASH (eligible first). CoC s/s = steady state once a year of letting is on record (short let only). Every figure is an estimate resting on the listed inputs.",
        "",
    ]
    out.append(
        f"{'candidate':34} {'strategy':10} {'lender / product':46} {'elig':5} {'cash in':>10} {'after-tax':>10} {'CoC yr1':>8} {'CoC s/s':>8} {'gross':>6} {'net':>6} {'basis':10}"
    )
    for r in rows:
        a = r.assessment
        assert a is not None
        out.append(
            f"{r.candidate.label[:34]:34} {r.strategy:10} {(r.lender.name + ' ' + r.product.code)[:46]:46} {('cond' if r.eligibility.conditional else 'yes') if r.eligibility.eligible else 'NO':5} "
            f"{gbp(a.cash_in.total):>10} {gbp(a.year.after_tax_cash_flow):>10} {pct(a.returns.cash_on_cash):>8} "
            f"{(pct(a.scenarios[0].returns.cash_on_cash) if a.scenarios else '-'):>8} {pct(a.returns.gross_yield):>6} {pct(a.returns.net_yield):>6} {a.returns.provenance.basis:10}"
        )
    return "\n".join(out)
