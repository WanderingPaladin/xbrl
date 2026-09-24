"""Grade a Taxonomy Package zip using submitted DTS + sealed expected fixtures.

Used by the main tests and by the mutant regression suite. Does not read
verifier-owned schemas as if they were the candidate.
"""

from __future__ import annotations

import json
from pathlib import Path

from runner.drs import (
    DrsError,
    canonicalize_model,
    effective_model,
    evaluate_context,
    models_equal,
)
from runner.package import (
    NS_EXT,
    NS_LOPT,
    PackageError,
    load_submitted_dts,
    unpack_taxonomy_zip,
)

L = lambda n: f"{{{NS_LOPT}}}{n}"
E = lambda n: f"{{{NS_EXT}}}{n}"
FIXTURES = Path("/tests/fixtures")


def _fixtures_dir() -> Path:
    if FIXTURES.is_dir():
        return FIXTURES
    return Path(__file__).resolve().parents[1] / "fixtures"


def grade_package(zip_path: Path, unpack_to: Path) -> list[str]:
    """Return a list of failures. Empty means the package satisfies the contract."""
    errors: list[str] = []
    try:
        root = unpack_taxonomy_zip(zip_path, unpack_to)
        dts = load_submitted_dts(root)
    except (PackageError, DrsError, ValueError) as exc:
        return [str(exc)]

    schemas = dts["schemas"]
    by_qn = schemas["by_qn"]
    try:
        parsed = dts.get("parsed")
        if parsed is None:
            from runner.drs import parse_definition_sources

            parsed = parse_definition_sources(dts.get("linkbases") or [], schemas)
        model = effective_model(parsed, schemas)
    except (DrsError, PackageError, ValueError) as exc:
        return [str(exc)]

    if by_qn.get(L("NetAssets"), {}).get("periodType") != "instant":
        errors.append("NetAssets periodType must be instant")
    for name in (
        "ChangeInNetAssets",
        "Contributions",
        "EmployerContributions",
        "BenefitsPaid",
        "InvestmentReturn",
        "AdminExpenses",
    ):
        if by_qn.get(L(name), {}).get("periodType") != "duration":
            errors.append(f"{name} periodType must be duration")

    if L("Dependant") not in by_qn:
        errors.append("Dependant missing from submitted schema")
    if E("Other") not in by_qn:
        errors.append("ext:Other missing from submitted schema")

    fx = _fixtures_dir()
    expected = canonicalize_model(json.loads((fx / "manifest.json").read_text(encoding="utf-8")))
    ok, msg = models_equal(canonicalize_model(model), expected)
    if not ok:
        errors.append(msg)

    for name in ("public_cases.json", "holdout_cases.json"):
        cases = json.loads((fx / name).read_text(encoding="utf-8"))
        for case in cases:
            got = evaluate_context(model, case["concept"], case["segment"], case["scenario"])
            if got != case["expect"]:
                errors.append(f"{case['id']}: got {got} expect {case['expect']}")

    defaults = model["defaults"]
    if L("MemberClassDimension") in defaults:
        errors.append("MemberClassDimension must not have a default")
    if defaults.get(L("SchemeDimension")) != L("Occupational"):
        errors.append("SchemeDimension default is not Occupational")
    if defaults.get(L("GeographyDimension")) != L("Domestic"):
        errors.append("GeographyDimension default is not Domestic")
    if defaults.get(L("CurrencyDimension")) != L("PresentationCurrency"):
        errors.append("CurrencyDimension default is not PresentationCurrency")

    admin = [
        c
        for c in model["primary_items"].get(L("AdminExpenses"), {}).get("all", [])
        if "CubeAdmin" in c["cube"]
    ]
    if admin:
        dims = admin[0].get("dimensions") or {}
        ccy = dims.get(L("CurrencyDimension"))
        if not ccy:
            errors.append("CubeAdmin is missing CurrencyDimension")
        else:
            usable = set(ccy["usable"])
            if E("Other") not in usable:
                errors.append("currency usable set missing ext:Other")
            if L("Other") in usable:
                errors.append("core Other must not be a currency member")

    return errors
