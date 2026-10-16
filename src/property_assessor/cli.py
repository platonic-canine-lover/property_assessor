"""assess compare: run candidates through lenders, strategies and the return model."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from property_assessor.assessor import Inputs, comparables, run
from property_assessor.report import render

app = typer.Typer(add_completion=False, help="Compare UK property candidates as long-let or short-let investments.")


@app.command()
def compare(
    candidates: Annotated[Path, typer.Option(help="YAML list of candidate properties")] = Path("data/candidates.yaml"),
    applicant: Annotated[Path, typer.Option(help="YAML applicant profile")] = Path("data/applicant.yaml"),
    market: Annotated[Path, typer.Option(help="YAML market snapshot (rents, ADR, occupancy, sold medians)")] = Path(
        "data/snapshots/cornwall-2026-10.yaml"
    ),
    config: Annotated[Path, typer.Option(help="config directory")] = Path("config"),
    area: Annotated[str, typer.Option(help="area rules file name under config/rules")] = "cornwall",
    as_json: Annotated[bool, typer.Option("--json", help="machine-readable output")] = False,
) -> None:
    inputs = Inputs.load(config, candidates, applicant, market, area)
    results = run(inputs)
    comps = comparables(inputs)
    if as_json:
        payload = [
            {
                "candidate": r.candidate.id,
                "strategy": r.strategy,
                "lender": r.lender.id,
                "product": r.product.code,
                "eligible": r.eligibility.eligible,
                "checks": [c.model_dump() for c in r.eligibility.checks],
                "missing": r.missing,
                "assessment": r.assessment.model_dump(mode="json") if r.assessment else None,
            }
            for r in results
        ]
        typer.echo(
            json.dumps({"comparables": {k: v.model_dump(mode="json") for k, v in comps.items()}, "results": payload}, indent=2, default=str)
        )
    else:
        typer.echo(render(results, comps))


if __name__ == "__main__":
    app()
