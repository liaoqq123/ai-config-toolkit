"""Minimal worksheet edits and metadata-preserving archive staging.

Archive profile checks and cell splicing are extracted from the source toolkit's
generic ZIP writer. Untouched archive members retain their exact content bytes.
"""
import copy
import re
import struct
from xml.etree import ElementTree as ET
import zipfile
from .common import ToolError
from .excel import MAIN, N, column_name, column_number


def _extra(extra):
    offset = 0
    while offset < len(extra):
        if offset + 4 > len(extra):
            raise ToolError("zip_profile", "Incomplete ZIP extra field.")
        kind, size = struct.unpack_from("<HH", extra, offset)
        offset += 4 + size
        if kind == 1 or offset > len(extra):
            raise ToolError("zip_profile", "ZIP64 or incomplete ZIP extras are unsupported.")


def _local_extra(archive, info):
    if (info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
            or info.flag_bits & ~0x806 or info.extract_version > 20 or info.volume != 0
            or max(info.file_size, info.compress_size, info.header_offset) >= zipfile.ZIP64_LIMIT
            or info.orig_filename != info.filename or info.compress_type == zipfile.ZIP_STORED and info.flag_bits & 6):
        raise ToolError("zip_profile", "Unsupported encrypted, descriptor or ZIP64 archive member.")
    _extra(info.extra)
    archive.fp.seek(info.header_offset)
    header = archive.fp.read(30)
    if len(header) != 30:
        raise ToolError("zip_profile", "Incomplete local ZIP header.")
    signature, version, flags, compression, time, date, crc, compressed, size, nlen, elen = struct.unpack("<4s5H3I2H", header)
    name, extra = archive.fp.read(nlen), archive.fp.read(elen)
    encoded, encoded_flags = info._encodeFilenameFlags()
    year, month, day, hour, minute, second = info.date_time
    if (signature != b"PK\x03\x04" or version != info.extract_version | info.reserved << 8
            or flags != info.flag_bits or encoded_flags != info.flag_bits or name != encoded
            or compression != info.compress_type or crc != info.CRC or compressed != info.compress_size
            or size != info.file_size or time != hour << 11 | minute << 5 | second // 2
            or date != (year - 1980) << 9 | month << 5 | day or len(extra) != elen):
        raise ToolError("zip_profile", "Local and central ZIP headers do not agree.")
    _extra(extra)
    return extra


class _MetadataZipFile(zipfile.ZipFile):
    def _writecheck(self, info):
        info.flag_bits, info.external_attr = self.source_attributes[info.filename]
        return super()._writecheck(info)


def stage_many(source, destination, changes):
    with zipfile.ZipFile(source) as original, _MetadataZipFile(destination, "w", allowZip64=False) as target:
        if len(original.namelist()) != len(set(original.namelist())) or not changes or not set(changes) <= set(original.namelist()):
            raise ToolError("zip_members", "Duplicate or missing target workbook members.")
        if len(original.infolist()) >= zipfile.ZIP_FILECOUNT_LIMIT or original.start_dir >= zipfile.ZIP64_LIMIT:
            raise ToolError("zip_profile", "ZIP64 workbooks are unsupported.")
        target.comment = original.comment
        target.source_attributes = {info.filename:(info.flag_bits,info.external_attr) for info in original.infolist()}
        for info in original.infolist():
            local_extra = _local_extra(original, info)
            output = copy.copy(info)
            output.extra = local_extra
            target.writestr(output, changes.get(info.filename, original.read(info.filename)))
            output.extra, output.external_attr = info.extra, info.external_attr


def _fragments(raw, tag):
    return list(re.finditer(rb'<(?P<tag>(?:[A-Za-z_][\w.-]*:)?' + tag + rb')\b[^>]*?(?:\s*/>|>.*?</(?P=tag)\s*>)', raw, re.S))


def _attr(raw, name):
    match = re.search(rb'\b' + name + rb'\s*=\s*["\']([^"\']*)["\']', raw)
    return match[1].decode() if match else ""


def literal_cell(address, value, original=None):
    attrs = {"r":address}
    if original is not None and original.get("s") is not None:
        attrs["s"] = original.get("s")
    cell = ET.Element("{" + MAIN + "}c", attrs)
    if isinstance(value, bool):
        cell.set("t", "b")
        ET.SubElement(cell, "{" + MAIN + "}v").text = "1" if value else "0"
    elif isinstance(value, (int, float)):
        ET.SubElement(cell, "{" + MAIN + "}v").text = str(value)
    elif value is not None:
        cell.set("t", "inlineStr")
        inline = ET.SubElement(cell, "{" + MAIN + "}is")
        node = ET.SubElement(inline, "{" + MAIN + "}t")
        node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        node.text = str(value)
    return cell


def _range_contains(reference,address):
    bounds=reference.replace("$","").split(":")
    if len(bounds)>2 or any(not re.fullmatch(r"[A-Z]+[1-9][0-9]*",bound) for bound in bounds):
        raise ToolError("layout", "Unsupported protected cell range.")
    first,last=bounds[0],bounds[-1]
    row=int(re.search(r"[0-9]+$",address)[0])
    return (column_number(first)<=column_number(address)<=column_number(last)
            and int(re.search(r"[0-9]+$",first)[0])<=row<=int(re.search(r"[0-9]+$",last)[0]))


def _guard_cell(sheet,address):
    if not hasattr(sheet,"_protected_ranges"):
        root=ET.fromstring(sheet.raw)
        sheet._protected_ranges=(
            [node.get("ref","") for node in root.findall("s:mergeCells/s:mergeCell",N)],
            [node.get("ref") for node in root.findall("s:sheetData/s:row/s:c/s:f",N) if node.get("ref")])
    merged,formulas=sheet._protected_ranges
    if any(_range_contains(reference,address) for reference in merged):
        raise ToolError("merged_cell", "Merged cells need an explicit layout adapter.")
    for reference in formulas:
        if _range_contains(reference,address):
            raise ToolError("formula", "Target belongs to a multi-cell formula range.")


def patch_cells(sheet, edits):
    raw = sheet.raw
    for edit in edits:
        address, value = edit["address"], edit["after"]
        _guard_cell(sheet,address)
        number = int(re.search(r"\d+$", address)[0])
        original = sheet.cells.get(address)
        if original is not None and (original.find("s:f", N) is not None or set(original.attrib) - {"r", "s", "t"}
                                    or any(child.tag not in ("{"+MAIN+"}v", "{"+MAIN+"}is") for child in original)):
            raise ToolError("formula", "Formula or extended cells require a dedicated adapter: " + address)
        rows = [match for match in _fragments(raw,b"row") if _attr(match[0],b"r") == str(number)]
        if len(rows) != 1:
            raise ToolError("layout", "Target row is missing or ambiguous.")
        match, chunk = rows[0], rows[0][0]
        replacement = ET.tostring(literal_cell(address,value,original),encoding="utf-8").replace(b'\r',b'&#13;')
        cells = _fragments(chunk,b"c")
        hits = [candidate for candidate in cells if _attr(candidate[0],b"r") == address]
        if len(hits) > 1:
            raise ToolError("layout", "Duplicate target cell.")
        if hits:
            hit = hits[0]
            chunk = chunk[:hit.start()] + replacement + chunk[hit.end():]
        else:
            later = [candidate for candidate in cells if column_number(_attr(candidate[0],b"r")) > column_number(address)]
            closing = re.search(rb'</(?:[A-Za-z_][\w.-]*:)?row\s*>',chunk)
            if not later and closing is None:
                raise ToolError("layout", "Cannot safely insert target cell.")
            offset = later[0].start() if later else closing.start()
            chunk = chunk[:offset] + replacement + chunk[offset:]
        raw = raw[:match.start()] + chunk + raw[match.end():]
    ET.fromstring(raw)
    return raw


def append_records(sheet, records, layout):
    if not records:
        raise ToolError("append", "Append requires at least one row.")
    if sheet.end+len(records)>1048576:
        raise ToolError("append_layout", "Append exceeds the worksheet row limit.")
    if layout.get("end_marker") and not layout.get("include_end",True):
        raise ToolError("append_layout", "Standalone end-marker layouts need an explicit append adapter.")
    root = ET.fromstring(sheet.raw)
    if any(root.find("s:" + name,N) is not None for name in ("mergeCells","tableParts","dataValidations","autoFilter","conditionalFormatting")):
        raise ToolError("append_layout", "Merged, table or range-controlled layouts require an explicit append adapter.")
    if max(sheet.rows,default=sheet.end) != sheet.end:
        raise ToolError("append_layout", "Rows after the effective range require an explicit tail-layout adapter.")
    template = sheet.rows.get(sheet.end)
    if template is None:
        raise ToolError("append_layout", "Append requires an existing template record row.")
    if any(cell.find("s:f",N) is not None for cell in template.findall("s:c",N)):
        raise ToolError("formula", "Appending a formula template needs an explicit formula adapter.")
    raw = sheet.raw
    marker = layout.get("marker_column")
    if layout.get("end_marker"):
        raw = patch_cells(sheet,[{"address":marker+str(sheet.end),"after":""}])
    additions=[]
    for offset, record in enumerate(records,1):
        number=sheet.end+offset
        for column in sheet.fields.values():
            _guard_cell(sheet,column_name(column)+str(number))
        attrs=dict(template.attrib,r=str(number))
        row=ET.Element("{"+MAIN+"}row",attrs)
        for field,column in sorted(sheet.fields.items(),key=lambda pair:pair[1]):
            old=sheet.cells.get(column_name(column)+str(sheet.end))
            row.append(literal_cell(column_name(column)+str(number),record[field],old))
        if marker and layout.get("end_marker"):
            row.insert(0,literal_cell(marker+str(number),layout["end_marker"] if offset==len(records) else ""))
        additions.append(ET.tostring(row,encoding="utf-8").replace(b'\r',b'&#13;'))
    closing=re.search(rb'</(?:[A-Za-z_][\w.-]*:)?sheetData\s*>',raw)
    if closing is None:
        raise ToolError("layout", "Worksheet data boundary is missing.")
    raw=raw[:closing.start()]+b"".join(additions)+raw[closing.start():]
    dimension=re.search(rb'<(?:[A-Za-z_][\w.-]*:)?dimension\b[^>]*\bref=["\']([^"\']+)["\'][^>]*/>',raw)
    if dimension:
        old=dimension[1]
        start=old.split(b":")[0]
        last=column_name(max(sheet.fields.values())).encode()+str(sheet.end+len(records)).encode()
        raw=raw[:dimension.start(1)]+start+b":"+last+raw[dimension.end(1):]
    ET.fromstring(raw)
    return raw
