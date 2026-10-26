/** Shared token-allowance editor. Server authorization and accounting are authoritative. */
'use strict';
const TokenLimits = {
  draft: {input_k: null, output_k: null, period: 'never'},
  reset() {
    this.draft = {input_k: null, output_k: null, period: 'never'};
    document.getElementById('limits-dialog')?.remove();
  },
  summary(value) {
    if (!value) return 'No limit';
    const parts = ['input', 'output'].map(kind => {
      const cap = value[kind + '_k'];
      if (cap == null) return `${kind}: no limit`;
      if (value.policy_only) return `${kind}: ${cap}k per user`;
      const used = (value[kind + '_used'] || 0) / 1000;
      const remaining = value[kind + '_remaining'];
      return `${kind}: ${used}k / ${cap}k${value[kind + '_unknown'] ? ' (usage unknown; blocked)' : remaining == null ? '' : `; ${remaining / 1000}k left`}`;
    });
    return (value.per_user && !value.policy_only ? `Your model allowance (${value.user}) · ` : value.inherited ? 'Default user limits · ' : '') + parts.join(' · ') + ` · ${value.period}` + (value.resets_at ? ` · resets ${new Date(value.resets_at * 1000).toISOString()}` : '');
  },
  button(scope, subject, title) {
    const button = document.createElement('button');
    button.type = 'button'; button.textContent = 'Config'; button.setAttribute('aria-label', `Configure limits for ${title}`);
    button.addEventListener('click', safe(async () => {
      const epoch = sessionEpoch;
      const url = `/api/limits/${scope}?subject=${encodeURIComponent(subject)}`;
      const value = await api(url);
      if (epoch !== sessionEpoch || !csrf) return;
      const refreshLimits = async () => {
        await refreshKeys(); await refresh();
        if (!userBoard) await refreshUsers();
      };
      this.edit(title, value, async config => {
        if (epoch !== sessionEpoch || !csrf) return;
        await api(url, config); await refreshLimits();
      }, scope === 'user' ? async () => {
        if (epoch !== sessionEpoch || !csrf) return;
        await api(url, {}, 'DELETE'); await refreshLimits();
      } : null);
    }));
    return button;
  },
  edit(title, value, save, inherit = null) {
    document.getElementById('limits-dialog')?.remove();
    const dialog = document.createElement('dialog'); dialog.id = 'limits-dialog';
    dialog.setAttribute('aria-labelledby', 'limits-title');
    const heading = document.createElement('h2'); heading.id = 'limits-title'; heading.textContent = `Usage limits — ${title}`;
    const form = document.createElement('form');
    const help = document.createElement('p');
    help.textContent = '1 unit = 1,000 tokens. UTC calendar resets. All user, model and key limits apply. In-flight requests may exceed allowances. Saving does not reset usage.';
    if (value.per_user) help.textContent += ' Model limits apply separately to each user, across all of their keys; users do not share a pool.';
    const controls = {};
    for (const kind of ['input', 'output']) {
      const label = document.createElement('label'); label.textContent = `${kind === 'input' ? 'Input' : 'Output'} (thousand tokens)`;
      const amount = document.createElement('input'); amount.type = 'number'; amount.min = '1'; amount.max = '1000000000'; amount.step = '1'; amount.required = true; amount.value = value[kind + '_k'] ?? 1;
      const unlimitedLabel = document.createElement('label'); unlimitedLabel.className = 'checkbox-label';
      const unlimited = document.createElement('input'); unlimited.type = 'checkbox'; unlimited.checked = value[kind + '_k'] == null;
      amount.disabled = unlimited.checked;
      unlimited.addEventListener('change', () => { amount.disabled = unlimited.checked; });
      unlimitedLabel.append(unlimited, document.createTextNode(' No limit'));
      label.append(amount); form.append(label, unlimitedLabel); controls[kind] = {amount, unlimited};
    }
    const periodLabel = document.createElement('label'); periodLabel.textContent = 'Reset timeframe';
    const period = document.createElement('select');
    for (const [id, label] of [['day', '1 day'], ['week', '1 week'], ['month', '1 month'], ['never', 'Never']]) period.add(new Option(label, id));
    period.value = value.period || 'never'; periodLabel.append(period);
    const usage = document.createElement('p'); usage.textContent = this.summary(value);
    const submit = document.createElement('button'); submit.type = 'submit'; submit.textContent = 'Save limits';
    const cancel = document.createElement('button'); cancel.type = 'button'; cancel.textContent = 'Cancel'; cancel.addEventListener('click', () => dialog.close());
    form.append(periodLabel, usage, submit, cancel);
    if (inherit) {
      const defaults = document.createElement('button'); defaults.type = 'button'; defaults.textContent = 'Use defaults';
      defaults.disabled = value.inherited === true;
      defaults.addEventListener('click', safe(async () => {
        defaults.disabled = true;
        try { await inherit(); dialog.close(); } finally { defaults.disabled = value.inherited === true; }
      }));
      form.append(defaults);
      help.textContent += ' Saving creates a user override; Use defaults restores automatic inheritance.';
    }
    form.addEventListener('submit', safe(async event => {
      event.preventDefault();
      const config = {period: period.value};
      for (const kind of ['input', 'output']) config[kind + '_k'] = controls[kind].unlimited.checked ? null : Number(controls[kind].amount.value);
      submit.disabled = true;
      try { await save(config); dialog.close(); } finally { submit.disabled = false; }
    }));
    dialog.append(heading, help, form); document.body.append(dialog);
    dialog.addEventListener('close', () => dialog.remove()); dialog.showModal();
  },
};
if (typeof module !== 'undefined') module.exports = TokenLimits;
if (typeof document !== 'undefined') document.addEventListener('DOMContentLoaded', () => {
  const defaults = document.getElementById('default-user-limits');
  const button = TokenLimits.button('user-default', '*', 'default user limits');
  button.textContent = 'Default user limits'; defaults.append(button);
  document.getElementById('key-limits-config').addEventListener('click', () => {
    TokenLimits.edit('new key', TokenLimits.draft, async config => { TokenLimits.draft = config; });
  });
});