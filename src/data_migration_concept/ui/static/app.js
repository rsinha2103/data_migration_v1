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
    SQL: ['', 'SQL expression (alias s = staging row)', '', ''],
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
  document.querySelectorAll('tr.maprow').forEach(row => {
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
    const filter = () => {
      const needle = search.value.trim().toLowerCase();
      document.querySelectorAll('tr.maprow').forEach(row => {
        const show = (!needle || row.dataset.name.includes(needle)
            || row.querySelector('[name$="_target"]').value.toLowerCase().includes(needle))
          && (!onlyUnmapped.checked || row.querySelector('.rule-type').value === 'UNMAPPED')
          && (!onlyMandatory.checked || row.querySelector('[name$="_mandatory"]').checked);
        row.classList.toggle('hidden', !show);
      });
    };
    [search, onlyUnmapped, onlyMandatory].forEach(el => el.addEventListener('input', filter));
  }

  // fill an XREF textarea with the unmapped values found in the data
  const fill = document.getElementById('fill-missing');
  if (fill) fill.addEventListener('click', () => {
    const area = document.getElementById('xref-text');
    const values = JSON.parse(document.getElementById('missing-values').textContent);
    area.value = area.value.replace(/\s*$/, '\n') + values.map(v => v + ',').join('\n') + '\n';
  });
});
