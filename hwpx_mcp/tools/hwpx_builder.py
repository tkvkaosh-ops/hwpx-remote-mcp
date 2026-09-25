"""
HWPX Builder - Cross-platform HWPX document creation using python-hwpx library.
Uses the official HwpxDocument API with post-processing for proper text visibility.
Supports Table Formulas, Charts (as images), Custom Table Borders, and Text Styles.
"""

import logging
import zipfile
import tempfile
import shutil
import os
import re
import base64
import uuid
from io import BytesIO
from typing import List, Optional, Dict, Tuple
from lxml import etree as lxml_etree
import xml.etree.ElementTree as ET  # Use standard ET for library compatibility

logger = logging.getLogger("hwp-mcp-extended.hwpx_builder")

# Mandatory import
from hwpx.document import HwpxDocument

HAS_MATPLOTLIB = False
try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    HAS_MATPLOTLIB = True
except ImportError:
    logger.warning("matplotlib not installed. Charts will not be generated.")

HP_NS = "http://www.hancom.co.kr/hwpml/2011/paragraph"
HS_NS = "http://www.hancom.co.kr/hwpml/2011/section"
HC_NS = "http://www.hancom.co.kr/hwpml/2011/core"
HH_NS = "http://www.hancom.co.kr/hwpml/2011/head"

# Register namespaces for ET
ET.register_namespace("hp", HP_NS)
ET.register_namespace("hs", HS_NS)
ET.register_namespace("hc", HC_NS)
ET.register_namespace("hh", HH_NS)

NSMAP = {
    "hp": HP_NS,
    "hs": HS_NS,
    "ha": "http://www.hancom.co.kr/hwpml/2011/app",
    "hc": HC_NS,
    "hh": HH_NS,
    "hp10": "http://www.hancom.co.kr/hwpml/2016/paragraph",
    "hhs": "http://www.hancom.co.kr/hwpml/2011/history",
    "hm": "http://www.hancom.co.kr/hwpml/2011/master-page",
    "hpf": "http://www.hancom.co.kr/schema/2011/hpf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "opf": "http://www.idpf.org/2007/opf/",
    "ooxmlchart": "http://www.hancom.co.kr/hwpml/2016/ooxmlchart",
    "hwpunitchar": "http://www.hancom.co.kr/hwpml/2016/HwpUnitChar",
    "epub": "http://www.idpf.org/2007/ops",
    "config": "urn:oasis:names:tc:opendocument:xmlns:config:1.0",
}

# --- Border Styles ---
BORDER_STYLES = {
    "default": {"id": "101", "width": "0.5 mm", "type": "SOLID", "color": "#000000"},
    "thin": {"id": "102", "width": "0.12 mm", "type": "SOLID", "color": "#000000"},
    "bold": {"id": "103", "width": "0.4 mm", "type": "SOLID", "color": "#000000"},
    "double": {
        "id": "104",
        "width": "0.5 mm",
        "type": "DOUBLETHIN",
        "color": "#000000",
    },
    "none": {"id": "105", "width": "0 mm", "type": "NONE", "color": "#000000"},
}

# --- Char Styles (Text Styles) ---
CHAR_STYLES = {
    "default": {"id": "200", "height": "1000", "color": "#000000", "bold": False},
    "title": {"id": "201", "height": "2000", "color": "#000000", "bold": True},
    "subtitle": {"id": "202", "height": "1500", "color": "#000000", "bold": True},
    "bold": {"id": "203", "height": "1000", "color": "#000000", "bold": True},
    "red": {"id": "204", "height": "1000", "color": "#FF0000", "bold": False},
    "blue": {"id": "205", "height": "1000", "color": "#0000FF", "bold": False},
    "large": {"id": "206", "height": "1300", "color": "#000000", "bold": False},
}


def _fix_hwpx_for_viewer(
    hwpx_path: str,
    text_lines: List[str],
    table_styles: List[str],
    text_styles_map: List[str],
) -> bool:
    try:
        temp_dir = tempfile.mkdtemp()
        extract_dir = os.path.join(temp_dir, "extracted")
        os.makedirs(extract_dir)

        with zipfile.ZipFile(hwpx_path, "r") as zf:
            zf.extractall(extract_dir)

        header_path = os.path.join(extract_dir, "Contents", "header.xml")
        if os.path.exists(header_path):
            _update_header_xml(header_path)

        section_path = os.path.join(extract_dir, "Contents", "section0.xml")
        if os.path.exists(section_path):
            _fix_section_xml(section_path, table_styles, text_styles_map)

        preview_path = os.path.join(extract_dir, "Preview", "PrvText.txt")
        if os.path.exists(preview_path):
            preview_text = "\r\n".join(text_lines) + "\r\n"
            with open(preview_path, "w", encoding="utf-8") as f:
                f.write(preview_text)

        temp_hwpx = os.path.join(temp_dir, "output.hwpx")
        with zipfile.ZipFile(temp_hwpx, "w", zipfile.ZIP_DEFLATED) as zf:
            mimetype_path = os.path.join(extract_dir, "mimetype")
            if os.path.exists(mimetype_path):
                zf.write(mimetype_path, "mimetype", compress_type=zipfile.ZIP_STORED)
            for root, dirs, files in os.walk(extract_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, extract_dir)
                    if arcname == "mimetype":
                        continue
                    zf.write(file_path, arcname)

        shutil.move(temp_hwpx, hwpx_path)
        shutil.rmtree(temp_dir, ignore_errors=True)
        return True
    except Exception as e:
        logger.error(f"Failed to fix HWPX: {e}")
        import traceback

        traceback.print_exc()
        return False


def _update_header_xml(header_path: str):
    # Use lxml here as we are parsing file from disk
    parser = lxml_etree.XMLParser(remove_blank_text=True)
    tree = lxml_etree.parse(header_path, parser)
    root = tree.getroot()

    ref_list = root.find(f"{{{HH_NS}}}refList")
    if ref_list is None:
        ref_list = lxml_etree.SubElement(root, f"{{{HH_NS}}}refList")

    border_fills = ref_list.find(f"{{{HH_NS}}}borderFills")
    if border_fills is None:
        border_fills = lxml_etree.SubElement(ref_list, f"{{{HH_NS}}}borderFills")

    for name, style in BORDER_STYLES.items():
        bid = style["id"]
        if border_fills.xpath(f"./hh:borderFill[@id='{bid}']", namespaces=NSMAP):
            continue

        bf = lxml_etree.SubElement(border_fills, f"{{{HH_NS}}}borderFill")
        bf.set("id", bid)
        bf.set("threeD", "0")
        bf.set("shadow", "0")
        bf.set("centerLine", "NONE")
        bf.set("breakCellSeparateLine", "0")

        for side in ["slash", "backSlash"]:
            s = lxml_etree.SubElement(bf, f"{{{HH_NS}}}{side}")
            s.set("type", "NONE")
            s.set("Crooked", "0")
            s.set("isCounter", "0")

        for side in ["leftBorder", "rightBorder", "topBorder", "bottomBorder"]:
            b = lxml_etree.SubElement(bf, f"{{{HH_NS}}}{side}")
            b.set("type", style["type"])
            b.set("width", style["width"])
            b.set("color", style["color"])

        diag = lxml_etree.SubElement(bf, f"{{{HH_NS}}}diagonal")
        diag.set("type", "NONE")
        diag.set("width", "0.1 mm")
        diag.set("color", "#000000")

    border_fills.set("itemCnt", str(len(border_fills)))

    char_properties = ref_list.find(f"{{{HH_NS}}}charProperties")
    if char_properties is None:
        char_properties = lxml_etree.SubElement(ref_list, f"{{{HH_NS}}}charProperties")

    for name, style in CHAR_STYLES.items():
        cid = style["id"]
        if char_properties.xpath(f"./hh:charPr[@id='{cid}']", namespaces=NSMAP):
            continue

        cp = lxml_etree.SubElement(char_properties, f"{{{HH_NS}}}charPr")
        cp.set("id", cid)
        cp.set("height", style["height"])
        cp.set("textColor", style["color"])
        cp.set("shadeColor", "none")
        cp.set("useFontSpace", "0")
        cp.set("useKerning", "0")
        cp.set("symMark", "NONE")
        cp.set("borderFillIDRef", "1")

        fr = lxml_etree.SubElement(cp, f"{{{HH_NS}}}fontRef")
        fr.set("hangul", "1")
        fr.set("latin", "1")
        fr.set("hanja", "1")
        fr.set("japanese", "1")
        fr.set("other", "1")
        fr.set("symbol", "1")
        fr.set("user", "1")

        ra = lxml_etree.SubElement(cp, f"{{{HH_NS}}}ratio")
        ra.set("hangul", "100")
        ra.set("latin", "100")
        ra.set("hanja", "100")
        ra.set("japanese", "100")
        ra.set("other", "100")
        ra.set("symbol", "100")
        ra.set("user", "100")

        sp = lxml_etree.SubElement(cp, f"{{{HH_NS}}}spacing")
        sp.set("hangul", "0")
        sp.set("latin", "0")
        sp.set("hanja", "0")
        sp.set("japanese", "0")
        sp.set("other", "0")
        sp.set("symbol", "0")
        sp.set("user", "0")

        rs = lxml_etree.SubElement(cp, f"{{{HH_NS}}}relSz")
        rs.set("hangul", "100")
        rs.set("latin", "100")
        rs.set("hanja", "100")
        rs.set("japanese", "100")
        rs.set("other", "100")
        rs.set("symbol", "100")
        rs.set("user", "100")

        of = lxml_etree.SubElement(cp, f"{{{HH_NS}}}offset")
        of.set("hangul", "0")
        of.set("latin", "0")
        of.set("hanja", "0")
        of.set("japanese", "0")
        of.set("other", "0")
        of.set("symbol", "0")
        of.set("user", "0")

        if style["bold"]:
            lxml_etree.SubElement(cp, f"{{{HH_NS}}}bold")

    char_properties.set("itemCnt", str(len(char_properties)))

    xml_bytes = lxml_etree.tostring(root, encoding="UTF-8", xml_declaration=True)
    with open(header_path, "wb") as f:
        f.write(xml_bytes)


def _fix_section_xml(
    section_path: str, table_styles: List[str], text_styles_map: List[str]
):
    parser = lxml_etree.XMLParser(remove_blank_text=True)
    tree = lxml_etree.parse(section_path, parser)
    root = tree.getroot()

    vertpos = 0
    field_id_counter = 1000
    table_index = 0
    para_index = 0

    paragraphs = list(root.iter(f"{{{HP_NS}}}p"))

    for p in paragraphs:
        if para_index < len(text_styles_map):
            style_name = text_styles_map[para_index]
            if style_name and style_name in CHAR_STYLES:
                current_style_id = CHAR_STYLES[style_name]["id"]

        font_height = 1000
        if current_style_id:
            for style in CHAR_STYLES.values():
                if style["id"] == current_style_id:
                    font_height = int(style["height"])
                    break

        baseline = int(font_height * 0.85)
        spacing = int(font_height * 0.6)

        has_lineseg = any(child.tag.endswith("}linesegarray") for child in p)
        if not has_lineseg:
            lsa = lxml_etree.SubElement(p, f"{{{HP_NS}}}linesegarray")
            ls = lxml_etree.SubElement(lsa, f"{{{HP_NS}}}lineseg")
            ls.set("textpos", "0")
            ls.set("vertpos", str(vertpos))
            ls.set("vertsize", str(font_height))
            ls.set("textheight", str(font_height))
            ls.set("baseline", str(baseline))
            ls.set("spacing", str(spacing))
            ls.set("horzpos", "0")
            ls.set("horzsize", "42520")
            ls.set("flags", "393216")
        vertpos += int(font_height * 1.6)

        para_index += 1

        for child in list(p):
            if child.tag == f"{{{HP_NS}}}run":
                if current_style_id:
                    child.set("charPrIDRef", current_style_id)

                tables = child.findall(f"{{{HP_NS}}}tbl")
                for ctrl in child.findall(f"{{{HP_NS}}}ctrl"):
                    nested_table = ctrl.find(f"{{{HP_NS}}}tbl")
                    if nested_table is not None:
                        tables.append(nested_table)
                for tbl in tables:
                    if table_index < len(table_styles):
                        style_name = table_styles[table_index]
                        style_def = BORDER_STYLES.get(
                            style_name, BORDER_STYLES["default"]
                        )
                        style_id = style_def["id"]
                        tbl.set("borderFillIDRef", style_id)
                        for tc in tbl.xpath(".//hp:tc", namespaces=NSMAP):
                            tc.set("borderFillIDRef", style_id)
                        table_index += 1

                t_node = child.find(f"{{{HP_NS}}}t")
                if t_node is not None and t_node.text:
                    text = t_node.text
                    if text.startswith("[계산식:") and text.endswith("]"):
                        command = text[5:-1]
                        run1 = lxml_etree.Element(f"{{{HP_NS}}}run")
                        if "charPrIDRef" in child.attrib:
                            run1.set("charPrIDRef", child.attrib["charPrIDRef"])
                        fb = lxml_etree.SubElement(run1, f"{{{HP_NS}}}fieldBegin")
                        fb.set("Type", "Formula")
                        fb.set("Name", f"calc{field_id_counter}")
                        fb.set("Editable", "0")
                        fb.set("Command", command)

                        run2 = lxml_etree.Element(f"{{{HP_NS}}}run")
                        if "charPrIDRef" in child.attrib:
                            run2.set("charPrIDRef", child.attrib["charPrIDRef"])
                        t2 = lxml_etree.SubElement(run2, f"{{{HP_NS}}}t")
                        t2.text = "0.00"

                        run3 = lxml_etree.Element(f"{{{HP_NS}}}run")
                        if "charPrIDRef" in child.attrib:
                            run3.set("charPrIDRef", child.attrib["charPrIDRef"])
                        fe = lxml_etree.SubElement(run3, f"{{{HP_NS}}}fieldEnd")
                        fe.set("Type", "Formula")
                        fe.set("BeginIDRef", f"calc{field_id_counter}")

                        field_id_counter += 1
                        idx = p.index(child)
                        p.insert(idx, run1)
                        p.insert(idx + 1, run2)
                        p.insert(idx + 2, run3)
                        p.remove(child)
                        break

    xml_bytes = lxml_etree.tostring(root, encoding="UTF-8", xml_declaration=True)
    xml_str = xml_bytes.decode("utf-8")

    xml_str = xml_str.replace(
        "<?xml version='1.0' encoding='UTF-8'?>",
        '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>',
    )
    for old_ns, new_prefix in [
        (f'xmlns:ns0="{HS_NS}"', f'xmlns:hs="{HS_NS}"'),
        (f'xmlns:ns1="{HP_NS}"', f'xmlns:hp="{HP_NS}"'),
        ("<ns0:", "<hs:"),
        ("</ns0:", "</hs:"),
        ("<ns1:", "<hp:"),
        ("</ns1:", "</hp:"),
    ]:
        xml_str = xml_str.replace(old_ns, new_prefix)

    extra_namespaces = {
        "ha": "http://www.hancom.co.kr/hwpml/2011/app",
        "hp10": "http://www.hancom.co.kr/hwpml/2016/paragraph",
        "hc": "http://www.hancom.co.kr/hwpml/2011/core",
        "hh": "http://www.hancom.co.kr/hwpml/2011/head",
        "hhs": "http://www.hancom.co.kr/hwpml/2011/history",
        "hm": "http://www.hancom.co.kr/hwpml/2011/master-page",
        "hpf": "http://www.hancom.co.kr/schema/2011/hpf",
        "dc": "http://purl.org/dc/elements/1.1/",
        "opf": "http://www.idpf.org/2007/opf/",
        "ooxmlchart": "http://www.hancom.co.kr/hwpml/2016/ooxmlchart",
        "hwpunitchar": "http://www.hancom.co.kr/hwpml/2016/HwpUnitChar",
        "epub": "http://www.idpf.org/2007/ops",
        "config": "urn:oasis:names:tc:opendocument:xmlns:config:1.0",
    }
    ns_declarations = "".join(
        f' xmlns:{prefix}="{uri}"'
        for prefix, uri in extra_namespaces.items()
        if f"xmlns:{prefix}=" not in xml_str
    )
    if ns_declarations:
        xml_str = xml_str.replace("<hs:sec", f"<hs:sec{ns_declarations}", 1)

    with open(section_path, "w", encoding="utf-8") as f:
        f.write(xml_str)


def _latex_to_hwp_script(latex: str) -> str:
    script = latex
    replacements = {
        r"\\frac\{([^{}]+)\}\{([^{}]+)\}": r"{\1} over {\2}",
        r"\\sqrt\{([^{}]+)\}": r"sqrt{\1}",
        r"\\sum": "sum",
        r"\\int": "int",
        r"\\lim": "lim",
        r"\\to": "->",
        r"\\infty": "inf",
    }
    for pat, rep in replacements.items():
        script = re.sub(pat, rep, script)
    script = script.replace("\\", "")
    return script.strip()


class HwpxBuilder:
    def __init__(self):
        self._document = HwpxDocument.new()
        self._section = self._document.sections[0]
        self._text_content: List[str] = []
        self._table_styles: List[str] = []
        self._text_styles_map: List[str] = ["default"]

    def add_text(self, text: str, style: str = "default"):
        for line in text.split("\n"):
            if line.strip():
                self._document.add_paragraph(line, section=self._section)
                self._text_content.append(line)
                self._text_styles_map.append(style)

    def add_heading(self, text: str, level: int = 1):
        style = "title" if level == 1 else "subtitle"
        self._document.add_paragraph(text, section=self._section)
        self._text_content.append(text)
        self._text_styles_map.append(style)

    def add_paragraph(self, text: str, style: str = "default"):
        self._document.add_paragraph(text, section=self._section)
        self._text_content.append(text)
        self._text_styles_map.append(style)

    def add_equation(self, latex: str):
        hwp_script = _latex_to_hwp_script(latex)
        eq_text = f"[수식] {hwp_script}"
        self._document.add_paragraph(eq_text, section=self._section)
        self._text_content.append(eq_text)
        self._text_styles_map.append("default")

    def add_formula(self, command: str):
        text = f"[계산식:{command}]"
        self._document.add_paragraph(text, section=self._section)
        self._text_content.append(text)
        self._text_styles_map.append("default")

    def add_table(
        self,
        rows: int,
        cols: int,
        data: Optional[List[List[str]]] = None,
        style: str = "default",
    ):
        table = self._document.add_table(rows=rows, cols=cols, section=self._section)
        if style not in BORDER_STYLES:
            logger.warning(f"Unknown table style '{style}', using default")
            style = "default"
        self._table_styles.append(style)
        self._text_styles_map.append("default")

        if data and table:
            for row_idx, row_data in enumerate(data):
                row_texts = []
                for col_idx, cell_value in enumerate(row_data):
                    if row_idx < rows and col_idx < cols:
                        val_str = str(cell_value)
                        if val_str.startswith("="):
                            formula_cmd = val_str[1:]
                            val_str = f"[계산식:{formula_cmd}]"

                        table.set_cell_text(row_idx, col_idx, val_str)
                        row_texts.append(val_str)
                self._text_content.append("\t".join(row_texts))
        return table

    def insert_image(
        self, image_data: bytes, filename: str, width_mm: int = 100, height_mm: int = 60
    ):
        """
        Insert an image into the document.
        Since python-hwpx 1.9 does not yet have a high-level `add_image` helper,
        we implement it by directly manipulating the package and XML using xml.etree.ElementTree.
        """
        try:
            # 1. Add image to package (BinData)
            bin_id = f"bin{uuid.uuid4().hex[:8]}"
            image_path = f"BinData/{bin_id}.png"
            self._document.package.set_part(image_path, image_data)

            # 2. Add BinData to Header (DocInfo)
            header_xml = self._document.headers[0].element  # This is an ET.Element
            doc_info = header_xml

            # Find or create bindatas
            # Note: ET uses standard namespaces, so we need to be careful with finding
            bindatas = doc_info.find(f"{{{HH_NS}}}bindatas")
            if bindatas is None:
                bindatas = ET.SubElement(doc_info, f"{{{HH_NS}}}bindatas")

            bin_numeric_id = len(bindatas) + 1
            bd = ET.SubElement(bindatas, f"{{{HH_NS}}}bindata")
            bd.set("id", str(bin_numeric_id))
            bd.set("link", image_path)
            bd.set("type", "EMBEDDING")
            bd.set("compress", "BIN")

            bindatas.set("itemCnt", str(len(bindatas)))
            self._document.headers[0].mark_dirty()

            # 3. Add Picture Shape to Body
            width_hwp = int(width_mm * 283.465)
            height_hwp = int(height_mm * 283.465)

            p = self._document.add_paragraph("", section=self._section)
            run = p.runs[0]

            if run.text == "":
                run.text = None

            ctrl = ET.SubElement(run.element, f"{{{HP_NS}}}ctrl")
            pic = ET.SubElement(ctrl, f"{{{HP_NS}}}pic")

            pic.set("id", str(uuid.uuid4().hex[:8]))
            pic.set("zorder", "0")
            pic.set("numberingType", "PICTURE")
            pic.set("textWrap", "TOPANDBOTTOM")
            pic.set("textFlow", "BOTH_SIDES")
            pic.set("lock", "0")
            pic.set("dropcap", "0")

            sz = ET.SubElement(pic, f"{{{HC_NS}}}sz")
            sz.set("width", str(width_hwp))
            sz.set("height", str(height_hwp))
            sz.set("widthRelTo", "ABSOLUTE")
            sz.set("heightRelTo", "ABSOLUTE")
            sz.set("protect", "0")

            pos = ET.SubElement(pic, f"{{{HC_NS}}}pos")
            pos.set("treatAsChar", "0")
            pos.set("affectLSpacing", "0")
            pos.set("flowWithText", "1")
            pos.set("allowOverlap", "0")
            pos.set("holdAnchorAndSO", "0")
            pos.set("vertRelTo", "PARA")
            pos.set("horzRelTo", "PARA")
            pos.set("vertAlign", "TOP")
            pos.set("horzAlign", "CENTER")
            pos.set("vertOffset", "0")
            pos.set("horzOffset", "0")

            img_rect = ET.SubElement(pic, f"{{{HC_NS}}}imgRect")
            img_rect.set("rad", "0")
            img_rect.set("szUnit", "RATIO")
            img_rect.set("style", "CENTER")
            img_rect.set("borderFillIDRef", "0")
            img_rect.set("binDataIDRef", str(bin_numeric_id))

            pt0 = ET.SubElement(img_rect, f"{{{HC_NS}}}pt0")
            pt0.set("x", "0")
            pt0.set("y", "0")
            pt1 = ET.SubElement(img_rect, f"{{{HC_NS}}}pt1")
            pt1.set("x", str(width_hwp))
            pt1.set("y", str(height_hwp))

            ET.SubElement(pic, f"{{{HC_NS}}}shapeComponent")

            self._text_content.append(f"[이미지: {filename}]")
            self._text_styles_map.append("default")

            return True

        except Exception as e:
            logger.error(f"Failed to insert image: {e}")
            import traceback

            traceback.print_exc()
            return False

    def add_chart(self, chart_type: str, data: dict, title: str = ""):
        if not HAS_MATPLOTLIB:
            self.add_text("[Error: matplotlib not installed]")
            return

        try:
            plt.figure(figsize=(6, 4))
            labels = data.get("labels", [])
            datasets = data.get("datasets", [])
            if chart_type == "bar":
                for ds in datasets:
                    plt.bar(labels, ds["data"], label=ds.get("label", ""))
            elif chart_type == "line":
                for ds in datasets:
                    plt.plot(labels, ds["data"], label=ds.get("label", ""))
            elif chart_type == "pie":
                if datasets:
                    plt.pie(datasets[0]["data"], labels=labels, autopct="%1.1f%%")
            if title:
                plt.title(title)
            if chart_type != "pie":
                plt.legend()

            buf = BytesIO()
            plt.savefig(buf, format="png", dpi=100)
            plt.close()
            buf.seek(0)
            image_data = buf.read()

            success = self.insert_image(
                image_data,
                f"chart_{uuid.uuid4().hex[:4]}.png",
                width_mm=150,
                height_mm=100,
            )

            if not success:
                self.add_text(f"[차트: {title} (이미지 생성 실패)]")
            else:
                logger.info(f"Chart image embedded: {title}")

        except Exception as e:
            logger.error(f"Error generating chart: {e}")
            self.add_text(f"[Error generating chart: {e}]")

    def build(self, output_path: str) -> bool:
        try:
            if hasattr(self._document, "save_to_path"):
                self._document.save_to_path(output_path)
            else:
                self._document.save(output_path)
            if not _fix_hwpx_for_viewer(
                output_path,
                self._text_content,
                self._table_styles,
                self._text_styles_map,
            ):
                raise RuntimeError("HWPX viewer compatibility post-processing failed")
            logger.info(f"HWPX document created: {output_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to create HWPX: {e}")
            raise

    def save(self, output_path: str) -> str:
        self.build(output_path)
        return output_path

    @property
    def document(self) -> "HwpxDocument":
        return self._document

    @property
    def text_content(self) -> str:
        return "\n".join(self._text_content)


def create_hwpx_from_text(text: str, output_path: str) -> bool:
    builder = HwpxBuilder()
    builder.add_text(text)
    return builder.build(output_path)


def create_hwpx_document(
    paragraphs: List[str], output_path: str, title: Optional[str] = None
) -> str:
    builder = HwpxBuilder()
    if title:
        builder.add_heading(title, level=1)
    for para in paragraphs:
        builder.add_text(para)
    return builder.save(output_path)


def create_hwpx_with_table(
    title: str, paragraphs: List[str], table_data: List[List[str]], output_path: str
) -> str:
    builder = HwpxBuilder()
    builder.add_heading(title, level=1)
    for para in paragraphs:
        builder.add_text(para)
    if table_data:
        rows = len(table_data)
        cols = max(len(row) for row in table_data) if table_data else 0
        builder.add_table(rows, cols, table_data, style="default")
    return builder.save(output_path)
