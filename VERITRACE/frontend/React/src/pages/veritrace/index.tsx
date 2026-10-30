import { useState } from 'react';
import { Input } from 'antd';
import { motion, AnimatePresence } from 'framer-motion';
import { VeritraceContext } from './provider/context';
import ChatRight from './components/chat-right';
import SessionItem from './components/session-item';
import Notice from './components/notice';
import { BentoCard, BentoInputCard } from '@/components/ui/BentoCard';
import ErrorBoundary from '@/components/ErrorBoundary';
import ConversationSidebar from '@/components/ConversationSidebar';
import useResearchSession from '@/hooks/useResearchSession';
import { ResearchState, researchStatusLabel } from '@/research/state';

const SendIcon = () => (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M18.3333 1.66667L9.16666 10.8333" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
        <path d="M18.3333 1.66667L12.5 18.3333L9.16666 10.8333L1.66666 7.5L18.3333 1.66667Z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
);

const ThinkingIcon = () => (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M10 18.3333C14.6024 18.3333 18.3333 14.6024 18.3333 10C18.3333 5.39763 14.6024 1.66667 10 1.66667C5.39763 1.66667 1.66667 5.39763 1.66667 10C1.66667 14.6024 5.39763 18.3333 10 18.3333Z" stroke="currentColor" strokeWidth="1.5"/>
        <path d="M10 13.3333V10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
        <path d="M10 6.66667H10.0083" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
);

const VERITRACECon = () => {
    const { conversations, currentId, refreshConversations, session, selectedTask, selectTask,
        start, recover, retry, cancel, newConversation, selectConversation, deleteConversation, saveError, flush } = useResearchSession();
    const [sidebarCollapsed, setSidebarCollapsed] = useState(true);
    const [inputFocused, setFocused] = useState(false);
    const [stashedQuestion, setStashedQuestion] = useState('');
    const [showRight, setShowRight] = useState(false);
    const rounds = session.rounds;
    const currentRun = session.current;
    const chatIsOver = currentRun.status !== 'running';
    const isEnd = chatIsOver;
    const activeNode = selectedTask?.id || '';
    const usageStats = currentRun.usage || rounds[rounds.length - 1]?.usage;
    const newChatTip = chatIsOver && rounds.length + Number(Boolean(currentRun.question)) >= 5;
    const submitDisabled = newChatTip || !stashedQuestion.trim() || !chatIsOver;
    const toggleRight = () => setShowRight(value => !value);
    const handleNodeClick = (id: string, round: number) => {
        if (![...rounds, currentRun][round]?.tasks[id]) return;
        selectTask(id, round); setShowRight(true);
    };
    const handleNewConversation = () => { newConversation(); setShowRight(false); };
    const handleSelectConversation = (id: string) => { void selectConversation(id); setShowRight(false); };
    const handleDeleteConversation = (id: string) => { void deleteConversation(id); };
    const createSseChat = () => {
        if (submitDisabled) return;
        const question = stashedQuestion.trim();
        setStashedQuestion('');
        void start(question);
    };

    return (
        <VeritraceContext.Provider value={{
            isEnd,
            chatIsOver,
            activeNode: activeNode
        }}>
            <div className="flex h-[calc(100vh-64px)]">
                <ConversationSidebar
                    conversations={conversations}
                    currentId={currentId}
                    isCollapsed={sidebarCollapsed}
                    onToggleCollapse={() => setSidebarCollapsed(!sidebarCollapsed)}
                    onSelectConversation={handleSelectConversation}
                    onNewConversation={handleNewConversation}
                    onDeleteConversation={handleDeleteConversation}
                    onRefreshConversations={refreshConversations}
                />

                <div className="flex-1 overflow-auto">
                    <div className="max-w-bento mx-auto px-6 py-8">
                        <div className={`grid gap-6 transition-all duration-500 ${
                            showRight
                                ? 'grid-cols-1 lg:grid-cols-[1fr_400px]'
                                : 'grid-cols-1 max-w-chat mx-auto'
                        }`}>
                    <div className="flex flex-col gap-6 min-w-0 overflow-hidden">
                        {saveError && (
                            <div role="alert" className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
                                <p>{saveError}</p>
                                <button className="mt-2 font-medium underline" onClick={() => { void flush(); }}>Retry save</button>
                            </div>
                        )}
                        <BentoCard
                            variant="elevated"
                            padding="none"
                            className="flex-1 min-h-[400px] flex flex-col overflow-hidden"
                        >
                            <div className="px-6 py-4 border-b border-bento-borderLight flex items-center justify-between">
                                <h2 className="text-h4 text-text-primary">Conversation</h2>
                                {!chatIsOver && (
                                    <div className="flex items-center gap-2">
                                        <span className="w-2 h-2 rounded-full bg-accent-blue animate-pulse" />
                                        <span className="text-caption text-text-secondary">{researchStatusLabel(currentRun)}</span>
                                    </div>
                                )}
                            </div>

                            <div
                                id="chatArea"
                                className="flex-1 overflow-y-auto overflow-x-hidden p-6 scrollbar-hide"
                            >
                                <div id="messageWindowId" className="space-y-8 max-w-full">
                                    {rounds.length > 0 && rounds.map((item: ResearchState, idx) => (
                                        <motion.div
                                            key={`qa-item-${idx}`}
                                            initial={{ opacity: 0, y: 20 }}
                                            animate={{ opacity: 1, y: 0 }}
                                            transition={{ delay: idx * 0.1 }}
                                        >
                                            {item.question && (
                                                <SessionItem
                                                    item={item}
                                                    handleNodeClick={handleNodeClick}
                                                    idx={idx}
                                                />
                                            )}
                                        </motion.div>
                                    ))}

                                    {currentRun?.question && (
                                        <motion.div
                                            initial={{ opacity: 0, y: 20 }}
                                            animate={{ opacity: 1, y: 0 }}
                                        >
                                            <SessionItem
                                                item={currentRun}
                                                onRecover={action => { void recover(action); }}
                                                onRetry={() => { void retry(); }}
                                                handleNodeClick={handleNodeClick}
                                                idx={rounds.length}
                                            />
                                        </motion.div>
                                    )}

                                    {rounds.length === 0 && !currentRun?.question && (
                                        <motion.div
                                            className="flex flex-col items-center justify-center py-20 text-center"
                                            initial={{ opacity: 0, scale: 0.95 }}
                                            animate={{ opacity: 1, scale: 1 }}
                                        >
                                            <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-accent-blue/10 to-accent-purple/10 flex items-center justify-center mb-4">
                                                <svg width="32" height="32" viewBox="0 0 32 32" fill="none" className="text-accent-blue">
                                                    <path d="M16 2L2 9.33333L16 16.6667L30 9.33333L16 2Z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                                                    <path d="M2 22.6667L16 30L30 22.6667" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                                                    <path d="M2 16L16 23.3333L30 16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                                                </svg>
                                            </div>
                                            <h3 className="text-h3 text-text-primary mb-2">Start a Conversation</h3>
                                            <p className="text-body text-text-secondary max-w-md">
                                                Ask any question and VERITRACE will verify the information through multiple sources.
                                            </p>
                                        </motion.div>
                                    )}
                                </div>

                                <AnimatePresence>
                                    {newChatTip && (
                                        <motion.div
                                            className="mt-6 p-4 rounded-bento bg-accent-orange/10 border border-accent-orange/20"
                                            initial={{ opacity: 0, y: 10 }}
                                            animate={{ opacity: 1, y: 0 }}
                                            exit={{ opacity: 0, y: -10 }}
                                        >
                                            <p className="text-body-sm text-text-secondary">
                                                Conversation limit reached. Start a{' '}
                                                <button onClick={handleNewConversation} className="text-accent-blue font-medium hover:underline">
                                                    new chat
                                                </button>
                                                {' '}to continue.
                                            </p>
                                        </motion.div>
                                    )}
                                </AnimatePresence>
                            </div>
                        </BentoCard>

                        <AnimatePresence>
                            {chatIsOver && usageStats && (
                                <motion.div
                                    initial={{ opacity: 0, y: 20, height: 0 }}
                                    animate={{ opacity: 1, y: 0, height: 'auto' }}
                                    exit={{ opacity: 0, y: -10, height: 0 }}
                                >
                                    <BentoCard variant="gradient" padding="md">
                                        <div className="flex items-center gap-2 mb-4">
                                            <div className="w-1 h-4 rounded-full bg-gradient-to-b from-accent-blue to-accent-purple" />
                                            <h3 className="text-h4 text-text-primary">Usage Statistics</h3>
                                        </div>
                                        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                                            <div className="p-3 rounded-bento-sm bg-bento-surface border border-bento-borderLight">
                                                <p className="text-caption text-text-secondary mb-1">Tokens</p>
                                                <p className="text-body font-semibold text-text-primary font-mono">
                                                    {usageStats?.tokens?.total?.toLocaleString() || 0}
                                                </p>
                                                <p className="text-[11px] text-text-secondary font-mono mt-0.5">
                                                    {usageStats?.tokens?.input?.toLocaleString() || 0} in / {usageStats?.tokens?.output?.toLocaleString() || 0} out
                                                </p>
                                            </div>
                                            <div className="p-3 rounded-bento-sm bg-bento-surface border border-bento-borderLight">
                                                <p className="text-caption text-text-secondary mb-1">Searches</p>
                                                <p className="text-body font-semibold text-text-primary font-mono">
                                                    {usageStats?.search?.api_calls || 0}
                                                </p>
                                            </div>
                                            <div className="p-3 rounded-bento-sm bg-bento-surface border border-bento-borderLight">
                                                <p className="text-caption text-text-secondary mb-1">LLM Calls</p>
                                                <p className="text-body font-semibold text-text-primary font-mono">
                                                    {usageStats?.llm?.calls || 0}
                                                </p>
                                            </div>
                                            <div className="p-3 rounded-bento-sm bg-bento-surface border border-bento-borderLight">
                                                <p className="text-caption text-text-secondary mb-1">Duration</p>
                                                <p className="text-body font-semibold text-text-primary font-mono">
                                                    {usageStats?.timing?.duration_seconds || 0}s
                                                </p>
                                            </div>
                                        </div>
                                        {usageStats?.llm?.per_model && Object.keys(usageStats.llm.per_model).length > 0 && (
                                            <div className="mt-3 space-y-1.5">
                                                <p className="text-caption text-text-secondary">Per Model</p>
                                                {Object.entries(usageStats.llm.per_model).map(([model, stats]: [string, any]) => (
                                                    <div key={model} className="flex items-center justify-between px-3 py-1.5 rounded-bento-sm bg-bento-surface border border-bento-borderLight">
                                                        <span className="text-[11px] text-text-secondary font-mono truncate max-w-[180px]" title={model}>
                                                            {model.split('/').pop()}
                                                        </span>
                                                        <span className="text-[11px] text-text-primary font-mono ml-2 whitespace-nowrap">
                                                            {stats.calls} calls · {stats.input?.toLocaleString()} in / {stats.output?.toLocaleString()} out
                                                        </span>
                                                    </div>
                                                ))}
                                            </div>
                                        )}
                                    </BentoCard>
                                </motion.div>
                            )}
                        </AnimatePresence>

                        <BentoInputCard isFocused={inputFocused}>
                            {!chatIsOver && <button onClick={cancel} className="btn-ghost text-caption mb-2">Stop research</button>}
                            {currentRun.error && <p role="alert" className="text-red-600 text-caption mb-2">{currentRun.error}</p>}
                            <div className="flex items-center gap-3">
                                <Input
                                    className="flex-1 !bg-transparent !text-body !text-text-primary placeholder:!text-text-muted"
                                    variant="borderless"
                                    value={stashedQuestion}
                                    placeholder="Ask a question..."
                                    onChange={(e) => setStashedQuestion(e.target.value)}
                                    onPressEnter={createSseChat}
                                    onFocus={() => setFocused(true)}
                                    onBlur={() => setFocused(false)}
                                />
                                <motion.button
                                    className={`w-11 h-11 rounded-xl flex items-center justify-center transition-all ${
                                        submitDisabled
                                            ? 'bg-bento-surfaceHover text-text-muted cursor-not-allowed'
                                            : 'bg-gradient-to-br from-accent-blue to-accent-purple text-white shadow-lg hover:shadow-xl'
                                    }`}
                                    onClick={createSseChat}
                                    aria-label="Send question"
                                    disabled={submitDisabled}
                                    whileHover={!submitDisabled ? { scale: 1.05 } : {}}
                                    whileTap={!submitDisabled ? { scale: 0.95 } : {}}
                                >
                                    <SendIcon />
                                </motion.button>
                            </div>
                        </BentoInputCard>

                        <Notice />
                    </div>

                    <AnimatePresence>
                        {showRight && (
                            <motion.div
                                initial={{ opacity: 0, x: 20 }}
                                animate={{ opacity: 1, x: 0 }}
                                exit={{ opacity: 0, x: 20 }}
                                transition={{ duration: 0.3 }}
                                className="hidden lg:block lg:sticky lg:top-6 self-start h-[calc(100vh-128px)] overflow-hidden rounded-bento border border-bento-borderLight"
                            >
                                <ErrorBoundary>
                                    <ChatRight
                                        task={selectedTask}
                                        toggleRight={toggleRight}
                                    />
                                </ErrorBoundary>
                            </motion.div>
                        )}
                    </AnimatePresence>
                </div>

                {!showRight && (
                    <motion.button
                        className="fixed bottom-24 right-6 lg:hidden z-50 w-12 h-12 rounded-xl bg-bento-surface border border-bento-borderLight shadow-bento-hover flex items-center justify-center"
                        onClick={toggleRight}
                        aria-label="Open task details"
                        whileHover={{ scale: 1.05 }}
                        whileTap={{ scale: 0.95 }}
                        initial={{ opacity: 0, scale: 0.8 }}
                        animate={{ opacity: 1, scale: 1 }}
                    >
                        <ThinkingIcon />
                    </motion.button>
                )}

                <AnimatePresence>
                    {showRight && (
                        <motion.div
                            className="fixed inset-0 z-50 lg:hidden"
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                        >
                            <div
                                className="absolute inset-0 bg-black/20 backdrop-blur-sm"
                                onClick={toggleRight}
                            />
                            <motion.div
                                className="absolute right-0 top-0 bottom-0 w-[90%] max-w-[400px] bg-bento-bg shadow-bento-lg"
                                initial={{ x: '100%' }}
                                animate={{ x: 0 }}
                                exit={{ x: '100%' }}
                                transition={{ type: 'spring', damping: 30, stiffness: 300 }}
                            >
                                <ErrorBoundary>
                                    <ChatRight
                                        task={selectedTask}
                                        toggleRight={toggleRight}
                                    />
                                </ErrorBoundary>
                            </motion.div>
                        </motion.div>
                    )}
                </AnimatePresence>
                    </div>
                </div>
            </div>
        </VeritraceContext.Provider>
    );
};

export default VERITRACECon;
