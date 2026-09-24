# Goal

Repair the damaged 2025 Lattice Occupational Pensions XBRL taxonomy and produce a self-contained, offline-valid XBRL Taxonomy Package at:

`/app/out/repaired-taxonomy.zip`

Use `/app/current-package`, `/app/prior-package`, the desk evidence, and `filing-gate` to reconstruct the intended 2025 model. Some differences between 2024 and 2025 are intentional. The 2025 desk evidence describes the approved changes. Prior-year material covers mechanics that remain unchanged. Treat damaged material in the current 2025 package as untrusted.

# Available Inputs

- `/app/current-package`
- `/app/prior-package`
- `/app/desk/change-memo.txt`
- `/app/desk/line-item-cubes.csv`
- `/app/desk/marked-contexts.json`
- `/app/desk/operating-notes.txt`
- `/app/desk/migration-logs.txt`

`filing-gate` is on PATH while you solve.

# Deliverable

Write `/app/out/repaired-taxonomy.zip` as a real ZIP file, not a symlink. It must be an XBRL Taxonomy Package that is self-contained, resolves and validates offline, and does not depend on files outside the ZIP.

The ZIP contains exactly one top-level package directory. `<package-root>` means that single top-level directory inside `repaired-taxonomy.zip`.

# Package Requirements

Include these files inside `<package-root>`:

- `<package-root>/META-INF/taxonomyPackage.xml` in namespace `http://xbrl.org/2016/taxonomy-package`, identifier `http://lattice.example/lopt/taxonomy-package/2025`, and an entry point whose href resolves to the 2025 entry schema.
- `<package-root>/META-INF/catalog.xml` in namespace `urn:oasis:names:tc:entity:xmlns:xml:catalog`. Remap the 2025 taxonomy URIs and the required standard XBRL schema URIs onto files inside the package so DTS resolution needs no network.
- `<package-root>/taxonomy/lopt-2025.xsd` as the 2025 entry schema.

Keep both 2025 namespaces: `http://lattice.example/lopt/2025-01-31` and `http://lattice.example/lopt/ext/2025-01-31`. Include the remaining schemas, linkbases, and resources the DTS requires. Repair schema, import, and package-resolution defects as well as the dimensional relationships.

# 2025 Concept Semantics

Preserve every concept declaration already present in the supplied 2025 taxonomy schemas unless this task explicitly requires that declaration to be removed. Do not prune a declaration merely because the repaired model does not use it. Withdrawing a concept from a member tree does not delete its schema declaration.

That includes `CubeLegacy`, `ValuationDimension`, `ValuationDomain`, `Ongoing`, and `Windup`, and concepts the model uses, including NetAssets, ChangeInNetAssets, Contributions, EmployerContributions, BenefitsPaid, InvestmentReturn, AdminExpenses, HybridGroup, Dependant, lopt:Other, and ext:Other.

A preserved declaration does not preserve obsolete relationships or require new ones. `lopt:Other` stays declared but withdrawn from the geography member tree. Dependant stays declared for leftover tagging and outside the usable MemberClass members. Those legacy and valuation concepts stay declared without new cube attachments.

Period types:

- NetAssets: instant
- ChangeInNetAssets, Contributions, EmployerContributions, BenefitsPaid, InvestmentReturn, and AdminExpenses: duration

# Dimensional Rules

Every effective has-hypercube relationship must explicitly carry `xbrldt:closed`.

- Closed cubes and exclusions: `xbrldt:closed="true"` or the XML Schema Boolean `1`.
- Open cubes and exclusions: `xbrldt:closed="false"` or the XML Schema Boolean `0`.

Closed positive cubes reject extra dimensions on their context element. Open positive cubes permit extra dimensions. Open `notAll` exclusions continue to match when additional dimensions are present on the same context side.

NetAssets:

- Closed CubeScheme on segment, scheme members only.
- Occupational is the default.
- HybridGroup is non-usable. CashBalance and MixedBenefit are reportable members beneath it.

Movement items:

- ChangeInNetAssets and its movement descendants inherit open CubeFlow on segment.
- Dimensions: scheme, geography, and member class.
- Domestic is the geography default. Overseas is a non-usable grouping heading.
- There is no MemberClass default. Usable MemberClass members are Active, Deferred, and Pensioner.
- Core geography lopt:Other is withdrawn from the geography member tree.

Contributions and EmployerContributions have the open CubeForbiddenContributions `notAll` exclusion on scenario for Pensioner. Extra dimensions on that same context side do not disable the exclusion.

BenefitsPaid has the open CubeBenefitsExclusion `notAll` exclusion on segment for Personal plus Rest of World. Extra flow dimensions on that same context side do not disable the exclusion.

AdminExpenses also has closed CubeAdmin on scenario. PresentationCurrency is the default. SettlementCurrency and ext:Other are usable. ext:Other is the only valid currency Other. Do not treat lopt:Other as a currency member.

Dependant remains declared in the schema for leftover tagging and is not a usable MemberClass member.

# Validation Requirements

The completed package must load entirely offline, use its catalog for canonical URI resolution, and contain no dependency on files outside the ZIP. It must represent the required effective 2025 dimensional model, permit the required valid filings, and reject the required invalid filings, including filings the desk has not marked. Valid equivalent serializations and modular DTS organizations are acceptable.

`filing-gate` may be used while solving. It is not available at grading time.
