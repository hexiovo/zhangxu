# 余序本机账本

余序在本机提供一个 Python 服务，同时开放 `127.0.0.1` 网页和 MCP stdio 工具。账本数据保存在 `%LOCALAPPDATA%/Yuxu`，运行状态保存在其 `runtime` 子目录。

## 本机 AI 记账与交付

- 日常记账、凭证待办、对账和导出继续使用同一个本机账本。宿主使用本机 MCP stdio，网页登录和文件领取走 `127.0.0.1`。图片与 PDF 可在「流水与核账 → 凭证记账」用 Windows OCR 转为草稿，也可通过 `stage_receipt_bookkeeping` 提交文件和已提取的候选条目。先看实际金额、日期、收支类型和疑似重复，确认草稿后才入账。两次提交相同凭证可找回已有草稿。每笔上传最多 10 MiB，PDF OCR 最多五页，无法识别的字段由账本所有者补齐。
- 新宿主仍默认只有 `summary_read`。按需在「授权与安全」开启 `detail_read`、`write`、`export_summary`（生成本机文件）和 `export_artifact`（领取授权的脱敏 PNG）。敏感字段导出由本人打开 `review_url` 在网页确认，文件由本人登录下载。
- MCP 写入参数必须包含稳定的 `request_id`。宿主应在首次调用前保存 ID 和参数。超时后先查 `get_request_status` 的 `data.outcome`，重试沿用原 ID 和参数。按 `get_workflow_guide({scenario:"record"})`、`"receipt"`、`"correct"`、`"reconcile"` 或 `"export"` 查常用流程。
- `export_report_card` 支持生成月报、保存的月报快照，以及按写入请求号生成的回执卡。PNG 可以经 `get_export_artifact({file_id, inline:true})` 回到支持图片的本机 MCP 宿主。也可用 `review_url` 在网页登录下载。月报和回执卡均可另存为 PDF。
- `create_category_or_tag` 允许有写入权限的本机 MCP 宿主新建本人账本的分类或标签。类型填 `category` 或 `tag`，二级分类需提供 `parent_category_id`。同一级存在等价名称时返回原记录。记账时使用返回的分类编号或标签名称。

## 首次设置

打开「余序」快捷方式，在登录页点击「注册本机账号」。为两位使用者分别填写用户名、账本显示名和密码，选择一位本机维护者，可另外选择支出管理员。注册完成后即可登录。如需接入 MCP，各使用者登录后在「授权与安全」创建自己的宿主令牌。

也可使用 Python 3.12 在命令行初始化：

```powershell
py -3.12 -X utf8 -B F:/桌面/程序/yuxu/run_yuxu.py setup
```

网页与命令行首次设置都为两位使用者创建独立账本。支出管理员可读取已授予账本的支出报告与明确目标进度。维护者可在本机创建、预览和确认恢复备份。命令行设置另外为两人创建初始 MCP 令牌，明文只显示一次。新令牌默认只有 `summary_read`，为对应 MCP 客户端进程安全设置 `YUXU_TOKEN`。

本人也可在“授权与安全”中为单个宿主创建令牌或换发其令牌。新令牌默认只有 `summary_read`；明文仅在创建或换发响应中显示一次。首次启用明细读取或记账写入：选择对应宿主，按需勾选 `detail_read` 与 `write` 并保存。之后 MCP 会在下一次请求读取新权限。写入仍受本人账本的 `own_write` 访问范围约束。变更或撤销都只影响本人令牌。

读取单笔交易的商家或备注时，先在同一网页页面选定宿主、流水和字段，明确确认一次授权；随后 MCP 可在三分钟内用一次 `get_transaction({transaction_id, private_fields:["note"]})` 请求读取已选字段。授权只针对该令牌、账本、流水和字段，成功读取后立即失效。相同的商家授权也可由 `copy_transaction_to_draft` 一次性使用；新草稿不沿用原金额或日期，MCP 响应仍隐藏商家和备注。授权不记录到操作回执、运行日志或访问审计字段中。

## MCP 宿主初始化

宿主启动后依次调用 `get_current_account`、`get_workflow_guide`、`list_accessible_accounts` 和 `get_account_access_policy`。仅使用返回的账户 ID 与已验证 scopes；跨账工具必须在顶层显式传入目标 `account_id`，并拥有对应的 `admin_spending` 或只读 `admin_goal`。省略时始终使用本人账本。写操作复用稳定 `request_id`；结果不确定时用 `get_request_status(target_request_id)` 查询，再用原 ID 和原参数重试。宿主的汇总读取、明细读取和写入能力由 MCP 清单及后端分别检查，撤销立即对后续请求生效。

## 计划、提醒、导出与备份

登录后可从导航进入「数据可视化」，按日期和分类查看支出走势、期间对比、分类占比及年度支出日历。月报和交叉报表在「报表分析」中。

固定支出和订阅规则可按账期补录；每条规则每账期最多生成一笔流水，待确认项目通过更新原流水确认。未来现金流也可登记有日期的一次性计划收入或支出，这些计划不会计入真实流水。四类提醒按账本、原因和月份去重；异常消费在历史样本不足时不会触发。自定义周期、交叉报表、消费次数、支出归因、滚动趋势、固定/弹性分类、预算试算、月报模板和快照从 insights 工具及网页报表入口进入。

导出使用 `preview_ledger_export` 展示范围、字段和行数，选择脱敏配置时用 `redaction_profile_id`；确认后 `confirm_ledger_export` 使用同一版本的配置生成文件，再从已登录的同源网页下载。维护者可用 `create_backup` 建立版本备份，并用 `preview_restore` 检查恢复内容；正式恢复必须在网页确认预览，系统会先自动创建当前状态备份。`backup_drill` 会将备份恢复到隔离 SQLite 副本检查条数、余额和定期款账期，不触碰当前数据库。可用 `run_yuxu.py grant-maintenance USERNAME` 将独立维护权限重新指定给一位本机账本用户。

流水表格支持 CSV/XLSX。月报和回执卡支持 PNG/PDF，默认只包含统计结果。大部分生成的文件自生成起七天内可从本机领取。

跨设备迁移时，先在来源设备创建 ZIP 备份，再由用户将文件安全带到目标设备。目标设备完成自己的 `setup` 后，本机维护者可在网页登录并选择 ZIP 上传，或将 ZIP 放入 `<YUXU_DATA_DIR>\backups`（默认 `%LOCALAPPDATA%\Yuxu\backups`）后预览。跨设备预览先列出来源两本账和本机两位用户的目标账户；用户逐本明确选择一对一映射后再次预览，再检查来源、目标和摘要并确认恢复。服务保留目标设备现有的账号、密码、角色授权和 MCP 令牌，只替换迁移的账务数据；来源登录凭据、会话、令牌和临时授权不会导入，业务 ID 与附件内容保留并重新绑定到目标身份。目标现有数据会先自动备份。不要按账户名称自动匹配，也不要把备份 ZIP 放入不受信任的共享目录。

## 13 项增长功能

| 功能 | 使用方式与边界 |
|---|---|
| 报表期间对照 | 在现有报表中选择去年同期、上一工资账期或任意有效自选区间；结果同时显示双方的起止日期。 |
| 概览分类联动 | 点击分类可在当前页筛选趋势和近期流水，再次点击清除筛选；原有进入流水明细的入口保留。 |
| 常看报表视图 | 保存期间、日期口径、分类、图表、聚合和可用对照；可应用或删除，配置随账本备份。 |
| 预算进度 | 显示已用、剩余及周期内日期进度；已用金额沿用当前账本的授权与消费入账口径。 |
| 年度消费日历 | 区分无流水、有流水但零消费和待核对日期；逐日查看详情，日历覆盖完整自然年。 |
| MCP 本机查看链接 | 可生成账本、期间、分类或本人流水的短时链接；Web 打开时重新检查主体和账本权限。链接不暴露账务数据、筛选条件或长期密钥；流水链接只用于本人账本。 |
| CSV 流水对账 | 本机网页和 MCP 均可生成预览，MCP 返回本机交接链接。只有本人在网页勾选并确认的缺失行才入账。支持微信支付、支付宝和银行账单常见列名映射、可选支付状态过滤及订单号精确关联。 |
| 支付账户余额核对 | 按日期查看账面余额，录入实测余额并保存差额和核对时间；信用卡欠款按正数显示。 |
| 复制流水为草稿 | Web 可复制现有流水的允许字段；MCP 还需要当前宿主的 `write`、`detail_read` 以及对该流水商家的单次授权。提交前必须填写新金额和发生日。 |
| MCP 宿主令牌创建与换发 | 在本人 Web 会话中单独创建或换发令牌；默认最小 `summary_read` 权限，明文只显示一次，换发只撤销所选旧令牌。 |
| CSV 来源模板 | 按账本保存列映射、日期格式、金额单位和表头签名，不保存 CSV 正文或行。表头变化时重新核对映射。 |
| 定期本机备份 | 可选启用间隔备份和保留份数；设置页显示最近成功备份及恢复演练状态，计划设置只保留在本机安装。 |
| 网页上传备份 ZIP | 选择本机 ZIP 后先执行完整校验，再进入原有预览及逐本绑定流程；上传本身不会覆盖当前账本。 |

共享 stdio 宿主默认没有可信会话 ID，不能使用“刚才”引用。只有宿主确认一个 MCP 进程只服务一个对话时，才为该进程设置 `YUXU_ISOLATED_CONVERSATION=1`；其他情况用明确流水 ID、日期和筛选条件。工具参数不能自行声明会话 ID。账户页可查询本人看到的入向/出向授权、访问记录和 MCP 宿主权限；本机网页还提供脱敏运行日志、CSV 导入预览、附件管理、备份演练和一致性诊断。

## 独立安装与故障排查

`YUXU_DATA_DIR` 指定 SQLite 数据目录，`YUXU_RUNTIME_DIR` 指定服务 PID、端口和控制状态目录，`YUXU_PORT` 指定本机服务端口。独立安装要为三者设置互不冲突的值；同一安装中的网页和 MCP 必须使用同一组值。首次设置只在该安装的数据目录执行一次：

```powershell
$env:YUXU_DATA_DIR = "$env:LOCALAPPDATA\Yuxu-personal-a"
$env:YUXU_RUNTIME_DIR = "$env:LOCALAPPDATA\Yuxu-personal-a\runtime"
$env:YUXU_PORT = "8765"
py -3.12 -X utf8 -B F:/桌面/程序/yuxu/run_yuxu.py setup
py -3.12 -X utf8 -B F:/桌面/程序/yuxu/run_yuxu.py launch-web
```

需要另开一套互相隔离的安装时，为它设置新的数据目录、运行目录和空闲端口（例如 `Yuxu-personal-b`、对应的 `runtime` 和 `8766`），再运行一次 `setup`。不要为了新增独立账本而在已初始化的数据目录重复执行 `setup`；同一安装内的两位用户使用各自账号和账本。MCP 客户端启动时也要继承与目标网页服务相同的三个变量，并在客户端的受保护进程环境中配置对应个人的 `YUXU_TOKEN`。不要把口令或令牌写入共享 JSON、脚本仓库或群聊。

遇到端口冲突时为该独立安装改用空闲 `YUXU_PORT`，并让网页、MCP、`status` 和 `stop` 使用同一运行目录与端口。登录后看到了另一套数据时，先检查服务与 MCP 是否继承了同一个 `YUXU_DATA_DIR`；服务状态找不到时检查 `YUXU_RUNTIME_DIR` 是否一致。提示“已初始化”时先核对数据目录，不要再次运行初始化覆盖流程。默认路径仍是 `%LOCALAPPDATA%/Yuxu` 与其 `runtime` 子目录；不设置三个变量时可继续使用默认值。

## 个人宿主、QQ 与模型数据

每个 MCP 令牌都对应一个已验证的个人账号；首次令牌只有 `summary_read`，账号本人可在网页“授权与安全”页为所选主机开启 `detail_read` 或 `write`，也能撤销该令牌。请为每位使用者和宿主单独配置令牌，不要将令牌共享给群机器人、多人共用且无法判断发起人的主机或匿名入口。当前部署不自动注册、连接或发送 QQ/CuteDuck/桌宠消息；用户要接入时需在各自可信个人宿主完成注册、账号归属确认、数据范围与权限设置，并在隔离环境验证后才能声称已联通。群聊或无法归属到具体用户的共享宿主不得接到账本工具。

SQLite 账本、密码摘要、访问授权和附件保存在本机数据目录；本机服务不会因启动而自动上传整个数据库。用户输入到模型的文字、宿主发出的工具参数以及工具返回内容，会经用户选择的模型服务或外部宿主处理；模型供应商的保存与处理方式取决于用户的账号和服务配置。本机启动不代表已设置 QQ、常驻代理、自动通知或关机后投递。完成真实 QQ 或桌宠联调前，应先核对宿主身份、使用的模型/服务端、可发送的字段和返回路径；不把私密备注、访问令牌或全库内容放入提示词或共享日志。

## 基础 20 项与扩展 48 项功能入口

MCP 与网页共用本机 SQLite 和业务动作。MCP 中先调用 `get_workflow_guide({scenario:"base-01"})` 或 `get_workflow_guide({scenario:"ext-01"})`，用完整编号查询当前可见工具、必填字段、限制及调用顺序；无参数调用仍返回通用指南。网页按表中页面进入相应功能；个别工具会因本人账本权限、MCP 宿主权限或 Web 确认要求而不可见。所有需要确认的预览先展示实际范围及影响，再按网页提示确认。

证据列中的包代号指实现包记录（`PASS` 表示实现完成并交独立验收，不代表该阶段整体通过）：

- `BF`：`evidence/execution/wp-base-flows.json`；`LE`：`evidence/execution/wp-ledger-extension-01-16.json`。
- `PL`：`evidence/execution/wp-planning-budget-goals.json`；`MV`：`evidence/execution/wp-movements-and-obligations.json`。
- `RR`：`evidence/execution/wp-recurring-reminders-files.json`；`IN`：`evidence/wp-insights-extension-33-41.json`。
- `MT`：`evidence/execution/wp-maintenance-extension-42-48-v2.json`；`PF`：`evidence/execution/wp-platform-files-integration-step-03.json`。
- `G5-01 PASS` 与 `G5-02 PASS` 分别对应 `evidence/step-01-independent-verdict.json`、`evidence/step-02-independent-verdict.json`。`step-03 verdict` 以 `evidence/step-03-independent-verdict.json` 的真实最终状态为准；逐项补充证据保留在 `evidence/step-03-supplement-01-*`。不能用实现包状态代替独立验收结论。

这些证据文件位于本次 RUN：`H:/AIcode/HexiPlan/runs/yuxu-full-20261124-01a20ce4/`。

基础需求：

| 编号 | 功能 | 最短动作顺序与限制 | 网页入口 | 执行包证据 / 独立验收阶段 |
|---|---|---|---|---|
| base-01 | 一句话记多笔 | 一次调用 `add_transactions`；逐笔回显日期、收支、金额、账本和估算标志 | 流水与核账 | BF · G5-01 PASS |
| base-02 | 缺金额时追问并续填 | `upsert_transaction_draft` 保留日期和类别候选 → 用户补齐后 `commit_transaction_draft` | 流水与核账 | BF · G5-01 PASS |
| base-03 | 修改本对话上一笔 | `resolve_reference` 唯一定位 → `get_transaction` 取版本 → `update_transaction`；歧义时先询问 | 流水与核账 | BF · G5-01 PASS |
| base-04 | 撤销操作 | `undo_operation`；仅限可撤销操作 | 流水与核账 | BF · G5-01 PASS |
| base-05 | 查询写入回执 | `get_request_status({target_request_id})` | 流水与核账 | BF · G5-01 PASS |
| base-06 | 更新流水分类和标签 | `get_transaction` 核对当前记录与版本 → `update_transaction` 更新分类/标签 | 流水与核账 | LE · G5-01 PASS |
| base-07 | 跨聊天查找旧账 | `search_transactions` 按明确日期、金额、商家等线索查询；多条匹配时列候选 | 流水与核账 | LE · G5-01 PASS |
| base-08 | 组合筛选和分页 | `search_transactions` 按标签、金额、类别、期间等筛选，再用 `continue_transaction_search` 续页 | 流水与核账 | LE · G5-01 PASS |
| base-09 | 本月与上月比较 | `get_period_summary` → `get_spending_comparison`；标注估算、待确认与空区间 | 总览、报表分析 | BF · G5-01 PASS |
| base-10 | 分类饼图与钻取 | `get_category_breakdown` → `get_report_drilldown`；金额/占比显示，“其他”可展开，钻取受明细权限限制 | 总览、报表分析 | BF · G5-01 PASS |
| base-11 | 管理员跨账看花销 | `get_current_account` / `list_accessible_accounts` → 明确 `get_ledger_context({account_id})` → `get_period_summary` / `get_category_breakdown`；需要目标账本 `admin_spending` 授权 | 授权与安全、总览 | PF · G5-01 PASS |
| base-12 | 项目预算及指定流水 | `apply_actions` 原子设置项目预算并关联指定流水，或依次 `upsert_project_budget` → `link_project_transaction` → `get_project_budget` | 项目预算与目标 | PL · G5-02 PASS |
| base-13 | 项目结项 | `get_project_budget` → `close_project` | 项目预算与目标 | PL · G5-02 PASS |
| base-14 | 储蓄目标 | `upsert_saving_goal` → `record_saving_goal_change` → `get_saving_goal_progress` | 项目预算与目标 | PL · G5-02 PASS |
| base-15 | 报销垫付与回收 | `record_reimbursement_advance` → `record_reimbursement_receipt` → `list_pending_reimbursements` | 支付与往来 | MV · G5-02 PASS |
| base-16 | 周期账单 | `create_recurring_rule` → `process_due_recurring`；用 `confirm_recurring_transaction` 更新原待确认流水 | 定期提醒 | RR · G5-02 PASS |
| base-17 | 确认待扣续费 | `list_recurring_rules` 定位规则 → `confirm_recurring_transaction` 更新同一待确认流水；核对该账期仅一笔 | 定期提醒 | RR · G5-02 PASS |
| base-18 | 提醒 | `get_reminder_settings`、`list_reminders`、`refresh_reminders`、`mark_reminder_read` | 定期提醒 | RR · G5-02 PASS |
| base-19 | 脱敏导出 | `preview_ledger_export` → 用户确认 → `confirm_ledger_export`；导出文件由本人网页下载 | 导出与维护 | RR · G5-02 PASS |
| base-20 | 备份、迁移与恢复 | `create_backup` → `preview_restore`；跨设备时逐本绑定目标账户后重新预览，正式恢复仅本机网页确认，恢复前自动备份 | 导出与维护 | PF · G5-02 PASS |

扩展需求：

| 编号 | 功能 | 最短动作顺序与限制 | 网页入口 | 执行包证据 / 独立验收阶段 |
|---|---|---|---|---|
| ext-01 | 交易模板 | `upsert_transaction_template` → `list_transaction_templates` → `add_transactions` | 流水与核账 | LE · G5-02 PASS |
| ext-02 | 金额表达式 | `evaluate_amount_expression`；先核对返回的分金额 | 流水与核账 | LE · G5-02 PASS |
| ext-03 | 暂存流水 | `upsert_transaction_draft` → `list_transaction_drafts` → `commit_transaction_draft` | 流水与核账 | LE · G5-02 PASS |
| ext-04 | 估算值转实账 | `list_estimated_transactions` → `confirm_estimated_transaction` | 流水与核账 | LE · G5-02 PASS |
| ext-05 | 流水拆分 | `set_transaction_splits` → `get_transaction` | 流水与核账 | LE · G5-02 PASS |
| ext-06 | 商家别名 | `upsert_merchant_alias` → `list_merchant_aliases`；查询用 `search_transactions` | 流水与核账 | LE · G5-02 PASS |
| ext-07 | 合并分类 | `preview_category_change` → 确认 → `apply_category_change` | 流水与核账 | LE · G5-02 PASS |
| ext-08 | 合并标签 | `preview_tag_merge` → 确认 → `apply_tag_merge` | 流水与核账 | LE · G5-02 PASS |
| ext-09 | 自动分类 | `upsert_classification_rule` → `preview_classification_rule` → `apply_classification` | 流水与核账 | LE · G5-02 PASS |
| ext-10 | 批量更改流水 | `preview_transaction_bulk_change` → 确认 → `apply_transaction_bulk_change` | 流水与核账 | LE · G5-02 PASS |
| ext-11 | 合并重复项 | `preview_duplicate_merge` → 确认 → `merge_duplicate_transactions` | 流水与核账 | LE · G5-02 PASS |
| ext-12 | 更正版本记录 | `update_transaction` → `list_transaction_revisions` | 流水与核账 | LE · G5-02 PASS |
| ext-13 | 保存查询条件 | `upsert_saved_transaction_filter` → `list_saved_transaction_filters` → `run_saved_transaction_filter` | 流水与核账 | LE · G5-02 PASS |
| ext-14 | 延续查询 | `search_transactions` → `continue_transaction_search`；继续使用服务返回的分页游标 | 流水与核账 | LE · G5-02 PASS |
| ext-15 | 月末清单 | `get_month_end_checklist` → `get_month_close_preview` | 项目预算与目标 | LE · G5-02 PASS |
| ext-16 | 日期口径 | `search_transactions`、`get_period_summary`；明确按发生日或入账日筛选 | 流水与核账、报表分析 | LE · G5-02 PASS |
| ext-17 | 支付账户 | `upsert_payment_account` → `list_payment_accounts` → `record_account_transaction` | 支付与往来 | MV · G5-02 PASS |
| ext-18 | 账户转账 | `transfer` → 按返回账户 ID 核对；支付子账户 ID 不是跨账本 account_id | 支付与往来 | MV · G5-02 PASS |
| ext-19 | 信用卡账单 | `get_credit_card_due` → `record_credit_card_repayment` | 支付与往来 | MV · G5-02 PASS |
| ext-20 | 退款 | `record_refund`；提供原交易和实际退款金额 | 支付与往来 | MV · G5-02 PASS |
| ext-21 | 个人分摊 | `record_personal_split` → `record_split_settlement` | 支付与往来 | MV · G5-02 PASS |
| ext-22 | 个人借贷 | `record_personal_loan` → `record_loan_repayment` → `list_personal_loans` | 支付与往来 | MV · G5-02 PASS |
| ext-23 | 项目预算 | `upsert_project_budget` → `link_project_transaction` → `get_project_spending` | 项目预算与目标 | PL · G5-02 PASS |
| ext-24 | 分类预算 | `upsert_monthly_category_budget` → `get_monthly_category_budgets` | 项目预算与目标 | PL · G5-02 PASS |
| ext-25 | 结转预算 | `carry_forward_category_budget` → `get_monthly_category_budgets` | 项目预算与目标 | PL · G5-02 PASS |
| ext-26 | 未来现金流 | `create_cashflow_plan` → `get_future_cashflow`；一次性计划不计作真实收支 | 定期提醒 | RR · G5-02 PASS |
| ext-27 | 订阅涨价和单期例外 | `update_subscription_price`、`set_recurring_exception` → `list_recurring_rules` | 定期提醒 | RR · G5-02 PASS |
| ext-28 | 固定款单期例外 | `set_recurring_exception` 对指定账期跳过或覆盖金额/日期 → `get_future_cashflow` 核对后续期间；后续账期不变 | 定期提醒 | RR · G5-02 PASS |
| ext-29 | 收入分类 | `set_income_classification` → `get_income_summary` | 报表分析 | PL · G5-02 PASS |
| ext-30 | 储蓄进度 | `list_saving_goals` → `get_saving_goal_progress` → 确认后 `record_saving_goal_change` | 项目预算与目标 | PL · G5-02 PASS |
| ext-31 | 目标关联转账 | `associate_saving_goal_transfer` → `get_saving_goal_progress` | 项目预算与目标 | PL · G5-02 PASS |
| ext-32 | 月结与重开 | `get_month_close_preview` → 确认后 `close_month`；重开需 `reopen_month` 和理由 | 项目预算与目标 | PL · G5-02 PASS |
| ext-33 | 财务周期 | `set_financial_cycle` → `get_financial_cycle` → `get_financial_period_summary` | 报表分析 | IN · step-03 verdict |
| ext-34 | 交叉报表 | `get_cross_tab_report` → 按返回参数 `get_report_drilldown`；商家维度需要明细权限 | 报表分析 | IN · step-03 verdict |
| ext-35 | 消费频次 | `get_purchase_frequency` → 按返回参数 `get_report_drilldown` | 报表分析 | IN · step-03 verdict |
| ext-36 | 支出归因 | `get_spending_variance` → 按返回参数 `get_report_drilldown` | 报表分析 | IN · step-03 verdict |
| ext-37 | 滚动趋势 | `get_rolling_spending_trend`；数据不足时显示样本不足 | 报表分析 | IN · step-03 verdict |
| ext-38 | 刚性与弹性支出 | `upsert_expense_view_rule` → `get_expense_style_summary` | 报表分析 | IN · step-03 verdict |
| ext-39 | 预算试算 | `create_budget_scenario_draft` → `update_budget_scenario_draft` → `get_budget_scenario_draft` | 报表分析 | IN · step-03 verdict |
| ext-40 | 月报模板 | `upsert_monthly_report_template` → `run_monthly_report` | 报表分析 | IN · step-03 verdict |
| ext-41 | 月报快照 | `save_monthly_report_snapshot` → `list_report_snapshots` → `compare_monthly_report_snapshot` | 报表分析 | IN · step-03 verdict |
| ext-42 | CSV 导入 | 本机选文件 → `preview_transaction_import` → 确认 → `commit_transaction_import`；封账期不能写入 | 导出与维护 | MT · step-03 verdict |
| ext-43 | 流水附件 | 选流水及本机文件 → `attach_transaction_file`；用 `list_transaction_files` 查找，附件内容仅在已登录本机网页 `get_transaction_attachment` 读取 | 导出与维护 | MT · step-03 verdict |
| ext-44 | 脱敏配置 | `upsert_redaction_profile` → `preview_ledger_export` → 确认 → `confirm_ledger_export` | 导出与维护 | MT · step-03 verdict |
| ext-45 | 账本访问记录 | `get_account_access_policy` → `list_security_access` 按日期和宿主查看本人账本的入/出向访问范围与安全审计 | 授权与安全 | MT · step-03 verdict |
| ext-46 | MCP 宿主权限管理 | `get_account_access_policy` → 本人网页 `list_host_tokens` → `set_host_token_scopes` 或 `revoke_host_token`；撤销影响下一次请求 | 授权与安全 | MT · step-03 verdict |
| ext-47 | 隔离恢复演练 | `backup_drill`；只检查隔离副本，不覆盖当前数据库 | 导出与维护 | MT · step-03 verdict |
| ext-48 | 一致性诊断 | `preview_maintenance_issues` → 针对单条指纹确认后 `repair_maintenance_issue` | 导出与维护 | MT · step-03 verdict |

首页分类饼图显示真实金额和占比，“其他”可用键盘展开；有明细权限时可按同一分类钻取流水，并与数表保持同数，空数据与零金额分别显示。授权与安全页可展开或关闭右下本机运行面板；该面板只读显示时间、来源、操作名、状态、安全错误码和请求编号，不记录金额、请求参数、备注、令牌或文件路径。

## 启动和停止

日常使用「余序」快捷方式或 `启动余序.cmd`。网页打开后，启动命令窗口自动关闭，本机服务在后台继续运行。右键 Windows 通知区域的余序图标，选择「打开网页」可再次进入，选择「退出余序」可停止后台服务。也可运行：

```powershell
py -3.12 -X utf8 -B F:/桌面/程序/yuxu/run_yuxu.py launch-web --background
```

后台服务可用 `run_yuxu.py stop` 停止。省略 `--background` 时，新启动的服务由当前终端持有。启动器发现已有服务会直接复用，并显示服务的拥有者。`--no-open` 可启动服务而不打开默认浏览器。

```powershell
py -3.12 -X utf8 -B F:/桌面/程序/yuxu/run_yuxu.py mcp
py -3.12 -X utf8 -B F:/桌面/程序/yuxu/run_yuxu.py serve
py -3.12 -X utf8 -B F:/桌面/程序/yuxu/run_yuxu.py status
py -3.12 -X utf8 -B F:/桌面/程序/yuxu/run_yuxu.py stop
```

MCP stdio 会按需启动同一服务。网页地址为 `http://127.0.0.1:8765/`。MCP 客户端应运行在 Python 3.12 环境，并使用已安装的官方 `mcp==2.3.0` SDK；当前方案不执行依赖安装。下面的宿主配置可从任意工作目录调用项目入口，不含令牌；请在 MCP 客户端受保护的进程环境中单独设置 `YUXU_TOKEN`，不要把令牌写入仓库或共享配置：

```json
{
  "mcpServers": {
    "yuxu": {
      "command": "py",
      "args": ["-3.12", "-X", "utf8", "-B", "F:/桌面/程序/yuxu/run_yuxu.py", "mcp"]
    }
  }
}
```

MCP 冷启动的服务由 MCP SDK 宿主持有，SDK 关闭时会回收服务进程，但账本数据库保留。需要网页在 MCP 关闭后继续在线时，先用「余序」快捷方式启动后台服务，再连接 MCP。

停止当前本机服务使用 `py -3.12 -X utf8 -B F:/桌面/程序/yuxu/run_yuxu.py stop`；重启网页服务使用 `launch-web`，重新建立 MCP 连接则运行上述 stdio 命令。单独关闭网页标签不会停止服务；运行状态和所有账本数据保存在 `%LOCALAPPDATA%/Yuxu`，恢复服务后继续使用同一数据。
