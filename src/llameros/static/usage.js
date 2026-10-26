/**
 * @file Accessible sortable usage tables for the Llameros admin and user dashboards.
 * Receives already-authorized aggregates; sorting is presentation, not access control.
 */
'use strict';
const UsageTable = {
  /**
   * Bind accessible sortable headings and retain sort/scroll state across usage updates.
   *
   * @param {HTMLTableSectionElement} body - Destination tbody in a scroll-wrapped table.
   * @param {Array<{field: string, numeric?: boolean, decimal?: boolean}>} columns - Ordered field definitions.
   * @returns {{update: function(Array<Object>): void}} Table controller accepting fresh scoped rows.
   */
  create(body, columns) {
    const table = body.closest('table'), scroll = table.parentElement;
    let rows = [], field = 'output_tokens', direction = 'descending';
    const headers = [...table.querySelectorAll('th')];
    const buttons = headers.map((header, index) => {
      const label = header.textContent;
      const button = document.createElement('button');
      button.type = 'button'; button.textContent = label; button.title = label;
      button.addEventListener('click', () => {
        const next = columns[index].field;
        direction = next === field && direction === 'ascending' ? 'descending' : 'ascending';
        field = next;
        render();
      });
      header.replaceChildren(button);
      header.scope = 'col';
      return {button, label};
    });
    /**
     * Stably sort the current rows, paint text-only cells, and restore scroll and aria-sort state.
     */
    function render() {
      const top = scroll.scrollTop, left = scroll.scrollLeft;
      const column = columns.find(item => item.field === field);
      const sorted = rows.map((row, index) => ({row, index})).sort((a, b) => {
        const av = a.row[field], bv = b.row[field];
        const compared = column.numeric
          ? (Number(av) || 0) - (Number(bv) || 0)
          : String(av ?? '').localeCompare(String(bv ?? ''), undefined, {numeric: true, sensitivity: 'base'});
        return (direction === 'ascending' ? compared : -compared) || a.index - b.index;
      });
      body.replaceChildren();
      for (const {row} of sorted) {
        const tr = document.createElement('tr');
        for (const col of columns) {
          const td = document.createElement('td'), value = row[col.field];
          td.textContent = col.decimal ? (Number(value) || 0).toFixed(1) : value ?? '';
          td.title = String(td.textContent); tr.append(td);
        }
        body.append(tr);
      }
      headers.forEach((header, index) => {
        const active = columns[index].field === field;
        header.setAttribute('aria-sort', active ? direction : 'none');
        buttons[index].button.textContent = buttons[index].label + (active ? direction === 'ascending' ? ' ▲' : ' ▼' : '');
      });
      scroll.scrollTop = top; scroll.scrollLeft = left;
    }
    return {update(data) { rows = [...data]; render(); }};
  },
  modelColumns: [
    {field: 'model'}, {field: 'requests', numeric: true}, {field: 'prompt_tokens', numeric: true},
    {field: 'output_tokens', numeric: true}, {field: 'tokens_per_second', numeric: true, decimal: true},
    {field: 'seconds', numeric: true, decimal: true},
    {field: 'limits_text'},
  ],
  keyColumns: [
    {field: 'name'}, {field: 'user'}, {field: 'status'}, {field: 'requests', numeric: true}, {field: 'failed', numeric: true},
    {field: 'prompt_tokens', numeric: true}, {field: 'output_tokens', numeric: true},
    {field: 'seconds', numeric: true, decimal: true},
    {field: 'limits_text'}, {field: 'user_limits_text'},
  ],
};
if (typeof module !== 'undefined') module.exports = UsageTable;