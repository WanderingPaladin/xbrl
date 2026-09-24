"""Safe unpack and submitted-package DTS composition.

The candidate ZIP is the only source of schemas and linkbases used for
semantic grading. Verifier-owned copies are not consulted.
"""

from __future__ import annotations

import json
import posixpath
import shutil
import stat
import zipfile
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from lxml import etree
from runner.xmlbool import BooleanError, parse_optional_xs_boolean, parse_xs_boolean

NS_XS = "http://www.w3.org/2001/XMLSchema"
NS_LINK = "http://www.xbrl.org/2003/linkbase"
NS_XLINK = "http://www.w3.org/1999/xlink"
NS_TP = "http://xbrl.org/2016/taxonomy-package"
NS_CAT = "urn:oasis:names:tc:entity:xmlns:xml:catalog"
NS_XBRLI = "http://www.xbrl.org/2003/instance"
NS_XBRLDT = "http://xbrl.org/2005/xbrldt"
NS_XBRLDT_WWW = "http://www.xbrl.org/2005/xbrldt"

NS_LOPT = "http://lattice.example/lopt/2025-01-31"
NS_EXT = "http://lattice.example/lopt/ext/2025-01-31"
NS_XL = "http://www.xbrl.org/2003/XLink"
NS_XBRLDI = "http://xbrl.org/2006/xbrldi"
NS_XML = "http://www.w3.org/XML/1998/namespace"
PACKAGE_ID = "http://lattice.example/lopt/taxonomy-package/2025"
ENTRY_REL = "taxonomy/lopt-2025.xsd"
ENTRY_URI = "http://lattice.example/lopt/2025-01-31/lopt-2025.xsd"
EXT_URI = "http://lattice.example/lopt/ext/2025-01-31/lopt-2025-ext.xsd"
MANDATORY_2025_RESOURCE_URIS = frozenset({ENTRY_URI, EXT_URI})
XBRLI_SCHEMA_URI = "http://www.xbrl.org/2003/xbrl-instance-2003-12-31.xsd"
LINKBASE_SCHEMA_URI = "http://www.xbrl.org/2003/xbrl-linkbase-2003-12-31.xsd"
XL_SCHEMA_URI = "http://www.xbrl.org/2003/xl-2003-12-31.xsd"
XLINK_SCHEMA_URI = "http://www.xbrl.org/2003/xlink-2003-12-31.xsd"
XBRLDT_SCHEMA_URI = "http://www.xbrl.org/2005/xbrldt-2005.xsd"
XBRLDI_SCHEMA_URI = "http://www.xbrl.org/2006/xbrldi-2006.xsd"
XBRLDI_SCHEMA_URI_ALT = "http://xbrl.org/2006/xbrldi-2006.xsd"
XML_SCHEMA_URI = "http://www.w3.org/2001/xml.xsd"
DEF_LINKBASE_ROLE = "http://www.xbrl.org/2003/role/definitionLinkbaseRef"

# One identity map. Mandatory contract URIs are a subset. Alternate starter
# aliases are checked only when the submitted DTS actually references them.
SCHEMA_IDENTITY_SPECS = {
    ENTRY_URI: {"label": "canonical core entry", "target_namespace": NS_LOPT, "role": "entry"},
    EXT_URI: {"label": "canonical extension", "target_namespace": NS_EXT, "role": "extension"},
    XBRLI_SCHEMA_URI: {"label": "canonical XBRL instance", "target_namespace": NS_XBRLI, "role": "standard"},
    LINKBASE_SCHEMA_URI: {"label": "canonical XBRL linkbase", "target_namespace": NS_LINK, "role": "standard"},
    XL_SCHEMA_URI: {"label": "canonical XBRL XL", "target_namespace": NS_XL, "role": "standard"},
    XLINK_SCHEMA_URI: {"label": "canonical XLink", "target_namespace": NS_XLINK, "role": "standard"},
    XBRLDT_SCHEMA_URI: {"label": "canonical XBRL Dimensions", "target_namespace": NS_XBRLDT, "role": "standard"},
    XBRLDI_SCHEMA_URI: {"label": "canonical XBRLDI", "target_namespace": NS_XBRLDI, "role": "standard"},
    XBRLDI_SCHEMA_URI_ALT: {"label": "canonical XBRLDI", "target_namespace": NS_XBRLDI, "role": "standard"},
    XML_SCHEMA_URI: {"label": "canonical XML", "target_namespace": NS_XML, "role": "standard"},
}
MANDATORY_STANDARD_SCHEMA_SPECS = {
    uri: SCHEMA_IDENTITY_SPECS[uri]
    for uri in (
        XBRLI_SCHEMA_URI,
        LINKBASE_SCHEMA_URI,
        XL_SCHEMA_URI,
        XLINK_SCHEMA_URI,
        XBRLDT_SCHEMA_URI,
        XBRLDI_SCHEMA_URI,
        XML_SCHEMA_URI,
    )
}
MANDATORY_STANDARD_SCHEMA_URIS = frozenset(MANDATORY_STANDARD_SCHEMA_SPECS)
REFERENCED_SCHEMA_SPECS = SCHEMA_IDENTITY_SPECS


class PackageError(ValueError):
    pass


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return stat.S_ISLNK(mode) if mode else False


def unpack_taxonomy_zip(zip_path: Path, dest: Path) -> Path:
    if zip_path.is_symlink():
        raise PackageError("deliverable must not be a symlink")
    if not zipfile.is_zipfile(zip_path):
        raise PackageError(f"{zip_path} is not a zip archive")
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        members: list[tuple[zipfile.ZipInfo, str, list[str]]] = []
        tops: set[str] = set()
        for info in zf.infolist():
            if _is_zip_symlink(info):
                raise PackageError(f"symlink in zip: {info.filename}")
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or name.startswith("\\"):
                raise PackageError(f"absolute path in zip: {name}")
            parts = [p for p in name.split("/") if p]
            if any(p == ".." for p in parts):
                raise PackageError(f"path traversal in zip: {name}")
            if parts:
                tops.add(parts[0])
            members.append((info, name, parts))
        found = ", ".join(sorted(tops)) or "(empty)"
        if len(tops) != 1:
            raise PackageError(
                f"taxonomy package ZIP must contain exactly one top-level directory; found: {found}"
            )
        sole = next(iter(tops))
        saw_directory = False
        saw_top_file = False
        for info, name, parts in members:
            if not parts or parts[0] != sole:
                continue
            if len(parts) > 1 or info.is_dir() or name.endswith("/"):
                saw_directory = True
            else:
                saw_top_file = True
        if saw_top_file or not saw_directory:
            raise PackageError(
                f"taxonomy package ZIP must contain exactly one top-level directory; found: {sole}"
            )
        for info, name, parts in members:
            if not parts or info.is_dir() or name.endswith("/"):
                continue
            target = dest.joinpath(*parts)
            try:
                target.resolve().relative_to(dest.resolve())
            except ValueError as exc:
                raise PackageError(f"path escapes unpack root: {name}") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out, length=1024 * 1024)
    return dest


def find_taxonomy_package_root(extraction_root: Path) -> Path:
    """The archive must contain exactly one top-level directory, and that directory is the package."""
    root = extraction_root.resolve()
    entries = [child for child in root.iterdir()]
    names = ", ".join(sorted(child.name for child in entries)) or "(empty)"
    if len(entries) != 1 or not entries[0].is_dir():
        raise PackageError(
            f"taxonomy package must contain exactly one top-level directory; found: {names}"
        )
    package_root = entries[0].resolve()
    try:
        package_root.relative_to(root)
    except ValueError as exc:
        raise PackageError("top-level package directory escapes extraction root") from exc
    if not (package_root / "META-INF" / "catalog.xml").is_file():
        raise PackageError("top-level package directory is missing META-INF/catalog.xml")
    return package_root


def safe_join(root: Path, *parts: str) -> Path:
    root_r = root.resolve()
    candidate = root_r.joinpath(*parts)
    try:
        candidate.resolve().relative_to(root_r)
    except ValueError as exc:
        raise PackageError(f"path escapes package: {parts}") from exc
    return candidate.resolve()


def resolve_href(root: Path, base_file: Path, href: str) -> Path:
    href = (href or "").strip()
    if not href:
        raise PackageError("empty href")
    if href.startswith("file:"):
        raise PackageError(f"file URI not allowed: {href}")
    parsed = urlparse(href)
    if parsed.scheme in {"http", "https"}:
        raise PackageError(f"unmapped remote href: {href}")
    if parsed.scheme and parsed.scheme not in {"", "file"}:
        raise PackageError(f"unsupported href scheme: {href}")
    path_part = href.split("#", 1)[0]
    if not path_part:
        return base_file
    if path_part.startswith("/") or posixpath.isabs(path_part):
        raise PackageError(f"absolute href: {href}")
    joined = posixpath.normpath(posixpath.join(base_file.parent.relative_to(root).as_posix(), path_part))
    if joined.startswith(".."):
        raise PackageError(f"href escapes package: {href}")
    target = safe_join(root, *joined.split("/"))
    if not target.is_file():
        raise PackageError(f"href does not exist in package: {href} -> {joined}")
    return target


def resolve_href_or_catalog(root: Path, base_file: Path, href: str) -> Path:
    href = (href or "").strip()
    if not href:
        raise PackageError("empty href")
    if href.startswith("file:"):
        raise PackageError(f"file URI not allowed: {href}")
    path_part = href.split("#", 1)[0]
    if path_part.startswith("http://") or path_part.startswith("https://"):
        mapped = resolve_catalog_uri(root, path_part)
        if mapped is None:
            raise PackageError(
                f"catalog does not resolve required URI {path_part} to an existing packaged file"
            )
        return mapped
    return resolve_href(root, base_file, href)


def _xml_ns(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    if tag.startswith("{") and "}" in tag:
        return tag[1:].split("}", 1)[0]
    return ""


def _local(tag: object) -> str | None:
    """Local name of an element. Comments and processing instructions are not elements."""
    if not isinstance(tag, str):
        return None
    return tag.split("}", 1)[-1]


def parse_taxonomy_package_xml(root: Path) -> dict:
    path = root / "META-INF" / "taxonomyPackage.xml"
    if not path.is_file():
        raise PackageError("missing META-INF/taxonomyPackage.xml")
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        raise PackageError(f"taxonomyPackage.xml is not well-formed XML: {exc}") from exc
    el = tree.getroot()
    tag = el.tag
    if _local(tag) != "taxonomyPackage":
        raise PackageError(f"taxonomyPackage root namespace/name is {tag}")
    if _xml_ns(tag) != NS_TP:
        raise PackageError("taxonomyPackage.xml must use namespace http://xbrl.org/2016/taxonomy-package")
    ident = el.find(f"{{{NS_TP}}}identifier")
    if ident is None or (ident.text or "").strip() != PACKAGE_ID:
        raise PackageError("taxonomy package identifier is not http://lattice.example/lopt/taxonomy-package/2025")
    docs = list(el.findall(f".//{{{NS_TP}}}entryPointDocument"))
    if not docs:
        raise PackageError("taxonomyPackage.xml has no entryPointDocument")
    resolved: list[Path] = []
    rels: list[str] = []
    hrefs: list[str] = []
    for doc in docs:
        href = doc.get("href")
        if not href:
            raise PackageError("entryPointDocument missing href")
        hrefs.append(href)
        try:
            entry = resolve_href_or_catalog(root, path, href)
        except PackageError as exc:
            raise PackageError(
                f"taxonomy package entry point {href} did not resolve through catalog to {ENTRY_REL}"
            ) from exc
        rel = entry.relative_to(root.resolve()).as_posix()
        resolved.append(entry)
        rels.append(rel)
    matches = [p for p, rel in zip(resolved, rels) if rel == ENTRY_REL]
    if not matches:
        raise PackageError(
            f"taxonomy package entry point {hrefs} did not resolve through catalog to {ENTRY_REL}"
        )
    return {
        "identifier": PACKAGE_ID,
        "entry": matches[0],
        "entries": resolved,
        "rels": rels,
        "hrefs": hrefs,
    }


def parse_catalog_xml(root: Path) -> dict:
    path = root / "META-INF" / "catalog.xml"
    if not path.is_file():
        raise PackageError("missing META-INF/catalog.xml")
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        raise PackageError(f"catalog.xml is not well-formed XML: {exc}") from exc
    el = tree.getroot()
    ns = _xml_ns(el.tag)
    if ns != NS_CAT:
        raise PackageError("catalog.xml must use namespace urn:oasis:names:tc:entity:xmlns:xml:catalog")
    rewrites: list[tuple[str, str]] = []
    exact: list[tuple[str, Path]] = []
    for node in el.findall(f"{{{NS_CAT}}}rewriteURI"):
        start = node.get("uriStartString")
        prefix = node.get("rewritePrefix")
        if not start or not prefix:
            raise PackageError("rewriteURI missing uriStartString or rewritePrefix")
        dest = (path.parent / prefix).resolve()
        try:
            dest.relative_to(root.resolve())
        except ValueError as exc:
            raise PackageError(f"catalog rewritePrefix escapes package: {prefix}") from exc
        rewrites.append((start, prefix))
    for node in el.findall(f"{{{NS_CAT}}}uri"):
        name = node.get("name")
        href = node.get("uri")
        if not name or not href:
            raise PackageError("catalog uri missing name or uri")
        target = resolve_href(root, path, href)
        if not target.is_file():
            raise PackageError(
                f"catalog does not resolve required URI {name} to an existing packaged file"
            )
        exact.append((name, target))
    rewrites.sort(key=lambda item: len(item[0]), reverse=True)
    return {"rewrites": rewrites, "exact": exact, "path": path}


def resolve_catalog_uri(root: Path, uri: str) -> Path | None:
    catalog = parse_catalog_xml(root)
    cat_path: Path = catalog["path"]
    root_r = root.resolve()
    for name, target in catalog["exact"]:
        if uri == name:
            return target
    for start, prefix in catalog["rewrites"]:
        if not uri.startswith(start):
            continue
        rest = uri[len(start) :].lstrip("/")
        base_rel = posixpath.normpath(
            posixpath.join(cat_path.parent.relative_to(root_r).as_posix(), prefix)
        )
        if base_rel.startswith(".."):
            raise PackageError(f"catalog mapped URI escapes package: {uri}")
        base = safe_join(root, *base_rel.split("/")) if base_rel not in {".", ""} else root_r
        if base.is_file():
            target = base
        elif rest:
            joined = posixpath.normpath(posixpath.join(base_rel, rest))
            if joined.startswith(".."):
                raise PackageError(f"catalog mapped URI escapes package: {uri}")
            target = safe_join(root, *joined.split("/"))
        else:
            target = base
        try:
            target.relative_to(root_r)
        except ValueError as exc:
            raise PackageError(f"catalog mapped URI escapes package: {uri}") from exc
        if not target.is_file():
            raise PackageError(
                f"catalog does not resolve required URI {uri} to an existing packaged file"
            )
        return target
    return None


def catalog_map(root: Path, uri: str) -> Path | None:
    return resolve_catalog_uri(root, uri)


def _schema_import_targets(root: Path, schemas: dict, namespace: str, sources: list[dict] | None = None) -> set[Path]:
    found: set[Path] = set()
    for sch in sources if sources is not None else schemas["schemas"]:
        for imp in sch["imports"]:
            if imp.get("namespace") != namespace or not imp.get("schemaLocation"):
                continue
            target = follow_schema_location(root, sch["path"], imp["schemaLocation"])
            if target is not None:
                found.add(target.resolve())
    return found


def _linkbase_ref_targets(root: Path, linkbases: list[Path], local_name: str) -> set[Path]:
    found: set[Path] = set()
    for linkbase in linkbases:
        try:
            tree = ET.parse(linkbase)
        except ET.ParseError as exc:
            raise PackageError(f"malformed definition linkbase {linkbase.name}: {exc}") from exc
        for node in tree.getroot().iter():
            if _local(node.tag) != local_name:
                continue
            href = node.get(f"{{{NS_XLINK}}}href") or node.get("href")
            if not href:
                continue
            found.add(resolve_href_or_catalog(root, linkbase, href).resolve())
    return found


def _require_schema_namespace(mapped: Path, label: str, expected_namespace: str) -> dict:
    parsed = parse_schema_file(mapped)
    if _xml_ns(parsed["root"].tag) != NS_XS:
        raise PackageError(f"catalog {label} URI did not resolve to an XML Schema")
    got = parsed["targetNamespace"]
    if got != expected_namespace:
        raise PackageError(
            f"catalog {label} URI resolved to schema namespace {got}; expected {expected_namespace}"
        )
    return parsed


def assert_catalog_coverage(
    root: Path,
    uris: set[str],
    catalog: dict,
    *,
    entry: Path,
    schemas: dict,
) -> None:
    """Resolve required canonical resources and any HTTP URI the submitted DTS uses.

    Exact uri entries, directory rewrites, and file-specific rewrites are all
    valid when resolve_catalog_uri maps the URI onto the right packaged file.
    """
    del catalog
    required = set(MANDATORY_2025_RESOURCE_URIS)
    required.update(MANDATORY_STANDARD_SCHEMA_URIS)
    required.update(uris)
    for uri in sorted(required):
        mapped = resolve_catalog_uri(root, uri)
        if mapped is None:
            raise PackageError(
                f"catalog does not resolve required URI {uri} to an existing packaged file"
            )
        spec = SCHEMA_IDENTITY_SPECS.get(uri)
        if spec is None:
            continue
        label = spec["label"]
        mapped_path = mapped.resolve()
        _require_schema_namespace(mapped_path, label, spec["target_namespace"])
        role = spec["role"]
        if role == "entry":
            if mapped_path != entry.resolve():
                raise PackageError(
                    f"catalog {label} URI does not resolve to taxonomy/lopt-2025.xsd"
                )
        elif role == "extension":
            targets = _schema_import_targets(root, schemas, NS_EXT, sources=[schemas["entry"]])
            if not targets:
                targets = _schema_import_targets(root, schemas, NS_EXT)
            if mapped_path not in targets:
                raise PackageError(
                    "catalog canonical extension URI does not resolve to the extension "
                    "schema participating in the submitted DTS"
                )
        else:
            targets = _schema_import_targets(root, schemas, spec["target_namespace"])
            if targets and mapped_path not in targets:
                raise PackageError(
                    f"catalog {label} URI does not resolve to the schema participating in the submitted DTS"
                )


def require_entry_xsd(root: Path) -> Path:
    path = root / "taxonomy" / "lopt-2025.xsd"
    if not path.is_file():
        raise PackageError("package is missing the exact entry point taxonomy/lopt-2025.xsd")
    return path.resolve()


def parse_xml_with_ns_context(path: Path):
    """Parse XML with lxml so in-scope xmlns prefix bindings are available."""
    try:
        return etree.parse(str(path))
    except etree.XMLSyntaxError as exc:
        raise PackageError(f"malformed XML {path.name}: {exc}") from exc


def expand_lexical_qname(value: str | None, element) -> str | None:
    """Expand a QName-valued attribute using the node's actual in-scope nsmap.

    Prefixed names use the bound prefix. Unprefixed names use the in-scope
    default namespace (lxml key None), matching XML Schema QName attributes
    such as type and substitutionGroup.
    """
    if not value:
        return value
    return expand_qname(value, dict(element.nsmap or {}))


def expand_qname(value: str | None, nsmap: dict[str | None, str | None], *, require_bound: bool = True) -> str | None:
    """Expand a lexical QName against an explicit namespace map to Clark notation."""
    if not value:
        return value
    if value.startswith("{") and "}" in value:
        ns, local = value[1:].split("}", 1)
        if ns in {NS_XBRLDT, NS_XBRLDT_WWW}:
            ns = NS_XBRLDT
        return f"{{{ns}}}{local}"
    if ":" in value:
        pfx, local = value.split(":", 1)
        ns = nsmap.get(pfx)
        if ns in {NS_XBRLDT, NS_XBRLDT_WWW}:
            ns = NS_XBRLDT
        if not ns:
            if require_bound:
                raise PackageError(f"unbound QName prefix {pfx!r} in {value!r}")
            return value
        return f"{{{ns}}}{local}"
    default_ns = nsmap.get(None)
    if default_ns is None:
        default_ns = nsmap.get("")
    if default_ns:
        ns = NS_XBRLDT if default_ns in {NS_XBRLDT, NS_XBRLDT_WWW} else default_ns
        return f"{{{ns}}}{value}"
    return value


def concept_signature(info: dict) -> dict:
    return {
        "qname": info["qname"],
        "name": info["name"],
        "type": info.get("type"),
        "substitutionGroup": info.get("substitutionGroup"),
        "abstract": bool(info.get("abstract")),
        "nillable": bool(info.get("nillable")),
        "periodType": info.get("periodType"),
    }


def parse_schema_file(path: Path) -> dict:
    tree = parse_xml_with_ns_context(path)
    el = tree.getroot()
    if _local(el.tag) != "schema":
        raise PackageError(f"{path.name} is not an XML Schema")
    tns = el.get("targetNamespace")
    by_id = {}
    by_qn = {}
    imports = []
    includes = []
    linkbase_refs = []
    role_types = {}
    for child in el:
        if child.tag == f"{{{NS_XS}}}element" and child.get("name"):
            name = child.get("name")
            eid = child.get("id")
            qn = f"{{{tns}}}{name}" if tns else name
            try:
                abstract, _ = parse_optional_xs_boolean(child.get("abstract"), False)
                nillable, _ = parse_optional_xs_boolean(child.get("nillable"), False)
            except BooleanError as exc:
                raise PackageError(str(exc)) from exc
            period = child.get(f"{{{NS_XBRLI}}}periodType") or child.get("periodType")
            if not period:
                for k, v in child.attrib.items():
                    if not isinstance(k, str):
                        continue
                    if k.endswith("}periodType") or k == "periodType":
                        period = v
            info = {
                "id": eid,
                "name": name,
                "ns": tns,
                "qname": qn,
                "abstract": abstract,
                "nillable": nillable,
                "type": expand_lexical_qname(child.get("type"), child),
                "substitutionGroup": expand_lexical_qname(child.get("substitutionGroup") or "", child)
                or "",
                "periodType": period,
                "path": path.name,
                "file": path,
            }
            if eid:
                by_id[eid] = info
            by_qn[qn] = info
        elif child.tag == f"{{{NS_XS}}}import":
            imports.append(
                {
                    "namespace": child.get("namespace"),
                    "schemaLocation": child.get("schemaLocation"),
                }
            )
        elif child.tag == f"{{{NS_XS}}}include":
            includes.append({"schemaLocation": child.get("schemaLocation")})
    for child in el.iter():
        loc = _local(child.tag)
        if loc is None:
            continue
        if loc == "linkbaseRef":
            linkbase_refs.append(
                {
                    "href": child.get(f"{{{NS_XLINK}}}href") or child.get("href"),
                    "role": child.get(f"{{{NS_XLINK}}}role") or child.get("role"),
                }
            )
        elif loc == "roleType":
            uri = child.get("roleURI")
            rid = child.get("id")
            if rid:
                by_id[rid] = {
                    "id": rid,
                    "name": child.get("name") or rid,
                    "ns": tns,
                    "qname": f"{{{tns}}}{rid}" if tns else rid,
                    "abstract": False,
                    "nillable": False,
                    "type": None,
                    "substitutionGroup": "",
                    "periodType": None,
                    "path": path.name,
                    "file": path,
                }
            if uri:
                role_types[uri] = rid
        elif loc == "arcroleType" and child.get("id"):
            eid = child.get("id")
            by_id[eid] = {
                "id": eid,
                "name": child.get("name") or eid,
                "ns": tns,
                "qname": f"{{{tns}}}{eid}" if tns else eid,
                "abstract": False,
                "nillable": False,
                "type": None,
                "substitutionGroup": "",
                "periodType": None,
                "path": path.name,
                "file": path,
            }
    return {
        "path": path,
        "targetNamespace": tns,
        "by_id": by_id,
        "by_qn": by_qn,
        "imports": imports,
        "includes": includes,
        "linkbase_refs": linkbase_refs,
        "role_types": role_types,
        "root": el,
    }


def follow_schema_location(root: Path, from_schema: Path, location: str | None) -> Path | None:
    if not location:
        return None
    if location.startswith("http://") or location.startswith("https://"):
        mapped = resolve_catalog_uri(root, location)
        if mapped is None:
            raise PackageError(
                f"catalog does not resolve required URI {location} to an existing packaged file"
            )
        return mapped
    return resolve_href(root, from_schema, location)


def collect_submitted_schemas(root: Path, entry: Path) -> dict:
    queued = [entry]
    seen: set[Path] = set()
    schemas = []
    while queued:
        path = queued.pop()
        path = path.resolve()
        if path in seen:
            continue
        seen.add(path)
        parsed = parse_schema_file(path)
        schemas.append(parsed)
        for imp in parsed["imports"] + parsed["includes"]:
            loc = imp.get("schemaLocation")
            if not loc:
                if "namespace" in imp:
                    raise PackageError(f"{path.name} import missing schemaLocation")
                continue
            target = follow_schema_location(root, path, loc)
            if target is not None:
                queued.append(target)
    by_id = {}
    by_qn = {}
    role_types = {}
    for sch in schemas:
        for eid, info in sch["by_id"].items():
            by_id[eid] = info
        by_qn.update(sch["by_qn"])
        role_types.update(sch["role_types"])
    entry_parsed = next(s for s in schemas if s["path"] == entry.resolve())
    if entry_parsed["targetNamespace"] != NS_LOPT:
        raise PackageError(
            f"entry schema targetNamespace must be {NS_LOPT}, got {entry_parsed['targetNamespace']}"
        )
    ext_present = any(s["targetNamespace"] == NS_EXT for s in schemas)
    if not ext_present:
        raise PackageError("submitted DTS does not include the 2025 extension schema")
    if f"{{{NS_EXT}}}Other" not in by_qn:
        raise PackageError("submitted extension schema missing ext:Other")
    return {
        "schemas": schemas,
        "by_id": by_id,
        "by_qn": by_qn,
        "role_types": role_types,
        "entry": entry_parsed,
    }


def _fixtures_dir() -> Path:
    packed = Path("/tests/fixtures")
    if packed.is_dir():
        return packed
    return Path(__file__).resolve().parents[1] / "fixtures"


_SIGNATURE_FIELDS = ("type", "substitutionGroup", "abstract", "nillable", "periodType")


def assert_concepts_preserved(schemas: dict) -> None:
    """Every required 2025 concept keeps its semantic declaration signature."""
    by_qn = schemas["by_qn"]
    fx = _fixtures_dir()
    required = json.loads((fx / "required_concepts.json").read_text(encoding="utf-8"))
    for rec in required:
        qn = rec["qname"]
        if qn not in by_qn:
            raise PackageError(f"submitted schema is missing required 2025 concept {qn}")
        got = concept_signature(by_qn[qn])
        for field in _SIGNATURE_FIELDS:
            if field not in rec:
                raise PackageError(f"sealed signature for {qn} is missing {field}")
            local = qn.rsplit("}", 1)[-1]
            if got.get(field) != rec[field]:
                if field == "abstract":
                    raise PackageError(
                        f"{local} abstract mismatch: expected {str(rec[field]).lower()}, "
                        f"got {str(got.get(field)).lower()}"
                    )
                raise PackageError(
                    f"required 2025 concept {local} changed {field}: "
                    f"expected {rec[field]!r} got {got.get(field)!r}"
                )


def external_definition_linkbases_from_dts(root: Path, schemas: dict) -> list[Path]:
    """External definition linkbases referenced by any schema reachable from the entry.

    An empty list is valid when definition relationships are embedded in a
    reachable schema instead.
    """
    found: list[Path] = []
    seen: set[Path] = set()
    root_r = root.resolve()
    for schema in schemas["schemas"]:
        schema_path = Path(schema["path"])
        for ref in schema.get("linkbase_refs", []):
            href = ref.get("href")
            if not href or ref.get("role") != DEF_LINKBASE_ROLE:
                continue
            try:
                target = resolve_href_or_catalog(root, schema_path, href)
            except PackageError as exc:
                raise PackageError(
                    f"definition linkbaseRef {href} in {schema_path.name} did not resolve: {exc}"
                ) from exc
            resolved = target.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(resolved)
    return sorted(found, key=lambda path: path.relative_to(root_r).as_posix())


def definition_linkbases_from_dts(root: Path, schemas: dict) -> list[Path]:
    """Compatibility name for external definition-linkbase discovery."""
    return external_definition_linkbases_from_dts(root, schemas)


def collect_http_locations(value: str | None) -> str | None:
    if not value:
        return None
    path_part = value.split("#", 1)[0].strip()
    if path_part.startswith("http://") or path_part.startswith("https://"):
        return path_part
    return None


def collect_required_external_uris(
    root: Path,
    schemas: dict,
    linkbases: list[Path],
    entry_hrefs: list[str] | None = None,
    documents: list[dict] | None = None,
) -> set[str]:
    """HTTP(S) locations the submitted DTS actually references.

    Parsed definition documents, including embedded linkbases, are the source
    for roleRef, arcroleRef, and locator hrefs. This set is in addition to the
    mandatory canonical mappings.
    """
    uris: set[str] = set()
    for href in entry_hrefs or []:
        http = collect_http_locations(href)
        if http:
            uris.add(http)
    for sch in schemas["schemas"]:
        for imp in sch["imports"] + sch.get("includes", []):
            http = collect_http_locations(imp.get("schemaLocation"))
            if http:
                uris.add(http)
        for ref in sch.get("linkbase_refs", []):
            http = collect_http_locations(ref.get("href"))
            if http:
                uris.add(http)
    if documents is not None:
        for document in documents:
            hrefs = list((document.get("role_refs") or {}).values())
            hrefs.extend((document.get("arcrole_refs") or {}).values())
            hrefs.extend(document.get("locator_hrefs") or [])
            for href in hrefs:
                http = collect_http_locations(href)
                if http:
                    uris.add(http)
        return uris
    for lb in linkbases:
        try:
            tree = ET.parse(lb)
        except ET.ParseError as exc:
            raise PackageError(f"malformed definition linkbase {lb.name}: {exc}") from exc
        for node in tree.getroot().iter():
            if not isinstance(node.tag, str):
                continue
            href = node.get(f"{{{NS_XLINK}}}href") or node.get("href") or node.get("schemaLocation")
            http = collect_http_locations(href)
            if http:
                uris.add(http)
    return uris


def assert_linkbase_refs_in_package(root: Path, document: dict) -> None:
    """Resolve roleRef and arcroleRef hrefs from the linkbase that declared them."""
    linkbase_path = Path(document["path"])
    role_refs = document.get("role_refs") or {}
    for uri, href in role_refs.items():
        target = resolve_href_or_catalog(root, linkbase_path, href)
        declared = parse_schema_file(target)["role_types"]
        if uri not in declared:
            raise PackageError(f"roleRef {uri} is not declared in {target.name}")
    for uri, href in (document.get("arcrole_refs") or {}).items():
        target = resolve_href_or_catalog(root, linkbase_path, href)
        frag = href.split("#", 1)[1] if "#" in href else ""
        if frag and frag not in parse_schema_file(target)["by_id"]:
            raise PackageError(f"arcroleRef {uri} fragment {frag} missing from {target.name}")
    for uri in set(document.get("used_roles") or ()) | set(document.get("used_target_roles") or ()):
        if uri not in role_refs:
            raise PackageError(
                f"definition linkbase {linkbase_path.name} uses role {uri} without a roleRef in that linkbase"
            )


def load_submitted_dts(root: Path) -> dict:
    root = find_taxonomy_package_root(root)
    catalog = parse_catalog_xml(root)
    meta = parse_taxonomy_package_xml(root)
    entry = require_entry_xsd(root)
    if meta["entry"].resolve() != entry.resolve():
        raise PackageError("taxonomyPackage entryPointDocument does not match taxonomy/lopt-2025.xsd")
    schemas = collect_submitted_schemas(root, entry)
    assert_concepts_preserved(schemas)
    linkbases = external_definition_linkbases_from_dts(root, schemas)
    try:
        from runner.drs import (
            DrsError,
            parse_definition_sources,
            require_core_geography_other_withdrawn,
            require_dependant_outside_member_class,
            require_hypercube_closed_semantics,
            require_no_legacy_cube_attachments,
        )

        parsed = parse_definition_sources(linkbases, schemas)
        for document in parsed["documents"]:
            assert_linkbase_refs_in_package(root, document)
        require_hypercube_closed_semantics(parsed)
        require_dependant_outside_member_class(parsed)
        require_core_geography_other_withdrawn(parsed)
        require_no_legacy_cube_attachments(parsed)
    except DrsError as exc:
        raise PackageError(str(exc)) from exc
    uris = collect_required_external_uris(
        root,
        schemas,
        linkbases,
        meta.get("hrefs"),
        parsed["documents"],
    )
    assert_catalog_coverage(root, uris, catalog, entry=entry, schemas=schemas)
    return {
        "root": root,
        "entry": entry,
        "linkbases": linkbases,
        "schemas": schemas,
        "catalog": catalog,
        "meta": meta,
        "parsed": parsed,
    }
