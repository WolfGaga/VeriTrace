import { useState, useEffect, useContext, lazy, Suspense } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import MindMapGraph from '../mind-map';
import CognitiveGraph from '../cognitive-graph';
import { VeritraceContext } from '../../provider/context';
import CustomMarkdown from '../custom-markdown';
import ErrorBoundary from '@/components/ErrorBoundary';

const MindMap3D = lazy(() => import('@/components/3d/MindMap3D'));

interface IProps {
    tree: any[];
    isEnd: boolean;
    response: string;
    refList: any;
    listId: number;
    question: string;
    handleNodeClick: (info: any, idx: number) => void;
    cognitiveGraph?: any;
    status?: string;
    activityLabel?: string;
    isPartialReport?: boolean;
}

const CollapseIcon = () => (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path
            fillRule="evenodd"
            clipRule="evenodd"
            d="M2.66667 2C2.29848 2 2 2.29848 2 2.66667V6C2 6.36819 2.29848 6.66667 2.66667 6.66667C3.03486 6.66667 3.33333 6.36819 3.33333 6V3.33333H6C6.36819 3.33333 6.66667 3.03486 6.66667 2.66667C6.66667 2.29848 6.36819 2 6 2H2.66667ZM13.3333 14C13.7015 14 14 13.7015 14 13.3333V10C14 9.63181 13.7015 9.33333 13.3333 9.33333C12.9651 9.33333 12.6667 9.63181 12.6667 10V12.6667H10C9.63181 12.6667 9.33333 12.9651 9.33333 13.3333C9.33333 13.7015 9.63181 14 10 14H13.3333Z"
            fill="currentColor"
        />
    </svg>
);

const ExpandIcon = () => (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M4 12L12 4M12 4H6M12 4V10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
);

const View3DIcon = () => (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M8 1L14 4.5V11.5L8 15L2 11.5V4.5L8 1Z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
        <path d="M8 15V8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
        <path d="M14 4.5L8 8L2 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
);

const View2DIcon = () => (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M2 4H14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
        <path d="M2 8H10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
        <path d="M2 12H12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
        <circle cx="14" cy="8" r="1.5" fill="currentColor"/>
        <circle cx="12" cy="12" r="1.5" fill="currentColor"/>
    </svg>
);

const ExportIcon = () => (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M8 2V10M8 10L5 7M8 10L11 7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
        <path d="M3 13H13" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
);

const CheckIcon = () => (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M3 8L6.5 11.5L13 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
);

const CognitiveIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.5"/>
        <circle cx="4" cy="4" r="2" stroke="currentColor" strokeWidth="1.5"/>
        <circle cx="20" cy="4" r="2" stroke="currentColor" strokeWidth="1.5"/>
        <circle cx="4" cy="20" r="2" stroke="currentColor" strokeWidth="1.5"/>
        <circle cx="20" cy="20" r="2" stroke="currentColor" strokeWidth="1.5"/>
        <line x1="6" y1="6" x2="9" y2="9" stroke="currentColor" strokeWidth="1.5"/>
        <line x1="15" y1="9" x2="18" y2="6" stroke="currentColor" strokeWidth="1.5"/>
        <line x1="9" y1="15" x2="6" y2="18" stroke="currentColor" strokeWidth="1.5"/>
        <line x1="15" y1="15" x2="18" y2="18" stroke="currentColor" strokeWidth="1.5"/>
    </svg>
);

const MindMap3DLoading = () => (
    <div className="w-full h-[280px] bg-gradient-to-br from-slate-50 to-indigo-50/30 flex items-center justify-center">
        <div className="flex flex-col items-center gap-2">
            <div className="w-10 h-10 border-2 border-violet-400/30 border-t-violet-500 rounded-full animate-spin" />
            <span className="text-gray-500 text-xs">Loading 3D visualization...</span>
        </div>
    </div>
);

const Answer = ({ refList = null, tree, isEnd, response = '', listId, handleNodeClick, question = '', cognitiveGraph, status, activityLabel = 'Researching', isPartialReport = false }: IProps) => {
    const { activeNode } = useContext(VeritraceContext);
    const [showGraph, setShowGraph] = useState(true);
    const [view3D, setView3D] = useState(false);
    const [showCognitive, setShowCognitive] = useState(false);
    const renderData = tree;
    const [webGLError, setWebGLError] = useState(false);
    const [exportStatus, setExportStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle');
    const [exportMessage, setExportMessage] = useState('');

    const handleExportEval = async () => {
        if (!response || !question) return;
        setExportStatus('loading');
        try {
            const res = await fetch('/api/export-eval', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ question, article: response }),
            });
            const data = await res.json();
            if (data.success) {
                setExportStatus('success');
                setExportMessage(`ID=${data.query_id} → ${data.file_path}`);
                setTimeout(() => setExportStatus('idle'), 5000);
            } else {
                setExportStatus('error');
                setExportMessage(data.error || 'Export failed');
                setTimeout(() => setExportStatus('idle'), 5000);
            }
        } catch (e) {
            setExportStatus('error');
            setExportMessage('Network error');
            setTimeout(() => setExportStatus('idle'), 5000);
        }
    };

    const toggleGraph = () => {
        setShowGraph(!showGraph);
    };

    const toggleView = () => {
        if (webGLError) {
            console.warn('[VERITRACE] 3D view unavailable due to WebGL error');
            return;
        }
        setView3D(!view3D);
    };

    // Fall back to 2D if WebGL becomes unavailable.
    const handleWebGLError = () => {
        console.warn('[VERITRACE] WebGL error detected, falling back to 2D view');
        setWebGLError(true);
        setView3D(false);
    };

    const toggleCognitive = () => {
        setShowCognitive(!showCognitive);
    };

    const handleClick = (node: string) => {
        handleNodeClick(node, listId);
    };

    useEffect(() => {
        const handleContextLost = () => {
            console.warn('[VERITRACE] WebGL context lost event detected');
            handleWebGLError();
        };

        window.addEventListener('webglcontextlost', handleContextLost, true);

        return () => {
            window.removeEventListener('webglcontextlost', handleContextLost, true);
        };
    }, []);

    return (
        <motion.div
            className="flex items-start gap-4 max-w-full"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4, delay: 0.1 }}
        >
            <motion.div
                className="w-10 h-10 rounded-xl bg-gradient-to-br from-accent-blue to-accent-teal flex items-center justify-center flex-shrink-0 shadow-md"
                whileHover={{ scale: 1.05, rotateY: 15 }}
                transition={{ type: 'spring', stiffness: 300 }}
            >
                <svg
                    width="20"
                    height="20"
                    viewBox="0 0 20 20"
                    fill="none"
                    xmlns="http://www.w3.org/2000/svg"
                    className="text-white"
                >
                    <path
                        d="M10 1.66667L2.5 5.83333L10 10L17.5 5.83333L10 1.66667Z"
                        stroke="currentColor"
                        strokeWidth="1.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                    />
                    <path
                        d="M2.5 14.1667L10 18.3333L17.5 14.1667"
                        stroke="currentColor"
                        strokeWidth="1.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                    />
                    <path
                        d="M2.5 10L10 14.1667L17.5 10"
                        stroke="currentColor"
                        strokeWidth="1.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                    />
                </svg>
            </motion.div>

            <div className="flex-1 space-y-4 min-w-0 max-w-[calc(100%-56px)] overflow-hidden">
                <AnimatePresence mode="wait">
                    {showGraph && (showCognitive || renderData?.length > 0) ? (
                        <motion.div
                            key="mindmap"
                            className="bg-bento-surface border border-bento-borderLight rounded-bento overflow-hidden max-w-full"
                            style={{ maxWidth: '100%' }}
                            initial={{ opacity: 0, height: 0 }}
                            animate={{ opacity: 1, height: 'auto' }}
                            exit={{ opacity: 0, height: 0 }}
                            transition={{ duration: 0.3 }}
                        >
                            <div className="flex items-center justify-between p-3 border-b border-bento-borderLight">
                                <div className="flex items-center gap-2">
                                    <div className="w-1.5 h-4 rounded-full bg-gradient-to-b from-accent-blue to-accent-purple" />
                                    <h4 className="text-body-sm font-medium text-text-primary">
                                        {showCognitive ? 'Cognitive Graph' : 'Thinking Process'}
                                    </h4>
                                    {!showCognitive && view3D && (
                                        <span className="px-2 py-0.5 bg-accent-blue/10 text-accent-blue text-xs rounded-full">3D</span>
                                    )}
                                    {showCognitive && (
                                        <span className="px-2 py-0.5 bg-accent-green/10 text-accent-green text-xs rounded-full">Structure</span>
                                    )}
                                </div>
                                <div className="flex items-center gap-1">
                                    <button
                                        onClick={toggleCognitive}
                                        className={`p-1.5 rounded-lg transition-colors flex items-center gap-1 text-caption ${
                                            showCognitive
                                                ? 'bg-accent-green/10 text-accent-green'
                                                : 'hover:bg-bento-surfaceHover text-text-secondary hover:text-text-primary'
                                        }`}
                                        title={showCognitive ? 'Show Thinking Process' : 'Show Cognitive Graph'}
                                    >
                                        <CognitiveIcon />
                                    </button>
                                    {!showCognitive && !webGLError && (
                                        <button
                                            onClick={toggleView}
                                            className={`p-1.5 rounded-lg transition-colors flex items-center gap-1 text-caption ${
                                                view3D
                                                    ? 'bg-accent-blue/10 text-accent-blue'
                                                    : 'hover:bg-bento-surfaceHover text-text-secondary hover:text-text-primary'
                                            }`}
                                            title={view3D ? 'Switch to 2D View' : 'Switch to 3D View'}
                                        >
                                            {view3D ? <View2DIcon /> : <View3DIcon />}
                                        </button>
                                    )}
                                    <button
                                        onClick={toggleGraph}
                                        aria-label="Collapse visualization"
                                        className="p-1.5 rounded-lg hover:bg-bento-surfaceHover text-text-secondary hover:text-text-primary transition-colors flex items-center gap-1 text-caption"
                                    >
                                        <CollapseIcon />
                                    </button>
                                </div>
                            </div>

                            <div className={showCognitive ? 'p-4' : 'p-4 overflow-hidden max-w-full'}>
                                <ErrorBoundary key={showCognitive ? 'cognitive' : view3D ? '3d' : '2d'}>
                                    {showCognitive ? (
                                        <CognitiveGraph
                                            data={cognitiveGraph}
                                            isLoading={!isEnd && !cognitiveGraph}
                                        />
                                    ) : view3D ? (
                                        <Suspense fallback={<MindMap3DLoading />}>
                                            <MindMap3D
                                                data={renderData}
                                                activeNode={activeNode}
                                                onNodeClick={handleClick}
                                                isEnd={isEnd}
                                            />
                                        </Suspense>
                                    ) : (
                                        <MindMapGraph
                                            status={status}
                                            listId={listId}
                                            isEnd={isEnd}
                                            renderData={renderData}
                                            handleNodeClick={handleClick}
                                            key={`graph-${question}`}
                                        />
                                    )}
                                </ErrorBoundary>
                            </div>
                        </motion.div>
                    ) : showGraph ? (
                        <motion.div
                            key="empty-state"
                            className="bg-bento-surface border border-bento-borderLight rounded-bento p-4"
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                        >
                            <div className="flex items-center justify-between mb-3">
                                <span className="text-body-sm text-text-secondary">
                                    {showCognitive ? 'Cognitive Graph' : 'Thinking Process'}
                                </span>
                                <div className="flex items-center gap-1">
                                    <button
                                        onClick={toggleCognitive}
                                        className={`p-1.5 rounded-lg transition-colors text-caption ${
                                            showCognitive
                                                ? 'bg-accent-green/10 text-accent-green'
                                                : 'hover:bg-bento-surfaceHover text-text-secondary hover:text-text-primary'
                                        }`}
                                        title={showCognitive ? 'Switch to Thinking Process' : 'Switch to Cognitive Graph'}
                                    >
                                        <CognitiveIcon />
                                    </button>
                                    <button
                                        onClick={toggleGraph}
                                        aria-label="Collapse visualization"
                                        className="p-1.5 rounded-lg hover:bg-bento-surfaceHover text-text-secondary hover:text-text-primary transition-colors"
                                    >
                                        <CollapseIcon />
                                    </button>
                                </div>
                            </div>
                            <p className="text-caption text-text-muted">
                                {!isEnd
                                    ? 'Gathering data, please wait...'
                                    : showCognitive
                                        ? 'No cognitive graph data available for this conversation.'
                                        : 'No thinking process data available for this conversation.'}
                            </p>
                        </motion.div>
                    ) : (
                        (renderData?.length > 0 || cognitiveGraph) && (
                            <motion.button
                                key="expand-btn"
                                onClick={toggleGraph}
                                className="inline-flex items-center gap-2 px-4 py-2 bg-bento-surfaceHover hover:bg-bento-border/30 rounded-bento-sm text-body-sm text-text-secondary hover:text-text-primary transition-all"
                                initial={{ opacity: 0 }}
                                animate={{ opacity: 1 }}
                                exit={{ opacity: 0 }}
                                whileHover={{ scale: 1.02 }}
                                whileTap={{ scale: 0.98 }}
                            >
                                <ExpandIcon />
                                <span>View Thinking Process</span>
                            </motion.button>
                        )
                    )}
                </AnimatePresence>

                {response && (
                    <motion.div
                        className="prose prose-sm max-w-none"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        transition={{ delay: 0.2 }}
                    >
                        <CustomMarkdown
                            source={response}
                            refList={isEnd || isPartialReport ? refList : null}
                            quoType="merge"
                        />
                    </motion.div>
                )}

                {isEnd && status === 'completed' && response && (
                    <motion.div
                        className="flex items-center gap-2"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        transition={{ delay: 0.4 }}
                    >
                        <button
                            onClick={handleExportEval}
                            disabled={exportStatus === 'loading'}
                            className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-caption transition-all ${
                                exportStatus === 'success'
                                    ? 'bg-emerald-50 text-emerald-600 border border-emerald-200'
                                    : exportStatus === 'error'
                                    ? 'bg-red-50 text-red-600 border border-red-200'
                                    : 'bg-bento-surfaceHover hover:bg-bento-border/30 text-text-secondary hover:text-text-primary border border-bento-borderLight'
                            }`}
                            title="Export article for FACT+RACE evaluation"
                        >
                            {exportStatus === 'success' ? <CheckIcon /> : <ExportIcon />}
                            {exportStatus === 'loading' ? 'Exporting...' :
                             exportStatus === 'success' ? 'Exported' :
                             exportStatus === 'error' ? 'Failed' :
                             'Export for Eval'}
                        </button>
                        {exportMessage && (exportStatus === 'success' || exportStatus === 'error') && (
                            <span className={`text-caption ${exportStatus === 'success' ? 'text-emerald-600' : 'text-red-500'}`}>
                                {exportMessage}
                            </span>
                        )}
                    </motion.div>
                )}

                {!response && !isEnd && (
                    <motion.div
                        className="flex items-center gap-2 py-3"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                    >
                        <div className="flex gap-1">
                            <span className="w-2 h-2 rounded-full bg-accent-blue animate-bounce" style={{ animationDelay: '0ms' }} />
                            <span className="w-2 h-2 rounded-full bg-accent-blue animate-bounce" style={{ animationDelay: '150ms' }} />
                            <span className="w-2 h-2 rounded-full bg-accent-blue animate-bounce" style={{ animationDelay: '300ms' }} />
                        </div>
                        <span className="text-caption text-text-secondary">{activityLabel}...</span>
                    </motion.div>
                )}
            </div>
        </motion.div>
    );
};

export default Answer;
