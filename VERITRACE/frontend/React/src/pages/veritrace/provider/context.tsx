import { createContext } from 'react';

export const VeritraceContext = createContext(
    {} as {
        activeNode: string;
        isEnd: boolean;
        chatIsOver: boolean;
        setActiveNode?: () => void;
    },
);
