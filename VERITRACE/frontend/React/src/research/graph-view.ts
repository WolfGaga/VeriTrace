function unwrap(value: unknown): unknown {
  if (value && typeof value === 'object' && !Array.isArray(value) && 'value' in value) {
    return unwrap((value as { value: unknown }).value);
  }
  return value;
}

export function nodeAnimationPhase(id: string | number): number {
  return typeof id === 'number' ? id : Array.from(id).reduce((phase, character) => phase + character.charCodeAt(0), 0);
}

function text(value: unknown): string {
  const content = unwrap(value);
  return typeof content === 'string' ? content : JSON.stringify(content) ?? '';
}

function entries(value: unknown): Array<[string, unknown]> {
  return value && typeof value === 'object' && !Array.isArray(value) ? Object.entries(value) : [];
}

// Historical task attribution stays in stored graph data; only display copies are unwrapped.
export function findingViewFields(entity: Record<string, unknown>) {
  const cross: Record<string, string | string[]> = {};
  for (const [aspect, entry] of entries(entity.cross_item_findings)) {
    const value = unwrap(entry);
    cross[aspect] = Array.isArray(value) ? value.map(text) : text(value);
  }
  return {
    findings: Object.fromEntries(entries(entity.findings).map(([key, value]) => [key, text(value)])),
    item_findings: Object.fromEntries(entries(entity.item_findings).map(([item, attributes]) => [
      item, Object.fromEntries(entries(attributes).map(([attribute, value]) => [attribute, text(value)])),
    ])),
    cross_item_findings: cross,
    unexpected_discoveries: Array.isArray(entity.unexpected_discoveries)
      ? entity.unexpected_discoveries.map(text) : [],
  };
}
