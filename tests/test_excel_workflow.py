"""Synthetic-workbook tests; these never open an external project data root."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from openpyxl import Workbook,load_workbook

from config_toolkit.core.common import ToolError,file_hash,load_json
from config_toolkit.core.project import load_project
from config_toolkit.core.excel import build_index,read_table
from config_toolkit.core.workflow import Workflow
from config_toolkit.core import excel as excel_module


class ExcelWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base=Path(temporary.name)
        self.data=self.base/"data"
        self.data.mkdir()
        self.profile=self.base/"project.json"
        self.config={"schema_version":1,"project_id":"isolated","data_roots":{"demo":"data"},"state_dir":"state",
                     "excel":{"layout":{"logical_table_cell":"A1","field_row":5,"type_row":4,"description_row":2,
                        "constraint_row":6,"data_start_row":7,"marker_column":"A","start_marker":"Start","end_marker":"End",
                        "include_end":True,"skip_sheet_prefixes":["$"]}},
                     "tables":{"Groups":[{"file":"Groups.xlsx","sheet":"配置"}]},"references":[]}
        self.write_profile()
        self.make_book("Catalog.xlsx","Catalog",["Id","Name","GroupId","Count"],["string","string","string","int"],
                       ["PRIMARY","","FOREIGN:Groups.Id",""],[["item_1","First","group_1",1],["item_2","Second","group_1",2]])
        self.make_book("Groups.xlsx","Groups",["Id","Name"],["string","string"],["PRIMARY",""],
                       [["group_1","Group one"],["group_2","Group two"]])
        self.project=load_project(self.profile)
        self.engine=Workflow(self.project,"demo")

    def write_profile(self):
        self.profile.write_text(json.dumps(self.config,ensure_ascii=False),encoding="utf-8")

    def make_book(self,filename,logical,fields,types,constraints,rows):
        book=Workbook()
        sheet=book.active
        sheet.title="配置"
        sheet["A1"]=logical
        for column,field in enumerate(fields,2):
            sheet.cell(2,column,"Description "+field)
            sheet.cell(4,column,types[column-2])
            sheet.cell(5,column,field)
            sheet.cell(6,column,constraints[column-2])
        for number,values in enumerate(rows,7):
            for column,value in enumerate(values,2):
                sheet.cell(number,column,value)
        sheet["A7"]="Start"
        sheet.cell(6+len(rows),1,"End")
        book.save(self.data/filename)

    def request(self,**change):
        return {"operation":"patch","file":"Catalog.xlsx","sheet":"配置","rows":[{"id":"item_1","changes":change or {"Name":{"before":"First","after":"Updated"}}}]}

    def approved(self,request=None):
        preview=self.engine.preview(request or self.request())
        approval=self.engine.approve(preview["preview_id"],"Synthetic reviewer","Explicit isolated test decision")
        return preview,approval

    def test_read_index_and_configurable_layout(self):
        source_before=file_hash(self.data/"Catalog.xlsx")
        result=read_table(self.project,"demo","Catalog.xlsx","配置",["item_1"])
        self.assertEqual(result["records"][0]["values"]["Name"],"First")
        index=build_index(self.project,"demo",self.base/"index.json")
        self.assertEqual(index["kind"],"table_index")
        self.assertEqual(len(index["tables"]),2)
        self.assertEqual(file_hash(self.data/"Catalog.xlsx"),source_before)
        self.assertEqual(index["tables"][0]["fields"]["Id"],2)
        with self.assertRaises(ToolError):
            build_index(self.project,"demo",self.data/"index.json")
        self.config["excel"]["layout"]={"logical_table_cell":None,"field_row":1,"data_start_row":2,"primary_field":"Id"}
        self.write_profile()
        simple=self.base/"simple"
        simple.mkdir()
        book=Workbook();sheet=book.active;sheet.title="Plain"
        sheet.append(["Id","Name"]);sheet.append(["a","Alpha"]);sheet.append(["b","Beta"])
        book.save(simple/"Plain.xlsx")
        self.config["data_roots"]={"simple":"simple"}
        self.write_profile()
        alternate=load_project(self.profile)
        self.assertEqual(read_table(alternate,"simple","Plain.xlsx","Plain")["records"][0]["id"],"a")
        workflow=Workflow(alternate,"simple")
        preview=workflow.preview({"operation":"patch","file":"Plain.xlsx","sheet":"Plain","rows":[{"id":"a","changes":{"Name":{"before":"Alpha","after":"New"}}}]})
        self.assertEqual(preview["after"][0]["values"]["Name"],"New")

    def test_patch_approval_backup_readback_and_idempotence(self):
        preview,approval=self.approved()
        target=self.data/"Catalog.xlsx"
        before=target.read_bytes()
        with zipfile.ZipFile(target) as archive:
            members={name:archive.read(name) for name in archive.namelist()}
        receipt=self.engine.apply(preview["preview_id"],approval["approval_id"])
        self.assertEqual(receipt["status"],"completed")
        self.assertEqual(receipt["readback"]["status"],"passed")
        self.assertEqual(Path(receipt["backup"]).read_bytes(),before)
        self.assertEqual(read_table(self.project,"demo","Catalog.xlsx","配置")["records"][0]["values"]["Name"],"Updated")
        with zipfile.ZipFile(target) as archive:
            for name,raw in members.items():
                if name!="xl/worksheets/sheet1.xml":
                    self.assertEqual(archive.read(name),raw,name)
        repeated=self.engine.apply(preview["preview_id"],approval["approval_id"])
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(file_hash(target),receipt["after_sha256"])

    def test_append_moves_end_and_keeps_old_records(self):
        request={"operation":"append","file":"Catalog.xlsx","sheet":"配置","rows":[{"Id":"item_3","Name":"Third","GroupId":"group_2","Count":3}]}
        preview,approval=self.approved(request)
        self.engine.apply(preview["preview_id"],approval["approval_id"])
        read=read_table(self.project,"demo","Catalog.xlsx","配置")
        self.assertEqual([row["id"] for row in read["records"]],["item_1","item_2","item_3"])
        book=load_workbook(self.data/"Catalog.xlsx")
        self.assertEqual(book["配置"]["A7"].value,"Start")
        self.assertEqual(book["配置"]["A9"].value,"End")
        self.assertNotEqual(book["配置"]["A8"].value,"End")

    def test_before_type_and_primary_validation(self):
        for request in (self.request(Name={"before":"wrong","after":"new"}),
                        self.request(Count={"before":1,"after":"not numeric"}),
                        self.request(Id={"before":"item_1","after":"other"}),
                        self.request(Count={"before":True,"after":2})):
            with self.subTest(request=request),self.assertRaises(ToolError):
                self.engine.preview(request)

    def test_formula_is_read_and_preserved_but_never_overwritten(self):
        target=self.data/"Catalog.xlsx"
        book=load_workbook(target);book["配置"]["E7"]="=1+1";book.save(target)
        self.assertEqual(read_table(self.project,"demo","Catalog.xlsx","配置")["records"][0]["values"]["Count"],"=1+1")
        with self.assertRaises(ToolError):
            self.engine.preview(self.request(Count={"before":"=1+1","after":2}))
        preview,approval=self.approved()
        self.engine.apply(preview["preview_id"],approval["approval_id"])
        self.assertEqual(load_workbook(target)["配置"]["E7"].value,"=1+1")

    def test_reference_invalid_and_dependency_changes_block_writes(self):
        with self.assertRaises(ToolError):
            self.engine.preview(self.request(GroupId={"before":"group_1","after":"missing"}))
        preview,approval=self.approved()
        self.assertIn("Groups.xlsx",preview["source_hashes"])
        before=(self.data/"Catalog.xlsx").read_bytes()
        group=self.data/"Groups.xlsx"
        book=load_workbook(group);book["配置"]["C7"]="Changed dependency";book.save(group)
        with self.assertRaises(ToolError):
            self.engine.apply(preview["preview_id"],approval["approval_id"])
        self.assertEqual((self.data/"Catalog.xlsx").read_bytes(),before)

    def test_index_can_resolve_references_without_global_discovery(self):
        build_index(self.project,"demo",self.base/"index.json")
        self.config["tables"]={}
        self.config["knowledge"]={"index":"index.json"}
        self.write_profile()
        engine=Workflow(load_project(self.profile),"demo")
        self.assertIn("Groups.xlsx",engine.preview(self.request())["source_hashes"])

    def test_unapproved_mismatched_and_tampered_previews_are_rejected(self):
        preview=self.engine.preview(self.request())
        with self.assertRaises(ToolError):
            self.engine.apply(preview["preview_id"],"0"*32)
        second,approval=self.approved(self.request(Name={"before":"First","after":"Different"}))
        with self.assertRaises(ToolError):
            self.engine.apply(preview["preview_id"],approval["approval_id"])
        path=self.engine.directory/"previews"/(preview["preview_id"]+".json")
        payload=load_json(path);payload["changes"][0]["after"]="Tampered"
        path.write_text(json.dumps(payload),encoding="utf-8")
        with self.assertRaises(ToolError):
            self.engine.get_preview(preview["preview_id"])

    def test_source_and_project_cas(self):
        preview,approval=self.approved()
        source=self.data/"Catalog.xlsx"
        book=load_workbook(source);book["配置"]["C8"]="External";book.save(source)
        with self.assertRaises(ToolError):
            self.engine.apply(preview["preview_id"],approval["approval_id"])
        refreshed=self.engine.preview(self.request())
        approval=self.engine.approve(refreshed["preview_id"],"Reviewer","Confirm refreshed")
        self.config["description"]="New profile version"
        self.write_profile()
        with self.assertRaises(ToolError):
            self.engine.apply(refreshed["preview_id"],approval["approval_id"])

    def test_failed_readback_rolls_back_exact_source(self):
        preview,approval=self.approved()
        original=(self.data/"Catalog.xlsx").read_bytes()
        with patch.object(self.engine,"_validate_saved",side_effect=ToolError("test","Synthetic readback failure")):
            with self.assertRaises(ToolError):
                self.engine.apply(preview["preview_id"],approval["approval_id"])
        self.assertEqual((self.data/"Catalog.xlsx").read_bytes(),original)
        journals=list((self.engine.directory/"transactions").glob("*.json"))
        self.assertEqual(load_json(journals[0])["status"],"rolled_back")

    def test_external_edit_is_never_overwritten_during_recovery(self):
        preview,approval=self.approved()
        target=self.data/"Catalog.xlsx"
        external=b"An external editor owns this new image"
        def conflict(_):
            target.write_bytes(external)
            raise ToolError("test","External change after replace")
        with patch.object(self.engine,"_validate_saved",side_effect=conflict):
            with self.assertRaises(ToolError):
                self.engine.apply(preview["preview_id"],approval["approval_id"])
        self.assertEqual(target.read_bytes(),external)
        identifier=next((self.engine.directory/"transactions").glob("*.json")).stem
        self.assertEqual(self.engine.recover(identifier)["status"],"recovery_required")
        self.assertEqual(target.read_bytes(),external)

    def test_postwrite_dependency_change_rolls_back_target_only(self):
        preview,approval=self.approved()
        target=self.data/"Catalog.xlsx"
        before=target.read_bytes()
        original=self.engine._validate_saved
        def mutate(preview):
            result=original(preview)
            group=self.data/"Groups.xlsx"
            book=load_workbook(group);book["配置"]["C7"]="External dependency update";book.save(group)
            return result
        with patch.object(self.engine,"_validate_saved",side_effect=mutate):
            with self.assertRaises(ToolError):
                self.engine.apply(preview["preview_id"],approval["approval_id"])
        self.assertEqual(target.read_bytes(),before)
        self.assertEqual(load_workbook(self.data/"Groups.xlsx")["配置"]["C7"].value,"External dependency update")

    def test_recover_completed_journal_after_secondary_state_failure(self):
        preview,approval=self.approved()
        original=self.engine._save
        failed=[False]
        def save(folder,identifier,payload):
            if folder=="previews" and payload.get("status")=="completed" and not failed[0]:
                failed[0]=True
                raise OSError("Synthetic secondary state write failure")
            return original(folder,identifier,payload)
        with patch.object(self.engine,"_save",side_effect=save):
            with self.assertRaises(OSError):
                self.engine.apply(preview["preview_id"],approval["approval_id"])
        repeated=self.engine.apply(preview["preview_id"],approval["approval_id"])
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(repeated["status"],"completed")

    def test_scope_is_explicit(self):
        with self.assertRaises(ToolError):
            read_table(self.project,"demo","../outside.xlsx","配置")
        self.config["state_dir"]="data/state"
        self.write_profile()
        with self.assertRaises(ToolError):
            load_project(self.profile)

    def test_snapshot_race_cannot_bind_old_rows_to_a_new_file_hash(self):
        from config_toolkit.core import workflow as workflow_module
        original=workflow_module.read_workbook
        modified=[False]
        def racing(project,root_name,filename):
            result=original(project,root_name,filename)
            if filename=="Catalog.xlsx" and not modified[0]:
                modified[0]=True
                target=self.data/filename
                book=load_workbook(target);book["配置"]["C7"]="External race";book.save(target)
            return result
        with patch.object(workflow_module,"read_workbook",side_effect=racing):
            with self.assertRaises(ToolError):
                self.engine.preview(self.request())
        self.assertEqual(load_workbook(self.data/"Catalog.xlsx")["配置"]["C7"].value,"External race")
        self.assertEqual(self.engine.list_previews(),[])

    def test_merged_cells_and_ambiguous_json_are_rejected(self):
        target=self.data/"Catalog.xlsx"
        book=load_workbook(target);book["配置"].merge_cells("C7:D7");book.save(target)
        with self.assertRaises(ToolError):
            self.engine.preview(self.request())
        book=load_workbook(target);book["配置"].unmerge_cells("C7:D7");book["配置"]["D7"]="group_1";book["配置"]["E4"]="json";book.save(target)
        for value in ('{"a":1,"a":2}','{"a":NaN}'):
            with self.subTest(value=value),self.assertRaises(ToolError):
                self.engine.preview(self.request(Count={"before":1,"after":value}))

    def test_numeric_xml_does_not_round_large_integers_and_shared_indices_are_checked(self):
        from xml.etree import ElementTree as ET
        cell=ET.fromstring('<c xmlns="'+excel_module.MAIN+'"><v>9007199254740993</v></c>')
        self.assertEqual(excel_module._value(cell,[]),9007199254740993)
        invalid=ET.fromstring('<c xmlns="'+excel_module.MAIN+'" t="s"><v>-1</v></c>')
        with self.assertRaises(ToolError):
            excel_module._value(invalid,["Wrong fallback value"])


if __name__=="__main__":
    unittest.main()
