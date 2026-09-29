const $ = id => document.getElementById(id);
let current = null;
let currentInputs = null;
let editValue = null;
let testEvidence = null;
let working = false;

async function request(path, body) {
  const response = await fetch(path, body === undefined ? undefined : {
    method: 'POST', credentials: 'same-origin',
    headers: { 'content-type': 'application/json', 'x-sley-request': 'playground' },
    body: JSON.stringify(body),
  });
  const value = await response.json();
  if (!response.ok) throw value;
  return value;
}

function notice(message, kind = 'success') {
  $('notice').textContent = message;
  $('notice').dataset.kind = kind;
}

function short(value) { return value ? `${value.slice(0, 12)}…${value.slice(-8)}` : '—'; }
function elt(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function doWork(action) {
  if (working) return;
  working = true;
  document.body.style.cursor = 'progress';
  try { await action(); }
  catch (error) {
    const detail = error?.detail?.observed_root;
    notice(`${error?.message ?? 'The operation failed.'}${detail ? ` Observed root: ${short(detail)}.` : ''}`,
      error?.kind ?? 'runtime_failure');
    if (error?.kind === 'incomplete_operation') {
      try { current = await request('/api/session'); render(); } catch { /* Keep the uncertain notice. */ }
    }
  } finally {
    working = false;
    document.body.style.cursor = '';
    if (current) updateButtons();
  }
}

function renderPrograms(examples) {
  const nav = $('programs');
  nav.replaceChildren();
  for (const example of examples) {
    const button = elt('button', 'program-choice');
    button.type = 'button';
    button.dataset.example = example.id;
    button.append(elt('strong', '', example.title), elt('span', '', example.description));
    button.addEventListener('click', () => doWork(async () => {
      const next = await request('/api/sessions', { example: example.id });
      current = next;
      currentInputs = structuredClone(next.samples[0].inputs);
      editValue = next.edit?.suggested ?? null;
      testEvidence = null;
      render();
      notice(`Opened ${next.title} at accepted root ${short(next.root)}.`);
    }));
    nav.append(button);
  }
}

function renderInputs() {
  const fields = $('input-fields');
  fields.replaceChildren();
  for (const [name, type, label] of current.fields) {
    const wrapper = elt('div', 'field');
    const title = elt('label', '', label);
    title.htmlFor = `field-${name}`;
    let control;
    if (type === 'Bool') {
      control = elt('select');
      for (const [value, text] of [['true', 'True'], ['false', 'False']]) {
        const option = elt('option', '', text);
        option.value = value;
        control.append(option);
      }
      control.value = String(currentInputs[name]);
    } else {
      control = elt('input');
      control.type = 'text';
      control.inputMode = 'numeric';
      control.autocomplete = 'off';
      control.spellcheck = false;
      control.value = currentInputs[name];
    }
    control.id = `field-${name}`;
    control.dataset.name = name;
    wrapper.append(title, control, elt('small', '', type));
    fields.append(wrapper);
  }
  const samples = $('samples');
  samples.replaceChildren();
  for (const sample of current.samples) {
    const button = elt('button', '', sample.label);
    button.type = 'button';
    button.addEventListener('click', () => {
      currentInputs = structuredClone(sample.inputs);
      renderInputs();
      notice(`Loaded ${sample.label.toLowerCase()} inputs. Run the program to compute a result.`);
    });
    samples.append(button);
  }
}

function collectInputs() {
  const values = {};
  for (const [name, type] of current.fields) {
    const input = $(`field-${name}`);
    values[name] = type === 'Bool' ? input.value === 'true' : input.value.trim();
  }
  currentInputs = values;
  return values;
}

function renderResult() {
  const run = current.last_run;
  $('result-value').textContent = run ? JSON.stringify(run.result, null, 2) : '';
  $('result-root').textContent = run ? `from root ${run.root}` : '';
  $('result-caption').textContent = !run ? 'Run the program to see its computed result.'
    : run.result?.allow === false ? 'Computed successfully. This input does not permit a release.'
      : 'Computed successfully by the accepted Sley program.';
  $('test-result').textContent = testEvidence
    ? `${testEvidence.external_assertions_against_native_execution} published cases passed. Native core test admission: ${testEvidence.native_core_test_admission}.`
    : '';
}

function renderEdit() {
  const wrap = $('edit-controls');
  wrap.replaceChildren();
  const edit = current.edit;
  const available = !!edit;
  $('edit-explanation').textContent = available
    ? current.example === 'release-readiness'
      ? 'Change the accepted limit for blocking findings. A proposal leaves the current root untouched until commit.'
      : 'Change how negative measurements are marked. The decision remains in the native graph.'
    : 'This example is ready to run and inspect. A guided range edit is not available in local mode yet.';
  if (available) {
    const field = elt('div', 'edit-field');
    const label = elt('label', '', edit.label);
    label.htmlFor = 'edit-value';
    let control;
    if (edit.type === 'Bool') {
      control = elt('select');
      for (const [value, text] of [['true', 'True'], ['false', 'False']]) {
        const option = elt('option', '', text);
        option.value = value;
        control.append(option);
      }
      control.value = String(editValue);
    } else {
      control = elt('input');
      control.type = 'text';
      control.inputMode = 'numeric';
      control.value = editValue ?? edit.suggested;
    }
    control.id = 'edit-value';
    field.append(label, control);
    wrap.append(field);
  }
  const pending = current.pending;
  $('candidate-info').textContent = !pending ? ''
    : `Candidate ${pending.candidate_id}\nBase root ${pending.base_root}\n${pending.uncertain ? 'Outcome uncertain; check the verified commit receipt.' : pending.validated ? 'Validated by Sley. Ready to commit.' : 'Proposed. Validate before commit.'}`;
  updateButtons();
}

function detail(title, lines, open = false) {
  const node = elt('details');
  node.open = open;
  node.append(elt('summary', '', title));
  const list = elt('ul');
  for (const line of lines) list.append(elt('li', '', line));
  node.append(list);
  return node;
}

function renderInspector() {
  const box = $('inspector');
  box.replaceChildren();
  box.append(detail('Inputs', current.fields.map(([name, type]) => `${name} · ${type}`), true));
  const constants = current.authoring?.constants?.map(item =>
    `${item.name} = ${JSON.stringify(item.value)} · ${item.value_type}`) ??
    [`max_blocking_findings = ${current.threshold} · UInt32`];
  box.append(detail('Accepted constants', constants, true));
  box.append(detail('Result fields', current.result_fields.map(item =>
    `${item.name} · ${item.type}`)));
  if (current.graph) {
    box.append(detail('Blocks and operations', current.graph.blocks.flatMap(block => [
      `${block.name}:`, ...block.operations.map(operation =>
        `  ${operation.name} = ${operation.op}`),
      `  ${block.terminator.kind}`,
    ])));
  }
}

function renderReview() {
  const review = current.review;
  $('review-panel').hidden = !review;
  if (!review) return;
  $('review-meta').textContent = `Root ${review.before_root} → ${review.after_root}\nCandidate ${review.candidate_id}\nReceipt ${review.receipt_id}`;
  $('review-before').textContent = JSON.stringify(review.before, null, 2);
  $('review-after').textContent = JSON.stringify(review.after, null, 2);
}

function updateButtons() {
  if (!current) return;
  const pending = current.pending;
  $('propose-button').disabled = working || !current.edit || !!pending;
  $('validate-button').disabled = working || !pending || pending.validated || pending.uncertain;
  $('commit-button').disabled = working || !pending?.validated || pending.uncertain;
  $('reconcile-button').disabled = working || !pending?.uncertain;
  $('discard-button').disabled = working || !pending || pending.uncertain;
  $('run-button').disabled = working || !!pending?.uncertain || !!current.state_unavailable;
  $('test-button').disabled = working || !!pending?.uncertain || !!current.state_unavailable;
  $('export-button').disabled = working || !!pending?.uncertain || !!current.state_unavailable;
  $('copy-root').disabled = working || !!current.state_unavailable;
}

function render() {
  if (!current) return;
  $('welcome').hidden = true;
  $('workspace').hidden = false;
  for (const button of document.querySelectorAll('.program-choice'))
    button.setAttribute('aria-current', String(button.dataset.example === current.example));
  $('program-title').textContent = current.title;
  $('program-description').textContent = current.description;
  $('root-label').textContent = current.state_unavailable ? 'Last recorded root (inspection unavailable)'
    : current.pending?.uncertain ? 'Observed root (commit uncertain)' : 'Accepted root';
  $('state-root').textContent = current.root;
  $('state-root').title = current.root;
  if (!currentInputs) currentInputs = structuredClone(current.samples[0].inputs);
  renderInputs(); renderResult(); renderEdit(); renderInspector(); renderReview();
}

$('input-form').addEventListener('submit', event => {
  event.preventDefault();
  doWork(async () => {
    const result = await request('/api/run', { inputs: collectInputs() });
    current = result.state;
    render();
    notice(result.native.result?.allow === false
      ? 'Computed successfully: release not allowed for these inputs.'
      : `Computed successfully at root ${short(result.native.state_root)}.`);
  });
});
$('test-button').addEventListener('click', () => doWork(async () => {
  const result = await request('/api/test', {});
  current = result.state;
  testEvidence = result.test;
  render();
  notice(`${result.test.external_assertions_against_native_execution} published cases passed against native execution.`);
}));
$('propose-button').addEventListener('click', () => doWork(async () => {
  const input = $('edit-value');
  editValue = current.edit.type === 'Bool' ? input.value === 'true' : input.value.trim();
  const result = await request('/api/propose', { value: editValue });
  current = result.state;
  render();
  notice(`Candidate ${short(result.proposal.candidate_id)} proposed. Accepted root is unchanged.`);
}));
$('validate-button').addEventListener('click', () => doWork(async () => {
  const result = await request('/api/validate', {});
  current = result.state;
  render();
  notice(`Sley validated candidate ${short(result.validation.candidate_id)}. Commit it to change the accepted program.`);
}));
$('commit-button').addEventListener('click', () => doWork(async () => {
  const result = await request('/api/commit', {});
  if (result.state) current = result.state;
  else { current.root = result.root; current.pending = null; current.review = result.review;
    current.state_unavailable = true; }
  render();
  notice(result.postcommit_error
    ? `Committed at root ${short(result.root)}. The follow-up run needs inspection: ${result.postcommit_error}`
    : `${result.recovered_from_verified_receipt ? 'Verified committed native change from the Sley receipt' : 'Committed native change'}. Accepted root is now ${short(current.root)}.`,
  result.postcommit_error ? 'incomplete_operation' : 'success');
}));
$('reconcile-button').addEventListener('click', () => doWork(async () => {
  const result = await request('/api/reconcile', {});
  if (result.status === 'committed') {
    if (result.state) current = result.state;
    else { current.root = result.root; current.pending = null; current.review = result.review;
      current.state_unavailable = true; }
    render();
    notice(`Verified committed native change from the Sley receipt at root ${short(result.root)}.`);
  } else {
    if (result.state) current = result.state;
    else current.state_unavailable = true;
    render();
    notice(`Commit outcome is still unknown. Observed root: ${short(result.reconciliation.observed_root)}. Do not retry the commit.`, 'incomplete_operation');
  }
}));
$('discard-button').addEventListener('click', () => doWork(async () => {
  const result = await request('/api/discard', {});
  current = result.state;
  render();
  notice('Proposal discarded. The accepted program is unchanged.');
}));
$('copy-root').addEventListener('click', () => doWork(async () => {
  await navigator.clipboard.writeText(current.root);
  notice('Copied the exact accepted root.');
}));
$('export-button').addEventListener('click', () => doWork(async () => {
  const response = await fetch('/api/export', {
    method: 'POST', credentials: 'same-origin',
    headers: { 'content-type': 'application/json', 'x-sley-request': 'playground' },
    body: '{}',
  });
  if (!response.ok) throw await response.json();
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = elt('a');
  anchor.href = url;
  anchor.download = `sley-${current.example}.tar.gz`;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
  current = await request('/api/session');
  render();
  notice('Exported the accepted artifact, manifest, recipe, and cases.');
}));

try {
  const listing = await request('/api/examples');
  renderPrograms(listing.examples);
  try {
    const resumed = await request('/api/session');
    if (resumed.active !== false) {
      current = resumed;
      currentInputs = structuredClone(current.last_run?.inputs ?? current.samples[0].inputs);
      editValue = current.edit?.suggested ?? null;
      render();
      if (current.pending?.uncertain)
        notice('The previous commit outcome is uncertain. Check the verified receipt before making another change.', 'incomplete_operation');
      else if (current.state_unavailable)
        notice('A commit was verified, but the local project needs inspection before further actions.', 'incomplete_operation');
    }
  } catch { /* A fresh visitor chooses a program. */ }
} catch (error) {
  $('welcome').querySelector('p').textContent = error.message ?? 'The local Playground service is unavailable.';
}
