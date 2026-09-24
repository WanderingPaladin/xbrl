"""Pinned Arelle invocation against the candidate Taxonomy Package."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

NS_LOPT = "http://lattice.example/lopt/2025-01-31"
NS_EXT = "http://lattice.example/lopt/ext/2025-01-31"
SCHEMA_REF = "http://lattice.example/lopt/2025-01-31/lopt-2025.xsd"


def _parts(qn: str) -> tuple[str, str]:
    ns, local = qn[1:].split("}", 1)
    return ns, local


def write_instance_facts(path: Path, facts: list[dict]) -> None:
    """facts: {concept, segment, scenario}. Period types are verifier-owned."""
    prefixes: dict[str, str] = {NS_LOPT: "lopt", NS_EXT: "ext"}
    for fact in facts:
        prefixes.setdefault(_parts(fact["concept"])[0], f"ns{len(prefixes)}")
        for dim, mem in fact.get("segment", []) + fact.get("scenario", []):
            prefixes.setdefault(_parts(dim)[0], f"ns{len(prefixes)}")
            prefixes.setdefault(_parts(mem)[0], f"ns{len(prefixes)}")
    nsdecls = [
        'xmlns:xbrli="http://www.xbrl.org/2003/instance"',
        'xmlns:link="http://www.xbrl.org/2003/linkbase"',
        'xmlns:xlink="http://www.w3.org/1999/xlink"',
        'xmlns:xbrldi="http://xbrl.org/2006/xbrldi"',
    ]
    for ns, pfx in prefixes.items():
        nsdecls.append(f'xmlns:{pfx}="{escape(ns, {chr(34): "&quot;"})}"')

    def members_xml(pairs: list[tuple[str, str]]) -> str:
        bits = []
        for dim, mem in pairs:
            dns, dlocal = _parts(dim)
            mns, mlocal = _parts(mem)
            bits.append(
                f'      <xbrldi:explicitMember dimension="{prefixes[dns]}:{dlocal}">'
                f"{prefixes[mns]}:{mlocal}</xbrldi:explicitMember>\n"
            )
        return "".join(bits)

    bodies = []
    for i, fact in enumerate(facts, start=1):
        c_ns, c_local = _parts(fact["concept"])
        # Verifier owns expected period semantics; do not copy candidate periodType.
        if c_local == "NetAssets":
            period = "<xbrli:instant>2025-12-31</xbrli:instant>"
        else:
            period = "<xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate>"
        segment = fact.get("segment") or []
        scenario = fact.get("scenario") or []
        seg_xml = f"      <xbrli:segment>\n{members_xml(segment)}      </xbrli:segment>\n" if segment else ""
        scen_xml = f"    <xbrli:scenario>\n{members_xml(scenario)}    </xbrli:scenario>\n" if scenario else ""
        bodies.append(
            f"""  <xbrli:context id="c{i}">
    <xbrli:entity>
      <xbrli:identifier scheme="http://lattice.example/entity">LATTICE</xbrli:identifier>
{seg_xml}    </xbrli:entity>
    <xbrli:period>{period}</xbrli:period>
{scen_xml}  </xbrli:context>
  <{prefixes[c_ns]}:{c_local} contextRef="c{i}">1</{prefixes[c_ns]}:{c_local}>"""
        )
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl {' '.join(nsdecls)}>
  <link:schemaRef xlink:type="simple" xlink:href="{SCHEMA_REF}"/>
{chr(10).join(bodies)}
</xbrli:xbrl>
"""
    path.write_text(xml, encoding="utf-8")


def write_instance(
    path: Path,
    concept: str,
    segment: list[tuple[str, str]],
    scenario: list[tuple[str, str]],
) -> None:
    write_instance_facts(path, [{"concept": concept, "segment": segment, "scenario": scenario}])


def run_arelle(package_zip: Path, instance: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "arelle.CntlrCmdLine",
            "--internetConnectivity=offline",
            "--packages",
            str(package_zip),
            "--file",
            str(instance),
            "--validate",
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )


LOAD_FAILURE_MARKERS = (
    "[IOerror]",
    "[xbrl:schemaImportMissing]",
    "[tpe:invalidCatalogFile]",
    "tpe:invalidDirectoryStructure",
    "Could not load file from local filesystem",
    "no such option",
    "Usage: CntlrCmdLine",
    "Traceback (most recent call last)",
)


def arelle_output_ok(blob: str) -> None:
    """CLI/runtime/load failures must not be treated as dimensional results."""
    for marker in LOAD_FAILURE_MARKERS:
        if marker in blob:
            raise AssertionError(blob[-4000:])


def arelle_dimensional_hit(blob: str) -> bool:
    return "[xbrldt" in blob or "[xbrldte" in blob or "[xbrldie" in blob


def _message_code(line: str) -> str | None:
    bracket = line.find("[")
    if bracket < 0:
        return None
    end = line.find("]", bracket + 1)
    if end < 0:
        return None
    return line[bracket + 1 : end]


def arelle_validation_error(blob: str) -> bool:
    """True for dimensional or other instance/taxonomy validation errors.

    Informational ``[info]`` lines are not validation failures. An abstract
    concept used as a fact is ``[xmlSchema:abstractElement]``, which is not a
    dimensional code.
    """
    if arelle_dimensional_hit(blob):
        return True
    for line in blob.splitlines():
        code = _message_code(line)
        if not code or code == "info" or code.startswith("info."):
            continue
        if code.startswith("xmlSchema:") or code.startswith("xbrl.") or code.startswith("xbrl:"):
            return True
        if code in {"error", "Exception"}:
            return True
    return False


def arelle_validate_facts(package_zip: Path, facts: list[dict]) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as tmp:
        inst = Path(tmp) / "probe.xbrl"
        write_instance_facts(inst, facts)
        proc = run_arelle(package_zip, inst)
        blob = (proc.stdout or "") + "\n" + (proc.stderr or "")
        arelle_output_ok(blob)
        return proc.returncode, blob


def arelle_validate_context(
    package_zip: Path,
    concept: str,
    segment: list[tuple[str, str]],
    scenario: list[tuple[str, str]],
) -> tuple[int, str]:
    return arelle_validate_facts(
        package_zip,
        [{"concept": concept, "segment": segment, "scenario": scenario}],
    )
