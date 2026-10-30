import { useState, useCallback, useEffect, useRef } from 'react';
import { Conversation } from '../components/ConversationSidebar';
import { emptySession, ResearchSession } from '../research/state';
import { readSession } from '../research/history';

const API_BASE = '/api/conversations';
const titleFor = (question: string) => question.replace(/[#*`]/g, '').trim().slice(0, 50) || 'New Conversation';
interface PendingSave { id: string; data: ResearchSession; title: string; skip_timestamp_update: boolean }

export default function useConversationHistory() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [saveError, setSaveError] = useState<string | null>(null);
  const activeId = useRef<string | null>(null);
  const pending = useRef<PendingSave | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const creates = useRef(new Map<string, Promise<unknown>>());
  const writes = useRef(new Map<string, Promise<unknown>>());
  const pendingCreates = useRef(new Map<string, string>());
  const failedSaves = useRef(new Map<string, PendingSave>());
  const latestSaves = useRef(new Map<string, PendingSave>());
  const selectionSerial = useRef(0);

  const ensureCreated = useCallback((id: string): Promise<unknown> => {
    if (!pendingCreates.current.has(id)) return Promise.resolve();
    const active = creates.current.get(id);
    if (active) return active;
    const request = fetch(API_BASE, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id, title: pendingCreates.current.get(id), data: emptySession() }),
    }).then(response => {
      // A previous creation can succeed even if its response is interrupted.
      if (!response.ok && response.status !== 409) throw new Error(`Create failed (${response.status})`);
      pendingCreates.current.delete(id);
    }).finally(() => { creates.current.delete(id); });
    creates.current.set(id, request);
    return request;
  }, []);

  const flush = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    const saves = new Map(failedSaves.current);
    if (pending.current) saves.set(pending.current.id, pending.current);
    pending.current = null;
    if (!saves.size) return Promise.resolve();
    return Promise.all(Array.from(saves.values()).map(save => {
      failedSaves.current.delete(save.id);
      const previous = writes.current.get(save.id) || Promise.resolve();
      const write = previous.then(async () => {
        await ensureCreated(save.id);
        const body = JSON.stringify(save);
        // Large reports exceed the browser's keepalive body limit.
        const response = await fetch(`${API_BASE}/${save.id}`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body, keepalive: new Blob([body]).size <= 60_000,
        });
        if (!response.ok) throw new Error(`Save failed (${response.status})`);
        if (latestSaves.current.get(save.id) === save) latestSaves.current.delete(save.id);
        return true;
      }).catch(error => {
        console.warn('[VERITRACE] Failed to save conversation:', save.id, error);
        if (latestSaves.current.get(save.id) === save) failedSaves.current.set(save.id, save);
        setSaveError(`Conversation could not be saved. ${String(error)}`);
        return false;
      }).finally(() => {
        if (writes.current.get(save.id) === write) writes.current.delete(save.id);
      });
      writes.current.set(save.id, write);
      return write;
    })).then(results => {
      if (results.every(Boolean) && !failedSaves.current.size && !pendingCreates.current.size) setSaveError(null);
    });
  }, [ensureCreated]);

  const refreshConversations = useCallback(async () => {
    try {
      const response = await fetch(API_BASE);
      if (!response.ok) throw new Error(`History request failed (${response.status})`);
      const data = await response.json();
      setConversations(data.map((item: any) => ({ id: item.id, title: item.title,
        createdAt: item.created_at, updatedAt: item.updated_at })));
    } catch (error) { console.warn('[VERITRACE] Failed to load conversations:', error); }
    finally { setIsLoading(false); }
  }, []);
  useEffect(() => { void refreshConversations(); }, [refreshConversations]);
  useEffect(() => {
    const save = () => { void flush(); };
    window.addEventListener('pagehide', save);
    return () => { window.removeEventListener('pagehide', save); void flush(); };
  }, [flush]);

  const createConversation = useCallback((question = '') => {
    selectionSerial.current += 1;
    void flush();
    const id = 'conv_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
    const title = titleFor(question);
    const now = Date.now();
    activeId.current = id;
    setCurrentId(id);
    localStorage.setItem('veritrace_current_conversation', id);
    setConversations(items => [{ id, title, createdAt: now, updatedAt: now }, ...items]);
    pendingCreates.current.set(id, title);
    void ensureCreated(id).catch(error => {
      console.warn('[VERITRACE] Failed to create conversation:', id, error);
      setSaveError(`Conversation could not be saved. ${String(error)}`);
    });
    return id;
  }, [flush, ensureCreated]);

  const selectConversation = useCallback(async (id: string) => {
    const selection = ++selectionSerial.current;
    await flush();
    await writes.current.get(id);
    const response = await fetch(`${API_BASE}/${id}`);
    if (!response.ok) throw new Error(`History request failed (${response.status})`);
    const result = await response.json();
    if (selection !== selectionSerial.current) return null;
    activeId.current = id;
    setCurrentId(id);
    localStorage.setItem('veritrace_current_conversation', id);
    return readSession(result.data);
  }, [flush]);

  const updateCurrentConversation = useCallback((data: ResearchSession, skipTimestampUpdate = false) => {
    const id = activeId.current;
    if (!id) return;
    const title = titleFor(data.rounds[0]?.question || data.current.question);
    pending.current = { id, data, title, skip_timestamp_update: skipTimestampUpdate };
    latestSaves.current.set(id, pending.current);
    setConversations(items => items.map(item => item.id === id
      ? { ...item, title, updatedAt: skipTimestampUpdate ? item.updatedAt : Date.now() } : item));
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => { void flush(); }, 500);
  }, [flush]);

  const deleteConversation = useCallback(async (id: string) => {
    selectionSerial.current += 1;
    await flush();
    const response = await fetch(`${API_BASE}/${id}`, { method: 'DELETE' });
    if (!response.ok) throw new Error(`Delete failed (${response.status})`);
    failedSaves.current.delete(id);
    latestSaves.current.delete(id);
    pendingCreates.current.delete(id);
    setConversations(items => items.filter(item => item.id !== id));
    if (activeId.current === id) { activeId.current = null; setCurrentId(null); }
  }, [flush]);

  return { conversations, currentId, isLoading, saveError, createConversation, selectConversation,
    updateCurrentConversation, deleteConversation, refreshConversations, flush };
}
