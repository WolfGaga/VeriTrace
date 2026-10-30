const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

const cache = new Map();
function load(file) {
  const absolute = path.resolve(__dirname, '../src/research', file + '.ts');
  if (cache.has(absolute)) return cache.get(absolute);
  const module = { exports: {} };
  const source = ts.transpileModule(fs.readFileSync(absolute, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  new Function('require', 'module', 'exports', source)(name => load(name.replace('./', '')), module, module.exports);
  cache.set(absolute, module.exports);
  return module.exports;
}
const { emptyResearch, researchReducer, taskTree, isResearchEvent } = load('state');
const { researchStatusLabel } = load('state');
const { readSession } = load('history');
const { nodeAnimationPhase } = load('graph-view');
const { mergeReplaceToDiv, replaceStr, safeSourceUrl } = load('../pages/veritrace/utils/tools');
for (const id of [0, 7, 'task-alpha', 'entity-2', '任务']) {
  const phase = nodeAnimationPhase(id);
  assert.ok(Number.isFinite(Math.sin(0.8 + phase)), 'Graph node IDs must produce finite animation coordinates');
  assert.equal(phase, nodeAnimationPhase(id), 'Animation phase must remain stable between renders');
}
assert.equal(nodeAnimationPhase(7), 7, 'Numeric graph IDs retain their phase');
for (const citations of ['[[0]][[1]]', '[[0]] [[1]]', '[[0]]\n[[1]]']) {
  assert.match(mergeReplaceToDiv(citations), /data-ids="0,1"/, 'Grouped citations must retain every source');
}
assert.match(replaceStr('Evidence [[12]]'), />12<\/i>/);
for (const value of ['javascript:alert(1)', 'data:text/html,unsafe', 'file:///private', 'not a URL', null]) {
  assert.equal(safeSourceUrl(value), undefined, 'Source links must not execute code or open local files');
}
assert.equal(safeSourceUrl('https://source.test/a?x=1&y=2#section'), 'https://source.test/a?x=1&y=2#section');
assert.equal(safeSourceUrl('http://source.test/a'), 'http://source.test/a');
const event = (type, data) => ({ schema_version: 2, type, data });
let progressState = { ...emptyResearch('Progress'), status: 'running', tasks: {
  pending: { id: 'pending', question: 'Pending task', status: 'running', result: null },
  failed: { id: 'failed', question: 'Failed task', status: 'failed', result: { answer: '', error: 'offline' } },
} };
progressState = researchReducer(progressState, event('research.progress', {
  stage: 'writing_section', section_index: 2, section_total: 3, title: 'Evidence',
}));
assert.equal(researchStatusLabel(progressState), 'Writing report');
assert.equal(progressState.activity.section_index, 2);
progressState = researchReducer(progressState, event('research.heartbeat', { waiting_seconds: 30 }));
assert.equal(researchStatusLabel(progressState), 'Writing report', 'Heartbeats preserve the active research phase');
assert.equal(progressState.waitingSeconds, 30);
for (const terminal of ['research.completed', 'research.failed', 'research.cancelled']) {
  const stopped = researchReducer(progressState, event(terminal, { article: 'Report', error: 'Failure' }));
  assert.equal(stopped.tasks.pending.status, 'cancelled', 'Terminal states stop unfinished task indicators');
  assert.equal(stopped.tasks.failed.status, 'failed', 'Task failures remain visible');
  assert.equal(researchReducer(stopped, event('research.progress', { stage: 'searching' })), stopped);
  assert.equal(researchReducer(stopped, event('research.heartbeat', { waiting_seconds: 60 })), stopped);
}
const restoredProgress = readSession({ schema_version: 2, rounds: [], current: progressState }).current;
assert.equal(restoredProgress.status, 'cancelled');
assert.equal(restoredProgress.tasks.pending.status, 'cancelled', 'Reloaded sessions cannot retain active task spinners');
const failedTree = taskTree({ ...progressState, tasks: { failed: { ...progressState.tasks.failed, cognitive_target_id: 'entity' } } });
assert.equal(failedTree[0].children[0].state, 2, 'A group of failed tasks must not be shown as complete');
assert.equal(failedTree[0].children[0].children[0].status, 'failed');
const task = { id: 'task-one', question: 'Find evidence', status: 'running', cognitive_target_id: 'e1', result: null };
let state = emptyResearch('Question');
state = researchReducer(state, event('research.started', { question: 'Question', cognitive_graph: { nodes: { e1: { name: 'Entity' } } } }));
state = researchReducer(state, event('tasks.updated', { tasks: [task] }));
const before = state;
state = researchReducer(state, event('task.completed', { task: { ...task, status: 'completed', result: { answer: 'Finding [[0]]', references: { 0: { url: 'https://example.test', title: 'Source' } } } } }));
assert.equal(before.tasks['task-one'].status, 'running', 'Reducer does not mutate earlier snapshots');
state = researchReducer(state, event('research.completed', { article: 'Verified report', ref2url: { 0: { url: 'https://example.test' } } }));
state = researchReducer(state, event('usage.updated', { usage: { tokens: { total: 42 } } }));
assert.equal(state.article, 'Verified report');
assert.equal(state.status, 'completed');
assert.equal(state.usage.tokens.total, 42);
assert.deepEqual(Object.keys(state.tasks), ['task-one'], 'No synthetic task records');
const tree = taskTree(state);
assert.equal(tree[0].children[0].name, 'Entity');
assert.equal(tree[0].children[0].children[0].id, 'task-one');
assert.equal(isResearchEvent({ response: {} }), false);
assert.equal(isResearchEvent(event('research.completed', {})), true);

let running = researchReducer(emptyResearch('Question'), event('tasks.updated', { tasks: [task] }));
running = researchReducer(running, event('research.cancelled', {}));
assert.equal(running.status, 'cancelled');
assert.equal(running.tasks['task-one'].status, 'cancelled');
assert.equal(researchReducer(emptyResearch(), event('research.failed', { error: 'offline' })).error, 'offline');

const legacy = {
  qaList: [{ question: 'Old question', response: 'Old report', nodes: { old: { subQuestion: 'Old task', conclusion: 'Old answer', conclusionRef: { 0: { url: 'https://old.test' } } } } }],
  formatted: { question: 'Old question', response: 'Old report' },
  usageStats: { tokens: { total: 100 } }, stashedNodes: {},
};
const restored = readSession(legacy);
assert.equal(restored.schema_version, 2);
assert.equal(restored.rounds.length, 1, 'Legacy duplicated final response is deduplicated');
assert.equal(restored.current.question, '');
assert.equal(restored.rounds[0].tasks.old.result.answer, 'Old answer');
assert.equal(restored.rounds[0].usage.tokens.total, 100);
assert.equal(readSession({ schema_version: 2, rounds: [], current: { ...state, status: 'running' } }).current.status, 'cancelled');
assert.equal(readSession(null).rounds.length, 0);
const mixedReferences = {
  0: 'https://string.test/source?key=value#section',
  1: { url: 'https://object.test/source', title: 'Object source', snippet: 'Excerpt', publisher: 'Publisher' },
};
const tracedTask = { ...task, result: { answer: 'Mixed references', references: mixedReferences,
  messages: [{ role: 'assistant', content: 'Search trace' }], feedback: { reason: 'Feedback' } } };
const mixed = researchReducer(emptyResearch('Question'), event('research.completed', {
  article: 'Report', ref2url: mixedReferences, tasks: [tracedTask],
}));
assert.equal(mixed.references[0].url, mixedReferences[0]);
assert.deepEqual(mixed.references[1], mixedReferences[1]);
assert.equal(mixed.tasks[task.id].result.references[0].url, mixedReferences[0]);
assert.deepEqual(mixed.tasks[task.id].result.messages, tracedTask.result.messages);
assert.deepEqual(mixed.tasks[task.id].result.feedback, tracedTask.result.feedback);
assert.equal(typeof tracedTask.result.references[0], 'string', 'Input events remain unchanged');
const oldRefs = readSession({ formatted: { question: 'Legacy refs', response: 'Report', responseRefList: mixedReferences },
  stashedNodes: { old: { conclusion: 'Answer', conclusionRef: mixedReferences } } });
assert.equal(oldRefs.current.references[0].url, mixedReferences[0]);
assert.equal(oldRefs.current.tasks.old.result.references[0].url, mixedReferences[0]);
const storedRefs = readSession({ schema_version: 2, rounds: [], current: {
  ...mixed, references: mixedReferences, tasks: { [task.id]: tracedTask },
} });
assert.equal(storedRefs.current.references[0].url, mixedReferences[0]);
assert.equal(storedRefs.current.tasks[task.id].result.references[0].url, mixedReferences[0]);
const { findingViewFields } = load('graph-view');
const tagged = {
  item_findings: { Item: { Attribute: { value: 'Historical item finding', task: 'task-original' } } },
  cross_item_findings: { Ranked: { value: ['First', { value: 'Second', task: 'task-second' }], task: 'task-original' } },
  unexpected_discoveries: [{ value: 'Historical unexpected finding', task: 'task-original' }],
  findings: { Legacy: { value: 'Legacy finding', task: 'task-original' } },
};
const taggedBefore = JSON.stringify(tagged);
const graphView = findingViewFields(tagged);
assert.equal(graphView.item_findings.Item.Attribute, 'Historical item finding');
assert.deepEqual(graphView.cross_item_findings.Ranked, ['First', 'Second']);
assert.deepEqual(graphView.unexpected_discoveries, ['Historical unexpected finding']);
assert.equal(graphView.findings.Legacy, 'Legacy finding');
assert.equal(JSON.stringify(tagged), taggedBefore, 'View conversion preserves raw attribution');
const flat = { item_findings: { Item: { Attribute: 'Flat' } }, cross_item_findings: { Aspect: ['A', 'B'] }, unexpected_discoveries: ['Unexpected'] };
assert.deepEqual(findingViewFields(flat), { findings: {}, ...flat });
console.log('Native events, historical sessions/findings, and reference normalization passed.');
