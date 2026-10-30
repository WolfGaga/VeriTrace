import { useContext } from 'react';
import { VeritraceContext } from '../../provider/context';
import { TaskTreeNode } from '@/research/state';

interface Props {
  isEnd: boolean;
  renderData: TaskTreeNode[];
  handleNodeClick: (id: string) => void;
  listId?: number;
  status?: string;
}

function TaskBranch({ node, selected, onSelect }: {
  node: TaskTreeNode; selected: string; onSelect: (id: string) => void;
}) {
  const active = selected === String(node.id);
  const style = active ? 'border-accent-blue bg-accent-blue/10 text-accent-blue'
    : node.status === 'failed' ? 'border-red-300 bg-red-50 text-red-700'
    : node.status === 'cancelled' ? 'border-slate-300 bg-slate-50 text-slate-500'
    : node.state === 1 ? 'border-bento-border bg-bento-surfaceHover text-text-secondary'
    : node.state === 2 ? 'border-slate-300 bg-bento-surface text-text-secondary'
    : 'border-accent-green/30 bg-bento-surface text-text-primary';
  return <li className="list-none min-w-0">
    <button type="button" onClick={() => node.selectable && onSelect(String(node.id))}
      disabled={!node.selectable} aria-pressed={node.selectable ? active : undefined}
      className={`inline-flex max-w-full items-center gap-2 rounded-lg border px-3 py-2 text-xs text-left break-words ${style}`}>
      <span>{node.name}</span>
      {node.state === 1 && <span className="h-1.5 w-1.5 rounded-full bg-accent-blue animate-pulse" />}
      {node.selectable && node.state === 3 && <span aria-label="Completed" className="text-accent-green">✓</span>}
      {node.status === 'failed' && <span className="text-red-600">Failed</span>}
      {node.status === 'cancelled' && <span className="text-slate-500">Stopped</span>}
    </button>
    {!!node.children?.length && <ul className="ml-4 mt-2 space-y-2 border-l border-bento-border pl-4 list-none">
      {node.children.map(child => <TaskBranch key={child.id} node={child} selected={selected} onSelect={onSelect} />)}
    </ul>}
  </li>;
}

export default function MindMapGraph({ isEnd, renderData, handleNodeClick, status }: Props) {
  const { activeNode } = useContext(VeritraceContext);
  return <div className="max-w-full space-y-3 py-2">
    <ul className="list-none m-0 p-0 space-y-2">
      {renderData.map(node => <TaskBranch key={node.id} node={node} selected={activeNode} onSelect={handleNodeClick} />)}
    </ul>
    {isEnd && <div className="inline-flex rounded-lg border border-bento-border px-3 py-1.5 text-xs text-text-secondary">{status === 'failed' ? 'Failed' : status === 'cancelled' ? 'Stopped' : 'Done'}</div>}
  </div>;
}
