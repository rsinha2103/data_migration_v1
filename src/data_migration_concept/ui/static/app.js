// Small helpers; every page still works as plain forms without them.
document.addEventListener('DOMContentLoaded', () => {
  // confirm destructive actions
  document.querySelectorAll('form[data-confirm]').forEach(f =>
    f.addEventListener('submit', e => { if (!confirm(f.dataset.confirm)) e.preventDefault(); }));

  // disable buttons while a long step runs
  document.querySelectorAll('form[data-busy]').forEach(f =>
    f.addEventListener('submit', () => {
      f.querySelectorAll('button').forEach(b => { b.disabled = true; b.textContent = 'Running...'; });
    }));

  // forms that show different fields depending on a <select data-switch>
  document.querySelectorAll('select[data-switch]').forEach(sel => {
    const scope = sel.closest('form');
    const apply = () => scope.querySelectorAll('[data-for]').forEach(block => {
      const on = block.dataset.for.split(' ').includes(sel.value);
      block.classList.toggle('hidden', !on);
      block.querySelectorAll('input,select,textarea').forEach(i => { i.disabled = !on; });
    });
    sel.addEventListener('change', apply);
    apply();
  });

  // mapping editor: show only the inputs a rule type needs, and rename their labels
  const LABELS = {
    UNMAPPED: ['', '', '', ''],
    MOVE: ['Source column', '', '', ''],
    CONSTANT: ['', 'Fixed value', '', ''],
    XREF: ['Source column', 'XREF name', 'If no entry', 'Default value'],
    CONCAT: ['Source columns (comma separated)', 'Separator', '', ''],
    SUBSTRING: ['Source column', 'Start (1 = first)', 'Length', ''],
    NOT_MIGRATED: ['', '', '', ''],
    SQL: ['', 'SQL expression (s = this row; other sources: {{ stg.name }})', '', ''],
  };
  const refresh = row => {
    const type = row.querySelector('.rule-type').value;
    const labels = LABELS[type] || ['', '', '', ''];
    ['src', 'a1', 'a2', 'a3'].forEach((key, i) => {
      const cell = row.querySelector('.p-' + key);
      cell.classList.toggle('hidden', labels[i] === '');
      const hint = cell.querySelector('.hint');
      if (hint) hint.textContent = labels[i];
      cell.querySelectorAll('input,select').forEach(el => { el.disabled = labels[i] === ''; });
    });
    const unmapped = row.querySelector('.p-a2 select');
    const dflt = row.querySelector('.p-a3');
    if (type === 'XREF' && unmapped && unmapped.value !== 'default') {
      dflt.classList.add('hidden');
      dflt.querySelectorAll('input').forEach(el => { el.disabled = true; });
    }
  };
  document.querySelectorAll('tr.maprow, tr.ovrow').forEach(row => {
    refresh(row);
    row.querySelector('.rule-type').addEventListener('change', () => refresh(row));
    const unmapped = row.querySelector('.p-a2 select');
    if (unmapped) unmapped.addEventListener('change', () => refresh(row));
  });

  // add a blank mapping row by cloning the template row
  const addBtn = document.getElementById('add-row');
  if (addBtn) addBtn.addEventListener('click', () => {
    const tbody = document.getElementById('maprows');
    const tpl = document.getElementById('row-template').innerHTML;
    const index = tbody.querySelectorAll('tr.maprow').length + 1000 + Date.now() % 1000;
    tbody.insertAdjacentHTML('beforeend', tpl.replaceAll('__i__', index));
    const row = tbody.lastElementChild;
    refresh(row);
    row.querySelector('.rule-type').addEventListener('change', () => refresh(row));
    const unmapped = row.querySelector('.p-a2 select');
    if (unmapped) unmapped.addEventListener('change', () => refresh(row));
  });

  // proposals: select every proposal of a group
  document.querySelectorAll('.select-group').forEach(box => box.addEventListener('change', () => {
    let el = box.closest('label').nextElementSibling;
    while (el && el.tagName !== 'H2') {
      el.querySelectorAll('input[name="ids"]').forEach(c => { c.checked = box.checked; });
      el = el.nextElementSibling;
    }
  }));

  // mapping editor: filter rows by name / not mapped / mandatory
  const search = document.getElementById('map-search');
  if (search) {
    const onlyUnmapped = document.getElementById('only-unmapped');
    const onlyMandatory = document.getElementById('only-mandatory');
    const onlyReview = document.getElementById('only-review');
    const rows = () => Array.from(document.querySelectorAll('tr.maprow'));
    const filter = () => {
      const needle = search.value.trim().toLowerCase();
      rows().forEach(row => {
        const review = row.querySelector('.review').value || 'open';
        const show = (!needle || row.dataset.name.includes(needle)
            || row.querySelector('[name$="_target"]').value.toLowerCase().includes(needle))
          && (!onlyUnmapped.checked || row.querySelector('.rule-type').value === 'UNMAPPED')
          && (!onlyMandatory.checked || row.querySelector('[name$="_mandatory"]').checked)
          && (onlyReview.value === '*' || onlyReview.value === review);
        row.classList.toggle('hidden', !show);
      });
    };
    [search, onlyUnmapped, onlyMandatory, onlyReview].forEach(el => el.addEventListener('input', filter));

    // sort the rows (the save order follows the form order, so sorting also reorders the file)
    const sorter = document.getElementById('map-sort');
    sorter.addEventListener('change', () => {
      const body = document.getElementById('maprows');
      const key = row => sorter.value === 'name'
        ? row.querySelector('[name$="_target"]').value.toLowerCase()
        : sorter.value === 'status'
          ? (row.querySelector('.rule-type').value === 'UNMAPPED' ? '0' : '1')
            + (row.querySelector('[name$="_mandatory"]').checked ? '0' : '1')
            + String(row.dataset.order).padStart(6, '0')
          : String(row.dataset.order).padStart(6, '0');
      rows().sort((a, b) => key(a).localeCompare(key(b))).forEach(r => body.appendChild(r));
    });

    // bulk tools: act on the ticked rows; nothing is saved until "Save mapping"
    const picked = () => rows().filter(r => r.querySelector('.pick').checked);
    const count = () => { document.getElementById('picked').textContent = picked().length; };
    document.getElementById('maprows').addEventListener('change', e => {
      if (e.target.classList.contains('pick')) count();
    });
    document.getElementById('pick-all').addEventListener('change', e => {
      rows().filter(r => !r.classList.contains('hidden'))
        .forEach(r => { r.querySelector('.pick').checked = e.target.checked; });
      count();
    });
    const setRule = (row, type, vals) => {
      const sel = row.querySelector('.rule-type');
      sel.value = type;
      sel.dispatchEvent(new Event('change'));
      ['src', 'a1', 'a2', 'a3'].forEach((k, i) => {
        const el = row.querySelector('.p-' + k + ' input, .p-' + k + ' select');
        if (el && vals[i] !== undefined && vals[i] !== '') el.value = vals[i];
      });
    };
    const need = () => {
      const chosen = picked();
      if (!chosen.length) alert('Tick the fields to change first.');
      return chosen;
    };
    document.getElementById('bulk-apply').addEventListener('click', () => {
      const vals = ['bulk-src', 'bulk-a1', 'bulk-a2', 'bulk-a3'].map(id => document.getElementById(id).value);
      need().forEach(r => setRule(r, document.getElementById('bulk-rule').value, vals));
    });
    document.getElementById('bulk-copy').addEventListener('click', () => {
      const chosen = need();
      if (chosen.length < 2) { if (chosen.length) alert('Tick at least two fields: the first one is copied to the others.'); return; }
      const first = chosen[0];
      const val = k => { const el = first.querySelector('.p-' + k + ' input, .p-' + k + ' select'); return el ? el.value : ''; };
      const vals = ['src', 'a1', 'a2', 'a3'].map(val);
      chosen.slice(1).forEach(r => setRule(r, first.querySelector('.rule-type').value, vals));
    });
    document.getElementById('bulk-mark').addEventListener('click', () => {
      const v = document.getElementById('bulk-review').value;
      need().forEach(r => { r.querySelector('.review').value = v; });
    });
  }

  // fill an XREF textarea with the unmapped values found in the data
  const fill = document.getElementById('fill-missing');
  if (fill) fill.addEventListener('click', () => {
    const area = document.getElementById('xref-text');
    const values = JSON.parse(document.getElementById('missing-values').textContent);
    area.value = area.value.replace(/\s*$/, '\n') + values.map(v => v + ',').join('\n') + '\n';
  });
});
