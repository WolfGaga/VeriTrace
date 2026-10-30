import { ResearchState } from '@/research/state';

export default function RecoveryPanel({ state, onRecover, onRetry, onTask }: {
  state: ResearchState;
  onRecover?: (action: 'continue' | 'write_report') => void;
  onRetry?: () => void;
  onTask: (id: string) => void;
}) {
  const tasks = Object.values(state.tasks).filter(task => task.result?.answer && task.status === 'completed');
  const recovery = state.recovery;
  const writing = ['planning_outline', 'planning_sections', 'writing_section', 'writing_summary', 'finalizing'].includes(state.activity?.stage || '');
  const download = () => {
    const payload = { status: state.status, incomplete: state.status !== 'completed', question: state.question,
      article: state.article || state.partialArticle || '', tasks: state.tasks,
      references: state.references, cognitive_graph: state.cognitiveGraph, recovery };
    const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a');
    link.href = url; link.download = `veritrace-${state.status === 'completed' ? 'report' : 'partial-findings'}.json`;
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return <section aria-label="Available research" className="rounded-xl border border-bento-borderLight p-4 space-y-3">
    <div className="flex flex-wrap items-center gap-3">
      <strong>Available findings · {tasks.length} completed searches</strong>
      <button className="underline text-sm" onClick={download}>Download {state.status === 'completed' ? 'research' : 'partial findings'}</button>
    </div>
    {state.status !== 'completed' && <p className="text-sm text-text-secondary">
      Findings and draft sections are available before the final report. Incomplete material may contain gaps.
    </p>}
    {state.error && <p role="alert" className="text-sm text-red-700">{state.error}</p>}
    {recovery?.checkpoint_saved === false && <p role="alert" className="text-sm text-amber-800">
      Server checkpoint could not be saved. Download the available findings before leaving this page.
    </p>}
    {tasks.length > 0 && <details><summary className="cursor-pointer">Browse collected findings and sources</summary>
      <div className="space-y-2 mt-2">{tasks.map(task => <div key={task.id} className="border-b pb-2">
        <button className="text-left underline" onClick={() => onTask(task.id)}>{task.id} · {task.question}</button>
        <p className="text-sm whitespace-pre-wrap mt-1">{task.result!.answer.slice(0, 500)}{task.result!.answer.length > 500 ? '…' : ''}</p>
      </div>)}</div>
    </details>}
    {onRecover && recovery && state.status !== 'completed' && <div className="flex flex-wrap gap-3">
      {state.status !== 'running' && recovery.can_continue && <button className="px-3 py-2 rounded border" onClick={() => onRecover('continue')}>Continue research</button>}
      {recovery.can_write_report && (state.status !== 'running' || !writing) && <button className="px-3 py-2 rounded border" onClick={() => onRecover('write_report')}>
        {recovery.saved_sections > 0 ? 'Resume report' : state.status === 'running' ? 'Stop search and write report' : 'Write report from findings'}
      </button>}
    </div>}
    {state.status !== 'running' && state.status !== 'completed' && recovery?.can_continue && <p className="text-sm text-text-secondary">
      Fix any API or configuration issue, restart the backend if needed, then continue from the saved checkpoint.
    </p>}
    {onRetry && (state.status === 'failed' || state.status === 'cancelled') && !recovery?.can_continue && <div>
      <p className="text-sm text-text-secondary">No server checkpoint is available. Retry starts a new run; existing findings stay in this conversation.</p>
      <button className="mt-2 px-3 py-2 rounded border" onClick={onRetry}>Retry research</button>
    </div>}
  </section>;
}
