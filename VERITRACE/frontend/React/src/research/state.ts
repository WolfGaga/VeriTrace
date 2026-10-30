export type TaskStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
export type RunStatus = 'idle' | 'running' | 'completed' | 'failed' | 'cancelled';
export interface ResearchActivity {
  stage: string;
  task_id?: string;
  section_index?: number;
  section_total?: number;
  title?: string;
  turn?: number;
}
export interface Source { url: string; title?: string; snippet?: string; summ?: string }
export interface TaskResult {
  answer: string;
  references: Record<string, Source>;
  messages?: Array<{ role?: string; content?: unknown }>;
  quality_results?: unknown[];
  feedback?: unknown;
  evidence_records?: unknown[];
  error?: string;
}
export interface ResearchTask {
  id: string;
  question: string;
  status: TaskStatus;
  cognitive_target_id?: string;
  cognitive_edge_id?: string;
  task_type?: string;
  task_objective?: string;
  attention_notes?: string[];
  result: TaskResult | null;
  activity?: ResearchActivity;
}
export interface ResearchState {
  question: string;
  status: RunStatus;
  tasks: Record<string, ResearchTask>;
  article: string;
  progress: string;
  references: Record<string, Source>;
  cognitiveGraph: any;
  usage: any;
  error?: string;
  activity?: ResearchActivity;
  startedAt?: number;
  lastEventAt?: number;
  waitingSeconds?: number;
  partialArticle?: string;
  recovery?: { run_id: string; language?: string; attempt: number; status: string;
    can_continue: boolean; can_write_report: boolean; saved_sections: number; checkpoint_saved?: boolean };
}
export interface ResearchEvent {
  schema_version: 2;
  type: string;
  task_id?: string | null;
  data: Record<string, any>;
}
export interface ResearchSession {
  schema_version: 2;
  rounds: ResearchState[];
  current: ResearchState;
}
export const emptyResearch = (question = ''): ResearchState => ({
  question, status: 'idle', tasks: {}, article: '', progress: '',
  references: {}, cognitiveGraph: null, usage: null,
});
export const emptySession = (): ResearchSession => ({
  schema_version: 2, rounds: [], current: emptyResearch(),
});
export const isResearchEvent = (value: any): value is ResearchEvent =>
  value?.schema_version === 2 && typeof value.type === 'string' &&
  value.data !== null && typeof value.data === 'object' && !Array.isArray(value.data);

export function normalizeReferences(input: unknown): Record<string, Source> {
  const references: Record<string, Source> = {};
  if (!input || typeof input !== 'object') return references;
  for (const [id, source] of Object.entries(input)) {
    if (typeof source === 'string') references[id] = { url: source };
    else if (source && typeof source === 'object' && !Array.isArray(source)) {
      references[id] = { ...source, url: typeof source.url === 'string' ? source.url : '' };
    }
  }
  return references;
}

function mergeTasks(previous: ResearchState['tasks'], input: unknown): ResearchState['tasks'] {
  if (!input || typeof input !== 'object') return previous;
  const tasks = Array.isArray(input) ? input : Object.values(input);
  const next = { ...previous };
  for (const task of tasks) {
    if (task && typeof task === 'object' && typeof (task as ResearchTask).id === 'string') {
      const item = task as ResearchTask;
      const merged = { ...next[item.id], ...item };
      next[item.id] = { ...merged, result: merged.result
        ? { ...merged.result, references: normalizeReferences(merged.result.references) } : null };
    }
  }
  return next;
}

export function normalizeResearchState(state: Partial<ResearchState>): ResearchState {
  const tasks = mergeTasks({}, state.tasks);
  return { ...emptyResearch(), ...state, tasks: isTerminal(state.status) ? stopPendingTasks(tasks) : tasks,
    references: normalizeReferences(state.references) };
}

export function restoreSavedResearch(state: ResearchState, saved: any): ResearchState {
  const view = saved.view || {};
  return normalizeResearchState({ ...state, tasks: view.tasks || state.tasks,
    article: view.article || state.article, partialArticle: view.partial_article || state.partialArticle,
    references: view.ref2url || state.references, cognitiveGraph: view.cognitive_graph || state.cognitiveGraph,
    usage: view.usage || state.usage,
    activity: view.activity || state.activity, waitingSeconds: view.waiting_seconds || 0, lastEventAt: Date.now(),
    recovery: { ...saved.recovery, language: saved.language },
    error: saved.recovery.status === 'completed' ? undefined : saved.error || undefined,
    status: saved.recovery.status === 'completed' ? 'completed' : saved.recovery.status === 'running' ? 'running'
      : saved.recovery.status === 'failed' ? 'failed' : 'cancelled' });
}

function isTerminal(status?: RunStatus) {
  return status === 'completed' || status === 'failed' || status === 'cancelled';
}

function stopPendingTasks(tasks: ResearchState['tasks']): ResearchState['tasks'] {
  return Object.fromEntries(Object.entries(tasks).map(([id, task]) => [id,
    task.status === 'running' || task.status === 'pending' ? { ...task, status: 'cancelled' as TaskStatus } : task,
  ]));
}

export const phaseLabels: Record<string, string> = {
  connecting: 'Connecting', initializing: 'Preparing research', planning: 'Planning research',
  searching: 'Searching sources', filtering: 'Selecting sources', reading: 'Reading sources',
  updating_graph: 'Updating cognitive graph', planning_outline: 'Planning report outline',
  planning_sections: 'Planning section evidence', writing_section: 'Writing report',
  writing_summary: 'Writing executive summary', finalizing: 'Finalizing report and references',
};

export function researchStatusLabel(state: ResearchState): string {
  if (state.status === 'completed') return 'Completed';
  if (state.status === 'failed') return 'Failed';
  if (state.status === 'cancelled') return 'Stopped';
  if (state.status === 'idle') return 'Ready';
  return phaseLabels[state.activity?.stage || ''] || 'Researching';
}

export function researchReducer(state: ResearchState, event: ResearchEvent): ResearchState {
  const data = event.data;
  if (isTerminal(state.status) && (event.type === 'research.progress' || event.type === 'research.heartbeat')) return state;
  let next = { ...state, lastEventAt: Date.now() };
  if (data.tasks) next.tasks = mergeTasks(state.tasks, data.tasks);
  if (data.task) next.tasks = mergeTasks(next.tasks, [data.task]);
  if (data.cognitive_graph) next.cognitiveGraph = data.cognitive_graph;
  if (data.ref2url) next.references = normalizeReferences(data.ref2url);
  switch (event.type) {
    case 'research.run':
    case 'research.recovery':
      return { ...next, recovery: { ...state.recovery, ...data } as ResearchState['recovery'] };
    case 'research.restored':
      return { ...next, tasks: mergeTasks({}, data.tasks), error: undefined };
    case 'research.partial':
      return { ...next, partialArticle: data.partial_article || state.partialArticle };
    case 'research.started':
      return { ...next, question: data.question ?? state.question, status: 'running',
        startedAt: state.startedAt || Date.now(), activity: { stage: 'planning' }, waitingSeconds: 0 };
    case 'research.progress': {
      const activity = { ...data, stage: String(data.stage || 'planning'), task_id: event.task_id || undefined };
      if (event.task_id && next.tasks[event.task_id]) {
        next.tasks = { ...next.tasks, [event.task_id]: { ...next.tasks[event.task_id], activity } };
      }
      return { ...next, activity, waitingSeconds: 0 };
    }
    case 'research.heartbeat':
      return { ...next, waitingSeconds: Number(data.waiting_seconds || 0) };
    case 'planner.thinking':
    case 'planner.executing':
    case 'planner.observation':
      return { ...next, activity: { stage: 'planning' }, waitingSeconds: 0,
        progress: typeof data.content === 'string' ? data.content : state.progress };
    case 'research.updated':
      return { ...next, progress: typeof data.content === 'string' ? data.content : state.progress };
    case 'research.completed':
      return { ...next, article: data.article ?? '', status: 'completed', error: undefined, tasks: stopPendingTasks(next.tasks) };
    case 'research.failed':
      return { ...next, status: 'failed', error: String(data.error || 'Research failed'), tasks: stopPendingTasks(next.tasks) };
    case 'research.cancelled':
      return { ...next, status: 'cancelled', tasks: stopPendingTasks(next.tasks) };
    case 'usage.updated':
      return { ...next, usage: data.usage };
    default:
      return next;
  }
}

export interface TaskTreeNode {
  id: string | number;
  name: string;
  state: number;
  selectable?: boolean;
  status?: TaskStatus;
  children?: TaskTreeNode[];
}

// Tree nodes are presentation data; research tasks never contain synthetic nodes.
export function taskTree(state: ResearchState): TaskTreeNode[] {
  if (!state.question) return [];
  const groups = new Map<string, TaskTreeNode>();
  const children: TaskTreeNode[] = [];
  const graphNodes = state.cognitiveGraph?.nodes || state.cognitiveGraph?.entities || {};
  for (const task of Object.values(state.tasks)) {
    const node: TaskTreeNode = {
      id: task.id, name: task.id, selectable: true, status: task.status,
      state: task.status === 'running' || task.status === 'pending' ? 1 : task.status === 'completed' ? 3 : 2,
    };
    if (!task.cognitive_target_id) { children.push(node); continue; }
    let group = groups.get(task.cognitive_target_id);
    if (!group) {
      const entity = Array.isArray(graphNodes)
        ? graphNodes.find((item: any) => item.id === task.cognitive_target_id)
        : graphNodes[task.cognitive_target_id];
      group = { id: task.cognitive_target_id, name: entity?.name || task.cognitive_target_id,
        state: 3, selectable: false, children: [] };
      groups.set(task.cognitive_target_id, group);
      children.push(group);
    }
    group.children!.push(node);
    if (node.state === 1) group.state = 1;
    else if (node.state === 2 && group.state !== 1) group.state = 2;
  }
  return [{ id: 0, name: 'Original Question', state: 3, selectable: false, children }];
}
