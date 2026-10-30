import { useCallback, useEffect, useRef, useState } from 'react';
import { message } from 'antd';
import { fetchEventSource } from '@microsoft/fetch-event-source';
import useConversationHistory from './useConversationHistory';
import { emptyResearch, emptySession, isResearchEvent, researchReducer, restoreSavedResearch, ResearchSession } from '@/research/state';
import { readBrowserSession } from '@/research/history';

async function readRejectedResponse(response: Response): Promise<string> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      response.text(),
      new Promise<string>(resolve => {
        timer = setTimeout(() => resolve('Response body unavailable after 2 seconds'), 2000);
      }),
    ]);
  } catch (error) { return String(error); }
  finally { if (timer) clearTimeout(timer); }
}

export default function useResearchSession() {
  const history = useConversationHistory();
  const [session, setSession] = useState<ResearchSession>(emptySession);
  const sessionRef = useRef(session);
  const controller = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const initialized = useRef(false);
  const [selection, setSelection] = useState<{ id: string; round: number } | null>(null);

  const commit = useCallback((next: ResearchSession, persist = true) => {
    sessionRef.current = next;
    setSession(next);
    try { sessionStorage.setItem('veritrace_session_v2', JSON.stringify(next)); }
    catch (error) { console.warn('[VERITRACE] Browser session cache unavailable:', error); }
    if (persist) history.updateCurrentConversation(next);
  }, [history.updateCurrentConversation]);

  const cancel = useCallback(() => {
    const runId = sessionRef.current.current.recovery?.run_id;
    if (runId && sessionRef.current.current.status === 'running') {
      void fetch(`/api/research/${runId}/stop`, { method: 'POST' }).catch(console.warn);
    }
    generation.current += 1;
    controller.current?.abort();
    controller.current = null;
    const current = sessionRef.current;
    if (current.current.status === 'running') {
      commit({ ...current, current: researchReducer(current.current,
        { schema_version: 2, type: 'research.cancelled', data: {} }) });
    }
  }, [commit]);

  const newConversation = useCallback(() => {
    cancel();
    history.createConversation();
    commit(emptySession());
    setSelection(null);
  }, [cancel, history.createConversation, commit]);

  const selectConversation = useCallback(async (id: string) => {
    cancel();
    const token = generation.current;
    try {
      const restored = await history.selectConversation(id);
      if (token === generation.current && restored) {
        const runId = restored.current.recovery?.run_id;
        if (runId) {
          try {
            const response = await fetch(`/api/research/${runId}`);
            if (response.ok) {
              const saved = await response.json();
              restored.current = restoreSavedResearch(restored.current, saved);
            }
          } catch (error) { console.warn('[VERITRACE] Saved research is temporarily unavailable:', error); }
        }
        if (token === generation.current) { commit(restored, false); setSelection(null); }
      }
    } catch (error) {
      console.error('[VERITRACE] Failed to open conversation:', id, error);
      message.error(String(error));
    }
  }, [cancel, history.selectConversation, commit]);

  const deleteConversation = useCallback(async (id: string) => {
    if (id === history.currentId) cancel();
    try {
      await history.deleteConversation(id);
      if (id === history.currentId) {
        const next = history.conversations.find(item => item.id !== id);
        if (next) await selectConversation(next.id);
        else { commit(emptySession(), false); setSelection(null); }
      }
    } catch (error) {
      console.error('[VERITRACE] Failed to delete conversation:', id, error);
      message.error(String(error));
    }
  }, [history.deleteConversation, history.currentId, history.conversations, cancel, selectConversation, commit]);

  useEffect(() => {
    if (history.isLoading || initialized.current) return;
    initialized.current = true;
    if (localStorage.getItem('veritrace_create_new')) {
      localStorage.removeItem('veritrace_create_new'); newConversation(); return;
    }
    const id = localStorage.getItem('veritrace_current_conversation');
    if (id && history.conversations.some(item => item.id === id)) {
      void selectConversation(id); return;
    }
    const saved = readBrowserSession();
    if (saved) { history.createConversation(saved.rounds[0]?.question || saved.current.question); commit(saved); }
  }, [history.isLoading, history.conversations, history.createConversation, selectConversation, newConversation, commit]);

  useEffect(() => {
    window.addEventListener('veritrace-new-chat', newConversation);
    return () => { window.removeEventListener('veritrace-new-chat', newConversation); };
  }, [newConversation]);
  useEffect(() => () => { generation.current += 1; controller.current?.abort(); }, []);

  useEffect(() => {
    const runId = session.current.recovery?.run_id;
    if (!runId || session.current.status !== 'running' || controller.current) return;
    const token = generation.current;
    const timer = setInterval(async () => {
      try {
        const response = await fetch(`/api/research/${runId}`);
        if (response.ok && generation.current === token && !controller.current) {
          const saved = await response.json();
          const previous = sessionRef.current;
          if (generation.current === token) commit({ ...previous, current: restoreSavedResearch(previous.current, saved) });
        }
      } catch (error) { console.warn('[VERITRACE] Could not refresh research status:', error); }
    }, 2000);
    return () => clearInterval(timer);
  }, [session.current.recovery?.run_id, session.current.status, commit]);

  const start = useCallback(async (question: string, recoveryAction?: 'continue' | 'write_report') => {
    if (!question.trim() || sessionRef.current.current.status === 'running') return;
    cancel();
    const token = generation.current;
    if (!history.currentId) history.createConversation(question);
    const previous = sessionRef.current;
    const rounds = !recoveryAction && previous.current.question ? [...previous.rounds, previous.current] : previous.rounds;
    const current = { ...(recoveryAction ? previous.current : emptyResearch(question)), error: undefined, status: 'running' as const,
      activity: { stage: 'connecting' }, startedAt: Date.now(), lastEventAt: Date.now() };
    commit({ schema_version: 2, rounds, current });
    setSelection(null);
    const active = new AbortController();
    controller.current = active;
    try {
      await fetchEventSource('/solve', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ inputs: question,
          lang: recoveryAction ? previous.current.recovery?.language || localStorage.getItem('veritrace_lang') || 'en'
            : localStorage.getItem('veritrace_lang') || 'en',
          ...(recoveryAction ? { resume_run_id: previous.current.recovery?.run_id, recovery_action: recoveryAction } : {}) }),
        signal: active.signal, openWhenHidden: true,
        async onopen(response) {
          if (!response.ok || !response.headers.get('content-type')?.includes('text/event-stream')) {
            console.error(`[VERITRACE] Research request rejected (${response.status}):`, await readRejectedResponse(response));
            throw new Error(response.status === 409 ? 'Previous attempt is still stopping. Please retry shortly.'
              : `Research request failed (${response.status}). Check the backend log, fix the configuration, and retry.`);
          }
          const runId = response.headers.get('x-research-run-id');
          if (runId && /^[a-f0-9]{32}$/.test(runId) && token === generation.current) {
            const previous = sessionRef.current;
            commit({ ...previous, current: researchReducer(previous.current,
              { schema_version: 2, type: 'research.run', data: { run_id: runId,
                attempt: Number(response.headers.get('x-research-attempt')) || 1,
                status: 'running', can_continue: false, can_write_report: false, saved_sections: 0 } }) });
          }
        },
        onmessage(event) {
          if (!event.data || token !== generation.current) return;
          const value: unknown = JSON.parse(event.data);
          if (!isResearchEvent(value)) throw new Error('Unsupported research event');
          if (value.type === 'research.failed') console.error('[VERITRACE] Backend research failed:', value.data.error);
          const previous = sessionRef.current;
          const next = { ...previous, current: researchReducer(previous.current, value) };
          if (value.type === 'research.progress' || value.type === 'research.heartbeat') {
            // Transient activity must not serialize and save the full research session.
            sessionRef.current = next;
            setSession(next);
          } else commit(next);
        },
        onerror(error) { throw error; },
        onclose() {
          if (token === generation.current && sessionRef.current.current.status === 'running') {
            throw new Error('Research stream closed before completion');
          }
        },
      });
    } catch (error) {
      if (active.signal.aborted || token !== generation.current) return;
      console.error('[VERITRACE] Research stream failed:', error);
      const previous = sessionRef.current;
      commit({ ...previous, current: researchReducer(previous.current,
        { schema_version: 2, type: 'research.failed', data: { error: String(error) } }) });
      message.error(String(error));
    } finally {
      if (token === generation.current) { controller.current = null; void history.flush(); }
    }
  }, [cancel, history.currentId, history.createConversation, history.flush, commit]);

  const recover = useCallback(async (action: 'continue' | 'write_report' | 'retry') => {
    const current = sessionRef.current.current;
    if (!current.recovery?.run_id) {
      if (action === 'retry') await start(current.question);
      return;
    }
    cancel();
    const token = generation.current;
    try {
      let saved;
      for (let attempt = 0; attempt < 25; attempt++) {
        const response = await fetch(`/api/research/${current.recovery.run_id}`);
        if (!response.ok) throw new Error('Saved research unavailable. Download the findings and retry later.');
        saved = await response.json();
        if (saved.recovery.status === 'running' && current.status === 'failed' && action !== 'write_report') {
          if (generation.current === token) {
            const previous = sessionRef.current;
            commit({ ...previous, current: restoreSavedResearch(previous.current, saved) });
          }
          return;
        }
        if (saved.recovery.status !== 'running' && saved.recovery.status !== 'stopping') break;
        if (attempt === 0) await fetch(`/api/research/${current.recovery.run_id}/stop`, { method: 'POST' });
        await new Promise(resolve => setTimeout(resolve, 200));
      }
      if (generation.current !== token) return;
      if (saved.recovery.status === 'running' || saved.recovery.status === 'stopping') throw new Error('Previous attempt is still stopping. Retry shortly.');
      const previous = sessionRef.current;
      if (saved.recovery.status === 'completed' && typeof saved.view?.article === 'string' && saved.view.article.trim()) {
        commit({ ...previous, current: restoreSavedResearch(previous.current, saved) });
        return;
      }
      commit({ ...previous, current: { ...previous.current,
        recovery: { ...saved.recovery, language: saved.language } } });
      await start(current.question, action === 'retry' ? saved.recovery.can_continue ? 'continue' : undefined : action);
    } catch (error) { message.error(String(error)); }
  }, [cancel, commit, start]);

  const selectedRound = selection ? [...session.rounds, session.current][selection.round] : null;
  const selectedTask = selection ? selectedRound?.tasks[selection.id] || null : null;
  return { ...history, session, selectedTask, selectTask: (id: string, round: number) => setSelection({ id, round }),
    start, recover, retry: () => recover('retry'), cancel, newConversation, selectConversation, deleteConversation };
}
