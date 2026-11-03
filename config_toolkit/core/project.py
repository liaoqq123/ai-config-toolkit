"""Explicit project profiles; no machine paths or domain tables are built in."""
from pathlib import Path
import re
from .common import ToolError, file_hash, load_json


class Project:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.config = load_json(self.path)
        if self.config.get("schema_version") != 1:
            raise ToolError("project_version", "Project schema_version must be 1.")
        self.project_id = self.config.get("project_id", "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", self.project_id):
            raise ToolError("project_id", "project_id must be a short portable identifier.")
        roots = self.config.get("data_roots")
        if not isinstance(roots, dict) or not roots:
            raise ToolError("data_roots", "Declare at least one named data root.")
        if any(not isinstance(key,str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}",key) for key in roots):
            raise ToolError("data_roots", "Use short portable names for data roots.")
        self.data_roots = {key: self.resolve_config_path(value) for key, value in roots.items()}
        self.state_dir = self.resolve_config_path(self.config.get("state_dir", "../.work/" + self.project_id))
        for root in self.data_roots.values():
            if self.state_dir == root or self.state_dir.is_relative_to(root) or root.is_relative_to(self.state_dir):
                raise ToolError("state_scope", "State and workbook roots must be separate, non-nested directories.")
        self.layout = dict((self.config.get("excel") or {}).get("layout") or {})
        allowed={"logical_table_cell","field_row","type_row","description_row","constraint_row","data_start_row",
                 "marker_column","start_marker","end_marker","include_end","skip_sheet_prefixes","primary_field"}
        if set(self.layout)-allowed:
            raise ToolError("layout", "Unknown layout options: " + ", ".join(sorted(set(self.layout)-allowed)))
        for key in ("field_row", "data_start_row"):
            if type(self.layout.get(key)) is not int or self.layout[key] < 1:
                raise ToolError("layout", "Declare a positive integer layout." + key)
        if self.layout["data_start_row"] <= self.layout["field_row"]:
            raise ToolError("layout", "data_start_row must follow field_row.")
        for key in ("type_row", "description_row", "constraint_row"):
            if self.layout.get(key) is not None and (type(self.layout[key]) is not int or self.layout[key] < 1):
                raise ToolError("layout", key + " must be a positive integer or null.")
            if self.layout.get(key) and self.layout[key]>=self.layout["data_start_row"]:
                raise ToolError("layout", "Metadata header rows must precede data_start_row.")
        if self.layout.get("logical_table_cell"):
            value=self.layout["logical_table_cell"]
            if not isinstance(value,str) or not re.fullmatch(r"[A-Z]+[1-9][0-9]*",value):
                raise ToolError("layout", "logical_table_cell must be an uppercase cell address or null.")
        if self.layout.get("marker_column") and (not isinstance(self.layout["marker_column"],str)
                or not re.fullmatch(r"[A-Z]+",self.layout["marker_column"])):
            raise ToolError("layout", "marker_column must be an uppercase column name.")
        if type(self.layout.get("include_end",True)) is not bool:
            raise ToolError("layout", "include_end must be a JSON boolean.")
        prefixes=self.layout.get("skip_sheet_prefixes",[])
        if not isinstance(prefixes,list) or any(not isinstance(value,str) or not value for value in prefixes):
            raise ToolError("layout", "skip_sheet_prefixes must be a list of nonempty strings.")
        for key in ("start_marker", "end_marker"):
            if self.layout.get(key) is not None and not isinstance(self.layout[key],str):
                raise ToolError("layout", key + " must be a string or null.")
            if self.layout.get(key) and not self.layout.get("marker_column"):
                raise ToolError("layout", "Markers require marker_column.")
        if self.layout.get("start_marker") and self.layout.get("start_marker")==self.layout.get("end_marker"):
            raise ToolError("layout", "Start and end marker strings must be distinct.")
        references=self.config.get("references",[])
        if not isinstance(references,list):
            raise ToolError("references", "references must be a list of explicit field contracts.")
        for reference in references:
            if (not isinstance(reference,dict) or any(not isinstance(reference.get(key),str) or not reference[key]
                    for key in ("table","field","target_table","target_field"))
                    or type(reference.get("nullable",True)) is not bool):
                raise ToolError("references", "Each reference requires table/field/target_table/target_field and optional boolean nullable.")
        aliases=self.config.get("aliases",{})
        if not isinstance(aliases,dict) or any(not isinstance(key,str) or not isinstance(value,str) or not key or not value
                                             for key,value in aliases.items()):
            raise ToolError("aliases", "aliases must map nonempty strings to explicit canonical table names.")
        tables=self.config.get("tables",{})
        if not isinstance(tables,dict):
            raise ToolError("tables", "tables must map logical names to explicit file/sheet locations.")
        for table,locations in tables.items():
            if isinstance(locations,dict):
                locations=[locations]
            if not isinstance(table,str) or not table or not isinstance(locations,list) or not locations:
                raise ToolError("tables", "Each table needs at least one source location.")
            if any(not isinstance(item,dict) or any(not isinstance(item.get(key),str) or not item[key]
                   for key in ("file","sheet")) for item in locations):
                raise ToolError("tables", "Each table source requires explicit file and sheet strings.")
        self.digest = file_hash(self.path)

    def resolve_config_path(self, value):
        if not isinstance(value, str) or not value:
            raise ToolError("path", "A nonempty path string is required.")
        path = Path(value)
        return (path if path.is_absolute() else self.path.parent / path).resolve()

    def root(self, name):
        if name not in self.data_roots:
            raise ToolError("root", "Unknown data root: " + str(name))
        return self.data_roots[name]


def load_project(path):
    return Project(path)
