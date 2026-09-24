"""Candidate outcome tests for /app/out/repaired-taxonomy.zip.

Every reward-producing test inspects the submitted Taxonomy Package against
instruction.md. Verifier-owned fixture regressions live in authoring/ and are
not executed by tests/test.sh.
"""

from __future__ import annotations

import json
from pathlib import Path

from runner.arelle_load import (
    arelle_output_ok,
    arelle_validate_context,
    arelle_validate_facts,
    arelle_validation_error,
)
from runner.grade import grade_package
from runner.package import (
    NS_EXT,
    NS_LOPT,
)

SUBMISSION = Path("/app/out/repaired-taxonomy.zip")
L = lambda n: f"{{{NS_LOPT}}}{n}"
E = lambda n: f"{{{NS_EXT}}}{n}"


def _load_fixture(name: str):
    packed = Path("/tests/fixtures") / name
    path = packed if packed.is_file() else Path(__file__).resolve().parent / "fixtures" / name
    return json.loads(path.read_text(encoding="utf-8"))


def test_required_artifact_exists():
    """instruction.md: write a real Taxonomy Package, not a symlink, at the documented path."""
    assert SUBMISSION.is_file(), f"{SUBMISSION} was not created"
    assert not SUBMISSION.is_symlink(), "deliverable must not be a symlink"


def test_submitted_package_grades_cleanly(tmp_path):
    """Submitted DTS: package metadata, catalog, entry, concepts, sealed DRS, and public/holdout contexts."""
    errors = grade_package(SUBMISSION, tmp_path / "unpacked")
    assert errors == [], "\n".join(errors)


def test_submitted_package_loads_offline_with_arelle():
    """instruction.md: the package loads offline and accept/reject matches the 2025 model, including unmarked filings."""
    accepts = [
        ("netassets-default", L("NetAssets"), [], []),
        ("cashbalance", L("NetAssets"), [(L("SchemeDimension"), L("CashBalance"))], []),
        (
            "ext-other-currency",
            L("AdminExpenses"),
            [(L("SchemeDimension"), L("Personal")), (L("MemberClassDimension"), L("Active"))],
            [(L("CurrencyDimension"), E("Other"))],
        ),
    ]
    rejects = [
        (
            "closed-scheme-extra-dimension",
            L("NetAssets"),
            [(L("SchemeDimension"), L("Personal")), (L("GeographyDimension"), L("Domestic"))],
            [],
        ),
        (
            "benefits-open-notall-extra-memberclass",
            L("BenefitsPaid"),
            [
                (L("SchemeDimension"), L("Personal")),
                (L("GeographyDimension"), L("RestOfWorld")),
                (L("MemberClassDimension"), L("Active")),
            ],
            [],
        ),
        (
            "contributions-open-notall-extra-scenario-dim",
            L("Contributions"),
            [(L("SchemeDimension"), L("Personal")), (L("MemberClassDimension"), L("Active"))],
            [(L("MemberClassDimension"), L("Pensioner")), (L("CurrencyDimension"), L("SettlementCurrency"))],
        ),
        (
            "dependant-member-class",
            L("InvestmentReturn"),
            [
                (L("SchemeDimension"), L("Personal")),
                (L("GeographyDimension"), L("EEA")),
                (L("MemberClassDimension"), L("Dependant")),
            ],
            [],
        ),
        (
            "core-other-as-currency",
            L("AdminExpenses"),
            [(L("SchemeDimension"), L("Personal")), (L("MemberClassDimension"), L("Active"))],
            [(L("CurrencyDimension"), L("Other"))],
        ),
        ("hybrid", L("NetAssets"), [(L("SchemeDimension"), L("HybridGroup"))], []),
        (
            "overseas",
            L("InvestmentReturn"),
            [
                (L("SchemeDimension"), L("Personal")),
                (L("GeographyDimension"), L("Overseas")),
                (L("MemberClassDimension"), L("Active")),
            ],
            [],
        ),
        (
            "contrib-pensioner",
            L("Contributions"),
            [(L("SchemeDimension"), L("Personal")), (L("MemberClassDimension"), L("Active"))],
            [(L("MemberClassDimension"), L("Pensioner"))],
        ),
        (
            "employer-pensioner",
            L("EmployerContributions"),
            [(L("SchemeDimension"), L("Personal")), (L("MemberClassDimension"), L("Active"))],
            [(L("MemberClassDimension"), L("Pensioner"))],
        ),
        (
            "benefits-row",
            L("BenefitsPaid"),
            [
                (L("SchemeDimension"), L("Personal")),
                (L("GeographyDimension"), L("RestOfWorld")),
                (L("MemberClassDimension"), L("Active")),
            ],
            [],
        ),
        (
            "admin-extra-dim",
            L("AdminExpenses"),
            [(L("SchemeDimension"), L("Personal")), (L("MemberClassDimension"), L("Active"))],
            [(L("CurrencyDimension"), L("SettlementCurrency")), (L("MemberClassDimension"), L("Active"))],
        ),
    ]
    rc, blob = arelle_validate_facts(
        SUBMISSION,
        [
            {"concept": L("NetAssets"), "segment": [], "scenario": []},
            {"concept": L("NetAssets"), "segment": [(L("SchemeDimension"), L("CashBalance"))], "scenario": []},
            {
                "concept": L("InvestmentReturn"),
                "segment": [
                    (L("SchemeDimension"), L("Personal")),
                    (L("GeographyDimension"), L("EEA")),
                    (L("MemberClassDimension"), L("Active")),
                ],
                "scenario": [],
            },
            {
                "concept": L("InvestmentReturn"),
                "segment": [
                    (L("SchemeDimension"), L("Personal")),
                    (L("GeographyDimension"), L("EEA")),
                    (L("MemberClassDimension"), L("Deferred")),
                ],
                "scenario": [],
            },
            {
                "concept": L("InvestmentReturn"),
                "segment": [
                    (L("SchemeDimension"), L("Personal")),
                    (L("GeographyDimension"), L("EEA")),
                    (L("MemberClassDimension"), L("Pensioner")),
                ],
                "scenario": [],
            },
            {
                "concept": L("BenefitsPaid"),
                "segment": [
                    (L("SchemeDimension"), L("Personal")),
                    (L("GeographyDimension"), L("EEA")),
                    (L("MemberClassDimension"), L("Active")),
                ],
                "scenario": [],
            },
            {
                "concept": L("AdminExpenses"),
                "segment": [
                    (L("SchemeDimension"), L("Personal")),
                    (L("MemberClassDimension"), L("Active")),
                ],
                "scenario": [(L("CurrencyDimension"), L("SettlementCurrency"))],
            },
            {
                "concept": L("Contributions"),
                "segment": [
                    (L("SchemeDimension"), L("Personal")),
                    (L("MemberClassDimension"), L("Active")),
                ],
                "scenario": [],
            },
            {
                "concept": L("EmployerContributions"),
                "segment": [
                    (L("SchemeDimension"), L("Personal")),
                    (L("GeographyDimension"), L("EEA")),
                    (L("MemberClassDimension"), L("Active")),
                ],
                "scenario": [],
            },
        ],
    )
    arelle_output_ok(blob)
    assert rc == 0, blob[-4000:]
    assert not arelle_validation_error(blob), blob[-4000:]

    reportable = [
        case
        for case in _load_fixture("holdout_cases.json")
        if case["id"] in {"change-in-net-assets-reportable", "acc-employer-flow"}
    ]
    assert {case["id"] for case in reportable} == {"change-in-net-assets-reportable", "acc-employer-flow"}
    for case in reportable:
        assert case["expect"] == "accept"
        accepts.append((case["id"], case["concept"], case["segment"], case["scenario"]))

    failures = []
    for name, concept, segment, scenario in accepts:
        rc, blob = arelle_validate_context(SUBMISSION, concept, segment, scenario)
        try:
            arelle_output_ok(blob)
        except AssertionError:
            failures.append(f"{name}: Arelle load/runtime failure\n{blob[-2000:]}")
            continue
        if rc != 0 or arelle_validation_error(blob):
            failures.append(f"{name}: unexpectedly rejected\n{blob[-2000:]}")
    for name, concept, segment, scenario in rejects:
        rc, blob = arelle_validate_context(SUBMISSION, concept, segment, scenario)
        try:
            arelle_output_ok(blob)
        except AssertionError:
            failures.append(f"{name}: Arelle load/runtime failure\n{blob[-2000:]}")
            continue
        if rc == 0 and not arelle_validation_error(blob):
            failures.append(f"{name}: unexpectedly accepted\n{blob[-2000:]}")
    assert not failures, "offline Arelle mismatches:\n" + "\n".join(failures)
