"""Read-only OOXML schemas and records using an explicit configurable layout."""
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from xml.etree import ElementTree as ET
import zipfile
from .common import ToolError, atomic_json, canonical, file_hash

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
N = {"s": MAIN}


def column_number(address):
    letters = re.match(r"[A-Za-z]+", str(address))
    if not letters:
        raise ToolError("cell", "Invalid column or cell: " + str(address))
    result = 0
    for letter in letters[0].upper():
        result = result * 26 + ord(letter) - 64
    return result


def column_name(number):
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def workbook_path(project, root_name, filename):
    root = project.root(root_name)
    relative = Path(filename)
    path = (root / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(root) or path == root or path.suffix.lower() != ".xlsx":
        raise ToolError("workbook_scope", "Workbook must be a relative .xlsx path inside the selected root.")
    if path.name.startswith("~$"):
        raise ToolError("workbook_scope", "Excel lock files are not workbooks.")
    return path


def _visible(element):
    return "".join(node.text or "" for child in element for node in
                   ([child] if child.tag == "{" + MAIN + "}t" else list(child) if child.tag == "{" + MAIN + "}r" else [])
                   if node.tag == "{" + MAIN + "}t")


def _value(cell, shared):
    kind = cell.get("t", "n")
    if kind == "inlineStr":
        node = cell.find("s:is", N)
        return _visible(node) if node is not None else ""
    raw = cell.findtext("s:v", default="", namespaces=N)
    if kind == "s":
        try:
            if not re.fullmatch(r"[0-9]+",raw) or int(raw)>=len(shared):
                raise ValueError()
            return shared[int(raw)]
        except (ValueError, IndexError) as exc:
            raise ToolError("shared_string", "Invalid shared string index.") from exc
    if kind == "b":
        return raw == "1"
    if kind in ("str", "e", "d") or raw == "":
        return raw
    try:
        if re.fullmatch(r"[+-]?[0-9]+",raw):
            return int(raw)
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError()
        return int(value) if value.is_integer() else value
    except ValueError as exc:
        raise ToolError("cell_value", "Invalid numeric cell value: " + raw) from exc


@dataclass
class Sheet:
    file: str
    title: str
    member: str
    raw: bytes
    shared: list
    table: str
    fields: dict
    types: dict
    constraints: dict
    descriptions: dict
    primary: str
    rows: dict
    cells: dict
    records: list
    start: int
    end: int
    source_sha256: str

    def schema(self):
        return {"file": self.file, "sheet": self.title, "table": self.table,
                "fields": self.fields, "types": self.types, "constraints": self.constraints,
                "descriptions": self.descriptions, "primary": self.primary,
                "source_sha256": self.source_sha256,
                "schema_sha256": hashlib.sha256(canonical({"table":self.table,"fields":self.fields,
                    "types":self.types,"constraints":self.constraints,"primary":self.primary}).encode()).hexdigest()}


def _table_name(project, file, title, values):
    for table, locations in project.config.get("tables", {}).items():
        if isinstance(locations, dict):
            locations = [locations]
        if any(row.get("file") == file and row.get("sheet") == title for row in locations):
            return table
    cell = project.layout.get("logical_table_cell")
    if cell:
        value = values.get(cell)
        if value in (None, ""):
            raise ToolError("logical_table", "Missing logical table identity at " + cell + ": " + file + "/" + title)
        return str(value)
    return title


def read_workbook(project, root_name, filename):
    path = workbook_path(project, root_name, filename)
    before = file_hash(path)
    result = []
    with zipfile.ZipFile(path) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise ToolError("zip_members", "Duplicate workbook archive members.")
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relations = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {node.get("Id"):node.get("Target") for node in relations}
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = [_visible(node) for node in ET.fromstring(archive.read("xl/sharedStrings.xml"))]
        for node in workbook.findall("s:sheets/s:sheet", N):
            title = node.get("name", "")
            if any(title.startswith(prefix) for prefix in project.layout.get("skip_sheet_prefixes", [])):
                continue
            target = targets[node.get("{" + REL + "}id")]
            member = target.lstrip("/") if target.startswith("/") else "xl/" + target
            if ".." in Path(member).parts:
                raise ToolError("zip_member", "Unsafe worksheet relationship.")
            raw = archive.read(member)
            root = ET.fromstring(raw)
            cells, rows, values = {}, {}, {}
            for row in root.findall("s:sheetData/s:row", N):
                number = int(row.get("r", "0"))
                if not 1<=number<=1048576 or number in rows:
                    raise ToolError("layout", "Repeated or invalid worksheet row.")
                rows[number] = row
                for cell in row.findall("s:c", N):
                    address = cell.get("r")
                    if address in cells or not re.fullmatch(r"[A-Z]+" + str(number), address or ""):
                        raise ToolError("layout", "Repeated or invalid cell address.")
                    if column_number(address)>16384:
                        raise ToolError("layout", "Cell lies outside the spreadsheet column limit.")
                    cells[address] = cell
                    formula = cell.find("s:f", N)
                    values[address] = "=" + (formula.text or "") if formula is not None else _value(cell, shared)
            field_row = project.layout["field_row"]
            names = [(column_number(address), str(value)) for address, value in values.items()
                     if re.fullmatch(r"[A-Z]+" + str(field_row), address) and value not in (None, "")]
            marker = project.layout.get("marker_column")
            fields = {}
            for column, name in names:
                if marker and column == column_number(marker) or name.startswith("$"):
                    continue
                if name in fields:
                    raise ToolError("duplicate_field", "Repeated field: " + name)
                fields[name] = column
            if not fields:
                raise ToolError("fields", "No exported fields in " + filename + "/" + title)
            def header(key):
                row = project.layout.get(key)
                return {name:str(values.get(column_name(column) + str(row), "")) for name,column in fields.items()} if row else {}
            constraints, types, descriptions = header("constraint_row"), header("type_row"), header("description_row")
            primaries = [name for name,value in constraints.items() if value.upper() == "PRIMARY"]
            primary = project.layout.get("primary_field") or (primaries[0] if len(primaries) == 1 else None)
            if not primary or primary not in fields or len(primaries) > 1:
                raise ToolError("primary", "Declare one PRIMARY or layout.primary_field.")
            starts, ends = [], []
            for number in sorted(rows):
                if marker:
                    mark = values.get(marker + str(number))
                    if project.layout.get("start_marker") and mark == project.layout["start_marker"]:
                        starts.append(number)
                    if project.layout.get("end_marker") and mark == project.layout["end_marker"]:
                        ends.append(number)
            if project.layout.get("start_marker") and len(starts) != 1 or project.layout.get("end_marker") and len(ends) != 1:
                raise ToolError("boundary", "Configured markers must each occur exactly once.")
            start = starts[0] if starts else project.layout["data_start_row"]
            end = ends[0] if ends else max(rows, default=start - 1)
            inclusive_end = project.layout.get("include_end", True)
            if start < project.layout["data_start_row"] or end < start:
                raise ToolError("boundary", "Invalid effective record range.")
            records, seen = [], set()
            for number in sorted(rows):
                if not start <= number <= end or number == end and not inclusive_end:
                    continue
                record = {field:values.get(column_name(column) + str(number), "") for field,column in fields.items()}
                if all(value in (None, "") for value in record.values()):
                    if number in starts or number in ends:
                        raise ToolError("empty_boundary", "A boundary record has no PRIMARY value.")
                    continue
                key = record[primary]
                if cells.get(column_name(fields[primary])+str(number)) is not None and cells[column_name(fields[primary])+str(number)].find("s:f",N) is not None:
                    raise ToolError("formula_primary", "Formula PRIMARY values require an explicit evaluated-key adapter.")
                if key in (None, "") or str(key) in seen:
                    raise ToolError("primary", "Missing or duplicate PRIMARY: " + str(key))
                seen.add(str(key))
                records.append({"row":number,"id":key,"values":record})
            result.append(Sheet(filename,title,member,raw,shared,_table_name(project,filename,title,values),
                                fields,types,constraints,descriptions,primary,rows,cells,records,start,end,before))
    if file_hash(path) != before:
        raise ToolError("source_changed", "Workbook changed while being read.")
    return result


def get_sheet(project, root_name, filename, title):
    matches = [sheet for sheet in read_workbook(project, root_name, filename) if sheet.title == title]
    if len(matches) != 1:
        raise ToolError("sheet", "Sheet is missing, excluded or ambiguous: " + title)
    return matches[0]


def build_index(project, root_name, output=None):
    root = project.root(root_name)
    if not root.is_dir():
        raise ToolError("root", "Data root does not exist.")
    tables = []
    for path in sorted(root.rglob("*.xlsx")):
        if not path.name.startswith("~$"):
            tables.extend(dict(sheet.schema(), root=root_name) for sheet in read_workbook(project,root_name,path.relative_to(root).as_posix()))
    result = {"schema_version":1,"kind":"table_index","project_id":project.project_id,
              "roots":{root_name:str(root)},"tables":tables,"business_writes":0}
    if output:
        output = Path(output).resolve()
        if output.is_relative_to(root):
            raise ToolError("output_scope", "Index must be written outside the data root.")
        atomic_json(output, result)
    return result


def read_table(project, root_name, filename, title, ids=None):
    sheet = get_sheet(project, root_name, filename, title)
    selected = sheet.records if ids is None else [row for row in sheet.records if str(row["id"]) in {str(key) for key in ids}]
    return {"status":"read","project_id":project.project_id,"root":root_name,
            "schema":sheet.schema(),"records":selected,"business_writes":0}
