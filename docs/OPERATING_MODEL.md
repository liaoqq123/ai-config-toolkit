# 读取、确认、写入与恢复

## 一次任务的边界

每次任务先明确项目配置、数据根、业务目标、物理表范围和实际依赖。用户指定的路径、分表和 ID 直接沿用；有歧义时只补问影响当前操作的部分。

索引用于定位，知识用于解释，实际工作簿用于取得当前值。预览、审批与回执各自证明不同阶段，不能相互替代。

## 只读阶段

1. 查询知识并精确读取命中章节，理解字段与证据。
2. 定向读取目标工作簿和必需依赖；核对真实逻辑表、主键、字段和公式。
3. 列出本次所有待写表与只读依赖。业务闭包未定义时先补齐，不先写主表再临时补子表。
4. 对已支持操作生成请求。当前通用操作是 patch 和 append；其他业务需要先扩展。

来源结构陈旧时刷新受影响的索引与知识。普通查询不自动全盘扫描，也不把上一次读到的记录当作当前值。

## 两种通用请求

Patch 修改已有记录，逐字段绑定前后值：

```json
{
  "file": "Catalog.xlsx",
  "sheet": "配置",
  "operation": "patch",
  "rows": [
    {
      "id": "item_alpha",
      "changes": {
        "Count": {"before": 3, "after": 5},
        "GroupId": {"before": "group_a", "after": "group_b"}
      }
    }
  ]
}
```

Append 增加完整新记录：

```json
{
  "file": "Groups.xlsx",
  "sheet": "配置",
  "operation": "append",
  "rows": [
    {"Id": "group_c", "Name": "第三分组"}
  ]
}
```

这些内容仅描述虚构 fixture。实际请求的字段、类型和前值必须来自本次实表；不能先写固定示例再让校验迁就它。追加到带公式的表时，新行公式与未提供字段如何处理必须有明确策略，不能沿用旧缓存值冒充新计算结果。

## 预览与人工确认

```powershell
python -X utf8 config_toolkit/tools/config_workflow.py --project config/project.example.json --root demo preview --input requests/catalog-count.json
```

把返回的真实差异展示给用户：目标根、文件与分表、记录 ID、字段前后值、依赖和任何阻断项。需要多张表时先把整批清单列全，再按表执行已有预览流程。

确认的是这一份具体预览。需求、来源、目标、依赖或差异变化后应重新预览；旧确认不能自动沿用。

收到明确确认后登记审批，再执行写入：

```powershell
python -X utf8 config_toolkit/tools/config_workflow.py --project config/project.example.json --root demo approve --preview <本次预览ID> --actor "reviewer" --reason "已确认本次预览的字段差异"
python -X utf8 config_toolkit/tools/config_workflow.py --project config/project.example.json --root demo apply --preview <本次预览ID> --approval <本次审批ID>
```

审批 ID 与预览 ID 由工具返回；不能自行拼接、抄用示例或跨项目复用。不要把审批 token 写进知识笔记、截图说明或外部交接正文。

本包也提供本机确认页：

```powershell
python -X utf8 client/review_server.py --project config/project.example.json --root demo --port 8765
```

按 `REVIEW.md` 在页面上逐表核对真实差异、填写确认人和说明并勾选确认。页面只返回批准 ID，不自动apply，也不会代表用户确认整个配方。

如果使用多表模板，先按 `RECIPES.md` 编译；当前规划器只提供显式请求、依赖顺序和单步预览，不自动推进。每步实际回读成功后，再准备下一步当前预览，不能把此前批量生成的旧预览视为一直有效。

## CAS、备份和回读

Apply 重新核对预览绑定的目标与依赖快照；检查成功才进入写入。`before` 正确只是一个字段的条件，不能替代文件与依赖CAS。

保存前先持久保存备份和事务身份，执行允许范围内的改动，再从保存后的文件回读。检查实际值、结构和非目标内容，成功后才生成完成回执。

来源冲突不通过“强制覆盖”解决。重新读取当前状态，重新生成可核查预览，再取得针对新差异的确认。

## 安全恢复

```powershell
python -X utf8 config_toolkit/tools/config_workflow.py --project config/project.example.json --root demo recover --transaction <原事务ID>
```

恢复仍使用原项目、原数据根、原事务与备份；不要为了解锁而修改 journal、审批或哈希。

|当前状态|处理原则|
|---|---|
|文件仍是事务前镜像|无需再次覆盖，按记录核对恢复结果|
|文件仍是本事务的后镜像|使用校验通过的原备份安全撤回|
|文件已被外部修改|停止自动回滚，保留事务与证据，避免覆盖外部改动|
|事务已完成|读取实际回执，不把同一次apply重新执行|
|日志或备份不完整|保留现场并报告缺项，不能伪造完成状态|

恢复命令返回结果后仍要查看状态和实际文件，不把命令启动成功等同于恢复成功。

## 验证与完成报告

工具改动先在隔离 fixture 运行相关测试，必要时再跑完整测试集：

```powershell
python -m unittest discover -s tests -v
```

业务任务完成报告应包含实际保存的表、回读结论和未完成项。耗时区分定位、读表、预览、等待确认和写入回读；不要把等待用户回复称作工具读取耗时。

不会自动安装或注册服务，也不自动改变全局 AI 配置。除本机确认页外的调度、远程界面和外部审批服务，需要先核对其身份验证、实际能力和项目绑定，再接入这条预览与执行边界。
