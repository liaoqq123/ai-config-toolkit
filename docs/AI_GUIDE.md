# 给接手 AI 的工作指南

这个包解决两件事：可靠地读取和修改 Excel 配置，以及把分散知识合编为少量可检索、可精确读取的 Obsidian 文档。两者共用项目身份，但知识命中不授予写入权限。

## 先确定本次任务

先读根目录 `AGENTS.md`、`README.md`，然后选择最短入口。

|用户意图|先做什么|后续入口|
|---|---|---|
|了解表或字段|知识查询，读取命中章节，再读实际分表|`config_toolkit/tools/knowledge.py`、`config_toolkit/tools/config_read_plan.py`|
|修改已有记录|定位真实 ID、字段、原值和引用，准备 patch 请求|`config_toolkit/tools/config_workflow.py`|
|增加记录|确认目标分表、完整字段与新 ID，准备 append 请求|`config_toolkit/tools/config_workflow.py`|
|接入新项目|明确项目配置和数据根，检查表头协议|`PROJECT_ONBOARDING.md`|
|增加知识|登记来源、证据、未知项，再重建和校验|`EXTENDING.md`|
|增加写入能力|实现适配、预览、回读和隔离回归|`ARCHITECTURE.md`、`EXTENDING.md`|
|组合多表显式请求|编译通用配方，按依赖逐步取得真实预览|`RECIPES.md`|
|通过网页确认|启动仅绑定本机的确认页，逐表确认真实差异|`REVIEW.md`|
|写入失败|读取原事务，检查当前文件后恢复|`OPERATING_MODEL.md`|

不要把“需要支持某项业务”理解成“可以用已有通用写入拼出任意业务”。数据闭包、所有权、复制策略和最终启用规则可能仍未定义。

## 识别三种真值

1. 项目配置确定允许的数据根、状态目录、表头位置、别名与显式引用。它描述工具如何解释数据，不证明某条业务记录存在。
2. 当前工作簿确定真实表头、记录、公式与文件指纹。需要当前值时必须定向读取。
3. 知识笔记解释用途、证据与未知项。生成时间、模板内容和旧 ID 不能作为当前实值。

一个结论至少说明“在哪个根、哪个文件/分表、什么字段或规则”。如果证据只是推断，应保留推断标识，不把它升级成程序契约。

## 读取范式

以下只操作虚构演示项目；接入真实项目后替换 `--project` 和 `--root`。

```powershell
if (!(Test-Path 'examples/data/Catalog.xlsx')) { python -X utf8 examples/create_demo.py }
python -X utf8 config_toolkit/tools/config_read_plan.py --project config/project.example.json --root demo build-index --output config/schema-index.json
python -X utf8 config_toolkit/tools/knowledge.py --project config/project.example.json build
python -X utf8 config_toolkit/tools/knowledge.py --project config/project.example.json query "Catalog"
```

使用查询返回的稳定节点 ID 执行：

```powershell
python -X utf8 config_toolkit/tools/knowledge.py --project config/project.example.json read "spec:catalog"
```

ID 以本次真实查询结果为准。命中路径可能是包含许多章节的合集；不要为了一个字段把整卷当作该节点全文。`read` 返回该节点原笔记的完整内容，包含恢复后的属性与标题。

再使用读表 CLI 定位 `Catalog.xlsx` 的 `配置` 分表。先运行 `read --help` 核对选择参数，不猜当前版本的参数拼法。需要 `item_alpha` 时只读取该 ID 及其依赖；如果多个分表包含相同 ID，保留歧义并确定本次范围。

```powershell
python -X utf8 config_toolkit/tools/config_read_plan.py --project config/project.example.json --root demo read --file Catalog.xlsx --sheet 配置 --id item_alpha
```

query 默认检查返回候选的文件指纹，不读取业务行。只有导航时可加 `--no-source-check`；该选项不是写入时跳过CAS的入口。`stale_knowledge` 表示配置、知识输入或结构索引变化，应更新相应来源再构建；`stale_sources` 表示真实来源文件指纹变化，先定向核对并刷新所需索引。单独重建笔记不等于重新索引工作簿。

## 写入范式

一个 patch 请求明确文件、分表、记录和前后值：

```json
{
  "file": "Catalog.xlsx",
  "sheet": "配置",
  "operation": "patch",
  "rows": [
    {
      "id": "item_alpha",
      "changes": {
        "Count": {"before": 3, "after": 5}
      }
    }
  ]
}
```

`before` 来自本次读取。数字在请求中采用字符串还是数值，按读取结果和实际类型协议处理，不为让校验通过而随意转换。保存为状态目录之外的请求文件，例如 `requests/catalog-count.json`。

```powershell
python -X utf8 config_toolkit/tools/config_workflow.py --project config/project.example.json --root demo preview --input requests/catalog-count.json
```

预览通过后，把实际字段差异和影响交给用户确认；保存本次返回的预览 ID。只有收到对该预览的明确确认，才执行：

```powershell
python -X utf8 config_toolkit/tools/config_workflow.py --project config/project.example.json --root demo approve --preview <本次预览ID> --actor "reviewer" --reason "已确认本次Count字段差异"
python -X utf8 config_toolkit/tools/config_workflow.py --project config/project.example.json --root demo apply --preview <本次预览ID> --approval <本次审批ID>
```

尖括号处必须替换为本次返回的真实 ID，不能沿用示例或其他项目凭据。命令中出现 `approve` 不表示 AI 获得了替用户批准的权限。

也可以按 `REVIEW.md` 启动本机确认页，由用户逐表检查并返回批准 ID；页面不自动写入。需要组合步骤时按 `RECIPES.md` 编译模板，但每步仍需本次实际读取、预览、人工确认、独立apply与回读。当前规划器不会自动执行后续步骤，也没有整批自动审批。

## 何时停下来补信息

以下情况先补齐证据或实现，不能通过放宽校验继续：

- 目标分表或数据根不唯一。
- 表头与当前 profile 不匹配；主键重复；Start/End 结构异常。
- 公式原值、缓存或新行公式策略不清楚。
- 某字段像引用，但表头和项目配置没有声明。
- JSON 中的值是否为 ID、嵌套对象、枚举或数量尚未确认。
- 业务要求复制多个表，但新 ID、复用字典、所有权和依赖闭包未定义。
- 预览后来源变化、审批过期或回读失败。

集中列出影响本次任务的缺项；已经确定的路径、分表和业务答案不要重复询问。

## 如何留下可继续维护的交接

一次改动结束时，给下一位 AI 留下项目配置路径、实际修改的表、预览/事务引用、回读结论和新增知识的位置。不要在交接正文粘贴审批 token、完整缓存或与任务无关的业务数据。

如果扩展仍未实现，说明已有的输入与证据、缺少的适配器、预览/回读验收条件及对应 fixture；不要写成“已支持，待测试”。
