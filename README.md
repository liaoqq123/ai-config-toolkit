# AI 通用配表与知识库工具

一份与业务项目分离的工具底座：显式 Excel 读取与结构索引、修改及追加预览、逐表人工确认、CAS 并发检查、备份与回读恢复，以及少量 Obsidian 合集和精确章节读取。随包只有虚构演示数据；真实项目通过配置接入。

**其他 AI 从 [AGENTS.md](AGENTS.md) 开始，再读 [AI 工作指南](docs/AI_GUIDE.md)。** 怎样补知识、接新表和开发功能见 [扩展指南](docs/EXTENDING.md)；接入新项目见 [接入步骤](docs/PROJECT_ONBOARDING.md)。

## 目录

```text
ai-config-toolkit/
├─ AGENTS.md                   AI 首读规则与完成标准
├─ config_toolkit/core/        读表、预览事务、知识合集与配方规划
├─ config_toolkit/tools/       输出 JSON 的命令入口
├─ config/                    项目模板、演示规则、配方和知识输入
├─ client/                    本机逐表人工确认网页
├─ examples/                  虚构 Excel、输入请求与生成脚本
├─ obsidian-vault/            精简知识合集；不保存业务现值
├─ docs/                      接入、证据、维护和开发说明
├─ tests/                     临时工作簿隔离测试
├─ verification/              本副本验收结果
└─ .work/                     使用后产生的本地状态，默认不纳入版本控制
```

原项目的业务表、实际目录、领域引用、活动规则、历史记录、审批凭据和服务安装设置均不在这份副本里。配置路径相对所属 JSON 文件解析，命令输入及输出路径相对当前工作目录；无需写死本机目录。

Git 保存工具代码、文档、知识输入和演示生成脚本；生成的工作簿、结构索引、Obsidian 合集、本机项目配置和运行状态不入库。首次克隆后按下列命令生成演示资料。新增真实项目的 `projects/` 目录默认忽略，其资料应使用项目自己的版本管理。

## 运行演示

需要 Python 3.10+；运行测试和创建演示表需要 `openpyxl`。如当前环境缺少依赖，先运行 `python -m pip install -r requirements.txt`。在工具根目录执行下列 PowerShell 命令：

```powershell
if (!(Test-Path 'examples/data/Catalog.xlsx')) { python -X utf8 examples/create_demo.py }
python -X utf8 -m unittest discover -s tests -v
python -X utf8 config_toolkit/tools/config_read_plan.py --project config/project.example.json --root demo build-index --output config/schema-index.json
python -X utf8 config_toolkit/tools/knowledge.py --project config/project.example.json build
python -X utf8 config_toolkit/tools/knowledge.py --project config/project.example.json validate
python -X utf8 config_toolkit/tools/knowledge.py --project config/project.example.json query Catalog
python -X utf8 config_toolkit/tools/knowledge.py --project config/project.example.json read spec:catalog
python -X utf8 config_toolkit/tools/config_read_plan.py --project config/project.example.json --root demo read --file Catalog.xlsx --sheet 配置 --id item_alpha
```

生成脚本拒绝覆盖已有工作簿。演示表采用 A1、六行表头与 Start/End，这些约定在 `excel.layout` 中显式配置；它们不代表所有 Excel 的结构。已有索引可用于定位，当前记录仍须定向读取。

## 预览、确认、写入

```powershell
python -X utf8 config_toolkit/tools/config_workflow.py --project config/project.example.json --root demo preview --input examples/patch.json
python -X utf8 client/review_server.py --project config/project.example.json --root demo --port 8765
```

打开 [本机确认页](http://127.0.0.1:8765)，逐表检查实际 before/after，填写确认人和说明，再勾选确认。页面返回 `approval_id`，由执行方用对应的 `preview_id` 写入：

```powershell
python -X utf8 config_toolkit/tools/config_workflow.py --project config/project.example.json --root demo apply --preview <preview_id> --approval <approval_id>
```

人工确认也可通过 CLI `approve` 记录，具体命令见 [运行约定](docs/OPERATING_MODEL.md)。AI 不能为自己伪造确认人或批准。确认页面只记录批准，不自动执行写入。预览后配置、目标或已读取依赖改变，原批准失效，需要重新预览。

每次写入只提交一张明确的物理表。每个步骤都有备份、持久事务记录和回读。恢复操作只撤回仍与该事务 after-image 一致的内容，遇到外部新编辑会保留冲突供处理。完整说明见 [确认页面说明](docs/REVIEW.md)。

## 业务配方

配方只组合通用请求，不带领域默认值，也不提供写入许可。示例编译：

```powershell
python -X utf8 config_toolkit/tools/config_plan.py --project config/project.example.json --root demo compile --recipe config/recipes/example_batch.json --answers examples/recipe-answers.json --output .work/example/plan.json
python -X utf8 config_toolkit/tools/config_plan.py --project config/project.example.json --root demo preview --plan .work/example/plan.json --step edit_catalog
```

每步仍需当前真实预览、逐表人工确认和独立写入回执。依赖顺序由计划给出；当前版本不会凭计划自动连写后续步骤。计划输出已存在时拒绝覆盖，重复演练请换新文件名。细则见 [配方规划](docs/RECIPES.md)。

## 怎样完善

1. 新项目：复制 `project.template.json`，先确认数据根、布局和状态隔离，再在样表副本上做读表测试。
2. 新知识：增加稳定 ID、正文、分组、来源和证据边，再 build/validate；合编后用 `read <id>` 精确读取。
3. 新引用：登记真实表头 FOREIGN 或 `references`；先用当前实际记录验证，不按字段名字猜目标。
4. 新操作：写清输入、作用范围、引用契约和回读标准，再接入预览、审批、事务和恢复，并补隔离回归。
5. 每次扩展同时更新能力矩阵、示例、测试和文档；未完成的适配不能写成已支持。

目前正式支持 `.xlsx` 的显式读取、受控 `patch` 和 `append`。追加模板带公式、复杂尾部布局、新建分表、跨表复制、业务激活、特殊 JSON 引用、SVN 同步和外部审批连接需要另行适配；不能把配置文件中的新名字当作已实现能力。已存在的非目标公式、样式、绘图及其他 ZIP 成员在受支持的定向修改中保留。

Obsidian 直接打开 `obsidian-vault`；旧笔记归档在仓库外的项目状态目录。原始笔记属性与全文保留在合集章节中。给其他 AI 的维护方式和扩展验收清单均在 `docs/`，不依赖当前聊天记录。
