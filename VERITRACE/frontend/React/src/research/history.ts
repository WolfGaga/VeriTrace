import { emptyResearch, emptySession, normalizeResearchState, ResearchSession, ResearchState, ResearchTask } from './state';

function legacyRound(value: any, cachedTasks: any = {}): ResearchState {
  const state = emptyResearch(value?.question || '');
  state.article = value?.response || '';
  state.references = value?.responseRefList || {};
  state.cognitiveGraph = value?.cognitive_graph || null;
  state.status = state.article ? 'completed' : state.question ? 'cancelled' : 'idle';
  const nodes = { ...cachedTasks, ...value?.nodes };
  for (const [id, raw] of Object.entries(nodes)) {
    const node = raw as any;
    if (id === 'root' || id === 'response') continue;
    const references = node.conclusionRef || node.memory?.ref2url || {};
    const answer = node.conclusion || node.response?.content || node.readingData || node.thinkingData || '';
    const task: ResearchTask = {
      id, question: node.subQuestion || node.content || id,
      status: node.outputing ? 'cancelled' : 'completed', result: {
        answer: typeof answer === 'string' ? answer : '', references,
        messages: node.thinkingData ? [{ role: 'assistant', content: node.thinkingData }] : [],
      },
    };
    state.tasks[id] = task;
  }
  for (const [parent, rawChildren] of Object.entries(value?.adjacency_list || {})) {
    if (!Array.isArray(rawChildren)) continue;
    for (const child of rawChildren) {
      const id = String(child.id ?? child.name);
      if (state.tasks[id] && parent !== 'root') state.tasks[id].cognitive_target_id = parent;
    }
  }
  return normalizeResearchState(state);
}

export function readSession(data: any): ResearchSession {
  if (data?.schema_version === 2) {
    const restore = (state: ResearchState): ResearchState => normalizeResearchState({
      ...state,
      status: state.status === 'running' ? 'cancelled' : state.status,
    });
    return { schema_version: 2, rounds: (data.rounds || []).map(restore),
      current: restore(data.current || emptyResearch()) };
  }
  if (!data || typeof data !== 'object') return emptySession();
  const rounds = (data.qaList || []).map((item: any) => legacyRound(item));
  let current = legacyRound({ ...data.formatted, adjacency_list: data.adjList || data.formatted?.adjacency_list }, data.stashedNodes);
  current.usage = data.usageStats || null;
  // Older saves sometimes contain the final response in both locations.
  const last = rounds[rounds.length - 1];
  if (last && last.question === current.question && last.article === current.article) {
    rounds[rounds.length - 1] = { ...last, usage: current.usage,
      tasks: { ...last.tasks, ...current.tasks } };
    current = emptyResearch();
  }
  return { schema_version: 2, rounds, current };
}

export function readBrowserSession(): ResearchSession | null {
  try {
    const native = sessionStorage.getItem('veritrace_session_v2');
    if (native) return readSession(JSON.parse(native));
    const parse = (key: string, fallback: any) => JSON.parse(sessionStorage.getItem(key) || JSON.stringify(fallback));
    const formatted = parse('veritrace_formatted', {});
    const qaList = parse('veritrace_qaList', []);
    if (!formatted.question && !qaList.length) return null;
    return readSession({ formatted, qaList, adjList: parse('veritrace_adjList', {}),
      stashedNodes: parse('veritrace_stashedNodes', JSON.parse(localStorage.getItem('stashedNodes') || '{}')) });
  } catch { return null; }
}
