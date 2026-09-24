"""Effective DRS extraction and filing-context evaluation.

This module grades a native definition linkbase. It does not emit a linkbase
and is not a solver: expected constraints come from the sealed manifest.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from runner.xmlbool import BooleanError, parse_optional_xs_boolean

NS = {
    "xs": "http://www.w3.org/2001/XMLSchema",
    "xbrli": "http://www.xbrl.org/2003/instance",
    "link": "http://www.xbrl.org/2003/linkbase",
    "xlink": "http://www.w3.org/1999/xlink",
    "xbrldt": "http://xbrl.org/2005/xbrldt",
    "xbrldi": "http://xbrl.org/2006/xbrldi",
}
AR_ALL = "http://xbrl.org/int/dim/arcrole/all"
AR_NOTALL = "http://xbrl.org/int/dim/arcrole/notAll"
AR_HCD = "http://xbrl.org/int/dim/arcrole/hypercube-dimension"
AR_DD = "http://xbrl.org/int/dim/arcrole/dimension-domain"
AR_DM = "http://xbrl.org/int/dim/arcrole/domain-member"
AR_DEF = "http://xbrl.org/int/dim/arcrole/dimension-default"

HAS_HC = {AR_ALL, AR_NOTALL}

NS_LOPT = "http://lattice.example/lopt/2025-01-31"
R_STMT = "http://lattice.example/role/2025/statement/net-assets"

XBRLI_ITEM = f"{{{NS['xbrli']}}}item"
XBRLDT_DIMENSION_ITEM = f"{{{NS['xbrldt']}}}dimensionItem"
XBRLDT_HYPERCUBE_ITEM = f"{{{NS['xbrldt']}}}hypercubeItem"

PI_TREE_NODES = {
    f"{{{NS_LOPT}}}StatementAbstract",
    f"{{{NS_LOPT}}}NetAssets",
    f"{{{NS_LOPT}}}ChangeInNetAssets",
    f"{{{NS_LOPT}}}Contributions",
    f"{{{NS_LOPT}}}EmployerContributions",
    f"{{{NS_LOPT}}}BenefitsPaid",
    f"{{{NS_LOPT}}}InvestmentReturn",
    f"{{{NS_LOPT}}}AdminExpenses",
}

REQUIRED_HC_CLOSED = (
    (f"{{{NS_LOPT}}}NetAssets", f"{{{NS_LOPT}}}CubeScheme", True),
    (f"{{{NS_LOPT}}}ChangeInNetAssets", f"{{{NS_LOPT}}}CubeFlow", False),
    (f"{{{NS_LOPT}}}Contributions", f"{{{NS_LOPT}}}CubeForbiddenContributions", False),
    (f"{{{NS_LOPT}}}BenefitsPaid", f"{{{NS_LOPT}}}CubeBenefitsExclusion", False),
    (f"{{{NS_LOPT}}}AdminExpenses", f"{{{NS_LOPT}}}CubeAdmin", True),
)

REQUIRED_CLOSED_HC = tuple((frm, cube) for frm, cube, _expected in REQUIRED_HC_CLOSED)

SCHEME_DIMENSION = f"{{{NS_LOPT}}}SchemeDimension"
GEOGRAPHY_DIMENSION = f"{{{NS_LOPT}}}GeographyDimension"
CORE_OTHER = f"{{{NS_LOPT}}}Other"
MEMBER_CLASS_DIMENSION = f"{{{NS_LOPT}}}MemberClassDimension"
MEMBER_CLASS_DOMAIN = f"{{{NS_LOPT}}}MemberClassDomain"
DEPENDANT = f"{{{NS_LOPT}}}Dependant"

# Dimension trees compared against the sealed manifest. Extraction walks every
# effective domain-member edge from the dimension; it does not filter targets
# through an expected-node allowlist.
DIMENSION_TREES = {
    "scheme": SCHEME_DIMENSION,
    "geography": GEOGRAPHY_DIMENSION,
    "memberClass": MEMBER_CLASS_DIMENSION,
}


def clark(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}"


def expand(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}"


def qname_text(value: str, nsmap: dict[str | None, str]) -> str:
    value = value.strip()
    if value.startswith("{") and "}" in value:
        return value
    if ":" in value:
        prefix, local = value.split(":", 1)
        if prefix not in nsmap or nsmap[prefix] is None:
            raise ValueError(f"unbound prefix {prefix!r} in {value!r}")
        return expand(nsmap[prefix], local)
    default_ns = nsmap.get(None) or nsmap.get("")
    if default_ns:
        return expand(default_ns, value)
    raise ValueError(f"unprefixed QName {value!r} has no in-scope default namespace")


class DrsError(Exception):
    """Structural problem in the submitted linkbase or supporting schema."""


def load_xml(path: Path) -> ET.ElementTree:
    return ET.parse(path)


def parse_schemas(schema_paths: list[Path]) -> dict[str, dict[str, Any]]:
    """Map element @id and expanded QName to schema metadata."""
    by_id: dict[str, dict[str, Any]] = {}
    by_qn: dict[str, dict[str, Any]] = {}
    for path in schema_paths:
        tree = load_xml(path)
        root = tree.getroot()
        tns = root.get("targetNamespace")
        if not tns:
            raise DrsError(f"{path} has no targetNamespace")
        for el in root.findall("xs:element", NS):
            name = el.get("name")
            eid = el.get("id")
            if not name or not eid:
                continue
            subst = el.get("substitutionGroup", "")
            try:
                abstract, _ = parse_optional_xs_boolean(el.get("abstract"), False)
            except BooleanError as exc:
                raise DrsError(str(exc)) from exc
            info = {
                "id": eid,
                "name": name,
                "ns": tns,
                "qname": expand(tns, name),
                "abstract": abstract,
                "substitutionGroup": subst,
                "path": path.name,
            }
            if eid in by_id:
                raise DrsError(f"duplicate schema id {eid}")
            by_id[eid] = info
            by_qn[info["qname"]] = info
    return {"by_id": by_id, "by_qn": by_qn}


def _href_id(href: str) -> str:
    if "#" not in href:
        raise DrsError(f"locator/roleRef href has no fragment: {href!r}")
    return href.split("#", 1)[1]


def _href_file(href: str) -> str:
    file_part = href.split("#", 1)[0]
    return Path(file_part).name if file_part else ""


def _xbrldt_attr(el: ET.Element, name: str) -> str | None:
    """Read an XBRL Dimensions attribute from the canonical namespace."""
    return el.get(clark(NS["xbrldt"], name))


def parse_role_types(schema_paths: list[Path]) -> dict[str, str]:
    """roleURI -> id."""
    out: dict[str, str] = {}
    for path in schema_paths:
        tree = load_xml(path)
        for rt in tree.getroot().findall("xs:annotation/xs:appinfo/link:roleType", NS):
            uri = rt.get("roleURI")
            rid = rt.get("id")
            if uri and rid:
                out[uri] = rid
    return out


def _parse_linkbase_element(
    linkbase_element: ET.Element,
    origin_path: Path,
    schemas: dict[str, dict[str, Any]],
    source_id: tuple,
) -> dict[str, Any]:
    """Parse one link:linkbase element. Relative hrefs use origin_path as the base."""
    by_id = schemas["by_id"]
    locators_unresolved: list[str] = []
    locator_hrefs: list[str] = []
    role_refs: dict[str, str] = {}
    arcrole_refs: dict[str, str] = {}
    raw_arcs: list[dict[str, Any]] = []
    used_roles: set[str] = set()
    used_arcroles: set[str] = set()
    used_target_roles: set[str] = set()

    for rr in linkbase_element.findall("link:roleRef", NS):
        uri = rr.get("roleURI")
        href = rr.get(clark(NS["xlink"], "href"))
        if not uri or not href:
            raise DrsError("roleRef missing roleURI or href")
        role_refs[uri] = href

    for ar in linkbase_element.findall("link:arcroleRef", NS):
        uri = ar.get("arcroleURI")
        href = ar.get(clark(NS["xlink"], "href"))
        if not uri or not href:
            raise DrsError("arcroleRef missing arcroleURI or href")
        arcrole_refs[uri] = href

    for dlink in linkbase_element.findall("link:definitionLink", NS):
        elr = dlink.get(clark(NS["xlink"], "role"))
        if not elr:
            raise DrsError("definitionLink missing xlink:role")
        used_roles.add(elr)
        labels: dict[str, str] = {}
        for loc in dlink.findall("link:loc", NS):
            label = loc.get(clark(NS["xlink"], "label"))
            href = loc.get(clark(NS["xlink"], "href"))
            if not label or not href:
                raise DrsError("loc missing label or href")
            locator_hrefs.append(href)
            frag = _href_id(href)
            if frag not in by_id:
                locators_unresolved.append(href)
                continue
            info = by_id[frag]
            named = _href_file(href)
            if named and Path(named).name != info["path"]:
                locators_unresolved.append(href)
                continue
            labels[label] = info["qname"]

        for arc in dlink.findall("link:definitionArc", NS):
            arcrole = arc.get(clark(NS["xlink"], "arcrole"))
            frm = arc.get(clark(NS["xlink"], "from"))
            to = arc.get(clark(NS["xlink"], "to"))
            if not arcrole or not frm or not to:
                raise DrsError("definitionArc missing arcrole/from/to")
            if frm not in labels or to not in labels:
                raise DrsError(f"definitionArc endpoints not in this extended link: {frm} -> {to}")
            used_arcroles.add(arcrole)
            try:
                priority = int(arc.get("priority", "0"))
            except ValueError as exc:
                raise DrsError(f"non-integer priority on {frm}->{to}") from exc
            use = arc.get("use", "optional")
            target_role = _xbrldt_attr(arc, "targetRole")
            if target_role:
                used_target_roles.add(target_role)
            try:
                closed, closed_present = parse_optional_xs_boolean(_xbrldt_attr(arc, "closed"), False)
                usable, usable_present = parse_optional_xs_boolean(_xbrldt_attr(arc, "usable"), True)
            except BooleanError as exc:
                raise DrsError(str(exc)) from exc
            rec = {
                "elr": elr,
                "arcrole": arcrole,
                "from": labels[frm],
                "to": labels[to],
                "priority": priority,
                "use": use,
                "order": arc.get("order", "1"),
                "targetRole": target_role,
                "closed": closed,
                "closed_present": closed_present,
                "contextElement": _xbrldt_attr(arc, "contextElement"),
                "usable": usable,
                "usable_present": usable_present,
            }
            raw_arcs.append(rec)

    if locators_unresolved:
        raise DrsError("unresolved locators: " + ", ".join(sorted(set(locators_unresolved))))
    return {
        "path": origin_path,
        "source_id": source_id,
        "role_refs": role_refs,
        "arcrole_refs": arcrole_refs,
        "locator_hrefs": locator_hrefs,
        "used_roles": used_roles,
        "used_arcroles": used_arcroles,
        "used_target_roles": used_target_roles,
        "raw_arcs": raw_arcs,
    }


def _parse_linkbase_document(path: Path, schemas: dict[str, dict[str, Any]]) -> dict[str, Any]:
    tree = load_xml(path)
    root = tree.getroot()
    if root.tag != clark(NS["link"], "linkbase"):
        raise DrsError(f"root element is {root.tag}, expected link:linkbase")
    return _parse_linkbase_element(root, path, schemas, ("external", str(path.resolve())))


def _parse_embedded_linkbases(schema_path: Path, schemas: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Definition linkbases embedded in a reachable schema. Base URI is that schema."""
    tree = load_xml(schema_path)
    elements = [
        el
        for el in tree.getroot().iter(clark(NS["link"], "linkbase"))
        if el is not tree.getroot()
    ]
    docs = []
    resolved = str(schema_path.resolve())
    for ordinal, element in enumerate(elements):
        docs.append(
            _parse_linkbase_element(
                element,
                schema_path,
                schemas,
                ("embedded", resolved, ordinal),
            )
        )
    return docs


def _compose_definition_documents(docs: list[dict[str, Any]]) -> dict[str, Any]:
    used_roles: set[str] = set()
    used_arcroles: set[str] = set()
    used_target_roles: set[str] = set()
    winning: dict[tuple, dict[str, Any]] = {}
    for doc in docs:
        used_roles |= doc["used_roles"]
        used_arcroles |= doc["used_arcroles"]
        used_target_roles |= doc["used_target_roles"]
        for rec in doc["raw_arcs"]:
            key = (rec["elr"], rec["arcrole"], rec["from"], rec["to"])
            prev = winning.get(key)
            if prev is None or rec["priority"] > prev["priority"]:
                winning[key] = rec
    effective = [rec for rec in winning.values() if rec["use"] != "prohibited"]
    return {
        "documents": docs,
        "used_roles": used_roles,
        "used_arcroles": used_arcroles,
        "used_target_roles": used_target_roles,
        "arcs": effective,
        "winning": winning,
    }


def parse_definition_sources(paths: list[Path], schemas: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Compose external definition linkbases and linkbases embedded in reachable schemas."""
    docs: list[dict[str, Any]] = []
    seen_sources: set[tuple] = set()
    for path in sorted(paths, key=lambda item: item.as_posix()):
        doc = _parse_linkbase_document(path, schemas)
        if doc["source_id"] in seen_sources:
            continue
        seen_sources.add(doc["source_id"])
        docs.append(doc)
    seen_schemas: set[Path] = set()
    for schema in schemas.get("schemas") or []:
        schema_path = Path(schema["path"]).resolve()
        if schema_path in seen_schemas:
            continue
        seen_schemas.add(schema_path)
        for doc in _parse_embedded_linkbases(schema_path, schemas):
            if doc["source_id"] in seen_sources:
                continue
            seen_sources.add(doc["source_id"])
            docs.append(doc)
    if not docs:
        raise DrsError("submitted DTS contains no discoverable definition linkbase")
    return _compose_definition_documents(docs)


def parse_linkbases(paths: list[Path], schemas: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Compose definition relationships, including linkbases embedded in reachable schemas."""
    return parse_definition_sources(paths, schemas)


def parse_linkbase(path: Path, schemas: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return parse_definition_sources([path], schemas)


def require_hypercube_closed_semantics(parsed: dict[str, Any]) -> None:
    """Effective has-hypercube arcs must carry xbrldt:closed with the contract value.

    Omission is not allowed. Lexical true/1 and false/0 remain valid.
    """
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for rec in parsed["arcs"]:
        if rec["arcrole"] not in HAS_HC:
            continue
        if not rec.get("closed_present", False):
            raise DrsError(
                f"has-hypercube relationship {_local_name(rec['from'])} -> {_local_name(rec['to'])} "
                f"must explicitly specify xbrldt:closed"
            )
        found[(rec["from"], rec["to"])] = rec
    for frm, cube, expected in REQUIRED_HC_CLOSED:
        rec = found.get((frm, cube))
        if rec is None:
            raise DrsError(
                f"missing required has-hypercube relationship {_local_name(frm)} -> {_local_name(cube)}"
            )
        if rec.get("closed") is not expected:
            raise DrsError(
                f"has-hypercube relationship {_local_name(frm)} -> {_local_name(cube)} "
                f"must be semantically closed={expected}, got {rec.get('closed')!r}"
            )


def require_explicit_closed(parsed: dict[str, Any]) -> None:
    """Compatibility alias for require_hypercube_closed_semantics."""
    require_hypercube_closed_semantics(parsed)


LEGACY_VALUATION_LOCALS = {
    "CubeLegacy",
    "ValuationDimension",
    "ValuationDomain",
    "Ongoing",
    "Windup",
}


def require_no_legacy_cube_attachments(parsed: dict[str, Any]) -> None:
    """Legacy valuation concepts stay declared and must not gain an effective has-hypercube attachment.

    Disconnected domain-member, hypercube-dimension, dimension-domain, and
    dimension-default relationships are not cube attachments.
    """
    for rec in parsed["arcs"]:
        if rec["arcrole"] not in HAS_HC:
            continue
        ends = {_local_name(rec["from"]), _local_name(rec["to"])}
        if not ends & LEGACY_VALUATION_LOCALS:
            continue
        raise DrsError(
            "legacy valuation concept must not receive a new has-hypercube attachment: "
            f"{_local_name(rec['from'])} -> {_local_name(rec['to'])}"
        )


def _local_name(qn: str) -> str:
    return qn.split("}", 1)[-1] if "}" in qn else qn


def _index_arcs(arcs: list[dict[str, Any]]) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    idx: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for rec in arcs:
        idx[(rec["elr"], rec["arcrole"], rec["from"])].append(rec)
    return idx


def _walk_members(idx: dict, start_elr: str, start_qn: str) -> tuple[set[str], set[str]]:
    """Descendants of start_qn via domain-member, honouring targetRole.

    The start node is not added; the arc that reached it already decided
    whether that node is reportable. Traversal continues through nonusable
    grouping members.
    """
    usable: set[str] = set()
    nonusable: set[str] = set()
    seen: set[tuple[str, str]] = set()
    dq: deque[tuple[str, str]] = deque([(start_elr, start_qn)])
    while dq:
        elr, node = dq.popleft()
        key = (elr, node)
        if key in seen:
            continue
        seen.add(key)
        for rec in idx.get((elr, AR_DM, node), []):
            nxt_elr = rec["targetRole"] or elr
            if rec["usable"]:
                usable.add(rec["to"])
            else:
                nonusable.add(rec["to"])
            dq.append((nxt_elr, rec["to"]))
    return usable, nonusable


def _dimension_members(idx: dict, elr: str, dimension: str) -> dict[str, Any]:
    dd_arcs = idx.get((elr, AR_DD, dimension), [])
    usable: set[str] = set()
    nonusable: set[str] = set()
    if not dd_arcs:
        return {"usable": usable, "nonusable": nonusable}
    for rec in dd_arcs:
        nxt_elr = rec["targetRole"] or elr
        domain = rec["to"]
        if rec["usable"]:
            usable.add(domain)
        else:
            nonusable.add(domain)
        u, n = _walk_members(idx, nxt_elr, domain)
        usable |= u
        nonusable |= n
    return {"usable": usable, "nonusable": nonusable}


def _cube_dimensions(idx: dict, cube_elr: str, cube: str) -> dict[str, dict[str, Any]]:
    dims: dict[str, dict[str, Any]] = {}
    for rec in idx.get((cube_elr, AR_HCD, cube), []):
        dim_elr = rec["targetRole"] or cube_elr
        dim = rec["to"]
        dims[dim] = _dimension_members(idx, dim_elr, dim)
    return dims


def _pi_descendants(idx: dict, start_elr: str, start_qn: str) -> set[str]:
    """Primary-item descendants reachable from (start_elr, start_qn).

    Follows domain-member targetRole transitions. Does not use the
    has-hypercube targetRole; that locates the cube/dimension network.
    """
    seen: set[tuple[str, str]] = set()
    out: set[str] = set()
    dq: deque[tuple[str, str]] = deque([(start_elr, start_qn)])
    while dq:
        elr, node = dq.popleft()
        key = (elr, node)
        if key in seen:
            continue
        seen.add(key)
        for rec in idx.get((elr, AR_DM, node), []):
            if rec["from"] not in PI_TREE_NODES or rec["to"] not in PI_TREE_NODES:
                continue
            out.add(rec["to"])
            dq.append((rec["targetRole"] or elr, rec["to"]))
    return out


def _constraint_semantic_key(c: dict[str, Any]) -> tuple:
    """Filing identity of one inherited has-hypercube constraint.

    Source primary item, ELR, and physical origin are not part of identity.
    Resolved cube, sign, closed, context side, usable members, and defaults are.
    """
    dimensions = tuple(
        sorted(
            (
                dim,
                tuple(sorted(dinfo.get("usable") or ())),
                dinfo.get("default"),
            )
            for dim, dinfo in (c.get("dimensions") or {}).items()
        )
    )
    return (
        c["cube"],
        c["sign"],
        bool(c["closed"]),
        c["contextElement"],
        dimensions,
    )


def _dedupe_constraints(cubes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one representative of each filing-equivalent cube constraint."""
    unique: dict[tuple, dict[str, Any]] = {}
    for rec in cubes:
        unique.setdefault(_constraint_semantic_key(rec), rec)
    return [unique[key] for key in sorted(unique)]


def effective_model(parsed: dict[str, Any], schemas: dict[str, dict[str, Any]]) -> dict[str, Any]:
    arcs = parsed["arcs"]
    idx = _index_arcs(arcs)

    defaults: dict[str, str] = {}
    for rec in arcs:
        if rec["arcrole"] == AR_DEF:
            defaults[rec["from"]] = rec["to"]

    # Collect has-hypercube attachments per primary item (direct only).
    direct: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in arcs:
        if rec["arcrole"] in HAS_HC:
            if rec["contextElement"] not in {"segment", "scenario"}:
                raise DrsError(f"has-hypercube missing contextElement on {rec['from']}")
            cube_elr = rec["targetRole"] or rec["elr"]
            dims = _cube_dimensions(idx, cube_elr, rec["to"])
            for dim, dinfo in dims.items():
                if dim in defaults:
                    dinfo["default"] = defaults[dim]
            direct[rec["from"]].append(
                {
                    "cube": rec["to"],
                    "sign": "all" if rec["arcrole"] == AR_ALL else "notAll",
                    "closed": rec["closed"],
                    "closed_present": rec.get("closed_present", False),
                    "contextElement": rec["contextElement"],
                    "elr": rec["elr"],
                    "arcrole": rec["arcrole"],
                    "dimensions": {k: {"usable": sorted(v["usable"]), "nonusable": sorted(v.get("nonusable", ())), **({"default": v["default"]} if "default" in v else {})} for k, v in dims.items()},
                }
            )

    items = [
        qn
        for qn, info in schemas["by_qn"].items()
        if info.get("substitutionGroup") == XBRLI_ITEM
    ]

    constraints_by_item: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for source, cubes in direct.items():
        for rec in cubes:
            constraints_by_item[source].append(rec)
            for descendant in _pi_descendants(idx, rec["elr"], source):
                constraints_by_item[descendant].append(rec)

    primary: dict[str, dict[str, list]] = {}
    for qn in items:
        inherited = constraints_by_item.get(qn, [])
        if not inherited:
            continue
        all_c = _dedupe_constraints([c for c in inherited if c["sign"] == "all"])
        not_c = _dedupe_constraints([c for c in inherited if c["sign"] == "notAll"])
        primary[qn] = {"all": all_c, "notAll": not_c}

    return {
        "primary_items": primary,
        "defaults": defaults,
        "hierarchies": _hierarchies(parsed),
        "primary_item_relationships": _primary_item_relationships(parsed),
        "direct_hypercubes": _direct_hypercubes(direct),
    }


def _primary_item_relationships(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for rec in parsed["arcs"]:
        if rec["arcrole"] != AR_DM:
            continue
        if rec["from"] not in PI_TREE_NODES or rec["to"] not in PI_TREE_NODES:
            continue
        out.append(
            {
                "source": rec["from"],
                "target": rec["to"],
                "arcrole": rec["arcrole"],
                "elr": rec["elr"],
                "targetRole": rec.get("targetRole"),
            }
        )
    out.sort(key=lambda x: (x["elr"], x["source"], x["target"], x["arcrole"], x.get("targetRole") or ""))
    return out


def _direct_hypercubes(direct: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    out = []
    for frm, cubes in direct.items():
        if frm not in PI_TREE_NODES:
            continue
        for c in cubes:
            out.append(
                {
                    "source": frm,
                    "cube": c["cube"],
                    "arcrole": c["arcrole"],
                    "elr": c["elr"],
                    "closed": bool(c["closed"]),
                    "contextElement": c["contextElement"],
                }
            )
    out.sort(key=lambda x: (x["source"], x["cube"], x["arcrole"], x["elr"]))
    return out


def _reachable_dm_edges(idx: dict, start_elr: str, start_qn: str) -> list[dict[str, Any]]:
    """Effective domain-member edges under start_qn, including nonusable targets.

    Follows targetRole. Does not drop an edge because its target is absent from
    an expected-member allowlist. A higher-priority prohibited arc is already
    absent from ``idx`` (effective arcs only).
    """
    edges: list[dict[str, Any]] = []
    seen_nodes: set[tuple[str, str]] = set()
    seen_edges: set[tuple[str, str, bool]] = set()
    dq: deque[tuple[str, str]] = deque([(start_elr, start_qn)])
    while dq:
        elr, node = dq.popleft()
        key = (elr, node)
        if key in seen_nodes:
            continue
        seen_nodes.add(key)
        for rec in idx.get((elr, AR_DM, node), []):
            nxt_elr = rec["targetRole"] or elr
            edge = (rec["from"], rec["to"], bool(rec["usable"]))
            if edge not in seen_edges:
                seen_edges.add(edge)
                edges.append({"from": rec["from"], "to": rec["to"], "usable": bool(rec["usable"])})
            dq.append((nxt_elr, rec["to"]))
    edges.sort(key=lambda x: (x["from"], x["to"], not x["usable"]))
    return edges


def _dimension_member_edges(idx: dict, dimension: str) -> list[dict[str, Any]]:
    """Every effective domain-member edge reachable from this dimension's domains."""
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, str, bool]] = set()
    for (elr, arcrole, frm), recs in idx.items():
        if arcrole != AR_DD or frm != dimension:
            continue
        for rec in recs:
            nxt_elr = rec["targetRole"] or elr
            for edge in _reachable_dm_edges(idx, nxt_elr, rec["to"]):
                key = (edge["from"], edge["to"], edge["usable"])
                if key in seen:
                    continue
                seen.add(key)
                found.append(edge)
    found.sort(key=lambda x: (x["from"], x["to"], not x["usable"]))
    return found


def _hierarchies(parsed: dict[str, Any]) -> dict[str, list]:
    idx = _index_arcs(parsed["arcs"])
    return {name: _dimension_member_edges(idx, dim) for name, dim in DIMENSION_TREES.items()}


def _find_dependant_parent(idx: dict, start_elr: str, start_qn: str) -> tuple[str | None, bool]:
    seen: set[tuple[str, str]] = set()
    dq: deque[tuple[str, str]] = deque([(start_elr, start_qn)])
    while dq:
        elr, node = dq.popleft()
        key = (elr, node)
        if key in seen:
            continue
        seen.add(key)
        for rec in idx.get((elr, AR_DM, node), []):
            nxt_elr = rec["targetRole"] or elr
            if rec["to"] == DEPENDANT:
                return rec["from"], bool(rec["usable"])
            dq.append((nxt_elr, rec["to"]))
    return None, False


def require_dependant_outside_member_class(parsed: dict[str, Any]) -> None:
    """Dependant must have no effective domain-member path under MemberClassDomain.

    A usable=false child is still inside the domain. Context rejection of
    Dependant is not sufficient: the structural relationship itself must
    be absent.
    """
    idx = _index_arcs(parsed["arcs"])
    hits: list[tuple[str, str, bool]] = []
    for rec in parsed["arcs"]:
        if rec["arcrole"] != AR_DD:
            continue
        if rec["from"] != MEMBER_CLASS_DIMENSION:
            continue
        nxt_elr = rec["targetRole"] or rec["elr"]
        domain = rec["to"]
        if domain == DEPENDANT:
            hits.append((rec["from"], rec["to"], bool(rec["usable"])))
            continue
        usable, nonusable = _walk_members(idx, nxt_elr, domain)
        if DEPENDANT in usable or DEPENDANT in nonusable:
            parent, parent_usable = _find_dependant_parent(idx, nxt_elr, domain)
            hits.append((parent or domain, DEPENDANT, parent_usable if parent else DEPENDANT in usable))
    if not hits:
        return
    src, tgt, usable = hits[0]
    kind = "usable" if usable else "nonusable"
    raise DrsError(
        f"Dependant must remain outside the 2025 MemberClass domain; "
        f"found {kind} effective domain-member relationship {_local_name(src)} -> {_local_name(tgt)}"
    )


def require_core_geography_other_withdrawn(parsed: dict[str, Any]) -> None:
    """Core lopt:Other must not appear anywhere in the effective Geography network.

    Overseas stays as a nonusable grouping heading. A usable=false child is
    still membership, so core Other is withdrawn rather than merely nonusable.
    ext:Other is a different QName and is not this concept.
    """
    idx = _index_arcs(parsed["arcs"])
    hits: list[tuple[str, str, bool]] = []
    for (elr, arcrole, frm), recs in idx.items():
        if arcrole != AR_DD or frm != GEOGRAPHY_DIMENSION:
            continue
        for rec in recs:
            nxt_elr = rec["targetRole"] or elr
            domain = rec["to"]
            if domain == CORE_OTHER:
                hits.append((frm, domain, bool(rec["usable"])))
            for edge in _reachable_dm_edges(idx, nxt_elr, domain):
                if edge["from"] == CORE_OTHER or edge["to"] == CORE_OTHER:
                    hits.append((edge["from"], edge["to"], bool(edge["usable"])))
    if not hits:
        return
    src, tgt, usable = hits[0]
    kind = "usable" if usable else "nonusable"
    raise DrsError(
        "core geography Other is withdrawn and must not appear in the Geography domain; "
        f"found {kind} effective relationship {_local_name(src)} -> {_local_name(tgt)}"
    )


def _ctx_map(pairs: list[list[str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for dim, mem in pairs:
        if dim in out:
            raise DrsError(f"duplicate dimension in context: {dim}")
        out[dim] = mem
    return out


def evaluate_context(model: dict[str, Any], concept: str, segment: list[list[str]], scenario: list[list[str]]) -> str:
    """Return 'accept' or 'reject' for one explicit-member context."""
    cons = model["primary_items"].get(concept)
    if cons is None:
        # Unbound primary item: no dimensional constraints. Inheritance
        # mutants that detach a child from the statement tree land here.
        return "accept"

    seg = _ctx_map(segment)
    sce = _ctx_map(scenario)

    def side_map(element: str) -> dict[str, str]:
        return seg if element == "segment" else sce

    for cube in cons["all"]:
        ctx = side_map(cube["contextElement"])
        cube_dims = set(cube["dimensions"])
        extra = set(ctx) - cube_dims
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
            else:
                if default is not None:
                    continue
                # All-hypercube dimensions are required by explicit member or default.
                # Open/closed only controls extra dimensions, not omitted ones.
                return "reject"
    for cube in cons["notAll"]:
        ctx = side_map(cube["contextElement"])
        cube_dims = set(cube["dimensions"])
        extra = set(ctx) - cube_dims
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


def canonicalize_model(model: dict[str, Any]) -> dict[str, Any]:
    """Order-insensitive form for comparison."""
    items = {}
    for pi, cons in model["primary_items"].items():
        def dump(cubes: list[dict[str, Any]]) -> list[dict[str, Any]]:
            out = []
            for c in cubes:
                dims = {}
                for dim, dinfo in c["dimensions"].items():
                    rec = {
                        "usable": sorted(dinfo["usable"]),
                    }
                    if dinfo.get("default"):
                        rec["default"] = dinfo["default"]
                    dims[dim] = rec
                out.append(
                    {
                        "cube": c["cube"],
                        "closed": bool(c["closed"]),
                        "contextElement": c["contextElement"],
                        "dimensions": dims,
                    }
                )
            out.sort(key=lambda x: (x["cube"], x["contextElement"], x["closed"]))
            return out

        items[pi] = {"all": dump(cons["all"]), "notAll": dump(cons["notAll"])}
    trees = {}
    for name, pairs in (model.get("hierarchies") or {}).items():
        trees[name] = sorted(
            [{"from": p["from"], "to": p["to"], "usable": bool(p["usable"])} for p in pairs],
            key=lambda x: (x["from"], x["to"], x["usable"]),
        )
    pi_rels = sorted(
        [
            {
                "source": r["source"],
                "target": r["target"],
                "arcrole": r["arcrole"],
                "elr": r["elr"],
                "targetRole": r.get("targetRole"),
            }
            for r in (model.get("primary_item_relationships") or [])
        ],
        key=lambda x: (x["elr"], x["source"], x["target"], x["arcrole"], x.get("targetRole") or ""),
    )
    directs = sorted(
        [
            {
                "source": r["source"],
                "cube": r["cube"],
                "arcrole": r["arcrole"],
                "elr": r["elr"],
                "closed": bool(r.get("closed")),
                "contextElement": r["contextElement"],
            }
            for r in (model.get("direct_hypercubes") or [])
        ],
        key=lambda x: (x["source"], x["cube"], x["arcrole"], x["elr"]),
    )
    return {
        "primary_items": items,
        "hierarchies": trees,
        "primary_item_relationships": pi_rels,
        "direct_hypercubes": directs,
    }


def models_equal(got: dict[str, Any], expected: dict[str, Any]) -> tuple[bool, str]:
    g = canonicalize_model(got)
    e = canonicalize_model(expected)
    if g.get("hierarchies") != e.get("hierarchies"):
        return False, "member hierarchy differs (grouping headings must not be flattened)"
    if e.get("primary_item_relationships"):
        # ELR and targetRole stay in the records for traversal and debugging.
        # Answer identity is the conceptual edge. Equivalent role organization
        # is accepted when effective constraints still match.
        got_pi = {
            (r["source"], r["target"], r["arcrole"])
            for r in g.get("primary_item_relationships") or []
        }
        for rec in e["primary_item_relationships"]:
            key = (rec["source"], rec["target"], rec["arcrole"])
            if key not in got_pi:
                return False, (
                    f"missing required primary-item relationship {_local_name(rec['source'])} -> {_local_name(rec['target'])}"
                )
    if e.get("direct_hypercubes"):
        got_hc = {(r["source"], r["cube"], r["arcrole"]) for r in g.get("direct_hypercubes") or []}
        for rec in e["direct_hypercubes"]:
            key = (rec["source"], rec["cube"], rec["arcrole"])
            if key not in got_hc:
                return False, (
                    f"missing required direct has-hypercube {_local_name(rec['source'])} -> {_local_name(rec['cube'])}"
                )
    g_cmp = {k: g[k] for k in ("primary_items", "hierarchies")}
    e_cmp = {k: e[k] for k in ("primary_items", "hierarchies")}
    if g_cmp == e_cmp:
        return True, ""
    g_pis = set(g["primary_items"])
    e_pis = set(e["primary_items"])
    if g_pis != e_pis:
        return False, f"primary items differ extra={sorted(g_pis-e_pis)} missing={sorted(e_pis-g_pis)}"
    for pi in sorted(e_pis):
        if g["primary_items"][pi] != e["primary_items"][pi]:
            return False, f"effective constraints differ for {pi}"
    return False, "effective DRS differs"


def parse_instance_members(path: Path) -> dict[str, Any]:
    """Desk XML is not graded. Goldens are JSON fixtures."""
    raise DrsError("instance XML parsing is not used in grading")
