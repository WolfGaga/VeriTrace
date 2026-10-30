
import React, { useState, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';

interface DeletedConversation {
    id: string;
    title: string;
    created_at: number;
    updated_at: number;
    deleted_at: number;
}

interface TrashManagerProps {
    isOpen: boolean;
    onClose: () => void;
    onConversationRestored?: () => void;
}

const API_BASE = '/api/admin';

const formatDate = (timestamp: number): string => {
    return new Date(timestamp).toLocaleString();
};

const TrashManager: React.FC<TrashManagerProps> = ({
    isOpen,
    onClose,
    onConversationRestored,
}) => {
    const [deletedConversations, setDeletedConversations] = useState<DeletedConversation[]>([]);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [confirmAction, setConfirmAction] = useState<{
        type: 'delete' | 'empty';
        id?: string;
        title?: string;
    } | null>(null);

    const loadDeletedConversations = useCallback(async () => {
        setIsLoading(true);
        setError(null);
        try {
            const response = await fetch(`${API_BASE}/deleted-conversations`);
            if (response.ok) {
                const data = await response.json();
                setDeletedConversations(data);
            } else {
                setError('Failed to load deleted conversations');
            }
        } catch (e) {
            setError('Network error');
            console.error('[TrashManager] Failed to load:', e);
        } finally {
            setIsLoading(false);
        }
    }, []);

    useEffect(() => {
        if (isOpen) {
            loadDeletedConversations();
        }
    }, [isOpen, loadDeletedConversations]);

    const handleRestore = async (id: string) => {
        try {
            const response = await fetch(`${API_BASE}/conversations/${id}/restore`, {
                method: 'POST',
            });
            if (response.ok) {
                setDeletedConversations((prev) => prev.filter((c) => c.id !== id));
                onConversationRestored?.();
            } else {
                setError('Failed to restore conversation');
            }
        } catch (e) {
            setError('Network error');
            console.error('[TrashManager] Failed to restore:', e);
        }
    };

    const handlePermanentDelete = async (id: string) => {
        try {
            const response = await fetch(`${API_BASE}/conversations/${id}/permanent`, {
                method: 'DELETE',
            });
            if (response.ok) {
                setDeletedConversations((prev) => prev.filter((c) => c.id !== id));
            } else {
                setError('Failed to delete conversation');
            }
        } catch (e) {
            setError('Network error');
            console.error('[TrashManager] Failed to delete:', e);
        }
        setConfirmAction(null);
    };

    const handleEmptyTrash = async () => {
        try {
            const response = await fetch(`${API_BASE}/deleted-conversations/all`, {
                method: 'DELETE',
            });
            if (response.ok) {
                setDeletedConversations([]);
            } else {
                setError('Failed to empty trash');
            }
        } catch (e) {
            setError('Network error');
            console.error('[TrashManager] Failed to empty trash:', e);
        }
        setConfirmAction(null);
    };

    if (!isOpen) return null;

    return (
        <AnimatePresence>
            <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="fixed inset-0 z-50 flex items-center justify-center"
            >
                <div
                    className="absolute inset-0 bg-black/50 backdrop-blur-sm"
                    onClick={onClose}
                />

                <motion.div
                    initial={{ scale: 0.95, opacity: 0 }}
                    animate={{ scale: 1, opacity: 1 }}
                    exit={{ scale: 0.95, opacity: 0 }}
                    role="dialog"
                    aria-label="Trash"
                    className="relative w-full max-w-2xl max-h-[80vh] bg-bento-card border border-bento-border rounded-2xl shadow-2xl overflow-hidden"
                >
                    <div className="flex items-center justify-between px-6 py-4 border-b border-bento-border">
                        <div className="flex items-center gap-3">
                            <div className="w-10 h-10 rounded-xl bg-red-500/10 flex items-center justify-center">
                                <svg width="20" height="20" viewBox="0 0 20 20" fill="none" className="text-red-500">
                                    <path
                                        d="M3 5H17M7 5V3C7 2.44772 7.44772 2 8 2H12C12.5523 2 13 2.44772 13 3V5M15 5V16C15 16.5523 14.5523 17 14 17H6C5.44772 17 5 16.5523 5 16V5"
                                        stroke="currentColor"
                                        strokeWidth="1.5"
                                        strokeLinecap="round"
                                        strokeLinejoin="round"
                                    />
                                </svg>
                            </div>
                            <div>
                                <h2 className="text-h4 text-text-primary">Trash</h2>
                                <p className="text-caption text-text-secondary">
                                    {deletedConversations.length} deleted conversation{deletedConversations.length !== 1 ? 's' : ''}
                                </p>
                            </div>
                        </div>
                        <button
                            onClick={onClose}
                            aria-label="Close trash"
                            className="p-2 rounded-lg hover:bg-bento-hover transition-colors text-text-secondary"
                        >
                            <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
                                <path
                                    d="M15 5L5 15M5 5L15 15"
                                    stroke="currentColor"
                                    strokeWidth="1.5"
                                    strokeLinecap="round"
                                />
                            </svg>
                        </button>
                    </div>

                    <div className="overflow-y-auto max-h-[calc(80vh-140px)] p-4">
                        {error && (
                            <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-500 text-body-sm">
                                {error}
                            </div>
                        )}

                        {isLoading ? (
                            <div className="flex items-center justify-center py-12">
                                <div className="w-8 h-8 border-2 border-accent-blue border-t-transparent rounded-full animate-spin" />
                            </div>
                        ) : deletedConversations.length === 0 ? (
                            <div className="text-center py-12">
                                <div className="w-16 h-16 mx-auto mb-4 rounded-2xl bg-bento-surface flex items-center justify-center">
                                    <svg width="32" height="32" viewBox="0 0 32 32" fill="none" className="text-text-tertiary">
                                        <path
                                            d="M5 9H27M11 9V7C11 5.89543 11.8954 5 13 5H19C20.1046 5 21 5.89543 21 7V9M23 9V25C23 26.1046 22.1046 27 21 27H11C9.89543 27 9 26.1046 9 25V9"
                                            stroke="currentColor"
                                            strokeWidth="2"
                                            strokeLinecap="round"
                                            strokeLinejoin="round"
                                        />
                                    </svg>
                                </div>
                                <p className="text-body text-text-secondary">Trash is empty</p>
                            </div>
                        ) : (
                            <div className="space-y-2">
                                {deletedConversations.map((conv) => (
                                    <div
                                        key={conv.id}
                                        className="p-4 rounded-xl bg-bento-surface border border-bento-borderLight hover:border-bento-border transition-colors"
                                    >
                                        <div className="flex items-start justify-between gap-4">
                                            <div className="flex-1 min-w-0">
                                                <h3 className="text-body text-text-primary truncate">
                                                    {conv.title}
                                                </h3>
                                                <p className="text-caption text-text-tertiary mt-1">
                                                    Deleted: {formatDate(conv.deleted_at)}
                                                </p>
                                            </div>
                                            <div className="flex items-center gap-2">
                                                <button
                                                    onClick={() => handleRestore(conv.id)}
                                                    className="px-3 py-1.5 rounded-lg bg-accent-blue/10 hover:bg-accent-blue/20 text-accent-blue text-caption font-medium transition-colors"
                                                >
                                                    Restore
                                                </button>
                                                <button
                                                    onClick={() => setConfirmAction({ type: 'delete', id: conv.id, title: conv.title })}
                                                    className="px-3 py-1.5 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-red-500 text-caption font-medium transition-colors"
                                                >
                                                    Delete
                                                </button>
                                            </div>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>

                    {deletedConversations.length > 0 && (
                        <div className="px-6 py-4 border-t border-bento-border flex justify-end">
                            <button
                                onClick={() => setConfirmAction({ type: 'empty' })}
                                className="px-4 py-2 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-red-500 text-body-sm font-medium transition-colors"
                            >
                                Empty Trash
                            </button>
                        </div>
                    )}
                </motion.div>

                <AnimatePresence>
                    {confirmAction && (
                        <motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            className="absolute inset-0 z-10 flex items-center justify-center"
                        >
                            <div className="absolute inset-0 bg-black/30" onClick={() => setConfirmAction(null)} />
                            <motion.div
                                initial={{ scale: 0.95 }}
                                animate={{ scale: 1 }}
                                exit={{ scale: 0.95 }}
                                role="dialog"
                                aria-label={confirmAction.type === 'empty' ? 'Empty Trash?' : 'Permanently Delete?'}
                                className="relative bg-bento-card border border-bento-border rounded-xl shadow-xl p-6 max-w-sm"
                            >
                                <h3 className="text-h4 text-text-primary mb-2">
                                    {confirmAction.type === 'empty' ? 'Empty Trash?' : 'Permanently Delete?'}
                                </h3>
                                <p className="text-body-sm text-text-secondary mb-4">
                                    {confirmAction.type === 'empty'
                                        ? `This will permanently delete all ${deletedConversations.length} conversations. This action cannot be undone.`
                                        : `This will permanently delete "${confirmAction.title}". This action cannot be undone.`}
                                </p>
                                <div className="flex justify-end gap-2">
                                    <button
                                        onClick={() => setConfirmAction(null)}
                                        className="px-4 py-2 rounded-lg bg-bento-surface hover:bg-bento-hover text-text-secondary text-body-sm font-medium transition-colors"
                                    >
                                        Cancel
                                    </button>
                                    <button
                                        onClick={() => {
                                            if (confirmAction.type === 'empty') {
                                                handleEmptyTrash();
                                            } else if (confirmAction.id) {
                                                handlePermanentDelete(confirmAction.id);
                                            }
                                        }}
                                        className="px-4 py-2 rounded-lg bg-red-500 hover:bg-red-600 text-white text-body-sm font-medium transition-colors"
                                    >
                                        Delete Forever
                                    </button>
                                </div>
                            </motion.div>
                        </motion.div>
                    )}
                </AnimatePresence>
            </motion.div>
        </AnimatePresence>
    );
};

export default TrashManager;
