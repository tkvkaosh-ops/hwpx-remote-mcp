"""Secure HWPX template inspection and style-preserving XML edits."""

from __future__ import annotations

import copy
import ipaddress
import logging
import os
import re
import socket
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from lxml import etree
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from hwpx_mcp.core.xml_parser import SecureXmlParser

logger = logging.getLogger("hwp-mcp-extended.hwpx_templates")

HWPX_MIME_TYPES = {
    "application/vnd.hancom.hwpx",
    "application/hwp+zip",
    "application/haansofthwpx",
    "application/zip",
    "application/octet-stream",
}
PLACEHOLDER_PATTERN = re.compile(
    r"\{\{\s*([^{}]+?)\s*\}\}|\$\{\s*([^{}]+?)\s*\}|<<\s*([^<>]+?)\s*>>"
)


class OpenAIFile(BaseModel):
    """Portable file input compatible with ChatGPT ``openai/fileParams``."""

    model_config = ConfigDict(extra="forbid")

    download_url: AnyHttpUrl
    file_id: str = Field(min_length=1, max_length=512)
    mime_type: str | None = None
    file_name: str | None = None


def _local_name(element: etree._Element) -> str:
    return etree.QName(element.tag).localname if isinstance(element.tag, str) else ""


def _attribute_case_insensitive(element: etree._Element, *names: str) -> str:
    wanted = {name.lower() for name in names}
    for key, value in element.attrib.items():
        if etree.QName(key).localname.lower() in wanted:
            return value
    return ""


def _allow_private_downloads() -> bool:
    return os.getenv("ALLOW_PRIVATE_DOWNLOADS", "false").lower() == "true"


def _validate_remote_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("template download_url must be an HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("template download_url must not contain credentials")
    if parsed.scheme != "https" and not _allow_private_downloads():
        raise ValueError("template download_url must use HTTPS")

    if _allow_private_downloads():
        return

    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443)
        }
    except socket.gaierror as exc:
        raise ValueError("template download host could not be resolved") from exc

    for address in addresses:
        ip = ipaddress.ip_address(address)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError("template download_url resolves to a non-public address")


def _download_template(file_info: OpenAIFile, destination: Path) -> dict[str, Any]:
    file_name = file_info.file_name or "template.hwpx"
    if not file_name.lower().endswith(".hwpx"):
        raise ValueError("only .hwpx template files are supported")
    if file_info.mime_type:
        declared_mime = file_info.mime_type.split(";", 1)[0].strip().lower()
        if declared_mime not in HWPX_MIME_TYPES:
            raise ValueError(f"unsupported template MIME type: {declared_mime}")

    max_bytes = int(os.getenv("HWPX_MAX_UPLOAD_BYTES", "20971520"))
    current_url = str(file_info.download_url)
    redirects = 0
    headers = {"User-Agent": "hwpx-remote-mcp/1.0"}

    with httpx.Client(timeout=30.0, follow_redirects=False, headers=headers) as client:
        while True:
            _validate_remote_url(current_url)
            with client.stream("GET", current_url) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location or redirects >= 3:
                        raise ValueError("template download redirect limit exceeded")
                    current_url = urljoin(current_url, location)
                    redirects += 1
                    continue
                response.raise_for_status()

                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > max_bytes:
                    raise ValueError("template file exceeds upload size limit")

                response_mime = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if response_mime and response_mime not in HWPX_MIME_TYPES:
                    raise ValueError(
                        f"downloaded template has unsupported MIME type: {response_mime}"
                    )

                downloaded = 0
                with destination.open("wb") as output:
                    for chunk in response.iter_bytes():
                        downloaded += len(chunk)
                        if downloaded > max_bytes:
                            raise ValueError("template file exceeds upload size limit")
                        output.write(chunk)
                break

    from hwpx_mcp.tools.remote_documents import validate_hwpx_archive

    validation = validate_hwpx_archive(destination)
    if not validation.get("valid"):
        raise ValueError(
            "uploaded template is not a valid HWPX: "
            + "; ".join(validation.get("errors", []))
        )
    return validation


def _section_names(archive: zipfile.ZipFile) -> list[str]:
    return sorted(
        name
        for name in archive.namelist()
        if re.fullmatch(r"Contents/section\d+\.xml", name)
    )


def _text_nodes(element: etree._Element) -> list[etree._Element]:
    return [node for node in element.iter() if _local_name(node) == "t"]


def _element_text(element: etree._Element) -> str:
    return "".join(node.text or "" for node in _text_nodes(element)).strip()


def inspect_hwpx_file(file_path: Path) -> dict[str, Any]:
    fields: list[dict[str, Any]] = []
    placeholders: list[str] = []
    paragraphs: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    labels: list[str] = []

    with zipfile.ZipFile(file_path, "r") as archive:
        section_names = _section_names(archive)
        for section_index, section_name in enumerate(section_names):
            root = SecureXmlParser.parse_string(archive.read(section_name))
            paragraph_index = 0
            table_index = 0

            for element in root.iter():
                name = _local_name(element)
                if name == "fieldBegin":
                    field_name = _attribute_case_insensitive(
                        element, "name", "fieldName", "id"
                    )
                    if field_name:
                        fields.append(
                            {
                                "name": field_name,
                                "section": section_index,
                            }
                        )
                elif name == "p":
                    text = _element_text(element)
                    if text:
                        for match in PLACEHOLDER_PATTERN.finditer(text):
                            placeholder = next(
                                group for group in match.groups() if group is not None
                            ).strip()
                            if placeholder and placeholder not in placeholders:
                                placeholders.append(placeholder)
                        if len(paragraphs) < 300:
                            paragraphs.append(
                                {
                                    "section": section_index,
                                    "index": paragraph_index,
                                    "text": text[:1000],
                                    "paragraph_style_id": element.get("styleIDRef"),
                                    "paragraph_property_id": element.get("paraPrIDRef"),
                                }
                            )
                        if text.endswith((":", "：")) and len(text) <= 80:
                            labels.append(text.rstrip(":：").strip())
                    paragraph_index += 1
                elif name == "tbl":
                    rows: list[list[str]] = []
                    for row in [node for node in element.iter() if _local_name(node) == "tr"]:
                        cells = [
                            _element_text(node)
                            for node in row
                            if _local_name(node) == "tc"
                        ]
                        if cells:
                            rows.append(cells)
                            if cells[0] and len(cells[0]) <= 80:
                                labels.append(cells[0])
                    if len(tables) < 100:
                        tables.append(
                            {
                                "section": section_index,
                                "index": table_index,
                                "row_count": len(rows),
                                "column_count": max(
                                    (len(row) for row in rows), default=0
                                ),
                                "cells": rows[:50],
                            }
                        )
                    table_index += 1

        members = archive.namelist()
        image_parts = [
            name
            for name in members
            if name.startswith(("BinData/", "Contents/media/"))
        ]
        header_footer_parts = [
            name
            for name in members
            if any(token in name.lower() for token in ("header", "footer", "masterpage"))
        ]
        page_number_elements = 0
        for section_name in section_names:
            root = SecureXmlParser.parse_string(archive.read(section_name))
            page_number_elements += sum(
                1
                for element in root.iter()
                if _local_name(element) in {"pageNum", "autoNum"}
                and (
                    not _attribute_case_insensitive(element, "numType")
                    or _attribute_case_insensitive(element, "numType").upper() == "PAGE"
                )
            )

    unique_fields = []
    seen_fields: set[str] = set()
    for field in fields:
        if field["name"] not in seen_fields:
            unique_fields.append(field)
            seen_fields.add(field["name"])

    unique_labels = list(dict.fromkeys(label for label in labels if label))[:200]
    return {
        "fields": unique_fields,
        "placeholders": placeholders,
        "paragraphs": paragraphs,
        "tables": tables,
        "section_count": len(section_names),
        "page_count": None,
        "page_count_note": "HWPX does not store a reliable rendered page count without a layout engine.",
        "editable_areas": {
            "field_names": [field["name"] for field in unique_fields],
            "placeholders": placeholders,
            "table_count": len(tables),
        },
        "detected_labels": unique_labels,
        "preserved_assets": {
            "image_count": len(image_parts),
            "header_footer_master_parts": len(header_footer_parts),
            "page_number_elements": page_number_elements,
        },
    }


def inspect_remote_hwpx_template(template_file: OpenAIFile) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="hwpx-inspect-") as workspace:
        template_path = Path(workspace) / "template.hwpx"
        validation = _download_template(template_file, template_path)
        inspection = inspect_hwpx_file(template_path)
        return {
            "success": True,
            "source_file_id": template_file.file_id,
            "source_filename": template_file.file_name or "template.hwpx",
            "validation": validation,
            **inspection,
        }


def _set_container_text(container: etree._Element, value: str) -> None:
    nodes = _text_nodes(container)
    if nodes:
        nodes[0].text = value
        for node in nodes[1:]:
            node.text = ""
        return

    paragraph = next(
        (node for node in container.iter() if _local_name(node) == "p"), None
    )
    if paragraph is None:
        return
    namespace = etree.QName(paragraph.tag).namespace
    run = etree.SubElement(paragraph, f"{{{namespace}}}run")
    text = etree.SubElement(run, f"{{{namespace}}}t")
    text.text = value


def _replace_field_values(
    root: etree._Element, field_values: dict[str, str]
) -> list[str]:
    applied: list[str] = []
    active_name: str | None = None
    active_value = ""
    wrote_value = False
    active_begin: etree._Element | None = None

    for element in root.iter():
        name = _local_name(element)
        if name == "fieldBegin":
            field_name = _attribute_case_insensitive(
                element, "name", "fieldName", "id"
            )
            if field_name in field_values:
                active_name = field_name
                active_value = str(field_values[field_name])
                wrote_value = False
                active_begin = element
        elif name == "t" and active_name is not None:
            element.text = active_value if not wrote_value else ""
            wrote_value = True
        elif name == "fieldEnd" and active_name is not None:
            if not wrote_value and active_begin is not None:
                parent = active_begin.getparent()
                if parent is not None and _local_name(parent) == "run":
                    namespace = etree.QName(parent.tag).namespace
                    text = etree.Element(f"{{{namespace}}}t")
                    text.text = active_value
                    parent.insert(parent.index(active_begin) + 1, text)
            applied.append(active_name)
            active_name = None
            active_begin = None
    return applied


def _replace_text(root: etree._Element, replacements: dict[str, str]) -> list[str]:
    applied: list[str] = []
    for paragraph in [element for element in root.iter() if _local_name(element) == "p"]:
        nodes = _text_nodes(paragraph)
        if not nodes:
            continue

        for source, target in replacements.items():
            replaced_directly = False
            for node in nodes:
                current = node.text or ""
                if source in current:
                    node.text = current.replace(source, target)
                    replaced_directly = True
            if replaced_directly:
                applied.append(source)
                continue

            combined = "".join(node.text or "" for node in nodes)
            if source in combined:
                nodes[0].text = combined.replace(source, target)
                for node in nodes[1:]:
                    node.text = ""
                applied.append(source)
    return applied


def _tables(root: etree._Element) -> list[etree._Element]:
    return [element for element in root.iter() if _local_name(element) == "tbl"]


def _table_rows(table: etree._Element) -> list[list[etree._Element]]:
    rows: list[list[etree._Element]] = []
    for row in [element for element in table.iter() if _local_name(element) == "tr"]:
        cells = [element for element in row if _local_name(element) == "tc"]
        if cells:
            rows.append(cells)
    return rows


def _apply_table_values(
    roots: list[etree._Element], values: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    all_tables = [table for root in roots for table in _tables(root)]
    applied: list[dict[str, Any]] = []
    for spec in values:
        value = str(spec.get("value", ""))
        if "label" in spec:
            label = str(spec["label"])
            matched = False
            for table_index, table in enumerate(all_tables):
                for row_index, row in enumerate(_table_rows(table)):
                    for column_index, cell in enumerate(row):
                        if _element_text(cell) == label and column_index + 1 < len(row):
                            _set_container_text(row[column_index + 1], value)
                            applied.append(
                                {
                                    "label": label,
                                    "table_index": table_index,
                                    "row": row_index,
                                    "column": column_index + 1,
                                }
                            )
                            matched = True
                            break
                    if matched:
                        break
                if matched:
                    break
            continue

        table_index = int(spec.get("table_index", 0))
        row_index = int(spec.get("row", 0))
        column_index = int(spec.get("column", 0))
        if table_index < 0 or table_index >= len(all_tables):
            raise ValueError(f"table_index out of range: {table_index}")
        rows = _table_rows(all_tables[table_index])
        if row_index < 0 or row_index >= len(rows):
            raise ValueError(f"table row out of range: {row_index}")
        if column_index < 0 or column_index >= len(rows[row_index]):
            raise ValueError(f"table column out of range: {column_index}")
        _set_container_text(rows[row_index][column_index], value)
        applied.append(
            {
                "table_index": table_index,
                "row": row_index,
                "column": column_index,
            }
        )
    return applied


def _new_paragraph_like(source: etree._Element, content: str) -> etree._Element:
    paragraph = etree.Element(source.tag, attrib=dict(source.attrib), nsmap=source.nsmap)
    source_run = next(
        (child for child in source if _local_name(child) == "run"), None
    )
    namespace = etree.QName(source.tag).namespace
    run = etree.SubElement(
        paragraph,
        source_run.tag if source_run is not None else f"{{{namespace}}}run",
        attrib=dict(source_run.attrib) if source_run is not None else {},
    )
    text = etree.SubElement(run, f"{{{namespace}}}t")
    text.text = content
    return paragraph


def _insert_paragraphs(
    roots: list[etree._Element], paragraphs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    applied: list[dict[str, Any]] = []
    if not roots:
        return applied

    for spec in paragraphs:
        content = str(spec.get("content", "")).strip()
        if not content:
            continue
        after_text = str(spec.get("after_text", "")).strip()
        target: etree._Element | None = None
        target_root = roots[-1]
        for root in roots:
            candidates = [element for element in root.iter() if _local_name(element) == "p"]
            if after_text:
                match = next(
                    (element for element in candidates if after_text in _element_text(element)),
                    None,
                )
                if match is not None:
                    target = match
                    target_root = root
                    break
            elif candidates:
                target = candidates[-1]
                target_root = root

        if target is None:
            raise ValueError(f"paragraph insertion anchor not found: {after_text}")
        parent = target.getparent()
        if parent is None:
            parent = target_root
        new_paragraph = _new_paragraph_like(target, content)
        parent.insert(parent.index(target) + 1, new_paragraph)
        applied.append({"after_text": after_text, "content": content})
    return applied


def _serialize_xml(root: etree._Element) -> bytes:
    return etree.tostring(
        root,
        encoding="UTF-8",
        xml_declaration=True,
        standalone=True,
    )


def fill_hwpx_file(
    template_path: Path,
    output_path: Path,
    field_values: dict[str, str],
    replacements: dict[str, str],
    table_cell_values: list[dict[str, Any]],
    paragraphs: list[dict[str, Any]],
) -> dict[str, Any]:
    modified_parts: dict[str, bytes] = {}
    applied_fields: list[str] = []
    applied_replacements: list[str] = []

    with zipfile.ZipFile(template_path, "r") as source:
        section_names = _section_names(source)
        roots: list[etree._Element] = []
        for section_name in section_names:
            root = SecureXmlParser.parse_string(source.read(section_name))
            roots.append(root)
            applied_fields.extend(_replace_field_values(root, field_values))

            combined_replacements = dict(replacements)
            for key, value in field_values.items():
                combined_replacements.setdefault(f"{{{{{key}}}}}", str(value))
                combined_replacements.setdefault(f"${{{key}}}", str(value))
                combined_replacements.setdefault(f"<<{key}>>", str(value))
            applied_replacements.extend(
                _replace_text(root, combined_replacements)
            )

        applied_table_cells = _apply_table_values(roots, table_cell_values)
        applied_paragraphs = _insert_paragraphs(roots, paragraphs)

        for section_name, root in zip(section_names, roots):
            modified_parts[section_name] = _serialize_xml(root)

        preview_lines = [
            _element_text(paragraph)
            for root in roots
            for paragraph in root.iter()
            if _local_name(paragraph) == "p" and _element_text(paragraph)
        ]
        if "Preview/PrvText.txt" in source.namelist():
            modified_parts["Preview/PrvText.txt"] = (
                "\r\n".join(preview_lines) + "\r\n"
            ).encode("utf-8")

        with zipfile.ZipFile(output_path, "w") as destination:
            for info in source.infolist():
                data = modified_parts.get(info.filename, source.read(info.filename))
                destination.writestr(copy.copy(info), data)

    return {
        "applied_fields": sorted(set(applied_fields)),
        "applied_replacements": sorted(set(applied_replacements)),
        "applied_table_cells": applied_table_cells,
        "inserted_paragraphs": applied_paragraphs,
        "modified_package_parts": sorted(modified_parts),
    }


def fill_remote_hwpx_template(
    template_file: OpenAIFile,
    filename: str,
    field_values: dict[str, str] | None = None,
    replacements: dict[str, str] | None = None,
    table_cell_values: list[dict[str, Any]] | None = None,
    paragraphs: list[dict[str, Any]] | None = None,
    instructions: str = "",
) -> dict[str, Any]:
    from hwpx_mcp.tools.remote_documents import publish_hwpx_file

    with tempfile.TemporaryDirectory(prefix="hwpx-fill-") as workspace:
        workspace_path = Path(workspace)
        template_path = workspace_path / "template.hwpx"
        output_path = workspace_path / "completed.hwpx"
        source_validation = _download_template(template_file, template_path)

        operation = fill_hwpx_file(
            template_path=template_path,
            output_path=output_path,
            field_values={str(key): str(value) for key, value in (field_values or {}).items()},
            replacements={str(key): str(value) for key, value in (replacements or {}).items()},
            table_cell_values=table_cell_values or [],
            paragraphs=paragraphs or [],
        )
        result = publish_hwpx_file(output_path, filename)
        return {
            **result,
            "source_file_id": template_file.file_id,
            "source_filename": template_file.file_name or "template.hwpx",
            "source_validation": source_validation,
            "original_modified": False,
            "instructions_received": bool(instructions.strip()),
            **operation,
        }
