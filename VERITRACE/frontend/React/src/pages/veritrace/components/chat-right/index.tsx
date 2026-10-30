import CustomMarkdown from '../custom-markdown';
import { ResearchTask } from '@/research/state';
import { safeSourceUrl } from '../../utils/tools';

interface Props { task: ResearchTask | null; toggleRight: () => void }

export default function ChatRight({ task, toggleRight }: Props) {
  const result = task?.result;
  return <div className="h-full min-h-0 flex flex-col bg-bento-bg">
    <div className="shrink-0 flex items-center justify-between px-5 py-4 border-b border-bento-borderLight bg-bento-surface">
      <h2 className="text-h4 text-text-primary">Thinking Process</h2>
      <button onClick={toggleRight} aria-label="Close task details" className="btn-ghost">×</button>
    </div>
    <div className="flex-1 min-h-0 p-5 space-y-5 overflow-auto">
      {!task ? <p className="text-text-secondary">Select a search task to view its details.</p> : <>
        <h3 className="font-medium break-words">{task.question}</h3>
        <p className="text-caption text-text-secondary capitalize" role="status">{task.status}</p>
        {task.task_objective && <p className="text-body-sm">{task.task_objective}</p>}
        {!!task.attention_notes?.length && <ul className="list-disc pl-4 text-body-sm">
          {task.attention_notes.map((note, index) => <li key={index}>{note}</li>)}
        </ul>}
        {result?.answer && <section className="space-y-2">
          <h4 className="text-caption text-text-secondary">Findings</h4>
          <CustomMarkdown source={result.answer} refList={result.references} />
        </section>}
        {result?.error && <p role="alert" className="text-red-600">{result.error}</p>}
        {!!Object.keys(result?.references || {}).length && <section className="space-y-2">
          <h4 className="text-caption text-text-secondary">Sources</h4>
          {Object.entries(result!.references).map(([id, source]) => <a key={id} href={safeSourceUrl(source.url)}
            target="_blank" rel="noopener noreferrer" className="block rounded-lg border border-bento-border p-3 text-body-sm">
            <span className="font-medium">{source.title || source.url}</span>
            {(source.snippet || source.summ) && <p className="text-caption text-text-secondary mt-1">{source.snippet || source.summ}</p>}
          </a>)}
        </section>}
        {!!result?.messages?.length && <details>
          <summary className="cursor-pointer text-caption text-text-secondary">Search trace</summary>
          <div className="mt-3 space-y-3">{result.messages.map((entry, index) =>
            <pre key={index} className="whitespace-pre-wrap break-words text-xs rounded-lg bg-bento-surfaceHover p-3">
              {typeof entry.content === 'string' ? entry.content : JSON.stringify(entry.content, null, 2)}
            </pre>)}</div>
        </details>}
        {result?.feedback != null && <details><summary className="cursor-pointer text-caption text-text-secondary">Search feedback</summary>
          <pre className="whitespace-pre-wrap text-xs mt-2">{JSON.stringify(result.feedback, null, 2)}</pre></details>}
      </>}
    </div>
  </div>;
}
