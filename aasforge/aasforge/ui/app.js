// AAS Forge web editor: browse shells and submodels, edit values, create submodels from templates,
// check them against templates, inspect history, run queries and follow events.
// All content is inserted with textContent/DOM nodes (never as HTML), and the page runs under a
// script-src 'self' CSP. Credentials stay in sessionStorage and are sent as headers only.
'use strict';

const API = '/api/v3.1';
const state = { view: 'shells', selected: null, templates: null, eventsSince: 0, eventsTimer: null };

// -- helpers -----------------------------------------------------------------------------------
function b64url(text) {
  const bytes = new TextEncoder().encode(text);
  let binary = '';
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key === 'text') node.textContent = value;
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) {
    if (child === undefined || child === null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function clear(node) { while (node.firstChild) node.firstChild.remove(); return node; }

function toast(message, error = false) {
  const box = el('div', { class: error ? 'error' : '' }, message);
  document.getElementById('toast').append(box);
  setTimeout(() => box.remove(), error ? 8000 : 3500);
}

function credential() {
  try { return sessionStorage.getItem('aasforge.credential') || ''; } catch (e) { return ''; }
}

function authHeaders() {
  const secret = credential();
  if (!secret) return {};
  return secret.split('.').length === 3 ? { Authorization: `Bearer ${secret}` } : { 'X-API-Key': secret };
}

async function api(method, path, body, { raw = false, headers = {} } = {}) {
  const options = { method, headers: { ...authHeaders(), ...headers } };
  if (body !== undefined) {
    if (body instanceof FormData) options.body = body;
    else { options.body = JSON.stringify(body); options.headers['Content-Type'] = 'application/json'; }
  }
  const response = await fetch(path.startsWith('/') ? path : `${API}/${path}`, options);
  if (!response.ok) {
    let text = `HTTP ${response.status}`;
    try {
      const result = await response.json();
      if (result.messages && result.messages.length) text = result.messages.map(m => m.text).join('; ');
    } catch (e) { /* not a Result */ }
    const error = new Error(text);
    error.status = response.status;
    throw error;
  }
  if (raw) return response;
  if (response.status === 204) return null;
  const type = response.headers.get('Content-Type') || '';
  return type.includes('json') ? response.json() : response.text();
}

function langText(list) {
  if (!Array.isArray(list) || !list.length) return '';
  const en = list.find(item => (item.language || '').startsWith('en'));
  return (en || list[0]).text || '';
}

function firstKey(reference) {
  const keys = (reference && reference.keys) || [];
  return keys.length ? keys[0].value : '';
}

function label(element) {
  return langText(element.displayName) || element.idShort || '(item)';
}

function cardinality(element) {
  const q = (element.qualifiers || []).find(q => q.type === 'SMT/Cardinality' || q.type === 'Cardinality'
    || q.type === 'Multiplicity');
  return q ? q.value : '';
}

function failed(error) { toast(error.message, true); }

// -- navigation --------------------------------------------------------------------------------
function show(view) {
  state.view = view;
  for (const button of document.querySelectorAll('#tabs button')) {
    button.classList.toggle('active', button.dataset.view === view);
  }
  if (state.eventsTimer) { clearInterval(state.eventsTimer); state.eventsTimer = null; }
  clear(document.getElementById('detail')).append(el('p', { class: 'muted' }, 'Select an item.'));
  ({ shells: listShells, submodels: listSubmodels, templates: listTemplates, query: queryView, events: eventsView })[view]()
    .catch(failed);
}

function listPane(title, items, render, open) {
  const pane = clear(document.getElementById('list'));
  pane.append(el('h2', {}, title, ' ', el('span', { class: 'badge' }, String(items.length))));
  const filter = el('input', { type: 'search', placeholder: 'Filter', 'aria-label': 'Filter' });
  const list = el('ul', { class: 'items' });
  const draw = () => {
    clear(list);
    const needle = filter.value.toLowerCase();
    for (const item of items) {
      const [main, sub] = render(item);
      if (needle && !`${main} ${sub}`.toLowerCase().includes(needle)) continue;
      const li = el('li', { tabindex: '0' }, main, el('span', { class: 'sub' }, sub));
      const choose = () => {
        for (const other of list.children) other.classList.remove('selected');
        li.classList.add('selected');
        open(item).catch(failed);
      };
      li.addEventListener('click', choose);
      li.addEventListener('keydown', e => { if (e.key === 'Enter') choose(); });
      list.append(li);
    }
  };
  filter.addEventListener('input', draw);
  pane.append(filter, list);
  draw();
}

async function fetchAll(path) {
  const items = [];
  let cursor = '';
  for (let page = 0; page < 20; page++) {
    const data = await api('GET', `${path}${path.includes('?') ? '&' : '?'}limit=100${cursor ? `&cursor=${cursor}` : ''}`);
    items.push(...(data.result || []));
    cursor = data.paging_metadata && data.paging_metadata.cursor;
    if (!cursor) break;
  }
  return items;
}

// -- shells ---------------------------------------------------------------------------------------
async function listShells() {
  const shells = await fetchAll('shells');
  listPane('Shells', shells, s => [s.idShort || s.id, s.id], openShell);
}

async function openShell(shell) {
  const detail = clear(document.getElementById('detail'));
  const info = shell.assetInformation || {};
  detail.append(el('h2', {}, shell.idShort || shell.id), el('p', { class: 'mono' }, shell.id));
  detail.append(el('dl', { class: 'kv' },
    el('dt', {}, 'Asset kind'), el('dd', {}, info.assetKind || ''),
    el('dt', {}, 'Global asset id'), el('dd', { class: 'mono' }, info.globalAssetId || ''),
    ...(info.specificAssetIds || []).flatMap(a => [el('dt', {}, a.name), el('dd', { class: 'mono' }, a.value)])));
  const passport = el('a', { href: `/dpp/passports/${b64url(shell.id)}?format=html`, target: '_blank', rel: 'noopener' },
    'Open passport page');
  detail.append(el('div', { class: 'row' }, passport));
  detail.append(el('h3', {}, 'Submodels'));
  const list = el('ul', { class: 'items' });
  for (const ref of shell.submodels || []) {
    const id = ((ref.keys || []).slice(-1)[0] || {}).value;
    const li = el('li', { tabindex: '0' }, id);
    li.addEventListener('click', async () => {
      try { openSubmodel(await api('GET', `submodels/${b64url(id)}`)); } catch (e) { failed(e); }
    });
    list.append(li);
  }
  detail.append(list);
}

// -- submodels and the element editor -------------------------------------------------------------
async function listSubmodels() {
  const submodels = await fetchAll('submodels/$metadata');
  listPane('Submodels', submodels, s => [`${s.idShort || s.id}${s.kind === 'Template' ? ' (template)' : ''}`,
    firstKey(s.semanticId) || s.id], async s => openSubmodel(await api('GET', `submodels/${b64url(s.id)}`)));
}

function walk(children, prefix, visit, list = false) {
  (children || []).forEach((child, index) => {
    const path = list ? `${prefix}[${index}]` : (prefix ? `${prefix}.${child.idShort}` : child.idShort);
    visit(path, child);
    const kids = child.modelType === 'SubmodelElementCollection' ? child.value
      : child.modelType === 'SubmodelElementList' ? child.value
        : child.modelType === 'Entity' ? child.statements
          : child.modelType === 'AnnotatedRelationshipElement' ? child.annotations : null;
    if (kids) walk(kids, path, visit, child.modelType === 'SubmodelElementList');
  });
}

function shortValue(element) {
  if (element.modelType === 'Property') return element.value ?? '';
  if (element.modelType === 'MultiLanguageProperty') return langText(element.value);
  if (element.modelType === 'Range') return `${element.min ?? ''} … ${element.max ?? ''}`;
  if (element.modelType === 'File') return element.value || '';
  return '';
}

function openSubmodel(submodel) {
  state.selected = submodel;
  const detail = clear(document.getElementById('detail'));
  const b64 = b64url(submodel.id);
  detail.append(el('h2', {}, submodel.idShort || submodel.id), el('p', { class: 'mono' }, submodel.id));
  const actions = el('div', { class: 'row' });
  const editor = el('div');
  const tree = el('ul', { class: 'tree' });
  const extra = el('div');
  actions.append(
    el('button', { onclick: () => history(submodel, extra) }, 'History'),
    el('button', { onclick: () => checkTemplate(submodel, extra) }, 'Check against template'),
    el('button', { onclick: () => download(`submodels/${b64}/$signed`, `${submodel.idShort || 'submodel'}.jws`) },
      'Signed export'),
    el('button', { onclick: () => download(`submodels/${b64}/$value`, `${submodel.idShort || 'submodel'}.json`) },
      'Values (JSON)'));
  detail.append(actions, el('div', { class: 'split' }, el('div', {}, tree), el('div', {}, editor)), extra);

  const nodes = new Map();
  const roots = [];
  walk(submodel.submodelElements, '', (path, element) => nodes.set(path, element));
  const children = new Map();
  for (const path of nodes.keys()) {
    const cut = Math.max(path.lastIndexOf('.'), path.lastIndexOf('['));
    const parent = cut > 0 ? path.slice(0, cut) : '';
    if (!parent) roots.push(path);
    else (children.get(parent) || children.set(parent, []).get(parent)).push(path);
  }
  const draw = (paths, container) => {
    for (const path of paths) {
      const element = nodes.get(path);
      const kids = children.get(path) || [];
      const node = el('span', { class: 'node', tabindex: '0' },
        kids.length ? '▸' : '·', label(element), el('span', { class: 'badge' }, element.modelType),
        shortValue(element) !== '' ? el('span', { class: 'value' }, String(shortValue(element)).slice(0, 60)) : null);
      const li = el('li', {}, node);
      const sub = el('ul', { class: 'tree', hidden: true });
      if (kids.length) { draw(kids, sub); li.append(sub); }
      node.addEventListener('click', () => {
        for (const other of tree.querySelectorAll('.node.selected')) other.classList.remove('selected');
        node.classList.add('selected');
        if (kids.length) { sub.hidden = !sub.hidden; node.firstChild.textContent = sub.hidden ? '▸' : '▾'; }
        editElement(submodel, path, element, editor);
      });
      container.append(li);
    }
  };
  draw(roots, tree);
  if (!roots.length) tree.append(el('li', { class: 'muted' }, 'No elements.'));
}

async function download(path, name) {
  try {
    const response = await api('GET', path, undefined, { raw: true });
    const url = URL.createObjectURL(await response.blob());
    const link = el('a', { href: url, download: name });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  } catch (e) { failed(e); }
}

async function conceptName(semanticId) {
  if (!semanticId) return '';
  try {
    const cd = await api('GET', `concept-descriptions/${b64url(semanticId)}`);
    const content = ((cd.embeddedDataSpecifications || [])[0] || {}).dataSpecificationContent || {};
    return [langText(content.preferredName), content.unit ? `[${content.unit}]` : '', langText(content.definition)]
      .filter(Boolean).join(' ');
  } catch (e) { return ''; }
}

function input(valueType, value) {
  const type = (valueType || '').replace('xs:', '');
  if (type === 'boolean') {
    const box = el('input', { type: 'checkbox' });
    box.checked = String(value).toLowerCase() === 'true';
    box.read = () => box.checked;
    return box;
  }
  const numeric = ['decimal', 'integer', 'double', 'float', 'int', 'long', 'short', 'byte', 'nonNegativeInteger',
    'positiveInteger', 'unsignedInt', 'unsignedLong', 'unsignedShort', 'unsignedByte', 'negativeInteger',
    'nonPositiveInteger'].includes(type);
  const field = el('input', { type: numeric ? 'number' : type === 'date' ? 'date' : 'text', step: numeric ? 'any' : null });
  field.value = value ?? '';
  field.read = () => {
    if (field.value === '') return null;
    if (numeric) return Number(field.value);
    return field.value;
  };
  return field;
}

async function editElement(submodel, path, element, editor) {
  clear(editor);
  const b64 = b64url(submodel.id);
  const base = `submodels/${b64}/submodel-elements/${encodeURIComponent(path)}`;
  editor.append(el('h3', {}, label(element)), el('p', { class: 'mono' }, path));
  const facts = el('dl', { class: 'kv' },
    el('dt', {}, 'Type'), el('dd', {}, element.modelType + (element.valueType ? ` (${element.valueType})` : '')),
    el('dt', {}, 'Semantic id'), el('dd', { class: 'mono' }, firstKey(element.semanticId) || '—'),
    cardinality(element) ? el('dt', {}, 'Cardinality') : null, cardinality(element) ? el('dd', {}, cardinality(element)) : null);
  editor.append(facts);
  const description = langText(element.description);
  if (description) editor.append(el('p', { class: 'muted' }, description));
  conceptName(firstKey(element.semanticId)).then(text => { if (text) editor.append(el('p', { class: 'muted' }, `Concept: ${text}`)); });

  const save = async value => {
    try {
      await api('PATCH', `${base}/$value`, value);
      toast('Saved');
      openSubmodel(await api('GET', `submodels/${b64}`));
    } catch (e) { failed(e); }
  };
  if (element.modelType === 'Property') {
    const field = input(element.valueType, element.value);
    editor.append(el('div', { class: 'field' }, el('label', {}, 'Value'), field),
      el('div', { class: 'row' }, el('button', { class: 'primary', onclick: () => save(field.read()) }, 'Save')));
  } else if (element.modelType === 'MultiLanguageProperty') {
    const rows = el('div');
    const addRow = (language = '', text = '') => {
      const lang = el('input', { size: 4, value: language, 'aria-label': 'Language' });
      const value = el('input', { value: text, 'aria-label': 'Text' });
      rows.append(el('div', { class: 'row' }, lang, value));
    };
    for (const item of element.value || []) addRow(item.language, item.text);
    if (!(element.value || []).length) addRow('en', '');
    editor.append(rows, el('div', { class: 'row' },
      el('button', { onclick: () => addRow() }, 'Add language'),
      el('button', { class: 'primary', onclick: () => save([...rows.children].map(r => {
        const [lang, text] = r.querySelectorAll('input');
        return lang.value ? { [lang.value]: text.value } : null;
      }).filter(Boolean)) }, 'Save')));
  } else if (element.modelType === 'Range') {
    const min = input(element.valueType, element.min);
    const max = input(element.valueType, element.max);
    editor.append(el('div', { class: 'field' }, el('label', {}, 'Min'), min), el('div', { class: 'field' }, el('label', {}, 'Max'), max),
      el('div', { class: 'row' }, el('button', { class: 'primary', onclick: () => save({ min: min.read(), max: max.read() }) }, 'Save')));
  } else if (element.modelType === 'File') {
    const file = el('input', { type: 'file', 'aria-label': 'File' });
    editor.append(el('div', { class: 'row' },
      el('button', { onclick: () => download(`${base}/attachment`, (element.value || 'file').split('/').pop()) }, 'Download'),
      file, el('button', { class: 'primary', onclick: async () => {
        if (!file.files.length) return;
        const form = new FormData();
        form.append('file', file.files[0]);
        form.append('fileName', file.files[0].name);
        try { await api('PUT', `${base}/attachment`, form); toast('Uploaded'); openSubmodel(await api('GET', `submodels/${b64}`)); }
        catch (e) { failed(e); }
      } }, 'Upload')));
  } else if (element.modelType === 'Operation') {
    const args = el('textarea', { 'aria-label': 'Input arguments (ValueOnly JSON)' });
    args.value = JSON.stringify(Object.fromEntries((element.inputVariables || []).map(v => [v.value.idShort, null])), null, 1);
    const out = el('pre');
    editor.append(el('div', { class: 'field' }, el('label', {}, 'Input arguments'), args), el('div', { class: 'row' },
      el('button', { class: 'primary', onclick: async () => {
        try { out.textContent = JSON.stringify(await api('POST', `${base}/invoke/$value`, { inputArguments: JSON.parse(args.value) }), null, 1); }
        catch (e) { failed(e); }
      } }, 'Invoke')), out);
  }
  editor.append(el('div', { class: 'row' }, el('button', { class: 'danger', onclick: async () => {
    if (!confirm(`Delete ${path}?`)) return;
    try { await api('DELETE', base); toast('Deleted'); openSubmodel(await api('GET', `submodels/${b64}`)); } catch (e) { failed(e); }
  } }, 'Delete element')));
}

async function history(submodel, target) {
  clear(target).append(el('h3', {}, 'History'));
  try {
    const audit = await api('GET', `submodels/${b64url(submodel.id)}/$audit`);
    const list = el('ul', { class: 'items' });
    for (const revision of audit.revisions.slice().reverse()) {
      const li = el('li', {}, `Revision ${revision.revision}`,
        el('span', { class: 'sub' }, `${revision.valid_from}${revision.actor ? ` by ${revision.actor}` : ''}${revision.deleted ? ' (deleted)' : ''}`));
      li.addEventListener('click', async () => {
        try {
          const old = await api('GET', `submodels/${b64url(submodel.id)}/$history?date=${encodeURIComponent(revision.valid_from)}`);
          const pre = el('pre', {}, JSON.stringify(old, null, 1).slice(0, 20000));
          li.after(pre);
        } catch (e) { failed(e); }
      });
      list.append(li);
    }
    target.append(list);
  } catch (e) { failed(e); }
}

async function templates() {
  if (!state.templates) state.templates = (await api('GET', 'templates')).result;
  return state.templates;
}

async function checkTemplate(submodel, target) {
  clear(target).append(el('h3', {}, 'Template check'));
  const semantic = firstKey(submodel.semanticId);
  const match = (await templates()).find(t => t.semanticId === semantic && t.ref !== submodel.id);
  if (!match) { target.append(el('p', { class: 'muted' }, 'No template with this semanticId.')); return; }
  const result = await api('POST', 'templates/check', { template: match.ref, submodel: submodel.id });
  target.append(el('p', { class: 'muted' }, `Template: ${match.idShort} (${match.ref})`));
  if (!result.findings.length) target.append(el('p', { class: 'ok' }, 'No findings.'));
  for (const f of result.findings) {
    target.append(el('div', { class: `finding ${f.severity}` }, `${f.severity}: ${f.path} — ${f.text}`));
  }
}

// -- templates and the generated form ---------------------------------------------------------------
async function listTemplates() {
  listPane('Templates', await templates(), t => [t.idShort || t.ref, `${t.source}: ${t.semanticId || t.ref}`], openTemplate);
}

async function openTemplate(entry) {
  const detail = clear(document.getElementById('detail'));
  const template = entry.ref.startsWith('idta:')
    ? (await api('GET', `/dpp/templates/${encodeURIComponent(entry.ref.slice(5))}`))
    : await api('GET', `submodels/${b64url(entry.ref)}`);
  detail.append(el('h2', {}, `New submodel from ${entry.idShort || entry.ref}`), el('p', { class: 'mono' }, entry.ref));
  const id = el('input', { placeholder: 'urn:example:submodel:1', 'aria-label': 'Submodel id' });
  const idShort = el('input', { placeholder: template.idShort || '', 'aria-label': 'idShort' });
  detail.append(el('div', { class: 'field' }, el('label', {}, 'Id'), id), el('div', { class: 'field' }, el('label', {}, 'idShort'), idShort));
  const fields = [];
  const form = el('div');
  const build = (children, prefix, container, inList) => {
    (children || []).forEach((child, index) => {
      const path = inList ? `${prefix}[0]` : (prefix ? `${prefix}.${child.idShort}` : child.idShort);
      const card = cardinality(child);
      const title = el('label', {}, label(child), ' ', card ? el('span', { class: 'badge' }, card) : null);
      if (child.modelType === 'Property' || child.modelType === 'MultiLanguageProperty') {
        const example = (child.qualifiers || []).find(q => q.type === 'SMT/ExampleValue');
        const field = child.modelType === 'Property' ? input(child.valueType, '') : el('input', {});
        if (example) field.placeholder = example.value;
        if (child.modelType === 'MultiLanguageProperty') field.read = () => field.value ? [{ en: field.value }] : null;
        fields.push([path, field]);
        container.append(el('div', { class: 'field' }, title, field));
      } else if (['SubmodelElementCollection', 'SubmodelElementList', 'Entity'].includes(child.modelType)) {
        const section = el('div', { class: 'form-section' }, el('h3', {}, label(child), card ? ` (${card})` : ''));
        container.append(section);
        build(child.modelType === 'Entity' ? child.statements : child.value, path, section,
          child.modelType === 'SubmodelElementList');
      }
    });
  };
  build(template.submodelElements, '', form, false);
  const result = el('div');
  detail.append(form, el('div', { class: 'row' },
    el('button', { onclick: () => create(false) }, 'Preview and check'),
    el('button', { class: 'primary', onclick: () => create(true) }, 'Create submodel')), result);

  async function create(store) {
    const values = {};
    for (const [path, field] of fields) {
      const value = field.read();
      if (value !== null && value !== '') values[path] = value;
    }
    if (!id.value) { toast('Enter an id', true); return; }
    try {
      const response = await api('POST', 'templates/instantiate', { template: entry.ref, id: id.value,
        idShort: idShort.value || undefined, values, store });
      clear(result);
      result.append(el('h3', {}, store ? 'Created' : 'Preview'));
      const errors = response.findings.filter(f => f.severity === 'error');
      result.append(el('p', { class: errors.length ? 'err' : 'ok' },
        `${errors.length} error(s), ${response.findings.length - errors.length} warning(s)`));
      for (const f of response.findings.slice(0, 50)) {
        result.append(el('div', { class: `finding ${f.severity}` }, `${f.path}: ${f.text}`));
      }
      if (store) {
        toast('Submodel created');
        result.append(el('button', { onclick: () => openSubmodel(response.submodel) }, 'Open submodel'));
      }
    } catch (e) { failed(e); }
  }
}

// -- query console ------------------------------------------------------------------------------------
async function queryView() {
  const pane = clear(document.getElementById('list'));
  const kind = el('select', { 'aria-label': 'Collection' },
    ...['submodels', 'shells', 'concept-descriptions', 'shell-descriptors', 'submodel-descriptors']
      .map(k => el('option', { value: k }, k)));
  const text = el('textarea', { 'aria-label': 'Query (IDTA-01002 JSON)' });
  text.value = JSON.stringify({ $condition: { $eq: [{ $field: '$sm#idShort' }, { $strVal: 'Nameplate' }] }, $select: 'id' }, null, 1);
  const output = clear(document.getElementById('detail'));
  output.append(el('p', { class: 'muted' }, 'Results appear here.'));
  pane.append(el('h2', {}, 'Query'), el('div', { class: 'field' }, el('label', {}, 'Collection'), kind),
    el('div', { class: 'field' }, el('label', {}, 'AAS Query Language (JSON)'), text),
    el('div', { class: 'row' }, el('button', { class: 'primary', onclick: async () => {
      try {
        const result = await api('POST', `query/${kind.value}`, JSON.parse(text.value));
        clear(output).append(el('h2', {}, `${result.result.length} result(s)`), el('pre', {}, JSON.stringify(result, null, 1).slice(0, 100000)));
      } catch (e) { failed(e); }
    } }, 'Run')));
}

// -- events ----------------------------------------------------------------------------------------------
async function eventsView() {
  const pane = clear(document.getElementById('list'));
  const list = el('ul', { class: 'items' });
  pane.append(el('h2', {}, 'Events'), el('p', { class: 'muted' }, 'Changes as they happen (polled every 3 s).'), list);
  const poll = async () => {
    try {
      const data = await api('GET', `events?since=${state.eventsSince}`);
      for (const event of data.events) {
        state.eventsSince = Math.max(state.eventsSince, event.seq);
        list.prepend(el('li', {}, `${event.op} ${event.kind}`, el('span', { class: 'sub' }, `${event.ts} ${event.id}${event.path ? ` · ${event.path}` : ''}`)));
      }
    } catch (e) { failed(e); }
  };
  await poll();
  state.eventsTimer = setInterval(poll, 3000);
}

// -- start -------------------------------------------------------------------------------------------------
function refreshWho() {
  const signedIn = Boolean(credential());
  document.getElementById('who').textContent = signedIn ? 'signed in' : 'anonymous';
  document.getElementById('logout').hidden = !signedIn;
}

document.addEventListener('DOMContentLoaded', () => {
  for (const button of document.querySelectorAll('#tabs button')) {
    button.addEventListener('click', () => show(button.dataset.view));
  }
  document.getElementById('login').addEventListener('submit', event => {
    event.preventDefault();
    const field = document.getElementById('credential');
    try { sessionStorage.setItem('aasforge.credential', field.value.trim()); } catch (e) { /* private mode */ }
    field.value = '';
    state.templates = null;
    refreshWho();
    show(state.view);
  });
  document.getElementById('logout').addEventListener('click', () => {
    try { sessionStorage.removeItem('aasforge.credential'); } catch (e) { /* ignore */ }
    state.templates = null;
    refreshWho();
    show(state.view);
  });
  refreshWho();
  show('shells');
});
