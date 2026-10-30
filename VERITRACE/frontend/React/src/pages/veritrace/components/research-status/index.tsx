import { useEffect, useState } from 'react';
import { ResearchState, researchStatusLabel } from '@/research/state';

export default function ResearchStatus({ state }: { state: ResearchState }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (state.status !== 'running') return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [state.status]);
  const tasks = Object.values(state.tasks);
  const count = (status: string) => tasks.filter(task => task.status === status).length;
  const active = count('running') + count('pending');
  const activity = state.activity;
  const taskDescription = activity?.task_id ? state.tasks[activity.task_id]?.task_objective || state.tasks[activity.task_id]?.question || activity.task_id : '';
  const elapsed = state.startedAt ? Math.max(0, Math.floor((now - state.startedAt) / 1000)) : 0;
  const disconnected = state.status === 'running' && state.lastEventAt && now - state.lastEventAt > 45000;
  return <div role="status" aria-label="Research status" className="rounded-lg border border-bento-borderLight bg-bento-surface px-4 py-3 text-sm">
    <div className="flex items-center gap-2">
      <span className={`h-2 w-2 rounded-full ${state.status === 'running' ? 'bg-accent-blue animate-pulse' : state.status === 'failed' ? 'bg-red-500' : state.status === 'completed' ? 'bg-emerald-500' : 'bg-slate-400'}`} />
      <span className="font-medium">{researchStatusLabel(state)}</span>
      {state.status === 'running' && state.startedAt && <span className="ml-auto text-text-secondary" aria-hidden="true">{Math.floor(elapsed / 60)}m {elapsed % 60}s</span>}
    </div>
    {state.status === 'running' && activity?.section_index && <p className="mt-1 text-text-secondary">Section {activity.section_index}/{activity.section_total}: {activity.title}</p>}
    {state.status === 'running' && activity?.task_id && <p title={taskDescription} className="mt-1 break-words text-text-secondary">Task: {taskDescription.length > 180 ? taskDescription.slice(0, 180) + '…' : taskDescription}</p>}
    {state.status === 'running' && activity?.stage === 'reading' && activity.title && <p className="mt-1 break-words text-text-secondary">Source: {activity.title}</p>}
    {tasks.length > 0 && <p className="mt-1 text-text-secondary">Research tasks: {count('completed')} completed · {active} active · {count('failed')} failed · {count('cancelled')} stopped</p>}
    {state.status === 'running' && !disconnected && (state.waitingSeconds || 0) >= 15 && <p className="mt-1 text-text-secondary">Connected; waiting for the current step ({Math.floor(state.waitingSeconds!)}s since last progress).</p>}
    {disconnected && <p className="mt-1 text-amber-700">No recent server update. Check the connection or stop research.</p>}
    {state.error && <p className="mt-1 text-red-600">{state.error}</p>}
  </div>;
}
