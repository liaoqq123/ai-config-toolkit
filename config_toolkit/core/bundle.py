"""Lossless, project-independent Markdown collections with precise chapter reads."""
from collections import defaultdict
import hashlib
import re
from pathlib import Path


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_note(text):
    match = re.match(r"\A(---\n.*?\n---)(\n+)(.*)\Z", text, re.S)
    return match.groups() if match else ("", "", text)


def section_text(text, identifier):
    start = "<!-- tool-node:" + identifier + " -->\n"
    end = "<!-- tool-end:" + identifier + " -->"
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError("章节标记缺失或重复：" + identifier)
    left = text.index(start) + len(start)
    right = text.index(end, left)
    return text[left:right]


def headings(text):
    fenced = None
    for line in text.splitlines():
        fence = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence:
            if fenced is None:
                fenced = fence[1][0]
            elif fence[1][0] == fenced:
                fenced = None
        elif fenced is None:
            match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
            if match:
                yield len(match[1]), match[2]


def transform_headings(text, section, original_heading, reverse=False, six_level_headings=()):
    result, fenced, first = [], None, True
    for line in text.splitlines(keepends=True):
        fence = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence:
            if fenced is None:
                fenced = fence[1][0]
            elif fence[1][0] == fenced:
                fenced = None
        match = None if fenced else re.match(r"^(#{1,6})\s+(.+?)(\n?)$", line)
        if match:
            level, title, newline = len(match[1]), match[2], match[3]
            if reverse:
                if first and title == section:
                    title = original_heading
                elif title.startswith(section + " / "):
                    title = title[len(section) + 3:]
                level = 6 if level == 6 and title in six_level_headings else max(1, level - 1)
            else:
                title = section if first else section + " / " + title
                level = min(6, level + 1)
            line = "#" * level + " " + title + newline
            first = False
        result.append(line)
    return "".join(result)


def extract_note(node, text):
    if not node.get("collection_id"):
        return text
    section = section_text(text, node["id"])
    if node["section"] not in {title for _, title in headings(section)}:
        raise ValueError("章节标题缺失：" + node["id"])
    marker = "<!-- tool-body:" + node["id"] + " -->\n"
    if section.count(marker) != 1:
        raise ValueError("章节正文边界损坏：" + node["id"])
    metadata, body = section.split(marker, 1)
    front = re.search(r"```yaml\n(.*?)\n```", metadata, re.S)
    front = front[1] if front else ""
    if not node.get("body_ends_newline", True) and body.endswith("\n"):
        body = body[:-1]
    body = transform_headings(body, node["section"], node["original_heading"], reverse=True,
                              six_level_headings=node.get("six_level_headings", []))
    return front + node.get("note_separator", "\n\n") + body if front else body


def read_existing(node, vault, cache):
    path = (Path(vault) / node["path"]).resolve()
    if not path.is_relative_to(Path(vault).resolve()):
        raise ValueError("笔记路径超出知识库。")
    if path not in cache:
        raw = path.read_bytes()
        cache[path] = raw.decode("utf-8").replace("\r\n", "\n")
        cache[(path, "sha256")] = hashlib.sha256(raw).hexdigest()
    return extract_note(node, cache[path])


def bucket(node):
    return node.get("group") or "Notes"


def safe_group(value):
    result = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value)).strip(" .")
    return result or "Notes"


def consolidate(nodes, contents, old_nodes, vault, legacy_redirects=None):
    """Return visible collection documents and each node's complete own note."""
    cache, own = {}, {}
    for node in list(nodes.values()):
        text = contents[node["path"]]
        previous = old_nodes.get(node["id"])
        if previous and (Path(vault) / previous["path"]).is_file():
            existing = read_existing(previous, vault, cache)
            if previous.get("collection_id"):
                modified = digest(section_text(cache[(Path(vault) / previous["path"]).resolve()], node["id"])) != previous.get("section_sha256")
            else:
                modified = cache[((Path(vault) / previous["path"]).resolve(), "sha256")] != previous.get("content_sha256")
            if not node["generated"] or modified or previous.get("preserve_edited_note"):
                text = existing
                if node["generated"]:
                    node["preserve_edited_note"] = True
        own[node["id"]] = text

    groups = defaultdict(list)
    for node in list(nodes.values()):
        groups[bucket(node)].append(node)
    redirects, used = {}, defaultdict(set)
    group_names = {}
    occupied = set()
    for name in sorted(groups):
        candidate = safe_group(name)
        if candidate.casefold() in occupied:
            candidate += "-" + digest(name)[:8]
        occupied.add(candidate.casefold())
        group_names[name] = candidate
    for group, members in sorted(groups.items()):
        path = "10-知识/" + group_names[group] + ".md"
        owner_id = "collection:" + group
        for node in sorted(members, key=lambda n: n["id"]):
            previous_path = node["path"]
            front, separator, body = split_note(own[node["id"]])
            original_heading = next((title for _, title in headings(body)), node["title"])
            section = original_heading.replace("#", "＃").replace("[", "［").replace("]", "］").replace("|", "｜")
            if section.casefold() in used[path]:
                section += "（" + digest(node["id"])[:8] + "）"
            used[path].add(section.casefold())
            node.update(path=path, collection_id=owner_id, section=section,
                        original_heading=original_heading, note_separator=separator,
                        body_ends_newline=body.endswith("\n"))
            destination = (path, section, node["title"], node["id"])
            redirects[previous_path.removesuffix(".md")] = destination
            previous = old_nodes.get(node["id"])
            if previous and not previous.get("collection_id"):
                redirects[previous["path"].removesuffix(".md")] = destination
            elif previous:
                redirects[previous["path"].removesuffix(".md") + "#" + previous["section"]] = destination
            for _, title in headings(body):
                if title != original_heading:
                    redirects[previous_path.removesuffix(".md") + "#" + title] = (path, section + " / " + title, node["title"], node["id"])
        nodes[owner_id] = dict(id=owner_id, kind="collection", title=group, path=path,
                               aliases=[group], generated=True)

    for legacy, value in (legacy_redirects or {}).items():
        identifier = value[2]
        if identifier in nodes and nodes[identifier].get("collection_id"):
            node = nodes[identifier]
            redirects[legacy] = (node["path"], node["section"], node["title"], identifier)
    basename_targets = defaultdict(list)
    for target, destination in list(redirects.items()):
        if "#" not in target:
            basename_targets[Path(target).name].append(destination)
    for name, destinations in basename_targets.items():
        distinct = {tuple(item) for item in destinations}
        if len(distinct) == 1:
            redirects[name] = destinations[0]

    def rewrite(text):
        def replace(match):
            target, sep, label = match[1].partition("|")
            base, anchor, fragment = target.partition("#")
            base = base.removesuffix(".md")
            destination = redirects.get(base + ("#" + fragment if anchor else ""))
            if destination is None and base in redirects:
                path, section, title, identifier = redirects[base]
                destination = (path, section + (" / " + fragment if anchor else ""), title, identifier)
            if destination is None:
                return match[0]
            path, section, title, _ = destination
            return "[[" + path.removesuffix(".md") + "#" + section + "|" + (label if sep else title) + "]]"
        return re.sub(r"(?<!\\)\[\[([^\]]+)\]\]", replace, text)

    own = {identifier: rewrite(text) for identifier, text in own.items()}
    documents = {}
    for group, members in sorted(groups.items()):
        path = "10-知识/" + group_names[group] + ".md"
        pieces = ["---\nkind: collection\ntags: [结构导航]\n---\n\n# " + group +
                  "\n\n本页合编原有笔记，正文与属性完整保留。可用大纲或查询结果定位具体章节。\n\n"]
        for node in sorted(members, key=lambda n: (n["kind"] != "overview", n["id"])):
            front, _, body = split_note(own[node["id"]])
            node["six_level_headings"] = [title for level, title in headings(body) if level == 6]
            metadata = ("<details>\n<summary>笔记属性</summary>\n\n```yaml\n" + front +
                        "\n```\n\n</details>\n\n") if front else ""
            converted = transform_headings(body, node["section"], node["original_heading"])
            if not converted.endswith("\n"):
                converted += "\n"
            section = metadata + "<!-- tool-body:" + node["id"] + " -->\n" + converted
            node["section_sha256"] = digest(section)
            pieces.append("<!-- tool-node:" + node["id"] + " -->\n" + section + "<!-- tool-end:" + node["id"] + " -->\n\n")
        documents[path] = "".join(pieces)
        owner = nodes["collection:" + group]
        previous_owner = old_nodes.get(owner["id"])
        # Free text outside the indexed chapters is user material too. Preserve
        # it at collection level rather than overwriting it on the next build.
        def outside(text):
            return re.sub(r"<!-- tool-node:([^\n]+) -->\n.*?<!-- tool-end:\1 -->\n\n", "", text, flags=re.S)
        generated_outer = outside(documents[path])
        if previous_owner and (Path(vault) / previous_owner["path"]).is_file():
            previous_path = (Path(vault) / previous_owner["path"]).resolve()
            if previous_path not in cache:
                read_existing(previous_owner, vault, cache)
            current_outer = outside(cache[previous_path])
            expected = previous_owner.get("outer_sha256", digest(generated_outer))
            if previous_owner.get("preserve_outer") or digest(current_outer) != expected:
                documents[path] = current_outer + "".join(pieces[1:])
                owner["preserve_outer"] = True
        owner["outer_sha256"] = digest(outside(documents[path]))
    for identifier, note in own.items():
        node = nodes[identifier]
        if node.get("collection_id") and extract_note(node, documents[node["path"]]) != note:
            raise ValueError("合编前后内容不一致：" + identifier)
    return documents, own, rewrite, {str(path): cache[(path, "sha256")] for path in cache if isinstance(path, Path)}
