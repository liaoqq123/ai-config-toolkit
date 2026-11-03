"""Knowledge integrity checks using isolated, invented inputs only."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from config_toolkit.core.knowledge import GRAPH, build, query, read_note, validate


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / "config"
        self.config.mkdir()
        self.data = self.root / "data"
        self.data.mkdir()
        self.vault = self.root / "vault"
        self.project = self.config / "project.json"
        self.notes_file = self.config / "notes.json"
        self.project.write_text(json.dumps(dict(schema_version=1, project_id="isolated", data_roots={"sample":"../data"},
            state_dir="../state", knowledge={"vault":"../vault", "notes":"notes.json"})), encoding="utf-8")
        self.alpha = '---\nid: alpha\naliases: [first]\n---\n\n# Alpha\n\nUnique-alpha content.\n\n###### Deep heading\n\nDeep content.\n'
        self.payload = dict(schema_version=1, notes=[dict(id="guide:alpha",kind="guide",title="Alpha",group="Guides",aliases=["first"],content=self.alpha),
            dict(id="guide:beta",kind="guide",title="Beta",group="Guides",content="# Beta\n\nUnique-beta content.\n"),
            dict(id="schema:local",kind="schema",title="Local schema",group="Schemas",content="# Local schema\n\nStructure only.\n",
                 sources=[dict(root="sample",file="Catalog.xlsx",sheet="Main",table="Catalog")])], edges=[])
        self.save()

    def save(self):
        self.notes_file.write_text(json.dumps(self.payload, ensure_ascii=False), encoding="utf-8")

    def graph(self):
        return json.loads((self.vault / GRAPH).read_text(encoding="utf-8"))

    def test_complete_content_and_six_level_headings_round_trip(self):
        result = build(self.project)
        self.assertEqual(result["documents"], 3)
        self.assertEqual(read_note(self.project,"guide:alpha")["content"], self.alpha)
        self.assertNotIn("Unique-beta", read_note(self.project,"guide:alpha")["content"])
        self.assertEqual(validate(self.project)["status"], "passed")
        build(self.project)
        self.assertEqual(read_note(self.project,"guide:alpha")["content"], self.alpha)

    def test_precise_search_paths_and_single_section_reads(self):
        build(self.project)
        result = query(self.project,"Unique-alpha",limit=10)
        self.assertEqual([match["id"] for match in result["matches"]], ["guide:alpha"])
        match = result["matches"][0]
        self.assertTrue((self.vault/match["path"]).is_file())
        self.assertNotIn("#",match["path"])
        self.assertIn("#"+match["section"],match["wikilink"])
        source = query(self.project,"Local schema",limit=1)["read_targets"]
        self.assertEqual(source[0]["table"],"Catalog")

    def test_rebuild_stable_and_manual_edits_inside_outside_chapters_survive(self):
        build(self.project)
        second = build(self.project)
        self.assertEqual(second["files_changed"],0)
        node = next(node for node in self.graph()["nodes"] if node["id"]=="guide:alpha")
        path = self.vault/node["path"]
        text = path.read_text(encoding="utf-8").replace("Unique-alpha content.","Unique-alpha content. User-added detail.")
        text += "\nCollection-level user appendix.\n"
        path.write_text(text,encoding="utf-8")
        for _ in range(2):
            build(self.project)
            self.assertIn("User-added detail",read_note(self.project,"guide:alpha")["content"])
            self.assertEqual(path.read_text(encoding="utf-8").count("Collection-level user appendix."),1)

    def test_authoring_source_updates_without_erasing_changed_generated_sections(self):
        build(self.project)
        self.payload["notes"][1]["content"] += "\nNew authoritative input.\n"
        self.save()
        self.assertEqual(query(self.project,"Beta")["status"],"stale_knowledge")
        build(self.project)
        self.assertIn("New authoritative input",read_note(self.project,"guide:beta")["content"])

    def test_id_links_and_nested_heading_links_rewrite_precisely(self):
        self.payload["notes"][1]["content"] += "\nSee [[guide:alpha|custom label]] and [[guide:alpha#Deep heading|detail]].\n"
        self.save()
        build(self.project)
        self.assertEqual(validate(self.project)["status"],"passed")
        text = read_note(self.project,"guide:beta")["content"]
        self.assertIn("#Alpha|custom label]]",text)
        self.assertIn("#Alpha / Deep heading|detail]]",text)
        self.assertEqual(build(self.project)["files_changed"],0)

    def test_invalid_collection_or_section_cannot_read_neighboring_content(self):
        build(self.project)
        graph = self.graph()
        node = next(node for node in graph["nodes"] if node["id"]=="guide:alpha")
        node["section"]="Missing heading"
        (self.vault/GRAPH).write_text(json.dumps(graph),encoding="utf-8")
        self.assertEqual(validate(self.project)["status"],"failed")
        with self.assertRaises(ValueError):
            read_note(self.project,"guide:alpha")
        with self.assertRaises(ValueError):
            read_note(self.project,"missing")

    def test_index_import_has_no_business_rows_and_retains_root_identity(self):
        index = dict(schema_version=1,kind="table_index",project_id="isolated",roots={"sample":str(self.data.resolve())},tables=[
            dict(root="sample",file="Catalog.xlsx",sheet="Main",table="Catalog",fields={"Id":1,"label":2},types={"Id":"string","label":"string"},
                 constraints={"Id":"PRIMARY","label":"FOREIGN:Labels.Id"},primary="Id",source_sha256="a"*64,schema_sha256="b"*64)])
        (self.config/"index.json").write_text(json.dumps(index),encoding="utf-8")
        project=json.loads(self.project.read_text(encoding="utf-8"));project["knowledge"]["index"]="index.json"
        self.project.write_text(json.dumps(project),encoding="utf-8")
        build(self.project)
        result=query(self.project,"Catalog",limit=1)
        self.assertEqual(result["read_targets"][0]["root"],"sample")
        self.assertEqual(result["read_targets"][0]["file"],"Catalog.xlsx")
        self.assertEqual(result["business_records_read"],0)
        unknown=query(self.project,"Labels",limit=1)
        self.assertEqual(unknown["read_targets"],[])
        self.assertEqual(validate(self.project)["status"],"passed")

    def test_old_collection_archives_outside_vault_on_group_change(self):
        build(self.project)
        self.payload["notes"][0]["group"]="Moved"
        self.payload["notes"][1]["group"]="Moved"
        self.save()
        build(self.project)
        self.assertFalse((self.vault/"10-知识/Guides.md").exists())
        archives=list((self.root/"state/knowledge-archive").rglob("*.md"))
        self.assertTrue(archives)
        self.assertTrue(all(not path.resolve().is_relative_to(self.vault.resolve()) for path in archives))
        self.assertEqual(validate(self.project)["status"],"passed")

    def test_unsafe_sources_and_cross_project_indexes_are_rejected(self):
        self.payload["notes"][2]["sources"][0]["file"]="../outside.xlsx"
        self.save()
        with self.assertRaises(ValueError):
            build(self.project)

    def test_archive_cross_drive_fallback_keeps_verified_original_content(self):
        build(self.project)
        source=self.vault/"10-知识/Guides.md"
        original=source.read_text(encoding="utf-8")
        self.payload["notes"][0]["group"]="Moved"
        self.payload["notes"][1]["group"]="Moved"
        self.save()
        with patch.object(Path,"rename",side_effect=OSError("different drive")):
            build(self.project)
        destination=self.root/"state/knowledge-archive/10-知识/Guides.md"
        self.assertEqual(destination.read_text(encoding="utf-8"),original)
        self.assertFalse(source.exists())

    def test_source_fingerprints_detect_change_without_parsing_business_records(self):
        source=self.data/"Catalog.xlsx"
        source.write_bytes(b"opaque fingerprint fixture")
        fingerprint=hashlib.sha256(source.read_bytes()).hexdigest()
        index=dict(schema_version=1,kind="table_index",project_id="isolated",roots={"sample":str(self.data.resolve())},tables=[
            dict(root="sample",file="Catalog.xlsx",sheet="Main",table="Catalog",fields={"Id":1},types={"Id":"string"},
                 constraints={"Id":"PRIMARY"},primary="Id",source_sha256=fingerprint,schema_sha256="b"*64)])
        (self.config/"index.json").write_text(json.dumps(index),encoding="utf-8")
        project=json.loads(self.project.read_text(encoding="utf-8"));project["knowledge"]["index"]="index.json"
        self.project.write_text(json.dumps(project),encoding="utf-8")
        build(self.project)
        self.assertEqual(query(self.project,"Catalog",limit=1)["status"],"located")
        source.write_bytes(b"changed fixture")
        result=query(self.project,"Catalog",limit=1)
        self.assertEqual(result["status"],"stale_sources")
        self.assertEqual(result["business_records_read"],0)
        self.assertEqual(query(self.project,"Catalog",limit=1,check_sources=False)["status"],"located")


if __name__=="__main__":
    unittest.main()
