
import React, { useState, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import TrashManager from './TrashManager';

export interface Conversation {
    id: string;
    title: string;
    createdAt: number;
    updatedAt: number;
}

interface ConversationSidebarProps {
    conversations: Conversation[];
    currentId: string | null;
    isCollapsed: boolean;
    onToggleCollapse: () => void;
    onSelectConversation: (id: string) => void;
    onNewConversation: () => void;
    onDeleteConversation: (id: string) => void;
    onRefreshConversations?: () => void;
}

const formatRelativeTime = (timestamp: number): string => {
    const now = Date.now();
    const diff = now - timestamp;

    const minutes = Math.floor(diff / 60000);
    const hours = Math.floor(diff / 3600000);
    const days = Math.floor(diff / 86400000);

    if (minutes < 1) return 'Just now';
    if (minutes < 60) return `${minutes}m ago`;
    if (hours < 24) return `${hours}h ago`;
    if (days < 7) return `${days}d ago`;

    return new Date(timestamp).toLocaleDateString();
};

const truncateTitle = (title: string, maxLength: number = 40): string => {
    if (title.length <= maxLength) return title;
    return title.substring(0, maxLength) + '...';
};

const ConversationSidebar: React.FC<ConversationSidebarProps> = ({
    conversations,
    currentId,
    isCollapsed,
    onToggleCollapse,
    onSelectConversation,
    onNewConversation,
    onDeleteConversation,
    onRefreshConversations,
}) => {
    const [contextMenu, setContextMenu] = useState<{ x: number; y: number; id: string } | null>(null);
    const [trashOpen, setTrashOpen] = useState(false);

    const handleContextMenu = useCallback((e: React.MouseEvent, id: string) => {
        e.preventDefault();
        setContextMenu({ x: e.clientX, y: e.clientY, id });
    }, []);

    const closeContextMenu = useCallback(() => {
        setContextMenu(null);
    }, []);

    const handleDelete = useCallback(() => {
        if (contextMenu) {
            onDeleteConversation(contextMenu.id);
            setContextMenu(null);
        }
    }, [contextMenu, onDeleteConversation]);

    React.useEffect(() => {
        const handleClick = () => closeContextMenu();
        if (contextMenu) {
            document.addEventListener('click', handleClick);
            return () => document.removeEventListener('click', handleClick);
        }
    }, [contextMenu, closeContextMenu]);

    const sortedConversations = [...conversations].sort((a, b) => b.updatedAt - a.updatedAt);

    return (
        <>
            <motion.aside
                initial={false}
                animate={{ width: isCollapsed ? 48 : 280 }}
                transition={{ duration: 0.2, ease: 'easeInOut' }}
                className="h-full bg-bento-card border-r border-bento-border flex flex-col overflow-hidden"
            >
                <div className="flex items-center justify-between p-3 border-b border-bento-border">
                    {!isCollapsed && (
                        <motion.span
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            className="text-caption font-medium text-text-secondary"
                        >
                            History
                        </motion.span>
                    )}
                    <button
                        onClick={onToggleCollapse}
                        className="p-1.5 rounded-lg hover:bg-bento-hover transition-colors text-text-secondary"
                        title={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
                    >
                        <svg
                            width="16"
                            height="16"
                            viewBox="0 0 16 16"
                            fill="none"
                            className={`transition-transform ${isCollapsed ? 'rotate-180' : ''}`}
                        >
                            <path
                                d="M10 12L6 8L10 4"
                                stroke="currentColor"
                                strokeWidth="1.5"
                                strokeLinecap="round"
                                strokeLinejoin="round"
                            />
                        </svg>
                    </button>
                </div>

                <div className="p-2">
                    <button
                        onClick={onNewConversation}
                        className={`w-full flex items-center gap-2 p-2 rounded-lg bg-accent-blue/10 hover:bg-accent-blue/20 text-accent-blue transition-colors ${
                            isCollapsed ? 'justify-center' : ''
                        }`}
                        title="New Chat"
                    >
                        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                            <path
                                d="M8 3V13M3 8H13"
                                stroke="currentColor"
                                strokeWidth="1.5"
                                strokeLinecap="round"
                            />
                        </svg>
                        {!isCollapsed && <span className="text-caption font-medium">New Chat</span>}
                    </button>
                </div>

                <div className="flex-1 overflow-y-auto scrollbar-hide">
                    <AnimatePresence mode="popLayout">
                        {sortedConversations.map((conv) => (
                            <motion.div
                                key={conv.id}
                                layout
                                initial={{ opacity: 0, x: -20 }}
                                animate={{ opacity: 1, x: 0 }}
                                exit={{ opacity: 0, x: -20 }}
                                className={`mx-2 mb-1 rounded-lg cursor-pointer transition-colors ${
                                    currentId === conv.id
                                        ? 'bg-accent-blue/15 border border-accent-blue/30'
                                        : 'hover:bg-bento-hover border border-transparent'
                                }`}
                                onClick={() => onSelectConversation(conv.id)}
                                onContextMenu={(e) => handleContextMenu(e, conv.id)}
                            >
                                {isCollapsed ? (
                                    <div className="p-3 flex justify-center">
                                        <div
                                            className={`w-2 h-2 rounded-full ${
                                                currentId === conv.id ? 'bg-accent-blue' : 'bg-text-tertiary'
                                            }`}
                                        />
                                    </div>
                                ) : (
                                    <div className="p-3">
                                        <div className="text-body-sm text-text-primary truncate">
                                            {truncateTitle(conv.title)}
                                        </div>
                                        <div className="text-caption text-text-tertiary mt-1">
                                            {formatRelativeTime(conv.updatedAt)}
                                        </div>
                                    </div>
                                )}
                            </motion.div>
                        ))}
                    </AnimatePresence>

                    {conversations.length === 0 && !isCollapsed && (
                        <div className="p-4 text-center text-text-tertiary text-caption">
                            No conversations yet
                        </div>
                    )}
                </div>

                <div className="p-2 border-t border-bento-border">
                    <button
                        onClick={() => setTrashOpen(true)}
                        className={`w-full flex items-center gap-2 p-2 rounded-lg hover:bg-bento-hover text-text-secondary transition-colors ${
                            isCollapsed ? 'justify-center' : ''
                        }`}
                        title="Trash"
                    >
                        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                            <path
                                d="M2 4H14M5.33333 4V2.66667C5.33333 2.29848 5.63181 2 6 2H10C10.3682 2 10.6667 2.29848 10.6667 2.66667V4M12.6667 4V13.3333C12.6667 13.7015 12.3682 14 12 14H4C3.63181 14 3.33333 13.7015 3.33333 13.3333V4"
                                stroke="currentColor"
                                strokeWidth="1.2"
                                strokeLinecap="round"
                                strokeLinejoin="round"
                            />
                        </svg>
                        {!isCollapsed && <span className="text-caption">Trash</span>}
                    </button>
                </div>
            </motion.aside>

            <AnimatePresence>
                {contextMenu && (
                    <motion.div
                        initial={{ opacity: 0, scale: 0.95 }}
                        animate={{ opacity: 1, scale: 1 }}
                        exit={{ opacity: 0, scale: 0.95 }}
                        style={{ left: contextMenu.x, top: contextMenu.y }}
                        className="fixed z-50 bg-bento-card border border-bento-border rounded-lg shadow-xl py-1 min-w-[120px]"
                    >
                        <button
                            onClick={handleDelete}
                            className="w-full px-3 py-2 text-left text-body-sm text-red-500 hover:bg-red-500/10 transition-colors flex items-center gap-2"
                        >
                            <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                                <path
                                    d="M2 4H12M5 4V3C5 2.44772 5.44772 2 6 2H8C8.55228 2 9 2.44772 9 3V4M11 4V11C11 11.5523 10.5523 12 10 12H4C3.44772 12 3 11.5523 3 11V4"
                                    stroke="currentColor"
                                    strokeWidth="1.2"
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                />
                            </svg>
                            Delete
                        </button>
                    </motion.div>
                )}
            </AnimatePresence>

            <TrashManager
                isOpen={trashOpen}
                onClose={() => setTrashOpen(false)}
                onConversationRestored={onRefreshConversations}
            />
        </>
    );
};

export default ConversationSidebar;
