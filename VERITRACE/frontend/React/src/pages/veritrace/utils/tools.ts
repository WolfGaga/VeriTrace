export const mergeReplaceToDiv = (str: string) => {
    // Group adjacent citations into one reference popover.
    const regexOptimized = /\[\[(\d+)\]](?:\s*\[\[(\d+)\]])*/g;

    function replaceWithDivSpanOptimized(match: any) {
        const numbers = (match.match(/\d+/g) || []).map(Number);
        return `<span class="mergeQuo" data-ids="${numbers.toString()}">${numbers.map((num: number) => `<span>${num}</span>`).join('')}</span>`;
    }

    const resultOptimized = str.replace(regexOptimized, replaceWithDivSpanOptimized);

    return resultOptimized;
};

export const replaceStr = (str: string) => {
    return str.replace(/\[\[(\d+)\]\]/g, (_match: any, number: any) => {
        return `<i class='f custom'>${number}</i>`;
    });
};

export const safeSourceUrl = (value: unknown): string | undefined => {
    if (typeof value !== 'string') return;
    try {
        const url = new URL(value);
        return url.protocol === 'http:' || url.protocol === 'https:' ? value : undefined;
    } catch {
        return;
    }
};
