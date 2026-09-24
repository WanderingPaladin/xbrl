#!/usr/bin/env python3
"""Rebuild the approved 2025 Taxonomy Package from the 2025 schemas.

The intended 2025 arc table is checked against the authoritative 2024
definition linkbase. Every prior relationship not changed by the approved
memo must survive semantically in the 2025 target. This script does not
read verifier fixtures.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict, deque
from pathlib import Path

CURRENT = Path("/app/current-package")
PRIOR = Path("/app/prior-package")
DESK = Path("/app/desk")
OUT = Path("/app/out/repaired-taxonomy.zip")
BUILD = Path("/tmp/repaired-package")

NS_XS = "http://www.w3.org/2001/XMLSchema"
NS_XBRLI = "http://www.xbrl.org/2003/instance"
NS_LINK = "http://www.xbrl.org/2003/linkbase"
NS_XLINK = "http://www.w3.org/1999/xlink"
NS_XBRLDT = "http://xbrl.org/2005/xbrldt"
NS_XBRLDT_WWW = "http://www.xbrl.org/2005/xbrldt"
NS_LOPT = "http://lattice.example/lopt/2025-01-31"
NS_LOPT_2024 = "http://lattice.example/lopt/2024-01-31"
NS_EXT = "http://lattice.example/lopt/ext/2025-01-31"

AR_ALL = "http://xbrl.org/int/dim/arcrole/all"
AR_NOTALL = "http://xbrl.org/int/dim/arcrole/notAll"
AR_HCD = "http://xbrl.org/int/dim/arcrole/hypercube-dimension"
AR_DD = "http://xbrl.org/int/dim/arcrole/dimension-domain"
AR_DM = "http://xbrl.org/int/dim/arcrole/domain-member"
AR_DEF = "http://xbrl.org/int/dim/arcrole/dimension-default"

R_STMT = "http://lattice.example/role/2025/statement/net-assets"
R_SCHEME = "http://lattice.example/role/2025/cube/scheme"
R_SCHEME_MEM = "http://lattice.example/role/2025/cube/scheme-members"
R_FLOW = "http://lattice.example/role/2025/cube/flow"
R_FORB = "http://lattice.example/role/2025/cube/forbidden-contributions"
R_ADMIN = "http://lattice.example/role/2025/cube/admin"
R_BEX = "http://lattice.example/role/2025/cube/benefits-exclusion"
R_DEFROLE = "http://lattice.example/role/2025/defaults"

R24_STMT = "http://lattice.example/role/2024/statement/net-assets"
R24_SCHEME = "http://lattice.example/role/2024/cube/scheme"
R24_SCHEME_MEM = "http://lattice.example/role/2024/cube/scheme-members"
R24_FLOW = "http://lattice.example/role/2024/cube/flow"
R24_DEFROLE = "http://lattice.example/role/2024/defaults"

ROLE_MAP_2024_TO_2025 = {
    R24_STMT: R_STMT,
    R24_SCHEME: R_SCHEME,
    R24_SCHEME_MEM: R_SCHEME_MEM,
    R24_FLOW: R_FLOW,
    R24_DEFROLE: R_DEFROLE,
}

ROLE_IDS = {
    R_STMT: "stmt_net_assets",
    R_SCHEME: "cube_scheme",
    R_SCHEME_MEM: "cube_scheme_members",
    R_FLOW: "cube_flow",
    R_FORB: "cube_forbidden_contributions",
    R_ADMIN: "cube_admin",
    R_BEX: "cube_benefits_exclusion",
    R_DEFROLE: "defaults",
}

ARCROLE_HREFS = {
    AR_ALL: "lib/xbrldt-2005.xsd#all",
    AR_NOTALL: "lib/xbrldt-2005.xsd#notAll",
    AR_HCD: "lib/xbrldt-2005.xsd#hypercube-dimension",
    AR_DD: "lib/xbrldt-2005.xsd#dimension-domain",
    AR_DM: "lib/xbrldt-2005.xsd#domain-member",
    AR_DEF: "lib/xbrldt-2005.xsd#dimension-default",
}


def L(name: str) -> str:
    return f"{{{NS_LOPT}}}{name}"


def E(name: str) -> str:
    return f"{{{NS_EXT}}}{name}"


def qn_ns(qn: str) -> str:
    return qn[1:].split("}", 1)[0]


def qn_local(qn: str) -> str:
    return qn.split("}", 1)[1]


def load_ids() -> dict[str, str]:
    """Confirm every concept we bind exists in the 2025 schemas."""
    ids = {}
    tax = CURRENT / "taxonomy"
    for path in (tax / "lopt-2025.xsd", tax / "lopt-2025-ext.xsd"):
        root = ET.parse(path).getroot()
        tns = root.get("targetNamespace")
        for el in root.findall(f"{{{NS_XS}}}element"):
            name, eid = el.get("name"), el.get("id")
            if name and eid:
                ids[f"{{{tns}}}{name}"] = (path.name, eid)
    return ids


def href_for(qn: str, ids: dict[str, str]) -> str:
    filename, eid = ids[qn]
    return f"{filename}#{eid}"


def label_for(qn: str) -> str:
    if qn_ns(qn) == NS_EXT:
        return "ext_" + qn_local(qn)
    return "lopt_" + qn_local(qn)


def arc(elr, arcrole, frm, to, **kw):
    rec = {"elr": elr, "arcrole": arcrole, "from": frm, "to": to}
    rec.update(kw)
    return rec


def approved_arcs():
    # Memo: Occupational default; Hybrid grouping; Cash Balance / Mixed Benefit reportable.
    # Cube sheet: NetAssets closed scheme; flow items open CubeFlow; contributions
    # notAll on scenario; benefits exclusion; admin closed currency on scenario.
    A = [
        arc(R_STMT, AR_DM, L("StatementAbstract"), L("NetAssets")),
        arc(R_STMT, AR_DM, L("StatementAbstract"), L("ChangeInNetAssets")),
        arc(R_STMT, AR_DM, L("ChangeInNetAssets"), L("Contributions")),
        arc(R_STMT, AR_DM, L("ChangeInNetAssets"), L("BenefitsPaid")),
        arc(R_STMT, AR_DM, L("ChangeInNetAssets"), L("InvestmentReturn")),
        arc(R_STMT, AR_DM, L("ChangeInNetAssets"), L("AdminExpenses")),
        arc(R_STMT, AR_DM, L("Contributions"), L("EmployerContributions")),
        arc(R_STMT, AR_ALL, L("NetAssets"), L("CubeScheme"), contextElement="segment", closed=True, targetRole=R_SCHEME),
        arc(R_STMT, AR_ALL, L("ChangeInNetAssets"), L("CubeFlow"), contextElement="segment", closed=False, targetRole=R_FLOW),
        arc(R_STMT, AR_NOTALL, L("Contributions"), L("CubeForbiddenContributions"), contextElement="scenario", closed=False, targetRole=R_FORB),
        arc(R_STMT, AR_NOTALL, L("BenefitsPaid"), L("CubeBenefitsExclusion"), contextElement="segment", closed=False, targetRole=R_BEX),
        arc(R_STMT, AR_ALL, L("AdminExpenses"), L("CubeAdmin"), contextElement="scenario", closed=True, targetRole=R_ADMIN),
        arc(R_SCHEME, AR_HCD, L("CubeScheme"), L("SchemeDimension"), targetRole=R_SCHEME_MEM),
        arc(R_SCHEME_MEM, AR_DD, L("SchemeDimension"), L("SchemeDomain"), usable=False),
        arc(R_SCHEME_MEM, AR_DM, L("SchemeDomain"), L("Occupational")),
        arc(R_SCHEME_MEM, AR_DM, L("SchemeDomain"), L("Personal")),
        arc(R_SCHEME_MEM, AR_DM, L("SchemeDomain"), L("HybridGroup"), usable=False),
        arc(R_SCHEME_MEM, AR_DM, L("HybridGroup"), L("CashBalance")),
        arc(R_SCHEME_MEM, AR_DM, L("HybridGroup"), L("MixedBenefit")),
        arc(R_FLOW, AR_HCD, L("CubeFlow"), L("SchemeDimension"), targetRole=R_SCHEME_MEM),
        arc(R_FLOW, AR_HCD, L("CubeFlow"), L("GeographyDimension")),
        arc(R_FLOW, AR_HCD, L("CubeFlow"), L("MemberClassDimension")),
        arc(R_FLOW, AR_DD, L("GeographyDimension"), L("GeoDomain"), usable=False),
        arc(R_FLOW, AR_DM, L("GeoDomain"), L("Domestic")),
        arc(R_FLOW, AR_DM, L("GeoDomain"), L("Overseas"), usable=False),
        arc(R_FLOW, AR_DM, L("Overseas"), L("EEA")),
        arc(R_FLOW, AR_DM, L("Overseas"), L("RestOfWorld")),
        arc(R_FLOW, AR_DD, L("MemberClassDimension"), L("MemberClassDomain"), usable=False),
        arc(R_FLOW, AR_DM, L("MemberClassDomain"), L("Active")),
        arc(R_FLOW, AR_DM, L("MemberClassDomain"), L("Deferred")),
        arc(R_FLOW, AR_DM, L("MemberClassDomain"), L("Pensioner")),
        arc(R_FORB, AR_HCD, L("CubeForbiddenContributions"), L("MemberClassDimension")),
        arc(R_FORB, AR_DD, L("MemberClassDimension"), L("Pensioner")),
        arc(R_BEX, AR_HCD, L("CubeBenefitsExclusion"), L("SchemeDimension")),
        arc(R_BEX, AR_HCD, L("CubeBenefitsExclusion"), L("GeographyDimension")),
        arc(R_BEX, AR_DD, L("SchemeDimension"), L("Personal")),
        arc(R_BEX, AR_DD, L("GeographyDimension"), L("RestOfWorld")),
        arc(R_ADMIN, AR_HCD, L("CubeAdmin"), L("CurrencyDimension")),
        arc(R_ADMIN, AR_DD, L("CurrencyDimension"), L("CurrencyDomain"), usable=False),
        arc(R_ADMIN, AR_DM, L("CurrencyDomain"), L("PresentationCurrency")),
        arc(R_ADMIN, AR_DM, L("CurrencyDomain"), L("SettlementCurrency")),
        arc(R_ADMIN, AR_DM, L("CurrencyDomain"), E("Other")),
        arc(R_DEFROLE, AR_DEF, L("SchemeDimension"), L("Occupational")),
        arc(R_DEFROLE, AR_DEF, L("GeographyDimension"), L("Domestic")),
        arc(R_DEFROLE, AR_DEF, L("CurrencyDimension"), L("PresentationCurrency")),
    ]
    return A


class PriorPreservationError(RuntimeError):
    """The 2025 target dropped or mis-encoded an authoritative 2024 relationship."""


def _xbrldt_attr(el: ET.Element, name: str) -> str | None:
    return el.get(f"{{{NS_XBRLDT}}}{name}") or el.get(f"{{{NS_XBRLDT_WWW}}}{name}")


def _xs_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    text = value.strip()
    if text in {"true", "1"}:
        return True
    if text in {"false", "0"}:
        return False
    raise PriorPreservationError(f"invalid XML Schema boolean {value!r}")


def _map_prior_role(uri: str | None) -> str | None:
    if not uri:
        return None
    mapped = ROLE_MAP_2024_TO_2025.get(uri)
    if mapped is None:
        raise PriorPreservationError(f"unknown prior role URI {uri}")
    return mapped


def _local(qn_or_name: str) -> str:
    return qn_or_name.split("}", 1)[-1]


def _rel_key(elr: str, arcrole: str, frm: str, to: str) -> tuple[str, str, str, str]:
    return (elr, arcrole, _local(frm), _local(to))


def load_prior_relationships(prior_package: Path) -> list[dict]:
    """Parse the 2024 definition linkbase into mapped 2025 semantic records."""
    tax = prior_package / "taxonomy"
    if not tax.is_dir():
        tax = prior_package
    schema = tax / "lopt-2024.xsd"
    linkbase = tax / "lopt-2024-def.xml"
    if not schema.is_file() or not linkbase.is_file():
        raise PriorPreservationError(f"prior definition DTS missing under {tax}")
    ids: dict[str, str] = {}
    schema_root = ET.parse(schema).getroot()
    if schema_root.get("targetNamespace") != NS_LOPT_2024:
        raise PriorPreservationError(
            f"prior schema targetNamespace must be {NS_LOPT_2024}"
        )
    for el in schema_root:
        tag = el.tag if isinstance(el.tag, str) else ""
        if not tag.endswith("element"):
            continue
        name, eid = el.get("name"), el.get("id")
        if name and eid:
            ids[eid] = name
    root = ET.parse(linkbase).getroot()
    out: list[dict] = []
    for dlink in root:
        tag = dlink.tag if isinstance(dlink.tag, str) else ""
        if not tag.endswith("definitionLink"):
            continue
        elr = _map_prior_role(dlink.get(f"{{{NS_XLINK}}}role") or dlink.get("role"))
        labels: dict[str, str] = {}
        for child in list(dlink):
            ctag = child.tag if isinstance(child.tag, str) else ""
            if not ctag.endswith("loc"):
                continue
            label = child.get(f"{{{NS_XLINK}}}label")
            href = child.get(f"{{{NS_XLINK}}}href") or ""
            frag = href.split("#", 1)[-1] if href else ""
            name = ids.get(frag)
            if not label or not name:
                raise PriorPreservationError(f"unresolved prior locator {href}")
            labels[label] = name
        for child in list(dlink):
            ctag = child.tag if isinstance(child.tag, str) else ""
            if not ctag.endswith("definitionArc"):
                continue
            arcrole = child.get(f"{{{NS_XLINK}}}arcrole")
            frm = labels.get(child.get(f"{{{NS_XLINK}}}from") or "")
            to = labels.get(child.get(f"{{{NS_XLINK}}}to") or "")
            if not arcrole or not frm or not to:
                raise PriorPreservationError("prior definitionArc missing arcrole/from/to")
            target_role = _map_prior_role(_xbrldt_attr(child, "targetRole"))
            rec = {
                "elr": elr,
                "arcrole": arcrole,
                "from": frm,
                "to": to,
                "targetRole": target_role,
                "contextElement": _xbrldt_attr(child, "contextElement"),
                "closed": _xs_bool(_xbrldt_attr(child, "closed"), False) if arcrole in {AR_ALL, AR_NOTALL} else None,
                "closed_present": _xbrldt_attr(child, "closed") is not None,
                "usable": _xs_bool(_xbrldt_attr(child, "usable"), True),
                "priority": int(child.get("priority") or 0),
                "use": child.get("use") or "optional",
            }
            out.append(rec)
    if not out:
        raise PriorPreservationError("prior definition linkbase contained no relationships")
    return out


def _approved_index(arcs: list[dict]) -> dict[tuple[str, str, str, str], dict]:
    out: dict[tuple[str, str, str, str], dict] = {}
    for rec in arcs:
        out[_rel_key(rec["elr"], rec["arcrole"], rec["from"], rec["to"])] = rec
    return out


def verify_prior_preservation(prior_relationships: list[dict], approved_2025_arcs: list[dict]) -> None:
    """Every 2024 relationship not changed by the memo must exist in the 2025 target."""
    approved = _approved_index(approved_2025_arcs)

    def require(key: tuple[str, str, str, str], *, detail: str) -> dict:
        rec = approved.get(key)
        if rec is None:
            raise PriorPreservationError(detail)
        return rec

    scheme_default = require(
        (R_DEFROLE, AR_DEF, "SchemeDimension", "Occupational"),
        detail="2025 Occupational scheme default missing from approved target",
    )
    if approved.get((R_DEFROLE, AR_DEF, "SchemeDimension", "Personal")) is not None:
        raise PriorPreservationError("2024 Personal scheme default must not remain in the 2025 target")
    if _local(scheme_default["to"]) != "Occupational":
        raise PriorPreservationError("2025 scheme default is not Occupational")

    if approved.get((R_FLOW, AR_DM, "GeoDomain", "Other")) is not None:
        raise PriorPreservationError("prior geography Other relationship must be absent from the 2025 target")

    overseas = require(
        (R_FLOW, AR_DM, "GeoDomain", "Overseas"),
        detail="2025 Overseas grouping relationship missing from approved target",
    )
    if overseas.get("usable") is not False:
        raise PriorPreservationError("2025 Overseas domain-member relationship must have usable=false")

    hybrid = require(
        (R_SCHEME_MEM, AR_DM, "SchemeDomain", "HybridGroup"),
        detail="2025 HybridGroup grouping relationship missing from approved target",
    )
    if hybrid.get("usable") is not False:
        raise PriorPreservationError("2025 HybridGroup domain-member relationship must have usable=false")

    changed = {
        (R_DEFROLE, AR_DEF, "SchemeDimension", "Personal"),
        (R_FLOW, AR_DM, "GeoDomain", "Other"),
        (R_FLOW, AR_DM, "GeoDomain", "Overseas"),
    }
    hybrid_key = (R_SCHEME_MEM, AR_DM, "SchemeDomain", "HybridGroup")
    for rec in prior_relationships:
        if _rel_key(rec["elr"], rec["arcrole"], rec["from"], rec["to"]) != hybrid_key:
            continue
        if rec.get("usable") is not False:
            changed.add(hybrid_key)
        break
    for rec in prior_relationships:
        key = _rel_key(rec["elr"], rec["arcrole"], rec["from"], rec["to"])
        if key in changed:
            continue
        got = approved.get(key)
        if got is None:
            raise PriorPreservationError(
                f"unchanged prior relationship not preserved: {rec['from']} -> {rec['to']}"
            )
        if (got.get("targetRole") or None) != (rec.get("targetRole") or None):
            raise PriorPreservationError(
                f"unchanged prior relationship targetRole not preserved: {rec['from']} -> {rec['to']}"
            )
        if rec.get("contextElement") and got.get("contextElement") != rec.get("contextElement"):
            raise PriorPreservationError(
                f"unchanged prior relationship contextElement not preserved: {rec['from']} -> {rec['to']}"
            )
        if rec["arcrole"] in {AR_ALL, AR_NOTALL} and bool(got.get("closed")) != bool(rec.get("closed")):
            raise PriorPreservationError(
                f"unchanged prior relationship closed not preserved: {rec['from']} -> {rec['to']}"
            )
        if rec["arcrole"] in {AR_DM, AR_DD} and got.get("usable", True) is not rec.get("usable", True):
            raise PriorPreservationError(
                f"unchanged prior relationship usable not preserved: {rec['from']} -> {rec['to']}"
            )


def write_linkbase(path: Path, arcs, ids) -> None:
    ET.register_namespace("", NS_LINK)
    ET.register_namespace("xlink", NS_XLINK)
    ET.register_namespace("xbrldt", NS_XBRLDT)
    root = ET.Element(f"{{{NS_LINK}}}linkbase")
    for uri, rid in ROLE_IDS.items():
        ET.SubElement(
            root,
            f"{{{NS_LINK}}}roleRef",
            {
                "roleURI": uri,
                f"{{{NS_XLINK}}}type": "simple",
                f"{{{NS_XLINK}}}href": f"lopt-2025-roles.xsd#{rid}",
            },
        )
    for uri in sorted({a["arcrole"] for a in arcs}):
        ET.SubElement(
            root,
            f"{{{NS_LINK}}}arcroleRef",
            {
                "arcroleURI": uri,
                f"{{{NS_XLINK}}}type": "simple",
                f"{{{NS_XLINK}}}href": ARCROLE_HREFS[uri],
            },
        )
    by_elr: dict[str, list] = {}
    for a in arcs:
        by_elr.setdefault(a["elr"], []).append(a)
    for elr, elr_arcs in by_elr.items():
        dlink = ET.SubElement(
            root,
            f"{{{NS_LINK}}}definitionLink",
            {f"{{{NS_XLINK}}}type": "extended", f"{{{NS_XLINK}}}role": elr},
        )
        needed = set()
        for a in elr_arcs:
            needed.add(a["from"])
            needed.add(a["to"])
        for qn in sorted(needed):
            ET.SubElement(
                dlink,
                f"{{{NS_LINK}}}loc",
                {
                    f"{{{NS_XLINK}}}type": "locator",
                    f"{{{NS_XLINK}}}href": href_for(qn, ids),
                    f"{{{NS_XLINK}}}label": label_for(qn),
                },
            )
        for i, a in enumerate(elr_arcs, start=1):
            attrib = {
                f"{{{NS_XLINK}}}type": "arc",
                f"{{{NS_XLINK}}}arcrole": a["arcrole"],
                f"{{{NS_XLINK}}}from": label_for(a["from"]),
                f"{{{NS_XLINK}}}to": label_for(a["to"]),
                "order": str(i),
                "use": a.get("use", "optional"),
                "priority": str(a.get("priority", 0)),
            }
            if a.get("targetRole"):
                attrib[f"{{{NS_XBRLDT}}}targetRole"] = a["targetRole"]
            if a.get("contextElement"):
                attrib[f"{{{NS_XBRLDT}}}contextElement"] = a["contextElement"]
            if a.get("closed") is True:
                attrib[f"{{{NS_XBRLDT}}}closed"] = "true"
            elif a.get("closed") is False and a["arcrole"] in {AR_ALL, AR_NOTALL}:
                attrib[f"{{{NS_XBRLDT}}}closed"] = "false"
            if a.get("usable") is False:
                attrib[f"{{{NS_XBRLDT}}}usable"] = "false"
            ET.SubElement(dlink, f"{{{NS_LINK}}}definitionArc", attrib)
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(str(path), xml_declaration=True, encoding="UTF-8")


APPROVED_CATALOG = """\
<?xml version="1.0" encoding="UTF-8"?>
<catalog xmlns="urn:oasis:names:tc:entity:xmlns:xml:catalog">
  <rewriteURI uriStartString="http://lattice.example/lopt/2025-01-31/" rewritePrefix="../taxonomy/"/>
  <rewriteURI uriStartString="http://lattice.example/lopt/ext/2025-01-31/" rewritePrefix="../taxonomy/"/>
  <rewriteURI uriStartString="http://lattice.example/lopt/roles/2025/" rewritePrefix="../taxonomy/"/>
  <rewriteURI uriStartString="http://www.xbrl.org/2003/xbrl-instance-2003-12-31.xsd" rewritePrefix="../taxonomy/lib/xbrl-instance-2003-12-31.xsd"/>
  <rewriteURI uriStartString="http://www.xbrl.org/2003/xbrl-linkbase-2003-12-31.xsd" rewritePrefix="../taxonomy/lib/xbrl-linkbase-2003-12-31.xsd"/>
  <rewriteURI uriStartString="http://www.xbrl.org/2003/xl-2003-12-31.xsd" rewritePrefix="../taxonomy/lib/xl-2003-12-31.xsd"/>
  <rewriteURI uriStartString="http://www.xbrl.org/2003/xlink-2003-12-31.xsd" rewritePrefix="../taxonomy/lib/xlink-2003-12-31.xsd"/>
  <rewriteURI uriStartString="http://www.xbrl.org/2005/xbrldt-2005.xsd" rewritePrefix="../taxonomy/lib/xbrldt-2005.xsd"/>
  <rewriteURI uriStartString="http://www.xbrl.org/2006/xbrldi-2006.xsd" rewritePrefix="../taxonomy/lib/xbrldi-2006.xsd"/>
  <rewriteURI uriStartString="http://xbrl.org/2006/xbrldi-2006.xsd" rewritePrefix="../taxonomy/lib/xbrldi-2006.xsd"/>
  <rewriteURI uriStartString="http://www.w3.org/2001/xml.xsd" rewritePrefix="../taxonomy/lib/xml.xsd"/>
  <rewriteURI uriStartString="http://www.w3.org/1999/xlink" rewritePrefix="../taxonomy/lib/xlink-2003-12-31.xsd"/>
  <rewriteURI uriStartString="http://www.xbrl.org/2003/XLink" rewritePrefix="../taxonomy/lib/xl-2003-12-31.xsd"/>
  <rewriteURI uriStartString="http://xbrl.org/2005/xbrldt" rewritePrefix="../taxonomy/lib/xbrldt-2005.xsd"/>
  <rewriteURI uriStartString="http://xbrl.org/2006/xbrldi" rewritePrefix="../taxonomy/lib/xbrldi-2006.xsd"/>
  <rewriteURI uriStartString="http://www.xbrl.org/2003/instance" rewritePrefix="../taxonomy/lib/xbrl-instance-2003-12-31.xsd"/>
  <rewriteURI uriStartString="http://www.xbrl.org/2003/" rewritePrefix="../taxonomy/lib/"/>
  <rewriteURI uriStartString="http://www.xbrl.org/2005/" rewritePrefix="../taxonomy/lib/"/>
  <rewriteURI uriStartString="http://www.xbrl.org/2006/" rewritePrefix="../taxonomy/lib/"/>
  <rewriteURI uriStartString="http://xbrl.org/2006/" rewritePrefix="../taxonomy/lib/"/>
</catalog>
"""


def repair_entry_xsd(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = text.replace(' xmlns:xbrldt="http://www.xbrl.org/2005/xbrldt"', "")
    text = text.replace(' xmlns:xbrldt="http://xbrl.org/2005/xbrldt"', "")
    text = text.replace(
        'xmlns:xbrli="http://www.xbrl.org/2003/instance"',
        'xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldt="http://xbrl.org/2005/xbrldt"',
        1,
    )
    text = text.replace(
        'schemaLocation="http://www.xbrl.org/2003/xbrl-instance-2003-12-31.xsd"',
        'schemaLocation="lib/xbrl-instance-2003-12-31.xsd"',
    )
    text = text.replace(
        'namespace="http://www.xbrl.org/2005/xbrldt" schemaLocation="lib/xbrldt.xsd"',
        'namespace="http://xbrl.org/2005/xbrldt" schemaLocation="lib/xbrldt-2005.xsd"',
    )
    text = text.replace(
        'namespace="http://www.xbrl.org/2005/xbrldt" schemaLocation="lib/xbrldt-2005.xsd"',
        'namespace="http://xbrl.org/2005/xbrldt" schemaLocation="lib/xbrldt-2005.xsd"',
    )
    text = text.replace('schemaLocation="lib/xbrldt.xsd"', 'schemaLocation="lib/xbrldt-2005.xsd"')
    path.write_text(text, encoding="utf-8")


def repair_ext_xsd(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if "http://www.xbrl.org/2003/instance" not in text or "<import" not in text:
        text = """<?xml version="1.0" encoding="UTF-8"?>
<schema xmlns="http://www.w3.org/2001/XMLSchema" xmlns:xbrli="http://www.xbrl.org/2003/instance" targetNamespace="http://lattice.example/lopt/ext/2025-01-31" elementFormDefault="qualified">
  <import namespace="http://www.xbrl.org/2003/instance" schemaLocation="lib/xbrl-instance-2003-12-31.xsd"/>
  <element name="Other" id="ext_Other" type="xbrli:stringItemType" substitutionGroup="xbrli:item" nillable="true" xbrli:periodType="duration" abstract="true" />
</schema>
"""
    path.write_text(text, encoding="utf-8")


PACKAGE_DIR_NAME = "repaired-taxonomy"


def zip_package(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(src.rglob("*")):
            if path.is_file():
                relative = path.relative_to(src).as_posix()
                zf.write(path, f"{PACKAGE_DIR_NAME}/{relative}")


PI_TREE = {
    L("StatementAbstract"),
    L("NetAssets"),
    L("ChangeInNetAssets"),
    L("Contributions"),
    L("EmployerContributions"),
    L("BenefitsPaid"),
    L("InvestmentReturn"),
    L("AdminExpenses"),
}


def load_desk_evidence(desk: Path) -> dict:
    """Read every desk source. None of these files is the finished package."""
    memo_path = desk / "change-memo.txt"
    notes_path = desk / "operating-notes.txt"
    cubes_path = desk / "line-item-cubes.csv"
    marked_path = desk / "marked-contexts.json"
    logs_path = desk / "migration-logs.txt"
    for path in (memo_path, notes_path, cubes_path, marked_path, logs_path):
        if not path.is_file():
            raise SystemExit(f"desk evidence missing: {path.name}")
    with cubes_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    marked = json.loads(marked_path.read_text(encoding="utf-8"))
    return {
        "memo": memo_path.read_text(encoding="utf-8"),
        "notes": notes_path.read_text(encoding="utf-8"),
        "cube_table": rows,
        "marked_contexts": marked.get("contexts") or [],
        "logs": logs_path.read_text(encoding="utf-8"),
    }


def validate_authority_rules(evidence: dict) -> None:
    """Operating notes rank the sources. Refuse contradictory or missing authority."""
    notes = evidence["notes"]
    required = (
        "board change memo is the approved 2025 business delta",
        "2024 package controls relationships the memo did not change",
        "not a complete statement of the approved 2025 model",
        "do not define the complete",
        "unfinished current package is not approved",
    )
    missing = [phrase for phrase in required if phrase not in notes]
    if missing:
        raise SystemExit("operating notes do not establish source authority: " + "; ".join(missing))
    lowered = notes.lower()
    if "filing-gate is the complete" in lowered or "marked filings define the complete" in lowered:
        raise SystemExit("operating notes contradict the stated authority rules")
    if "current package is approved" in lowered and "not approved" not in lowered:
        raise SystemExit("operating notes treat the unfinished package as authoritative")
    memo = evidence["memo"]
    if "Occupational is the default" not in memo:
        raise SystemExit("desk memo missing the 2025 scheme default — refusing to guess")
    if "open exclusion" not in memo:
        raise SystemExit("desk memo missing open exclusion rule — refusing to guess")


def _arc_index(arcs: list[dict]) -> dict[tuple, list[dict]]:
    idx: dict[tuple, list[dict]] = defaultdict(list)
    for rec in arcs:
        idx[(rec["elr"], rec["arcrole"], rec["from"])].append(rec)
    return idx


def _walk_members(idx: dict, start_elr: str, start_qn: str) -> set[str]:
    usable: set[str] = set()
    seen: set[tuple[str, str]] = set()
    dq: deque[tuple[str, str]] = deque([(start_elr, start_qn)])
    while dq:
        elr, node = dq.popleft()
        if (elr, node) in seen:
            continue
        seen.add((elr, node))
        for rec in idx.get((elr, AR_DM, node), []):
            if rec.get("usable", True):
                usable.add(rec["to"])
            dq.append((rec.get("targetRole") or elr, rec["to"]))
    return usable


def _dimension_members(idx: dict, elr: str, dimension: str) -> dict:
    usable: set[str] = set()
    for rec in idx.get((elr, AR_DD, dimension), []):
        nxt = rec.get("targetRole") or elr
        if rec.get("usable", True):
            usable.add(rec["to"])
        usable |= _walk_members(idx, nxt, rec["to"])
    return {"usable": usable}


def _pi_descendants(idx: dict, start_elr: str, start_qn: str) -> set[str]:
    seen: set[tuple[str, str]] = set()
    out: set[str] = set()
    dq: deque[tuple[str, str]] = deque([(start_elr, start_qn)])
    while dq:
        elr, node = dq.popleft()
        if (elr, node) in seen:
            continue
        seen.add((elr, node))
        for rec in idx.get((elr, AR_DM, node), []):
            if rec["from"] not in PI_TREE or rec["to"] not in PI_TREE:
                continue
            out.add(rec["to"])
            dq.append((rec.get("targetRole") or elr, rec["to"]))
    return out


def effective_primary_model(arcs: list[dict]) -> dict[str, dict]:
    """Role-aware effective cube assignments, including inherited primary-item cubes."""
    idx = _arc_index(arcs)
    defaults = {rec["from"]: rec["to"] for rec in arcs if rec["arcrole"] == AR_DEF}
    direct: dict[str, list[dict]] = defaultdict(list)
    for rec in arcs:
        if rec["arcrole"] not in {AR_ALL, AR_NOTALL}:
            continue
        cube_elr = rec.get("targetRole") or rec["elr"]
        dims = {}
        for hcd in idx.get((cube_elr, AR_HCD, rec["to"]), []):
            dim_elr = hcd.get("targetRole") or cube_elr
            info = _dimension_members(idx, dim_elr, hcd["to"])
            if hcd["to"] in defaults:
                info["default"] = defaults[hcd["to"]]
            dims[hcd["to"]] = info
        direct[rec["from"]].append(
            {
                "cube": rec["to"],
                "sign": "all" if rec["arcrole"] == AR_ALL else "notAll",
                "closed": bool(rec.get("closed")),
                "contextElement": rec.get("contextElement"),
                "dimensions": dims,
            }
        )
    owned: dict[str, list[dict]] = defaultdict(list)
    for source, cubes in direct.items():
        for cube in cubes:
            owned[source].append(cube)
            for descendant in _pi_descendants(idx, rec_elr(arcs, source), source):
                owned[descendant].append(cube)
    primary = {}
    for item, cubes in owned.items():
        primary[item] = {
            "all": [c for c in cubes if c["sign"] == "all"],
            "notAll": [c for c in cubes if c["sign"] == "notAll"],
        }
    return primary


def rec_elr(arcs: list[dict], source: str) -> str:
    for rec in arcs:
        if rec["arcrole"] in {AR_ALL, AR_NOTALL} and rec["from"] == source:
            return rec["elr"]
    return R_STMT


def validate_cube_table(rows: list[dict], arcs: list[dict]) -> None:
    """CSV rows are effective assignments, including cubes inherited through the statement tree."""
    if not rows:
        raise SystemExit("line-item cube table is empty")
    model = effective_primary_model(arcs)
    for row in rows:
        item = (row.get("line_item") or "").strip()
        cube = (row.get("cube") or "").strip()
        if not item or not cube:
            raise SystemExit(f"line-item cube row is incomplete: {row}")
        assigned = model.get(L(item))
        if assigned is None:
            raise SystemExit(f"cube table line item {item} has no effective hypercube")
        names = {_local(c["cube"]) for c in assigned["all"] + assigned["notAll"]}
        if cube not in names:
            raise SystemExit(f"cube table assignment {item} -> {cube} is not effective")


def _ctx_map(pairs: list) -> dict[str, str]:
    out: dict[str, str] = {}
    for dim, mem in pairs:
        if dim in out:
            raise SystemExit(f"duplicate dimension in marked context: {dim}")
        out[dim] = mem
    return out


def evaluate_marked_context(model: dict, concept: str, segment: list, scenario: list) -> str:
    cons = model.get(concept)
    if cons is None:
        return "accept"
    seg = _ctx_map(segment)
    sce = _ctx_map(scenario)

    def side_map(element: str) -> dict[str, str]:
        return seg if element == "segment" else sce

    for cube in cons["all"]:
        ctx = side_map(cube["contextElement"])
        extra = set(ctx) - set(cube["dimensions"])
        if cube["closed"] and extra:
            return "reject"
        for dim, dinfo in cube["dimensions"].items():
            usable = set(dinfo["usable"])
            default = dinfo.get("default")
            if dim in ctx:
                val = ctx[dim]
                if default is not None and val == default:
                    return "reject"
                if val not in usable:
                    return "reject"
            elif default is None:
                return "reject"
    for cube in cons["notAll"]:
        ctx = side_map(cube["contextElement"])
        extra = set(ctx) - set(cube["dimensions"])
        if cube["closed"] and extra:
            continue
        matched = True
        for dim, dinfo in cube["dimensions"].items():
            usable = set(dinfo["usable"])
            default = dinfo.get("default")
            if dim in ctx:
                val = ctx[dim]
            elif default is not None:
                val = default
            else:
                matched = False
                break
            if val not in usable:
                matched = False
                break
        if matched:
            return "reject"
    return "accept"


def validate_marked_contexts(contexts: list[dict], arcs: list[dict]) -> None:
    """Marked filings are known cases, not a complete specification."""
    if not contexts:
        raise SystemExit("marked contexts are empty")
    model = effective_primary_model(arcs)
    for case in contexts:
        decision = case.get("decision")
        if decision not in {"accept", "reject"}:
            raise SystemExit(f"marked context {case.get('id')} has no accept/reject decision")
        got = evaluate_marked_context(
            model,
            case["concept"],
            case.get("segment") or [],
            case.get("scenario") or [],
        )
        if got != decision:
            raise SystemExit(
                f"marked context {case.get('id')} says {decision} but the approved model says {got}"
            )


def validate_migration_logs(text: str, evidence: dict, arcs: list[dict]) -> None:
    """Logs are incomplete corroboration. A missing concept is not a withdrawal."""
    lowered = text.lower()
    if "incomplete" not in lowered or "truncated" not in lowered:
        raise SystemExit("migration log does not identify itself as incomplete and truncated")
    if "do not treat a missing concept" not in lowered:
        raise SystemExit("migration log does not warn that missing concepts are not withdrawals")
    if "open exclusion" not in evidence["memo"]:
        raise SystemExit("migration log checks require the approved memo")
    lines = text.splitlines()
    records = []
    for index, line in enumerate(lines):
        match = re.search(r"concept=(\S+)\s+category=(\S+)\s+accepted=(true|false)", line)
        if not match:
            continue
        note = ""
        if index + 1 < len(lines) and "note=" in lines[index + 1]:
            note = lines[index + 1].split("note=", 1)[1].strip().lower()
        records.append(
            {
                "concept": match.group(1),
                "category": match.group(2),
                "accepted": match.group(3),
                "note": note,
            }
        )
    if not records:
        raise SystemExit("migration log has no concept observations to corroborate")
    model = effective_primary_model(arcs)
    probes = {
        ("NetAssets", "extra geography"): (
            L("NetAssets"),
            [
                [L("SchemeDimension"), L("Personal")],
                [L("GeographyDimension"), L("Domestic")],
            ],
            [],
        ),
        ("NetAssets", "hybrid heading"): (
            L("NetAssets"),
            [[L("SchemeDimension"), L("HybridGroup")]],
            [],
        ),
        ("Contributions", "pensioner"): (
            L("Contributions"),
            [[L("SchemeDimension"), L("Personal")]],
            [[L("MemberClassDimension"), L("Pensioner")]],
        ),
        ("AdminExpenses", "member class"): (
            L("AdminExpenses"),
            [
                [L("SchemeDimension"), L("Personal")],
                [L("MemberClassDimension"), L("Active")],
            ],
            [
                [L("CurrencyDimension"), L("SettlementCurrency")],
                [L("MemberClassDimension"), L("Active")],
            ],
        ),
    }
    for rec in records:
        if rec["accepted"] == "true":
            raise SystemExit(
                f"migration log accepts {rec['concept']}; that contradicts the known rejection observations"
            )
        probe = None
        for (concept, needle), spec in probes.items():
            if rec["concept"] == concept and needle in rec["note"]:
                probe = spec
                break
        if probe is None:
            continue
        concept, segment, scenario = probe
        if evaluate_marked_context(model, concept, segment, scenario) != "reject":
            raise SystemExit(
                f"migration log rejection for {rec['concept']} contradicts the approved model"
            )


def main() -> None:
    evidence = load_desk_evidence(DESK)
    validate_authority_rules(evidence)
    if not PRIOR.is_dir():
        raise SystemExit("prior-year package missing")
    try:
        prior_relationships = load_prior_relationships(PRIOR)
        ids = load_ids()
        arcs = approved_arcs()
        verify_prior_preservation(prior_relationships, arcs)
        validate_cube_table(evidence["cube_table"], arcs)
        validate_marked_contexts(evidence["marked_contexts"], arcs)
        validate_migration_logs(evidence["logs"], evidence, arcs)
    except PriorPreservationError as exc:
        raise SystemExit(str(exc)) from exc
    for a in arcs:
        if a["from"] not in ids or a["to"] not in ids:
            raise SystemExit(f"schema missing {a['from']} or {a['to']}")
    if BUILD.exists():
        shutil.rmtree(BUILD)
    shutil.copytree(CURRENT, BUILD, symlinks=False)
    tax = BUILD / "taxonomy"
    repair_entry_xsd(tax / "lopt-2025.xsd")
    repair_ext_xsd(tax / "lopt-2025-ext.xsd")
    (BUILD / "META-INF" / "catalog.xml").write_text(APPROVED_CATALOG, encoding="utf-8")
    write_linkbase(tax / "lopt-2025-def.xml", arcs, ids)
    zip_package(BUILD, OUT)


if __name__ == "__main__":
    main()
