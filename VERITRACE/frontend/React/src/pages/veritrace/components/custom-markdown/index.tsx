import ReactMarkdown from 'react-markdown';
import type { Element, Root, RootContent } from 'hast';
import rehypeRaw from 'rehype-raw';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';
import remarkGfm from 'remark-gfm';
import { replaceStr, mergeReplaceToDiv, safeSourceUrl } from '../../utils/tools';
import { Popover } from 'antd';

const reportSchema: typeof defaultSchema = {
    ...defaultSchema,
    attributes: {
        ...defaultSchema.attributes,
        i: [...(defaultSchema.attributes?.i || []), ['className', 'f', 'custom']],
        span: [...(defaultSchema.attributes?.span || []), ['className', 'mergeQuo'], 'dataIds'],
    },
};

function preserveFragmentLinks() {
    return (tree: Root) => {
        const ids = new Set<string>();
        const links: Element[] = [];
        const visit = (node: Root | RootContent) => {
            if (node.type === 'element') {
                if (typeof node.properties.id === 'string') ids.add(node.properties.id);
                if (node.tagName === 'a') links.push(node);
            }
            if ('children' in node) node.children.forEach(visit);
        };
        visit(tree);
        // Rewrite only links whose target ID was prefixed by sanitization.
        for (const link of links) {
            const href = link.properties.href;
            if (typeof href !== 'string' || !href.startsWith('#') || href.length === 1) continue;
            let id: string;
            try { id = decodeURIComponent(href.slice(1)); } catch { continue; }
            const target = `${reportSchema.clobberPrefix ?? 'user-content-'}${id}`;
            if (!ids.has(id) && ids.has(target)) link.properties.href = `#${encodeURIComponent(target)}`;
        }
    };
}

interface IMarkdownProps {
    source: string;
    refList?: any;
    quoType?: string;
    chatIsOver?: boolean;
}

const CustomMarkdown = ({ source, refList = null, quoType = 'single' }: IMarkdownProps) => {
    const linkToExtend = (url: string) => {
        const target = safeSourceUrl(url);
        if (target) window.open(target, '_blank', 'noopener,noreferrer');
    };

    const CustomI = ({ children, className }: any) => {
        const content = refList
            ? Object.keys(refList).map((item) => {
                if (Number(item) === Number(children)) {
                    return (
                        <li
                            key={`ref-item-${item}`}
                            onClick={() => {
                                linkToExtend(refList[item].url);
                            }}
                            className="cursor-pointer p-3 hover:bg-bento-surfaceHover rounded-lg transition-colors"
                        >
                            <div className="text-caption text-accent-blue truncate mb-1">{refList[item].url}</div>
                            <div className="text-body-sm font-medium text-text-primary mb-1">{refList[item].title}</div>
                            <div className="text-caption text-text-secondary line-clamp-2">{refList[item].summ}</div>
                        </li>
                    );
                } else {
                    return null;
                }
            })
            : null;
        return className?.includes('custom') ? (
            <Popover overlayClassName="iQuoPopover" content={content} arrow={false} key={`iQuoPopover-${children}`}>
                <b className="reference-citation">{children}</b>
            </Popover>
        ) : (
            <code>{children}</code>
        );
    };

    const CustomDiv = ({ children, className, ...props }: any) => {
        const list = props['data-ids']?.split(',') || [];
        const content = refList
            ? Object.keys(refList).map((item) => {
                if (list.includes(String(item))) {
                    return (
                        <div key={`ref-item-${refList[item].title}`}>
                            <li
                                className="cursor-pointer p-2 hover:bg-bento-surfaceHover rounded-lg transition-colors"
                                onClick={() => {
                                    linkToExtend(refList[item].url);
                                }}
                            >
                                <div className="text-caption text-accent-blue truncate mb-1">{refList[item].url}</div>
                                <div className="text-body-sm font-medium text-text-primary">{refList[item].title}</div>
                            </li>
                            <div className="h-px bg-bento-borderLight my-1" />
                        </div>
                    );
                } else {
                    return null;
                }
            })
            : null;
        return className?.includes('mergeQuo') ? (
            <Popover content={content} arrow={false} overlayClassName="mergeQuoPopover" key={`custom-dev-${props['data-ids']}`}>
                <b className="reference-citation inline-flex items-center justify-center">
                    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14" fill="none">
                        <path
                            fillRule="evenodd"
                            clipRule="evenodd"
                            d="M5.49491 2.65877C3.29849 4.06934 2.07828 5.71113 1.83428 7.58413C1.45443 10.4999 4.06198 11.9272 5.3832 10.6448C6.70443 9.36248 5.91243 7.73483 4.95574 7.28993C3.99904 6.84499 3.41399 6.99995 3.51604 6.40539C3.6181 5.81085 4.97919 4.16241 6.17496 3.39472C6.2543 3.32725 6.28448 3.19635 6.2081 3.09704C6.15784 3.03174 6.05929 2.90365 5.91243 2.71278C5.78401 2.54586 5.66099 2.55211 5.49491 2.65877Z"
                            fill="currentColor"
                            fillOpacity="0.5"
                        />
                        <path
                            fillRule="evenodd"
                            clipRule="evenodd"
                            d="M11.2801 2.65877C9.08366 4.06934 7.86344 5.71113 7.61943 7.58413C7.2396 10.4999 9.84715 11.9272 11.1684 10.6448C12.4896 9.36248 11.6976 7.73483 10.7409 7.28993C9.78421 6.84499 9.19913 6.99995 9.30121 6.40539C9.40327 5.81085 10.7644 4.16241 11.9601 3.39472C12.0395 3.32725 12.0697 3.19635 11.9932 3.09704C11.943 3.03174 11.8445 2.90365 11.6976 2.71278C11.5692 2.54586 11.4462 2.55211 11.2801 2.65877Z"
                            fill="currentColor"
                            fillOpacity="0.5"
                        />
                    </svg>
                </b>
            </Popover>
        ) : (
            <code>{children}</code>
        );
    };

    return (
        <div className="prose prose-sm max-w-none">
            <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw, [rehypeSanitize, reportSchema], preserveFragmentLinks]} components={refList && Object.keys(refList)?.length ? { i: CustomI, span: CustomDiv } : {}}>
                {
                    refList ?
                        quoType === 'merge' ? mergeReplaceToDiv(source) :
                            replaceStr(source) :
                        source
                }
            </ReactMarkdown>
        </div>
    );
};

export default CustomMarkdown;
