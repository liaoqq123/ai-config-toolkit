# 接入一个新项目

接入的目标是让另一位 AI 可以从明确的配置入口，重复完成读取、知识定位、预览、确认和回读，而不是复制演示数据后修改几个名称。

## 接入前先取得这些信息

|信息|需要的证据|
|---|---|
|项目身份|用户指定的独立项目名和配置文件位置|
|数据根|确切目录及命名；多个根各自的用途|
|表格格式|逻辑表位置、字段/类型/约束行、有效区边界|
|写入目标|实际文件和分表，不能只给逻辑表别名|
|引用规则|表头声明或明确登记的契约及来源|
|公式策略|哪些单元格已有公式，新记录如何取得明确字段值|
|运行状态|源目录之外的独立状态目录|
|知识来源|人工文档、结构索引和未解决的问题|

只缺某项业务语义时，可以继续完成无关的只读定位；不能在缺少目标或格式协议时先写一部分。

## 建立独立配置

在 `projects/catalog-demo/` 新建 `project.json`。下面是完全虚构且指向演示 fixture 的完整样例；接真实项目时，数据根由用户实际指定。

```json
{
  "schema_version": 1,
  "project_id": "catalog-demo",
  "data_roots": {
    "main": "../../examples/data"
  },
  "state_dir": "../../.work/catalog-demo",
  "excel": {
    "layout": {
      "logical_table_cell": "A1",
      "field_row": 5,
      "type_row": 4,
      "description_row": 2,
      "constraint_row": 6,
      "data_start_row": 7,
      "marker_column": "A",
      "start_marker": "Start",
      "end_marker": "End",
      "include_end": true,
      "skip_sheet_prefixes": ["$"]
    }
  },
  "references": [
    {
      "table": "Catalog",
      "field": "GroupId",
      "target_table": "Groups",
      "target_field": "Id",
      "nullable": false
    }
  ],
  "aliases": {},
  "knowledge": {
    "vault": "../../vaults/catalog-demo",
    "notes": "knowledge-notes.json",
    "index": "schema-index.json"
  }
}
```

所有相对路径按项目配置文件的位置解析。`main` 是本示例数据根名称；根目录演示配置使用的 `demo` 与它不同，不能混用命令。

不要复制 `.work`、审批记录、token 或其他项目的真实索引。仅复制配置结构与文档模板，然后为本项目重新生成来源证据。

## 检查格式而不是猜格式

先生成隔离演示数据：

```powershell
if (!(Test-Path 'examples/data/Catalog.xlsx')) { python -X utf8 examples/create_demo.py }
```

示例有 `Catalog.xlsx/配置` 和 `Groups.xlsx/配置`。A1 分别为 `Catalog`、`Groups`；Catalog 使用 `Id`、`Name`、`GroupId`、`Count`、`Rule` 字段。主键约束是 `PRIMARY`，`GroupId` 的表头声明为 `FOREIGN:Groups.Id`。Rule 中已有公式，用于验证读取与写入边界。

真实项目应核对相同行号和边界规则。如果格式不受当前引擎支持，先开发格式适配与隔离 fixture；不要靠改几个 profile 行号宣称任意格式已适配。

## 建结构索引并定向读取

```powershell
python -X utf8 config_toolkit/tools/config_read_plan.py --project projects/catalog-demo/project.json --root main build-index --output projects/catalog-demo/schema-index.json
python -X utf8 config_toolkit/tools/config_read_plan.py --project projects/catalog-demo/project.json --root main read --help
```

索引只覆盖所选根。后续读取明确文件、分表与 ID，例如 `Catalog.xlsx/配置/item_alpha` 及它的分组依赖。核对返回的真实逻辑表、类型、约束、公式和来源指纹。

`build-index` 不是业务值同步服务。普通查询不因有新任务就重建全库；发现文件新增或表头变化时，只刷新受影响范围与相应知识。

## 建立最初知识

最初知识只需覆盖项目入口、格式说明、Catalog 与 Groups 的关系、写入边界和未知项。将下面的完整配置保存为 `projects/catalog-demo/knowledge-notes.json`：

```json
{
  "schema_version": 1,
  "notes": [
    {
      "id": "spec:catalog",
      "kind": "table",
      "title": "Catalog 表结构",
      "group": "表结构",
      "aliases": ["Catalog", "目录", "目录分组"],
      "content": "# Catalog 表结构\n\n虚构目录表。Id 为主键，GroupId 显式引用 Groups.Id。Count 当前值必须从实际文件读取。Rule 中已有公式，普通 patch 不能直接覆盖公式单元格。\n\n未知：应用中的显示顺序尚无运行证据。",
      "sources": [
        {"root": "main", "file": "Catalog.xlsx", "sheet": "配置", "table": "Catalog"}
      ]
    },
    {
      "id": "spec:groups",
      "kind": "table",
      "title": "Groups 表结构",
      "group": "表结构",
      "aliases": ["Groups", "分组"],
      "content": "# Groups 表结构\n\n虚构基础表，字段 Id 和 Name，Id 为主键。可新增完整、无冲突的分组记录；实际记录与主键存在性由本次读取确认。",
      "sources": [
        {"root": "main", "file": "Groups.xlsx", "sheet": "配置", "table": "Groups"}
      ]
    }
  ],
  "edges": [
    {
      "source": "spec:catalog",
      "target": "spec:groups",
      "field": "GroupId",
      "evidence": "explicit_header_and_project_reference"
    }
  ]
}
```

这条边的两端都在 notes 中定义。引用来自实际表头与项目配置，不能把随意取名的节点或尚未登记的逻辑表 ID 当作存在的目标。

至少登记这些内容：

- 结构来源：命名数据根、文件、分表、A1 逻辑表和索引文件。
- 引用证据：Catalog.GroupId 的目标由表头 FOREIGN 声明，不因字段包含 `Group` 就猜出关系。
- 业务说明：这些虚构表的用途、哪些来源可复用、哪些语义仍未知。
- 边界：Rule 的公式不能被普通字段更新静默覆盖，结构迁移和业务激活尚无适配器。

```powershell
python -X utf8 config_toolkit/tools/knowledge.py --project projects/catalog-demo/project.json build
python -X utf8 config_toolkit/tools/knowledge.py --project projects/catalog-demo/project.json query "Catalog"
python -X utf8 config_toolkit/tools/knowledge.py --project projects/catalog-demo/project.json validate
```

对查询返回的真实 ID 执行 `read`，确认没有把其他节点的正文当成该节点内容。笔记中不要预填当前 Count 数量并把它长期当作业务真值。

## 做一次可核查的写入演练

在演示数据中读取 `item_alpha` 的当前 Count，然后按本次实际值创建 patch 请求。请求示例见 `AI_GUIDE.md`。目标是验证完整生命周期，不能跳过预览与审批直接调用保存函数。

```powershell
python -X utf8 config_toolkit/tools/config_workflow.py --project projects/catalog-demo/project.json --root main preview --input requests/catalog-count.json
```

核对预览后，记录实际预览 ID，按 `OPERATING_MODEL.md` 确认、审批、执行和回读。真实项目首次接入先在用户授权的独立副本验证，不把真实目录当 fixture。

## 交付给下一位 AI 的接入结论

接入记录要能回答：配置文件在哪、根名称是什么、格式如何确认、哪些表能读/能写、引用证据是什么、有哪些未知、哪些操作已通过隔离验证。

至少完成以下验收：

```powershell
python -m unittest discover -s tests -v
python -X utf8 config_toolkit/tools/knowledge.py --project projects/catalog-demo/project.json validate
```

再验证一次来源变更：预览后在隔离副本中更改目标或引用依赖，旧预览必须被拒绝。确认状态、备份和知识归档都在数据根之外。没有完成这些检查时，将接入状态写成“只读已验证”或“基础写入待验证”，不要概括为“全部业务已支持”。
