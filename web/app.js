(() => {
  'use strict';

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const state = {
    session: null,
    tools: new Set(),
    toolScopes: new Map(),
    toolCatalog: [],
    entityOptions: {},
    activeFeatureAction: null,
    featureGroupForAction: null,
    activeFeatureParams: null,
    pendingPreview: null,
    pendingRequests: new Map(),
    categories: [],
    accounts: [],
    targetAccountId: null,
    targetAccount: null,
    period: null,
    periodVersion: 0,
    selectedPeriodPreset: null,
    quickReportMode: null,
    quickReportResponse: null,
    quickReportVersion: 0,
    selectedCategoryId: null,
    calendarVersion: 0,
    calendarDetailVersion: 0,
    selectedCalendarDate: null,
    annualCalendar: null,
    insightViews: [],
    pendingReviewRef: null,
    pendingLocalWork: null,
    reviewLinkNotice: null,
    summary: null,
    breakdown: null,
    recent: [],
    nextCursor: null,
    listFilters: {},
    lastUndoableRequestId: null,
    activeView: 'overview',
    importDraft: null,
    csvMode: 'import',
    csvTemplates: [],
    selectedCsvTemplateId: null,
    reconciliationDraft: null,
    paymentAccounts: [],
    draftCopyContext: null,
    draftCopyNeedsFreshFields: false,
    draftCommitContext: null,
    backupSchedule: null,
    attachmentTransactions: [],
    attachmentObjectUrls: [],
    restoreBindingChoices: null
  };

  const featureGroups = {
    flow: {view: 'transactions', label: '流水与核账'},
    movements: {view: 'movements', label: '支付与往来'},
    planning: {view: 'planning', label: '项目预算与目标'},
    recurring: {view: 'recurring', label: '定期提醒'},
    insights: {view: 'reports', label: '报表分析'},
    maintenance: {view: 'maintenance', label: '导出与维护'},
    security: {view: 'security', label: '授权与安全'}
  };

  const adminSpendingActions = new Set([
    'get_period_summary', 'get_category_breakdown', 'get_spending_comparison',
    'get_insight_period_comparison', 'get_annual_spending_calendar', 'get_cashflow_summary'
  ]);
  const crossCategoryFilterActions = new Set([
    'get_period_summary', 'get_category_breakdown', 'get_spending_comparison',
    'get_insight_period_comparison', 'get_annual_spending_calendar'
  ]);
  const adminGoalActions = new Set(['list_saving_goals', 'get_saving_goal_progress']);
  const quickReportTools = {trend: 'get_rolling_spending_trend', compare: 'get_insight_period_comparison'};
  const pageCopy = {
    overview: ['本月总览', '查看所选期间的收支和最近流水'],
    transactions: ['流水与核账', '查找、更正和核对账本记录'],
    visualization: ['数据可视化', '查看支出走势、分类占比和不同时期的变化'],
    reports: ['报表分析', '查看期间汇总、交叉报表和月报'],
    movements: ['支付与往来', '登记和核对款项、应收应付'],
    planning: ['项目预算与目标', '管理项目、周期预算与目标进度'],
    recurring: ['定期提醒', '管理周期规则与到期提醒'],
    maintenance: ['导出与维护', '导出数据、管理备份和恢复'],
    security: ['授权与安全', '查看账本授权、访问记录与主机权限']
  };

  const directTools = new Set([
    'add_transactions', 'get_transaction', 'update_transaction', 'undo_operation',
    'list_categories', 'search_transactions', 'get_period_summary',
    'get_category_breakdown', 'get_report_drilldown', 'set_host_token_scopes',
    'revoke_host_token', 'repair_maintenance_issue', 'get_insight_period_comparison',
    'get_budget_period_progress', 'get_annual_spending_calendar', 'upsert_insight_view',
    'list_insight_views', 'delete_insight_view', 'get_local_review_link', 'resolve_local_review_link',
    'copy_transaction_to_draft', 'create_host_token', 'rotate_host_token',
    'get_payment_account_balance_check', 'record_payment_account_balance_check',
    'get_backup_schedule', 'set_backup_schedule', 'backup_drill',
    'list_csv_mapping_templates', 'upsert_csv_mapping_template', 'delete_csv_mapping_template',
    'preview_transaction_reconciliation', 'commit_transaction_reconciliation'
  ]);

  const previewCommitActions = {
    preview_category_change: 'apply_category_change',
    preview_tag_merge: 'apply_tag_merge',
    preview_transaction_bulk_change: 'apply_transaction_bulk_change',
    preview_duplicate_merge: 'merge_duplicate_transactions',
    preview_project_entry_link: 'link_project_entries',
    preview_ledger_export: 'confirm_ledger_export',
    preview_restore: 'restore_backup',
    get_month_close_preview: 'close_month',
    preview_category_merge: 'merge_categories',
    preview_transaction_merge: 'merge_transactions',
    preview_bulk_transaction_update: 'commit_bulk_transaction_update'
  };
  const pairedCommitTools = new Set(Object.values(previewCommitActions));
  const fileBackedActions = new Set([
    'stage_receipt_bookkeeping', 'get_bookkeeping_draft', 'list_bookkeeping_drafts', 'discard_bookkeeping_draft', 'commit_bookkeeping_draft',
    'get_ledger_export_preview', 'get_transaction_reconciliation',
    'preview_transaction_import', 'commit_transaction_import', 'attach_transaction_file',
    'list_transaction_files', 'get_transaction_attachment', 'remove_transaction_file'
  ]);
  const noReceiptActions = new Set([
    'authorize_private_transaction_fields', 'set_host_token_scopes', 'revoke_host_token',
    'create_host_token', 'rotate_host_token'
  ]);

  const fieldLabels = {
    name: '名称', description: '说明', query: '搜索内容', limit: '显示数量',
    kind: '收支方向', amount_minor: '金额（元）', target_amount_minor: '目标金额（元）',
    actual_amount_minor: '实际金额（元）', actual_balance_minor: '实测余额（元）', occurred_on: '发生日期', charged_on: '扣款日期',
    balance_date: '核对日期', headers: 'CSV 表头（每行一项）', interval_days: '备份间隔（天）', retention_count: '保留份数', enabled: '启用',
    start_date: '开始日期', end_date: '结束日期', deadline: '截止日期', period: '月份',
    period_type: '预算周期', category_id: '分类', source_category_id: '原分类',
    parent_category_id: '上级分类', target_category_id: '目标分类', tag_id: '标签', source_tag_id: '原标签',
    target_tag_id: '目标标签', project_id: '关联项目', budget_id: '预算',
    goal_id: '目标', transaction_id: '流水', transaction_ids: '选择流水',
    keep_transaction_id: '保留的流水', merge_transaction_ids: '并入的流水',
    movement_id: '往来记录', counterparty: '往来对象', payee: '收款方',
    payer: '付款方', merchant: '商家或项目', canonical_name: '统一名称',
    alias: '商家别名', tags: '标签', tags_add: '添加标签', tags_remove: '移除标签',
    note: '备注', reason: '原因', status: '状态', active: '启用', active_only: '仅显示启用项',
    confirmation_status: '确认状态', estimated: '估算金额', account_scope: '目标范围',
    expected_version: '当前版本号', version: '版本号', priority: '优先级',
    expected_date: '预计日期', plan_id: '计划收支', current_minor: '当前金额（元）', gap_minor: '距目标金额（元）',
    filters: '筛选条件', add_filters: '增加条件', remove_filters: '移除条件',
    patch: '要更正的字段', defaults: '默认值', fields: '待保存的信息',
    missing_fields: '仍缺少的信息', conditions: '匹配条件', splits: '分类拆分',
    periods: '对比期间', preview_id: '已生成的预览',
    rule_id: '规则', draft_id: '草稿', template_id: '模板', filter_id: '已保存筛选',
    backup_id: '备份', file_id: '文件', filename: '文件名称', include_inactive: '包含已停用项',
    ledger_bindings: '备份账本映射', source_ledger_id: '备份来源账本编号', target_account_id: '本机目标账本编号',
    source_account_name: '备份来源账本', target_account_name: '本机目标账本', source_ledgers: '备份中的账本', target_ledgers: '本机账本',
    include_voided: '包含已作废项', date_basis: '日期口径', page: '分页设置',
    after: '继续位置', amount_min_minor: '最低金额（元）', amount_max_minor: '最高金额（元）',
    row_count: '记录数量', expires_at: '预览有效期', account_id: '收支账户',
    expression: '金额算式', alias_name: '商家别名', source_tags: '来源标签', target_tag: '合并到标签',
    source_transaction_id: '要合并的流水', target_transaction_id: '保留的流水',
    source_expected_version: '来源流水版本', target_expected_version: '目标流水版本',
    changes: '更正内容', mode: '筛选方式', exclude_category_id: '排除分类', exclude_tag: '排除标签',
    exclude_keyword: '排除关键词', exclude_merchant: '排除商家', classification: '收支类型',
    rule_kind: '规则类型', interval_months: '间隔（月）', due_day: '每月扣款日',
    first_due_date: '首次到期日', posting_mode: '入账方式', next_due_date: '下次到期日',
    annualized_cost_minor: '预计年支出（元）', through_date: '处理至', actual_date: '实际日期',
    actual_paid_minor: '实际支出（元）', remaining_minor: '剩余（元）', carry_in_minor: '上期结转（元）',
    personal_share_minor: '本人承担（元）', owed_minor: '对方应付（元）', participants: '参与人',
    participant_name: '参与人', source_account_id: '转出账户', destination_account_id: '转入账户',
    payment_account_id: '收支账户', account_type: '账户类型', currency: '币种',
    opening_balance_minor: '期初余额（元）', statement_day: '账单日', due_day: '还款日',
    reimbursement_id: '报销记录', split_id: '共同支出记录', loan_id: '借还记录',
    direction: '借贷方向', refund_amount_minor: '退款金额（元）', effective_date: '生效日期',
    month: '月份', from_month: '来源月份', to_month: '目标月份', target_minor: '目标金额（元）',
    budget_minor: '预算金额（元）', budget_items: '分类预算', transfer_id: '转账记录',
    savings_account_id: '储蓄账户', as_of: '统计日期', pace_months: '预计月数',
    include_notes: '导出备注', format: '导出格式', confirmation: '恢复确认',
    account_name: '账本名称', transaction_count: '流水数量', balance_minor: '账本余额（元）',
    reference: '要查找的记录', tag: '标签', keyword: '关键词', note_keyword: '备注关键词',
    action: '操作', reminder_id: '提醒', id: '编号', type: '类型', chart: '图表类型',
    date: '日期', category: '分类', amount: '金额', year: '年份',
    event_id: '事件编号', new_amount_minor: '更正后的金额（元）', target_request_id: '操作回执编号',
    inline: '在支持的客户端中直接显示图片', report_type: '报告类型',
    annualized_subscriptions: '订阅年支出', start: '起始位置', end: '结束位置', fields: '导出字段',
    state: '当前状态',
    start_day: '账期起始日', dimensions: '分析维度', group_values: '钻取条件',
    window_days: '滚动窗口天数', match_type: '匹配类型', match_value: '匹配值',
    classification: '支出属性', delta_minor: '金额调整（元）', assumptions: '预算假设',
    metrics: '月报指标', snapshot_id: '月报快照',
    date_format: '日期格式', amount_unit: '金额单位', duplicate_policy: '重复流水处理',
    column_mapping: 'CSV 字段映射', file_name: '文件名称', mime_type: '文件类型',
    content_base64: '文件内容', attachment_id: '流水附件', profile_id: '脱敏方案', redaction_profile_id: '数据脱敏方案',
    preset_id: '导出脱敏预设', hide_merchant: '隐藏商家', hide_note: '隐藏备注',
    hide_exact_date: '隐藏具体日期', merchant_policy: '商家脱敏方式', note_policy: '备注脱敏方式',
    date_precision: '日期精度', token_id: 'MCP 主机', merchant_ref: '已核对商家来源', scopes: '允许的权限',
    issue_id: '问题编号', issue_fingerprint: '问题指纹', resolution: '修复方式',
    confirmed: '已确认', display_name: '显示名称', incoming: '他人进入本人账本',
    outgoing: '本人进入他人账本', request_id: '请求编号', snapshot: '快照',
    group_by: '分组方式', expense_style: '支出属性',
    current_start_date: '本期开始日期', current_end_date: '本期结束日期',
    comparison_start_date: '对比期开始日期', comparison_end_date: '对比期结束日期',
    current_expense_minor: '本期支出（元）', comparison_expense_minor: '对比期支出（元）',
    change_minor: '支出变化（元）', change_percent: '变化比例', pending_record_count: '待确认流水数',
    pending_transaction_count: '待确认笔数', current_pending_count: '本期待确认笔数', comparison_pending_count: '对照期待确认笔数',
    current_estimated_count: '本期估算笔数', comparison_estimated_count: '对照期估算笔数', estimated_count: '估算笔数', actual_minor: '已确认支出（元）',
    rolling_average_minor: '滚动平均（元）', coverage_days: '有记录天数',
    enabled_kinds: '提醒类型', budget_low_percent: '预算偏低提醒阈值（%）',
    anomaly_min_samples: '异常提示所需历史样本数', anomaly_multiplier_milli: '异常倍数阈值（千分值，如 2500 表示 2.5 倍）',
    goal_risk_percent: '目标延期风险阈值（%）', renewal_notice_days: '续费提前提醒天数',
    unbudgeted_actual_minor: '未设预算分类支出（元）', net_spend_minor: '扣除已报销后（元）',
    advance_minor: '已垫付（元）', received_minor: '已到账报销（元）', net_reimbursable_minor: '尚待报销（元）',
    records: '记录', source: '来源', target: '目标', label: '期间名称',
    project_budget: '项目预算', saving_goal: '储蓄目标', confirmed_goal_progress: '目标进度记录',
    applied_actions: '已提交的计划操作', results: '操作结果',
    refund_minor: '退款（元）', reimbursement_receipt_minor: '报销到账（元）', pending_count: '待确认笔数',
    pending_expense_minor: '待确认支出（元）', adjustment_count: '资金调整笔数', transfer_minor: '转账（元）',
    adjustment_income_minor: '调整收入（元）', adjustment_expense_minor: '调整支出（元）', cashflow_net_minor: '现金流净额（元）',
    total_income_minor: '收入合计（元）', total_expense_minor: '支出合计（元）', record_count: '流水数量',
    net_increase_minor: '期间净增加（元）', average_monthly_increase_minor: '平均月增加（元）',
    required_monthly_increase_minor: '所需月增加（元）', months_remaining: '剩余月数',
    estimated_completion_date: '预计完成日期', due_date: '目标日期', original_names: '原始商家名称',
    adjustment_type: '资金调整类型', related_transaction_id: '关联流水'
  };

  const statusLabels = {
    ok: '完成', success: '完成', committed: '已提交', error: '未完成',
    open: '进行中', active: '启用', closed: '已结束', completed: '已完成',
    paused: '已暂停', pending: '待确认', archived: '已归档', cancelled: '已取消',
    confirmed: '已确认', read: '已读', unread: '未读', voided: '已作废',
    pending_confirmation: '待确认', overdue: '已逾期', upcoming: '即将到期',
    planned: '已计划', skipped: '已跳过', partial: '部分到账', received: '已到账',
    all: '全部', ambiguous: '存在多个候选', not_found: '未找到',
    resolved: '已找到', expired: '已过期', invalid: '无效',
    created: '已创建', passed: '通过', failed: '未通过', installed: '已导入',
    allowed: '允许', denied: '拒绝', unknown: '结果未确定',
    ready: '就绪', running: '运行中', setup_required: '待注册', stopped: '已停止',
    due: '已到期', not_due: '尚未到期', disabled: '已停用'
  };
  const sourceLabels = {web: '本机网页', mcp: 'MCP 主机', cli: '命令行', system: '系统'};
  const reminderKindLabels = {
    budget_low: '预算余量偏低', abnormal_spending: '支出异常',
    goal_risk: '储蓄目标风险', subscription_renewal: '订阅续费'
  };
  const movementTypeLabels = {
    transaction_payment: '流水收付', transfer: '账户转账', credit_card_repayment: '信用卡还款',
    refund: '退款', reimbursement_receipt: '报销到账', split_settlement: '共同支出结算',
    loan_principal: '借款本金', loan_repayment: '借款还款'
  };
  const hostPermissionLabels = {
    summary_read: '摘要只读', detail_read: '明细只读', write: '允许写入', export_summary: '生成本机导出', export_artifact: '领取脱敏图片',
    export_summary: '允许导出摘要', export_artifact: '允许生成导出文件'
  };
  const maintenanceIssueLabels = {
    orphan_split: '共同支出已失去关联的已确认流水',
    split_allocation_mismatch: '共同分摊金额与关联支出不一致',
    split_settlement_overrun: '参与人的已结算金额超过应付金额',
    orphan_refund: '退款已失去关联的原始支出',
    refund_overrun: '退款合计超过原始支出',
    orphan_reimbursement: '报销已失去关联的原始支出',
    reimbursement_overrun: '报销垫付超过原始支出',
    orphan_project_entry: '项目条目缺少有效的关联支出',
    orphan_attachment: '附件已失去关联的流水'
  };
  const enumLabels = {
    kind: {income: '收入', expense: '支出', transfer: '转账', duplicate: '重复', possible: '疑似重复', ...reminderKindLabels},
    confirmation_status: {confirmed: '已确认', pending: '待确认'},
    account_scope: {current: '当前账本', all_authorized: '已授权范围'},
    period_type: {week: '每周', month: '每月', quarter: '每季度', year: '每年'},
    status: statusLabels,
    state: statusLabels,
    outcome: statusLabels,
    rule_kind: {fixed: '固定支出', subscription: '订阅'},
    posting_mode: {pending: '先待确认', auto: '自动入账'},
    mode: {merge: '合并条件', replace: '替换条件', and: '缩小结果', exclude: '排除结果'},
    action: {skip: '本期跳过', override: '调整本期', add: '新增流水', update: '更正流水', void: '作废流水', undo: '撤销操作', merge: '合并流水'},
    direction: {lend: '借出', borrow: '借入'},
    classification: {recurring: '固定收入', one_off: '一次性收入', fixed: '刚性支出', flexible: '弹性支出', unclassified: '未标记', all: '全部'},
    reference: {last_transaction: '最近一笔流水', last_operation: '最近一次操作'},
    chart: {pie: '饼图', doughnut: '环形图', bar: '柱状图', line: '折线图', table: '表格'},
    source: sourceLabels,
    source_type: {recurring_rule: '定期规则', one_off_plan: '一次性计划', manual: '手动登记', transfer: '转账'},
    event_type: movementTypeLabels,
    adjustment_type: movementTypeLabels,
    classification_status: {classified: '已分类', unclassified: '未分类'},
    expense_style: {fixed: '刚性支出', flexible: '弹性支出', unclassified: '未标记'},
    unavailable_reason: {financial_cycle_not_configured: '尚未设置发薪账期'},
    match: {exact: '完全匹配', possible: '疑似匹配'},
    integrity_check: {ok: '通过'},
    format: {csv: 'CSV 表格', xlsx: 'Excel 工作簿', png: 'PNG 图片', pdf: 'PDF 文档'},
    report_type: {monthly: '月度报表', receipt: '记账回执'},
    delivery: {web: '本机网页'},
    date_basis: {occurred_on: '消费日期', charged_on: '扣款日期'},
    dimensions: {month: '月份', merchant: '商家', category: '分类', project: '项目'},
    group_by: {merchant: '商家', category: '分类'},
    match_type: {category: '分类', merchant: '商家'},
    duplicate_policy: {skip: '跳过重复项', include: '保留重复项'},
    amount_unit: {yuan: '元', fen: '分'},
    date_format: {iso: '年-月-日', slash: '斜线日期', compact: '紧凑数字日期'},
    merchant_policy: {keep: '保留', mask: '部分隐藏', omit: '隐藏'},
    note_policy: {keep: '保留', mask: '部分隐藏', omit: '隐藏'},
    date_precision: {day: '保留日期', month: '仅保留月份', year: '仅保留年份', omit: '隐藏日期'},
    type: {
      category: '分类', tag: '标签',
      net_cash_flow: '净现金流', expense_category: '分类支出', saving_goal_progress: '储蓄目标进度', expense_style: '支出属性',
      sync_split_total: '同步拆分合计', reallocate_split: '重新分配拆分', set_refund_amount: '更正退款金额',
      remove_refund_event: '移除退款记录', remove_orphan_project_entry: '移除失效的项目关联',
      remove_orphan_split: '移除失效的共同支出记录', set_split_settlement_amount: '更正分摊结算金额',
      remove_split_settlement: '移除分摊结算记录', remove_orphan_refund: '移除失效的退款记录',
      remove_orphan_reimbursement: '移除失效的报销记录', set_reimbursement_amount: '更正报销金额',
      remove_orphan_attachment: '移除失效的附件',
      ...maintenanceIssueLabels
    },
    scopes: hostPermissionLabels,
    enabled_kinds: reminderKindLabels,
    fields: {merchant: '商家名称', note: '备注'},
    account_type: {cash: '现金', bank: '银行卡', e_wallet: '电子钱包', credit_card: '信用卡'},
  };

  function localDate(value = new Date()) {
    const year = value.getFullYear();
    const month = String(value.getMonth() + 1).padStart(2, '0'); // taste-skill-ignore: ISO date fields require two digits.
    const day = String(value.getDate()).padStart(2, '0'); // taste-skill-ignore: ISO date fields require two digits.
    return year + '-' + month + '-' + day;
  }

  function currentMonthRange() {
    const now = new Date();
    return {start_date: localDate(new Date(now.getFullYear(), now.getMonth(), 1)), end_date: localDate(now)};
  }

  function showsCurrentMonth(period) {
    const range = currentMonthRange();
    return period.start_date === range.start_date && period.end_date === range.end_date;
  }

  function periodPresets(now = new Date()) {
    const year = now.getFullYear();
    const month = now.getMonth();
    const day = now.getDate();
    const today = localDate(now);
    return {
      month: {start_date: localDate(new Date(year, month, 1)), end_date: today},
      previous: {start_date: localDate(new Date(year, month - 1, 1)), end_date: localDate(new Date(year, month, 0))},
      days7: {start_date: localDate(new Date(year, month, day - 6)), end_date: today},
      days30: {start_date: localDate(new Date(year, month, day - 29)), end_date: today},
      year: {start_date: localDate(new Date(year, 0, 1)), end_date: today}
    };
  }

  function syncPeriodPresets(period) {
    const matches = Object.entries(periodPresets()).filter(([, range]) =>
      range.start_date === period.start_date && range.end_date === period.end_date
    );
    const current = matches.find(([name]) => name === state.selectedPeriodPreset) || matches[0];
    state.selectedPeriodPreset = current ? current[0] : null;
    for (const button of $$('#periodPresets button')) {
      button.setAttribute('aria-pressed', String(button.dataset.periodPreset === state.selectedPeriodPreset));
    }
  }

  function setMessage(node, message, isError = false) {
    node.textContent = message || '';
    node.classList.toggle('is-error', isError);
  }

  function chineseText(value) {
    return typeof value === 'string' && /[\u3400-\u9fff]/u.test(value) ? value : '';
  }

  const errorMessages = {
    unauthorized: '登录已失效，请重新登录。',
    session_expired: '登录已失效，请重新登录。',
    permission_denied: '当前账号没有此项操作权限。',
    forbidden: '当前账号没有此项操作权限。',
    invalid_input: '填写内容未通过校验，请检查日期、金额和必填项目。',
    conflict: '记录已发生变化，请重新读取后再操作。',
    version_conflict: '记录已发生变化，请重新读取最新内容后再操作。',
    validation_error: '填写内容未通过校验，请检查日期、金额和必填项目。',
    invalid_request: '填写内容未通过校验，请检查日期、金额和必填项目。',
    not_found: '目标记录不存在或当前不可见，请重新读取。',
    preview_expired: '预览已过期，请重新生成预览后再确认。',
    period_closed: '该月份已结算，请先重新打开该月份。',
    month_review_required: '请先处理未确认或未分类项目，再关闭月份。',
    needs_clarification: '此操作还需要补充信息，请核对填写项后重试。',
    internal_error: '本机服务暂时无法完成此次操作。',
    maintenance_scope_required: '当前账号缺少本机维护权限。',
    maintenance_scope_revoked: '本机维护权限已撤销。',
    cross_account_forbidden: '当前账号没有访问所选账本的权限。',
    host_permission_required: '此主机缺少操作所需权限。',
    csrf_failed: '登录会话校验失败，请重新登录。',
    invalid_credentials: '用户名或密码不正确。',
    setup_required: '请先注册本机账号。',
    idempotency_conflict: '此请求编号已用于另一项操作。',
    unknown_action: '此操作暂不可用。'
  };

  const serviceMessageLabels = {
    'A trusted conversation ID is required to continue the last search': '当前没有可接续的会话，请先查询一次流水。',
    'A trusted conversation ID is required to resolve a recent reference': '当前没有可用于查找最近记录的会话，请按日期或金额查找流水。',
    'There is no previous search in this conversation': '当前会话还没有查找过流水。',
    'A voided transaction cannot be changed': '这笔流水已作废，无法修改。',
    'This operation has already been undone': '这项操作已经撤销。',
    'This preview has expired; create a new preview': '预览已过期，请重新生成预览。',
    'This preview has already been applied': '这项预览已确认执行。',
    'A transaction changed after preview; create a new preview': '流水在预览后发生变化，请重新生成预览。',
    'The payment account does not have enough available balance': '所选账户可用余额不足。',
    'The source account does not have enough available balance': '转出账户可用余额不足。',
    'The refund exceeds the remaining amount of the original expense': '退款金额超过原支出可退的余额。',
    'The receipt exceeds the outstanding reimbursement amount': '到账金额超过待报销金额。',
    'The repayment exceeds the current credit card balance': '还款金额超过信用卡当前欠款。',
    'The repayment exceeds the outstanding loan principal': '还款金额超过剩余待还本金。',
    'Split amounts must sum to the transaction amount': '各分类拆分金额合计必须等于流水金额。',
    'Use either month or an explicit period filter': '请在月份和起止日期中选择一种筛选方式。',
    'start_date and end_date must be supplied together': '请同时填写开始日期和结束日期。',
    'start_date must be on or before end_date': '开始日期不能晚于结束日期。',
    'start_date must not be after end_date': '开始日期不能晚于结束日期。',
    'Only PDF, JPEG, PNG, and WebP attachments are supported': '附件支持 PDF、JPEG、PNG 和 WebP 格式。',
    'Attachment content does not match the selected file type': '附件内容与选中的文件类型不一致。',
    'CSV must be valid UTF-8 with well-formed rows': 'CSV 文件应使用 UTF-8 编码，并且每行格式正确。',
    'CSV must have a header row with non-empty column names': 'CSV 文件应包含表头，每列都需要列名。',
    'CSV column names must be unique': 'CSV 表头中的列名不能重复。',
    'row has a different number of columns than the header': '此行的列数与表头不一致。',
    'date is outside the selected reconciliation window': '日期不在所选对账范围内。',
    'date must match the selected format': '日期与所选格式不符。',
    'date must match YYYY-MM-DD': '日期应使用“年-月-日”格式。',
    'amount must be a non-negative integer number of fen': '以分为单位的金额应填写整数。',
    'amount must be a number in yuan': '金额应填写以元为单位的数字。',
    'amount in yuan must be positive and no more precise than one fen': '金额应大于零，并最多保留两位小数。',
    'amount is outside the supported integer-fen range': '金额超出允许的范围。',
    'kind must be income/收入 or expense/支出': '收支方向应填写“收入”或“支出”。',
    'Import preview expired; create a new preview': '导入预览已过期，请重新生成。',
    'Reconciliation preview expired; create a new preview': '对账预览已过期，请重新生成。',
    'Ledger transactions changed after the preview; review a new preview': '账本流水在预览后发生变化，请重新生成预览。'
  };

  function errorText(error, fallback = '本机服务未完成此次操作，请重试。') {
    const message = error?.message;
    return serviceMessageLabels[message] || chineseText(message) ||
      errorMessages[error?.code] || errorMessages[error?.status] || fallback;
  }

  function reviewLinkErrorText(error) {
    if (error && error.code === 'review_link_expired') return '链接已过期，请重新生成。';
    if (error && ['review_link_not_found', 'invalid_review_ref', 'not_found', 'forbidden', 'cross_account_forbidden'].includes(error.code)) {
      return '链接不可用，请重新生成。';
    }
    return errorText(error, '无法打开这条本机查看链接。');
  }

  async function apiRequest(path, options = {}) {
    const headers = new Headers({Accept: 'application/json'});
    if (options.body !== undefined) headers.set('Content-Type', 'application/json');
    if (options.csrf) headers.set('X-CSRF-Token', options.csrf);
    let response;
    try {
      response = await fetch(path, {
        method: options.method || 'GET',
        credentials: 'same-origin',
        headers,
        body: options.body === undefined ? undefined : JSON.stringify(options.body)
      });
    } catch {
      throw new Error('无法连接余序本机服务。请确认服务已启动后重试。');
    }
    let envelope;
    try {
      envelope = await response.json();
    } catch {
      throw new Error('本机服务返回的数据无法读取，请重试。');
    }
    const success = envelope.status === 'ok' || envelope.status === 'committed';
    if (!response.ok || !success) {
      const message = envelope.error && envelope.error.message;
      const error = new Error(message || '请求未完成，请检查服务状态后重试。');
      error.status = envelope.status || response.status;
      error.code = envelope.error && envelope.error.code || envelope.code || envelope.error_code;
      error.envelope = envelope;
      throw error;
    }
    return envelope;
  }

  function envelopeData(envelope) {
    return envelope.data || {};
  }

  function sessionFrom(envelope) {
    return Object.assign({}, envelope, envelopeData(envelope));
  }

  function isCrossAccount() {
    return Boolean(state.session && state.targetAccountId && String(state.targetAccountId) !== String(state.session.account_id));
  }

  function accountScopes(account) {
    if (Array.isArray(account && account.scopes)) return new Set(account.scopes);
    return new Set(account && account.scope ? [account.scope] : []);
  }

  function targetHasScope(scope) {
    return accountScopes(state.targetAccount).has(scope);
  }

  function crossToolAllowed(tool) {
    if (!isCrossAccount()) return true;
    if (!tool || !targetHasScope(tool.scope)) return false;
    if (tool.scope === 'admin_spending') return adminSpendingActions.has(tool.name);
    if (tool.scope === 'admin_goal') return adminGoalActions.has(tool.name);
    return false;
  }

  function crossFieldAllowed(key) {
    return !isCrossAccount() || !['account_id', 'project_id', 'transaction_id', 'transaction_ids', 'merchant', 'keyword', 'tag', 'tags', 'note', 'filters'].includes(key);
  }

  function assertAccount(envelope, expectedAccountId = state.targetAccountId || (state.session && state.session.account_id), envelopeOnly = false) {
    const returnedIds = (envelopeOnly ? [envelope.account_id] : [
      envelope.account_id,
      envelopeData(envelope).account_id,
      envelopeData(envelope).ledger && envelopeData(envelope).ledger.account_id
    ]).filter(value => value !== undefined && value !== null);
    if (expectedAccountId && returnedIds.some(value => String(value) !== String(expectedAccountId))) {
      throw new Error('服务返回了与当前会话不一致的账本数据，本页未显示该结果。');
    }
  }

  function hasTool(name) {
    return state.tools.has(name);
  }

  function groupForTool(tool) {
    const name = String(tool.name || '').toLowerCase();
    const hint = String(tool.description || '').toLowerCase();
    const declared = String(tool.group || tool.feature_group || tool.area || '').toLowerCase();
    if (/account_access_policy|security_access|host_token|accessible_accounts|access_policy|runtime_log|authorize_private_transaction_fields|local_review_link/.test(name)) return 'security';
    if (/financial_cycle|cross_tab|report_drilldown|purchase_frequency|spending_(?:variance|comparison)|rolling_spending|expense_(?:style|view_rule)|budget_scenario|monthly_report|report_snapshots|list_report_snapshots|period_summary|category_breakdown|cashflow_summary|merchant_breakdown|project_spending|insight_(?:period_comparison|view)|annual_spending_calendar|budget_period_progress/.test(name)) return 'insights';
    const groupAliases = {ledger: 'flow', transactions: 'flow', classification: 'flow', payment: 'movements', payments: 'movements', budgeting: 'planning', goals: 'planning', reminders: 'recurring', files: 'maintenance', reporting: 'insights', analysis: 'insights', security: 'security', authorization: 'security', audit: 'security'};
    if (featureGroups[declared]) return declared;
    if (groupAliases[declared]) return groupAliases[declared];
    const patterns = [
      ['security', /account_access_policy|security_access|host_token|accessible_accounts|access_policy|runtime_log|authorize_private_transaction_fields|local_review_link/],
      ['insights', /financial_cycle|cross_tab|report_drilldown|purchase_frequency|spending_(?:variance|comparison)|rolling_spending|expense_(?:style|view_rule)|budget_scenario|monthly_report|report_snapshots|list_report_snapshots|period_summary|category_breakdown|cashflow_summary|merchant_breakdown|project_spending|insight_(?:period_comparison|view)|annual_spending_calendar|budget_period_progress/],
      ['recurring', /recurr|remind|schedule|periodic|future_cashflow|cashflow_plan/],
      ['maintenance', /export|backup|restore|file|maintenance|archive|redaction_profile/],
      ['movements', /payment_account|credit_card|refund|reimburse|personal_split|split_settlement|personal_loan|loan_repayment|(^|_)transfer|account_transaction|movement|payable|receivable|settlement|invoice|debt|loan/],
      ['planning', /project|budget|goal|saving|settle_month|reopen_month|close_month|month_close|income_|apply_actions/],
      ['flow', /transaction|ledger|reconcil|classification|template|expression|draft|estimated|split|merchant|categor|(^|_)tag|saved_filter|reference|history|bulk|statement|month_end|cashflow/]
    ];
    for (const [group, pattern] of patterns) if (pattern.test(name)) return group;
    for (const [group, pattern] of patterns) if (pattern.test(hint)) return group;
    return null;
  }

  function featureTools(view) {
    const group = Object.keys(featureGroups).find(key => featureGroups[key].view === view) || view;
    return state.toolCatalog.filter(tool => {
      if (groupForTool(tool) !== group || directTools.has(tool.name) || pairedCommitTools.has(tool.name) || fileBackedActions.has(tool.name) || !crossToolAllowed(tool)) return false;
      const commitName = previewCommitActions[tool.name];
      if (commitName) {
        const commit = state.toolCatalog.find(item => item.name === commitName);
        return Boolean(commit && commit.scope === 'own_write' && !isCrossAccount());
      }
      return true;
    });
  }

  function groupHasTools(view) {
    return featureTools(view).length > 0;
  }

  function groupHasReadTools(view) {
    const group = Object.keys(featureGroups).find(key => featureGroups[key].view === view) || view;
    return state.toolCatalog.some(tool => groupForTool(tool) === group && crossToolAllowed(tool));
  }

  const actionLabels = {
    get_current_account: '查看当前账号',
    list_accessible_accounts: '查看可访问的账本',
    get_ledger_context: '查看账本与权限',
    get_workflow_guide: '查看记账操作指南',
    add_transactions: '记录流水',
    get_transaction: '查看流水详情',
    update_transaction: '更正流水',
    void_transaction: '作废流水',
    undo_operation: '撤销上次操作',
    resolve_reference: '查找本会话的最近记录',
    list_categories: '查看流水分类',
    list_tags: '查看流水标签',
    search_transactions: '查找流水',
    apply_classification: '更改流水分类与标签',
    get_period_summary: '查看期间收支',
    get_category_breakdown: '查看分类支出',
    get_request_status: '查询操作回执',
    apply_tag_merge: '确认整理标签',
    backup_upload: '上传备份',
    settings_update: '更新安全设置',
    unknown_action: '未知操作',
    get_ledger_export_preview: '查看已有导出预览',
    get_export_artifact: '查找导出文件',
    export_report_card: '导出月报或记账回执',
    list_transaction_templates: '查看记账模板',
    upsert_transaction_template: '保存记账模板',
    delete_transaction_template: '删除记账模板',
    calculate_amount_expression: '试算金额算式',
    save_transaction_draft: '保存流水草稿',
    list_transaction_drafts: '查看流水草稿',
    update_transaction_draft: '更正流水草稿',
    delete_transaction_draft: '删除流水草稿',
    commit_transaction_draft: '确认草稿并记账',
    list_estimated_transactions: '查看估算流水',
    review_estimated_transaction: '确认实际金额',
    set_transaction_splits: '拆分流水分类',
    list_merchant_aliases: '查看商家别名',
    upsert_merchant_alias: '添加商家别名',
    delete_merchant_alias: '删除商家别名',
    preview_category_merge: '预览分类合并',
    merge_categories: '确认合并分类',
    archive_category: '停用分类',
    preview_tag_merge: '预览标签合并',
    merge_tags: '确认合并标签',
    delete_unused_tag: '删除未使用标签',
    list_classification_rules: '查看自动分类规则',
    preview_classification_rule: '试跑分类规则',
    upsert_classification_rule: '保存分类规则',
    set_classification_rule_priority: '调整规则顺序',
    delete_classification_rule: '删除分类规则',
    preview_bulk_transaction_update: '预览批量更正',
    commit_bulk_transaction_update: '确认批量更正',
    preview_transaction_merge: '预览流水合并',
    merge_transactions: '确认合并流水',
    get_transaction_history: '查看流水更正历史',
    list_saved_transaction_filters: '查看已存筛选',
    save_transaction_filter: '保存当前筛选',
    delete_transaction_filter: '删除已存筛选',
    continue_transaction_search: '追加或缩小筛选',
    get_month_end_review: '查看月末核账',
    create_project: '新建项目',
    list_projects: '查看项目',
    update_project: '更正项目',
    close_project: '结束项目',
    preview_project_entry_link: '预览流水关联',
    link_project_entries: '确认关联流水',
    create_budget: '新建预算',
    update_budget: '更正预算',
    get_budget_status: '查看预算进度',
    list_budgets: '查看预算',
    create_goal: '新建储蓄目标',
    update_goal: '更正储蓄目标',
    record_goal_progress: '记录目标进度',
    get_goal_status: '查看目标进度',
    list_goals: '查看储蓄目标',
    list_saving_goals: '查看储蓄目标',
    get_savings_summary: '查看储蓄汇总',
    allocate_savings: '记录目标储蓄',
    settle_month: '月末结算',
    reopen_month: '重新打开月份',
    list_payment_accounts: '查看收支账户',
    get_payment_account_balance_check: '读取最近账户余额核对',
    record_payment_account_balance_check: '保存实测账户余额核对',
    copy_transaction_to_draft: '复制流水为新草稿',
    create_payment_account: '添加收支账户',
    record_account_transfer: '记录账户转账',
    get_credit_card_statement: '查看信用卡账单',
    record_credit_card_repayment: '记录信用卡还款',
    record_refund: '登记退款',
    record_reimbursement_advance: '登记报销垫付',
    record_reimbursement_receipt: '登记报销到账',
    list_pending_reimbursements: '查看待报销款项',
    record_aa_expense: '登记共同支出',
    record_aa_settlement: '登记共同支出结算',
    record_loan_principal: '登记借入或归还本金',
    evaluate_amount_expression: '试算金额算式',
    upsert_merchant_alias: '添加商家别名',
    list_merchant_aliases: '查看商家别名',
    set_transaction_splits: '拆分流水分类',
    list_estimated_transactions: '查看估算流水',
    deactivate_classification_rule: '停用自动分类规则',
    upsert_transaction_draft: '保存流水草稿',
    confirm_estimated_transaction: '确认实际金额',
    preview_category_change: '预览分类调整',
    create_category_or_tag: '新建分类或标签',
    apply_category_change: '确认分类调整',
    preview_transaction_bulk_change: '预览批量更正',
    apply_transaction_bulk_change: '确认批量更正',
    preview_duplicate_merge: '预览重复流水合并',
    merge_duplicate_transactions: '确认合并流水',
    list_transaction_revisions: '查看流水更正历史',
    upsert_saved_transaction_filter: '保存筛选条件',
    run_saved_transaction_filter: '运行已存筛选',
    get_spending_comparison: '比较期间收支',
    get_insight_period_comparison: '比较当前期间',
    get_budget_period_progress: '查看预算进度',
    get_annual_spending_calendar: '查看年度支出日历',
    upsert_insight_view: '保存常看视图',
    list_insight_views: '查看常看视图',
    delete_insight_view: '删除常看视图',
    get_local_review_link: '创建本机查看链接',
    resolve_local_review_link: '验证本机查看链接',
    get_month_end_checklist: '查看月末核账清单',
    get_cashflow_summary: '查看期间现金流',
    get_project_spending: '查看项目期间支出',
    get_merchant_breakdown: '查看商家支出构成',
    get_project_budget: '查看项目预算进度',
    list_project_budgets: '查看项目预算',
    upsert_project_budget: '设置项目预算',
    link_project_transaction: '关联项目流水',
    upsert_monthly_category_budget: '设置分类月预算',
    get_monthly_category_budgets: '查看本月预算',
    carry_forward_category_budget: '结转分类预算',
    upsert_saving_goal: '设置储蓄目标',
    record_saving_goal_change: '记录目标储蓄',
    associate_saving_goal_transfer: '关联目标转账',
    get_saving_goal_progress: '查看目标进度',
    set_income_classification: '标记收入类型',
    get_income_summary: '查看收入构成',
    get_month_close_preview: '预览月末结算',
    close_month: '确认月末结算',
    reopen_month: '重新打开月份',
    upsert_payment_account: '设置收支账户',
    record_account_transaction: '关联账户流水',
    transfer: '记录账户转账',
    get_payment_account_activity: '查看账户流水',
    get_credit_card_due: '查看信用卡账单',
    record_personal_split: '登记共同支出',
    record_split_settlement: '登记共同支出结算',
    record_personal_loan: '登记借入或借出',
    record_loan_repayment: '登记借还款',
    list_personal_loans: '查看借还往来',
    list_recurring_rules: '查看定期规则',
    create_recurring_rule: '新建定期规则',
    process_due_recurring: '处理到期规则',
    confirm_recurring_transaction: '确认定期流水',
    set_recurring_rule_state: '暂停或恢复定期规则',
    set_recurring_exception: '调整单次定期安排',
    update_subscription_price: '更新订阅价格',
    get_future_cashflow: '查看未来收支',
    create_cashflow_plan: '安排一次预计收支',
    list_cashflow_plans: '查看预计收支安排',
    cancel_cashflow_plan: '取消预计收支安排',
    get_reminder_settings: '查看提醒设置',
    set_reminder_settings: '调整提醒设置',
    refresh_reminders: '更新到期提醒',
    list_reminders: '查看到期提醒',
    mark_reminder_read: '标记提醒已读',
    preview_ledger_export: '预览账本导出',
    get_transaction_reconciliation: '查看对账进度',
    preview_bookkeeping: '预检记账条目',
    list_bookkeeping_drafts: '查看记账待办',
    get_bookkeeping_draft: '查看待办草稿',
    upsert_bookkeeping_draft: '保存记账草稿',
    discard_bookkeeping_draft: '丢弃记账草稿',
    commit_bookkeeping_draft: '确认草稿入账',
    stage_receipt_bookkeeping: '本机识别凭证并保存草稿',
    confirm_ledger_export: '确认导出并下载',
    create_backup: '创建备份',
    list_backups: '查看备份',
    preview_restore: '预览备份恢复',
    restore_backup: '确认恢复备份',
    apply_actions: '一次提交预算与目标变更',
    set_financial_cycle: '设置发薪账期',
    get_financial_cycle: '查看发薪账期',
    get_financial_period_summary: '查看自定义账期摘要',
    get_cross_tab_report: '按维度交叉分析',
    get_report_drilldown: '查看汇总来源流水',
    get_purchase_frequency: '查看消费频率与客单',
    get_spending_variance: '比较分类期间变化',
    get_rolling_spending_trend: '查看滚动支出走势',
    upsert_expense_view_rule: '设置刚性与弹性分类',
    list_expense_view_rules: '查看支出属性规则',
    get_expense_style_summary: '查看刚性与弹性支出',
    create_budget_scenario_draft: '新建预算方案草稿',
    update_budget_scenario_draft: '更正预算方案草稿',
    get_budget_scenario_draft: '查看预算方案测算',
    list_budget_scenario_drafts: '查看预算方案草稿',
    upsert_monthly_report_template: '保存月报模板',
    list_monthly_report_templates: '查看月报模板',
    run_monthly_report: '生成月报',
    save_monthly_report_snapshot: '保存月报快照',
    get_monthly_report_snapshot: '查看历史月报快照',
    list_report_snapshots: '查找月报快照',
    compare_monthly_report_snapshot: '对比快照与当前账本',
    preview_transaction_import: '生成 CSV 导入预览',
    commit_transaction_import: '确认 CSV 导入',
    preview_transaction_reconciliation: '生成 CSV 对账预览',
    commit_transaction_reconciliation: '确认入账 CSV 独有行',
    list_csv_mapping_templates: '查看 CSV 来源模板',
    upsert_csv_mapping_template: '保存 CSV 来源模板',
    delete_csv_mapping_template: '删除 CSV 来源模板',
    get_backup_schedule: '查看自动备份设置',
    set_backup_schedule: '保存自动备份设置',
    create_host_token: '创建 MCP 主机授权',
    rotate_host_token: '换发 MCP 主机密钥',
    attach_transaction_file: '添加流水附件',
    list_transaction_files: '查看流水附件',
    get_transaction_attachment: '打开流水附件',
    remove_transaction_file: '移除流水附件',
    list_export_presets: '查看隐私导出预设',
    upsert_export_preset: '保存隐私导出预设',
    upsert_redaction_profile: '保存数据脱敏方案',
    list_redaction_profiles: '查看数据脱敏方案',
    preview_redacted_export: '预览脱敏导出',
    list_security_access: '查看账本访问记录',
    get_account_access_policy: '查看账本授权范围',
    list_host_tokens: '查看 MCP 主机权限',
    set_host_token_scopes: '调整 MCP 主机权限',
    revoke_host_token: '撤销 MCP 主机权限',
    authorize_private_transaction_fields: '授予单次交易字段读取',
    backup_drill: '演练备份完整性',
    preview_maintenance_issues: '检查账本一致性',
    repair_maintenance_issue: '确认修复一致性问题'
  };

  const actionDescriptions = {
    list_accessible_accounts: '查看你可以进入的账本，以及每本账允许查看或修改的内容。',
    get_ledger_context: '查看所选账本及你的访问权限。留空时查看当前登录账号的账本。',
    void_transaction: '核对流水和当前版本后作废。作废后，这笔流水会从常规查询和报表中移除。',
    resolve_reference: '查找当前会话最近的一笔流水或一次操作。',
    list_tags: '查看当前账本已有的标签。',
    apply_classification: '为指定流水更改分类，或添加、移除标签。',
    get_spending_comparison: '用相同的筛选条件比较两个或更多期间的支出。',
    evaluate_amount_expression: '计算以元为单位的算式，将结果精确到分，供你记账前核对。',
    upsert_transaction_template: '保存常用的收支方向、分类、标签、商家或备注，记账时可重复使用。',
    list_transaction_templates: '查看你已保存的记账模板和各项默认值。',
    upsert_transaction_draft: '暂存尚未填完的流水，补齐信息后可以正式记账。',
    list_transaction_drafts: '查看尚未入账或已经入账的流水草稿。',
    commit_transaction_draft: '核对已补齐的草稿并记为一笔流水。',
    list_estimated_transactions: '查看所选期间内按估算金额记录的流水。',
    confirm_estimated_transaction: '用实际金额替换估算金额，并保留更正历史。',
    set_transaction_splits: '将一笔流水分配到两个或更多分类，各分类金额加起来应等于原金额。',
    upsert_merchant_alias: '设置商家的统一名称，便于汇总同一家商家的流水。',
    list_merchant_aliases: '查看商家别名及其对应的统一名称。',
    upsert_classification_rule: '设置自动归类的匹配条件、目标分类和优先级。',
    list_classification_rules: '按优先级查看账本中的自动分类规则。',
    preview_classification_rule: '查看现有流水中哪些记录符合分类规则。',
    deactivate_classification_rule: '停用指定规则，后续记账时跳过这条规则。',
    create_category_or_tag: '新建顶级分类、二级分类或标签。当前账本已有同名项时返回已有记录。',
    preview_category_change: '先查看合并或停用分类会影响哪些流水和规则。',
    preview_tag_merge: '先查看合并同义标签或清理未使用标签会影响哪些记录。',
    preview_transaction_bulk_change: '核对选中的流水以及每笔将要更改的分类或标签。',
    preview_duplicate_merge: '核对日期、方向和金额相同的两笔流水，以及合并时会保留的信息。',
    list_transaction_revisions: '查看一笔流水每次更正的前后内容、版本和操作来源。',
    upsert_saved_transaction_filter: '保存查询条件和日期口径，下次查账时可以直接使用。',
    list_saved_transaction_filters: '查看当前账本中已保存的查询条件。',
    run_saved_transaction_filter: '使用已保存的条件重新查询当前账本的流水。',
    continue_transaction_search: '在当前会话上次查询的基础上，追加、排除或替换筛选条件。',
    get_month_end_checklist: '检查当月未完成的草稿、估算、未分类流水、疑似重复和待确认扣款。',
    get_cashflow_summary: '按消费日期或扣款日期汇总现金流，并单列退款、报销和转账。',
    get_project_spending: '查看项目在所选期间的实际支出以及关联的资金调整。',
    get_merchant_breakdown: '按商家的统一名称汇总流水，并列出记录中使用的原始名称。',
    upsert_project_budget: '设置项目总预算，可同时分配分类预算并关联一笔支出。',
    link_project_transaction: '将一笔支出关联到进行中的项目。已确认的支出会计入项目预算。',
    get_project_budget: '查看项目预算、实际支出、剩余额度和单独统计的报销。',
    list_project_budgets: '查看当前账本各项目的预算和实付进度。',
    close_project: '结束项目并查看最终预算、实付金额、报销和分类汇总。',
    upsert_monthly_category_budget: '为某个月的指定分类设置预算，已确认的支出计入使用进度。',
    get_monthly_category_budgets: '查看所选月份的分类预算、结转金额、已用金额和余额。',
    carry_forward_category_budget: '将已结算月份的分类预算余额结转到下一个月。',
    set_income_classification: '将一笔已确认的收入标为固定收入或一次性收入。',
    get_income_summary: '按固定、一次性和未分类汇总收入，分开显示已确认与待确认金额。',
    upsert_saving_goal: '设置储蓄目标金额、截止日期和可选的储蓄账户。',
    record_saving_goal_change: '登记储蓄目标实际增加或减少的金额。',
    get_saving_goal_progress: '查看已确认金额、距目标差额、近期储蓄速度和预计完成日期。',
    list_saving_goals: '查看所选账本的目标名称和已确认进度。',
    associate_saving_goal_transfer: '将已完成的转账计入指定储蓄目标的进度。',
    get_month_close_preview: '结算前查看当月汇总、待确认流水和未分类流水。',
    reopen_month: '重新打开已结算的月份以便更正，并记录原因。',
    upsert_payment_account: '添加或编辑现金、银行卡、电子钱包或信用卡账户。创建时确定账户类型和期初余额。',
    list_payment_accounts: '查看启用中的收支账户及当前余额。',
    get_payment_account_activity: '查看所选账户在指定日期范围内的已确认流水和资金往来。',
    record_account_transaction: '将已确认的流水关联到实际收款或付款的账户，更新账面余额。',
    transfer: '记录本人两个资产账户之间的转账。',
    get_credit_card_due: '查看指定账期内的刷卡、退款、还款和未还余额。',
    record_credit_card_repayment: '记录从资产账户支付的信用卡还款。',
    record_refund: '为一笔已确认的支出登记部分或全额退款。',
    record_reimbursement_advance: '将现有支出的全部或一部分标为报销垫付。',
    record_reimbursement_receipt: '登记已到账的部分或全部报销款。',
    list_pending_reimbursements: '查看垫付金额、已到账金额和仍待报销的金额。',
    record_personal_split: '把一笔现有支出分为本人承担部分和各参与人应付金额。',
    record_split_settlement: '登记某位参与人归还的部分或全部垫付款。',
    record_personal_loan: '登记向他人借入或借出的本金。',
    record_loan_repayment: '为一笔借款登记部分还款或结清本金。',
    list_personal_loans: '查看借还往来的原始本金、已还金额和剩余本金。',
    create_recurring_rule: '为固定支出或订阅设置周期、金额、到期日和入账方式。',
    list_recurring_rules: '查看定期规则、下次到期日期、预计年支出及订阅价格记录。',
    process_due_recurring: '处理已经到期的固定付款和订阅。每项规则在同一期只会生成一笔流水。',
    confirm_recurring_transaction: '将一笔待确认的定期流水更新为实际入账。',
    set_recurring_rule_state: '暂停或恢复现有的固定支出或订阅规则。',
    set_recurring_exception: '跳过本期付款，或仅调整本期的付款日期与金额。',
    update_subscription_price: '从指定日期启用新价格，同时保留以前的价格记录。',
    get_future_cashflow: '按日期汇总未来的固定付款、订阅和一次性预计收支。',
    create_cashflow_plan: '为指定日期安排一笔预计收入或支出，供未来收支统计使用。',
    list_cashflow_plans: '查看已经安排的一次性预计收支。',
    cancel_cashflow_plan: '取消指定日期的一笔预计收支安排。',
    get_reminder_settings: '查看提醒类型、阈值和启用状态。',
    set_reminder_settings: '选择需要的提醒类型并设置阈值或提前天数。',
    refresh_reminders: '检查预算余量、异常支出、储蓄进度和即将续费的订阅。',
    list_reminders: '查看所选月份已生成的提醒。',
    mark_reminder_read: '将指定提醒标为已读。',
    list_export_presets: '查看当前账本已保存的隐私导出预设。',
    upsert_export_preset: '保存常用的导出字段和隐私设置。',
    preview_ledger_export: '在生成表格文件前，核对导出范围、字段和流水数量。',
    get_ledger_export_preview: '按预览编号查看仍有效的导出范围、字段和流水数量。',
    get_export_artifact: '按文件编号查看已导出的文件，并在本机网页下载。',
    export_report_card: '选择月份生成月报，或按操作回执编号生成记账回执，可保存为图片或 PDF。',
    create_backup: '在本机创建包含账本、分类、计划及回执的备份。',
    list_backups: '查看可用的本机备份及其中的账本摘要。',
    list_monthly_report_templates: '查看已保存的月报模板及其中的指标。',
    list_host_tokens: '查看当前账号下各主机的名称、启用状态和已获权限。',
    apply_actions: '一次确认项目预算与流水关联，或储蓄目标与进度的组合变更。',
    get_backup_schedule: '查看本机自动备份设置、最近成功时间和恢复演练状态。',
    set_backup_schedule: '设置本机自动备份间隔、保留份数和启停状态。',
    create_host_token: '为当前账号创建主机密钥。密钥只会显示一次。',
    rotate_host_token: '撤销所选旧密钥，按原权限生成新密钥。新密钥只会显示一次。',
    copy_transaction_to_draft: '复制商家、分类、标签和收支方向。新金额和发生日期需重新填写。',
    preview_transaction_reconciliation: '将 CSV 表格和所选日期范围内的账本流水进行比对。',
    commit_transaction_reconciliation: '入账预览中已勾选的表格独有记录。',
    list_csv_mapping_templates: '查看当前账本已保存的表格列映射、日期格式和金额单位。',
    upsert_csv_mapping_template: '保存表格表头和列映射设置，供下次导入时使用。',
    delete_csv_mapping_template: '从当前账本删除指定的表格映射模板。',
    set_financial_cycle: '设置个人月度起始日；只影响自定义账期统计。',
    preview_restore: '先检查备份内容，再逐本选择来源账本和本机目标账户；恢复前仍需在预览中确认。',
    get_financial_cycle: '读取当前账期范围，并说明实际统计起止日期。',
    get_financial_period_summary: '按发薪周期汇总本人账本，保留消费日期或扣款日期口径。',
    get_cross_tab_report: '按最多两个维度汇总金额与笔数；可从表格行继续查看来源。',
    get_report_drilldown: '仅显示日期、分类、金额等安全字段，不显示流水备注。',
    get_purchase_frequency: '比较消费笔数、总额、平均金额及待确认记录。',
    get_spending_variance: '对照两个日期范围的分类支出和变化金额。',
    get_rolling_spending_trend: '显示实际覆盖日期、待确认金额及滚动均值，不补造缺失记录。',
    upsert_expense_view_rule: '将分类或商家标记为刚性支出或弹性支出，不更改原始流水。',
    get_expense_style_summary: '按已设置的规则汇总刚性、弹性及未分类支出。',
    create_budget_scenario_draft: '保存独立预算假设草稿，不写入真实流水或预算。',
    update_budget_scenario_draft: '按当前预算和历史实付重新测算并保存草稿。',
    run_monthly_report: '按已保存的月报模板生成报告。',
    save_monthly_report_snapshot: '保存本次月报结果，供日后与当前账本对比。',
    compare_monthly_report_snapshot: '列出快照保存后发生变化的流水来源。',
    list_report_snapshots: '可按月份或一组成对的日期范围筛选，也可留空查看全部；月份与日期范围不能同时使用。',
    upsert_monthly_report_template: '选择现金流、分类支出、目标进度等指标保存月报模板。',
    get_monthly_report_snapshot: '读取历史快照中保存的指标和图表。',
    get_budget_scenario_draft: '读取预算草稿、分类假设和历史支出测算。',
    list_budget_scenario_drafts: '查找本人保存的预算测算草稿。',
    list_expense_view_rules: '查看当前用于刚性、弹性支出分类的规则。',
    preview_transaction_import: '使用本机选中的 CSV 文件；先显示字段映射、异常和重复项。',
    attach_transaction_file: '将本机选中的收据文件保存到指定流水。',
    upsert_redaction_profile: '保存商家、备注和日期精度的脱敏方式。',
    list_redaction_profiles: '查看本人已保存的数据脱敏方案。',
    preview_redacted_export: '查看应用脱敏规则后的记录数量、字段和样例。',
    get_transaction_attachment: '打开本人账本中已保存的流水附件。',
    list_security_access: '查看脱敏后的本人账本进入与离开记录。',
    get_account_access_policy: '分别查看本人账本的入向与出向授权范围，不含他人账本 ID。',
    set_host_token_scopes: '调整本人 MCP 主机授权范围；变更需要本人明确确认。',
    revoke_host_token: '撤销本人 MCP 主机授权；提交后该主机无法继续调用。',
    authorize_private_transaction_fields: '仅将所选字段绑定到指定主机和指定流水，一次读取后自动失效。',
    backup_drill: '在隔离副本中校验备份，不改动正式账本。',
    preview_maintenance_issues: '仅检查并列出可修复的一致性问题，不会自动更改账本。',
    repair_maintenance_issue: '针对单个问题核对指纹与修复范围后再提交。'
  };

  function featureTitle(tool) {
    return tool.title_zh || tool.label_zh || actionLabels[tool.name] || '账本操作';
  }

  function featureDescription(tool) {
    return tool.description_zh || actionDescriptions[tool.name] || '';
  }

  function featureSubgroup(tool, group) {
    const declared = tool.section_zh || tool.category_zh || tool.group_title_zh;
    if (declared) return declared;
    const name = String(tool.name || '').toLowerCase();
    if (group === 'flow') {
      if (/template|draft|expression|estimated/.test(name)) return '记账模板与草稿';
      if (/categor|tag|merchant|classification/.test(name)) return '分类与自动匹配';
      if (/bulk|merge|revision|history|month_end/.test(name)) return '批量更正与核账';
      if (/filter|search|spending|summary|cashflow/.test(name)) return '筛选与期间分析';
      return '流水操作';
    }
    if (group === 'movements') {
      if (/^transfer$/.test(name) || /transfer/.test(name)) return '账户转账';
      if (/refund/.test(name)) return '退款';
      if (/reimburse/.test(name)) return '报销';
      if (/personal_split/.test(name)) return '共同支出';
      if (/split_settlement/.test(name)) return '共同支出结算';
      if (/loan/.test(name)) return '借还往来';
      if (/account|credit_card/.test(name)) return '账户与信用卡';
      return '款项往来';
    }
    if (group === 'planning') {
      if (/project/.test(name)) return '项目';
      if (/month|monthly|budget/.test(name)) return '期间预算与结算';
      if (/goal|saving/.test(name)) return '储蓄目标';
      if (/income/.test(name)) return '收入规划';
      return '计划与目标';
    }
    if (group === 'recurring') {
      if (/remind/.test(name)) return '到期提醒';
      if (/cashflow_plan/.test(name)) return '一次性预计收支';
      return '周期规则与未来收支';
    }
    if (group === 'insights') {
      if (/cycle/.test(name)) return '账期设置';
      if (/cross_tab|drilldown|variance|trend|frequency/.test(name)) return '趋势与交叉分析';
      if (/expense_style/.test(name)) return '刚性与弹性支出';
      if (/scenario/.test(name)) return '预算方案草稿';
      return '月报模板与快照';
    }
    if (group === 'security') {
      if (/host_token/.test(name)) return 'MCP 主机权限';
      if (/security_access/.test(name)) return '账本访问记录';
      if (/private_transaction_fields/.test(name)) return '单次字段授权';
      return '账本授权范围';
    }
    if (/export/.test(name)) return '导出';
    if (/backup|restore/.test(name)) return '备份与恢复';
    return '文件与维护';
  }

  function renderFeatureGroups() {
    for (const [group, config] of Object.entries(featureGroups)) {
      const tools = featureTools(config.view);
      const list = $('#featureActions-' + group);
      const workspace = group === 'flow' ? $('#featureWorkspace-flow') : $('[data-feature-group="' + group + '"]');
      const empty = $('#featureEmpty-' + group);
      if (!list || !workspace || !empty) continue;
      list.replaceChildren();
      const visible = tools.filter(crossToolAllowed);
      const sections = new Map();
      for (const tool of visible) {
        const title = featureSubgroup(tool, group);
        if (!sections.has(title)) sections.set(title, []);
        sections.get(title).push(tool);
      }
      for (const [title, sectionTools] of sections) {
        const section = node('section', 'feature-action-section');
        section.append(node('h3', 'feature-action-section-title', title));
        const rows = node('div', 'feature-action-list');
        for (const tool of sectionTools) {
          const row = node('article', 'feature-action-row');
          const copy = node('div', 'feature-action-copy');
          copy.append(node('h4', 'feature-action-title', featureTitle(tool)));
          const description = featureDescription(tool);
          if (description) copy.append(node('p', 'feature-action-description', description));
          const access = isCrossAccount()
            ? tool.scope === 'admin_spending' ? '授权支出汇总只读' : '授权目标进度只读'
            : tool.scope === 'own_write' ? '提交前需确认' : '只读查询';
          copy.append(node('p', 'feature-action-meta', access));
          const previewPair = Boolean(previewCommitActions[tool.name]);
          const open = node('button', 'button button-secondary feature-action-open', previewPair ? '预览后确认' : tool.scope === 'own_write' ? '填写并预览' : '查看');
          open.type = 'button';
          open.addEventListener('click', () => openFeatureAction(tool, group));
          row.append(copy, open);
          rows.append(row);
        }
        section.append(rows);
        list.append(section);
      }
      const hasVisible = visible.length > 0;
      workspace.hidden = false;
      list.hidden = !hasVisible;
      empty.hidden = hasVisible;
    }
    const canCreateHostToken = hasTool('create_host_token') && !isCrossAccount();
    const canRotateHostToken = hasTool('rotate_host_token') && !isCrossAccount();
    $('#createHostTokenButton').hidden = !canCreateHostToken;
    $('#hostTokenManagement').hidden = !canCreateHostToken && !canRotateHostToken;
    $('#csvModeReconcile').hidden = isCrossAccount() || !hasTool('preview_transaction_reconciliation') || !hasTool('commit_transaction_reconciliation');
  }

  function inputSchemaFor(tool) {
    return tool.inputSchema || tool.input_schema || tool.parameters || {type: 'object', properties: {}};
  }

  function entityTypeForField(key) {
    const field = String(key).toLowerCase();
    if (/transaction_ids?$/.test(field)) return 'transactions';
    if (/category_id$/.test(field)) return 'categories';
    if (/project_id$/.test(field)) return 'projects';
    if (/budget_id$/.test(field)) return 'budgets';
    if (/goal_id$/.test(field)) return 'goals';
    if (/payment_account_id$|^(source_|destination_)?account_id$|savings_account_id$/.test(field)) return 'payment_accounts';
    if (/draft_id$/.test(field)) return state.featureGroupForAction === 'insights' ? 'budget_scenario_drafts' : 'drafts';
    if (/template_id$/.test(field)) {
      if (/csv_mapping_template/.test(state.activeFeatureAction && state.activeFeatureAction.name || '')) return 'csv_mapping_templates';
      return state.featureGroupForAction === 'insights' ? 'monthly_report_templates' : 'templates';
    }
    if (/filter_id$/.test(field)) return 'filters';
    if (/backup_id$/.test(field)) return 'backups';
    if (/token_id$/.test(field)) return 'host_tokens';
    if (/preset_id$/.test(field)) return 'export_presets';
    if (/profile_id$/.test(field)) return 'redaction_profiles';
    if (/reimbursement_id$/.test(field)) return 'reimbursements';
    if (/loan_id$/.test(field)) return 'loans';
    if (/reminder_id$/.test(field)) return 'reminders';
    if (/file_id$/.test(field)) return 'exports';
    if (/snapshot_id$/.test(field)) return 'monthly_report_snapshots';
    if (/split_id$/.test(field)) return 'splits';
    if (/rule_id$/.test(field)) return state.featureGroupForAction === 'recurring' ? 'recurring_rules' : state.featureGroupForAction === 'insights' ? 'expense_view_rules' : 'classification_rules';
    return null;
  }

  const entitySources = {
    projects: {tools: ['list_projects', 'list_project_budgets'], keys: ['projects', 'project_budgets', 'items'], id: 'project_id'},
    budgets: {tools: ['list_budgets', 'get_monthly_category_budgets'], keys: ['budgets', 'items'], id: 'budget_id'},
    goals: {tools: ['list_goals', 'list_saving_goals'], keys: ['goals', 'items'], id: 'goal_id'},
    payment_accounts: {tools: ['list_payment_accounts'], keys: ['accounts', 'payment_accounts', 'items'], id: 'account_id'},
    drafts: {tools: ['list_transaction_drafts'], keys: ['drafts', 'items'], id: 'draft_id'},
    templates: {tools: ['list_transaction_templates'], keys: ['templates', 'items'], id: 'template_id'},
    csv_mapping_templates: {tools: ['list_csv_mapping_templates'], keys: ['templates', 'items'], id: 'template_id'},
    filters: {tools: ['list_saved_transaction_filters'], keys: ['filters', 'saved_filters', 'items'], id: 'filter_id'},
    backups: {tools: ['list_backups'], keys: ['backups', 'items'], id: 'backup_id'},
    reimbursements: {tools: ['list_pending_reimbursements'], keys: ['reimbursements', 'items'], id: 'reimbursement_id'},
    loans: {tools: ['list_personal_loans'], keys: ['loans', 'items'], id: 'loan_id'},
    reminders: {tools: ['list_reminders'], keys: ['reminders', 'items'], id: 'reminder_id'},
    recurring_rules: {tools: ['list_recurring_rules'], keys: ['rules', 'items'], id: 'rule_id'},
    classification_rules: {tools: ['list_classification_rules'], keys: ['rules', 'items'], id: 'rule_id'},
    host_tokens: {tools: ['list_host_tokens'], keys: ['tokens', 'hosts', 'items'], id: 'token_id'},
    export_presets: {tools: ['list_export_presets'], keys: ['presets', 'items'], id: 'preset_id'},
    redaction_profiles: {tools: ['list_redaction_profiles'], keys: ['profiles', 'items'], id: 'profile_id'},
    budget_scenario_drafts: {tools: ['list_budget_scenario_drafts'], keys: ['drafts', 'items'], id: 'draft_id'},
    monthly_report_templates: {tools: ['list_monthly_report_templates'], keys: ['templates', 'items'], id: 'template_id'},
    monthly_report_snapshots: {tools: ['list_report_snapshots'], keys: ['snapshots', 'items'], id: 'snapshot_id'},
    expense_view_rules: {tools: ['list_expense_view_rules'], keys: ['rules', 'items'], id: 'rule_id'}
  };

  function collectSchemaKeys(schema, target = new Set()) {
    for (const [key, value] of Object.entries(schema.properties || {})) {
      target.add(key);
      collectSchemaKeys(value, target);
      if (value.items) collectSchemaKeys(value.items, target);
    }
    return target;
  }

  function optionRowsFor(type, data) {
    const source = entitySources[type];
    const records = source.keys.map(key => data[key]).find(Array.isArray) || [];
    return records.map(item => {
      if (!item || typeof item !== 'object') return null;
      const id = item[source.id] || item.id;
      if (id === undefined || id === null) return null;
      const period = item.period && typeof item.period === 'object'
        ? [item.period.start_date, item.period.end_date].filter(Boolean).join(' 至 ')
        : item.period;
      const label = item.label || item.path || item.name || item.template_name || item.project_name || item.category_name || item.account_name || item.filename || item.counterparty || String(id);
      const detail = item.occurred_on || enumLabels.kind[item.kind] || statusLabels[item.status] ||
        enumLabels.rule_kind[item.rule_kind] || enumLabels.date_basis[item.date_basis];
      const suffix = [period, detail].filter(Boolean).join(' · ');
      return {id: String(id), label: String(label) + (suffix ? ' · ' + suffix : '')};
    }).filter(Boolean);
  }

  async function loadFeatureEntityOptions(tool, schema) {
    const keys = Array.from(collectSchemaKeys(schema)).filter(key => !(isCrossAccount() && key === 'account_id'));
    const needed = new Set(keys.map(entityTypeForField).filter(Boolean));
    if (needed.has('categories')) {
      state.entityOptions.categories = state.categories.map(item => ({id: String(item.category_id), label: item.path || item.name || String(item.category_id)}));
    }
    if (needed.has('transactions')) {
      const transactions = (state.recent || []).concat(state.currentPageTransactions || []);
      state.entityOptions.transactions = Array.from(new Map(transactions.map(item => [String(item.transaction_id), {
        id: String(item.transaction_id),
        label: [item.occurred_on, item.merchant || item.note || categoryName(item.category_id), money(item.amount_minor)].filter(Boolean).join(' · ')
      }])).values());
    }
    const pending = Array.from(needed).filter(type => entitySources[type] && !state.entityOptions[type]);
    await Promise.all(pending.map(async type => {
      const source = entitySources[type];
      const action = source.tools.find(name => {
        const descriptor = state.toolCatalog.find(item => item.name === name);
        return hasTool(name) && crossToolAllowed(descriptor);
      });
      if (!action) return;
      try {
        const result = await callTool(action, {});
        state.entityOptions[type] = optionRowsFor(type, envelopeData(result.envelope));
      } catch {
        state.entityOptions[type] = [];
      }
    }));
  }

  async function openFeatureAction(tool, group) {
    if (!hasTool(tool.name) || !crossToolAllowed(tool)) return;
    state.activeFeatureAction = tool;
    state.featureGroupForAction = group;
    state.pendingPreview = null;
    state.activeFeatureParams = null;
    state.restoreBindingChoices = null;
    $('#featureForm').reset();
    $('#featureDialogTitle').replaceChildren(document.createTextNode(featureTitle(tool)));
    const description = featureDescription(tool);
    $('#featureDialogDescription').textContent = description;
    $('#featureDialogDescription').hidden = !description;
    $('#featureFields').replaceChildren(node('p', 'loading-copy', '正在准备可选项目…'));
    $('#featurePreview').replaceChildren();
    $('#featurePreview').hidden = true;
    $('#featureMessage').textContent = '';
    $('#featureConfirmButton').hidden = true;
    $('#featureConfirmButton').disabled = false;
    $('#featureConfirmButton').onclick = null;
    $('#featurePreviewButton').hidden = false;
    $('#featurePreviewButton').disabled = false;
    $('#featurePreviewButton').textContent = previewCommitActions[tool.name] ? '生成预览' : tool.scope === 'own_write' ? '检查信息' : '查询';
    const schema = inputSchemaFor(tool);
    $('#featureDialog').showModal();
    try { await loadFeatureEntityOptions(tool, schema); } catch { /* Fall back to direct ID entry when a list is unavailable. */ }
    if (state.activeFeatureAction !== tool || !$('#featureDialog').open) return;
    $('#featureFields').replaceChildren();
    const properties = {...(schema.properties || {})};
    if (tool.name === 'preview_restore') delete properties.ledger_bindings;
    renderSchemaFields($('#featureFields'), properties, (schema.required || []).filter(key => key !== 'ledger_bindings'), '');
    applyFeatureDefaults(tool);
    bindSnapshotSearchControls(tool);
    if (tool.name === 'preview_restore') {
      const backupSelect = $('[data-schema-path="backup_id"]', $('#featureFields'));
      backupSelect?.addEventListener('change', () => {
        if (!state.restoreBindingChoices || String(backupSelect.value) === state.restoreBindingChoices.backupId) return;
        clearRestoreBindingControls();
        $('#featurePreview').hidden = true;
        $('#featurePreviewButton').textContent = '生成预览';
        setMessage($('#featureMessage'), '备份已更改，请先生成新的来源账本列表。');
      });
    }
    $('#featureForm').dataset.stage = 'edit';
  }

  async function openFeatureActionWithValues(name, values, group) {
    const tool = state.toolCatalog.find(item => item.name === name);
    if (!tool || !crossToolAllowed(tool)) return;
    await openFeatureAction(tool, group);
    if (!$('#featureDialog').open || state.activeFeatureAction !== tool) return;
    for (const control of $$('#featureFields [data-schema-path]')) {
      const value = control.dataset.schemaPath.split('.').reduce((current, key) => current && current[key], values);
      if (value === undefined) continue;
      if (control.type === 'checkbox') control.checked = Boolean(value);
      else if (control.tagName === 'SELECT' && control.multiple) {
        const selected = new Set((Array.isArray(value) ? value : [value]).map(String));
        for (const option of control.options) option.selected = selected.has(option.value);
      } else if (control.dataset.schemaType === 'array' && Array.isArray(value)) control.value = value.join('\n');
      else control.value = String(value);
    }
    if (name === 'upsert_transaction_draft' && state.draftCopyContext && String(values.draft_id) === String(state.draftCopyContext.draft_id) && state.draftCopyNeedsFreshFields) {
      const amount = $('#featureFields [data-schema-path="fields.amount_minor"]');
      const occurredOn = $('#featureFields [data-schema-path="fields.occurred_on"]');
      if (amount) {
        amount.value = '';
        amount.required = true;
        amount.placeholder = '填写这笔新流水的金额';
        amount.dataset.currencySigned = 'false';
      }
      if (occurredOn) {
        occurredOn.value = '';
        occurredOn.required = true;
      }
      const note = node('p', 'feature-confirm-note', '这是新流水草稿。商家、分类和标签已复制；金额与发生日期必须重新填写，旧流水金额和日期不会带入。');
      $('#featureFields').prepend(note);
      state.draftCopyNeedsFreshFields = false;
    }
  }

  function applyFeatureDefaults(tool) {
    const range = state.period && validatePeriod(state.period) ? state.period : currentMonthRange();
    const futureStart = new Date();
    futureStart.setDate(futureStart.getDate() + 1);
    const futureEnd = new Date(futureStart);
    futureEnd.setDate(futureEnd.getDate() + 90);
    for (const control of $$('#featureFields [data-schema-key]')) {
      if (control.value) continue;
      const key = control.dataset.schemaKey;
      if (tool.name === 'list_report_snapshots' && ['month', 'start_date', 'end_date'].includes(key)) continue;
      if (tool.name === 'get_spending_comparison') {
        const periodField = control.dataset.schemaPath.match(/^periods\.(\d+)\.(start_date|end_date)$/);
        if (periodField) {
          const index = Number(periodField[1]);
          const monthStart = new Date(new Date().getFullYear(), new Date().getMonth() - index, 1);
          const monthEnd = index === 0 ? new Date() : new Date(monthStart.getFullYear(), monthStart.getMonth() + 1, 0);
          control.value = periodField[2] === 'start_date' ? localDate(monthStart) : localDate(monthEnd);
          continue;
        }
      }
      if (['month', 'from_month', 'to_month', 'period'].includes(key)) control.value = range.start_date.slice(0, 7);
      else if (key === 'as_of') control.value = localDate();
      else if (key === 'date_basis') control.value = ['get_cashflow_summary', 'get_payment_account_activity'].includes(tool.name) ? 'charged_on' : 'occurred_on';
      else if (key === 'start_date') control.value = tool.name === 'get_future_cashflow' ? localDate(futureStart) : range.start_date;
      else if (key === 'end_date') control.value = tool.name === 'get_future_cashflow' ? localDate(futureEnd) : range.end_date;
    }
  }

  function bindSnapshotSearchControls(tool) {
    if (tool.name !== 'list_report_snapshots') return;
    const month = $('#featureFields [data-schema-path="month"]');
    const start = $('#featureFields [data-schema-path="start_date"]');
    const end = $('#featureFields [data-schema-path="end_date"]');
    month.addEventListener('input', () => { start.value = ''; end.value = ''; });
    for (const control of [start, end]) control.addEventListener('input', () => { month.value = ''; });
  }

  function isPathField(key) {
    return /(^|_)(path|filepath|directory|folder)$/i.test(key);
  }

  function fieldLabel(key, schema, path = key) {
    if (key === 'label' && state.activeFeatureAction && ['create_host_token', 'rotate_host_token'].includes(state.activeFeatureAction.name)) return '主机名称';
    if (key === 'account_id' && state.activeFeatureAction && ['admin_spending', 'admin_goal'].includes(state.activeFeatureAction.scope)) return '目标账本编号';
    if (path.startsWith('metrics.')) {
      const metricLabels = {id: '指标编号', label: '指标名称', type: '指标类型'};
      if (metricLabels[key]) return metricLabels[key];
    }
    return fieldLabels[key] || (schema && (schema.title_zh || schema.label_zh || chineseText(schema.title))) || '其他信息';
  }

  function schemaHelp(key, schema) {
    if (key === 'account_id' && state.activeFeatureAction?.scope === 'admin_spending') {
      return '留空时使用当前账本。查看其他账本的支出需要相应授权。';
    }
    if (key === 'account_id' && state.activeFeatureAction?.scope === 'admin_goal') {
      return '留空时使用当前账本。查看其他账本的目标进度需要相应授权。';
    }
    return schema.description_zh || chineseText(schema.description);
  }

  function schemaTypeFor(schema) {
    if (typeof schema.type === 'string') return schema.type;
    if (Array.isArray(schema.type)) return schema.type.find(type => type !== 'null') || 'string';
    if (Array.isArray(schema.anyOf)) {
      const branch = schema.anyOf.find(item => item && item.type && item.type !== 'null');
      if (branch) return schemaTypeFor(branch);
    }
    return 'string';
  }

  function renderSchemaFields(target, properties, required, prefix, depth = 0) {
    for (const [key, schema] of Object.entries(properties)) {
      if (key === 'request_id' || isPathField(key)) continue;
      if (!crossFieldAllowed(key)) continue;
      const path = prefix ? prefix + '.' + key : key;
      const isRequired = required.includes(key);
      const type = schemaTypeFor(schema);
      if (type === 'object' && schema.properties) {
        const optionalGroup = !isRequired && Array.isArray(schema.required) && schema.required.length > 0;
        const fieldset = node('fieldset', 'schema-fieldset' + (optionalGroup ? ' schema-optional-object' : ''));
        if (optionalGroup) fieldset.dataset.optionalObject = 'true';
        const legend = node('legend', '', fieldLabel(key, schema, path) + (isRequired ? ' · 必填' : ''));
        fieldset.append(legend);
        let toggle = null;
        if (optionalGroup) {
          const toggleLabel = node('label', 'schema-optional-toggle');
          toggle = node('input');
          toggle.type = 'checkbox';
          toggleLabel.append(toggle, node('span', '', '填写此部分'));
          fieldset.append(toggleLabel);
        }
        const contents = node('div', 'schema-fieldset-body');
        renderSchemaFields(contents, schema.properties, schema.required || [], path, depth + 1);
        fieldset.append(contents);
        if (optionalGroup) {
          contents.hidden = true;
          for (const control of $$('input, select, textarea, button', fieldset)) {
            if (control !== toggle && control.closest('[data-optional-object]') === fieldset) control.disabled = true;
          }
          toggle.addEventListener('change', () => {
            contents.hidden = !toggle.checked;
            for (const control of $$('input, select, textarea, button', fieldset)) {
              if (control !== toggle && control.closest('[data-optional-object]') === fieldset) control.disabled = !toggle.checked;
            }
          });
        }
        target.append(fieldset);
        continue;
      }
      if (type === 'array') {
        renderArrayField(target, key, schema, path, isRequired, depth);
        continue;
      }
      const wrapper = node('label', 'schema-field');
      wrapper.append(node('span', 'schema-label', fieldLabel(key, schema, path) + (isRequired ? ' · 必填' : '')));
      const control = createSchemaControl(key, schema, path, isRequired);
      wrapper.append(control);
      const help = schemaHelp(key, schema);
      if (help) wrapper.append(node('small', 'schema-help', help));
      target.append(wrapper);
    }
  }

  function renderArrayField(target, key, schema, path, isRequired, depth) {
    const wrapper = node('fieldset', 'schema-fieldset schema-array-field');
    wrapper.append(node('legend', '', fieldLabel(key, schema, path) + (isRequired ? ' · 必填' : '')));
    const itemSchema = schema.items || {type: 'string'};
    const entityOptions = state.entityOptions[entityTypeForField(key)] || [];
    if (schemaTypeFor(itemSchema) === 'object' && itemSchema.properties) {
      const rows = node('div', 'schema-array-rows');
      wrapper.append(rows);
      const addRow = () => {
        const index = rows.children.length;
        const row = node('div', 'schema-array-row');
        row.append(node('span', 'schema-array-index', '第 ' + (index + 1) + ' 项'));
        renderSchemaFields(row, itemSchema.properties, itemSchema.required || [], path + '.' + index, depth + 1);
        const remove = node('button', 'text-button schema-remove-row', '移除此项');
        remove.type = 'button';
        remove.addEventListener('click', () => {
          if (rows.children.length <= Number(schema.minItems || (isRequired ? 1 : 0))) return;
          row.remove();
          Array.from(rows.children).forEach((child, childIndex) => {
            child.querySelector('.schema-array-index').textContent = '第 ' + (childIndex + 1) + ' 项';
            child.querySelectorAll('[data-schema-path]').forEach(control => {
              control.dataset.schemaPath = control.dataset.schemaPath.replace(new RegExp('^' + path.replaceAll('.', '\\.') + '\\.\\d+'), path + '.' + childIndex);
            });
          });
        });
        row.append(remove);
        rows.append(row);
      };
      const minimum = isRequired ? Math.max(1, Number(schema.minItems || 0)) : 0;
      for (let i = 0; i < minimum; i += 1) addRow();
      const add = node('button', 'text-button schema-add-row', '＋ 添加一项');
      add.type = 'button';
      add.addEventListener('click', addRow);
      wrapper.append(add);
    } else if (Array.isArray(itemSchema.enum) || entityOptions.length) {
      const control = node('select', 'schema-control');
      control.multiple = true;
      const enumValues = Array.isArray(itemSchema.enum) ? itemSchema.enum : [];
      const enumTitles = itemSchema.enumLabels || itemSchema.enum_zh || itemSchema.enum_titles || {};
      control.size = Math.min(6, Math.max(3, enumValues.length || entityOptions.length));
      if (enumValues.length) {
        for (const value of enumValues) addSelectOption(control, value, enumTitles[value] || schemaOptionLabel(key, value));
      } else {
        for (const option of entityOptions) addSelectOption(control, option.id, option.label);
      }
      control.dataset.schemaPath = path;
      control.dataset.schemaType = 'array';
      control.dataset.schemaItemType = schemaTypeFor(itemSchema);
      if (isRequired) control.required = true;
      wrapper.append(control);
    } else {
      const control = node('textarea', 'schema-control');
      control.rows = 2;
      control.placeholder = '每行一项，也可用逗号分隔';
      control.dataset.schemaPath = path;
      control.dataset.schemaType = 'array';
      control.dataset.schemaItemType = itemSchema.type || 'string';
      if (isRequired) control.required = true;
      wrapper.append(control);
    }
    const help = schemaHelp(key, schema);
    if (help) wrapper.append(node('small', 'schema-help', help));
    target.append(wrapper);
  }

  function schemaOptionLabel(key, value) {
    const booleanLabel = typeof value === 'boolean' ? (value ? '是' : '否') : '';
    return enumLabels[key]?.[value] || fieldLabels[value] || booleanLabel || chineseText(value) || '其他选项';
  }

  function createSchemaControl(key, schema, path, required) {
    const wrapper = node('span', 'schema-control-wrap');
    let control;
    const type = schemaTypeFor(schema);
    if (Array.isArray(schema.enum)) {
      control = node('select', 'schema-control');
      if (!required) addSelectOption(control, '', '请选择');
      for (const value of schema.enum) {
        const labels = schema.enumLabels || schema.enum_zh || schema.enum_titles || {};
        addSelectOption(control, value, labels[value] || schemaOptionLabel(key, value));
      }
    } else if (type === 'boolean') {
      control = node('select', 'schema-control');
      if (!required) addSelectOption(control, '', '不更改');
      addSelectOption(control, 'true', '是');
      addSelectOption(control, 'false', '否');
      if (schema.default !== undefined) control.value = String(schema.default);
    } else if (/_id$/.test(key) && (state.entityOptions[entityTypeForField(key)] || []).length) {
      control = node('select', 'schema-control');
      if (!required) addSelectOption(control, '', '请选择');
      for (const option of state.entityOptions[entityTypeForField(key)]) addSelectOption(control, option.id, option.label);
    } else {
      const isCsvMappingColumn = path.startsWith('column_mapping.');
      const isDate = !isCsvMappingColumn && (schema.format === 'date' || /(^|_)(date|on|deadline)$/.test(key));
      const isMonth = schema.format === 'month' || ['period', 'month', 'from_month', 'to_month'].includes(key);
      if (isDate || isMonth) {
        control = node('input', 'schema-control');
        control.type = isMonth ? 'month' : 'date';
      } else if (type === 'integer' || type === 'number') {
        control = node('input', 'schema-control');
        control.type = 'number';
        control.step = type === 'integer' ? '1' : 'any';
        if (schema.minimum !== undefined) control.min = schema.minimum;
        if (schema.maximum !== undefined) control.max = schema.maximum;
        if (/_minor$/.test(key)) {
          control.type = 'text';
          control.inputMode = 'decimal';
          control.placeholder = '例如 128.50';
          control.dataset.currencyMinor = 'true';
          control.dataset.currencySigned = String(schema.minimum === undefined || Number(schema.minimum) < 0);
          if (schema.minimum !== undefined) control.dataset.schemaMinimum = schema.minimum;
          if (schema.maximum !== undefined) control.dataset.schemaMaximum = schema.maximum;
        }
      } else if (schema.maxLength && schema.maxLength > 180 || /(^|_)(note|description|reason|expression)$/.test(key)) {
        control = node('textarea', 'schema-control');
        control.rows = 3;
      } else {
        control = node('input', 'schema-control');
        control.type = /query|keyword|search/i.test(key) ? 'search' : 'text';
        if (/_id$/.test(key)) {
          control.placeholder = '输入编号，或先查看相关记录';
        }
      }
    }
    control.dataset.schemaPath = path;
    control.dataset.schemaKey = key;
    control.dataset.schemaType = type;
    if (required && control.type !== 'checkbox') control.required = true;
    if (schema.default !== undefined && schema.type !== 'boolean' && control.type !== 'checkbox') control.value = schema.default;
    wrapper.prepend(control);
    return wrapper;
  }

  function setSchemaValue(target, path, value) {
    const parts = path.split('.');
    let current = target;
    for (let i = 0; i < parts.length; i += 1) {
      const key = parts[i];
      const isLast = i === parts.length - 1;
      if (isLast) {
        current[key] = value;
        return;
      }
      const nextIsIndex = /^\d+$/.test(parts[i + 1]);
      if (current[key] === undefined) current[key] = nextIsIndex ? [] : {};
      current = current[key];
    }
  }

  function pruneEmptyValues(value) {
    if (Array.isArray(value)) {
      const result = value.map(pruneEmptyValues).filter(item => item !== undefined);
      return result.length ? result : undefined;
    }
    if (value && typeof value === 'object') {
      const result = {};
      for (const [key, child] of Object.entries(value)) {
        const cleaned = pruneEmptyValues(child);
        if (cleaned !== undefined) result[key] = cleaned;
      }
      return Object.keys(result).length ? result : undefined;
    }
    return value === '' || value === null || value === undefined ? undefined : value;
  }

  function collectFeatureParams() {
    const params = {};
    for (const control of $$('#featureFields [data-schema-path]')) {
      if (control.disabled) continue;
      const path = control.dataset.schemaPath;
      const key = control.dataset.schemaKey || path.split('.').at(-1);
      let value;
      if (control.type === 'checkbox') value = control.checked;
      else if (control.tagName === 'SELECT' && control.dataset.schemaType === 'boolean') {
        if (control.value === '') continue;
        value = control.value === 'true';
      }
      else if (control.dataset.schemaType === 'array') {
        value = control.tagName === 'SELECT' && control.multiple
          ? Array.from(control.selectedOptions, option => option.value)
          : control.dataset.schemaKey === 'headers'
            ? control.value.split(/\r?\n/).map(item => item.trim()).filter(Boolean)
            : control.value.split(/[\n,]/).map(item => item.trim()).filter(Boolean);
        if (control.dataset.schemaItemType === 'integer') value = value.map(Number);
      } else if (control.value === '') continue;
      else if (control.dataset.currencyMinor === 'true') {
        value = parseAmount(control.value, control.dataset.currencySigned === 'true');
      } else if (control.dataset.schemaType === 'integer' || control.dataset.schemaType === 'number') {
        value = Number(control.value);
        if (!Number.isFinite(value) || (control.dataset.schemaType === 'integer' && !Number.isInteger(value))) {
          throw new Error(fieldLabel(key, null, path) + '必须是有效数字。');
        }
      } else value = control.value;
      if (control.dataset.schemaMinimum !== undefined && Number(value) < Number(control.dataset.schemaMinimum)) {
        throw new Error(fieldLabel(key, null, path) + '不能低于允许的最小值。');
      }
      if (control.dataset.schemaMaximum !== undefined && Number(value) > Number(control.dataset.schemaMaximum)) {
        throw new Error(fieldLabel(key, null, path) + '不能高于允许的最大值。');
      }
      setSchemaValue(params, path, value);
    }
    return pruneEmptyValues(params) || {};
  }

  const hiddenResultFields = new Set(['path', 'local_path', 'absolute_path', 'download_url', 'csrf_token', 'token', 'token_id', 'merchant_ref', 'password', 'secret', 'content_base64', 'request_id', 'preview_id', 'preview_sha256', 'source_sha256', 'issue_fingerprint', 'confirmation']);
  const resultLabels = {
    account: '收支账户', account_id: '账户编号', accounts: '账户列表', projects: '项目', budgets: '分类预算', goals: '储蓄目标',
    transactions: '流水', revisions: '更正历史', rules: '规则', reminders: '到期提醒',
    processed: '处理结果', created: '新建结果', days: '每日收支', items: '明细', plans: '预计收支安排',
    applied_actions: '已提交的计划操作', results: '操作结果', project_budget: '项目预算', saving_goal: '储蓄目标', confirmed_goal_progress: '确认的目标进度',
    income_minor: '收入（元）', expense_minor: '支出（元）', net_minor: '净额（元）',
    actual_minor: '实际支出（元）', actual_paid_minor: '实际支出（元）', net_spend_minor: '扣除已报销后（元）',
    advance_minor: '已垫付（元）', received_minor: '已到账报销（元）', net_reimbursable_minor: '尚待报销（元）',
    carry_in_minor: '上期结转（元）', unbudgeted_actual_minor: '未设预算分类的支出（元）',
    record_count: '流水数量', total_count: '记录数量', affected_count: '受影响记录数',
    annualized_cost_minor: '预计年支出（元）', sha256: '备份校验摘要', format_version: '备份格式版本',
    file_id: '导出文件编号', state: '状态', read_at: '已读时间', next_due_date: '下次到期日',
    date_basis_date: '统计日期', category_path: '分类', project_name: '项目名称',
    transaction_amount_minor: '原始流水金额（元）', pending_income_minor: '待确认收入（元）',
    detail_count: '明细条数', empty: '没有记录', zero: '净额为零', offset: '起始位置',
    has_more: '还有更多', next_offset: '下一页起始位置', ledger: '账本', ledger_id: '账本编号',
    actual_expense_minor: '实际支出（元）', actual_version: '最新版本', after_version: '更正后版本',
    action_name: '操作名称', actor_id: '操作者编号', alias_id: '商家别名编号', aliases: '商家别名',
    allocated_minor: '分配金额（元）', already_allocated: '已分配', already_confirmed: '已确认',
    already_linked: '已关联', amount_yuan: '金额（元）', at: '时间', attachments: '附件',
    adjustments: '资金调整', app_version: '应用版本', assumed_delta_minor: '计划调整额（元）',
    average_minor_per_transaction: '平均每笔金额（元）', backups: '备份列表', balance_due_minor: '待还金额（元）',
    baseline_average_monthly_estimated_minor: '估算月均金额（元）', baseline_average_monthly_pending_minor: '待确认月均金额（元）',
    baseline_average_monthly_spend_minor: '月均实际支出（元）', baseline_drilldown: '基准数据明细',
    baseline_estimated_transaction_count: '基准期估算笔数', baseline_insufficient_data: '基准期数据不足',
    baseline_method: '基准计算方式', baseline_pending_transaction_count: '基准期待确认笔数', baseline_remaining_minor: '基准余额（元）',
    before: '更正前', binding_required: '需要绑定账本', binding_summary: '账本对应关系',
    budget_allowance_minor: '可用预算（元）', budget_defined: '已设预算', can_merge: '可以合并', can_repoint: '可以重新关联',
    candidates: '候选记录', card_balance_minor: '信用卡余额（元）', carry_source_month: '结转来源月份', carryable_minor: '可结转金额（元）',
    categories: '分类', category_totals: '分类汇总', changed: '有变化', check: '核对记录',
    child_category_id: '子分类编号', classification_rule_id: '分类规则编号', classification_status: '分类状态',
    closed_at: '结算时间', closed_version: '结算版本', code: '原因', conflicts: '冲突详情',
    committed_request_id: '入账请求编号', comparison_available: '可与对照期比较', comparison_date: '对照日期',
    comparison_details: '对照期详情', comparison_drilldown: '对照期明细', comparison_estimated_minor: '对照期估算金额（元）',
    comparison_minor: '对照期金额（元）', comparison_pending_minor: '对照期待确认金额（元）',
    comparison_transaction_count: '对照期流水数', config: '设置', count: '数量', coverage_basis: '数据覆盖范围',
    created_at: '创建时间', cross_account: '跨账本', current: '本期', current_category_id: '当前分类',
    current_details: '本期详情', current_drilldown: '本期明细', current_estimated_minor: '本期估算金额（元）',
    current_pending_minor: '本期待确认金额（元）', current_transaction_count: '本期流水数', current_version: '当前版本',
    cycle: '账期', dates: '日期', deleted: '已删除', details: '详情', differences: '差异',
    drafts: '流水草稿', draft: '草稿', drill_id: '演练编号', drilldown: '明细查询', due_periods: '到期账期',
    effects: '变更内容', estimated_minor: '估算金额（元）', estimated_transactions: '估算流水',
    event_id: '事件编号', event_type: '资金往来类型', events: '事件', expected_destination_account_id: '应转入的账户',
    field: '字段', filter_hash: '筛选条件摘要', found: '已找到', goal: '储蓄目标', goal_changes: '目标进度变更',
    header_signature: '表头签名', host: '主机', host_label: '主机名称', host_permission: '主机权限',
    host_permissions: '主机权限', skipped: '跳过项目', imported_count: '已导入行数', independent_draft: '独立草稿',
    insufficient_data: '数据不足', integrity_check: '完整性检查', is_current_account: '当前登录账本',
    isolation: '演练环境', issue_count: '问题数量', last_drill: '最近演练', ledger_map: '账本映射', ledgers: '账本列表',
    left: '第一笔流水', linked: '已关联', live_ledger_summaries_unchanged: '正式账本摘要一致',
    loans: '借还记录', match: '匹配程度', match_count: '匹配数量', matches: '匹配记录', matches_live: '与正式账本一致',
    message: '说明', metric_count: '指标数量', missing: '缺少的信息', months: '月份', movement_links: '关联的资金往来',
    next_month_budget_minor: '下月预算（元）', next_month_total_allowance_minor: '下月可用预算（元）',
    next_run_at: '下次运行时间', notice_days: '提前提醒天数', numeric_changes: '数值变化', omitted_fields: '略去的字段',
    open_drafts: '未完成的草稿', original_amount_minor: '原金额（元）', outcome: '结果', outstanding_minor: '待结清金额（元）',
    over_budget_minor: '超出预算（元）', parent_id: '上级分类编号', payment_account_name: '收支账户名称',
    payment_links: '关联的收支账户', pending: '待确认记录', pending_minor: '待确认金额（元）',
    pending_transactions_by_charge_date: '按扣款日期列出的待确认流水', period_net_due_minor: '本期应还净额（元）',
    possible_duplicate_pairs: '疑似重复流水', pre_restore_backup_id: '恢复前自动备份编号',
    presets: '导出预设', previous_amount_minor: '原金额（元）', price_history: '价格变动记录',
    principal_id: '用户编号', principal_map: '用户对应关系', principal_minor: '本金（元）', profiles: '脱敏方案',
    progress_ratio: '进度比例', project: '项目', project_exists: '项目存在', project_status: '项目状态',
    recommended_keep_transaction_id: '建议保留的流水', refund_events: '退款记录', refunded_minor: '已退款金额（元）',
    refunded_total_minor: '累计退款（元）', reimbursements: '报销记录', remaining_issue_count: '剩余问题数',
    reminder: '提醒', removed: '已移除', repaid_minor: '已归还金额（元）', repairable: '可以修复', repaired: '已修复',
    reopened_at: '重新打开时间', restored: '已恢复', result: '操作结果', result_sha256: '结果校验摘要',
    revision_id: '更正编号', revision_ids: '更正编号', revoked_at: '撤销时间', right: '第二笔流水',
    rows: '记录行', rule: '规则', rule_count: '规则数量', rules_changed: '变更的规则',
    same_installation: '同一本机安装', sample_count: '样本数量', sample_rows: '示例行',
    saved: '已保存', saved_at: '保存时间', saved_filters: '已保存的查询条件', saved_result_sha256: '已保存结果的校验摘要',
    scenario_remaining_minor: '试算后剩余预算（元）', scenario_spend_minor: '试算后的支出（元）',
    scope: '权限范围', settings: '设置', settlement_events: '结算记录', share: '占比', size_bytes: '大小（字节）',
    source_balance_minor: '转出账户余额（元）', source_differences: '来源差异', source_type: '来源类型',
    source_version: '来源版本', split_total_minor: '拆分合计（元）', split_version: '拆分版本',
    target_category_path: '目标分类', target_exists: '目标已存在', target_kind: '目标内容',
    target_request_id: '要查询的请求编号', target_version: '目标版本', template: '模板', template_name: '模板名称',
    templates: '模板列表', timezone: '时区', today: '今日日期', total_amount_minor: '总金额（元）',
    total_income_and_pending_minor: '收入合计（含待确认，元）', total_minor: '合计（元）', transaction: '流水',
    transaction_exists: '流水存在', transaction_kind: '流水方向', transaction_status: '流水状态', transaction_version: '流水版本',
    transactions_changed: '变更的流水', transfer: '转账记录', unallocated_budget_minor: '未分配预算（元）',
    unavailable_reason: '不可用的原因', unbudgeted: '未设预算', unclassified_transactions: '未分类流水', undoable: '可以撤销',
    unresolved_count: '待处理笔数', unresolved_items: '待处理项目', unresolved_truncated: '仅显示部分待处理项目',
    unused_tag_count: '未使用标签数', unused_tags_removed: '已清理标签数', updated_at: '更新时间',
    url: '查看链接', usage_count: '使用次数', username: '用户名', verified_scopes: '已验证权限',
    view_id: '报表视图编号', views: '报表视图', void_reason: '作废原因', voided: '已作废', voided_at: '作废时间',
    after: '更正后', plan: '预计收支', assessment: '核对结果', summary: '摘要', observed: '实际情况',
    affected: '受影响的记录', repair_options: '可用的修复方式', arguments: '查询条件', currency_code: '币种',
    transaction_payment: '流水收付', total_repayment_minor: '还款合计（元）', current_balance_minor: '当前余额（元）',
    content_type: '文件类型', review_url: '查看链接', delivery: '获取方式', inline_available: '支持直接显示图片',
    database_sha256: '数据库校验摘要', backup: '备份数据', live: '本机现有数据'
  };

  function resultFieldLabel(key) {
    return resultLabels[key] || fieldLabels[key] || chineseText(key) || '其他信息';
  }

  function resultFieldHidden(key) {
    if (isCrossAccount() && ['account_id', 'ledger', 'project_id', 'transaction_id', 'transaction_ids', 'merchant', 'keyword', 'tag', 'tags', 'note', 'filters', 'savings_account_id', 'version'].includes(String(key).toLowerCase())) return true;
    return hiddenResultFields.has(String(key).toLowerCase()) || /(?:^|_)(?:password|secret|csrf_token)$/.test(String(key).toLowerCase());
  }

  const resultTextLabels = {
    'earliest matching non-voided transaction in the selected ledger and filters': '从所选账本中最早一笔符合筛选条件且未作废的流水开始。',
    'Average confirmed spending for each category over the prior three complete calendar months; pending and estimated amounts are surfaced separately, and months without spending count as zero.': '按过去三个完整自然月的各分类已确认支出计算月均值。待确认与估算金额单列，无支出的月份按零计算。',
    'in-memory SQLite copy; live database was not restored or replaced': '在内存副本中演练，正式账本未被替换。',
    'A similar transaction exists; this entry was recorded separately.': '已发现相似流水，本笔已单独保存。'
  };

  function resultScalarText(key, value) {
    if (value === null || value === undefined || value === '') return '未提供';
    if (/_minor$/.test(key) && Number.isFinite(Number(value))) return money(value);
    if (typeof value === 'boolean') return value ? '是' : '否';
    if (['scope', 'scopes', 'verified_scopes', 'host_scope', 'host_scopes', 'host_permission', 'permission'].includes(key)) return scopeLabel(value);
    if (key === 'code' || key === 'error_code') return errorMessages[value] || chineseText(value) || '操作未完成';
    if (key === 'action' || key === 'action_name') return enumLabels.action[value] || actionLabels[value] || chineseText(value) || '其他操作';
    if (['field', 'fields', 'missing_fields', 'omitted_fields', 'required_fields'].includes(key)) {
      return (key === 'fields' && enumLabels.fields[value]) || resultFieldLabel(value);
    }
    const labelKey = key === 'transaction_kind' ? 'kind' : key.endsWith('_status') && !enumLabels[key] ? 'status' : key;
    const labels = enumLabels[labelKey];
    if (labels) return labels[value] || chineseText(value) || '其他';
    if (resultTextLabels[value]) return resultTextLabels[value];
    if (typeof value === 'number' && /count|limit|day|months|priority|version|records|transactions/i.test(key)) return count(value);
    return String(value);
  }

  function appendResultTable(parent, rows) {
    const columns = Array.from(new Set(rows.flatMap(row => Object.keys(row || {})))).filter(key => !resultFieldHidden(key));
    if (!columns.length) return false;
    const scroll = node('div', 'feature-result-table-scroll');
    const table = node('table', 'data-table feature-result-table');
    const caption = node('caption', 'visually-hidden', '操作返回的记录明细');
    const head = document.createElement('thead');
    const heading = document.createElement('tr');
    for (const key of columns) heading.append(node('th', /_minor$|amount|balance|count|total/.test(key) ? 'num' : '', resultFieldLabel(key)));
    head.append(heading);
    const body = document.createElement('tbody');
    for (const row of rows) {
      const tr = document.createElement('tr');
      for (const key of columns) {
        const td = node('td', /_minor$|amount|balance|count|total/.test(key) ? 'num' : '');
        const value = row && row[key];
        if (Array.isArray(value)) td.textContent = value.map(item => previewValueText(key, item)).join('、') || '无记录';
        else if (value && typeof value === 'object') td.textContent = '包含 ' + Object.keys(value).length + ' 项';
        else td.textContent = resultScalarText(key, value);
        tr.append(td);
      }
      body.append(tr);
    }
    table.append(caption, head, body);
    scroll.append(table);
    parent.append(scroll);
    return true;
  }

  function appendResultValue(parent, key, value, depth = 0) {
    if (resultFieldHidden(key) || value === undefined) return;
    if (Array.isArray(value)) {
      if (!value.length) return;
      const content = node('div', 'feature-result-value');
      if (value.every(item => item && typeof item === 'object' && !Array.isArray(item))) appendResultTable(content, value);
      else {
        const list = node('ul', 'feature-result-items');
        for (const item of value) list.append(node('li', '', previewValueText(key, item)));
        content.append(list);
      }
      const field = node('div', 'feature-result-field');
      const details = node('dd');
      details.append(content);
      field.append(node('dt', '', resultFieldLabel(key)), details);
      parent.append(field);
      return;
    }
    if (value && typeof value === 'object') {
      const field = node('div', 'feature-result-field');
      field.append(node('dt', '', resultFieldLabel(key)));
      const nested = node('dd', 'feature-result-nested');
      if (depth < 3) appendResultObject(nested, value, depth + 1);
      else nested.textContent = '包含 ' + Object.keys(value).filter(childKey => !resultFieldHidden(childKey)).length + ' 项详情';
      field.append(nested);
      parent.append(field);
      return;
    }
    const field = node('div', 'feature-result-field');
    field.append(node('dt', '', resultFieldLabel(key)), node('dd', '', resultScalarText(key, value)));
    parent.append(field);
  }

  function appendResultObject(parent, object, depth = 0) {
    const fields = node('dl', depth ? 'feature-result-list feature-result-nested-list' : 'feature-result-list');
    for (const [key, value] of Object.entries(object || {})) appendResultValue(fields, key, value, depth);
    if (fields.childElementCount) parent.append(fields);
  }

  function featurePreviewSummary(params) {
    const list = node('dl', 'feature-preview-list');
    for (const [key, value] of Object.entries(params)) {
      if (key === 'token_id' && state.activeFeatureAction && ['authorize_private_transaction_fields', 'set_host_token_scopes', 'revoke_host_token'].includes(state.activeFeatureAction.name)) {
        const option = $('#featureFields [data-schema-path="token_id"] option:checked');
        const token = (state.entityOptions.host_tokens || []).find(item => String(item.id) === String(value));
        list.append(node('dt', '', 'MCP 主机'), node('dd', '', token ? token.label : option && option.textContent || String(value)));
        continue;
      }
      if (resultFieldHidden(key) || value === undefined || value === null || value === '') continue;
      const term = node('dt', '', fieldLabel(key));
      list.append(term, node('dd', '', previewValueText(key, value)));
    }
    return list;
  }

  function previewValueText(key, value, depth = 0) {
    if (Array.isArray(value)) return value.map(item => previewValueText(key, item, depth + 1)).join('、');
    if (value && typeof value === 'object') {
      if (depth >= 3) return '包含 ' + Object.keys(value).length + ' 项';
      return Object.entries(value).filter(([childKey]) => !resultFieldHidden(childKey))
        .map(([childKey, child]) => resultFieldLabel(childKey) + '：' + previewValueText(childKey, child, depth + 1)).join('；');
    }
    const entityType = entityTypeForField(key);
    const option = entityType && (state.entityOptions[entityType] || []).find(item => String(item.id) === String(value));
    if (option) return option.label;
    return resultScalarText(key, value);
  }

  function pairedCommitParams(previewName, previewId, params) {
    if (previewName === 'preview_restore') return {preview_id: previewId, confirmation: 'RESTORE'};
    if (previewName === 'get_month_close_preview') return {month: params.month};
    return {preview_id: previewId};
  }

  function previewConfirmationCopy(name) {
    if (name === 'preview_restore') return '请核对备份账本范围；跨设备恢复时逐本核对来源账本与本机目标账户。只有点击“确认恢复此备份”后才会执行，服务会先创建当前状态备份。';
    if (name === 'preview_ledger_export') return '确认后会生成所选范围的文件。备注只会按你刚才选择的字段导出，文件仅能由当前登录账本下载。';
    if (name === 'get_month_close_preview') return '请核对结算月份与预览结果。确认后会关闭该月份的结算周期。';
    return '请核对服务返回的影响范围。确认后才会应用这份有效预览。';
  }

  function clearRestoreBindingControls() {
    $('#restoreBindingControls')?.remove();
    state.restoreBindingChoices = null;
  }

  function renderRestoreBindingControls(data, backupId) {
    const sources = Array.isArray(data.source_ledgers) ? data.source_ledgers : [];
    const targets = Array.isArray(data.target_ledgers) ? data.target_ledgers : [];
    clearRestoreBindingControls();
    if (sources.length !== 2 || targets.length !== 2) return false;

    state.restoreBindingChoices = {backupId: String(backupId), sources, targets};
    const fieldset = node('fieldset', 'restore-binding-controls');
    fieldset.id = 'restoreBindingControls';
    fieldset.append(node('legend', '', '明确选择两本账的对应关系'));
    fieldset.append(node('p', 'schema-help', '每行分别选择备份来源和本机目标。两边各选一次，不能按账本名称自动匹配。'));
    const rows = node('div', 'restore-binding-grid');
    for (let index = 0; index < 2; index += 1) {
      const row = node('div', 'restore-binding-row');
      const sourceSelect = document.createElement('select');
      sourceSelect.required = true;
      sourceSelect.dataset.restoreSource = String(index);
      sourceSelect.append(new Option('选择备份来源账本', ''));
      for (const source of sources) {
        const label = (source.account_name || source.account_id || '未命名来源账本') + ' · ' + count(source.transaction_count) + ' 笔 · ' + money(source.balance_minor);
        sourceSelect.append(new Option(label, String(source.ledger_id)));
      }
      const sourceLabel = node('label');
      sourceLabel.append(node('span', '', '备份来源 ' + (index + 1)), sourceSelect);

      const targetSelect = document.createElement('select');
      targetSelect.required = true;
      targetSelect.dataset.restoreTarget = String(index);
      targetSelect.append(new Option('选择本机目标账本', ''));
      for (const target of targets) targetSelect.append(new Option(target.account_name || target.account_id || '未命名本机账本', String(target.account_id)));
      const targetLabel = node('label');
      targetLabel.append(node('span', '', '本机目标 ' + (index + 1)), targetSelect);
      row.append(sourceLabel, targetLabel);
      rows.append(row);
    }
    fieldset.append(rows);
    $('#featureFields').append(fieldset);
    return true;
  }

  function collectRestoreLedgerBindings() {
    const rows = $$('#restoreBindingControls .restore-binding-row');
    if (rows.length !== 2) throw new Error('备份需要两组账本映射，请重新生成预览。');
    const bindings = rows.map(row => ({
      source_ledger_id: $('select[data-restore-source]', row).value,
      target_account_id: $('select[data-restore-target]', row).value
    }));
    if (bindings.some(item => !item.source_ledger_id || !item.target_account_id)) throw new Error('请为每本备份账本选择一个本机目标账户。');
    if (new Set(bindings.map(item => item.source_ledger_id)).size !== 2 || new Set(bindings.map(item => item.target_account_id)).size !== 2) {
      throw new Error('每本来源账本和本机目标账户都必须各自唯一。');
    }
    return bindings;
  }

  function renderRestoreBindingPrompt(preview, data, params) {
    preview.replaceChildren(
      node('h3', '', '此备份需要逐本指定本机目标'),
      node('p', 'feature-confirm-note', '请先确认备份中的来源账本，再分别选择本机目标账户。复制备份文件后，服务会校验这组映射；当前尚未生成可执行的恢复预览。')
    );
    const sources = Array.isArray(data.source_ledgers) ? data.source_ledgers : [];
    if (sources.length) {
      const sourceList = node('ul', 'restore-source-list');
      for (const source of sources) {
        const item = node('li');
        item.append(node('strong', '', source.account_name || '未命名来源账本'));
        item.append(node('span', '', count(source.transaction_count) + ' 笔 · ' + money(source.balance_minor)));
        sourceList.append(item);
      }
      preview.append(sourceList);
    }
    if (!renderRestoreBindingControls(data, params.backup_id)) {
      preview.append(node('p', 'feature-confirm-note is-error', '服务未返回完整的两本来源账本和两本本机账本；请刷新备份列表后重试。'));
      preview.hidden = false;
      $('#featurePreviewButton').hidden = true;
      $('#featureConfirmButton').hidden = true;
      setMessage($('#featureMessage'), '未生成可确认的恢复预览。', true);
      return;
    }
    preview.append(node('p', 'feature-confirm-note', '跨设备 ZIP 可在维护页选择并上传；本机校验后仍需逐本选择来源账本和目标账户，再检查恢复预览。'));
    preview.hidden = false;
    $('#featurePreviewButton').textContent = '生成映射预览';
    $('#featurePreviewButton').hidden = false;
    $('#featurePreviewButton').disabled = false;
    $('#featureConfirmButton').hidden = true;
    $('#featureForm').dataset.stage = 'edit';
    setMessage($('#featureMessage'), '请选择两组来源和目标后，再生成可确认预览。');
  }

  function appendRestoreBindingSummary(preview, mappings) {
    if (!Array.isArray(mappings) || !mappings.length || !state.restoreBindingChoices) return;
    const list = node('ul', 'restore-source-list');
    for (const mapping of mappings) {
      const source = state.restoreBindingChoices.sources.find(item => String(item.ledger_id) === String(mapping.source_ledger_id));
      const target = state.restoreBindingChoices.targets.find(item => String(item.account_id) === String(mapping.target_account_id));
      const item = node('li');
      item.append(node('strong', '', (source && source.account_name) || String(mapping.source_ledger_id)));
      item.append(node('span', '', '→ ' + (mapping.target_account_name || (target && target.account_name) || String(mapping.target_account_id)) + ' · ' + count(mapping.transaction_count) + ' 笔 · ' + money(mapping.balance_minor)));
      list.append(item);
    }
    preview.append(node('h4', '', '已核对的来源到账本映射'), list);
  }

  function showFeatureConfirmation(tool, params) {
    const preview = $('#featurePreview');
    preview.replaceChildren(node('h3', '', '执行前核对'));
    preview.append(featurePreviewSummary(params));
    const message = tool.name === 'commit_transaction_draft' && state.draftCommitContext && String(params.draft_id) === String(state.draftCommitContext.draft_id)
      ? '确认后会将新草稿记入当前账本：' + (state.draftCommitContext.fields.merchant || '未填写商家') + ' · ' + money(state.draftCommitContext.fields.amount_minor) + ' · ' + state.draftCommitContext.fields.occurred_on + '。请核对新金额和发生日期。'
      : tool.name === 'authorize_private_transaction_fields'
      ? '这会允许所选 MCP 主机在 3 分钟内读取这笔流水的指定字段；授权只可使用一次，随后自动失效。请核对主机、流水编号和字段。'
      : tool.name === 'set_host_token_scopes'
        ? '确认后会更新这台 MCP 主机可使用的权限；变更会影响其下一次工具目录和调用。请核对主机名称及新权限。'
        : tool.name === 'revoke_host_token'
          ? '确认后会撤销这台 MCP 主机的授权；撤销会影响其后续调用。请核对主机名称。'
      : /restore|close_project|reopen_month|delete|deactivate/.test(tool.name)
      ? '请确认对象和影响范围。此操作会更改当前账本数据。'
      : '确认后会提交到当前账本。请核对对象、金额、日期和版本信息。';
    preview.append(node('p', 'feature-confirm-note', message));
    preview.hidden = false;
    state.activeFeatureParams = params;
    $('#featureMessage').textContent = '';
    $$('#featureFields input, #featureFields select, #featureFields textarea, #featureFields button').forEach(control => { control.disabled = true; });
    $('#featureForm').dataset.stage = 'confirm';
    $('#featurePreviewButton').hidden = true;
    $('#featureConfirmButton').disabled = false;
    $('#featureConfirmButton').textContent = '确认并执行';
    $('#featureConfirmButton').hidden = false;
    $('#featureConfirmButton').focus();
    $('#featureConfirmButton').onclick = () => runFeatureAction(tool, params);
  }

  function previewIdFrom(data) {
    if (!data || typeof data !== 'object') return null;
    if (data.preview_id) return String(data.preview_id);
    if (data.preview && data.preview.preview_id) return String(data.preview.preview_id);
    return null;
  }

  async function previewFeatureAction(tool, params) {
    const commitName = previewCommitActions[tool.name];
    const commitTool = state.toolCatalog.find(item => item.name === commitName);
    if (!commitTool || commitTool.scope !== 'own_write' || isCrossAccount()) {
      setMessage($('#featureMessage'), '当前账号没有此预览对应的确认权限。', true);
      return;
    }
    const button = $('#featurePreviewButton');
    button.disabled = true;
    setMessage($('#featureMessage'), '正在生成服务端预览…');
    try {
      const result = await callTool(tool.name, params, tool.scope === 'own_write');
      const data = envelopeData(result.envelope);
      const previewId = previewIdFrom(data);
      const preview = $('#featurePreview');
      if (tool.name === 'preview_restore' && data.binding_required === true) {
        renderRestoreBindingPrompt(preview, data, params);
        return;
      }
      preview.replaceChildren(node('h3', '', '服务端预览结果'));
      if (tool.name === 'preview_restore' && Array.isArray(data.ledger_bindings)) appendRestoreBindingSummary(preview, data.ledger_bindings);
      const visibleData = tool.name === 'preview_restore' ? {...data, ledger_bindings: undefined} : data;
      appendResultObject(preview, visibleData);
      if (tool.name === 'get_month_close_preview' && Number(data.unresolved_count || 0) > 0) {
        preview.append(node('p', 'feature-confirm-note is-error', '仍有待确认或未分类记录，请先处理清单后重新预览。'));
        preview.hidden = false;
        setMessage($('#featureMessage'), '当前月份尚未满足结算条件。', true);
        return;
      }
      if (!previewId && tool.name !== 'get_month_close_preview') {
        preview.append(node('p', 'feature-confirm-note is-error', '服务未返回可确认的预览凭据，本页不会继续执行。'));
        preview.hidden = false;
        setMessage($('#featureMessage'), '预览结果已读取，但暂时不能确认执行。', true);
        return;
      }
      preview.append(node('p', 'feature-confirm-note', previewConfirmationCopy(tool.name)));
      preview.hidden = false;
      state.pendingPreview = {previewName: tool.name, previewId, params, commitTool};
      $$('#featureFields input, #featureFields select, #featureFields textarea, #featureFields button').forEach(control => { control.disabled = true; });
      $('#featureForm').dataset.stage = 'confirm';
      $('#featurePreviewButton').hidden = true;
      $('#featureConfirmButton').disabled = false;
      $('#featureConfirmButton').textContent = tool.name === 'preview_restore' ? '确认恢复此备份' : tool.name === 'preview_ledger_export' ? '确认导出' : '确认并执行';
      $('#featureConfirmButton').hidden = false;
      $('#featureConfirmButton').onclick = () => commitFeaturePreview();
      $('#featureConfirmButton').focus();
      setMessage($('#featureMessage'), '');
    } catch (error) {
      setMessage($('#featureMessage'), errorText(error, '无法生成操作预览。'), true);
    } finally {
      button.disabled = false;
    }
  }

  async function commitFeaturePreview() {
    const pending = state.pendingPreview;
    if (!pending) return;
    const button = $('#featureConfirmButton');
    button.disabled = true;
    setMessage($('#featureMessage'), '正在提交已确认的操作…');
    try {
      const params = pairedCommitParams(pending.previewName, pending.previewId, pending.params);
      const result = await callTool(pending.commitTool.name, params, true);
      const group = state.featureGroupForAction;
      $('#featureDialog').close();
      await loadAppData();
      renderFeatureResult(group, pending.commitTool, result.envelope, result.requestId, pending.params);
      state.pendingPreview = null;
    } catch (error) {
      setMessage($('#featureMessage'), errorText(error, '操作未完成。预览可能已过期，请重新生成。'), true);
      button.disabled = false;
    }
  }

  function isPreviewAction(name) {
    return name.startsWith('preview_') || name.startsWith('calculate_') || name === 'evaluate_amount_expression' || name === 'get_month_close_preview';
  }

  async function submitFeatureForm(event) {
    event.preventDefault();
    const tool = state.activeFeatureAction;
    if (!tool || !hasTool(tool.name)) return;
    if (!$('#featureForm').reportValidity()) return;
    let params;
    try {
      params = collectFeatureParams();
    } catch (error) {
      setMessage($('#featureMessage'), errorText(error), true);
      return;
    }
    if (tool.name === 'upsert_transaction_draft' && state.draftCopyContext && String(params.draft_id) === String(state.draftCopyContext.draft_id)) {
      const fields = params.fields || {};
      if (!fields.amount_minor || !fields.occurred_on) {
        setMessage($('#featureMessage'), '请填写这笔新流水自己的金额和发生日期；旧值不会复制。', true);
        return;
      }
    }
    if (tool.name === 'preview_restore' && state.restoreBindingChoices) {
      if (String(params.backup_id || '') !== state.restoreBindingChoices.backupId) {
        clearRestoreBindingControls();
        $('#featurePreviewButton').textContent = '生成预览';
        $('#featurePreview').hidden = true;
        setMessage($('#featureMessage'), '备份已更改，请重新生成来源账本信息后再选择映射。', true);
        return;
      }
      try {
        params.ledger_bindings = collectRestoreLedgerBindings();
      } catch (error) {
        setMessage($('#featureMessage'), errorText(error), true);
        return;
      }
    }
    if (tool.name === 'list_report_snapshots') {
      const hasStart = Boolean(params.start_date);
      const hasEnd = Boolean(params.end_date);
      if (hasStart !== hasEnd || params.month && (hasStart || hasEnd)) {
        setMessage($('#featureMessage'), '请只选月份或成对的开始、结束日期；两种筛选不能同时使用。', true);
        return;
      }
    }
    if (previewCommitActions[tool.name]) {
      await previewFeatureAction(tool, params);
      return;
    }
    if (tool.scope !== 'own_write' || isPreviewAction(tool.name)) {
      await runFeatureAction(tool, params);
      return;
    }
    showFeatureConfirmation(tool, params);
  }

  async function runFeatureAction(tool, params) {
    const button = $('#featureConfirmButton').hidden ? $('#featurePreviewButton') : $('#featureConfirmButton');
    button.disabled = true;
    setMessage($('#featureMessage'), '正在执行…');
    try {
      const result = await callTool(tool.name, params, tool.scope === 'own_write');
      const group = state.featureGroupForAction;
      const data = envelopeData(result.envelope);
      $('#featureDialog').close();
      if (tool.name === 'create_host_token' || tool.name === 'rotate_host_token') {
        renderFeatureResult(group, tool, result.envelope, result.requestId, params);
        return;
      }
      const metadataOnlyWrites = new Set([
        'upsert_transaction_draft', 'record_payment_account_balance_check',
        'upsert_csv_mapping_template', 'delete_csv_mapping_template', 'set_backup_schedule'
      ]);
      if (tool.scope === 'own_write' && !metadataOnlyWrites.has(tool.name)) await loadAppData();
      renderFeatureResult(group, tool, result.envelope, result.requestId, params);
      if (tool.name === 'upsert_transaction_draft' && state.draftCopyContext && String(params.draft_id) === String(state.draftCopyContext.draft_id)) state.draftCopyNeedsFreshFields = false;
      if (tool.name === 'commit_transaction_draft' && state.draftCommitContext && String(params.draft_id) === String(state.draftCommitContext.draft_id)) {
        state.draftCopyContext = null;
        state.draftCopyNeedsFreshFields = false;
        state.draftCommitContext = null;
      }
      if (tool.name === 'upsert_csv_mapping_template' || tool.name === 'delete_csv_mapping_template') await loadCsvMappingTemplates(data.template && data.template.template_id);
      if (tool.name === 'set_backup_schedule') await refreshBackupSchedule(data);
    } catch (error) {
      setMessage($('#featureMessage'), errorText(error, '操作未完成，请核对信息后重试。'), true);
      if (noReceiptActions.has(tool.name) || (state.toolCatalog.find(item => item.name === tool.name) || {}).no_receipt === true) {
        button.disabled = true;
        setMessage($('#featureMessage'), errorText(error, '服务结果暂时无法确认。') + ' 本操作没有通用回执；请关闭窗口并重新读取主机权限或状态后再决定是否重试。', true);
      } else button.disabled = false;
    }
  }

  function appendFeatureResultHeader(target, tool, envelope, requestId) {
    const heading = node('div', 'feature-result-heading');
    const actionStatus = {
      authorize_private_transaction_fields: '单次授权已生效',
      set_host_token_scopes: '主机权限已更新',
      revoke_host_token: '主机授权已撤销',
      create_host_token: '主机授权已创建',
      rotate_host_token: '主机密钥已换发',
      record_payment_account_balance_check: '账户余额已核对',
      commit_transaction_reconciliation: 'CSV 对账确认已提交'
    }[tool.name];
    heading.append(node('h3', '', featureTitle(tool)), node('p', 'feature-result-status', actionStatus || (envelope.status === 'committed' ? '已提交到账本' : '查询完成')));
    target.append(heading);
    if (requestId) target.append(node('p', 'feature-result-receipt', '提交编号：' + String(requestId).slice(0, 12)));
  }

  function insightLabel(item) {
    if (item.dimensions && typeof item.dimensions === 'object') {
      if (item.dimensions.category_path) return String(item.dimensions.category_path);
      if (Object.hasOwn(item.dimensions, 'project_id')) return item.dimensions.project_name || (item.dimensions.project_id ? '项目名称不可用' : '未关联项目');
      return Object.entries(item.dimensions).map(([key, value]) => {
        if (key === 'category_id') return categoryName(value) || '未归类';
        if (key === 'project_id') return value ? '项目' : '未关联项目';
        if (key === 'category_path' || key === 'project_name') return String(value ?? '未分类');
        if (key === 'expense_style') return enumLabels.classification[value] || String(value);
        return String(value ?? '未分类');
      }).join(' · ');
    }
    if (item.category_path) return String(item.category_path);
    if (item.category_id) return categoryName(item.category_id) || String(item.category_id);
    if (item.classification) return enumLabels.classification[item.classification] || String(item.classification);
    if (item.merchant) return String(item.merchant);
    if (item.category) return categoryName(item.category) || String(item.category);
    return String(item.date || item.month || item.name || '未分类');
  }

  function insightStateText(item, toolName) {
    if (item.empty) return '无流水';
    if (item.zero) return toolName === 'get_cross_tab_report' ? '有流水，净额为 ¥0.00' : '有已确认记录，支出为 ¥0.00';
    return '';
  }

  function addInsightBar(parent, label, value, maximum, drill, description) {
    const button = node('button', 'insight-bar');
    button.type = 'button';
    button.setAttribute('aria-label', description || (label + '，' + money(value)));
    const copy = node('span', 'insight-bar-copy');
    copy.append(node('span', 'insight-bar-label', label), node('span', 'insight-bar-value', money(value)));
    const track = node('span', 'insight-bar-track');
    track.setAttribute('aria-hidden', 'true');
    const fill = node('span', 'insight-bar-fill');
    fill.style.width = maximum > 0 ? Math.max(0, Math.min(100, Number(value || 0) / maximum * 100)) + '%' : '0%';
    track.append(fill);
    button.append(copy, track);
    if (drill) button.addEventListener('click', drill);
    else button.disabled = true;
    parent.append(button);
  }

  function drilldownParams(tool, params, item, variant) {
    const selection = variant ? item[variant + '_drilldown'] : item.drilldown;
    const drilldown = selection && selection.arguments ? selection.arguments : selection || {};
    const groupValues = drilldown.group_values || {};
    let startDate = drilldown.start_date || params.start_date || currentPeriod().start_date;
    let endDate = drilldown.end_date || params.end_date || currentPeriod().end_date;
    if (tool.name === 'get_spending_variance' && variant && !drilldown.start_date) {
      const period = params[variant + '_period'];
      if (period) {
        startDate = period.start_date;
        endDate = period.end_date;
      } else {
        const prefix = variant === 'current' ? 'current' : 'comparison';
        startDate = params[prefix + '_start_date'] || startDate;
        endDate = params[prefix + '_end_date'] || endDate;
      }
    }
    if (tool.name === 'get_rolling_spending_trend') startDate = endDate = item.date;
    return {
      start_date: startDate,
      end_date: endDate,
      group_values: groupValues,
      filters: drilldown.filters || params.filters || {},
      date_basis: drilldown.date_basis || params.date_basis || 'occurred_on',
      limit: 50,
      offset: 0
    };
  }

  async function showInsightDrilldown(parent, params, chart = false) {
    const tool = state.toolCatalog.find(item => item.name === 'get_report_drilldown');
    if (!tool || !crossToolAllowed(tool)) return;
    const area = node('section', 'insight-drilldown');
    const previous = $('.insight-drilldown', parent);
    area.append(node('h3', '', '来源明细'), node('p', 'feature-result-empty', '正在读取流水…'));
    if (previous) previous.replaceWith(area);
    else parent.append(area);
    if (chart) {
      area.tabIndex = -1;
      area.focus();
    }
    try {
      const result = await callTool(tool.name, params);
      const data = envelopeData(result.envelope);
      area.replaceChildren(node('h3', '', '来源明细'));
      if (chart && !data.items.length) {
        area.append(node('p', 'feature-result-empty', '这一期间没有可显示的流水。'));
      } else if (chart) {
        const dates = params.start_date + ' 至 ' + params.end_date;
        const total = '共 ' + count(data.detail_count) + ' 条' + (data.has_more ? '，显示前 ' + count(data.items.length) + ' 条' : '');
        area.append(node('p', 'panel-subtitle', dates + ' · ' + total));
        appendResultTable(area, data.items.map(item => ({
          date_basis_date: item.date_basis_date,
          category_path: item.category_path || '未分类',
          confirmation_status: item.confirmation_status,
          estimated: item.estimated,
          amount_minor: item.amount_minor
        })));
      } else {
        appendResultObject(area, data);
      }
    } catch (error) {
      area.replaceChildren(node('h3', '', '来源明细'), node('p', 'error-inline', errorText(error, '无法读取该分组的来源。')));
    }
  }

  function insightColumns(toolName) {
    if (toolName === 'get_cross_tab_report') return [
      ['income_minor', '收入'], ['expense_minor', '支出'], ['pending_income_minor', '待确认收入'],
      ['pending_expense_minor', '待确认支出'], ['net_minor', '净额'], ['record_count', '流水数'], ['pending_record_count', '待确认流水数'], ['estimated_count', '估算笔数']
    ];
    if (toolName === 'get_spending_variance') return [
      ['comparison_expense_minor', '对照期支出'], ['current_expense_minor', '本期支出'],
      ['change_minor', '变化金额'], ['change_percent', '变化比例'], ['comparison_basis', '对照基准'],
      ['comparison_pending_minor', '对照期待确认'], ['current_pending_minor', '本期待确认'],
      ['comparison_pending_count', '对照期待确认笔数'], ['current_pending_count', '本期待确认笔数'],
      ['comparison_estimated_minor', '对照期估算'], ['current_estimated_minor', '本期估算'],
      ['comparison_estimated_count', '对照期估算笔数'], ['current_estimated_count', '本期估算笔数'],
      ['comparison_transaction_count', '对照期流水数'], ['current_transaction_count', '本期流水数']
    ];
    if (toolName === 'get_purchase_frequency') return [
      ['transaction_count', '消费笔数'], ['pending_transaction_count', '待确认笔数'],
      ['total_expense_minor', '支出合计'], ['average_minor_per_transaction', '平均金额'],
      ['pending_expense_minor', '待确认金额'], ['estimated_count', '估算笔数'], ['estimated_minor', '估算金额']
    ];
    if (toolName === 'get_expense_style_summary') return [
      ['expense_minor', '已确认支出'], ['pending_expense_minor', '待确认支出'], ['transaction_count', '流水数'], ['pending_transaction_count', '待确认笔数'], ['estimated_count', '估算笔数'], ['estimated_minor', '估算金额']
    ];
    return [
      ['actual_expense_minor', '已确认支出'], ['pending_expense_minor', '待确认支出'],
      ['record_count', '流水数'], ['pending_count', '待确认笔数'], ['estimated_count', '估算笔数'], ['estimated_minor', '估算金额'],
      ['rolling_average_minor', '滚动均值'], ['window_days', '窗口天数'], ['coverage_days', '覆盖天数']
    ];
  }

  function renderInsightTable(parent, tool, rows, params) {
    if (!rows.length) return;
    const scroll = node('div', 'table-scroll insight-table-scroll');
    const table = node('table', 'data-table insight-table');
    const caption = node('caption', '', '图表对应的完整数值表');
    const head = document.createElement('thead');
    const header = document.createElement('tr');
    header.append(node('th', '', tool.name === 'get_rolling_spending_trend' ? '日期' : tool.name === 'get_spending_variance' ? '分类' : '分组'));
    for (const [key, label] of insightColumns(tool.name)) if (rows.some(row => Object.hasOwn(row, key))) header.append(node('th', 'num', label));
    header.append(node('th', '', '状态'));
    if (hasTool('get_report_drilldown') && !isCrossAccount()) header.append(node('th', '', '来源'));
    head.append(header);
    const body = document.createElement('tbody');
    for (const item of rows) {
      const row = document.createElement('tr');
      row.append(node('th', '', insightLabel(item)));
      for (const [key] of insightColumns(tool.name)) {
        if (!rows.some(value => Object.hasOwn(value, key))) continue;
        const value = item[key];
        const textValue = value === undefined || value === null ? '未提供' : /_minor$/.test(key) ? money(value) : key === 'change_percent' ? percentagePoints(value) : resultScalarText(key, value);
        row.append(node('td', 'num', textValue));
      }
      const stateText = insightStateText(item, tool.name) || (item.insufficient_data ? '样本不足' : '');
      row.append(node('td', '', stateText || '无特殊标记'));
      if (hasTool('get_report_drilldown') && !isCrossAccount()) {
        const actionCell = node('td', 'insight-drill-cell');
        const drillButton = node('button', 'text-button', '查看来源');
        drillButton.type = 'button';
        drillButton.addEventListener('click', () => showInsightDrilldown(parent, drilldownParams(tool, params, item)));
        actionCell.append(drillButton);
        row.append(actionCell);
      }
      body.append(row);
    }
    table.append(caption, head, body);
    scroll.append(table);
    parent.append(scroll);
  }

  function canOpenQuickReport(mode) {
    const action = quickReportTools[mode];
    const tool = state.toolCatalog.find(item => item.name === action);
    return hasTool(action) && (!isCrossAccount() || mode === 'compare' && targetHasScope('admin_spending') && crossToolAllowed(tool));
  }

  function defaultQuickReportMode() {
    return Object.keys(quickReportTools).find(canOpenQuickReport);
  }

  function updateQuickReportControls() {
    for (const button of $$('#quickReportChoices button')) {
      const mode = button.dataset.quickReport;
      button.hidden = !canOpenQuickReport(mode);
      button.disabled = !state.period;
      button.setAttribute('aria-pressed', String(state.quickReportMode === mode));
    }
    $('#quickReportPanel').hidden = !defaultQuickReportMode();
    $('#visualizationGrid').classList.toggle('has-trend', !$('#quickReportPanel').hidden);
    $('#quickTrendUnitLabel').hidden = state.quickReportMode !== 'trend';
    const comparing = state.quickReportMode === 'compare';
    $('#quickCompareModeLabel').hidden = !comparing;
    $('#quickCompareRange').hidden = !comparing || $('#quickCompareMode').value !== 'explicit';
  }

  function clearQuickReport() {
    state.quickReportVersion++;
    state.quickReportMode = null;
    state.quickReportResponse = null;
    $('#quickReportResult').replaceChildren();
    $('#quickReportResult').hidden = true;
    setMessage($('#quickReportMessage'), '');
    updateQuickReportControls();
  }

  function shiftISODate(day, offset) {
    const date = new Date(day + 'T00:00:00Z');
    date.setUTCDate(date.getUTCDate() + offset);
    return date.toISOString().split('T')[0];
  }

  function trendBuckets(days, unit) {
    const buckets = new Map();
    for (const day of days) {
      const weekDay = new Date(day.date + 'T00:00:00Z').getUTCDay();
      const key = unit === 'week' ? shiftISODate(day.date, -((weekDay + 6) % 7)) :
        unit === 'month' ? day.date.slice(0, 7) : day.date;
      if (!buckets.has(key)) buckets.set(key, {
        start_date: day.date, end_date: day.date, expense_minor: 0, pending_minor: 0, estimated_count: 0, empty: true
      });
      const bucket = buckets.get(key);
      bucket.end_date = day.date;
      bucket.expense_minor += day.actual_expense_minor;
      bucket.pending_minor += day.pending_expense_minor;
      bucket.estimated_count += day.estimated_count;
      bucket.empty = bucket.empty && day.empty;
    }
    return Array.from(buckets.values());
  }

  function renderTrendPlot(figure, rows, unit, drillForRow) {
    const maximum = rows.reduce((max, row) => Math.max(max, row.expense_minor), 0);
    const interval = Math.max(1, maximum / 3);
    const magnitude = 10 ** Math.floor(Math.log10(interval));
    const step = magnitude * [1, 2, 5, 10].find(value => value * magnitude >= interval);
    const tickFormat = new Intl.NumberFormat('zh-CN', {notation: 'compact', maximumFractionDigits: 2});
    const plot = node('div', 'trend-plot');
    const scale = node('div', 'trend-scale');
    scale.setAttribute('aria-hidden', 'true');
    for (let index = 3; index >= 0; index--) {
      scale.append(node('span', '', tickFormat.format(index * step / 100)));
    }
    const scroll = node('div', 'trend-scroll');
    const columns = node('div', 'trend-columns');
    columns.style.setProperty('--point-count', rows.length);
    for (let line = 0; line < 2; line++) {
      const guide = node('i', 'trend-grid-line');
      guide.setAttribute('aria-hidden', 'true');
      columns.append(guide);
    }
    const readout = node('p', 'trend-readout');
    const labelGap = Math.ceil(rows.length / 6);
    for (const [index, row] of rows.entries()) {
      const date = row.start_date === row.end_date ? row.start_date : row.start_date + ' 至 ' + row.end_date;
      const details = [date, row.empty ? '无支出流水' : '支出 ' + money(row.expense_minor)];
      if (row.pending_minor > 0) details.push('待确认 ' + money(row.pending_minor));
      if (row.estimated_count) details.push(count(row.estimated_count) + ' 笔估算');
      const description = details.join(' · ');
      const column = node('div', 'trend-column');
      const drill = row.empty ? null : drillForRow(row);
      const point = node(drill ? 'button' : 'div', 'trend-point');
      const fill = node('span', 'trend-fill');
      fill.style.height = Math.max(0, row.expense_minor) / (step * 3) * 100 + '%';
      fill.setAttribute('aria-hidden', 'true');
      point.append(fill);
      point.title = description;
      point.setAttribute('aria-label', description + (drill ? '，查看来源明细' : ''));
      if (drill) {
        point.type = 'button';
        point.addEventListener('click', drill);
      } else {
        point.setAttribute('role', 'img');
      }
      point.addEventListener('focus', () => { readout.textContent = description; });
      point.addEventListener('pointerenter', () => { readout.textContent = description; });
      const tick = unit === 'month' ? row.start_date.slice(0, 7) : row.start_date.slice(5);
      const label = node('span', 'trend-date', index % labelGap === 0 ? tick : '');
      label.setAttribute('aria-hidden', 'true');
      column.append(point, label);
      columns.append(column);
    }
    scroll.append(columns);
    plot.append(scale, scroll);
    readout.textContent = rows.some(row => !row.empty) ? '点击有记录的柱形查看对应流水。' : '此期间没有支出流水。';
    figure.append(plot, readout);
  }

  function renderQuickReport() {
    const response = state.quickReportResponse;
    if (!response) return;
    const {period, data, mode} = response;
    const target = $('#quickReportResult');
    target.replaceChildren();
    target.hidden = false;
    const isTrend = mode === 'trend';
    const unit = $('#quickTrendUnit').value;
    if (!isTrend && data.comparison_available === false) {
      const message = data.unavailable_reason === 'financial_cycle_not_configured'
        ? '尚未设置发薪周期。请先到“项目预算与目标”设置发薪账期，再查看上一工资周期。'
        : '当前没有可用的对照期间。';
      target.append(node('p', 'feature-result-empty', periodLabel(period) + ' · ' + message));
      return;
    }
    const rows = isTrend ? trendBuckets(data.daily, unit) : data.periods;
    if (!rows.length) {
      target.append(node('p', 'feature-result-empty', periodLabel(period) + ' · 没有支出流水。'));
      return;
    }
    if (!isTrend && rows.length < 2) {
      target.append(node('p', 'feature-result-empty', '服务没有返回完整的两个对照期间。'));
      return;
    }

    const figure = node('figure', 'insight-chart quick-chart');
    const compareMode = $('#quickCompareMode').value;
    const comparisonTitles = {
      previous_equal_length: '本期与等长前期支出',
      previous_calendar_year: '本期与去年同期支出',
      previous_financial_cycle: '本期与上一工资周期支出',
      explicit: '本期与自选期间支出'
    };
    const title = isTrend ? {day: '按日支出', week: '按周支出', month: '按月支出'}[unit] : comparisonTitles[compareMode];
    const caption = node('figcaption', '', title);
    caption.append(node('span', 'quick-chart-caption-period', isTrend ? '已确认支出（元）' : periodLabel(period)));
    figure.append(caption);
    const canDrill = !isCrossAccount() && hasTool('get_report_drilldown');
    const drillForRow = row => canDrill ? () => showInsightDrilldown(target, {
      start_date: row.start_date, end_date: row.end_date, date_basis: period.date_basis,
      group_values: {}, filters: state.selectedCategoryId ? {kind: 'expense', category_id: state.selectedCategoryId} : {kind: 'expense'}
    }, true) : null;
    if (isTrend) {
      renderTrendPlot(figure, rows, unit, drillForRow);
      target.append(figure);
      return;
    }
    const chartRows = node('div', 'quick-chart-rows');
    const maximum = rows.reduce((max, row) => Math.max(max, Number(row.expense_minor || 0)), 0);
    for (const row of rows) {
      const dates = row.start_date === row.end_date ? row.start_date : row.start_date + ' 至 ' + row.end_date;
      const label = row.label + ' · ' + dates;
      const entry = node('div', 'quick-chart-row');
      entry.classList.toggle('is-comparison', row.role === 'comparison');
      if (row.empty) {
        const empty = node('div', 'quick-chart-empty-row');
        empty.append(node('span', '', label), node('span', '', '无支出流水 · ' + money(row.expense_minor || 0)));
        entry.append(empty);
      } else {
        addInsightBar(entry, label, row.expense_minor, maximum, drillForRow(row));
        const notes = [];
        if (row.pending_minor > 0) notes.push('待确认 ' + money(row.pending_minor));
        if (row.estimated_count > 0) notes.push(row.estimated_count + ' 笔估算');
        if (notes.length) entry.append(node('p', 'quick-chart-note', notes.join(' · ')));
      }
      chartRows.append(entry);
    }
    figure.append(chartRows);
    const comparison = rows.find(row => row.role === 'comparison') || rows[0];
    const current = rows.find(row => row.role === 'current') || rows[1];
    const difference = Number(current.expense_minor || 0) - Number(comparison.expense_minor || 0);
    figure.append(node('p', 'quick-chart-note', difference === 0 ? '两期支出相同。' :
      '本期比对照期' + (difference > 0 ? '多支出 ' : '少支出 ') + money(Math.abs(difference)) + '。'));
    target.append(figure);
  }

  async function loadQuickReport(mode) {
    if (!state.period || !canOpenQuickReport(mode)) {
      clearQuickReport();
      return;
    }
    const period = state.period;
    const version = ++state.quickReportVersion;
    state.quickReportMode = mode;
    state.quickReportResponse = null;
    $('#quickReportResult').replaceChildren();
    $('#quickReportResult').hidden = true;
    updateQuickReportControls();
    setMessage($('#quickReportMessage'), '正在读取图表…');
    try {
      let params = period;
      if (mode === 'compare') {
        const compareMode = $('#quickCompareMode').value;
        params = {mode: compareMode, start_date: period.start_date, end_date: period.end_date, date_basis: period.date_basis};
        if (state.selectedCategoryId) params.filters = {category_id: state.selectedCategoryId};
        if (compareMode === 'explicit') {
          const comparisonStart = $('#quickCompareStart').value;
          const comparisonEnd = $('#quickCompareEnd').value;
          if (!comparisonStart || !comparisonEnd || comparisonStart > comparisonEnd) {
            setMessage($('#quickReportMessage'), '请选择有效的自选对照起止日期。', true);
            return;
          }
          params.comparison_start_date = comparisonStart;
          params.comparison_end_date = comparisonEnd;
        }
      } else {
        params = {...period, window_days: 1, filters: state.selectedCategoryId ? {category_id: state.selectedCategoryId} : {}};
      }
      const result = await callTool(quickReportTools[mode], params);
      if (version !== state.quickReportVersion) return;
      state.quickReportResponse = {period, data: envelopeData(result.envelope), mode};
      renderQuickReport();
      setMessage($('#quickReportMessage'), '');
    } catch (error) {
      if (version === state.quickReportVersion) {
        setMessage($('#quickReportMessage'), errorText(error, '无法读取图表。'), true);
      }
    }
  }

  function renderInsightChart(parent, tool, rows, params) {
    if (!rows.length) return;
    const figure = node('figure', 'insight-chart');
    const title = tool.name === 'get_spending_variance' ? '两个期间的分类支出对照' :
      tool.name === 'get_rolling_spending_trend' ? '每日支出' :
      tool.name === 'get_purchase_frequency' ? '各分组消费总额' :
      tool.name === 'get_expense_style_summary' ? '支出属性构成' : '各分组支出';
    figure.append(node('figcaption', '', title));
    if (tool.name === 'get_spending_variance') {
      const maximum = Math.max(0, ...rows.flatMap(item => [Number(item.current_expense_minor || 0), Number(item.comparison_expense_minor || 0)]));
      const chartRows = node('div', 'insight-bar-list');
      for (const item of rows) {
        const entry = node('div', 'insight-pair-bars');
        entry.append(node('span', 'insight-bar-label', insightLabel(item)));
        for (const [variant, label] of [['comparison', '对照期'], ['current', '本期']]) {
          const value = Number(item[variant + '_expense_minor'] || 0);
          const selection = item[variant + '_drilldown'];
          const drill = selection && hasTool('get_report_drilldown') && !isCrossAccount()
            ? () => showInsightDrilldown(parent, drilldownParams(tool, params, item, variant)) : null;
          addInsightBar(entry, label, value, maximum, drill, insightLabel(item) + '，' + label + '支出 ' + money(value));
        }
        chartRows.append(entry);
      }
      figure.append(chartRows);
    } else {
      const field = tool.name === 'get_purchase_frequency' ? 'total_expense_minor' :
        tool.name === 'get_expense_style_summary' ? 'expense_minor' :
        tool.name === 'get_rolling_spending_trend' ? 'actual_expense_minor' : 'expense_minor';
      const maximum = Math.max(0, ...rows.map(item => Number(item[field] || 0)));
      const chartRows = node('div', 'insight-bar-list');
      for (const item of rows) {
        const drill = hasTool('get_report_drilldown') && !isCrossAccount()
          ? () => showInsightDrilldown(parent, drilldownParams(tool, params, item)) : null;
        const value = Number(item[field] || 0);
        const label = insightLabel(item);
        addInsightBar(chartRows, label, value, maximum, drill, label + '，' + money(value));
      }
      figure.append(chartRows);
    }
    parent.append(figure);
  }

  function renderInsightResult(target, tool, envelope, requestId, params) {
    const data = envelopeData(envelope);
    target.replaceChildren();
    appendFeatureResultHeader(target, tool, envelope, requestId);
    const body = node('div', 'feature-result-body');
    if (tool.name === 'get_report_drilldown') {
      appendInsightStructuredObject(body, data);
      if (!body.childElementCount) body.append(node('p', 'feature-result-empty', '此分组没有可显示的流水。'));
      target.append(body);
      target.hidden = false;
      return;
    }
    const chartActions = new Set(['get_cross_tab_report', 'get_spending_variance', 'get_purchase_frequency', 'get_expense_style_summary', 'get_rolling_spending_trend']);
    if (!chartActions.has(tool.name)) {
      appendInsightStructuredObject(body, data);
      if (!body.childElementCount) body.append(node('p', 'feature-result-empty', '服务没有返回可展示的报表结果。'));
      target.append(body);
      target.hidden = false;
      return;
    }
    const rowKey = tool.name === 'get_rolling_spending_trend' ? 'daily' :
      'items';
    const rows = Array.isArray(data[rowKey]) ? data[rowKey] : [];
    const summary = node('dl', 'feature-result-list insight-summary');
    for (const [key, value] of Object.entries(data)) {
      if (Array.isArray(value) || value && typeof value === 'object') continue;
      appendResultValue(summary, key, value);
    }
    if (summary.childElementCount) body.append(summary);
    renderInsightChart(body, tool, rows, params || {});
    if (rows.length) renderInsightTable(body, tool, rows, params || {});
    else body.append(node('p', 'feature-result-empty', data.insufficient_data ? '该区间有效样本不足；页面未补造缺失日期。' : data.empty ? '此期间没有符合条件的流水。' : '此期间没有可展示的分组。'));
    const pendingMinor = Number(data.pending_expense_minor || 0);
    if (pendingMinor > 0) body.append(node('p', 'insight-state-note', '另有待确认金额：' + money(pendingMinor)));
    target.append(body);
    if (data.file_id) {
      const download = node('a', 'button button-secondary feature-result-download', data.filename ? '下载 ' + data.filename : '下载导出文件');
      download.href = '/api/files/' + encodeURIComponent(String(data.file_id));
      download.setAttribute('download', data.filename || '余序账本导出');
      target.append(download);
    }
    target.hidden = false;
  }

  function appendInsightStructuredObject(parent, object, depth = 0) {
    const fields = node('dl', depth ? 'feature-result-list feature-result-nested-list insight-detail-fields' : 'feature-result-list insight-detail-fields');
    for (const [key, value] of Object.entries(object || {})) {
      if (resultFieldHidden(key) || value === undefined) continue;
      if (Array.isArray(value)) {
        if (!value.length) continue;
        const field = node('div', 'feature-result-field insight-detail-group');
        field.append(node('dt', '', resultFieldLabel(key)));
        const details = node('dd', 'feature-result-nested');
        if (value.every(item => item && typeof item === 'object' && !Array.isArray(item))) {
          const list = node('div', 'insight-detail-list');
          for (const item of value) {
            const card = node('article', 'insight-detail-card');
            const title = item.label || item.name || item.category_path || item.merchant || item.date || item.month || item.id ||
              (item.type && resultScalarText('type', item.type));
            if (title) card.append(node('h4', '', String(title)));
            appendInsightStructuredObject(card, item, depth + 1);
            list.append(card);
          }
          details.append(list);
        } else {
          const list = node('ul', 'feature-result-items');
          for (const item of value) list.append(node('li', '', previewValueText(key, item)));
          details.append(list);
        }
        field.append(details);
        fields.append(field);
      } else if (value && typeof value === 'object') {
        const field = node('div', 'feature-result-field');
        field.append(node('dt', '', resultFieldLabel(key)));
        const nested = node('dd', 'feature-result-nested');
        if (depth < 5) appendInsightStructuredObject(nested, value, depth + 1);
        else nested.textContent = '包含 ' + Object.keys(value).filter(childKey => !resultFieldHidden(childKey)).length + ' 项';
        field.append(nested);
        fields.append(field);
      } else {
        appendResultValue(fields, key, value, depth);
      }
    }
    if (fields.childElementCount) parent.append(fields);
  }

  function scopeLabel(scope) {
    return ({own_read: '本人账本只读', own_write: '本人账本可写', admin_spending: '授权支出汇总只读', admin_goal: '授权储蓄目标只读',
      maintenance: '本机维护'})[scope] || hostPermissionLabels[scope] || chineseText(scope) || '其他权限';
  }

  function renderAccessPolicy(target, data) {
    const sections = [
      ['本人账本设置', data.account ? [data.account] : []],
      ['他人获准进入本人账本（入向）', Array.isArray(data.incoming) ? data.incoming : []],
      ['本人获准进入他人账本（出向）', Array.isArray(data.outgoing) ? data.outgoing : []]
    ];
    for (const [title, rows] of sections) {
      const section = node('section', 'access-policy-section');
      section.append(node('h4', '', title));
      const list = node('ul', 'access-policy-list');
      if (!rows.length) list.append(node('li', 'feature-result-empty', '当前没有此类授权记录。'));
      for (const item of rows) {
        const scopes = Array.isArray(item.scopes) ? item.scopes : item.scopes ? [item.scopes] : [];
        const line = node('li');
        line.append(node('strong', '', item.display_name || '已授权账号'), node('span', '', scopes.length ? scopes.map(scopeLabel).join('、') : '未列出可用范围'));
        list.append(line);
      }
      section.append(list);
      target.append(section);
    }
    target.append(node('p', 'feature-result-empty', '账本授权在本页只读显示；本页不会创建或撤销他人的账本访问权限。'));
  }

  function renderHostTokens(target, data) {
    const rows = data.tokens || data.hosts || data.items || [];
    if (!Array.isArray(rows) || !rows.length) {
      target.append(node('p', 'feature-result-empty', '当前没有登记的 MCP 主机授权。'));
      return;
    }
    const list = node('div', 'host-token-list');
    for (const item of rows) {
      const row = node('article', 'host-token-row');
      const details = node('div', 'host-token-copy');
      details.append(node('h4', '', item.label || 'MCP 主机'), node('p', '', (item.scopes || []).map(scopeLabel).join('、') || '没有已开放权限'));
      details.append(node('p', 'feature-action-meta', item.revoked_at ? '已撤销' : '登记于 ' + (item.created_at || '时间未提供')));
      const actions = node('div', 'host-token-actions');
      if (!item.revoked_at) {
        const edit = node('button', 'button button-secondary', '调整权限');
        edit.type = 'button';
        edit.addEventListener('click', () => openFeatureActionWithValues('set_host_token_scopes', {token_id: item.token_id, expected_version: item.version, scopes: item.scopes || []}, 'security'));
        const revoke = node('button', 'button button-quiet', '撤销主机');
        revoke.type = 'button';
        revoke.addEventListener('click', () => openFeatureActionWithValues('revoke_host_token', {token_id: item.token_id, expected_version: item.version}, 'security'));
        actions.append(edit);
        if (hasTool('rotate_host_token')) {
          const rotate = node('button', 'button button-secondary', '换发密钥');
          rotate.type = 'button';
          rotate.addEventListener('click', () => openFeatureActionWithValues('rotate_host_token', {token_id: item.token_id}, 'security'));
          actions.append(rotate);
        }
        actions.append(revoke);
      }
      row.append(details, actions);
      list.append(row);
    }
    target.append(list);
  }

  function renderOneTimeHostToken(target, data) {
    const panel = $('#oneTimeHostTokenPanel');
    const value = $('#oneTimeHostTokenValue');
    value.value = typeof data.token === 'string' ? data.token : '';
    panel.hidden = !value.value;
    if (!value.value) {
      target.append(node('p', 'feature-confirm-note is-error', '服务没有返回新密钥明文。密钥无法再次读取；请重新换发并在本机页面确认收到后再离开。'));
      return;
    }
    setMessage($('#oneTimeHostTokenMessage'), '请现在复制密钥并保存到目标主机。离开授权页或退出登录后，本页会清除明文。');
    target.append(node('p', 'feature-confirm-note', (data.label || 'MCP 主机') + ' 的密钥已换发。明文只在这次响应中显示。'));
    $('#securityView').scrollIntoView({block: 'start', behavior: 'smooth'});
    value.focus({preventScroll: true});
    value.select();
  }

  function clearOneTimeHostToken() {
    $('#oneTimeHostTokenValue').value = '';
    $('#oneTimeHostTokenPanel').hidden = true;
    $('#oneTimeHostTokenMessage').textContent = '';
  }

  function renderSavedDraftResult(target, data) {
    const draft = data.draft;
    if (!draft || typeof draft !== 'object') {
      appendResultObject(target, data);
      return;
    }
    const fields = draft.fields || {};
    const card = node('section', 'copied-draft-result');
    card.append(node('h4', '', '流水草稿已保存'));
    const details = node('dl', 'feature-result-list');
    for (const [label, value] of [
      ['收支方向', fields.kind === 'income' ? '收入' : fields.kind === 'expense' ? '支出' : '未填写'],
      ['商家', fields.merchant || '未填写'],
      ['新金额', Number.isInteger(fields.amount_minor) ? money(fields.amount_minor) : '仍需填写'],
      ['新发生日期', fields.occurred_on || '仍需填写'],
      ['分类', fields.category_id ? categoryName(fields.category_id) || String(fields.category_id) : '未分类'],
      ['标签', Array.isArray(fields.tags) && fields.tags.length ? fields.tags.join(' · ') : '无']
    ]) details.append(node('dt', '', label), node('dd', '', value));
    card.append(details);
    const isCopied = state.draftCopyContext && String(state.draftCopyContext.draft_id) === String(draft.draft_id);
    if (isCopied) card.append(node('p', 'feature-confirm-note', '旧流水只提供商家、分类、标签和收支方向。请确认金额与发生日期是这笔新流水的值。'));
    const complete = Number.isInteger(fields.amount_minor) && fields.amount_minor > 0 && /^\d{4}-\d{2}-\d{2}$/.test(String(fields.occurred_on || ''));
    if (complete && isCopied) {
      const commit = node('button', 'button button-primary', '确认并记账');
      commit.type = 'button';
      commit.addEventListener('click', async () => {
        state.draftCommitContext = draft;
        await openFeatureActionWithValues('commit_transaction_draft', {draft_id: draft.draft_id, expected_version: draft.version}, 'flow');
      });
      card.append(commit);
    } else if (isCopied) {
      const edit = node('button', 'button button-secondary', '补充新金额和发生日期');
      edit.type = 'button';
      edit.addEventListener('click', () => openFeatureActionWithValues('upsert_transaction_draft', {
        draft_id: draft.draft_id,
        expected_version: draft.version,
        fields
      }, 'flow'));
      card.append(edit);
    }
    target.append(card);
  }

  function renderSecurityAccess(target, data) {
    const rows = data.events || data.access || data.entries || data.items || data.records || [];
    if (!Array.isArray(rows) || !rows.length) {
      target.append(node('p', 'feature-result-empty', '此日期范围内没有访问记录。'));
      return;
    }
    const scroll = node('div', 'table-scroll');
    const table = node('table', 'data-table feature-result-table security-access-table');
    table.append(node('caption', 'visually-hidden', '本人账本的入向和出向访问记录'));
    const head = document.createElement('thead');
    const header = document.createElement('tr');
    for (const label of ['时间', '方向', '主机或来源', '权限', '操作', '资源范围', '结果']) header.append(node('th', '', label));
    head.append(header);
    const body = document.createElement('tbody');
    for (const item of rows) {
      const row = document.createElement('tr');
      const direction = item.direction === 'incoming' ? '入向 · 他人进入本人账本' : item.direction === 'outgoing' ? '出向 · 本人进入授权账本' : '未知方向';
      const source = item.source === 'web' ? '本机网页' : [item.host_label || item.host, sourceLabels[item.source]].filter(Boolean).join(' · ');
      const range = [item.range_start, item.range_end].filter(Boolean).join(' 至 ');
      const permissions = Array.isArray(item.permission) ? item.permission : [item.permission].filter(Boolean);
      const resource = ({action: '账本操作', backup: '账本备份'})[item.resource_kind];
      const outcome = runtimeLogStatus(item);
      for (const value of [item.created_at || item.at || '时间未记录', direction, source || '未知来源',
        permissions.map(scopeLabel).join('、') || '未列出权限', runtimeLogAction(item),
        [resource, range].filter(Boolean).join(' · ') || '未标明资源范围', outcome]) row.append(node('td', '', value));
      body.append(row);
    }
    table.append(head, body);
    scroll.append(table);
    target.append(scroll);
    target.append(node('p', 'feature-result-empty', '此视图不显示资源 ID、请求内容、流水金额或备注。'));
  }

  function renderMaintenanceIssues(target, data) {
    const issues = data.issues || data.items || [];
    if (!Array.isArray(issues) || !issues.length) {
      target.append(node('p', 'feature-result-empty', '未发现账本一致性问题。'));
      return;
    }
    const list = node('div', 'maintenance-issue-list');
    for (const issue of issues) {
      const card = node('article', 'maintenance-issue-row');
      const copy = node('div', 'maintenance-issue-copy');
      copy.append(node('h4', '', maintenanceIssueLabels[issue.type] || '账本一致性问题'));
      const summary = chineseText(issue.summary);
      if (summary) copy.append(node('p', '', summary));
      if (issue.affected && typeof issue.affected === 'object') appendResultObject(copy, issue.affected);
      card.append(copy);
      const repairs = Array.isArray(issue.repair_options) ? issue.repair_options : [];
      if (issue.repairable && repairs.length && hasTool('repair_maintenance_issue')) {
        const actions = node('div', 'maintenance-repair-options');
        for (const option of repairs) {
          const resolution = typeof option === 'string' ? {type: option} : option.resolution || option;
          const label = chineseText(option.label || option.title || option.summary) || enumLabels.type[resolution.type] || '核对修复方案';
          const button = node('button', 'button button-secondary', '检查：' + label);
          button.type = 'button';
          button.addEventListener('click', () => openFeatureActionWithValues('repair_maintenance_issue', {
            issue_id: issue.issue_id,
            issue_fingerprint: issue.issue_fingerprint,
            confirmed: true,
            resolution
          }, 'maintenance'));
          actions.append(button);
        }
        card.append(actions);
      } else card.append(node('p', 'feature-result-empty', '当前没有可确认的修复方案。'));
      list.append(card);
    }
    target.append(list);
    target.append(node('p', 'feature-confirm-note', '修复会逐项提交。打开方案后仍需在确认窗口复核，且服务会再次校验问题指纹。'));
  }

  function renderFeatureResult(group, tool, envelope, requestId, params = {}) {
    const target = $('#featureResult-' + group);
    if (!target) return;
    const data = envelopeData(envelope);
    target.replaceChildren();
    if (group === 'insights') {
      renderInsightResult(target, tool, envelope, requestId, params);
      return;
    }
    if (tool.name === 'upsert_transaction_draft' && state.draftCopyContext && String(params.draft_id) === String(state.draftCopyContext.draft_id)) {
      appendFeatureResultHeader(target, tool, envelope, requestId);
      renderSavedDraftResult(target, data);
      target.hidden = false;
      return;
    }
    appendFeatureResultHeader(target, tool, envelope, requestId);
    const body = node('div', 'feature-result-body');
    if (tool.name === 'get_account_access_policy') renderAccessPolicy(body, data);
    else if (tool.name === 'list_host_tokens') renderHostTokens(body, data);
    else if (tool.name === 'create_host_token' || tool.name === 'rotate_host_token') renderOneTimeHostToken(body, data);
    else if (tool.name === 'list_security_access') renderSecurityAccess(body, data);
    else if (tool.name === 'preview_maintenance_issues') renderMaintenanceIssues(body, data);
    else appendResultObject(body, data);
    if (tool.name === 'authorize_private_transaction_fields') {
      body.append(node('p', 'feature-confirm-note', '单次字段授权已生效。请在到期时间前由所选主机读取指定流水；授权会在一次读取后失效。'));
    }
    if (!body.childElementCount) body.append(node('p', 'feature-result-empty', '操作已完成，服务没有返回其他明细。'));
    target.append(body);
    if (data.file_id) {
      const download = node('a', 'button button-secondary feature-result-download', data.filename ? '下载 ' + data.filename : '下载导出文件');
      download.href = '/api/files/' + encodeURIComponent(String(data.file_id));
      download.setAttribute('download', data.filename || '余序账本导出');
      target.append(download);
    }
    target.hidden = false;
  }

  function resetImportWorkflow() {
    state.importDraft = null;
    state.reconciliationDraft = null;
    $('#csvImportMapping').replaceChildren();
    $('#csvImportMapping').hidden = true;
    $('#csvTemplateControls').hidden = true;
    $('#csvImportOptions').hidden = true;
    $('#csvImportPreview').replaceChildren();
    $('#csvImportPreview').hidden = true;
    $('#csvReconciliationPreview').replaceChildren();
    $('#csvReconciliationPreview').hidden = true;
    $('#csvImportConfirmButton').hidden = true;
    $('#csvImportConfirmButton').disabled = true;
    $('#csvReconcileConfirmButton').hidden = true;
    $('#csvReconcileConfirmButton').disabled = true;
  }

  function readFileBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.addEventListener('load', () => resolve(String(reader.result).split(',')[1] || ''));
      reader.addEventListener('error', () => reject(new Error('无法读取所选文件。')));
      reader.readAsDataURL(file);
    });
  }

  function importAction(name) {
    const tool = state.toolCatalog.find(item => item.name === name);
    if (!tool || !crossToolAllowed(tool)) throw new Error('当前登录范围没有文件导入权限。');
    return tool;
  }

  function applyCsvMode(mode) {
    state.csvMode = mode === 'reconcile' ? 'reconcile' : 'import';
    $('#csvImportWorkspace').dataset.mode = state.csvMode;
    $('#csvModeImport').setAttribute('aria-pressed', String(state.csvMode === 'import'));
    $('#csvModeReconcile').setAttribute('aria-pressed', String(state.csvMode === 'reconcile'));
    $('#csvImportPreview').hidden = state.csvMode !== 'import' || !state.importDraft || !state.importDraft.previewId;
    $('#csvReconciliationPreview').hidden = state.csvMode !== 'reconcile' || !state.reconciliationDraft;
    $('#csvImportConfirmButton').hidden = state.csvMode !== 'import' || !state.importDraft || !state.importDraft.previewId;
    $('#csvReconcileConfirmButton').hidden = state.csvMode !== 'reconcile' || !state.reconciliationDraft;
  }

  function invalidateCsvPreviews() {
    if (state.importDraft) {
      state.importDraft.previewId = null;
      state.importDraft.previewSha256 = null;
      state.importDraft.insertCount = 0;
    }
    state.reconciliationDraft = null;
    $('#csvImportPreview').replaceChildren();
    $('#csvImportPreview').hidden = true;
    $('#csvReconciliationPreview').replaceChildren();
    $('#csvReconciliationPreview').hidden = true;
    $('#csvImportConfirmButton').hidden = true;
    $('#csvImportConfirmButton').disabled = true;
    $('#csvReconcileConfirmButton').hidden = true;
    $('#csvReconcileConfirmButton').disabled = true;
  }

  function currentCsvMapping() {
    const mapping = {};
    for (const control of $$('#csvImportMapping [data-import-field]')) if (control.value) mapping[control.dataset.importField] = control.value;
    return mapping;
  }

  function csvColumnsMatch(left, right) {
    return Array.isArray(left) && Array.isArray(right) && left.length === right.length && left.every((column, index) => String(column).trim() === String(right[index]).trim());
  }

  function renderCsvTemplateOptions(selectedId = state.selectedCsvTemplateId) {
    const select = $('#csvTemplateSelect');
    select.replaceChildren(node('option', '', '不使用模板'));
    select.options[0].value = '';
    for (const template of state.csvTemplates) {
      const option = node('option', '', template.name + ' · v' + template.version);
      option.value = String(template.template_id);
      select.append(option);
    }
    select.value = selectedId && state.csvTemplates.some(item => String(item.template_id) === String(selectedId)) ? String(selectedId) : '';
    state.selectedCsvTemplateId = select.value || null;
    $('#deleteCsvTemplateButton').hidden = !state.selectedCsvTemplateId || !hasTool('delete_csv_mapping_template');
    $('#saveCsvTemplateButton').disabled = !hasTool('upsert_csv_mapping_template') || !state.importDraft;
    $('#saveCsvTemplateButton').textContent = state.selectedCsvTemplateId ? '更新所选模板' : '保存为新模板';
  }

  async function loadCsvMappingTemplates(preselectId = null) {
    if (!hasTool('list_csv_mapping_templates') || isCrossAccount()) {
      state.csvTemplates = [];
      state.entityOptions.csv_mapping_templates = [];
      renderCsvTemplateOptions(null);
      return;
    }
    try {
      const result = await callTool('list_csv_mapping_templates');
      state.csvTemplates = envelopeData(result.envelope).templates || [];
      state.entityOptions.csv_mapping_templates = optionRowsFor('csv_mapping_templates', envelopeData(result.envelope));
      const filenameBase = state.importDraft && state.importDraft.file.name.replace(/\.[^.]+$/, '').trim().toLowerCase();
      const exact = state.importDraft && state.csvTemplates.find(item => csvColumnsMatch(item.headers, state.importDraft.columns));
      const sameName = filenameBase && state.csvTemplates.find(item => String(item.name).trim().toLowerCase() === filenameBase);
      const selectedId = preselectId || exact && exact.template_id || sameName && sameName.template_id || state.selectedCsvTemplateId;
      renderCsvTemplateOptions(selectedId);
      if (exact && String(exact.template_id) === String($('#csvTemplateSelect').value)) applySelectedCsvTemplate();
      else if (sameName && String(sameName.template_id) === String($('#csvTemplateSelect').value)) {
        setMessage($('#csvTemplateMessage'), '此文件名已有模板，但表头已变化。旧映射未套用；请重新映射后再明确更新模板。', true);
      } else if (!exact && state.csvTemplates.length) {
        setMessage($('#csvTemplateMessage'), '此 CSV 表头与已保存模板不匹配；不会自动套用。请重新映射，或选择来源模板确认是否更新。', true);
      }
    } catch (error) {
      state.csvTemplates = [];
      renderCsvTemplateOptions(null);
      setMessage($('#csvTemplateMessage'), errorText(error, '无法读取 CSV 来源模板。'), true);
    }
  }

  function applySelectedCsvTemplate() {
    invalidateCsvPreviews();
    const selectedId = $('#csvTemplateSelect').value;
    state.selectedCsvTemplateId = selectedId || null;
    $('#deleteCsvTemplateButton').hidden = !selectedId || !hasTool('delete_csv_mapping_template');
    const template = state.csvTemplates.find(item => String(item.template_id) === String(selectedId));
    if (!template || !state.importDraft) return setMessage($('#csvTemplateMessage'), '未使用已保存的来源模板。');
    if (!csvColumnsMatch(template.headers, state.importDraft.columns)) {
      setMessage($('#csvTemplateMessage'), 'CSV 表头与模板不一致。模板未套用；请重新映射后再明确更新模板。', true);
      return;
    }
    for (const control of $$('#csvImportMapping [data-import-field]')) {
      const value = template.column_mapping && template.column_mapping[control.dataset.importField];
      control.value = value && state.importDraft.columns.includes(value) ? value : '';
    }
    $('#csvDateFormat').value = template.date_format || '';
    $('#csvAmountUnit').value = template.amount_unit || '';
    setMessage($('#csvTemplateMessage'), '已按完全匹配的表头套用模板。请检查映射、日期格式和金额单位。');
  }

  function openSaveCsvMappingTemplate() {
    const draft = state.importDraft;
    if (!draft) return setMessage($('#csvTemplateMessage'), '请先读取 CSV 表头。', true);
    const mapping = currentCsvMapping();
    if (!mapping.occurred_on || !mapping.kind || !mapping.amount || !$('#csvDateFormat').value || !$('#csvAmountUnit').value) {
      setMessage($('#csvTemplateMessage'), '请完成发生日期、收支方向、金额、日期格式和金额单位后再保存。', true);
      return;
    }
    const current = state.csvTemplates.find(item => String(item.template_id) === String($('#csvTemplateSelect').value));
    const values = {
      name: current && current.name || draft.file.name.replace(/\.[^.]+$/, ''),
      headers: draft.columns,
      column_mapping: mapping,
      date_format: $('#csvDateFormat').value,
      amount_unit: $('#csvAmountUnit').value
    };
    if (current) {
      values.template_id = current.template_id;
      values.expected_version = current.version;
    }
    openFeatureActionWithValues('upsert_csv_mapping_template', values, 'maintenance');
  }

  function deleteSelectedCsvMappingTemplate() {
    const template = state.csvTemplates.find(item => String(item.template_id) === String($('#csvTemplateSelect').value));
    if (!template) return;
    openFeatureActionWithValues('delete_csv_mapping_template', {template_id: template.template_id, expected_version: template.version}, 'maintenance');
  }

  function addCsvColumnMapping(target, columns, suggested = {}) {
    const fields = [
      ['occurred_on', '发生日期', true], ['charged_on', '扣款 / 入账日期', false], ['kind', '收支方向', true], ['amount', '金额', true],
      ['merchant', '商家', false], ['note', '备注', false], ['external_id', '订单号', false], ['payment_status', '支付状态', false]
    ];
    const fieldset = node('fieldset', 'schema-fieldset import-mapping-fields');
    fieldset.append(node('legend', '', '把账本字段对应到 CSV 列'));
    for (const [key, label, required] of fields) {
      const wrapper = node('label', 'schema-field');
      wrapper.append(node('span', 'schema-label', label + (required ? ' · 必填' : '')));
      const select = node('select', 'schema-control');
      select.dataset.importField = key;
      if (!required) {
        const empty = node('option', '', '不导入此字段');
        empty.value = '';
        select.append(empty);
      } else {
        const placeholder = node('option', '', '请选择 CSV 列');
        placeholder.value = '';
        select.append(placeholder);
      }
      for (const column of columns) {
        const option = node('option', '', column);
        option.value = column;
        select.append(option);
      }
      select.required = required;
      if (suggested[key]) select.value = suggested[key];
      wrapper.append(select);
      fieldset.append(wrapper);
    }
    target.replaceChildren(fieldset);
    target.hidden = false;
    const options = $('#csvDuplicatePolicy');
    options.replaceChildren();
    for (const [value, label] of [['skip', '跳过与现有流水重复的行'], ['include', '保留全部有效行']]) {
      const option = node('option', '', label);
      option.value = value;
      options.append(option);
    }
    options.value = 'skip';
    $('#csvDateFormat').value = '';
    $('#csvAmountUnit').value = '';
    $('#csvTemplateControls').hidden = false;
    $('#csvImportOptions').hidden = false;
    $('#csvReconcileStartDate').value = state.period && state.period.start_date || currentMonthRange().start_date;
    $('#csvReconcileEndDate').value = state.period && state.period.end_date || currentMonthRange().end_date;
    state.selectedCsvTemplateId = null;
    renderCsvTemplateOptions(null);
  }

  async function readCsvColumns() {
    const file = $('#csvImportFile').files[0];
    resetImportWorkflow();
    setMessage($('#csvImportMessage'), '');
    if (!file) {
      setMessage($('#csvImportMessage'), '请先选择本机 CSV 文件。', true);
      return;
    }
    const button = $('#csvReadColumnsButton');
    button.disabled = true;
    setMessage($('#csvImportMessage'), '正在读取表头并校验 CSV 编码…');
    try {
      const contentBase64 = await readFileBase64(file);
      const tool = importAction('preview_transaction_import');
      const result = await callTool(tool.name, {file_name: file.name, content_base64: contentBase64}, tool.scope === 'own_write');
      const data = envelopeData(result.envelope);
      if (!Array.isArray(data.columns)) throw new Error('服务没有返回可用的 CSV 列名。');
      state.importDraft = {file, contentBase64, columns: data.columns.map(String), sourceSha256: data.source_sha256};
      addCsvColumnMapping($('#csvImportMapping'), state.importDraft.columns, data.suggested_mapping || {});
      $('#csvStatementProvider').value = data.suggested_provider || '';
      await loadCsvMappingTemplates(null);
      await loadPaymentAccountOptions();
      applyCsvMode(state.csvMode);
      setMessage($('#csvImportMessage'), '读取到 ' + count(data.row_count || 0) + ' 行。请完成字段映射和金额单位，再生成导入预览。');
    } catch (error) {
      resetImportWorkflow();
      setMessage($('#csvImportMessage'), errorText(error, '无法读取 CSV 表头。'), true);
    } finally {
      button.disabled = false;
    }
  }

  function renderImportPreview(data) {
    const preview = $('#csvImportPreview');
    preview.replaceChildren();
    preview.append(node('h3', '', '导入影响预览'));
    const duplicateCount = Number(data.duplicate_count ?? (data.duplicates || []).length ?? 0);
    const summary = node('dl', 'feature-preview-list');
    for (const [label, value] of [
      ['可识别行数', count(data.valid_count || 0)], ['将新增流水', count(data.insert_count || 0)],
      ['无效行', count((data.invalid_rows || []).length)], ['疑似重复行', count(duplicateCount)],
      ['按设置跳过重复行', count(data.skipped_duplicate_count ?? data.skipped_duplicates ?? 0)]
    ]) summary.append(node('dt', '', label), node('dd', '', value));
    preview.append(summary);
    if (Array.isArray(data.invalid_rows) && data.invalid_rows.length) {
      const section = node('section', 'import-issue-list');
      section.append(node('h4', '', '需要处理的无效行'));
      const list = node('ul');
      for (const item of data.invalid_rows.slice(0, 20)) list.append(node('li', '', csvIssueText(item)));
      section.append(list);
      if (data.invalid_rows.length > 20) section.append(node('p', 'feature-action-meta', '其余 ' + count(data.invalid_rows.length - 20) + ' 行异常可在服务预览结果中查看。'));
      preview.append(section);
    }
    if (Array.isArray(data.duplicates) && data.duplicates.length) {
      const section = node('section', 'import-issue-list');
      section.append(node('h4', '', '疑似重复的行'));
      const list = node('ul');
      for (const item of data.duplicates.slice(0, 20)) list.append(node('li', '', 'CSV 第 ' + (item.line || item.row || '未知行') + ' 行 · 找到 ' + count((item.candidate_transaction_ids || item.candidates || []).length) + ' 笔相似流水'));
      section.append(list);
      preview.append(section);
    }
    preview.append(node('p', 'feature-confirm-note', '请检查新增数量、异常行及重复策略。正式账本尚未变更；点击“确认导入”后才会写入。'));
    preview.hidden = false;
  }

  const reconciliationStatusLabels = {
    matched: '一致', csv_only: 'CSV 独有', ledger_only: '账本独有',
    amount_mismatch: '金额不一致', ambiguous: '有歧义'
  };

  function csvReconciliationRecord(record) {
    if (!record) return '—';
    return [
      record.comparison_date || record.occurred_on || record.charged_on || '',
      record.kind === 'income' ? '收入' : record.kind === 'expense' ? '支出' : '',
      record.amount_minor === undefined ? '' : money(record.amount_minor),
      record.merchant || ''
    ].filter(Boolean).join(' · ') || '—';
  }

  function updateReconciliationSelection() {
    const selected = $$('#csvReconciliationPreview [data-csv-line]:checked').map(control => Number(control.dataset.csvLine));
    const button = $('#csvReconcileConfirmButton');
    button.disabled = selected.length === 0;
    button.textContent = selected.length
      ? '确认仅入账所选 ' + count(selected.length) + ' 条 CSV 独有行'
      : '确认仅入账勾选的 CSV 独有行';
  }

  function renderReconciliationPreview(data) {
    const preview = $('#csvReconciliationPreview');
    preview.replaceChildren(node('h3', '', 'CSV 对账结果'));
    const scope = data.scope || {};
    const basis = data.date_basis === 'charged_on' ? '扣款日期' : '消费日期';
    preview.append(node('p', 'feature-action-meta', '范围：' + (scope.start_date || '—') + ' 至 ' + (scope.end_date || '—') + ' · 按' + basis + (scope.payment_account_name ? ' · ' + scope.payment_account_name : ' · 当前账本全部流水')));
    const summary = data.summary || {};
    const counts = node('dl', 'feature-preview-list reconciliation-summary');
    for (const [key, label] of [
      ['matched_count', '一致'], ['csv_only_count', 'CSV 独有'], ['ledger_only_count', '账本独有'],
      ['amount_mismatch_count', '金额不一致'], ['ambiguous_count', '有歧义'],
      ['invalid_count', '无效行'], ['out_of_range_count', '超出日期窗口']
    ]) counts.append(node('dt', '', label), node('dd', '', count(summary[key] || 0)));
    preview.append(counts);
    const items = Array.isArray(data.items) ? data.items : [];
    if (items.length) {
      const scroll = node('div', 'table-scroll reconciliation-table-scroll');
      const table = node('table', 'data-table reconciliation-table');
      table.append(node('caption', 'visually-hidden', 'CSV 与账本记录的对账分类'));
      const head = document.createElement('thead');
      const header = document.createElement('tr');
      for (const label of ['选择', 'CSV 行', '状态', 'CSV 内容', '账本内容', '提示']) header.append(node('th', '', label));
      head.append(header);
      const body = document.createElement('tbody');
      for (const item of items) {
        const row = document.createElement('tr');
        const selection = node('td');
        if (item.status === 'csv_only' && Number.isInteger(item.csv_line)) {
          const label = node('label', 'reconciliation-select-label');
          const checkbox = node('input');
          checkbox.type = 'checkbox';
          checkbox.dataset.csvLine = String(item.csv_line);
          checkbox.setAttribute('aria-label', '勾选 CSV 第 ' + item.csv_line + ' 行为 CSV 独有记录');
          checkbox.addEventListener('change', updateReconciliationSelection);
          label.append(checkbox, node('span', '', '入账'));
          selection.append(label);
        } else selection.textContent = '—';
        const line = node('td', '', item.csv_line === null || item.csv_line === undefined ? '—' : '第 ' + item.csv_line + ' 行');
        const status = node('td');
        status.append(node('span', 'reconciliation-status reconciliation-status-' + String(item.status || 'unknown'), reconciliationStatusLabels[item.status] || '未知'));
        const csv = node('td', '', csvReconciliationRecord(item.csv));
        const ledger = node('td', '', csvReconciliationRecord(item.ledger));
        let detail = Array.isArray(item.differences) ? item.differences.map(value => ({payment_account: '支付账户', duplicate_candidates: '重复候选', merchant: '商家', confirmation_status: '待确认状态', amount_minor: '金额', date_basis: '日期口径'}[value] || resultFieldLabel(value))).join('、') : '';
        if (Array.isArray(item.candidate_transaction_ids) && item.candidate_transaction_ids.length) {
          detail = (detail ? detail + ' · ' : '') + count(item.candidate_transaction_ids.length) + ' 个候选';
        }
        row.append(selection, line, status, csv, ledger, node('td', 'reconciliation-detail', detail || '—'));
        body.append(row);
      }
      table.append(head, body);
      scroll.append(table);
      preview.append(scroll);
    } else preview.append(node('p', 'empty-cell', '所选日期窗口内没有可对照的 CSV 或账本流水。'));
    const invalidRows = Array.isArray(data.invalid_rows) ? data.invalid_rows : [];
    if (invalidRows.length) {
      const issues = node('section', 'import-issue-list');
      issues.append(node('h4', '', '未参与对账的无效行'));
      const list = node('ul');
      for (const item of invalidRows.slice(0, 100)) list.append(node('li', '', csvIssueText(item)));
      issues.append(list);
      preview.append(issues);
    }
    preview.append(node('p', 'feature-confirm-note', '预览不会写入账本。只有状态为“CSV 独有”的行可勾选；一致、账本独有、金额不一致、有歧义和无效行不能通过此确认入账。'));
    preview.hidden = false;
  }

  function csvIssueText(item) {
    const line = item.line || item.row;
    const row = line ? 'CSV 第 ' + line + ' 行' : 'CSV 行号未知';
    const field = item.field && item.field !== 'row' ? fieldLabel(item.field) + '：' : '';
    return row + ' · ' + field + errorText({message: item.error}, '无法识别此行，请核对原表格。');
  }

  async function previewCsvImport() {
    const draft = state.importDraft;
    if (!draft) {
      setMessage($('#csvImportMessage'), '请先读取 CSV 表头。', true);
      return;
    }
    const dateFormat = $('#csvDateFormat');
    const amountUnit = $('#csvAmountUnit');
    const columnMapping = {};
    for (const control of $$('#csvImportMapping [data-import-field]')) if (control.value) columnMapping[control.dataset.importField] = control.value;
    if ($$('#csvImportMapping select[required]').some(control => !control.checkValidity())) {
      $$('#csvImportMapping select[required]').find(control => !control.checkValidity())?.reportValidity();
      return;
    }
    if (!dateFormat.value || !amountUnit.value) {
      setMessage($('#csvImportMessage'), '请选择日期格式和金额单位。', true);
      return;
    }
    const button = $('#csvPreviewButton');
    button.disabled = true;
    $('#csvImportConfirmButton').hidden = true;
    $('#csvImportConfirmButton').disabled = true;
    $('#csvReconcileConfirmButton').hidden = true;
    $('#csvReconcileConfirmButton').disabled = true;
    state.reconciliationDraft = null;
    $('#csvReconciliationPreview').replaceChildren();
    $('#csvReconciliationPreview').hidden = true;
    setMessage($('#csvImportMessage'), '正在检查行内容、重复项和导入范围…');
    try {
      const tool = importAction('preview_transaction_import');
      const params = {
        file_name: draft.file.name,
        content_base64: draft.contentBase64,
        column_mapping: columnMapping,
        date_format: dateFormat.value,
        amount_unit: amountUnit.value,
        duplicate_policy: $('#csvDuplicatePolicy').value
      };
      const result = await callTool(tool.name, params, tool.scope === 'own_write');
      const data = envelopeData(result.envelope);
      if (!data.preview_id || !data.preview_sha256) throw new Error('服务未返回可确认的预览凭据，本页不会提交导入。');
      draft.previewId = data.preview_id;
      draft.previewSha256 = data.preview_sha256;
      const duplicateCount = Number(data.duplicate_count ?? (data.duplicates || []).length ?? 0);
      draft.insertCount = Number(data.insert_count ?? Math.max(0, Number(data.valid_count || 0) - ($('#csvDuplicatePolicy').value === 'skip' ? duplicateCount : 0)));
      data.insert_count = draft.insertCount;
      renderImportPreview(data);
      const confirm = $('#csvImportConfirmButton');
      confirm.hidden = draft.insertCount <= 0;
      confirm.disabled = draft.insertCount <= 0;
      setMessage($('#csvImportMessage'), draft.insertCount > 0 ? '预览已生成，正式账本尚未变化。请确认后导入。' : '没有可新增的有效行；请修正映射或数据后重新预览。', draft.insertCount <= 0);
    } catch (error) {
      setMessage($('#csvImportMessage'), errorText(error, '无法生成导入预览。'), true);
    } finally {
      button.disabled = false;
    }
  }

  async function previewCsvReconciliation() {
    const draft = state.importDraft;
    const startDate = $('#csvReconcileStartDate').value;
    const endDate = $('#csvReconcileEndDate').value;
    if (!draft) return setMessage($('#csvImportMessage'), '请先选择 CSV 并读取表头。', true);
    if (!startDate || !endDate || startDate > endDate) return setMessage($('#csvImportMessage'), '请选择有效的闭区间对账日期。', true);
    const columnMapping = currentCsvMapping();
    if (!columnMapping.occurred_on || !columnMapping.kind || !columnMapping.amount) {
      return setMessage($('#csvImportMessage'), '请映射发生日期、收支方向和金额列。', true);
    }
    const dateFormat = $('#csvDateFormat').value;
    const amountUnit = $('#csvAmountUnit').value;
    if (!dateFormat || !amountUnit) return setMessage($('#csvImportMessage'), '请选择日期格式和金额单位。', true);
    const button = $('#csvReconcilePreviewButton');
    button.disabled = true;
    $('#csvReconcileConfirmButton').hidden = true;
    $('#csvReconcileConfirmButton').disabled = true;
    state.reconciliationDraft = null;
    $('#csvReconciliationPreview').replaceChildren();
    $('#csvReconciliationPreview').hidden = true;
    setMessage($('#csvImportMessage'), '正在比较所选日期窗口内的 CSV 和账本流水…');
    try {
      const tool = importAction('preview_transaction_reconciliation');
      const params = {
        file_name: draft.file.name,
        content_base64: draft.contentBase64,
        column_mapping: columnMapping,
        ...($('#csvStatementProvider').value ? {provider: $('#csvStatementProvider').value} : {}),
        date_format: dateFormat,
        amount_unit: amountUnit,
        start_date: startDate,
        end_date: endDate
      };
      if ($('#csvReconcilePaymentAccount').value) params.payment_account_id = $('#csvReconcilePaymentAccount').value;
      const result = await callTool(tool.name, params, tool.scope === 'own_write');
      const data = envelopeData(result.envelope);
      if (!data.preview_id || !data.preview_sha256) throw new Error('服务未返回可确认的对账预览凭据；本页不会提交任何行。');
      state.reconciliationDraft = {previewId: data.preview_id, previewSha256: data.preview_sha256, data};
      renderReconciliationPreview(data);
      const confirm = $('#csvReconcileConfirmButton');
      confirm.hidden = false;
      confirm.disabled = true;
      updateReconciliationSelection();
      setMessage($('#csvImportMessage'), '对账预览已生成；账本尚未变化。请只勾选确认为 CSV 独有的行。');
      applyCsvMode('reconcile');
    } catch (error) {
      setMessage($('#csvImportMessage'), errorText(error, '无法生成对账预览。'), true);
    } finally {
      button.disabled = false;
    }
  }

  async function commitCsvReconciliation() {
    const draft = state.reconciliationDraft;
    const selected = $$('#csvReconciliationPreview [data-csv-line]:checked').map(control => Number(control.dataset.csvLine));
    if (!draft || !selected.length) return;
    const button = $('#csvReconcileConfirmButton');
    button.disabled = true;
    setMessage($('#csvImportMessage'), '正在入账已勾选的 CSV 独有行…');
    try {
      const tool = importAction('commit_transaction_reconciliation');
      const result = await callTool(tool.name, {
        preview_id: draft.previewId,
        preview_sha256: draft.previewSha256,
        selected_csv_lines: selected,
        confirmed: true
      }, true);
      renderFeatureResult('maintenance', tool, result.envelope, result.requestId);
      resetImportWorkflow();
      $('#csvImportFile').value = '';
      setMessage($('#csvImportMessage'), '所选 CSV 独有行已入账。请到流水页复核结果。');
      await loadAppData();
    } catch (error) {
      button.disabled = false;
      setMessage($('#csvImportMessage'), errorText(error, '对账确认未完成；请重新读取文件并生成对账预览。'), true);
    }
  }

  function renderBalanceCheck(check) {
    const target = $('#balanceCheckResult');
    target.replaceChildren();
    if (!check) {
      target.append(node('dt', '', '最近核对'), node('dd', 'empty-cell', '该账户尚无保存的实测余额核对。'));
      target.hidden = false;
      return;
    }
    const difference = Number(check.difference_minor || 0);
    const label = difference === 0 ? '账面与实测一致' : difference > 0 ? '实测高于账面' : '实测低于账面';
    for (const [name, value] of [
      ['核对日期', check.balance_date], ['账面金额', money(check.book_balance_minor)],
      ['实测金额', money(check.actual_balance_minor)], ['差额（实测 − 账面）', money(difference)],
      ['核对状态', label], ['最近核对时间', check.checked_at]
    ]) target.append(node('dt', '', name), node('dd', '', value || '—'));
    target.hidden = false;
  }

  async function loadPaymentAccountOptions() {
    if (isCrossAccount() || !hasTool('list_payment_accounts')) {
      state.paymentAccounts = [];
      $('#paymentBalanceCheckPanel').hidden = true;
      $('#balanceCheckAccount').replaceChildren(node('option', '', '无可用支付账户'));
      $('#csvReconcilePaymentAccount').replaceChildren(node('option', '', '当前账本全部流水'));
      $('#csvReconcilePaymentAccount').options[0].value = '';
      return;
    }
    let result;
    try {
      result = await callTool('list_payment_accounts');
    } catch (error) {
      $('#paymentBalanceCheckPanel').hidden = true;
      setMessage($('#balanceCheckMessage'), errorText(error, '无法读取支付账户。'), true);
      return;
    }
    state.paymentAccounts = envelopeData(result.envelope).accounts || [];
    state.entityOptions.payment_accounts = optionRowsFor('payment_accounts', envelopeData(result.envelope));
    for (const [selector, placeholder] of [
      ['#balanceCheckAccount', '请选择支付账户'], ['#csvReconcilePaymentAccount', '当前账本全部流水']
    ]) {
      const select = $(selector);
      const previous = select.value;
      select.replaceChildren(node('option', '', placeholder));
      select.options[0].value = '';
      for (const account of state.paymentAccounts) {
        const option = node('option', '', account.name + (account.account_type ? ' · ' + (enumLabels.account_type[account.account_type] || account.account_type) : ''));
        option.value = account.account_id;
        select.append(option);
      }
      if (previous && state.paymentAccounts.some(item => String(item.account_id) === String(previous))) select.value = previous;
    }
    $('#balanceCheckDate').value ||= localDate();
    $('#paymentBalanceCheckPanel').hidden = !hasTool('get_payment_account_balance_check') || !hasTool('record_payment_account_balance_check');
    if (!state.paymentAccounts.length) setMessage($('#balanceCheckMessage'), '请先在上方操作中添加支付账户，再保存余额核对。');
    else if (!$('#balanceCheckAccount').value) $('#balanceCheckAccount').value = String(state.paymentAccounts[0].account_id);
    if (state.activeView === 'movements' && $('#balanceCheckAccount').value) await loadPaymentAccountBalanceCheck();
  }

  async function loadPaymentAccountBalanceCheck() {
    const accountId = $('#balanceCheckAccount').value;
    if (!accountId || !hasTool('get_payment_account_balance_check')) return;
    const button = $('#refreshBalanceCheckButton');
    button.disabled = true;
    setMessage($('#balanceCheckMessage'), '正在读取最近核对…');
    try {
      const result = await callTool('get_payment_account_balance_check', {account_id: accountId});
      const check = envelopeData(result.envelope).check;
      renderBalanceCheck(check);
      setMessage($('#balanceCheckMessage'), check ? '已读取该账户最近一次核对。' : '尚无核对记录，可选择日期和实测余额后保存。');
    } catch (error) {
      setMessage($('#balanceCheckMessage'), errorText(error, '无法读取最近余额核对。'), true);
    } finally {
      button.disabled = false;
    }
  }

  async function recordPaymentAccountBalanceCheck(event) {
    event.preventDefault();
    const accountId = $('#balanceCheckAccount').value;
    const balanceDate = $('#balanceCheckDate').value;
    if (!accountId || !balanceDate || !$('#balanceCheckActual').value) return;
    const button = $('#recordBalanceCheckButton');
    button.disabled = true;
    setMessage($('#balanceCheckMessage'), '正在保存日期对应的实测余额快照…');
    try {
      const actualBalance = parseAmount($('#balanceCheckActual').value, true);
      const result = await callTool('record_payment_account_balance_check', {
        account_id: accountId,
        balance_date: balanceDate,
        actual_balance_minor: actualBalance
      }, true);
      const check = envelopeData(result.envelope).check;
      renderBalanceCheck(check);
      $('#balanceCheckActual').value = '';
      setMessage($('#balanceCheckMessage'), '核对已保存。信用卡正数表示欠款，差额为实测减账面。');
    } catch (error) {
      setMessage($('#balanceCheckMessage'), errorText(error, '余额核对未保存。'), true);
    } finally {
      button.disabled = false;
    }
  }

  function backupStatusValue(label, value) {
    return [node('dt', '', label), node('dd', '', value || '—')];
  }

  async function refreshBackupSchedule(knownData = null) {
    const target = $('#backupScheduleStatus');
    const canMaintain = !isCrossAccount() && hasTool('get_backup_schedule');
    $('#backupScheduleForm').hidden = isCrossAccount() || !hasTool('set_backup_schedule');
    $('#refreshBackupScheduleButton').hidden = !canMaintain;
    $('#runBackupDrillButton').hidden = isCrossAccount() || !hasTool('backup_drill');
    $('#backupUploadPanel').hidden = isCrossAccount() || !hasTool('preview_restore');
    $('#uploadBackupZipButton').disabled = isCrossAccount() || !hasTool('preview_restore');
    if (isCrossAccount() || !hasTool('get_backup_schedule')) {
      target.replaceChildren(node('dt', '', '状态'), node('dd', '', '当前登录范围不包含本机备份维护。'));
      setMessage($('#backupScheduleMessage'), '');
      return;
    }
    setMessage($('#backupScheduleMessage'), '正在读取自动备份和恢复演练状态…');
    try {
      const data = knownData && knownData.enabled !== undefined
        ? knownData
        : envelopeData((await callTool('get_backup_schedule')).envelope);
      state.backupSchedule = data;
      $('#backupScheduleEnabled').checked = Boolean(data.enabled);
      $('#backupScheduleInterval').value = data.interval_days || 7;
      $('#backupScheduleRetention').value = data.retention_count || 12;
      target.replaceChildren();
      const entries = [
        ['自动备份', data.enabled ? '已启用' : '已停用'],
        ['下次运行', data.next_run_at],
        ['最近自动成功', data.last_success ? data.last_success.created_at + ' · ' + data.last_success.backup_id : '尚无成功记录'],
        ['最近错误', data.last_error ? data.last_error.at + ' · ' + (errorMessages[data.last_error.code] || '备份未完成') : '无'],
        ['最近恢复演练', data.last_drill ? data.last_drill.created_at + ' · ' + resultScalarText('status', data.last_drill.status) : '尚无演练记录']
      ];
      for (const [label, value] of entries) target.append(...backupStatusValue(label, value));
      setMessage($('#backupScheduleMessage'), '已读取本机维护状态。');
    } catch (error) {
      target.replaceChildren(node('dt', '', '状态'), node('dd', 'error-inline', errorText(error, '无法读取本机备份设置。')));
      setMessage($('#backupScheduleMessage'), errorText(error, '无法读取本机备份设置。'), true);
    }
  }

  async function saveBackupSchedule(event) {
    event.preventDefault();
    const interval = Number($('#backupScheduleInterval').value);
    const retention = Number($('#backupScheduleRetention').value);
    if (!Number.isInteger(interval) || interval < 1 || interval > 365 || !Number.isInteger(retention) || retention < 1 || retention > 90) {
      setMessage($('#backupScheduleMessage'), '间隔须为 1–365 天，保留份数须为 1–90。', true);
      return;
    }
    const button = $('#saveBackupScheduleButton');
    button.disabled = true;
    setMessage($('#backupScheduleMessage'), '正在保存本机备份设置…');
    try {
      const result = await callTool('set_backup_schedule', {
        enabled: $('#backupScheduleEnabled').checked,
        interval_days: interval,
        retention_count: retention
      }, true);
      await refreshBackupSchedule(envelopeData(result.envelope));
    } catch (error) {
      setMessage($('#backupScheduleMessage'), errorText(error, '无法保存本机备份设置。'), true);
    } finally {
      button.disabled = false;
    }
  }

  async function uploadBackupZip() {
    const file = $('#backupZipFile').files[0];
    if (!file) return setMessage($('#backupZipMessage'), '请先选择 ZIP 备份文件。', true);
    if (isCrossAccount() || !hasTool('preview_restore') || !state.session || !state.session.csrf_token) {
      return setMessage($('#backupZipMessage'), '当前登录范围没有恢复此账本的权限。', true);
    }
    if (!/\.zip$/i.test(file.name) || file.size > 256 * 1024 * 1024) {
      return setMessage($('#backupZipMessage'), '请选择 ZIP 文件；文件上限为 256 MiB。', true);
    }
    const button = $('#uploadBackupZipButton');
    button.disabled = true;
    let uploaded = false;
    $('#backupUploadSummary').hidden = true;
    setMessage($('#backupZipMessage'), '正在通过本机登录会话上传 ZIP 并进行校验…');
    try {
      const response = await fetch('/api/backups/upload', {
        method: 'POST',
        credentials: 'same-origin',
        mode: 'same-origin',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/zip',
          'X-CSRF-Token': state.session.csrf_token
        },
        body: file
      });
      let envelope;
      try {
        envelope = await response.json();
      } catch {
        throw new Error('本机服务没有返回有效的 ZIP 校验结果。');
      }
      if (!response.ok || !['ok', 'committed'].includes(envelope.status)) {
        const error = new Error(envelope.error && envelope.error.message || '本机服务未接受此 ZIP 文件。');
        error.status = envelope.status || response.status;
        error.code = envelope.error && envelope.error.code || envelope.code;
        throw error;
      }
      assertAccount(envelope);
      const data = envelopeData(envelope);
      if (!data.backup_id) throw new Error('服务未返回已校验备份编号。');
      uploaded = true;
      const summary = $('#backupUploadSummary');
      summary.replaceChildren(node('strong', '', data.filename || file.name), node('span', '', 'ZIP 已上传并通过基础校验 · v' + (data.format_version || '—') + ' · SHA-256 ' + (data.sha256 || '未提供')));
      summary.hidden = false;
      state.entityOptions.backups = null;
      setMessage($('#backupZipMessage'), 'ZIP 已加入本机备份列表；正在打开原有来源账本映射预览。');
      await openFeatureActionWithValues('preview_restore', {backup_id: data.backup_id}, 'maintenance');
    } catch (error) {
      const fallback = uploaded ? 'ZIP 已上传，但无法打开恢复预览。请从现有备份列表选择此文件继续。' : 'ZIP 未上传；请检查文件并重试。';
      const message = errorText(error, fallback);
      setMessage($('#backupZipMessage'), uploaded ? 'ZIP 已上传。' + message : message, true);
    } finally {
      button.disabled = false;
    }
  }

  async function commitCsvImport() {
    const draft = state.importDraft;
    if (!draft || !draft.previewId || !draft.previewSha256 || draft.insertCount <= 0) return;
    const button = $('#csvImportConfirmButton');
    button.disabled = true;
    setMessage($('#csvImportMessage'), '正在导入已确认的流水…');
    try {
      const tool = importAction('commit_transaction_import');
      const result = await callTool(tool.name, {preview_id: draft.previewId, preview_sha256: draft.previewSha256, confirmed: true}, true);
      renderFeatureResult('maintenance', tool, result.envelope, result.requestId);
      resetImportWorkflow();
      $('#csvImportFile').value = '';
      setMessage($('#csvImportMessage'), '导入已完成。请到流水页复核结果。');
      await loadAppData();
    } catch (error) {
      button.disabled = false;
      setMessage($('#csvImportMessage'), errorText(error, '导入未完成；请重新读取文件并生成预览。'), true);
    }
  }

  function clearAttachmentObjectUrls() {
    for (const url of state.attachmentObjectUrls) URL.revokeObjectURL(url);
    state.attachmentObjectUrls = [];
  }

  function clearAttachmentPreview() {
    clearAttachmentObjectUrls();
    $('#attachmentPreview').replaceChildren();
    $('#attachmentPreview').hidden = true;
  }

  function resetLocalFilePanels() {
    resetImportWorkflow();
    state.csvTemplates = [];
    state.selectedCsvTemplateId = null;
    state.paymentAccounts = [];
    $('#csvTemplateSelect').replaceChildren(node('option', '', '不使用模板'));
    $('#csvTemplateMessage').textContent = '表头签名匹配时才自动套用。表头变化后请重新映射，再明确保存新版本。';
    applyCsvMode('import');
    $('#csvImportFile').value = '';
    $('#csvImportMessage').textContent = '';
    state.attachmentTransactions = [];
    clearAttachmentObjectUrls();
    $('#attachmentTransaction').replaceChildren(node('option', '', '先读取流水'));
    $('#attachmentTransaction').value = '';
    $('#attachmentFile').value = '';
    $('#attachmentList').replaceChildren();
    $('#attachmentList').hidden = true;
    clearAttachmentPreview();
    $('#attachmentMessage').textContent = '';
    $('#backupZipFile').value = '';
    $('#backupUploadSummary').replaceChildren();
    $('#backupUploadSummary').hidden = true;
    $('#backupZipMessage').textContent = '';
    $('#balanceCheckResult').replaceChildren();
    $('#balanceCheckResult').hidden = true;
    clearOneTimeHostToken();
    $('#runtimeLogRows').replaceChildren();
    const empty = node('tr');
    const cell = node('td', 'empty-cell', '点击“读取最新记录”。');
    cell.colSpan = 5;
    empty.append(cell);
    $('#runtimeLogRows').append(empty);
    updateAttachmentButtons();
  }

  function updateAttachmentButtons() {
    const selected = Boolean($('#attachmentTransaction').value) && !isCrossAccount();
    const fileSelected = Boolean($('#attachmentFile').files && $('#attachmentFile').files.length);
    $('#attachFileButton').disabled = !selected || !fileSelected || !hasTool('attach_transaction_file');
    $('#listTransactionFilesButton').disabled = !selected || !hasTool('list_transaction_files') || !hasTool('get_transaction_attachment');
  }

  async function loadAttachmentTransactions() {
    const button = $('#loadAttachmentTransactions');
    const startDate = $('#attachmentStartDate').value;
    const endDate = $('#attachmentEndDate').value;
    if (!startDate || !endDate || startDate > endDate) {
      setMessage($('#attachmentMessage'), '请选择有效的流水日期范围。', true);
      return;
    }
    button.disabled = true;
    setMessage($('#attachmentMessage'), '正在读取当前账本的流水…');
    try {
      const result = await callTool('search_transactions', {filters: {start_date: startDate, end_date: endDate}, date_basis: 'occurred_on', page: {limit: 100}});
      const data = envelopeData(result.envelope);
      state.attachmentTransactions = Array.isArray(data.transactions) ? data.transactions : [];
      const select = $('#attachmentTransaction');
      select.replaceChildren();
      const placeholder = node('option', '', state.attachmentTransactions.length ? '请选择一笔流水' : '此范围没有可用流水');
      placeholder.value = '';
      select.append(placeholder);
      for (const item of state.attachmentTransactions) {
        const option = node('option', '', [item.occurred_on, item.merchant || categoryName(item.category_id) || '未分类', money(item.amount_minor)].join(' · '));
        option.value = item.transaction_id;
        select.append(option);
      }
      updateAttachmentButtons();
      $('#attachmentTransactionStatus').textContent = '读取到 ' + count(state.attachmentTransactions.length) + ' 笔流水。选择一笔后可添加或查看附件。';
      setMessage($('#attachmentMessage'), '');
    } catch (error) {
      setMessage($('#attachmentMessage'), errorText(error, '无法读取流水。'), true);
    } finally {
      button.disabled = false;
    }
  }

  async function selectedTransactionVersion(transactionId) {
    const result = await callTool('get_transaction', {transaction_id: transactionId});
    const data = envelopeData(result.envelope);
    const transaction = data.transaction || data;
    const version = transaction.version ?? data.version;
    if (version === undefined || version === null || !Number.isInteger(Number(version))) throw new Error('服务没有返回这笔流水的当前版本号。请重新读取流水后重试。');
    return Number(version);
  }

  function renderTransactionAttachments(rows) {
    const target = $('#attachmentList');
    clearAttachmentPreview();
    target.replaceChildren();
    target.append(node('h3', '', '已保存附件'));
    if (!rows.length) target.append(node('p', 'feature-result-empty', '这笔流水没有附件。'));
    for (const item of rows) {
      const row = node('article', 'attachment-row');
      const copy = node('div', 'attachment-copy');
      copy.append(node('strong', '', item.file_name || item.filename || '流水附件'));
      copy.append(node('small', '', [item.mime_type || '文件', item.created_at || ''].filter(Boolean).join(' · ')));
      const actions = node('div', 'attachment-actions');
      const open = node('button', 'button button-secondary', '预览 / 下载');
      open.type = 'button';
      open.addEventListener('click', () => downloadTransactionAttachment(item));
      actions.append(open);
      const version = item.version ?? item.expected_version;
      if (hasTool('remove_transaction_file') && item.attachment_id && version !== undefined && version !== null) {
        const remove = node('button', 'button button-quiet', '移除');
        remove.type = 'button';
        remove.addEventListener('click', () => {
          if (remove.dataset.confirming !== 'true') {
            remove.dataset.confirming = 'true';
            remove.textContent = '确认移除';
            return;
          }
          remove.disabled = true;
          removeTransactionAttachment({...item, version}).catch(error => {
            remove.disabled = false;
            remove.dataset.confirming = 'false';
            remove.textContent = '移除';
            setMessage($('#attachmentMessage'), errorText(error, '无法移除该附件。'), true);
          });
        });
        actions.append(remove);
      }
      row.append(copy, actions);
      target.append(row);
    }
    target.hidden = false;
  }

  async function listTransactionAttachments() {
    const transactionId = $('#attachmentTransaction').value;
    if (!transactionId) {
      setMessage($('#attachmentMessage'), '请先选择一笔流水。', true);
      return;
    }
    setMessage($('#attachmentMessage'), '正在读取附件列表…');
    try {
      const result = await callTool('list_transaction_files', {transaction_id: transactionId});
      const data = envelopeData(result.envelope);
      renderTransactionAttachments(data.attachments || data.files || []);
      setMessage($('#attachmentMessage'), '附件列表已更新。');
    } catch (error) {
      setMessage($('#attachmentMessage'), errorText(error, '无法读取附件列表。'), true);
    }
  }

  async function downloadTransactionAttachment(item) {
    setMessage($('#attachmentMessage'), '正在读取附件内容…');
    try {
      const result = await callTool('get_transaction_attachment', {attachment_id: item.attachment_id});
      const data = envelopeData(result.envelope);
      const attachment = data.attachment || data;
      if (!attachment.content_base64) throw new Error('服务没有返回附件内容。');
      const binary = atob(attachment.content_base64);
      const bytes = Uint8Array.from(binary, character => character.charCodeAt(0));
      clearAttachmentPreview();
      const mimeType = attachment.mime_type || item.mime_type || 'application/octet-stream';
      const fileName = attachment.file_name || item.file_name || '流水附件';
      const url = URL.createObjectURL(new Blob([bytes], {type: mimeType}));
      state.attachmentObjectUrls.push(url);
      const preview = $('#attachmentPreview');
      preview.append(node('h3', '', fileName));
      if (mimeType.startsWith('image/')) {
        const figure = node('figure', 'attachment-image-preview');
        const image = document.createElement('img');
        image.src = url;
        image.alt = fileName;
        figure.append(image, node('figcaption', '', '附件的本机预览'));
        preview.append(figure);
      } else if (mimeType === 'application/pdf') {
        const documentPreview = document.createElement('object');
        documentPreview.className = 'attachment-pdf-preview';
        documentPreview.data = url;
        documentPreview.type = mimeType;
        documentPreview.setAttribute('aria-label', fileName + ' 本机预览');
        documentPreview.append(node('p', '', '浏览器无法内嵌预览此 PDF；可使用下方下载链接。'));
        preview.append(documentPreview);
      }
      const link = node('a', 'button button-secondary attachment-download-link', '下载 ' + fileName);
      link.href = url;
      link.download = fileName;
      preview.append(link);
      preview.hidden = false;
      setMessage($('#attachmentMessage'), '附件已准备好，可在本机预览或下载。');
    } catch (error) {
      setMessage($('#attachmentMessage'), errorText(error, '无法读取附件内容。'), true);
    }
  }

  async function removeTransactionAttachment(item) {
    const result = await callTool('remove_transaction_file', {attachment_id: item.attachment_id, expected_version: item.version ?? item.expected_version}, true);
    renderFeatureResult('maintenance', state.toolCatalog.find(tool => tool.name === 'remove_transaction_file'), result.envelope, result.requestId);
    await listTransactionAttachments();
  }

  async function attachSelectedFile() {
    const transactionId = $('#attachmentTransaction').value;
    const file = $('#attachmentFile').files[0];
    if (!transactionId || !file) {
      setMessage($('#attachmentMessage'), '请先选择流水和本机附件。', true);
      return;
    }
    const extension = file.name.split('.').pop().toLowerCase();
    const mimeType = file.type || ({pdf: 'application/pdf', jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', webp: 'image/webp'}[extension] || '');
    if (file.size > 10 * 1024 * 1024 || !['application/pdf', 'image/jpeg', 'image/png', 'image/webp'].includes(mimeType)) {
      setMessage($('#attachmentMessage'), '请选择 10 MB 以内的 PDF、JPEG、PNG 或 WebP 文件。', true);
      return;
    }
    const button = $('#attachFileButton');
    button.disabled = true;
    setMessage($('#attachmentMessage'), '正在核对流水版本并保存附件…');
    try {
      const version = await selectedTransactionVersion(transactionId);
      const contentBase64 = await readFileBase64(file);
      const tool = state.toolCatalog.find(item => item.name === 'attach_transaction_file');
      const result = await callTool(tool.name, {
        transaction_id: transactionId,
        expected_version: version,
        file_name: file.name,
        mime_type: mimeType,
        content_base64: contentBase64
      }, true);
      renderFeatureResult('maintenance', tool, result.envelope, result.requestId);
      $('#attachmentFile').value = '';
      updateAttachmentButtons();
      await listTransactionAttachments();
      setMessage($('#attachmentMessage'), '附件已保存到当前账本。');
    } catch (error) {
      setMessage($('#attachmentMessage'), errorText(error, '附件未保存。'), true);
    } finally {
      button.disabled = false;
    }
  }

  async function callTool(name, params = {}, write = false) {
    if (!hasTool(name)) throw new Error('当前账号没有可用的“' + featureTitle({name}) + '”操作。');
    const crossAccount = isCrossAccount();
    const descriptor = state.toolCatalog.find(tool => tool.name === name);
    if (crossAccount && (write || !crossToolAllowed(descriptor))) {
      throw new Error('当前授权只允许查看该账本的支出汇总或储蓄目标进度。');
    }
    if (crossAccount && params.filters && Object.keys(params.filters).length &&
        (Object.keys(params.filters).some(key => key !== 'category_id') || !crossCategoryFilterActions.has(name))) {
      throw new Error('授权查看只支持允许的分类汇总筛选，不提供项目或流水筛选。');
    }
    const usesReceipt = write && !noReceiptActions.has(name) && descriptor.no_receipt !== true;
    const signature = usesReceipt ? JSON.stringify([state.session && state.session.account_id, state.targetAccountId, name, params]) : null;
    const requestId = usesReceipt ? state.pendingRequests.get(signature) || crypto.randomUUID() : null;
    if (usesReceipt) state.pendingRequests.set(signature, requestId);
    const body = usesReceipt ? Object.assign({}, params, {request_id: requestId}) : Object.assign({}, params);
    if (crossAccount) body.account_id = state.targetAccountId;
    const envelope = await apiRequest('/api/tools/' + encodeURIComponent(name), {
      method: 'POST',
      body,
      csrf: state.session && state.session.csrf_token
    });
    assertAccount(envelope, state.targetAccountId, name === 'resolve_local_review_link');
    if (usesReceipt) state.pendingRequests.delete(signature);
    if (usesReceipt && ['add_transactions', 'update_transaction', 'void_transaction', 'commit_bookkeeping_draft', 'transfer', 'record_refund', 'record_credit_card_repayment', 'record_reimbursement_receipt'].includes(name)) state.lastUndoableRequestId = envelope.request_id || requestId;
    return {envelope, requestId};
  }

  function money(minor) {
    if (minor === null || minor === undefined || !Number.isFinite(Number(minor))) return '-';
    const value = Math.trunc(Number(minor));
    const sign = value < 0 ? '−' : '';
    const absolute = Math.abs(value);
    const yuan = Math.floor(absolute / 100);
    const cents = String(absolute % 100).padStart(2, '0'); // taste-skill-ignore: CNY prices use two decimal digits.
    return sign + '¥' + new Intl.NumberFormat('zh-CN').format(yuan) + '.' + cents;
  }

  function signedMoney(minor, kind) {
    const value = Math.abs(Number(minor || 0));
    return (kind === 'income' ? '+' : '−') + money(value);
  }

  function count(value) {
    return new Intl.NumberFormat('zh-CN').format(Number(value || 0));
  }

  function percent(value) {
    return new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 1, minimumFractionDigits: 1}).format(value) + '%';
  }

  function percentagePoints(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '无对比基数';
    const number = Number(value);
    return (number > 0 ? '+' : '') + new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 2, minimumFractionDigits: 2}).format(number) + '%';
  }

  function node(tag, className, value) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (value !== undefined && value !== null) element.textContent = String(value);
    return element;
  }

  function setPage(view) {
    let [title, subtitle] = pageCopy[view];
    const goalOnly = isCrossAccount() && targetHasScope('admin_goal');
    const spendingView = isCrossAccount() && targetHasScope('admin_spending');
    if (view === 'overview' && state.period && !showsCurrentMonth(state.period)) title = '期间总览';
    if (view === 'overview' && spendingView) subtitle = '查看已授权的收支与分类汇总';
    if (view === 'planning' && goalOnly) [title, subtitle] = ['储蓄目标进度', '此账本仅授权查看储蓄目标进度'];
    if (view === 'transactions' && spendingView) [title, subtitle] = ['支出汇总', '查看期间和分类支出汇总'];
    state.activeView = view;
    for (const section of $$('.view-panel')) section.hidden = section.id !== view + 'View';
    for (const button of $$('[data-view]')) {
      const active = button.dataset.view === view;
      button.classList.toggle('is-active', active);
      if (active) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    }
    $('#pageTitle').textContent = title;
    $('#pageSubtitle').textContent = subtitle;
    $('#planningPanelHeading').textContent = goalOnly ? '储蓄目标进度' : '项目、预算与目标';
    $('#planningPanelSubtitle').textContent = goalOnly
      ? '此账本仅授权查看目标名称、金额与进度；项目预算和流水明细不在此范围内。'
      : '为项目、周期预算和储蓄目标建立可查询的计划。';
    $('#flowWorkspaceHeading').textContent = spendingView ? '支出与期间分析' : '核账与流水操作';
    $('#flowWorkspaceSubtitle').textContent = spendingView
      ? '查看获授权的期间和分类汇总；流水、商家与项目明细不在此范围内。'
      : '使用中文表单处理当前账号可用的核对操作。';
    document.title = '余序 · ' + title;
    if (location.hash !== '#' + view) history.replaceState(null, '', '#' + view);
  }

  function changeView(view, closeNavigation = false) {
    if (!Object.hasOwn(pageCopy, view)) return;
    const crossAllowed = !isCrossAccount() || view === 'overview' ||
      (['transactions', 'reports', 'visualization'].includes(view) && targetHasScope('admin_spending')) ||
      (view === 'planning' && targetHasScope('admin_goal')) || groupHasReadTools(view);
    if (!crossAllowed) {
      const fallback = targetHasScope('admin_spending') ? 'overview' : targetHasScope('admin_goal') ? 'planning' : 'overview';
      setMessage($('#overviewMessage'), '当前授权范围不包含此类账本操作。');
      history.replaceState(null, '', '#' + fallback);
      setPage(fallback);
      return;
    }
    if (view !== 'security') clearOneTimeHostToken();
    setPage(view);
    if (closeNavigation && $('#navigationDialog').open) $('#navigationDialog').close();
    $('#mainContent').focus({preventScroll: true});
    window.scrollTo(0, 0);
    if (view === 'visualization' && state.period) {
      if (!state.quickReportMode) loadQuickReport(defaultQuickReportMode());
      if ($('#annualCalendarPanel').open && !$('#annualCalendarPanel').hidden && !state.annualCalendar) loadAnnualSpendingCalendar();
    }
    if (view === 'transactions' && !isCrossAccount()) searchTransactions();
    if (view === 'movements' && !isCrossAccount()) loadPaymentAccountOptions();
    if (view === 'maintenance' && !isCrossAccount()) refreshBackupSchedule();
  }

  function currentPeriod() {
    return {
      start_date: $('#periodStart').value,
      end_date: $('#periodEnd').value,
      date_basis: $('#periodDateBasis').value
    };
  }

  function validatePeriod(period) {
    return Boolean(period.start_date && period.end_date && period.start_date <= period.end_date);
  }

  function periodLabel(period) {
    return period.start_date + ' 至 ' + period.end_date + (period.date_basis === 'charged_on' ? ' · 按扣款日期' : ' · 按消费日期');
  }

  function renderAccessibleLedgerList() {
    const list = $('#accessibleLedgers');
    if (!list) return;
    list.replaceChildren();
    for (const account of state.accounts) {
      const own = String(account.account_id) === String(state.session && state.session.account_id);
      const scopes = Array.from(accountScopes(account)).map(scopeLabel);
      const item = node('li', 'access-ledger-row');
      const copy = node('div');
      copy.append(node('strong', '', account.account_name || '已授权账本'));
      copy.append(node('p', '', scopes.join('、') || '已登录账本'));
      item.append(copy, node('span', 'access-ledger-kind', own ? '本人账本' : '可进入 · 只读'));
      list.append(item);
    }
    if (!state.accounts.length) list.append(node('li', 'feature-result-empty', '当前没有可列出的账本范围。'));
  }

  function fillAccountName(session) {
    const name = (state.targetAccount && state.targetAccount.account_name) || session.account_name || '当前账本';
    $('#accountName').textContent = name;
    $('#signedInName').textContent = session.account_name || '本机用户';
    $('#mobileAccountName').textContent = '当前账本 · ' + name;
  }

  function renderAccountPickers(session) {
    const grouped = new Map();
    for (const row of session.accounts || []) {
      if (row.account_id === undefined || row.account_id === null) continue;
      const id = String(row.account_id);
      const account = grouped.get(id) || {account_id: row.account_id, account_name: row.account_name || '已授权账本', scopes: []};
      if (row.scope && !account.scopes.includes(row.scope)) account.scopes.push(row.scope);
      if (row.account_name) account.account_name = row.account_name;
      grouped.set(id, account);
    }
    const own = grouped.get(String(session.account_id)) || {
      account_id: session.account_id, account_name: session.account_name || '当前账本', scopes: ['own_read']
    };
    const unique = [own].concat(Array.from(grouped.values()).filter(account =>
      String(account.account_id) !== String(own.account_id) &&
      (accountScopes(account).has('admin_spending') || accountScopes(account).has('admin_goal'))
    ));
    state.accounts = unique;
    state.targetAccount = own;
    state.targetAccountId = own.account_id;
    renderAccessibleLedgerList();
    for (const [select, wrap] of [
      [$('#accountPicker'), $('#accountPickerWrap')],
      [$('#mobileAccountPicker'), $('#mobileAccountPickerWrap')]
    ]) {
      select.replaceChildren();
      for (const account of unique) {
        const isOwn = String(account.account_id) === String(own.account_id);
        const scopes = accountScopes(account);
        const access = [scopes.has('admin_spending') && '支出汇总', scopes.has('admin_goal') && '储蓄目标'].filter(Boolean).join('、');
        const option = node('option', '', account.account_name + (isOwn ? ' · 本人' : ' · ' + access + '只读'));
        option.value = account.account_id;
        select.append(option);
      }
      select.value = String(own.account_id);
      wrap.hidden = unique.length < 2;
    }
    fillAccountName(session);
    updateAccountViews();
  }

  function updateAccountViews() {
    localBookkeeping.update();
    const cross = isCrossAccount();
    const spending = cross && targetHasScope('admin_spending');
    for (const button of $$('[data-view="transactions"]')) {
      button.hidden = cross && !spending;
      button.querySelector('span').textContent = spending ? '支出汇总' : '流水与核账';
    }
    const canVisualize = (!cross || spending) && hasTool('get_category_breakdown');
    for (const button of $$('[data-view="visualization"], [data-open-view="visualization"]')) button.hidden = !canVisualize;
    $('#periodForm').hidden = cross && !spending;
    $('#overviewGrid').hidden = cross && !spending;
    $('#overviewBudgetPanel').hidden = cross;
    const annualCalendarTool = state.toolCatalog.find(tool => tool.name === 'get_annual_spending_calendar');
    $('#annualCalendarPanel').hidden = !annualCalendarTool || !crossToolAllowed(annualCalendarTool);
    $('#savedInsightViewsPanel').hidden = cross || !hasTool('list_insight_views');
    $('#reportCategoryFilterLabel').hidden = cross;
    renderCategorySelection();
    const goalOnly = cross && targetHasScope('admin_goal');
    for (const button of $$('[data-view="planning"]')) {
      button.querySelector('span').textContent = goalOnly ? '储蓄目标进度' : '项目预算与目标';
    }
    const canImport = !cross && hasTool('preview_transaction_import') && hasTool('commit_transaction_import');
    const canListAttachments = !cross && hasTool('search_transactions') && hasTool('list_transaction_files') && hasTool('get_transaction_attachment');
    const canAttachFiles = !cross && hasTool('search_transactions') && hasTool('get_transaction') && hasTool('attach_transaction_file');
    for (const group of Object.values(featureGroups)) {
      const visible = !cross || groupHasReadTools(group.view);
      for (const button of $$('[data-view="' + group.view + '"]')) {
        const hasActions = group.view === 'maintenance'
          ? groupHasTools(group.view) || canImport || canListAttachments || canAttachFiles
          : groupHasTools(group.view);
        if (group.view !== 'transactions') button.hidden = !visible || !hasActions;
      }
    }
    $('#csvImportWorkspace').hidden = !canImport;
    $('#attachmentWorkspace').hidden = !canListAttachments && !canAttachFiles;
    $('#attachmentFileLabel').hidden = !canAttachFiles;
    $('#attachFileButton').hidden = !canAttachFiles;
    $('#listTransactionFilesButton').hidden = !canListAttachments;
    $('#loadAttachmentTransactions').hidden = !canAttachFiles && !canListAttachments;
    $('#addButton').hidden = cross || !hasTool('add_transactions');
    $('#mobileAddButton').hidden = cross || !hasTool('add_transactions');
    $('#listAddButton').hidden = cross || !hasTool('add_transactions');
    $('#recentPanel').hidden = cross;
    $('#transactionListPanel').hidden = cross;
    $('#filterForm').hidden = cross;
    updateQuickReportControls();
    if (cross && ['transactions', 'reports', 'visualization', 'maintenance', 'security'].includes(state.activeView) && !spending) {
      setPage(targetHasScope('admin_goal') ? 'planning' : 'overview');
    }
  }

  async function switchAccount(accountId) {
    const target = state.accounts.find(account => String(account.account_id) === String(accountId));
    if (!target) return;
    state.periodVersion++;
    state.period = null;
    state.selectedPeriodPreset = null;
    state.selectedCategoryId = null;
    state.insightViews = [];
    state.selectedCalendarDate = null;
    state.annualCalendar = null;
    state.calendarVersion++;
    $('#annualCalendarPanel').open = false;
    state.targetAccountId = target.account_id;
    state.targetAccount = target;
    clearQuickReport();
    state.summary = null;
    state.breakdown = null;
    state.recent = [];
    state.currentPageTransactions = [];
    state.nextCursor = null;
    state.listFilters = {};
    state.entityOptions = {};
    resetLocalFilePanels();
    if ($('#featureDialog').open) $('#featureDialog').close();
    state.activeFeatureAction = null;
    state.pendingPreview = null;
    state.activeFeatureParams = null;
    for (const result of $$('.feature-result')) {
      result.replaceChildren();
      result.hidden = true;
    }
    $('#overviewSummary').replaceChildren(node('p', 'loading-block', '正在读取当前期间…'));
    $('#reportMetrics').replaceChildren(node('p', 'loading-block', '正在读取所选期间…'));
    $('#overviewCategoryRows').replaceChildren();
    $('#categoryRows').replaceChildren();
    $('#categoryComposition').replaceChildren();
    $('#categoryComposition').hidden = true;
    $('#reportCategoryRows').replaceChildren();
    $('#reportCategoryFoot').replaceChildren();
    $('#recentRows').replaceChildren();
    $('#budgetOverviewRows').replaceChildren();
    $('#overviewBudgetPanel').hidden = true;
    $('#overviewBudgetPanel tfoot')?.remove();
    const range = currentMonthRange();
    $('#periodStart').value = range.start_date;
    $('#periodEnd').value = range.end_date;
    $('#periodDateBasis').value = 'occurred_on';
    $('#filterForm [name="start_date"]').value = range.start_date;
    $('#filterForm [name="end_date"]').value = range.end_date;
    $('#filterDateBasis').value = 'occurred_on';
    $('#filterForm [name="category_id"]').value = '';
    $('#filterForm [name="kind"]').value = '';
    $('#filterForm [name="keyword"]').value = '';
    $('#filterForm [name="tag"]').value = '';
    $('#filterForm [name="amount_min"]').value = '';
    $('#filterForm [name="amount_max"]').value = '';
    $('#accountPicker').value = String(accountId);
    $('#mobileAccountPicker').value = String(accountId);
    fillAccountName(state.session);
    renderFeatureGroups();
    updateAccountViews();
    changeView(isCrossAccount() && !targetHasScope('admin_spending') && targetHasScope('admin_goal') ? 'planning' : 'overview');
    $('#overviewSummary').replaceChildren(node('p', 'loading-block', '正在切换账本…'));
    $('#categoryRows').replaceChildren();
    $('#recentRows').replaceChildren();
    await loadAppData();
  }

  function applyToolCatalog(envelope) {
    assertAccount(envelope, state.session && state.session.account_id);
    const list = Array.isArray(envelopeData(envelope).tools) ? envelopeData(envelope).tools : [];
    state.toolCatalog = list;
    state.tools = new Set(list.map(item => item.name));
    state.toolScopes = new Map(list.map(item => [item.name, item.scope]));
    renderFeatureGroups();
    updateAccountViews();
  }

  async function loadCategories() {
    if (isCrossAccount()) {
      state.categories = [];
      state.entityOptions.categories = [];
      populateCategoryFilters();
      return;
    }
    if (!hasTool('list_categories')) {
      state.categories = [];
      state.entityOptions.categories = [];
      populateCategoryFilters();
      return;
    }
    const result = await callTool('list_categories', {include_inactive: true});
    const categories = envelopeData(result.envelope).categories || [];
    state.categories = categories;
    state.entityOptions.categories = categories.map(item => ({id: String(item.category_id), label: item.path || item.name || String(item.category_id)}));
    populateCategoryFilters();
  }

  function categoryName(categoryId) {
    const category = state.categories.find(item => String(item.category_id) === String(categoryId));
    return category ? (category.path || category.name) : '';
  }

  function populateCategoryFilters() {
    const selects = [
      $('#filterCategory'),
      $('#reportCategoryFilter'),
      $('#editForm select[name="category_id"]')
    ];
    for (const select of selects) {
      const previous = select.value;
      select.replaceChildren();
      const isFilter = select === $('#filterCategory') || select === $('#reportCategoryFilter');
      const empty = node('option', '', isFilter ? '全部分类' : '不更改分类');
      empty.value = '';
      select.append(empty);
      for (const category of state.categories) {
        if (!category.active && isFilter) continue;
        const option = node('option', '', category.path || category.name);
        option.value = category.category_id;
        select.append(option);
      }
      if (select === $('#reportCategoryFilter')) select.value = state.selectedCategoryId || previous;
      else if (previous) select.value = previous;
    }
    for (const row of $$('.entry-row')) populateEntryCategory(row);
  }

  function populateEntryCategory(row) {
    const select = $('[data-field="category_id"]', row);
    if (!select) return;
    const previous = select.value;
    select.replaceChildren();
    const empty = node('option', '', '未分类');
    empty.value = '';
    select.append(empty);
    for (const category of state.categories) {
      if (!category.active) continue;
      const option = node('option', '', category.path || category.name);
      option.value = category.category_id;
      select.append(option);
    }
    if (previous) select.value = previous;
  }

  function captureReviewRefFromHash() {
    const workLink = location.hash.match(/^#(reconciliation|export-preview|export)=([a-f0-9-]{36})$/i);
    if (workLink) {
      state.pendingLocalWork = {type: workLink[1], id: workLink[2]};
      history.replaceState(null, '', location.pathname + location.search);
      $('#reviewLinkLoginNote').hidden = Boolean(state.session);
      return true;
    }
    if (!location.hash.startsWith('#review_ref=')) return false;
    const refs = new URLSearchParams(location.hash.slice(1)).getAll('review_ref');
    history.replaceState(null, '', location.pathname + location.search);
    const valid = refs.length === 1 && Boolean(refs[0]);
    $('#reviewLinkLoginNote').hidden = !valid || Boolean(state.session);
    if (valid) {
      state.pendingReviewRef = refs[0];
      state.reviewLinkNotice = null;
    } else {
      state.pendingReviewRef = null;
      state.reviewLinkNotice = '本机查看链接格式无效，已清除链接片段。';
      if (state.session) {
        $('#reviewLinkPanel').hidden = false;
        setMessage($('#reviewLinkMessage'), state.reviewLinkNotice, true);
      } else {
        setMessage($('#loginMessage'), state.reviewLinkNotice, true);
      }
    }
    return true;
  }

  function renderReviewTransaction(transaction) {
    const detail = $('#reviewLinkDetails');
    detail.replaceChildren();
    const fields = [
      ['商家或项目', transactionDescription(transaction)],
      ['分类', transaction.category_path || categoryName(transaction.category_id) || '未分类'],
      ['消费日期', transaction.occurred_on || '-'],
      ['状态', transactionState(transaction)[0]],
      ['金额', signedMoney(transaction.amount_minor, transaction.kind)]
    ];
    if (transaction.note) fields.push(['备注', transaction.note]);
    const list = node('dl', 'review-link-transaction');
    for (const [label, value] of fields) list.append(node('dt', '', label), node('dd', '', value));
    detail.append(list);
    $('#reviewLinkSummary').textContent = '已在本人账本中打开流水详情。';
  }

  async function resolveLocalWorkLink(work) {
    const panel = $('#reviewLinkPanel');
    const details = $('#reviewLinkDetails');
    const summary = $('#reviewLinkSummary');
    const message = $('#reviewLinkMessage');
    panel.hidden = false;
    $('#reviewLinkLoginNote').hidden = true;
    details.replaceChildren();
    setMessage(message, '');
    summary.textContent = '正在核验本机任务…';
    try {
      if (isCrossAccount()) await switchAccount(state.session.account_id);
      changeView('maintenance');
      if (work.type === 'reconciliation') {
        const item = envelopeData((await callTool('get_transaction_reconciliation', {preview_id: work.id})).envelope);
        if (item.status === 'committed') {
          summary.textContent = '对账已确认';
          setMessage(message, '已入账 ' + count(item.imported_count || 0) + ' 笔。');
          return;
        }
        state.reconciliationDraft = {previewId: item.preview_id, previewSha256: item.preview_sha256, data: item};
        $('#csvImportWorkspace').hidden = false;
        applyCsvMode('reconcile');
        renderReconciliationPreview(item);
        updateReconciliationSelection();
        summary.textContent = '对账预览已打开';
        return;
      }
      if (work.type === 'export-preview') {
        const item = envelopeData((await callTool('get_ledger_export_preview', {preview_id: work.id})).envelope);
        summary.textContent = '确认本机流水导出';
        const scope = node('p', 'panel-subtitle', (item.start_date || '最早') + ' 至 ' + (item.end_date || '最新') +
          ' · ' + count(item.record_count) + ' 行 · ' + item.format.toUpperCase());
        const selected = node('p', 'panel-subtitle', '字段 ' + item.fields.map(key => fieldLabel(key)).join('、'));
        const confirm = node('button', 'button button-primary', '确认导出文件');
        confirm.type = 'button';
        confirm.addEventListener('click', async () => {
          confirm.disabled = true;
          try {
            const response = await callTool('confirm_ledger_export', {preview_id: item.preview_id}, true);
            showDownload(envelopeData(response.envelope));
          } catch (error) {
            setMessage(message, errorText(error, '导出失败。'), true);
            confirm.disabled = false;
          }
        });
        details.append(scope, selected, confirm);
        return;
      }
      showDownload(envelopeData((await callTool('get_export_artifact', {file_id: work.id})).envelope));
    } catch (error) {
      summary.textContent = '本机任务暂不可用';
      setMessage(message, errorText(error, '请核对本人账本或重新生成任务。'), true);
    }

    function showDownload(item) {
      summary.textContent = '本机导出已就绪';
      const download = node('a', 'button button-primary', '下载 ' + item.filename);
      download.href = item.download_url;
      download.download = item.filename;
      details.replaceChildren(download);
    }
  }

  async function resolvePendingReviewLink() {
    if (state.pendingLocalWork && state.session) {
      const work = state.pendingLocalWork;
      state.pendingLocalWork = null;
      await resolveLocalWorkLink(work);
      return;
    }
    if (!state.pendingReviewRef || !state.session) return;
    const reviewRef = state.pendingReviewRef;
    state.pendingReviewRef = null;
    const panel = $('#reviewLinkPanel');
    panel.hidden = false;
    $('#reviewLinkDetails').replaceChildren();
    $('#reviewLinkSummary').textContent = '正在核验本机查看链接…';
    setMessage($('#reviewLinkMessage'), '');
    $('#reviewLinkLoginNote').hidden = true;
    try {
      if (isCrossAccount()) await switchAccount(state.session.account_id);
      if (!hasTool('resolve_local_review_link')) throw new Error('当前 Web 会话没有可用的本机查看链接解析操作。');
      const result = await callTool('resolve_local_review_link', {review_ref: reviewRef});
      const target = envelopeData(result.envelope);
      const account = state.accounts.find(item => String(item.account_id) === String(target.account_id));
      if (!account) throw new Error('目标账本不在当前会话已授权范围内。');
      if (String(target.account_id) !== String(state.targetAccountId)) await switchAccount(target.account_id);
      const dateBasis = target.date_basis || 'occurred_on';
      if (!['occurred_on', 'charged_on'].includes(dateBasis)) throw new Error('链接日期口径无效。');
      if (target.target_kind === 'transaction') {
        if (String(target.account_id) !== String(state.session.account_id)) throw new Error('流水详情链接只支持本人账本。');
        if (!hasTool('get_transaction')) throw new Error('当前账号没有查看流水明细的权限。');
        const transactionResult = await callTool('get_transaction', {transaction_id: target.transaction_id});
        changeView('transactions');
        renderReviewTransaction(envelopeData(transactionResult.envelope).transaction);
      } else {
        if (target.target_kind === 'period' && (!target.start_date || !target.end_date)) throw new Error('链接没有完整的期间日期。');
        if (target.start_date && target.end_date) {
          const period = {start_date: target.start_date, end_date: target.end_date};
          if (!validatePeriod(period)) throw new Error('链接期间日期无效。');
          $('#periodStart').value = period.start_date;
          $('#periodEnd').value = period.end_date;
          $('#filterForm [name="start_date"]').value = period.start_date;
          $('#filterForm [name="end_date"]').value = period.end_date;
          state.selectedPeriodPreset = null;
        }
        if (target.target_kind === 'period' || target.target_kind === 'category') {
          $('#periodDateBasis').value = dateBasis;
          $('#filterDateBasis').value = dateBasis;
        }
        if (target.target_kind === 'category' && target.category_id) state.selectedCategoryId = String(target.category_id);
        else if (target.target_kind === 'period' || target.target_kind === 'ledger') state.selectedCategoryId = null;
        renderCategorySelection();
        changeView(target.target_kind === 'period' ? 'reports' : 'overview');
        if (target.start_date || target.target_kind === 'category') await loadPeriodData();
      }
      state.reviewLinkNotice = null;
      $('#reviewLinkSummary').textContent = target.target_kind === 'transaction'
        ? $('#reviewLinkSummary').textContent
        : target.target_kind === 'period'
          ? target.start_date + ' 至 ' + target.end_date + ' · 按' + (dateBasis === 'charged_on' ? '扣款日期' : '消费日期')
          : target.target_kind === 'category'
            ? (categoryName(target.category_id) || '指定分类') + ' · ' + (target.start_date && target.end_date ? target.start_date + ' 至 ' + target.end_date : periodLabel(state.period)) + ' · 按' + (dateBasis === 'charged_on' ? '扣款日期' : '消费日期')
            : '已切换到获授权的账本。';
      setMessage($('#reviewLinkMessage'), '');
    } catch (error) {
      state.reviewLinkNotice = reviewLinkErrorText(error);
      $('#reviewLinkSummary').textContent = '没有打开目标内容。';
      $('#reviewLinkDetails').replaceChildren();
      setMessage($('#reviewLinkMessage'), state.reviewLinkNotice, true);
    }
  }

  function showApp(session) {
    state.session = session;
    state.pendingRequests.clear();
    state.categories = [];
    state.summary = null;
    state.breakdown = null;
    state.recent = [];
    state.currentPageTransactions = [];
    state.nextCursor = null;
    state.entityOptions = {};
    resetLocalFilePanels();
    for (const result of $$('.feature-result')) {
      result.replaceChildren();
      result.hidden = true;
    }
    $('#overviewSummary').replaceChildren(node('p', 'loading-block', '正在读取当前期间…'));
    $('#reportMetrics').replaceChildren(node('p', 'loading-block', '正在读取所选期间…'));
    $('#overviewCategoryRows').replaceChildren();
    $('#categoryRows').replaceChildren();
    $('#reportCategoryRows').replaceChildren();
    $('#recentRows').replaceChildren();
    $('#budgetOverviewRows').replaceChildren();
    $('#overviewBudgetPanel').hidden = true;
    $('#overviewBudgetPanel tfoot')?.remove();
    $('#loginScreen').hidden = true;
    $('#appShell').hidden = false;
    $('#reviewLinkLoginNote').hidden = true;
    $('#reviewLinkPanel').hidden = !state.reviewLinkNotice;
    if (state.reviewLinkNotice) setMessage($('#reviewLinkMessage'), state.reviewLinkNotice, true);
    $('#statusDock').hidden = localStorage.getItem('yuxu-status-hidden') === 'true';
    $('#restoreStatusButton').hidden = !$('#statusDock').hidden;
    $('#mobileRestoreStatusButton').hidden = !$('#statusDock').hidden;
    renderAccountPickers(session);
    const range = currentMonthRange();
    $('#periodStart').value = range.start_date;
    $('#periodEnd').value = range.end_date;
    $('#periodDateBasis').value = 'occurred_on';
    $('#filterForm [name="start_date"]').value = range.start_date;
    $('#filterForm [name="end_date"]').value = range.end_date;
    $('#filterDateBasis').value = 'occurred_on';
    const attachmentStart = new Date();
    attachmentStart.setDate(attachmentStart.getDate() - 90);
    $('#attachmentStartDate').value = localDate(attachmentStart);
    $('#attachmentEndDate').value = localDate();
    const view = location.hash.slice(1);
    setPage(Object.hasOwn(pageCopy, view) ? view : 'overview');
    const defaultOpen = localStorage.getItem('yuxu-status-default') === 'open';
    $('#statusDefaultSelect').value = defaultOpen ? 'open' : 'closed';
    if (!$('#statusDock').hidden && defaultOpen) openStatusPanel();
    loadAppData();
  }

  async function loadAppData() {
    $('#overviewMessage').textContent = '';
    $('#transactionsMessage').textContent = '';
    $('#reportsMessage').textContent = '';
    try {
      const catalog = await apiRequest('/api/tools');
      applyToolCatalog(catalog);
    } catch (error) {
      setMessage($('#overviewMessage'), errorText(error, '无法读取当前账号可用的账本操作。'), true);
      return;
    }
    try {
      await loadCategories();
    } catch (error) {
      setMessage($('#overviewMessage'), errorText(error, '无法读取分类。'), true);
    }
    await loadPeriodData();
    if (state.activeView === 'transactions' && !isCrossAccount()) await searchTransactions();
    await loadInsightViews();
    if (state.activeView === 'movements' && !isCrossAccount()) await loadPaymentAccountOptions();
    if (state.activeView === 'maintenance' && !isCrossAccount()) await refreshBackupSchedule();
    if (state.pendingReviewRef || state.pendingLocalWork) await resolvePendingReviewLink();
  }

  function insightViewConfig() {
    const period = state.selectedPeriodPreset
      ? {preset: ({month: 'this_month', previous: 'last_month', days7: 'last_7_days', days30: 'last_30_days', year: 'this_year'})[state.selectedPeriodPreset]}
      : {start_date: state.period.start_date, end_date: state.period.end_date};
    const config = {
      period,
      date_basis: state.period.date_basis,
      category_id: state.selectedCategoryId,
      chart: $('#categoryPanel').dataset.categoryDisplay === 'bars' ? 'bar' : $('#categoryPanel').dataset.categoryDisplay,
      aggregation: $('#quickTrendUnit').value
    };
    if (state.quickReportMode === 'compare') {
      const mode = $('#quickCompareMode').value;
      config.comparison = mode === 'explicit'
        ? {mode, start_date: $('#quickCompareStart').value, end_date: $('#quickCompareEnd').value}
        : {mode};
      if (mode === 'explicit' && (!config.comparison.start_date || !config.comparison.end_date || config.comparison.start_date > config.comparison.end_date)) {
        throw new Error('请选择有效的自选对照起止日期。');
      }
    }
    return config;
  }

  async function loadInsightViews() {
    const panel = $('#savedInsightViewsPanel');
    panel.hidden = isCrossAccount() || !hasTool('list_insight_views');
    if (panel.hidden) {
      state.insightViews = [];
      $('#savedInsightViewRows').replaceChildren();
      return;
    }
    try {
      const result = await callTool('list_insight_views');
      state.insightViews = envelopeData(result.envelope).views || [];
      renderInsightViews();
      setMessage($('#savedInsightViewMessage'), '');
    } catch (error) {
      state.insightViews = [];
      renderInsightViews();
      setMessage($('#savedInsightViewMessage'), errorText(error, '无法读取常看视图。'), true);
    }
  }

  function insightViewPeriodLabel(config) {
    if (config.period.preset) {
      return ({this_month: '本月', last_month: '上月', last_7_days: '近 7 天', last_30_days: '近 30 天', this_year: '今年'})[config.period.preset];
    }
    return config.period.start_date + ' 至 ' + config.period.end_date;
  }

  function renderInsightViews() {
    const target = $('#savedInsightViewRows');
    target.replaceChildren();
    if (!state.insightViews.length) {
      target.append(node('li', 'empty-cell', '还没有保存的常看视图。'));
      return;
    }
    for (const view of state.insightViews) {
      const item = node('li', 'saved-insight-view-row');
      const copy = node('div', 'saved-insight-view-copy');
      copy.append(node('strong', '', view.name));
      const config = view.config;
      const category = config.category_id ? categoryName(config.category_id) || '指定分类' : '全部分类';
      copy.append(node('span', '', [
        insightViewPeriodLabel(config),
        config.date_basis === 'charged_on' ? '按扣款日' : '按消费日',
        category,
        ({pie: '环形图', bar: '条形图', table: '表格'})[config.chart],
        ({day: '按日', week: '按周', month: '按月'})[config.aggregation]
      ].join(' · ')));
      const actions = node('div', 'saved-insight-view-actions');
      const apply = node('button', 'button button-secondary button-small', '应用');
      apply.type = 'button';
      apply.addEventListener('click', () => applyInsightView(view));
      const update = node('button', 'text-button', '用当前设置更新');
      update.type = 'button';
      update.addEventListener('click', () => upsertInsightView(view.name, view));
      const remove = node('button', 'text-button', '删除');
      remove.type = 'button';
      remove.addEventListener('click', () => deleteInsightView(view));
      actions.append(apply, update, remove);
      item.append(copy, actions);
      target.append(item);
    }
  }

  function applyInsightView(view) {
    const config = view.config;
    const presetNames = {this_month: 'month', last_month: 'previous', last_7_days: 'days7', last_30_days: 'days30', this_year: 'year'};
    const presetName = config.period.preset && presetNames[config.period.preset];
    const period = presetName ? periodPresets()[presetName] : config.period;
    $('#periodStart').value = period.start_date;
    $('#periodEnd').value = period.end_date;
    $('#periodDateBasis').value = config.date_basis;
    $('#filterForm [name="start_date"]').value = period.start_date;
    $('#filterForm [name="end_date"]').value = period.end_date;
    $('#filterDateBasis').value = config.date_basis;
    state.selectedPeriodPreset = presetName || null;
    state.selectedCategoryId = config.category_id;
    $('#categoryPanel').dataset.categoryDisplay = config.chart === 'bar' ? 'bars' : config.chart;
    for (const button of $$('#categoryDisplay button')) button.setAttribute('aria-pressed', String(button.dataset.categoryDisplay === $('#categoryPanel').dataset.categoryDisplay));
    $('#quickTrendUnit').value = config.aggregation;
    if (config.comparison) {
      $('#quickCompareMode').value = config.comparison.mode;
      $('#quickCompareStart').value = config.comparison.start_date || '';
      $('#quickCompareEnd').value = config.comparison.end_date || '';
      state.quickReportMode = 'compare';
      updateQuickReportControls();
    } else {
      clearQuickReport();
    }
    renderCategorySelection();
    $('#periodForm').requestSubmit();
  }

  async function upsertInsightView(name, existing = null) {
    const label = String(name || '').trim();
    if (!label || !state.period) return;
    const button = $('#savedInsightViewForm').querySelector('[type="submit"]');
    button.disabled = true;
    setMessage($('#savedInsightViewMessage'), '正在保存常看视图…');
    try {
      const params = {name: label, config: insightViewConfig()};
      if (existing) {
        params.view_id = existing.view_id;
        params.expected_version = existing.version;
      }
      await callTool('upsert_insight_view', params, true);
      if (!existing) $('#insightViewName').value = '';
      await loadInsightViews();
      setMessage($('#savedInsightViewMessage'), existing ? '常看视图已更新。' : '常看视图已保存。');
    } catch (error) {
      setMessage($('#savedInsightViewMessage'), errorText(error, '常看视图未保存。'), true);
    } finally {
      button.disabled = false;
    }
  }

  async function deleteInsightView(view) {
    if (!window.confirm('删除常看视图“' + view.name + '”？')) return;
    setMessage($('#savedInsightViewMessage'), '正在删除常看视图…');
    try {
      await callTool('delete_insight_view', {view_id: view.view_id, expected_version: view.version}, true);
      await loadInsightViews();
      setMessage($('#savedInsightViewMessage'), '常看视图已删除。');
    } catch (error) {
      setMessage($('#savedInsightViewMessage'), errorText(error, '常看视图未删除。'), true);
    }
  }

  function calendarStatusLabel(status) {
    return ({empty: '无记录', recorded_zero: '已记录零支出', pending: '待核', amount: '已记支出'})[status];
  }

  function renderAnnualCalendar(data) {
    const target = $('#annualCalendarGrid');
    target.replaceChildren();
    const days = Array.isArray(data.daily) ? data.daily : [];
    const canOpenDetails = !isCrossAccount() && hasTool('search_transactions');
    for (let month = 1; month <= 12; month += 1) {
      const monthDays = days.filter(day => Number(day.date.slice(5, 7)) === month);
      if (!monthDays.length) continue;
      const section = node('section', 'calendar-month');
      section.append(node('h3', '', month + ' 月'));
      const grid = node('div', 'calendar-month-grid');
      for (const label of ['一', '二', '三', '四', '五', '六', '日']) {
        const weekday = node('span', 'calendar-weekday', label);
        weekday.setAttribute('aria-hidden', 'true');
        grid.append(weekday);
      }
      const firstWeekday = new Date(monthDays[0].date + 'T00:00:00Z').getUTCDay();
      for (let offset = 0; offset < (firstWeekday + 6) % 7; offset += 1) {
        const spacer = node('span', 'calendar-day-spacer');
        spacer.setAttribute('aria-hidden', 'true');
        grid.append(spacer);
      }
      for (const day of monthDays) {
        const dayNumber = String(Number(day.date.slice(8, 10)));
        const selected = day.date === state.selectedCalendarDate;
        const button = node('button', 'calendar-day-button is-' + day.status);
        button.type = 'button';
        button.disabled = !canOpenDetails;
        button.setAttribute('aria-pressed', String(selected));
        button.setAttribute('aria-label', day.date + '，' + calendarStatusLabel(day.status) + '，已记支出 ' + money(day.actual_expense_minor) + '，待核 ' + money(day.pending_expense_minor) + '，' + count(day.record_count) + ' 笔记录');
        if (selected) button.classList.add('is-selected');
        button.append(node('span', 'calendar-day-number', dayNumber));
        const amount = Number(day.actual_expense_minor || 0);
        const pending = Number(day.pending_expense_minor || 0);
        const note = amount > 0 ? money(amount) : pending > 0 ? '待核 ' + money(pending) : Number(day.record_count || 0) > 0 ? count(day.record_count) + ' 笔' : '';
        button.append(node('span', 'calendar-day-amount', note));
        if (canOpenDetails) button.addEventListener('click', () => loadCalendarDate(day));
        grid.append(button);
      }
      section.append(grid);
      target.append(section);
    }
    $('#annualCalendarSubtitle').textContent = data.year + ' 年 · 按' + (data.date_basis === 'charged_on' ? '扣款日期' : '消费日期') + ' · ' + days.length + ' 天';
    if (!canOpenDetails) setMessage($('#annualCalendarMessage'), '当前授权仅包含汇总，不能打开流水明细。');
  }

  async function loadAnnualSpendingCalendar() {
    if (!hasTool('get_annual_spending_calendar') || !crossToolAllowed(state.toolCatalog.find(tool => tool.name === 'get_annual_spending_calendar'))) return;
    const year = Number($('#annualCalendarYear').value);
    if (!Number.isInteger(year) || year < 1 || year > 9999) {
      setMessage($('#annualCalendarMessage'), '请输入 1 至 9999 之间的年份。', true);
      return;
    }
    const version = ++state.calendarVersion;
    state.calendarDetailVersion++;
    state.selectedCalendarDate = null;
    state.annualCalendar = null;
    $('#calendarDayDetail').hidden = true;
    $('#annualCalendarGrid').replaceChildren(node('p', 'loading-block', '正在读取年度支出日历…'));
    setMessage($('#annualCalendarMessage'), '');
    const params = {year, date_basis: state.period?.date_basis || 'occurred_on'};
    if (state.selectedCategoryId) params.filters = {category_id: state.selectedCategoryId};
    try {
      const result = await callTool('get_annual_spending_calendar', params);
      if (version !== state.calendarVersion) return;
      state.annualCalendar = envelopeData(result.envelope);
      renderAnnualCalendar(state.annualCalendar);
    } catch (error) {
      if (version !== state.calendarVersion) return;
      $('#annualCalendarGrid').replaceChildren();
      setMessage($('#annualCalendarMessage'), errorText(error, '无法读取年度支出日历。'), true);
    }
  }

  async function loadCalendarDate(day) {
    if (isCrossAccount() || !hasTool('search_transactions')) return;
    state.selectedCalendarDate = day.date;
    renderAnnualCalendar(state.annualCalendar);
    const detail = $('#calendarDayDetail');
    detail.hidden = false;
    $('#calendarDaySummary').textContent = calendarStatusLabel(day.status) + ' · 已记支出 ' + money(day.actual_expense_minor) + ' · 待核 ' + money(day.pending_expense_minor) + ' · ' + count(day.record_count) + ' 笔记录';
    const loadingRow = node('tr');
    const loadingCell = node('td', 'loading-block', '正在读取这一天的流水…');
    loadingCell.colSpan = 6;
    loadingRow.append(loadingCell);
    $('#calendarDayRows').replaceChildren(loadingRow);
    setMessage($('#calendarDayMessage'), '');
    $('#calendarFullDetails').hidden = false;
    const version = ++state.calendarDetailVersion;
    const filters = {start_date: day.date, end_date: day.date};
    if (state.selectedCategoryId) filters.category_id = state.selectedCategoryId;
    try {
      const result = await callTool('search_transactions', {
        filters,
        date_basis: state.annualCalendar.date_basis,
        page: {limit: 25}
      });
      if (version !== state.calendarDetailVersion || state.selectedCalendarDate !== day.date) return;
      const data = envelopeData(result.envelope);
      const transactions = data.transactions || [];
      const target = $('#calendarDayRows');
      target.replaceChildren();
      if (!transactions.length) {
        showLoadError(target, '這一天沒有符合條件的流水。', 6);
      } else {
        for (const transaction of transactions) target.append(buildTransactionRow(transaction));
      }
      $('#calendarDaySummary').textContent += ' · 当前筛选共 ' + count(data.total_count) + ' 笔' + (data.next_cursor ? '，下方显示前 25 笔' : '');
    } catch (error) {
      if (version !== state.calendarDetailVersion) return;
      showLoadError($('#calendarDayRows'), errorText(error, '无法读取这一天的流水。'), 6);
      setMessage($('#calendarDayMessage'), errorText(error, '无法读取这一天的流水。'), true);
    }
  }

  function openCalendarDateInTransactions() {
    const date = state.selectedCalendarDate;
    if (!date || isCrossAccount()) return;
    $('#filterForm [name="start_date"]').value = date;
    $('#filterForm [name="end_date"]').value = date;
    $('#filterDateBasis').value = state.annualCalendar.date_basis;
    $('#filterForm [name="kind"]').value = '';
    $('#filterCategory').value = state.selectedCategoryId || '';
    changeView('transactions');
  }

  async function loadBudgetOverview(period, version) {
    const panel = $('#overviewBudgetPanel');
    $('#overviewBudgetPanel tfoot')?.remove();
    if (isCrossAccount() || !hasTool('get_budget_period_progress')) {
      panel.hidden = true;
      return;
    }
    const scope = state.toolScopes.get('get_budget_period_progress');
    if (isCrossAccount() ? scope !== 'admin_spending' : !['own_read', 'own_write'].includes(scope)) {
      panel.hidden = true;
      return;
    }
    panel.hidden = false;
    $('#budgetOverviewPeriod').textContent = '按消费日期读取月份：' + period.start_date.slice(0, 7);
    $('#openPlanningFromBudget').hidden = !groupHasTools('planning');
    const rowsTarget = $('#budgetOverviewRows');
    showLoadError(rowsTarget, '正在读取分类预算…', 5);
    setMessage($('#budgetOverviewMessage'), '');
    $('#budgetPeriodProgress').hidden = true;
    if (period.start_date.slice(0, 7) !== period.end_date.slice(0, 7)) {
      showLoadError(rowsTarget, '所选期间跨月；请改为单月范围后查看分类月预算。', 5);
      return;
    }
    try {
      const result = await callTool('get_budget_period_progress', {month: period.start_date.slice(0, 7), as_of: localDate()});
      if (version !== state.periodVersion) return;
      const data = envelopeData(result.envelope);
      const rows = Array.isArray(data.items) ? data.items : [];
      const budgetPeriod = data.period;
      const elapsedDays = Number(data.elapsed_days);
      const totalDays = Number(data.total_days);
      const ratio = Number(data.progress_ratio);
      $('#budgetOverviewPeriod').textContent = (budgetPeriod.start_date.slice(0, 7) || period.start_date.slice(0, 7)) +
        ' · 消费日期 ' + budgetPeriod.start_date + ' 至 ' + budgetPeriod.end_date + ' · 截至 ' + budgetPeriod.as_of;
      $('#budgetElapsedLabel').textContent = '已过 ' + count(elapsedDays) + ' / ' + count(totalDays) + ' 天';
      $('#budgetElapsedValue').textContent = percent(ratio * 100);
      const progress = $('#budgetElapsedProgress');
      progress.max = totalDays;
      progress.value = elapsedDays;
      progress.setAttribute('aria-valuetext', percent(ratio * 100));
      $('#budgetPeriodProgress').hidden = false;
      rowsTarget.replaceChildren();
      if (!rows.length) {
        showLoadError(rowsTarget, '本月还没有设置分类预算。', 5);
      } else {
        let budgetTotal = 0;
        let actualTotal = 0;
        let pendingTotal = 0;
        let remainingTotal = 0;
        let complete = true;
        let pendingComplete = true;
        for (const item of rows) {
          const tr = document.createElement('tr');
          const budget = item.total_allowance_minor ?? item.budget_minor;
          const actual = item.actual_paid_minor;
          const pending = item.pending_expense_minor;
          const remaining = item.remaining_minor;
          tr.append(node('td', '', item.category_path || categoryName(item.category_id) || '未分类'));
          tr.append(node('td', 'num', budget === undefined || budget === null ? '未设预算' : money(budget)));
          tr.append(node('td', 'num', actual === undefined || actual === null ? '无实付记录' : money(actual)));
          tr.append(node('td', 'num', pending === undefined || pending === null ? '—' : money(pending)));
          tr.append(node('td', 'num ' + (Number(remaining) < 0 ? 'amount-expense' : ''), remaining === undefined || remaining === null ? '未计算' : money(remaining)));
          rowsTarget.append(tr);
          if (budget === undefined || budget === null || actual === undefined || actual === null || remaining === undefined || remaining === null) complete = false;
          else if ([budget, actual, remaining].every(value => Number.isFinite(Number(value)))) {
            budgetTotal += Number(budget);
            actualTotal += Number(actual);
            remainingTotal += Number(remaining);
          } else complete = false;
          if (pending === undefined || pending === null) pendingComplete = false;
          else pendingTotal += Number(pending);
        }
        if (complete) {
          const foot = document.createElement('tfoot');
          const total = document.createElement('tr');
          total.className = 'table-total';
          total.append(node('th', '', '预算分类合计'), node('td', 'num', money(budgetTotal)), node('td', 'num', money(actualTotal)), node('td', 'num', pendingComplete ? money(pendingTotal) : '—'), node('td', 'num', money(remainingTotal)));
          foot.append(total);
          $('#overviewBudgetPanel table').append(foot);
        }
      }
    } catch (error) {
      if (version !== state.periodVersion) return;
      showLoadError(rowsTarget, errorText(error, '无法读取分类预算。'), 5);
      setMessage($('#budgetOverviewMessage'), errorText(error, '无法读取分类预算。'), true);
    }
  }

  function renderSummary(envelope, report = false) {
    const data = envelopeData(envelope);
    const income = data.income_minor;
    const expense = data.expense_minor;
    const net = data.net_minor;
    const messageId = report ? '#reportsMessage' : '#overviewMessage';
    if (income !== undefined && expense !== undefined && net !== undefined && Number(income) - Number(expense) !== Number(net)) {
      setMessage($(messageId), '收支合计与净额不一致，已暂停展示本次汇总。', true);
      if (report) $('#reportMetrics').replaceChildren(node('p', 'error-inline', '收支合计与净额不一致，已暂停展示本次汇总。'));
      else $('#overviewSummary').replaceChildren(node('p', 'error-inline', '收支合计与净额不一致，已暂停展示本次汇总。'));
      return;
    }
    state.summary = data;
    if (report) {
      $('#reportPeriodLabel').textContent = periodLabel(state.period);
      const metrics = $('#reportMetrics');
      metrics.replaceChildren();
      const values = [];
      if (data.income_minor !== undefined) values.push(['收入', signedMoney(data.income_minor, 'income')]);
      if (data.expense_minor !== undefined) values.push(['支出', money(data.expense_minor)]);
      if (data.net_minor !== undefined) values.push(['净额', money(data.net_minor)]);
      if (data.record_count !== undefined) values.push(['流水数量', count(data.record_count)]);
      if (data.estimated_count !== undefined && Number(data.estimated_count) > 0) values.push(['估算流水', count(data.estimated_count)]);
      for (const item of values) {
        const box = node('dl', 'report-metric');
        box.append(node('dt', '', item[0]), node('dd', '', item[1]));
        metrics.append(box);
      }
    } else {
      const card = $('#overviewSummary');
      card.replaceChildren();
      const lead = node('div', 'lead-number-block');
      const label = node('p', 'lead-label', '本期支出');
      const value = node('p', 'lead-number', money(data.expense_minor));
      const detail = node('p', 'lead-detail');
      detail.textContent = periodLabel(state.period) + ' · ' + count(data.record_count) + ' 笔流水';
      lead.append(label, value, detail);
      const facts = node('dl', 'lead-side');
      const entries = [];
      if (data.income_minor !== undefined) entries.push(['收入', signedMoney(data.income_minor, 'income'), true]);
      if (data.net_minor !== undefined) entries.push(['收支净额', money(data.net_minor), false]);
      if (Number(data.estimated_count || 0) > 0) entries.push(['估算流水', count(data.estimated_count) + ' 笔', false]);
      if (Number(data.pending_count || 0) > 0) entries.push(['待确认', count(data.pending_count) + ' 笔', false]);
      for (const entry of entries) {
        const box = node('div', 'lead-fact');
        box.append(node('dt', '', entry[0]));
        const figure = node('dd', entry[2] ? 'positive' : '', entry[1]);
        box.append(figure);
        facts.append(box);
      }
      card.append(lead, facts);
    }
  }

  function formatShare(value, total) {
    if (!total) return '-';
    return percent(Number(value) / Number(total) * 100);
  }

  function categoryLabel(item) {
    return item.path || item.category_path || categoryName(item.category_id) || (item.category_id ? '未命名分类' : '未分类');
  }

  function renderCategoryComposition(data, canDrillDown) {
    const target = $('#categoryComposition');
    target.replaceChildren();
    target.classList.remove('is-chart');
    const items = Array.isArray(data.items) ? data.items.slice().sort((a, b) => Number(b.expense_minor || 0) - Number(a.expense_minor || 0)) : [];
    if (!items.length) {
      target.hidden = true;
      return;
    }

    const itemTotal = items.reduce((sum, item) => sum + Number(item.expense_minor || 0), 0);
    const total = Number(data.expense_minor ?? itemTotal);
    const summaryExpense = state.summary && Number(state.summary.expense_minor);
    const totalsMatch = itemTotal === total && (summaryExpense === null || summaryExpense === undefined || summaryExpense === total);
    if (!totalsMatch || items.some(item => Number(item.expense_minor) < 0)) {
      target.append(node('p', 'category-pie-note', totalsMatch ? '本期有负数支出，请查看下方明细。' : '分类合计与期间摘要不同，请查看下方金额。'));
      target.hidden = false;
      return;
    }

    const palette = ['#1d5fbf', '#425a74', '#739ac4', '#526e92', '#a2b1c1', '#d3dbe4'];
    const topItems = items.slice(0, 5);
    const otherItems = items.slice(5);
    const buckets = topItems.map((item, index) => ({
      label: categoryLabel(item),
      amount: Number(item.expense_minor || 0),
      categoryId: item.category_id,
      color: palette[index]
    }));
    if (otherItems.length) {
      buckets.push({
        label: '其他',
        amount: otherItems.reduce((sum, item) => sum + Number(item.expense_minor || 0), 0),
        color: palette[5],
        other: true
      });
    }

    const figure = node('figure', 'category-pie-figure');
    figure.append(node('figcaption', 'visually-hidden', '分类支出占比环形图'));
    const chart = node('div', 'category-pie-chart');
    chart.setAttribute('aria-hidden', 'true');
    chart.append(node('span', 'category-pie-total', money(total)));
    if (total > 0) {
      let angle = 0;
      const stops = [];
      for (const bucket of buckets) {
        const share = Math.max(0, bucket.amount / total * 100);
        if (!share) continue;
        const end = angle + share;
        stops.push(bucket.color + ' ' + angle + '% ' + end + '%');
        angle = end;
      }
      chart.style.background = 'conic-gradient(' + stops.join(', ') + ')'; // taste-skill-ignore: hard stops encode the recorded category shares.
    }
    figure.append(chart);

    const legend = node('div', 'category-pie-legend');
    const legendHead = node('div', 'category-pie-legend-head');
    const legendSpacer = node('span');
    legendSpacer.setAttribute('aria-hidden', 'true');
    legendHead.append(legendSpacer, node('span', '', '分类'), node('span', '', '金额'), node('span', '', '占比'));
    const list = node('ul', 'category-pie-list');
    for (const bucket of buckets) {
      const row = node('li', 'category-pie-entry');
      const swatch = node('span', 'category-pie-swatch');
      swatch.style.setProperty('--pie-color', bucket.color);
      swatch.setAttribute('aria-hidden', 'true');
      const label = bucket.other ? node('button', 'category-pie-other-toggle', '其他') : canDrillDown && bucket.categoryId
        ? node('button', 'category-pie-entry-name category-pie-link', bucket.label)
        : node('span', 'category-pie-entry-name', bucket.label);
      if (bucket.other) {
        label.type = 'button';
        label.dataset.otherToggle = 'true';
        label.dataset.collapsedText = '展开其他分类（' + count(otherItems.length) + '）';
        label.dataset.expandedText = '收起其他分类';
        label.dataset.otherCount = String(otherItems.length);
        label.setAttribute('aria-expanded', 'false');
        label.setAttribute('aria-controls', 'categoryPieOtherItems');
      } else if (canDrillDown && bucket.categoryId) {
        label.type = 'button';
        label.dataset.categoryId = bucket.categoryId;
        label.setAttribute('aria-label', '按' + bucket.label + '筛选图表');
        label.setAttribute('aria-pressed', String(String(state.selectedCategoryId) === String(bucket.categoryId)));
        if (String(state.selectedCategoryId) === String(bucket.categoryId)) label.classList.add('is-selected');
      } else if (bucket.categoryId && String(state.selectedCategoryId) === String(bucket.categoryId)) {
        label.classList.add('is-selected');
        label.setAttribute('aria-current', 'true');
      }
      const amount = node('span', 'category-pie-amount', money(bucket.amount));
      const share = node('span', 'category-pie-share', total > 0 ? formatShare(bucket.amount, total) : '0.0%');
      row.append(swatch, label, amount, share);
      list.append(row);
    }
    legend.append(legendHead, list);

    if (otherItems.length) {
      const otherList = node('ul', 'category-pie-other-list');
      otherList.id = 'categoryPieOtherItems';
      otherList.hidden = true;
      for (let index = 0; index < otherItems.length; index += 1) {
        const item = otherItems[index];
        const row = node('li', 'category-pie-entry');
        const swatch = node('span', 'category-pie-swatch');
        swatch.style.setProperty('--pie-color', palette[(index + 5) % palette.length]);
        swatch.setAttribute('aria-hidden', 'true');
        const label = canDrillDown && item.category_id
          ? node('button', 'category-pie-entry-name category-pie-link', categoryLabel(item))
          : node('span', 'category-pie-entry-name', categoryLabel(item));
        if (canDrillDown && item.category_id) {
          label.type = 'button';
          label.dataset.categoryId = item.category_id;
          label.setAttribute('aria-label', '按' + categoryLabel(item) + '筛选图表');
          label.setAttribute('aria-pressed', String(String(state.selectedCategoryId) === String(item.category_id)));
          if (String(state.selectedCategoryId) === String(item.category_id)) label.classList.add('is-selected');
        } else if (item.category_id && String(state.selectedCategoryId) === String(item.category_id)) {
          label.classList.add('is-selected');
          label.setAttribute('aria-current', 'true');
        }
        row.append(swatch, label, node('span', 'category-pie-amount', money(item.expense_minor)), node('span', 'category-pie-share', total > 0 ? formatShare(item.expense_minor, total) : '0.0%'));
        otherList.append(row);
      }
      legend.append(otherList);
    }

    target.append(figure, legend);
    if (total === 0) target.append(node('p', 'category-pie-note', '分类数据已返回，但支出金额为零。'));
    if (!canDrillDown && isCrossAccount()) target.append(node('p', 'category-pie-note', '当前授权只允许查看分类汇总，不包含流水明细。'));
    target.classList.add('is-chart');
    target.hidden = false;
  }

  function renderCategoryRows(data, target, foot = null, makeFilter = true) {
    const rows = target;
    rows.replaceChildren();
    if (foot) foot.replaceChildren();
    const items = data.items || [];
    if (!items.length) {
      const tr = node('tr');
      const td = node('td', 'empty-cell', '此期间没有分类支出。');
      td.colSpan = 3;
      tr.append(td);
      rows.append(tr);
      return;
    }
    const totalOfItems = items.reduce((sum, item) => sum + Number(item.expense_minor || 0), 0);
    const total = Number(data.expense_minor || 0);
    const summaryExpense = state.summary && Number(state.summary.expense_minor);
    const totalsMatch = totalOfItems === total && (summaryExpense === null || summaryExpense === undefined || summaryExpense === total);
    if (!totalsMatch) setMessage($('#reportsMessage'), '分类明细与期间摘要口径不同；金额已列出，占比暂不展示。', true);
    const sorted = items.slice().sort((a, b) => Number(b.expense_minor) - Number(a.expense_minor));
    for (const item of sorted) {
      const tr = node('tr');
      const nameCell = node('td');
      const cellContent = node('div', 'category-name-cell');
      if (makeFilter && item.category_id) {
        const button = node('button', 'category-name-button', item.path || categoryName(item.category_id) || '未命名分类');
        button.type = 'button';
        button.dataset.categoryId = item.category_id;
        button.setAttribute('aria-label', '筛选分类 ' + button.textContent);
        button.setAttribute('aria-pressed', String(String(state.selectedCategoryId) === String(item.category_id)));
        if (String(state.selectedCategoryId) === String(item.category_id)) button.classList.add('is-selected');
        cellContent.append(button);
      } else {
        const label = node('span', 'category-tile', item.path || categoryName(item.category_id) || '未分类');
        if (item.category_id && String(state.selectedCategoryId) === String(item.category_id)) {
          label.classList.add('is-selected');
          label.setAttribute('aria-current', 'true');
        }
        cellContent.append(label);
      }
      if (rows.id === 'categoryRows' && totalsMatch) {
        const bar = node('div', 'category-bar');
        const fill = node('span');
        const share = total ? Math.max(0, Math.min(100, Number(item.expense_minor || 0) / total * 100)) : 0;
        fill.style.setProperty('--bar-width', share + '%');
        bar.setAttribute('aria-hidden', 'true');
        bar.append(fill);
        cellContent.append(bar);
      }
      nameCell.append(cellContent);
      const amountCell = node('td', 'num', money(item.expense_minor));
      const shareCell = node('td', 'num share-value', totalsMatch ? formatShare(item.expense_minor, total) : '-');
      tr.append(nameCell, amountCell, shareCell);
      rows.append(tr);
    }
    if (foot) {
      const tr = node('tr', 'table-total');
      tr.append(node('th', '', '合计'), node('td', 'num', money(total)), node('td', 'num', totalsMatch ? formatShare(total, total) : '-'));
      foot.append(tr);
    }
  }

  function renderCategorySelection() {
    const hasSelection = Boolean(state.selectedCategoryId);
    const selected = hasSelection && !isCrossAccount();
    const label = categoryName(state.selectedCategoryId) || '所选分类';
    $('#categorySelection').hidden = !hasSelection;
    $('#openCategoryTransactions').hidden = !selected;
    $('#selectedCategoryLabel').textContent = hasSelection ? '当前分类：' + label : '';
    $('#recentSubtitle').textContent = selected ? '分类：' + label + ' · 按日期从新到旧' : '按日期从新到旧';
    $('#reportCategoryFilter').value = hasSelection ? state.selectedCategoryId : '';
    for (const button of $$('[data-category-id]', $('#categoryComposition')).concat($$('[data-category-id]', $('#categoryRows')))) {
      const active = hasSelection && String(button.dataset.categoryId) === String(state.selectedCategoryId);
      button.setAttribute('aria-pressed', String(active));
      button.classList.toggle('is-selected', active);
    }
  }

  function setSelectedCategory(categoryId) {
    const normalized = categoryId ? String(categoryId) : null;
    if (normalized === state.selectedCategoryId) return;
    state.selectedCategoryId = normalized;
    renderCategorySelection();
    if (state.period) loadPeriodData();
  }

  function transactionDescription(transaction) {
    return transaction.merchant || transaction.note || '未填写商家';
  }

  function transactionSubline(transaction) {
    const pieces = [];
    if (transaction.category_path || categoryName(transaction.category_id)) pieces.push(transaction.category_path || categoryName(transaction.category_id));
    if (transaction.note && transaction.note !== transaction.merchant) pieces.push(transaction.note);
    if (Array.isArray(transaction.tags) && transaction.tags.length) pieces.push(transaction.tags.join(' · '));
    return pieces.join(' · ') || '-';
  }

  function transactionState(transaction) {
    if (transaction.voided) return ['已作废', 'is-voided'];
    if (transaction.estimated) return ['估算', 'is-estimated'];
    if (transaction.confirmation_status === 'pending') return ['待确认', 'is-pending'];
    return ['已记录', ''];
  }

  async function copyTransactionToDraft(transactionId, button) {
    if (isCrossAccount() || !hasTool('copy_transaction_to_draft') || !hasTool('upsert_transaction_draft')) return;
    if (button) button.disabled = true;
    try {
      const result = await callTool('copy_transaction_to_draft', {transaction_id: transactionId}, true);
      const draft = envelopeData(result.envelope).draft;
      if (!draft || !draft.draft_id) throw new Error('服务没有返回可继续编辑的草稿。');
      state.draftCopyContext = {draft_id: draft.draft_id, source_transaction_id: transactionId};
      state.draftCopyNeedsFreshFields = true;
      state.draftCommitContext = null;
      state.entityOptions.drafts = null;
      await openFeatureActionWithValues('upsert_transaction_draft', {
        draft_id: draft.draft_id,
        expected_version: draft.version,
        fields: draft.fields || {}
      }, 'flow');
    } catch (error) {
      showNotice(errorText(error, '无法将此流水复制为草稿。'));
    } finally {
      if (button) button.disabled = false;
    }
  }

  function buildTransactionRow(transaction, compact = false) {
    const tr = node('tr', transaction.voided ? 'is-voided' : '');
    const details = node('td');
    details.dataset.label = '项目';
    details.append(node('span', 'transaction-name', transactionDescription(transaction)));
    const subline = node('span', 'transaction-subline', transactionSubline(transaction));
    details.append(subline);
    const status = transactionState(transaction);
    if (compact) {
      details.append(node('span', 'mobile-meta', (transaction.occurred_on || '-') + ' · ' + status[0]));
      const date = node('td', '', transaction.occurred_on || '-');
      date.dataset.label = '日期';
      const amount = node('td', 'num ' + (transaction.kind === 'income' ? 'amount-income' : 'amount-expense'), signedMoney(transaction.amount_minor, transaction.kind));
      amount.dataset.label = '金额';
      tr.append(details, date, amount);
      return tr;
    }
    const category = node('td', '', transaction.category_path || categoryName(transaction.category_id) || '未分类');
    category.dataset.label = '分类';
    const date = node('td', '', transaction.occurred_on || '-');
    date.dataset.label = '日期';
    const statusCell = node('td');
    statusCell.dataset.label = '状态';
    statusCell.append(node('span', 'state-label ' + status[1], status[0]));
    details.append(node('span', 'mobile-meta', (transaction.category_path || categoryName(transaction.category_id) || '未分类') + ' · ' + (transaction.occurred_on || '-') + ' · ' + status[0]));
    const amount = node('td', 'num ' + (transaction.kind === 'income' ? 'amount-income' : 'amount-expense'), signedMoney(transaction.amount_minor, transaction.kind));
    amount.dataset.label = '金额';
    const actions = node('td', 'row-actions');
    actions.dataset.label = '操作';
    if (hasTool('update_transaction') && !transaction.voided) {
      const edit = node('button', 'row-action', '更正');
      edit.type = 'button';
      edit.dataset.editId = transaction.transaction_id;
      actions.append(edit);
      if (!isCrossAccount() && hasTool('copy_transaction_to_draft') && hasTool('upsert_transaction_draft')) {
        const copy = node('button', 'row-action', '复制为草稿');
        copy.type = 'button';
        copy.dataset.copyDraftId = transaction.transaction_id;
        actions.append(copy);
      }
    } else {
      if (!transaction.voided && !isCrossAccount() && hasTool('copy_transaction_to_draft') && hasTool('upsert_transaction_draft')) {
        const copy = node('button', 'row-action', '复制为草稿');
        copy.type = 'button';
        copy.dataset.copyDraftId = transaction.transaction_id;
        actions.append(copy);
      } else actions.textContent = '-';
    }
    tr.append(details, category, date, statusCell, amount, actions);
    return tr;
  }

  function renderRecent(envelope) {
    const data = envelopeData(envelope);
    const body = $('#recentRows');
    body.replaceChildren();
    const transactions = data.transactions || [];
    if (!transactions.length) {
      const tr = node('tr');
      const td = node('td', 'empty-cell', '此期间还没有流水。');
      td.colSpan = 3;
      tr.append(td);
      body.append(tr);
      return;
    }
    for (const transaction of transactions.slice(0, 5)) body.append(buildTransactionRow(transaction, true));
  }

  function showLoadError(target, message, columns = 1) {
    target.replaceChildren();
    const row = node('tr');
    const cell = node('td', 'error-inline', message);
    cell.colSpan = columns;
    row.append(cell);
    target.append(row);
  }

  async function loadPeriodData() {
    const period = currentPeriod();
    if (!validatePeriod(period)) {
      setMessage($('#periodMessage'), '请选择有效的开始与结束日期。', true);
      return;
    }
    setMessage($('#periodMessage'), '');
    const version = ++state.periodVersion;
    state.period = period;
    state.summary = null;
    state.breakdown = null;
    renderCategorySelection();
    syncPeriodPresets(period);
    updateQuickReportControls();
    const reportMode = state.quickReportMode || (state.activeView === 'visualization' && defaultQuickReportMode());
    if (reportMode) loadQuickReport(reportMode);
    const calendarPanel = $('#annualCalendarPanel');
    state.calendarVersion++;
    state.calendarDetailVersion++;
    state.annualCalendar = null;
    $('#calendarDayDetail').hidden = true;
    if (state.activeView === 'visualization' && calendarPanel.open && !calendarPanel.hidden) loadAnnualSpendingCalendar();
    setMessage($('#overviewMessage'), '');
    setMessage($('#reportsMessage'), '');
    $('#overviewBudgetPanel').hidden = true;
    $('#reportCategoryFoot').replaceChildren();
    if (isCrossAccount() && !targetHasScope('admin_spending')) {
      $('#overviewBudgetPanel').hidden = true;
      $('#overviewSummary').replaceChildren(node('p', 'empty-cell', '此账本授权仅包含储蓄目标进度。请打开“项目预算与目标”查看。'));
      $('#reportMetrics').replaceChildren(node('p', 'empty-cell', '当前授权不包含支出汇总。'));
      $('#categoryRows').replaceChildren();
      $('#overviewCategoryRows').replaceChildren();
      $('#reportCategoryRows').replaceChildren();
      $('#recentRows').replaceChildren();
      $('#categoryComposition').replaceChildren();
      $('#categoryComposition').hidden = true;
      state.breakdown = null;
      state.recent = [];
      return;
    }
    $('#overviewSummary').replaceChildren(node('p', 'loading-block', '正在读取所选期间…'));
    $('#reportMetrics').replaceChildren(node('p', 'loading-block', '正在读取所选期间…'));
    showLoadError($('#categoryRows'), '正在读取分类…', 3);
    showLoadError($('#overviewCategoryRows'), '正在读取分类…', 3);
    showLoadError($('#reportCategoryRows'), '正在读取分类…', 3);
    showLoadError($('#recentRows'), '正在读取流水…', 3);
    $('#categoryComposition').classList.remove('is-chart');
    $('#categoryComposition').hidden = true;
    if (state.activeView === 'overview') setPage('overview');
    const params = {start_date: period.start_date, end_date: period.end_date, date_basis: period.date_basis};
    const categorySummaryParams = isCrossAccount() && state.selectedCategoryId
      ? {...params, filters: {category_id: state.selectedCategoryId}} : params;
    const summaryTask = callTool('get_period_summary', categorySummaryParams).then(result => result.envelope);
    const categoryTask = callTool('get_category_breakdown', categorySummaryParams).then(result => result.envelope);
    const categoryFilter = !isCrossAccount() && state.selectedCategoryId ? {category_id: state.selectedCategoryId} : null;
    const recentTask = isCrossAccount() ? null : callTool('search_transactions', {
      filters: Object.assign({start_date: period.start_date, end_date: period.end_date}, categoryFilter || {}),
      date_basis: period.date_basis,
      page: {limit: 5}
    }).then(result => result.envelope);
    const results = await Promise.allSettled([
      summaryTask, categoryTask, recentTask
    ]);
    if (version !== state.periodVersion) return;
    if (results[0].status === 'fulfilled') {
      renderSummary(results[0].value);
      renderSummary(results[0].value, true);
    } else {
      setMessage($('#overviewMessage'), errorText(results[0].reason, '无法读取期间摘要。'), true);
      $('#overviewSummary').replaceChildren(node('p', 'error-inline', errorText(results[0].reason, '无法读取期间摘要。')));
      $('#reportMetrics').replaceChildren(node('p', 'error-inline', errorText(results[0].reason, '无法读取期间摘要。')));
    }
    if (results[1].status === 'fulfilled') {
      state.breakdown = envelopeData(results[1].value);
      renderCategoryComposition(state.breakdown, !isCrossAccount() && hasTool('search_transactions') && hasTool('get_rolling_spending_trend'));
      renderCategoryRows(state.breakdown, $('#categoryRows'), null, !isCrossAccount() && hasTool('search_transactions') && hasTool('get_rolling_spending_trend'));
      renderCategoryRows(state.breakdown, $('#overviewCategoryRows'), null, false);
      renderCategoryRows(state.breakdown, $('#reportCategoryRows'), $('#reportCategoryFoot'), false);
      renderCategorySelection();
    } else {
      showLoadError($('#categoryRows'), errorText(results[1].reason, '无法读取分类支出。'), 3);
      showLoadError($('#overviewCategoryRows'), errorText(results[1].reason, '无法读取分类支出。'), 3);
      showLoadError($('#reportCategoryRows'), errorText(results[1].reason, '无法读取分类支出。'), 3);
    }
    if (isCrossAccount()) {
      state.recent = [];
    } else if (results[2].status === 'fulfilled') {
      state.recent = envelopeData(results[2].value).transactions || [];
      renderRecent(results[2].value);
    } else {
      showLoadError($('#recentRows'), errorText(results[2].reason, '无法读取最近流水。'), 3);
    }
    delete state.entityOptions.transactions;
    await loadBudgetOverview(period, version);
  }

  function readFilters() {
    const form = $('#filterForm');
    const filters = {};
    for (const key of ['start_date', 'end_date', 'kind', 'category_id', 'keyword', 'tag']) {
      const value = form.elements[key].value.trim();
      if (value) filters[key] = value;
    }
    for (const key of ['amount_min', 'amount_max']) {
      const value = form.elements[key].value.trim();
      if (value) filters[key === 'amount_min' ? 'amount_min_minor' : 'amount_max_minor'] = parseAmount(value);
    }
    if (filters.start_date && filters.end_date && filters.start_date > filters.end_date) throw new Error('开始日期不能晚于结束日期。');
    if (filters.amount_min_minor !== undefined && filters.amount_max_minor !== undefined && filters.amount_min_minor > filters.amount_max_minor) {
      throw new Error('最低金额不能高于最高金额。');
    }
    return filters;
  }

  function setFilterDisclosure(collapsed) {
    const mobile = matchMedia('(max-width: 640px)').matches;
    const toggle = $('#toggleFiltersButton');
    const fields = $('#filterFields');
    toggle.hidden = !mobile;
    fields.hidden = mobile && collapsed;
    toggle.setAttribute('aria-expanded', String(!fields.hidden));
    toggle.textContent = fields.hidden ? '展开条件' : '收起条件';
  }

  function parseAmount(value, allowNegative = false) {
    const input = String(value).trim();
    const match = input.match(allowNegative ? /^(-?)(\d+)(?:\.(\d{1,2}))?$/ : /^(\d+)(?:\.(\d{1,2}))?$/);
    if (!match) throw new Error('金额请使用元为单位，最多输入两位小数。');
    const negative = allowNegative && match[1] === '-';
    const whole = Number(allowNegative ? match[2] : match[1]);
    const cents = Number(((allowNegative ? match[3] : match[2]) || '').padEnd(2, '0'));
    const minor = (whole * 100 + cents) * (negative ? -1 : 1);
    if (!Number.isSafeInteger(minor)) throw new Error('金额超出可录入范围。');
    return minor;
  }

  async function searchTransactions(append = false) {
    if (isCrossAccount()) return;
    let filters;
    try {
      filters = readFilters();
    } catch (error) {
      setMessage($('#transactionsMessage'), errorText(error, '无法读取流水。'), true);
      return;
    }
    state.listFilters = filters;
    const page = {limit: 25};
    if (append && state.nextCursor) page.after = state.nextCursor;
    if (!append) {
      state.nextCursor = null;
      $('#transactionRows').replaceChildren();
    }
    $('#loadMoreButton').disabled = true;
    setMessage($('#transactionsMessage'), append ? '正在读取后续流水…' : '正在读取流水…');
    try {
      const result = await callTool('search_transactions', {filters, date_basis: $('#filterDateBasis').value || 'occurred_on', page});
      const data = envelopeData(result.envelope);
      const tbody = $('#transactionRows');
      if (!append) tbody.replaceChildren();
      const transactions = data.transactions || [];
      state.currentPageTransactions = append ? (state.currentPageTransactions || []).concat(transactions) : transactions;
      delete state.entityOptions.transactions;
      if (!transactions.length && !append) {
        const tr = node('tr');
        const td = node('td', 'empty-cell', '没有符合条件的流水。');
        td.colSpan = 6;
        tr.append(td);
        tbody.append(tr);
      } else {
        for (const transaction of transactions) tbody.append(buildTransactionRow(transaction));
      }
      state.nextCursor = data.next_cursor || null;
      $('#loadMoreButton').hidden = !state.nextCursor;
      $('#loadMoreButton').disabled = false;
      const countText = count(data.total_count) + ' 笔 · 收入 ' + money(data.total_income_minor) + ' · 支出 ' + money(data.total_expense_minor);
      $('#listSummary').textContent = countText;
      setMessage($('#transactionsMessage'), '');
      if (state.activeView !== 'transactions' && !append) state.recent = transactions;
    } catch (error) {
      $('#loadMoreButton').hidden = true;
      showLoadError($('#transactionRows'), errorText(error, '无法读取流水。'), 6);
      setMessage($('#transactionsMessage'), errorText(error, '无法读取流水。'), true);
    }
  }

  function addField(parent, labelText, english, control) {
    const label = node('label');
    const caption = node('span', '', labelText + ' ');
    const en = node('span', '', english);
    en.lang = 'en';
    caption.append(en);
    label.append(caption, control);
    parent.append(label);
    return control;
  }

  function addSelectOption(select, value, labelText) {
    const option = node('option', '', labelText);
    option.value = value;
    select.append(option);
  }

  function createEntryRow() {
    const row = node('div', 'entry-row');
    const kind = node('select');
    kind.dataset.field = 'kind';
    addSelectOption(kind, 'expense', '支出');
    addSelectOption(kind, 'income', '收入');
    addField(row, '方向', 'Type', kind);

    const amount = node('input');
    amount.type = 'text';
    amount.inputMode = 'decimal';
    amount.required = true;
    amount.autocomplete = 'off';
    amount.placeholder = '0.00';
    amount.dataset.field = 'amount';
    addField(row, '金额（元）', 'CNY', amount);

    const date = node('input');
    date.type = 'date';
    date.dataset.field = 'occurred_on';
    addField(row, '日期', 'Date', date);

    const category = node('select');
    category.dataset.field = 'category_id';
    addField(row, '分类', 'Category', category);

    const merchant = node('input');
    merchant.type = 'text';
    merchant.maxLength = 160;
    merchant.autocomplete = 'off';
    merchant.dataset.field = 'merchant';
    addField(row, '商家或项目', 'Merchant / item', merchant);

    const remove = node('button', 'icon-button remove-entry', '×');
    remove.type = 'button';
    remove.setAttribute('aria-label', '删除这笔录入');
    remove.title = '删除这笔录入';
    row.append(remove);
    populateEntryCategory(row);

    const details = node('details', 'entry-details');
    const summary = node('summary', '', '其他字段');
    details.append(summary);
    const more = node('div', 'entry-extra-fields');

    const estimatedLabel = node('label', 'checkbox-label');
    const estimated = node('input');
    estimated.type = 'checkbox';
    estimated.dataset.field = 'estimated';
    const estimatedText = node('span', '', '估算金额');
    estimatedLabel.append(estimated, estimatedText);
    more.append(estimatedLabel);

    const confirmation = node('select');
    confirmation.dataset.field = 'confirmation_status';
    addSelectOption(confirmation, 'confirmed', '已确认');
    addSelectOption(confirmation, 'pending', '待确认');
    addField(more, '确认状态', 'Confirmation', confirmation);

    const tags = node('input');
    tags.type = 'text';
    tags.maxLength = 300;
    tags.autocomplete = 'off';
    tags.dataset.field = 'tags';
    addField(more, '标签，逗号分隔', 'Tags', tags);

    const note = node('textarea');
    note.rows = 2;
    note.maxLength = 1000;
    note.dataset.field = 'note';
    addField(more, '备注', 'Note', note);
    details.append(more);
    row.append(details);
    return row;
  }

  function addEntryRow() {
    if ($('#entryRows').children.length >= 100) return;
    $('#entryRows').append(createEntryRow());
  }

  function openEntryDialog() {
    if (!hasTool('add_transactions')) return;
    $('#entryRows').replaceChildren();
    addEntryRow();
    setMessage($('#entryMessage'), '');
    $('#entryDialog').showModal();
    $('[data-field="amount"]', $('#entryRows')).focus();
  }

  function entryFromRow(row) {
    const amountMinor = parseAmount($('[data-field="amount"]', row).value);
    if (amountMinor <= 0) throw new Error('金额必须大于 0。');
    const transaction = {
      kind: $('[data-field="kind"]', row).value,
      amount_minor: amountMinor
    };
    const date = $('[data-field="occurred_on"]', row).value;
    const categoryId = $('[data-field="category_id"]', row).value;
    const merchant = $('[data-field="merchant"]', row).value.trim();
    const estimated = $('[data-field="estimated"]', row).checked;
    const confirmation = $('[data-field="confirmation_status"]', row).value;
    const tags = $('[data-field="tags"]', row).value.split(/[，,]/).map(value => value.trim()).filter(Boolean);
    const note = $('[data-field="note"]', row).value.trim();
    if (date) transaction.occurred_on = date;
    if (categoryId) transaction.category_id = categoryId;
    if (merchant) transaction.merchant = merchant;
    if (estimated) transaction.estimated = true;
    if (confirmation !== 'confirmed') transaction.confirmation_status = confirmation;
    if (tags.length) transaction.tags = tags;
    if (note) transaction.note = note;
    return transaction;
  }

  function showNotice(message, undoable = false) {
    $('#noticeText').textContent = message;
    $('#undoButton').hidden = !undoable;
    $('#notice').hidden = false;
    window.clearTimeout(state.noticeTimer);
    state.noticeTimer = window.setTimeout(() => { $('#notice').hidden = true; }, 8000);
  }

  async function refreshAfterWrite() {
    await loadPeriodData();
    if (state.activeView === 'transactions') await searchTransactions();
  }

  async function submitEntries(event) {
    event.preventDefault();
    const rows = $$('.entry-row', $('#entryRows'));
    if (!rows.length) return;
    $('#entryForm').querySelector('[type="submit"]').disabled = true;
    setMessage($('#entryMessage'), '');
    try {
      const transactions = rows.map(entryFromRow);
      const result = await callTool('add_transactions', {transactions}, true);
      $('#entryDialog').close();
      const noun = transactions.length === 1 ? '1 笔流水已保存。' : transactions.length + ' 笔流水已保存。';
      const warnings = envelopeData(result.envelope).warnings || [];
      const warningCopy = warnings.length
        ? '发现 ' + warnings.length + ' 笔可能相似；本次流水已单独保存，未自动合并。' +
          warnings.slice(0, 2).map(warning => {
            const candidate = warning.candidates && warning.candidates[0];
            return candidate
              ? '相似记录：' + (candidate.occurred_on || '-') + ' · ' + (candidate.category_path || categoryName(candidate.category_id) || '未分类') + ' · ' + money(candidate.amount_minor)
              : '请在流水中核对相似记录。';
          }).join(' ')
        : '';
      showNotice(noun + (warningCopy ? ' ' + warningCopy : ''), Boolean(state.lastUndoableRequestId && hasTool('undo_operation')));
      await refreshAfterWrite();
    } catch (error) {
      setMessage($('#entryMessage'), errorText(error, '流水未保存。请检查内容后重试。'), true);
    } finally {
      $('#entryForm').querySelector('[type="submit"]').disabled = false;
    }
  }

  function openEditDialog(transaction) {
    const form = $('#editForm');
    form.elements.transaction_id.value = transaction.transaction_id;
    form.elements.expected_version.value = transaction.version;
    form.elements.kind.value = transaction.kind;
    form.elements.amount.value = (Number(transaction.amount_minor) / 100).toFixed(2);
    form.elements.occurred_on.value = String(transaction.occurred_on || '').slice(0, 10);
    form.elements.category_id.value = transaction.category_id || '';
    form.elements.estimated.checked = Boolean(transaction.estimated);
    form.elements.confirmation_status.value = transaction.confirmation_status || 'confirmed';
    form.elements.merchant.value = transaction.merchant || '';
    form.elements.tags.value = Array.isArray(transaction.tags) ? transaction.tags.join(', ') : '';
    form.elements.note.value = transaction.note || '';
    form.dataset.original = JSON.stringify(transaction);
    setMessage($('#editMessage'), '');
    $('#editDialog').showModal();
    form.elements.amount.focus();
  }

  async function submitEdit(event) {
    event.preventDefault();
    const form = $('#editForm');
    let amount;
    try {
      amount = parseAmount(form.elements.amount.value);
      if (amount <= 0) throw new Error('金额必须大于 0。');
    } catch (error) {
      setMessage($('#editMessage'), errorText(error, '无法读取这笔流水。'), true);
      return;
    }
    const original = JSON.parse(form.dataset.original);
    const patch = {};
    if (amount !== Number(original.amount_minor)) patch.amount_minor = amount;
    if (form.elements.kind.value !== original.kind) patch.kind = form.elements.kind.value;
    const occurredOn = form.elements.occurred_on.value;
    if (occurredOn && occurredOn !== String(original.occurred_on || '').slice(0, 10)) patch.occurred_on = occurredOn;
    const categoryId = form.elements.category_id.value;
    if (categoryId && String(categoryId) !== String(original.category_id || '')) patch.category_id = categoryId;
    if (form.elements.estimated.checked !== Boolean(original.estimated)) patch.estimated = form.elements.estimated.checked;
    const confirmation = form.elements.confirmation_status.value;
    if (confirmation !== (original.confirmation_status || 'confirmed')) patch.confirmation_status = confirmation;
    const merchant = form.elements.merchant.value.trim();
    if (merchant !== (original.merchant || '')) patch.merchant = merchant;
    const tags = form.elements.tags.value.split(/[，,]/).map(value => value.trim()).filter(Boolean);
    if (JSON.stringify(tags) !== JSON.stringify(original.tags || [])) patch.tags = tags;
    const note = form.elements.note.value.trim();
    if (note !== (original.note || '')) patch.note = note;
    if (!Object.keys(patch).length) {
      setMessage($('#editMessage'), '没有需要保存的更改。');
      return;
    }
    form.querySelector('[type="submit"]').disabled = true;
    setMessage($('#editMessage'), '');
    try {
      await callTool('update_transaction', {
        transaction_id: form.elements.transaction_id.value,
        expected_version: Number(form.elements.expected_version.value),
        patch
      }, true);
      $('#editDialog').close();
      showNotice('流水更正已保存。', Boolean(state.lastUndoableRequestId && hasTool('undo_operation')));
      await refreshAfterWrite();
    } catch (error) {
      setMessage($('#editMessage'), errorText(error, '更正未保存。记录可能已被其他操作更新。'), true);
    } finally {
      form.querySelector('[type="submit"]').disabled = false;
    }
  }

  async function undoLastOperation() {
    const targetRequestId = state.lastUndoableRequestId;
    if (!targetRequestId || !hasTool('undo_operation')) return;
    $('#undoButton').disabled = true;
    try {
      await callTool('undo_operation', {target_request_id: targetRequestId}, true);
      state.lastUndoableRequestId = null;
      showNotice('最近一次写入已撤销。');
      await refreshAfterWrite();
    } catch (error) {
      showNotice(errorText(error, '无法撤销这次操作；账本内容未被本页乐观修改。'));
    } finally {
      $('#undoButton').disabled = false;
      $('#undoButton').hidden = true;
    }
  }

  function editById(transactionId) {
    const transaction = state.recent.concat(state.currentPageTransactions || []).find(item => String(item.transaction_id) === String(transactionId));
    if (transaction) {
      openEditDialog(transaction);
      return;
    }
    callTool('get_transaction', {transaction_id: transactionId})
      .then(result => openEditDialog(envelopeData(result.envelope).transaction))
      .catch(error => showNotice(errorText(error, '无法读取这笔流水。')));
  }

  function setupStatus() {
    const hidden = localStorage.getItem('yuxu-status-hidden') === 'true';
    $('#statusDock').hidden = hidden;
    $('#restoreStatusButton').hidden = !hidden;
    $('#mobileRestoreStatusButton').hidden = !hidden;
  }

  async function refreshStatus() {
    const details = $('#statusDetails');
    details.replaceChildren();
    try {
      const envelope = await apiRequest('/api/status');
      const data = envelopeData(envelope);
      const healthy = ['ready', 'running'].includes(data.state);
      const stateLabel = statusLabels[data.state] || '未知状态';
      const fields = [
        ['状态', stateLabel],
        ['版本', data.version || '-'],
        ['连接', '本机回环'],
        ['启动时间', data.started_at || '-'],
        ['存储', '本机 SQLite']
      ];
      for (const field of fields) {
        const item = node('div');
        item.append(node('dt', '', field[0]), node('dd', '', field[1]));
        details.append(item);
      }
      $('#statusTrigger').querySelector('.status-dot').classList.toggle('is-running', healthy);
      $('#statusTrigger').querySelector('.status-dot').classList.toggle('is-error', !healthy);
    } catch {
      const item = node('div');
      item.append(node('dt', '', '状态'), node('dd', '', '未连接'));
      details.append(item);
      $('#statusTrigger').querySelector('.status-dot').classList.remove('is-running');
      $('#statusTrigger').querySelector('.status-dot').classList.add('is-error');
    }
  }

  function runtimeLogRowsFrom(envelope) {
    const data = envelopeData(envelope);
    return data.events || data.entries || data.items || data.records || [];
  }

  function runtimeLogAction(item) {
    const action = item.action_name || item.action || '';
    return actionLabels[action] || '其他操作';
  }

  function runtimeLogStatus(item) {
    const status = item.status || item.outcome;
    return statusLabels[status] || errorMessages[status] || (status ? '未完成' : '未知状态');
  }

  function runtimeLogErrorCode(item) {
    const code = item.error_code || item.security_error_code;
    return code && code !== (item.status || item.outcome) ? errorMessages[code] || '处理未完成' : '';
  }

  async function fetchRuntimeLogRows() {
    return runtimeLogRowsFrom(await apiRequest('/api/runtime-log'));
  }

  function renderStatusRuntimeLog(rows) {
    const target = $('#statusRuntimeLogRows');
    target.replaceChildren();
    const visible = Array.isArray(rows) ? rows.slice(-5).reverse() : [];
    if (!visible.length) {
      target.append(node('li', 'empty-cell', '当前没有运行记录。'));
      return;
    }
    for (const item of visible) {
      const row = node('li');
      const when = item.created_at || item.at || item.time || '时间未记录';
      const time = node('time', '', when);
      row.append(time, node('span', '', sourceLabels[item.source] || '未知来源'));
      row.append(node('strong', '', runtimeLogAction(item)));
      row.append(node('span', '', runtimeLogStatus(item)));
      const meta = [runtimeLogErrorCode(item), item.request_id ? '请求 ' + String(item.request_id).slice(0, 12) : '无请求编号'].filter(Boolean).join(' · ');
      row.append(node('span', 'status-log-meta', meta));
      target.append(row);
    }
  }

  async function refreshStatusRuntimeLog() {
    const button = $('#refreshStatusRuntimeLogButton');
    button.disabled = true;
    setMessage($('#statusRuntimeLogMessage'), '正在读取脱敏运行记录…');
    try {
      renderStatusRuntimeLog(await fetchRuntimeLogRows());
      setMessage($('#statusRuntimeLogMessage'), '只显示本机操作元数据，不含金额、参数、备注、令牌或路径。');
    } catch {
      renderStatusRuntimeLog([]);
      setMessage($('#statusRuntimeLogMessage'), '无法读取脱敏运行记录。', true);
    } finally {
      button.disabled = false;
    }
  }

  async function refreshRuntimeLog() {
    const button = $('#refreshRuntimeLogButton');
    const rowsTarget = $('#runtimeLogRows');
    button.disabled = true;
    setMessage($('#runtimeLogMessage'), '正在读取脱敏运行记录…');
    try {
      const rows = await fetchRuntimeLogRows();
      rowsTarget.replaceChildren();
      if (!Array.isArray(rows) || !rows.length) {
        const row = node('tr');
        const cell = node('td', 'empty-cell', '当前没有运行记录。');
        cell.colSpan = 5;
        row.append(cell);
        rowsTarget.append(row);
      } else {
        for (const item of rows) {
          const row = document.createElement('tr');
          const requestId = item.request_id ? String(item.request_id).slice(0, 12) : '无请求编号';
          row.append(node('td', '', item.created_at || item.at || item.time || '时间未记录'));
          row.append(node('td', '', sourceLabels[item.source] || '未知来源'));
          row.append(node('td', '', runtimeLogAction(item)));
          row.append(node('td', '', runtimeLogStatus(item) + (runtimeLogErrorCode(item) ? ' · ' + runtimeLogErrorCode(item) : '')));
          row.append(node('td', '', requestId));
          rowsTarget.append(row);
        }
      }
      setMessage($('#runtimeLogMessage'), '只显示操作元数据，不包含请求内容或账本明细。');
    } catch (error) {
      const row = node('tr');
      const cell = node('td', 'error-inline', errorText(error, '无法读取运行记录。'));
      cell.colSpan = 5;
      row.append(cell);
      rowsTarget.replaceChildren(row);
      setMessage($('#runtimeLogMessage'), errorText(error, '无法读取运行记录。'), true);
    } finally {
      button.disabled = false;
    }
  }

  function openStatusPanel() {
    $('#statusPanel').hidden = false;
    $('#statusTrigger').setAttribute('aria-expanded', 'true');
    refreshStatus();
    refreshStatusRuntimeLog();
  }

  function closeStatusPanel() {
    $('#statusPanel').hidden = true;
    $('#statusTrigger').setAttribute('aria-expanded', 'false');
  }

  function hideStatusPanel() {
    localStorage.setItem('yuxu-status-hidden', 'true');
    $('#statusDock').hidden = true;
    $('#restoreStatusButton').hidden = false;
    $('#mobileRestoreStatusButton').hidden = false;
  }

  function restoreStatusPanel() {
    localStorage.removeItem('yuxu-status-hidden');
    $('#statusDock').hidden = false;
    $('#restoreStatusButton').hidden = true;
    $('#mobileRestoreStatusButton').hidden = true;
    closeStatusPanel();
  }

  async function signOut() {
    $('#logoutButton').disabled = true;
    try {
      await apiRequest('/api/logout', {method: 'POST', body: {}, csrf: state.session.csrf_token});
      state.session = null;
      state.tools = new Set();
      state.toolScopes = new Map();
      state.categories = [];
      state.accounts = [];
      state.targetAccountId = null;
      state.targetAccount = null;
      state.toolCatalog = [];
      state.periodVersion++;
      state.period = null;
      state.selectedPeriodPreset = null;
      state.selectedCategoryId = null;
      state.insightViews = [];
      state.csvTemplates = [];
      state.selectedCsvTemplateId = null;
      state.paymentAccounts = [];
      state.backupSchedule = null;
      state.draftCopyContext = null;
      state.draftCopyNeedsFreshFields = false;
      state.draftCommitContext = null;
      state.pendingReviewRef = null;
      state.reviewLinkNotice = null;
      state.annualCalendar = null;
      state.selectedCalendarDate = null;
      state.calendarVersion++;
      $('#annualCalendarPanel').open = false;
      clearQuickReport();
      state.entityOptions = {};
      state.pendingRequests.clear();
      state.pendingPreview = null;
      state.activeFeatureAction = null;
      state.activeFeatureParams = null;
      state.featureGroupForAction = null;
      if ($('#featureDialog').open) $('#featureDialog').close();
      $('#featureFields').replaceChildren();
      $('#featurePreview').replaceChildren();
      for (const result of $$('.feature-result')) {
        result.replaceChildren();
        result.hidden = true;
      }
      state.attachmentTransactions = [];
      resetLocalFilePanels();
      clearOneTimeHostToken();
      $('#appShell').hidden = true;
      $('#statusDock').hidden = true;
      $('#loginScreen').hidden = false;
      $('#password').value = '';
      $('#reviewLinkPanel').hidden = true;
      $('#reviewLinkDetails').replaceChildren();
      $('#reviewLinkLoginNote').hidden = true;
      setMessage($('#loginMessage'), '已退出登录。');
      location.hash = '';
    } catch (error) {
      showNotice(errorText(error, '退出登录未完成。'));
    } finally {
      $('#logoutButton').disabled = false;
    }
  }

  async function onLogin(event) {
    event.preventDefault();
    const form = $('#loginForm');
    const button = form.querySelector('[type="submit"]');
    const password = $('#password');
    button.disabled = true;
    setMessage($('#loginMessage'), '正在验证本机账号…');
    try {
      const envelope = await apiRequest('/api/login', {
        method: 'POST',
        body: {username: $('#username').value.trim(), password: password.value}
      });
      password.value = '';
      const session = sessionFrom(envelope);
      if (!session.authenticated || !session.csrf_token) throw new Error('登录响应缺少有效会话信息。');
      setMessage($('#loginMessage'), '');
      showApp(session);
    } catch (error) {
      if (error.code === 'setup_required') $('#openRegistration').hidden = false;
      const message = errorText(error, '无法登录。请检查用户名和密码。');
      setMessage($('#loginMessage'), message, true);
    } finally {
      button.disabled = false;
    }
  }

  function closeRegistration() {
    $('#registrationForm').reset();
    setMessage($('#registrationMessage'), '');
    $('#registrationDialog').close();
  }

  async function onRegistration(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    const message = $('#registrationMessage');
    const accounts = [1, 2].map(index => ({
      username: fields.get('username' + index).trim(),
      account_name: fields.get('accountName' + index).trim(),
      password: fields.get('password' + index)
    }));
    const mismatch = accounts.findIndex((account, index) => account.password !== fields.get('repeatPassword' + (index + 1)));
    if (mismatch !== -1) {
      setMessage(message, '使用者 ' + (mismatch + 1) + ' 的两次密码不一致。', true);
      form.elements.namedItem('repeatPassword' + (mismatch + 1)).focus();
      return;
    }
    const submit = form.querySelector('[type="submit"]');
    const admin = fields.get('adminUser');
    submit.disabled = true;
    setMessage(message, '正在创建本机账号…');
    try {
      await apiRequest('/api/register', {
        method: 'POST',
        body: {
          accounts,
          admin_username: admin === '' ? null : accounts[Number(admin)].username,
          maintenance_username: accounts[Number(fields.get('maintenanceUser'))].username
        }
      });
      closeRegistration();
      $('#openRegistration').hidden = true;
      $('#username').value = accounts[0].username;
      $('#password').value = '';
      $('#password').focus();
      setMessage($('#loginMessage'), '注册完成。请用你的本机账号登录。');
    } catch (error) {
      if (error.code === 'already_initialized') {
        closeRegistration();
        $('#openRegistration').hidden = true;
        setMessage($('#loginMessage'), errorText(error));
        $('#username').focus();
      } else {
        setMessage(message, errorText(error, '注册未完成，请检查填写内容。'), true);
      }
    } finally {
      submit.disabled = false;
    }
  }

  async function boot() {
    captureReviewRefFromHash();
    const range = currentMonthRange();
    $('#periodStart').value = range.start_date;
    $('#periodEnd').value = range.end_date;
    $('#annualCalendarYear').value = String(new Date().getFullYear());
    $('#filterForm [name="start_date"]').value = range.start_date;
    $('#filterForm [name="end_date"]').value = range.end_date;
    setupStatus();
    setFilterDisclosure(true);
    try {
      const envelope = await apiRequest('/api/session');
      const session = sessionFrom(envelope);
      $('#openRegistration').hidden = session.setup_required !== true;
      if (session.authenticated) {
        if (!session.csrf_token) throw new Error('当前会话校验信息不完整，请重新登录。');
        showApp(session);
      }
    } catch (error) {
      setMessage($('#loginMessage'), errorText(error, '本机服务尚未连接。启动服务后可在此登录。'), true);
    }
  }

  $('#loginForm').addEventListener('submit', onLogin);
  $('#openRegistration').addEventListener('click', () => $('#registrationDialog').showModal());
  $('#registrationForm').addEventListener('submit', onRegistration);
  for (const button of $$('[data-close-registration]')) button.addEventListener('click', closeRegistration);
  $('#registrationDialog').addEventListener('cancel', event => {
    event.preventDefault();
    closeRegistration();
  });
  $('#logoutButton').addEventListener('click', signOut);
  $('#mobileLogoutButton').addEventListener('click', signOut);
  $('#periodForm').addEventListener('submit', event => {
    event.preventDefault();
    const period = currentPeriod();
    if (!validatePeriod(period)) {
      setMessage($('#periodMessage'), '请选择有效的开始与结束日期。', true);
      return;
    }
    $('#filterForm [name="start_date"]').value = period.start_date;
    $('#filterForm [name="end_date"]').value = period.end_date;
    $('#filterDateBasis').value = period.date_basis;
    loadPeriodData();
  });
  $('#periodPresets').addEventListener('click', event => {
    const button = event.target.closest('button[data-period-preset]');
    if (!button) return;
    const name = button.dataset.periodPreset;
    const range = periodPresets()[name];
    state.selectedPeriodPreset = name;
    $('#periodStart').value = range.start_date;
    $('#periodEnd').value = range.end_date;
    $('#periodForm').requestSubmit();
  });
  $('#categoryDisplay').addEventListener('click', event => {
    const button = event.target.closest('button[data-category-display]');
    if (!button) return;
    $('#categoryPanel').dataset.categoryDisplay = button.dataset.categoryDisplay;
    for (const option of $$('#categoryDisplay button')) {
      option.setAttribute('aria-pressed', String(option === button));
    }
  });
  $('#quickReportChoices').addEventListener('click', event => {
    const button = event.target.closest('button[data-quick-report]');
    if (!button) return;
    const mode = button.dataset.quickReport;
    if (mode !== state.quickReportMode || !state.quickReportResponse) loadQuickReport(mode);
  });
  $('#quickCompareMode').addEventListener('change', () => {
    updateQuickReportControls();
    if (state.quickReportMode === 'compare') loadQuickReport('compare');
  });
  $('#applyQuickCompareRange').addEventListener('click', () => loadQuickReport('compare'));
  $('#reportCategoryFilter').addEventListener('change', event => setSelectedCategory(event.target.value || null));
  $('#clearCategorySelection').addEventListener('click', () => {
    $('#reportCategoryFilter').focus();
    setSelectedCategory(null);
  });
  $('#openCategoryTransactions').addEventListener('click', () => {
    if (!state.period || !state.selectedCategoryId || isCrossAccount()) return;
    $('#filterForm [name="start_date"]').value = state.period.start_date;
    $('#filterForm [name="end_date"]').value = state.period.end_date;
    $('#filterDateBasis').value = state.period.date_basis;
    $('#filterForm [name="kind"]').value = 'expense';
    $('#filterCategory').value = state.selectedCategoryId;
    changeView('transactions');
  });
  $('#quickTrendUnit').addEventListener('change', () => {
    if (state.quickReportResponse) renderQuickReport();
  });
  $('#savedInsightViewForm').addEventListener('submit', event => {
    event.preventDefault();
    upsertInsightView($('#insightViewName').value);
  });
  $('#loadAnnualCalendarButton').addEventListener('click', loadAnnualSpendingCalendar);
  $('#annualCalendarPanel').addEventListener('toggle', event => {
    if (event.target.open && !state.annualCalendar) loadAnnualSpendingCalendar();
  });
  $('#calendarFullDetails').addEventListener('click', openCalendarDateInTransactions);
  $('#filterForm').addEventListener('submit', async event => {
    event.preventDefault();
    await searchTransactions();
    setFilterDisclosure(true);
  });
  $('#filterForm').addEventListener('reset', () => {
    window.setTimeout(() => {
      const range = currentMonthRange();
      $('#filterForm [name="start_date"]').value = range.start_date;
      $('#filterForm [name="end_date"]').value = range.end_date;
      $('#filterDateBasis').value = 'occurred_on';
      $('#filterForm [name="kind"]').value = '';
      $('#filterForm [name="category_id"]').value = '';
      $('#filterForm [name="keyword"]').value = '';
      $('#filterForm [name="tag"]').value = '';
      $('#filterForm [name="amount_min"]').value = '';
      $('#filterForm [name="amount_max"]').value = '';
      searchTransactions();
      setFilterDisclosure(true);
    }, 0);
  });
  $('#toggleFiltersButton').addEventListener('click', () => setFilterDisclosure(!$('#filterFields').hidden));
  $('#loadMoreButton').addEventListener('click', () => searchTransactions(true));
  $('#addButton').addEventListener('click', openEntryDialog);
  $('#mobileAddButton').addEventListener('click', openEntryDialog);
  $('#listAddButton').addEventListener('click', openEntryDialog);
  $('#addEntryRowButton').addEventListener('click', addEntryRow);
  $('#featureForm').addEventListener('submit', submitFeatureForm);
  $('#csvImportFile').addEventListener('change', () => {
    resetImportWorkflow();
    state.selectedCsvTemplateId = null;
    $('#csvTemplateSelect').value = '';
    const file = $('#csvImportFile').files[0];
    setMessage($('#csvImportMessage'), file ? '已选择 ' + file.name + '。读取表头后可映射字段。' : '');
  });
  $('#csvReadColumnsButton').addEventListener('click', readCsvColumns);
  $('#csvImportMapping').addEventListener('change', invalidateCsvPreviews);
  for (const selector of ['#csvDateFormat', '#csvAmountUnit', '#csvDuplicatePolicy', '#csvReconcileStartDate', '#csvReconcileEndDate', '#csvReconcilePaymentAccount']) {
    $(selector).addEventListener('change', invalidateCsvPreviews);
  }
  $('#csvPreviewButton').addEventListener('click', previewCsvImport);
  $('#csvImportConfirmButton').addEventListener('click', commitCsvImport);
  $('#csvModeImport').addEventListener('click', () => applyCsvMode('import'));
  $('#csvModeReconcile').addEventListener('click', () => applyCsvMode('reconcile'));
  $('#csvTemplateSelect').addEventListener('change', applySelectedCsvTemplate);
  $('#saveCsvTemplateButton').addEventListener('click', openSaveCsvMappingTemplate);
  $('#deleteCsvTemplateButton').addEventListener('click', deleteSelectedCsvMappingTemplate);
  $('#csvStatementProvider').addEventListener('change', invalidateCsvPreviews);
  $('#csvReconcilePreviewButton').addEventListener('click', previewCsvReconciliation);
  $('#csvReconcileConfirmButton').addEventListener('click', commitCsvReconciliation);
  $('#paymentBalanceCheckForm').addEventListener('submit', recordPaymentAccountBalanceCheck);
  $('#balanceCheckAccount').addEventListener('change', loadPaymentAccountBalanceCheck);
  $('#refreshBalanceCheckButton').addEventListener('click', loadPaymentAccountBalanceCheck);
  $('#backupScheduleForm').addEventListener('submit', saveBackupSchedule);
  $('#refreshBackupScheduleButton').addEventListener('click', () => refreshBackupSchedule());
  $('#runBackupDrillButton').addEventListener('click', () => {
    const tool = state.toolCatalog.find(item => item.name === 'backup_drill');
    if (tool) openFeatureAction(tool, 'maintenance');
  });
  $('#uploadBackupZipButton').addEventListener('click', uploadBackupZip);
  $('#createHostTokenButton').addEventListener('click', () => {
    const tool = state.toolCatalog.find(item => item.name === 'create_host_token');
    if (tool) openFeatureAction(tool, 'security');
  });
  $('#copyHostTokenButton').addEventListener('click', async () => {
    const value = $('#oneTimeHostTokenValue');
    if (!value.value) return;
    try {
      await navigator.clipboard.writeText(value.value);
      setMessage($('#oneTimeHostTokenMessage'), '密钥已复制到剪贴板。请立即粘贴到目标主机。');
    } catch {
      value.focus();
      value.select();
      setMessage($('#oneTimeHostTokenMessage'), '无法直接访问剪贴板。密钥已选中，请使用 Ctrl+C 复制。', true);
    }
  });
  $('#loadAttachmentTransactions').addEventListener('click', loadAttachmentTransactions);
  $('#attachmentTransaction').addEventListener('change', () => {
    $('#attachmentList').replaceChildren();
    $('#attachmentList').hidden = true;
    clearAttachmentPreview();
    setMessage($('#attachmentMessage'), '');
    updateAttachmentButtons();
  });
  $('#attachmentFile').addEventListener('change', updateAttachmentButtons);
  $('#attachFileButton').addEventListener('click', attachSelectedFile);
  $('#listTransactionFilesButton').addEventListener('click', listTransactionAttachments);
  $('#refreshRuntimeLogButton').addEventListener('click', refreshRuntimeLog);
  $('#entryForm').addEventListener('submit', submitEntries);
  $('#editForm').addEventListener('submit', submitEdit);
  $('#undoButton').addEventListener('click', undoLastOperation);
  $('#dismissNotice').addEventListener('click', () => { $('#notice').hidden = true; });
  $('#openNavigation').addEventListener('click', () => $('#navigationDialog').showModal());
  $('#closeNavigation').addEventListener('click', () => $('#navigationDialog').close());
  $('#statusTrigger').addEventListener('click', () => {
    if ($('#statusPanel').hidden) openStatusPanel();
    else closeStatusPanel();
  });
  $('#closeStatusPanel').addEventListener('click', closeStatusPanel);
  $('#refreshStatusRuntimeLogButton').addEventListener('click', refreshStatusRuntimeLog);
  $('#hideStatusButton').addEventListener('click', hideStatusPanel);
  $('#statusTrigger').addEventListener('contextmenu', event => {
    event.preventDefault();
    hideStatusPanel();
  });
  $('#statusDefaultSelect').addEventListener('change', event => localStorage.setItem('yuxu-status-default', event.target.value));
  $('#accountPicker').addEventListener('change', event => switchAccount(event.target.value));
  $('#mobileAccountPicker').addEventListener('change', event => {
    switchAccount(event.target.value).then(() => $('#navigationDialog').close());
  });
  $('#restoreStatusButton').addEventListener('click', restoreStatusPanel);
  $('#mobileRestoreStatusButton').addEventListener('click', () => {
    restoreStatusPanel();
    $('#navigationDialog').close();
  });
  for (const button of $$('[data-view]')) button.addEventListener('click', () => changeView(button.dataset.view, button.closest('dialog') !== null));
  for (const button of $$('[data-open-view]')) button.addEventListener('click', () => changeView(button.dataset.openView));
  for (const button of $$('[data-close-dialog]')) button.addEventListener('click', () => $('#' + button.dataset.closeDialog).close());
  $('#entryRows').addEventListener('click', event => {
    const button = event.target.closest('.remove-entry');
    if (!button) return;
    if ($('#entryRows').children.length === 1) {
      const row = button.closest('.entry-row');
      $$('input, textarea', row).forEach(input => { input.value = ''; if (input.type === 'checkbox') input.checked = false; });
      $('select[data-field="kind"]', row).value = 'expense';
      $('select[data-field="confirmation_status"]', row).value = 'confirmed';
      $('select[data-field="category_id"]', row).value = '';
      return;
    }
    button.closest('.entry-row').remove();
  });
  document.body.addEventListener('click', event => {
    const otherToggle = event.target.closest('[data-other-toggle]');
    if (otherToggle) {
      const list = $('#' + otherToggle.getAttribute('aria-controls'));
      list.hidden = !list.hidden;
      otherToggle.setAttribute('aria-expanded', String(!list.hidden));
      otherToggle.textContent = list.hidden ? otherToggle.dataset.collapsedText : otherToggle.dataset.expandedText;
      return;
    }
    const categoryButton = event.target.closest('button[data-category-id]');
    if (categoryButton && (categoryButton.closest('#categoryComposition') || categoryButton.closest('#categoryRows'))) {
      $('#reportCategoryFilter').focus();
      setSelectedCategory(String(categoryButton.dataset.categoryId) === String(state.selectedCategoryId) ? null : categoryButton.dataset.categoryId);
      return;
    }
    const editButton = event.target.closest('[data-edit-id]');
    if (editButton) editById(editButton.dataset.editId);
    const copyDraftButton = event.target.closest('[data-copy-draft-id]');
    if (copyDraftButton) copyTransactionToDraft(copyDraftButton.dataset.copyDraftId, copyDraftButton);
  });
  const localBookkeeping = window.YuxuBookkeeping.mount({
    select: $, node, call: callTool, data: envelopeData, readFile: readFileBase64, message: setMessage,
    visible: () => Boolean(state.session && !isCrossAccount() && hasTool('stage_receipt_bookkeeping')),
    id: () => state.session && state.session.account_id, tool: hasTool, categories: () => state.categories, refresh: loadAppData
  });

  window.addEventListener('hashchange', () => {
    if (captureReviewRefFromHash()) {
      if (state.session) resolvePendingReviewLink();
      return;
    }
    const view = location.hash.slice(1);
    if (state.session && view !== state.activeView) changeView(view);
  });
  window.addEventListener('resize', () => setFilterDisclosure($('#filterFields').hidden));
  applyCsvMode('import');
  boot();
})();
