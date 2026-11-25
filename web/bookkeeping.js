(() => {
  'use strict';

  const operations = {
    expense: '支出', income: '收入', transfer: '账户转账', credit_card_repayment: '信用卡还款',
    refund: '退款到账', reimbursement_receipt: '报销到账'
  };
  const fields = {
    amount_minor: '金额（元）', occurred_on: '发生日期', charged_on: '扣款日期', operation: '业务类型',
    payment_account_id: '支付账户', category_id: '分类', merchant: '商家', note: '备注',
    account_id: '目标账户', source_account_id: '付款账户', destination_account_id: '转入账户',
    transaction_id: '原流水编号', reimbursement_id: '报销编号', expected_version: '原记录版本',
    provider: '账单来源', external_id: '支付订单号'
  };

  function mount(api) {
    const {select, node, call, data, readFile, message, visible, id, tool, categories, refresh} = api;
    const panel = select('#localBookkeepingPanel');
    const result = select('#localBookkeepingResult');
    const status = select('#localBookkeepingMessage');
    const file = select('#localBookkeepingFile');
    let viewAccount;
    let sequence = 0;

    function button(label, primary = false) {
      const element = node('button', 'button button-' + (primary ? 'primary' : 'secondary'), label);
      element.type = 'button';
      return element;
    }

    async function perform(element, fn, pending) {
      if (!visible()) return;
      element.disabled = true;
      const marker = sequence;
      message(status, pending || '');
      try {
        await fn(() => marker === sequence && visible());
      } catch (error) {
        if (marker === sequence && visible()) message(status, error.message || '操作失败。', true);
      } finally {
        element.disabled = false;
      }
    }

    async function open(draftId, active) {
      const response = data((await call('get_bookkeeping_draft', {draft_id: draftId})).envelope);
      const accounts = tool('list_payment_accounts')
        ? data((await call('list_payment_accounts')).envelope).accounts || [] : [];
      if (active()) renderDraft(response, accounts);
    }

    async function list(active) {
      const response = data((await call('list_bookkeeping_drafts', {status: 'open', limit: 30})).envelope);
      if (!active()) return;
      result.replaceChildren();
      const drafts = response.drafts || [];
      if (!drafts.length) result.append(node('p', 'panel-subtitle', '当前没有待处理草稿。'));
      for (const draft of drafts) {
        const row = node('div', 'file-input-row');
        const openDraft = button('打开草稿');
        openDraft.addEventListener('click', () => perform(openDraft, check => open(draft.draft_id, check), '正在读取草稿…'));
        row.append(node('span', 'panel-subtitle', (draft.has_evidence ? '文件凭证' : '手工草稿') + ' · ' +
          new Date(draft.updated_at).toLocaleString('zh-CN') + ' · ' + draft.draft_id.slice(0, 8)), openDraft);
        result.append(row);
      }
      message(status, response.truncated ? '这里只显示最近 30 条待办。' : '');
    }

    function renderDraft(draft, accounts) {
      result.replaceChildren();
      const heading = node('h3', '', draft.status === 'open' ? '待确认草稿' : '已处理草稿');
      result.append(heading);
      if (draft.status !== 'open') {
        message(status, draft.status === 'committed' ? '此凭证已处理。' : '此草稿已丢弃。');
        return;
      }
      const editors = [];
      const choices = [];
      const filteredAccounts = accounts.filter(item => item.status !== 'archived')
        .map(item => [item.account_id, item.name]);
      const categoryOptions = categories().filter(item => item.active).map(item => [item.category_id, item.path]);
      function editor(line, key, options) {
        const field = node('label', 'schema-field');
        field.append(node('span', 'schema-label', fields[key]));
        const control = node(options ? 'select' : 'input', 'schema-control');
        const prior = line.fields[key];
        if (options) {
          control.append(new Option('未填写', ''));
          for (const [value, label] of options) control.append(new Option(label, value));
        } else if (['occurred_on', 'charged_on'].includes(key)) {
          control.type = 'date';
        } else if (key === 'amount_minor') {
          control.inputMode = 'decimal';
        }
        control.value = prior == null ? '' : key === 'amount_minor' ? (prior / 100).toFixed(2) : String(prior);
        editors.push({line: line.line, key, control, initial: control.value, hadValue: prior != null});
        field.append(control);
        return field;
      }
      for (const line of draft.items) {
        const group = node('fieldset', 'schema-fieldset import-mapping-fields');
        group.append(node('legend', '', '条目 ' + line.line));
        const groupKey = line.fields.operation;
        group.append(editor(line, 'operation', Object.entries(operations)), editor(line, 'amount_minor'), editor(line, 'occurred_on'));
        if (!groupKey || ['income', 'expense'].includes(groupKey)) {
          group.append(editor(line, 'payment_account_id', filteredAccounts), editor(line, 'category_id', categoryOptions),
            editor(line, 'merchant'), editor(line, 'note'), editor(line, 'charged_on'));
        } else {
          for (const key of ({
            transfer: ['source_account_id', 'destination_account_id'],
            credit_card_repayment: ['account_id', 'source_account_id'],
            refund: ['transaction_id', 'expected_version', 'account_id', 'charged_on'],
            reimbursement_receipt: ['reimbursement_id', 'expected_version', 'account_id']
          })[groupKey] || []) group.append(editor(line, key, key.endsWith('account_id') ? filteredAccounts : null));
        }
        if (line.missing_fields.length) {
          group.append(node('p', 'schema-help', '需核对 ' + line.missing_fields.map(key => fields[key] || key).join('、')));
        }
        if (line.known_source) group.append(node('p', 'schema-help', '该订单已有记录，确认时会关联原记录。'));
        const choice = node('label', 'schema-field');
        choice.append(node('span', 'schema-label', '处理方式'));
        const decision = node('select', 'schema-control');
        const candidates = line.duplicate_candidates || [];
        decision.append(new Option(candidates.length || (line.similar_draft_lines || []).length ? '请选择' : '确认后入账', ''));
        decision.append(new Option('另记一笔', 'separate'), new Option('跳过这条', 'skip'));
        for (const candidate of candidates) {
          decision.append(new Option('关联流水 ' + candidate.transaction_id.slice(0, 8), candidate.transaction_id));
        }
        choice.append(decision);
        if (!line.known_source) {
          group.append(choice);
          choices.push({line: line.line, control: decision, candidates});
        }
        result.append(group);
      }
      const actions = node('div', 'dialog-actions');
      const save = button('保存修改');
      const commit = button('确认记账', true);
      const discard = button('丢弃草稿');
      actions.append(discard, save, commit);
      result.append(actions);

      function patches() {
        const changed = new Map();
        for (const {line, key, control, initial, hadValue} of editors) {
          const value = control.value.trim();
          if (control.value === initial) continue;
          if (!value && !hadValue) continue;
          let normalized = value || null;
          if (value && key === 'amount_minor') {
            if (!/^\d+(\.\d{1,2})?$/.test(value)) throw new Error('金额请填写元和最多两位小数。');
            normalized = Math.round(Number(value) * 100);
            if (!Number.isSafeInteger(normalized) || normalized <= 0) throw new Error('金额须为大于零的有效数值。');
          } else if (value && key === 'expected_version') {
            normalized = Number(value);
          }
          const values = changed.get(line) || {};
          values[key] = normalized;
          changed.set(line, values);
        }
        return Array.from(changed, ([line, fields]) => ({line, fields}));
      }

      save.addEventListener('click', () => perform(save, async active => {
        const edits = patches();
        if (!edits.length) { message(status, '草稿内容未变更。'); return; }
        const updated = data((await call('upsert_bookkeeping_draft', {
          draft_id: draft.draft_id, expected_version: draft.version, patches: edits
        }, true)).envelope);
        if (active()) { renderDraft(updated, accounts); message(status, '已保存，请核对更新后的条目。'); }
      }, '正在保存…'));
      commit.addEventListener('click', () => perform(commit, async active => {
        if (patches().length) throw new Error('请先保存修改，再确认最新草稿。');
        const decisions = choices.filter(choice => choice.control.value).map(choice => {
          const value = choice.control.value;
          const candidate = choice.candidates.find(item => item.transaction_id === value);
          return candidate ? {line: choice.line, decision: 'link', transaction_id: value, expected_version: candidate.version}
            : {line: choice.line, decision: value};
        });
        const response = data((await call('commit_bookkeeping_draft', {
          draft_id: draft.draft_id, expected_version: draft.version, confirmed: true, decisions
        }, true)).envelope);
        if (active()) {
          result.replaceChildren();
          message(status, '已入账 ' + (response.transaction_ids.length + response.movement_results.length) + ' 笔，关联 ' +
            response.linked.length + ' 笔。');
          await refresh();
        }
      }, '正在确认入账…'));
      discard.addEventListener('click', () => {
        if (!window.confirm('丢弃此待确认草稿？')) return;
        perform(discard, async active => {
          await call('discard_bookkeeping_draft', {draft_id: draft.draft_id, expected_version: draft.version}, true);
          if (active()) { await list(active); message(status, '已丢弃草稿。'); }
        }, '正在丢弃…');
      });
      message(status, draft.evidence_available
        ? '已保留原始凭证。请核对实付金额、日期以及是否已经到账。' : '请核对金额、日期和业务类型。');
    }

    const stage = select('#localBookkeepingStage');
    stage.addEventListener('click', () => perform(stage, async active => {
      const selected = file.files[0];
      if (!selected) throw new Error('请先选择凭证。');
      const suffix = selected.name.split('.').pop().toLowerCase();
      const type = ({png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp', pdf: 'application/pdf'})[suffix];
      if (!type || selected.size > 10 * 1024 * 1024) throw new Error('请选择 10 MB 以内的 PNG、JPEG、WebP 或 PDF。');
      const draft = data((await call('stage_receipt_bookkeeping', {
        file_name: selected.name, mime_type: type, content_base64: await readFile(selected)
      }, true)).envelope);
      if (active()) await open(draft.draft_id, active);
    }, '正在用 Windows 本机识别凭证…'));
    const listButton = select('#localBookkeepingList');
    listButton.addEventListener('click', () => perform(listButton, list, '正在读取待办…'));

    return {
      update() {
        const identity = id();
        if (identity !== viewAccount) {
          viewAccount = identity;
          sequence++;
              file.value = '';
          result.replaceChildren();
          message(status, '');
        }
        panel.hidden = !visible();
      }
    };
  }

  window.YuxuBookkeeping = {mount};
})();
