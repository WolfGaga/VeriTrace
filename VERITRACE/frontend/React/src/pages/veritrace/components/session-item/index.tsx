import { motion } from 'framer-motion';
import Answer from '../answer';
import { ResearchState, researchStatusLabel, taskTree } from '@/research/state';
import ResearchStatus from '../research-status';
import RecoveryPanel from '../recovery-panel';

interface SessionItemProps {
    item: ResearchState;
    handleNodeClick: (node: string, idx: number) => void;
    idx: number;
    onRecover?: (action: 'continue' | 'write_report') => void;
    onRetry?: () => void;
}

const SessionItem = ({ item, handleNodeClick, idx, onRecover, onRetry }: SessionItemProps) => {
    return (
        <div className="space-y-6 max-w-full overflow-hidden">
            <motion.div
                className="flex items-start gap-4"
                initial={{ opacity: 0, x: -20 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.3 }}
            >
                <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-accent-purple to-accent-pink flex items-center justify-center flex-shrink-0 shadow-md">
                    <svg
                        width="20"
                        height="20"
                        viewBox="0 0 20 20"
                        fill="none"
                        xmlns="http://www.w3.org/2000/svg"
                        className="text-white"
                    >
                        <path
                            d="M10 10C12.0711 10 13.75 8.32107 13.75 6.25C13.75 4.17893 12.0711 2.5 10 2.5C7.92893 2.5 6.25 4.17893 6.25 6.25C6.25 8.32107 7.92893 10 10 10Z"
                            stroke="currentColor"
                            strokeWidth="1.5"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                        />
                        <path
                            d="M3.75 17.5C3.75 14.0482 6.54822 11.25 10 11.25C13.4518 11.25 16.25 14.0482 16.25 17.5"
                            stroke="currentColor"
                            strokeWidth="1.5"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                        />
                    </svg>
                </div>

                <div className="min-w-0 max-w-[85%]">
                    <div className="inline-block px-5 py-3 bg-bento-surfaceHover rounded-2xl rounded-tl-md max-w-full">
                        <p className="text-body text-text-primary leading-relaxed break-words">
                            {item.question}
                        </p>
                    </div>
                </div>
            </motion.div>

            <ResearchStatus state={item} />
            <RecoveryPanel state={item} onRecover={onRecover} onRetry={onRetry} onTask={id => handleNodeClick(id, idx)} />
            {!item.article && item.partialArticle && <p className="text-sm text-amber-800">Draft report · incomplete · {item.recovery?.saved_sections || 0} saved sections</p>}
            <Answer
                key={`answer-item-${idx}`}
                listId={idx}
                response={item.article || item.partialArticle || item.progress}
                refList={item.references}
                isEnd={item.status !== 'running'}
                status={item.status}
                activityLabel={researchStatusLabel(item)}
                isPartialReport={!item.article && Boolean(item.partialArticle)}
                tree={taskTree(item)}
                handleNodeClick={handleNodeClick}
                question={item?.question}
                cognitiveGraph={item.cognitiveGraph}
            />
        </div>
    );
};

export default SessionItem;
