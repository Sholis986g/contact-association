"""
Utilitas generator berkas spreadsheet XLSX menggunakan pure Python standard library (zipfile + xml).
Memungkinkan pembuatan fixture dan benchmark XLSX tanpa dependensi openpyxl eksternal.
"""
from __future__ import annotations
import os
import xml.etree.ElementTree as ET
import zipfile
from typing import Any, Dict, List


def create_simple_xlsx(file_path: str, sheets: Dict[str, List[List[Any]]]) -> str:
    """
    Membuat berkas XLSX yang valid dengan beberapa lembar kerja (sheets).
    sheets: dict nama_sheet -> list baris (setiap baris list nilai).
    """
    os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)

    # Kumpulkan semua teks ke shared strings
    shared_strings: List[str] = []
    string_map: Dict[str, int] = {}

    def get_str_id(s: str) -> int:
        if s not in string_map:
            string_map[s] = len(shared_strings)
            shared_strings.append(s)
        return string_map[s]

    sheet_names = list(sheets.keys())

    # Build XMLs
    # 1. [Content_Types].xml
    ct_root = ET.Element("Types", xmlns="http://schemas.openxmlformats.org/package/2006/content-types")
    ET.SubElement(ct_root, "Default", Extension="rels", ContentType="application/vnd.openxmlformats-package.relationships+xml")
    ET.SubElement(ct_root, "Default", Extension="xml", ContentType="application/xml")
    ET.SubElement(ct_root, "Override", PartName="/xl/workbook.xml", ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml")
    ET.SubElement(ct_root, "Override", PartName="/xl/sharedStrings.xml", ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml")

    for i in range(1, len(sheet_names) + 1):
        ET.SubElement(ct_root, "Override", PartName=f"/xl/worksheets/sheet{i}.xml", ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml")

    content_types_xml = ET.tostring(ct_root, encoding="utf-8", xml_declaration=True)

    # 2. _rels/.rels
    rels_root = ET.Element("Relationships", xmlns="http://schemas.openxmlformats.org/package/2006/relationships")
    ET.SubElement(rels_root, "Relationship", Id="rId1", Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument", Target="xl/workbook.xml")
    rels_xml = ET.tostring(rels_root, encoding="utf-8", xml_declaration=True)

    # 3. xl/workbook.xml
    wb_root = ET.Element("workbook", xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main", attrib={"xmlns:r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"})
    sheets_elem = ET.SubElement(wb_root, "sheets")
    for i, sname in enumerate(sheet_names, 1):
        ET.SubElement(sheets_elem, "sheet", name=sname, sheetId=str(i), attrib={"r:id": f"rId{i}"})
    workbook_xml = ET.tostring(wb_root, encoding="utf-8", xml_declaration=True)

    # 4. xl/_rels/workbook.xml.rels
    wb_rels_root = ET.Element("Relationships", xmlns="http://schemas.openxmlformats.org/package/2006/relationships")
    for i in range(1, len(sheet_names) + 1):
        ET.SubElement(wb_rels_root, "Relationship", Id=f"rId{i}", Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet", Target=f"worksheets/sheet{i}.xml")
    ET.SubElement(wb_rels_root, "Relationship", Id=f"rId{len(sheet_names)+1}", Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings", Target="sharedStrings.xml")
    workbook_rels_xml = ET.tostring(wb_rels_root, encoding="utf-8", xml_declaration=True)

    # Build sheets XML
    sheet_xmls: Dict[str, bytes] = {}
    for i, sname in enumerate(sheet_names, 1):
        rows = sheets[sname]
        ws_root = ET.Element("worksheet", xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main")
        sdata = ET.SubElement(ws_root, "sheetData")

        def col_to_letter(col_idx: int) -> str:
            res = ""
            col_idx += 1
            while col_idx > 0:
                col_idx, rem = divmod(col_idx - 1, 26)
                res = chr(65 + rem) + res
            return res

        for r_idx, row in enumerate(rows, 1):
            r_elem = ET.SubElement(sdata, "row", r=str(r_idx))
            for c_idx, cell_val in enumerate(row):
                if cell_val is None:
                    continue
                cell_ref = f"{col_to_letter(c_idx)}{r_idx}"
                c_elem = ET.SubElement(r_elem, "c", r=cell_ref)
                if isinstance(cell_val, (int, float)):
                    v_elem = ET.SubElement(c_elem, "v")
                    v_elem.text = str(cell_val)
                else:
                    s_id = get_str_id(str(cell_val))
                    c_elem.set("t", "s")
                    v_elem = ET.SubElement(c_elem, "v")
                    v_elem.text = str(s_id)

        sheet_xmls[f"xl/worksheets/sheet{i}.xml"] = ET.tostring(ws_root, encoding="utf-8", xml_declaration=True)

    # 5. xl/sharedStrings.xml
    sst_root = ET.Element("sst", xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main", count=str(len(shared_strings)), uniqueCount=str(len(shared_strings)))
    for s in shared_strings:
        si = ET.SubElement(sst_root, "si")
        t = ET.SubElement(si, "t")
        t.text = s
    shared_strings_xml = ET.tostring(sst_root, encoding="utf-8", xml_declaration=True)

    # Tulis ke file ZIP
    with zipfile.ZipFile(file_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types_xml)
        z.writestr("_rels/.rels", rels_xml)
        z.writestr("xl/workbook.xml", workbook_xml)
        z.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        z.writestr("xl/sharedStrings.xml", shared_strings_xml)
        for part_name, data_bytes in sheet_xmls.items():
            z.writestr(part_name, data_bytes)

    return file_path
