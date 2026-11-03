"""Build, search and read compact knowledge collections from explicit inputs.

This module consumes notes and schema indexes. It does not inspect business
records and it never grants permission to change a workbook.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from config_toolkit.core.bundle import (
    consolidate, digest, extract_note, headings, read_existing, section_text,
)

GRAPH = ".knowledge/graph.json"
HOME = "00-入口/首页.md"
CANVAS = "00-入口/结构图.canvas"
_IDENTIFIER = re.compile(r"^[^<>\r\n]+$")


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _hash(path):
    content = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            content.update(block)
    return content.hexdigest()


def _write(path, text):
    path = Path(path)
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".knowledge-", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def _vault_path(vault, relative):
    target = (vault / relative).resolve()
    if not target.is_relative_to(vault):
        raise ValueError("知识库路径指向配置仓库之外：" + relative)
    return target


def _settings(project_path):
    """Accept a project path or the shared Project object's public interface."""
    if hasattr(project_path, "config") and hasattr(project_path, "path"):
        path, project = Path(project_path.path).resolve(), project_path.config
    else:
        path = Path(project_path).resolve()
        project = _json(path)
    if project.get("schema_version") != 1 or not isinstance(project.get("project_id"), str):
        raise ValueError("项目配置需要 schema_version=1 和非空 project_id。")
    if not project["project_id"].strip():
        raise ValueError("project_id 不能为空。")
    config = project.get("knowledge") or {}
    if not isinstance(config, dict) or not config.get("vault"):
        raise ValueError("请在 knowledge.vault 显式配置知识库目录。")
    resolve = lambda value: (path.parent / value).resolve()
    vault = resolve(config["vault"])
    state = resolve(project.get("state_dir", "../.work/" + project["project_id"]))
    if state == vault or state.is_relative_to(vault):
        raise ValueError("state_dir 必须位于知识库之外，避免归档成为可见笔记。")
    roots = {name: resolve(value) for name, value in project.get("data_roots", {}).items()}
    if any(vault == root or vault.is_relative_to(root) or state == root or state.is_relative_to(root)
           for root in roots.values()):
        raise ValueError("知识库与运行状态必须位于表格数据目录之外。")
    return path, project, config, vault, state, roots


def _location(source, roots):
    if not isinstance(source, dict) or source.get("root") not in roots:
        raise ValueError("来源需要匹配 data_roots 中的 root 名称。")
    if any(not isinstance(source.get(key), str) or not source[key] for key in ("file", "sheet", "table")):
        raise ValueError("来源必须给出 file、sheet 和 table。")
    root = roots[source["root"]]
    file = Path(source["file"])
    resolved = (root / file).resolve()
    if file.is_absolute() or not resolved.is_relative_to(root) or resolved == root:
        raise ValueError("来源 file 必须是数据根内的相对文件路径。")
    return {key: source[key] for key in ("root", "file", "sheet", "table")}


def _add(nodes, contents, identifier, title, kind, group, content, **extra):
    if (not isinstance(identifier, str) or not identifier or not _IDENTIFIER.fullmatch(identifier)
            or "--" in identifier or identifier.startswith("collection:")):
        raise ValueError("笔记 id 无效或使用了保留的 collection: 前缀。")
    if identifier in nodes:
        raise ValueError("笔记 id 重复：" + identifier)
    if not all(isinstance(value, str) and value.strip() for value in (title, kind, group, content)):
        raise ValueError("笔记需要非空 title、kind、group、content。")
    if not re.search(r"^#{1,6}\s+", content, re.M):
        raise ValueError("笔记正文至少需要一个 Markdown 标题，以便稳定定位章节：" + identifier)
    path = "notes/" + digest(identifier)[:20] + ".md"
    node = dict(id=identifier, title=title, kind=kind, group=group, path=path,
                aliases=list(dict.fromkeys([title, *extra.pop("aliases", [])])),
                generated=bool(extra.pop("generated", True)), **extra)
    nodes[identifier] = node
    contents[path] = content.replace("\r\n", "\n")
    return node


def _note_body(title, lines):
    return "# " + title + "\n\n" + "\n".join(lines).rstrip() + "\n"


def _load_inputs(path, project, config, roots):
    nodes, contents, edges, input_paths = {}, {}, [], [path]
    note_path = (path.parent / config["notes"]).resolve() if config.get("notes") else None
    if note_path:
        payload = _json(note_path)
        input_paths.append(note_path)
        if payload.get("schema_version") != 1 or not isinstance(payload.get("notes", []), list):
            raise ValueError("知识输入需要 schema_version=1 和 notes 列表。")
        for item in payload.get("notes", []):
            if not isinstance(item, dict):
                raise ValueError("notes 每项必须是对象。")
            if ("content" in item) == ("source" in item):
                raise ValueError("每篇笔记需要且只能提供 content 或 source。")
            if "source" in item:
                source_path = (note_path.parent / item["source"]).resolve()
                if source_path.suffix.lower() != ".md":
                    raise ValueError("笔记 source 只允许 Markdown 文件。")
                text = source_path.read_text(encoding="utf-8")
                input_paths.append(source_path)
            else:
                text = item["content"]
            aliases = item.get("aliases", [])
            if not isinstance(aliases, list) or any(not isinstance(alias, str) for alias in aliases):
                raise ValueError("aliases 必须是字符串列表。")
            locations = [_location(source, roots) for source in item.get("sources", [])]
            _add(nodes, contents, item["id"], item["title"], item.get("kind", "note"),
                 item.get("group") or (config.get("kind_groups", {}).get(item.get("kind"), "Notes")),
                 text, aliases=aliases, generated=item.get("generated", True), sources=locations)
        edges.extend(payload.get("edges", []))

    index_names = config.get("indexes", config.get("index", []))
    if isinstance(index_names, str):
        index_names = [index_names]
    if not isinstance(index_names, list):
        raise ValueError("knowledge.indexes 必须是路径列表。")
    tables, source_nodes = defaultdict(list), []
    seen_sources = set()
    for name in index_names:
        index_path = (path.parent / name).resolve()
        index = _json(index_path)
        input_paths.append(index_path)
        if index.get("schema_version") != 1 or index.get("kind") != "table_index" or index.get("project_id") != project["project_id"]:
            raise ValueError("结构索引类型或 project_id 与当前项目不一致：" + str(index_path))
        for root_name, directory in index.get("roots", {}).items():
            if root_name not in roots or Path(directory).resolve() != roots[root_name]:
                raise ValueError("结构索引的数据根与当前配置不一致：" + root_name)
        for row in index.get("tables", []):
            location = _location(row, roots)
            identity = tuple(location[key] for key in ("root", "file", "sheet", "table"))
            if identity in seen_sources:
                raise ValueError("结构索引来源重复：" + repr(identity))
            seen_sources.add(identity)
            identifier = "source:" + digest(json.dumps(identity, ensure_ascii=False))[:20]
            title = location["root"] + " • " + location["file"] + " / " + location["sheet"]
            lines = ["逻辑表：`" + location["table"] + "`。仅保存结构证据，不包含业务记录。", "",
                     "来源指纹：`" + str(row.get("source_sha256", "")) + "`", "",
                     "主键：`" + str(row.get("primary", "")) + "`", "",
                     "## 字段", "", "|字段|列|类型|约束|", "|---|---|---|---|"]
            escape = lambda value: str(value or "").replace("|", "\\|").replace("\n", " ")
            for field, column in row.get("fields", {}).items():
                if type(column) is not int or column < 1:
                    raise ValueError("索引 fields 的列号必须是一基正整数。")
                lines.append("|" + "|".join(escape(value) for value in
                             (field, column, row.get("types", {}).get(field, ""), row.get("constraints", {}).get(field, ""))) + "|")
            group_template = config.get("source_group_template", "{root} tables")
            group = group_template.format(root=location["root"])
            node = _add(nodes, contents, identifier, title, "physical_source", group, _note_body(title, lines),
                        aliases=[location["file"], location["sheet"], location["table"]],
                        sources=[location], source_sha256=row.get("source_sha256"), schema_sha256=row.get("schema_sha256"))
            tables[location["table"]].append(node)
            source_nodes.append((node, row))
    target_tables = set(tables)
    for _, row in source_nodes:
        for constraint in row.get("constraints", {}).values():
            if isinstance(constraint, str) and constraint.upper().startswith("FOREIGN:"):
                target_tables.add(constraint.split(":", 1)[1].split(".", 1)[0])
    logical_ids = {}
    for table in sorted(target_tables):
        identifier = "table:" + digest(table)[:20]
        logical_ids[table] = identifier
        locations = [node["sources"][0] for node in tables[table]]
        lines = ["实际逻辑表：`" + table + "`。", "", "## 物理来源", ""]
        lines.extend("- [[" + node["id"] + "|" + node["title"] + "]]" for node in tables[table])
        if not locations:
            lines.append("当前没有来源。需要补齐真实索引，不能假设记录或字段已经存在。")
        _add(nodes, contents, identifier, table, "logical_table", config.get("table_group", "Table schemas"),
             _note_body(table, lines), aliases=[table], sources=locations, indexed=bool(locations))
        for source in tables[table]:
            edges.append(dict(source=identifier, target=source["id"], evidence="indexed_source"))
    for source, row in source_nodes:
        for field, constraint in row.get("constraints", {}).items():
            if isinstance(constraint, str) and constraint.upper().startswith("FOREIGN:"):
                target = constraint.split(":", 1)[1].split(".", 1)[0]
                edges.append(dict(source=source["id"], target=logical_ids[target], evidence="header_foreign", field=field))
    for edge in edges:
        if (not isinstance(edge, dict) or edge.get("source") not in nodes or edge.get("target") not in nodes
                or not isinstance(edge.get("evidence"), str) or not edge["evidence"]):
            raise ValueError("关系必须引用存在的 source、target，并显式给出 evidence。")
    return nodes, contents, edges, sorted(set(input_paths))


def build(project_path):
    path, project, config, vault, state, roots = _settings(project_path)
    nodes, contents, edges, inputs = _load_inputs(path, project, config, roots)
    old = _json(vault / GRAPH) if (vault / GRAPH).is_file() else {}
    if old and old.get("project_id") != project["project_id"]:
        raise ValueError("目标知识库已经属于另一个 project_id。")
    old_nodes = {node["id"]: node for node in old.get("nodes", [])}
    legacy = {node["id"]: (node["path"], node["title"], node["id"]) for node in nodes.values()}
    documents, own, rewrite, snapshots = consolidate(nodes, contents, old_nodes, vault, legacy)
    for filename, expected in snapshots.items():
        if _hash(filename) != expected:
            raise ValueError("合编期间笔记被外部修改，请重新构建：" + filename)
    collections = sorted((node for node in nodes.values() if node["kind"] == "collection"), key=lambda node: node["title"])
    navigation = "<!-- tool-navigation -->\n## 知识目录\n\n" + "\n".join(
        "- [[" + node["path"].removesuffix(".md") + "|" + node["title"] + "]]" for node in collections) + "\n<!-- /tool-navigation -->"
    home = "# " + project["project_id"] + " 知识入口\n\n" + navigation + "\n\n"
    home += "先 query 定位，再 read 笔记 ID 精确读取章节。知识仅记录结构与证据，业务实值必须定向读表，写入仍需单独确认。\n"
    if (vault / HOME).is_file():
        existing = (vault / HOME).read_text(encoding="utf-8")
        if "<!-- tool-navigation -->" in existing and "<!-- /tool-navigation -->" in existing:
            home = re.sub(r"<!-- tool-navigation -->.*?<!-- /tool-navigation -->", lambda _: navigation, existing, flags=re.S)
        else:
            home = existing.rstrip() + "\n\n" + navigation + "\n"
    documents[HOME] = rewrite(home)
    canvas_nodes = [{"id": "home", "type": "file", "file": HOME, "x": 0, "y": 0, "width": 340, "height": 180}]
    for number, node in enumerate(collections):
        canvas_nodes.append(dict(id=node["id"], type="file", file=node["path"], x=number % 4 * 390,
                                 y=260 + number // 4 * 260, width=340, height=200))
    canvas_edges = [dict(id="home-" + node["id"], fromNode="home", toNode=node["id"], fromSide="bottom", toSide="top") for node in collections]
    documents[CANVAS] = json.dumps(dict(nodes=canvas_nodes, edges=canvas_edges), ensure_ascii=False, indent=2) + "\n"
    for filename in [*documents, GRAPH]:
        _vault_path(vault, filename)
    written = sum(_write(_vault_path(vault, filename), text) for filename, text in documents.items())
    retired = []
    live = set(documents)
    visited = set()
    for previous in old_nodes.values():
        filename = previous["path"]
        if filename in live or filename in visited:
            continue
        visited.add(filename)
        source = (vault / filename).resolve()
        expected = snapshots.get(str(source), previous.get("content_sha256"))
        if source.is_relative_to(vault) and source.is_file() and _hash(source) == expected:
            destination = state / "knowledge-archive" / filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            number = 1
            while destination.exists():
                destination = destination.with_name(Path(filename).stem + "-" + str(number) + Path(filename).suffix)
                number += 1
            try:
                source.rename(destination)
            except OSError:
                # Configured state and vault can be on different drives.
                # Verify both copies before retiring the original snapshot.
                shutil.copy2(source, destination)
                if _hash(destination) != expected or _hash(source) != expected:
                    raise ValueError("归档期间原笔记发生变化，保留原页：" + filename)
                source.unlink()
            retired.append(str(destination))
    for source in vault.rglob("*.md"):
        relative = source.relative_to(vault)
        if any(part.startswith(".") for part in relative.parts) or relative.as_posix() in documents:
            continue
        written += _write(source, rewrite(source.read_text(encoding="utf-8")))
    for directory in sorted((p for p in vault.rglob("*") if p.is_dir() and not any(part.startswith(".") for part in p.relative_to(vault).parts)),
                            key=lambda p: len(p.parts), reverse=True):
        if not any(directory.iterdir()):
            directory.rmdir()
    content_hashes = {filename:_hash(vault/filename) for filename in {node["path"] for node in nodes.values()}}
    for node in nodes.values():
        node["search_text"] = node["title"] + " " + " ".join(node["aliases"]) + " " + own.get(node["id"], "")
        node["content_sha256"] = content_hashes[node["path"]]
    graph = dict(schema_version=1, kind="knowledge_graph", project_id=project["project_id"],
                 nodes=sorted(nodes.values(), key=lambda n:n["id"]), edges=edges, home=HOME, canvas=CANVAS,
                 inputs=[dict(path=str(source), sha256=_hash(source)) for source in inputs], business_records_cached=False)
    _write(vault / GRAPH, json.dumps(graph, ensure_ascii=False, indent=2) + "\n")
    return dict(status="built", vault=str(vault), notes=len(nodes), documents=len([name for name in documents if name.endswith(".md")]),
                files_changed=written, archived_files=retired, business_records_read=0, business_writes=0)


def _graph(project_path):
    path, project, config, vault, state, roots = _settings(project_path)
    graph = _json(vault / GRAPH)
    if graph.get("schema_version") != 1 or graph.get("kind") != "knowledge_graph" or graph.get("project_id") != project["project_id"]:
        raise ValueError("知识索引无效或不属于当前项目，请先 build。")
    return vault, roots, graph


def read_note(project_path, identifier):
    vault, roots, graph = _graph(project_path)
    selected = [node for node in graph["nodes"] if node["id"] == identifier]
    if not selected:
        selected = [node for node in graph["nodes"] if identifier in node.get("aliases", [])]
    if len(selected) != 1:
        raise ValueError("笔记不存在或名称不唯一，请使用 query 返回的 id。")
    node = selected[0]
    return dict(status="read", id=node["id"], path=node["path"], section=node.get("section"),
                content=read_existing(node, vault, {}), business_records_read=0, business_writes=0)


def query(project_path, text, *, limit=12, source_limit=20, source_offset=0, check_sources=True):
    vault, roots, graph = _graph(project_path)
    if not isinstance(text, str) or not text.strip() or not 1 <= limit <= 100 or not 1 <= source_limit <= 500 or source_offset < 0:
        raise ValueError("请提供查询词；limit 为1～100，source_limit 为1～500，source_offset 非负。")
    exact = text.strip().casefold()
    words = exact.split()
    if len(words) == 1 and not any(exact in node["search_text"].casefold() for node in graph["nodes"]):
        candidates = {alias.casefold() for node in graph["nodes"] for alias in node.get("aliases", []) if len(alias) >= 2 and alias.casefold() in exact}
        if candidates:
            words = sorted(candidates, key=lambda word:(-len(word),word))[:8]
    scored = []
    for node in graph["nodes"]:
        names = [name.casefold() for name in node.get("aliases", [])]
        searchable = node["search_text"].casefold()
        if not all(word in searchable for word in words):
            continue
        score = (200 if exact in names else 0) + sum(30 if any(word in name for name in names) else 2 for word in words)
        scored.append((score,node))
    scored.sort(key=lambda pair:(-pair[0],pair[1]["id"]))
    matches = []
    for score,node in scored[:limit]:
        target = node["path"].removesuffix(".md") + ("#"+node["section"] if node.get("section") else "")
        matches.append(dict(id=node["id"],kind=node["kind"],title=node["title"],path=node["path"],absolute_path=str(vault/node["path"]),
                            section=node.get("section"),wikilink="[["+target+"|"+node["title"]+"]]",score=score))
    selected = {match["id"] for match in matches}
    direct_matches = set(selected)
    selected.update(edge["target"] for edge in graph["edges"] if edge["source"] in direct_matches and edge["evidence"] not in ("header_foreign",))
    locations, seen = [],set()
    for node in graph["nodes"]:
        if node["id"] not in selected:
            continue
        for source in node.get("sources",[]):
            identity = tuple(source[key] for key in ("root","file","sheet","table"))
            if identity not in seen:
                seen.add(identity); locations.append(dict(source,access="read"))
    total = len(locations)
    page = locations[source_offset:source_offset+source_limit]
    expected_sources = {tuple(source[key] for key in ("root","file","sheet","table")):node.get("source_sha256")
                        for node in graph["nodes"] if node["kind"]=="physical_source" for source in node.get("sources",[])}
    fingerprints, freshness = {},[]
    for source in page:
        expected=expected_sources.get(tuple(source[key] for key in ("root","file","sheet","table")))
        if not check_sources:
            state="not_checked"
        elif not expected:
            state="not_indexed"
        else:
            filename=(roots[source["root"]]/source["file"]).resolve()
            if filename not in fingerprints:
                try:
                    fingerprints[filename]=_hash(filename)
                except OSError:
                    fingerprints[filename]=None
            actual=fingerprints[filename]
            state="missing" if actual is None else "unchanged" if actual==expected else "changed"
        freshness.append(dict(source,status=state))
    changed = [item["path"] for item in graph.get("inputs",[]) if not Path(item["path"]).is_file() or _hash(item["path"]) != item["sha256"]]
    stale_sources=any(item["status"] in ("missing","changed") for item in freshness)
    return dict(status="stale_knowledge" if changed else "stale_sources" if stale_sources else "located" if matches else "not_found",query=text,matches=matches,
                read_targets=page,read_target_count_total=total,
                source_offset=source_offset,source_limit=source_limit,read_targets_truncated=source_offset>0 or source_offset+source_limit<total,
                relationships=[edge for edge in graph["edges"] if edge["source"] in selected][:100],changed_inputs=changed,
                freshness=dict(sources=freshness,scope="returned_source_page_only",checked_workbooks=len(fingerprints)),
                limits=["知识只保存结构和证据；当前业务值须定向读取。","来源候选不是写入许可。"],business_records_read=0,business_writes=0)


def validate(project_path):
    vault, roots, graph = _graph(project_path)
    errors, cache, identifiers, owned_paths, sections = [],{},set(),set(),set()
    node_map = {node["id"]:node for node in graph["nodes"]}
    for node in graph["nodes"]:
        if node["id"] in identifiers:
            errors.append(dict(code="duplicate_identity",id=node["id"]))
        identifiers.add(node["id"])
        path = (vault/node["path"]).resolve()
        if not path.is_relative_to(vault) or not path.is_file():
            errors.append(dict(code="missing_or_unsafe_note",id=node["id"])); continue
        if node.get("collection_id"):
            owner = node_map.get(node["collection_id"])
            if not owner or owner["kind"]!="collection" or owner["path"]!=node["path"] or owner.get("collection_id"):
                errors.append(dict(code="invalid_collection",id=node["id"]))
            identity = (node["path"].casefold(),node.get("section","").casefold())
            if identity in sections:
                errors.append(dict(code="duplicate_section",id=node["id"]))
            sections.add(identity)
            try:
                read_existing(node,vault,cache)
            except (ValueError,KeyError) as exc:
                errors.append(dict(code="invalid_section",id=node["id"],error=str(exc)))
        else:
            if node["path"].casefold() in owned_paths:
                errors.append(dict(code="duplicate_path",path=node["path"]))
            owned_paths.add(node["path"].casefold())
        for source in node.get("sources",[]):
            try:
                _location(source,roots)
            except ValueError as exc:
                errors.append(dict(code="unsafe_source",id=node["id"],error=str(exc)))
    files = [p for p in vault.rglob("*.md") if not any(part.startswith(".") for part in p.relative_to(vault).parts)]
    targets = {p.relative_to(vault).as_posix().removesuffix(".md"):p for p in files}
    names = defaultdict(list)
    for target in targets:
        names[Path(target).name].append(target)
    titles = {target:{title for _,title in headings(path.read_text(encoding="utf-8"))} for target,path in targets.items()}
    canvases = [p for p in vault.rglob("*.canvas") if not any(part.startswith(".") for part in p.relative_to(vault).parts)]
    canvas_names = {p.relative_to(vault).as_posix() for p in canvases}
    wikilinks = 0
    for note in files:
        for match in re.finditer(r"(?<!\\)\[\[([^\]]+)\]\]",note.read_text(encoding="utf-8")):
            wikilinks += 1
            target,separator,section = match[1].split("|",1)[0].partition("#")
            if target in canvas_names:
                continue
            target = target.removesuffix(".md")
            if not target:
                target = note.relative_to(vault).as_posix().removesuffix(".md")
            if target not in targets and len(names.get(target,[]))==1:
                target = names[target][0]
            if target not in targets:
                errors.append(dict(code="unresolved_wikilink",target=target))
            elif separator and section not in titles[target]:
                errors.append(dict(code="unresolved_section",target=target+"#"+section))
    canvas_nodes = 0
    for path in canvases:
        canvas = _json(path)
        ids = [node["id"] for node in canvas.get("nodes",[])]
        canvas_nodes += len(ids)
        if len(ids)!=len(set(ids)):
            errors.append(dict(code="duplicate_canvas_node",path=str(path)))
        for node in canvas.get("nodes",[]):
            if node.get("type")=="file":
                file = (vault/node.get("file","")).resolve()
                if not file.is_relative_to(vault) or not file.is_file():
                    errors.append(dict(code="missing_canvas_file",file=node.get("file")))
        for edge in canvas.get("edges",[]):
            if edge.get("fromNode") not in ids or edge.get("toNode") not in ids:
                errors.append(dict(code="dangling_canvas_edge",id=edge.get("id")))
    for edge in graph["edges"]:
        if edge["source"] not in identifiers or edge["target"] not in identifiers:
            errors.append(dict(code="dangling_graph_edge",edge=edge))
    return dict(status="passed" if not errors else "failed",errors=errors,note_count=len(files),node_count=len(graph["nodes"]),
                wikilink_count=wikilinks,canvas_nodes=canvas_nodes,business_records_read=0,business_writes=0)
