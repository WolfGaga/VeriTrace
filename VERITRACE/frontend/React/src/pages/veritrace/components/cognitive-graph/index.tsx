import { motion, AnimatePresence } from 'framer-motion';
import { useMemo, useState, useRef, useEffect, useCallback, createContext, useContext } from 'react';
import { findingViewFields } from '@/research/graph-view';

// i18n Translations

type Language = 'zh' | 'en';

const translations = {
  zh: {
    cognitiveGraph: '认知图 · 系统大脑',
    subtitle: '知识认知 · 行为经验 · 时间感知',
    perceptions: '次感知',

    buildingCognition: '构建认知结构...',
    noCognitiveData: '暂无认知数据',

    cognitiveState: '认知状态:',
    confirmed: '已确认',
    partialKnown: '部分已知',
    toExplore: '待探索',
    clickForDetails: '点击查看详情',

    serialStructure: '串行结构',
    parallelStructure: '并行结构',
    convergenceStructure: '汇聚结构',
    mixedStructure: '混合结构',

    core: '核心',
    hop: 'Hop',

    planner: 'Planner',
    planCognitionGoal: '规划认知目标',
    searcher: 'Searcher',
    perceiveWorld: '感知外部世界',
    updateCognition: '更新认知',
    multiAgentCycle: '多智能体认知循环',

    knowledgeNode: '知识节点',
    explorationExperience: '探索经验',

    hopDistance: '跳数',
    discoveredValues: '发现的值',
    itemAttributes: '项目属性',
    crossItemInsights: '跨项目洞察',
    knowledgeCognition: '知识认知',
    pendingVerification: '待验证要点',
    corePending: '核心待验收',
    supplementaryPending: '补充待验收',
    unexpectedDiscoveries: '意外发现',
    timePerception: '时间感知',
    infoSources: '📚 信息来源',
    citedSources: '引用',
    sources: '个来源',
    needDeepRead: '需深入阅读',
    sourceQuality: '📊 源质量',
    infoDensity: 'CR',
    trustworthiness: 'AAP',
    pages: '页',

    explorationGoal: '🎯 探索目标',
    solved: '已解决',
    investigating: '调查中',
    exploring: '待探索',
    exhausted: '已耗尽',
    known: '已知',
    unknown: '未知',
    attempts: '尝试',
    times: '次',
    searcherPerceptionJourney: 'Searcher 感知历程',
    waitingForTask: '等待 Planner 分配感知任务...',
    multiAgentCollab: '💡 多智能体协作:',
    collabExplanation: '大脑(Planner)识别认知空缺 → 调度感知器(Searcher)探索外部世界 → 接收反馈更新认知 → 根据新认知重新规划行动',

    searchExperience: '🚫 搜索经验:',

    feedbackPerception: '🔄 反馈感知:',
    infoObtained: '✓ 信息已获取',
    partialObtained: '◐ 部分获取',
    notFound: '✗ 未找到',
    uncertain: '? 不确定',
    suggestion: '建议:',
    refineQuery: '优化查询',
    tryAlternative: '尝试替代方案',
    expandScope: '扩大搜索范围',
    acceptPartial: '接受部分结果',

    langToggle: '中/EN',
  },
  en: {
    cognitiveGraph: 'Cognitive Graph · System Brain',
    subtitle: 'Knowledge · Experience · Time Perception',
    perceptions: ' perceptions',

    buildingCognition: 'Building cognitive structure...',
    noCognitiveData: 'No cognitive data available',

    cognitiveState: 'State:',
    confirmed: 'Confirmed',
    partialKnown: 'Partial',
    toExplore: 'Unknown',
    clickForDetails: 'Click for details',

    serialStructure: 'Serial',
    parallelStructure: 'Parallel',
    convergenceStructure: 'Convergent',
    mixedStructure: 'Mixed',

    core: 'Core',
    hop: 'Hop',

    planner: 'Planner',
    planCognitionGoal: 'Plan goals',
    searcher: 'Searcher',
    perceiveWorld: 'Perceive world',
    updateCognition: 'Update',
    multiAgentCycle: 'Multi-Agent Cognitive Loop',

    knowledgeNode: 'Knowledge Node',
    explorationExperience: 'Exploration Experience',

    hopDistance: 'Hops',
    discoveredValues: 'Discovered Values',
    itemAttributes: 'Item Attributes',
    crossItemInsights: 'Cross-Item Insights',
    knowledgeCognition: 'Knowledge Cognition',
    pendingVerification: 'Pending Verification',
    corePending: 'Core Pending',
    supplementaryPending: 'Supplementary Pending',
    unexpectedDiscoveries: 'Unexpected Discoveries',
    timePerception: 'Temporal Notes',
    infoSources: '📚 Information Sources',
    citedSources: 'Cited',
    sources: ' sources',
    needDeepRead: 'Need deep read',
    sourceQuality: '📊 Source Quality',
    infoDensity: 'CR',
    trustworthiness: 'AAP',
    pages: 'pages',

    explorationGoal: '🎯 Exploration Goal',
    solved: 'Solved',
    investigating: 'Investigating',
    exploring: 'To Explore',
    exhausted: 'Exhausted',
    known: 'Known',
    unknown: 'Unknown',
    attempts: 'Attempt',
    times: '',
    searcherPerceptionJourney: 'Searcher Perception Journey',
    waitingForTask: 'Waiting for Planner to assign task...',
    multiAgentCollab: '💡 Multi-Agent Collaboration:',
    collabExplanation: 'Brain (Planner) identifies knowledge gaps → Dispatches Searcher to explore → Receives feedback → Replans based on new cognition',

    searchExperience: '🚫 Search Experience:',

    feedbackPerception: '🔄 Feedback:',
    infoObtained: '✓ Found',
    partialObtained: '◐ Partial',
    notFound: '✗ Not Found',
    uncertain: '? Uncertain',
    suggestion: 'Suggest:',
    refineQuery: 'Refine query',
    tryAlternative: 'Try alternative',
    expandScope: 'Expand scope',
    acceptPartial: 'Accept partial',

    langToggle: '中/EN',
  }
};

const LanguageContext = createContext<{
  lang: Language;
  t: typeof translations.zh;
  toggleLang: () => void;
}>({
  lang: 'zh',
  t: translations.zh,
  toggleLang: () => {},
});

const useLanguage = () => useContext(LanguageContext);

// Types

interface CognitiveEntity {
  id: string;
  name: string;
  node_type: 'start' | 'placeholder' | 'discovered';
  cognitive_state: 'START' | 'KNOWN' | 'PARTIAL' | 'UNKNOWN';
  type_constraint?: string;
  condition_constraints?: string[];
  hop_distance: number;
  discovery_dependency?: string;
  discovered_items?: string[];
  quality_profile?: {
    cr_mean?: number;
    aap_mean?: number;
    page_count?: number;
    finding_strength?: string;
    unexpected_strength?: string;
    accessibility_barriers?: string[];
    ref_quality?: Record<string, { cr: number; aap: number }>;
  };
  pending_criteria?: string[];  // Deprecated; retained for saved conversations.
  core_pending?: string[];  // Unresolved core criteria.
  supplementary_pending?: string[];  // Unresolved supplementary criteria.
  unexpected_discoveries?: string[];
  findings?: Record<string, string>;  // Legacy findings format.
  item_findings?: Record<string, Record<string, string>>;  // Item -> attribute -> value.
  cross_item_findings?: Record<string, string | string[]>;  // Aspect -> description or ranked list.
  cited_refs?: number[];
  need_detail_refs?: number[];
  temporal_notes?: string;
}

interface SearchAttempt {
  query: string;
  summary: string;
  node_name?: string;
  experience?: string[];          // Access failures or confirmed information gaps.
  feedback?: {
    // Searcher feedback payload.
    availability?: string;        // "found" | "partial" | "not_found" | "uncertain"
    difficulty_reason?: string;
    suggested_action?: string;    // "none" | "refine_query" | "try_alternative" | "expand_scope" | "accept_partial"
    action_detail?: string;
  };
}

interface CognitiveRelation {
  id: string;
  source: string;
  target: string;
  relation_type: string;
  inquiry_goal: string;
  state: 'known' | 'to_solve' | 'investigating' | 'solved' | 'exhausted';
  confidence?: number;
  attempt_count?: number;
  max_attempts?: number;
  search_history?: SearchAttempt[];
}

interface CognitiveGraphData {
  entities: CognitiveEntity[];
  relations: CognitiveRelation[];
  structure?: {
    type: 'serial' | 'parallel' | 'convergence' | 'mixed';
    parallel_chains?: string[][];
    convergence_nodes?: string[];
    dependency_order?: string[];
  };
  original_question?: string;
}

interface BackendCognitiveGraph {
  nodes?: Record<string, any>;
  edges?: Record<string, any>;
  structure?: any;
  languages?: string[];
  original_question?: string;
  summary?: any;
  entities?: CognitiveEntity[];
  relations?: CognitiveRelation[];
}

interface CognitiveGraphProps {
  data?: BackendCognitiveGraph;
  isLoading?: boolean;
}

interface ConnectionInfo {
  id: string;
  relation: CognitiveRelation;
  x1: number; y1: number;
  x2: number; y2: number;
  mx: number; my: number;
}

// Data Normalization

function normalizeGraphData(data: BackendCognitiveGraph | undefined): CognitiveGraphData | null {
  if (!data) return null;

  if (data.entities && Array.isArray(data.entities)) {
    return {
      entities: data.entities.map(entity => ({ ...entity, ...findingViewFields({ ...entity }) })),
      relations: data.relations || [],
      structure: data.structure,
      original_question: data.original_question,
    };
  }

  if (data.nodes && typeof data.nodes === 'object') {
    const entities: CognitiveEntity[] = Object.entries(data.nodes).map(([id, node]: [string, any]) => ({
      id: node.id || id,
      name: node.name || id,
      node_type: node.node_type || 'start',
      cognitive_state: node.cognitive_state || 'START',
      type_constraint: node.type_constraint,
      condition_constraints: node.condition_constraints || [],
      hop_distance: node.hop_distance || 0,
      discovery_dependency: node.discovery_dependency,
      discovered_items: node.discovered_items || [],
      quality_profile: node.quality_profile,
      pending_criteria: node.pending_criteria || [],
      core_pending: node.core_pending || [],
      supplementary_pending: node.supplementary_pending || [],
      ...findingViewFields(node),
      cited_refs: node.cited_refs || [],
      need_detail_refs: node.need_detail_refs || [],
      temporal_notes: node.temporal_notes,
    }));

    const relations: CognitiveRelation[] = data.edges
      ? Object.entries(data.edges).map(([id, edge]: [string, any]) => ({
          id: edge.id || id,
          source: edge.source_node_id || edge.source || '',
          target: edge.target_node_id || edge.target || '',
          relation_type: edge.relation_type || '',
          inquiry_goal: edge.inquiry_goal || '',
          state: edge.state || 'to_solve',
          confidence: edge.confidence,
          attempt_count: edge.attempt_count || 0,
          max_attempts: edge.max_attempts || 3,
          search_history: edge.search_history || [],
        }))
      : [];

    return {
      entities,
      relations,
      structure: data.structure ? {
        type: data.structure.structure_type || data.structure.type || 'serial',
        parallel_chains: data.structure.parallel_chains || [],
        convergence_nodes: data.structure.convergence_nodes || [],
        dependency_order: data.structure.dependency_order || [],
      } : undefined,
      original_question: data.original_question,
    };
  }

  return null;
}

// Icons

const BrainIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
    <path d="M12 4.5C7 4.5 3 7.5 3 12c0 2.5 1 4.5 3 6v2h12v-2c2-1.5 3-3.5 3-6 0-4.5-4-7.5-9-7.5z" />
    <path d="M9 9c0-1 1-2 3-2s3 1 3 2" />
    <path d="M9 14c0 1 1 2 3 2s3-1 3-2" />
    <path d="M12 7v10" />
  </svg>
);

const CloseIcon = () => (
  <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M4 4L14 14M14 4L4 14" />
  </svg>
);

const KnowledgeIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />
  </svg>
);

const ExperienceIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <circle cx="12" cy="12" r="10" />
    <polyline points="12 6 12 12 16 14" />
  </svg>
);

const PerceptionIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
    <circle cx="12" cy="12" r="3" />
  </svg>
);

const SearcherIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <circle cx="11" cy="11" r="8" />
    <path d="M21 21l-4.35-4.35" />
  </svg>
);

// Detail Modal

type DetailType = 'entity' | 'relation' | null;

interface DetailModalProps {
  type: DetailType;
  entity?: CognitiveEntity;
  relation?: CognitiveRelation;
  entities?: CognitiveEntity[];
  onClose: () => void;
}

const DetailModal = ({ type, entity, relation, entities, onClose }: DetailModalProps) => {
  const { t } = useLanguage();
  if (!type) return null;

  const findEntity = (id: string) => entities?.find(e => e.id === id);

  return (
    <motion.div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      onClick={onClose}
    >
      <motion.div
        className="bg-bento-surface border border-bento-border rounded-xl shadow-2xl w-full max-w-lg max-h-[80vh] overflow-hidden"
        role="dialog"
        aria-label={type === 'entity' ? 'Cognitive entity details' : 'Cognitive relation details'}
        initial={{ scale: 0.9, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.9, opacity: 0 }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between p-4 border-b border-bento-border bg-gradient-to-r from-accent-blue/10 to-accent-purple/10">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-gradient-to-br from-accent-blue to-accent-purple flex items-center justify-center">
              {type === 'entity' ? <KnowledgeIcon /> : <ExperienceIcon />}
            </div>
            <div>
              <h3 className="font-medium text-text-primary">
                {type === 'entity' ? t.knowledgeNode : t.explorationExperience}
              </h3>
              <p className="text-xs text-text-muted">
                {type === 'entity' ? entity?.name : relation?.relation_type}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close graph details"
            className="p-2 hover:bg-bento-surfaceHover rounded-lg transition-colors"
          >
            <CloseIcon />
          </button>
        </div>

        <div className="p-4 overflow-y-auto max-h-[calc(80vh-80px)]">
          {type === 'entity' && entity && (
            <EntityDetail entity={entity} />
          )}
          {type === 'relation' && relation && (
            <RelationDetail relation={relation} findEntity={findEntity} />
          )}
        </div>
      </motion.div>
    </motion.div>
  );
};

// Entity Detail

const EntityDetail = ({ entity }: { entity: CognitiveEntity }) => {
  const { t } = useLanguage();
  // Support both legacy findings and item/cross-item findings.
  const hasItemFindings = entity.item_findings && Object.keys(entity.item_findings).length > 0;
  const hasCrossItemFindings = entity.cross_item_findings && Object.keys(entity.cross_item_findings).length > 0;
  const hasLegacyFindings = entity.findings && Object.keys(entity.findings).length > 0;
  const stateLabel = {
    START: { text: 'Start', color: 'text-blue-400', bg: 'bg-blue-400/10' },
    KNOWN: { text: t.confirmed, color: 'text-accent-green', bg: 'bg-accent-green/10' },
    PARTIAL: { text: t.partialKnown, color: 'text-amber-500', bg: 'bg-amber-500/10' },
    UNKNOWN: { text: t.toExplore, color: 'text-gray-400', bg: 'bg-gray-400/10' },
  }[entity.cognitive_state] || { text: t.unknown, color: 'text-gray-400', bg: 'bg-gray-400/10' };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <span className={`px-3 py-1 rounded-full text-xs font-medium ${stateLabel.bg} ${stateLabel.color}`}>
          {stateLabel.text}
        </span>
        <span className="px-3 py-1 rounded-full text-xs bg-bento-surfaceHover text-text-muted">
          {t.hopDistance}: {entity.hop_distance}
        </span>
        {entity.type_constraint && (
          <span className="px-3 py-1 rounded-full text-xs bg-accent-blue/10 text-accent-blue">
            {entity.type_constraint}
          </span>
        )}
      </div>

      {entity.discovered_items && entity.discovered_items.length > 0 && (
        <div className="bg-accent-green/5 border border-accent-green/20 rounded-lg p-3">
          <div className="text-xs font-medium text-accent-green mb-2 flex items-center gap-2">
            <span className="w-5 h-5 rounded-full bg-accent-green/20 flex items-center justify-center">✓</span>
            {t.discoveredValues}
          </div>
          <div className="flex flex-wrap gap-2">
            {entity.discovered_items.map((val, idx) => (
              <span key={idx} className="px-2 py-1 bg-accent-green/10 text-accent-green text-sm rounded">
                {val}
              </span>
            ))}
          </div>
        </div>
      )}

      {hasItemFindings && (
        <div className="bg-accent-blue/5 border border-accent-blue/20 rounded-lg p-3">
          <div className="text-xs font-medium text-accent-blue mb-3 flex items-center gap-2">
            <span className="w-5 h-5 rounded-full bg-accent-blue/20 flex items-center justify-center text-[10px]">📊</span>
            {t.itemAttributes}
          </div>
          <div className="space-y-3">
            {Object.entries(entity.item_findings!).map(([entityName, attrs], idx) => (
              <div key={idx} className="bg-bento-surface rounded p-2">
                <div className="text-[11px] text-accent-blue font-semibold mb-2 border-b border-accent-blue/10 pb-1">
                  {entityName}
                </div>
                <div className="space-y-1">
                  {Object.entries(attrs).map(([attrName, attrValue], attrIdx) => (
                    <div key={attrIdx} className="flex">
                      <span className="text-[10px] text-text-muted w-24 shrink-0">{attrName}:</span>
                      <span className="text-xs text-text-secondary leading-relaxed">
                        {typeof attrValue === 'string' ? attrValue : JSON.stringify(attrValue)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {hasCrossItemFindings && (
        <div className="bg-purple-500/5 border border-purple-500/20 rounded-lg p-3">
          <div className="text-xs font-medium text-purple-400 mb-3 flex items-center gap-2">
            <span className="w-5 h-5 rounded-full bg-purple-500/20 flex items-center justify-center text-[10px]">💡</span>
            {t.crossItemInsights}
          </div>
          <div className="space-y-2">
            {Object.entries(entity.cross_item_findings!).map(([aspect, description], idx) => (
              <div key={idx} className="bg-bento-surface rounded p-2">
                <div className="text-[11px] text-purple-400 font-medium mb-1">{aspect}</div>
                {Array.isArray(description) ? (
                  <ol className="text-xs text-text-secondary leading-relaxed list-decimal list-inside space-y-0.5">
                    {description.map((item, i) => (
                      <li key={i}>{item}</li>
                    ))}
                  </ol>
                ) : (
                  <div className="text-xs text-text-secondary leading-relaxed">
                    {typeof description === 'string' ? description : JSON.stringify(description)}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Legacy findings compatibility. */}
      {hasLegacyFindings && !hasItemFindings && !hasCrossItemFindings && (
        <div className="bg-accent-blue/5 border border-accent-blue/20 rounded-lg p-3">
          <div className="text-xs font-medium text-accent-blue mb-3 flex items-center gap-2">
            <KnowledgeIcon /> {t.knowledgeCognition}
          </div>
          <div className="space-y-2">
            {Object.entries(entity.findings!).map(([key, value], idx) => (
              <div key={idx} className="bg-bento-surface rounded p-2">
                <div className="text-[11px] text-accent-blue font-medium mb-1">{key}</div>
                <div className="text-xs text-text-secondary leading-relaxed">
                  {typeof value === 'string' ? value : JSON.stringify(value)}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {entity.core_pending && entity.core_pending.length > 0 && (
        <div className="bg-red-500/5 border border-red-500/20 rounded-lg p-3">
          <div className="text-xs font-medium text-red-500 mb-2 flex items-center gap-2">
            <PerceptionIcon /> 🔴 {t.corePending || '核心待验收'}
          </div>
          <ul className="space-y-1">
            {entity.core_pending.map((criteria, idx) => (
              <li key={idx} className="text-xs text-text-secondary flex items-start gap-2">
                <span className="text-red-500 mt-0.5">●</span>
                {criteria}
              </li>
            ))}
          </ul>
        </div>
      )}

      {entity.supplementary_pending && entity.supplementary_pending.length > 0 && (
        <div className="bg-amber-500/5 border border-amber-500/20 rounded-lg p-3">
          <div className="text-xs font-medium text-amber-500 mb-2 flex items-center gap-2">
            <PerceptionIcon /> 🟡 {t.supplementaryPending || '补充待验收'}
          </div>
          <ul className="space-y-1">
            {entity.supplementary_pending.map((criteria, idx) => (
              <li key={idx} className="text-xs text-text-secondary flex items-start gap-2">
                <span className="text-amber-500 mt-0.5">○</span>
                {criteria}
              </li>
            ))}
          </ul>
        </div>
      )}

      {entity.unexpected_discoveries && entity.unexpected_discoveries.length > 0 && (
        <div className="bg-cyan-500/5 border border-cyan-500/20 rounded-lg p-3">
          <div className="text-xs font-medium text-cyan-500 mb-2 flex items-center gap-2">
            💡 {t.unexpectedDiscoveries || '意外发现'}
          </div>
          <ul className="space-y-1">
            {entity.unexpected_discoveries.map((discovery, idx) => (
              <li key={idx} className="text-xs text-text-secondary flex items-start gap-2">
                <span className="text-cyan-500 mt-0.5">★</span>
                {discovery}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Legacy pending criteria compatibility. */}
      {entity.pending_criteria && entity.pending_criteria.length > 0 &&
       !entity.core_pending?.length && !entity.supplementary_pending?.length && (
        <div className="bg-amber-500/5 border border-amber-500/20 rounded-lg p-3">
          <div className="text-xs font-medium text-amber-500 mb-2 flex items-center gap-2">
            <PerceptionIcon /> {t.pendingVerification}
          </div>
          <ul className="space-y-1">
            {entity.pending_criteria.map((criteria, idx) => (
              <li key={idx} className="text-xs text-text-secondary flex items-start gap-2">
                <span className="text-amber-500 mt-0.5">○</span>
                {criteria}
              </li>
            ))}
          </ul>
        </div>
      )}

      {entity.temporal_notes && (
        <div className="bg-purple-500/5 border border-purple-500/20 rounded-lg p-3">
          <div className="text-xs font-medium text-purple-500 mb-2 flex items-center gap-2">
            <ExperienceIcon /> {t.timePerception}
          </div>
          <p className="text-xs text-text-secondary">
            {typeof entity.temporal_notes === 'string'
              ? entity.temporal_notes
              : JSON.stringify(entity.temporal_notes)}
          </p>
        </div>
      )}

      {/* References: scatter plot CR×AAP */}
      {entity.cited_refs && entity.cited_refs.length > 0 && (() => {
        const refQuality = entity.quality_profile?.ref_quality;
        const hasQuality = refQuality && entity.cited_refs.some(r => refQuality[String(r)]);
        const W = 200, H = 140, PAD = { top: 12, right: 12, bottom: 22, left: 28 };
        const plotW = W - PAD.left - PAD.right;
        const plotH = H - PAD.top - PAD.bottom;
        const toX = (cr: number) => PAD.left + ((cr - 1) / 4) * plotW;
        const toY = (aap: number) => PAD.top + plotH - ((aap - 1) / 4) * plotH;

        return (
          <div className="bg-bento-surfaceHover rounded-lg p-3">
            <div className="text-xs font-medium text-text-muted mb-2">{t.infoSources}</div>
            {hasQuality ? (
              <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: 160 }}>
                {[1, 2, 3, 4, 5].map(v => (
                  <g key={v}>
                    <line x1={toX(v)} y1={PAD.top} x2={toX(v)} y2={PAD.top + plotH}
                      stroke="currentColor" strokeOpacity={0.08} strokeWidth={0.5} />
                    <line x1={PAD.left} y1={toY(v)} x2={PAD.left + plotW} y2={toY(v)}
                      stroke="currentColor" strokeOpacity={0.08} strokeWidth={0.5} />
                  </g>
                ))}
                {[1, 3, 5].map(v => (
                  <g key={`label-${v}`}>
                    <text x={toX(v)} y={H - 4} textAnchor="middle"
                      className="fill-text-muted" fontSize={8}>{v}</text>
                    <text x={PAD.left - 6} y={toY(v) + 3} textAnchor="end"
                      className="fill-text-muted" fontSize={8}>{v}</text>
                  </g>
                ))}
                <text x={PAD.left + plotW / 2} y={H} textAnchor="middle"
                  className="fill-text-muted" fontSize={7}>CR</text>
                <text x={4} y={PAD.top + plotH / 2} textAnchor="middle"
                  className="fill-text-muted" fontSize={7}
                  transform={`rotate(-90, 4, ${PAD.top + plotH / 2})`}>AAP</text>
                {entity.cited_refs.map((ref) => {
                  const rq = refQuality?.[String(ref)];
                  if (!rq) return null;
                  const cx = toX(rq.cr);
                  const cy = toY(rq.aap);
                  const avg = (rq.cr + rq.aap) / 2;
                  const color = avg >= 3.5 ? '#3b82f6' : avg >= 2.5 ? '#f59e0b' : '#ef4444';
                  return (
                    <g key={ref}>
                      <circle cx={cx} cy={cy} r={10} fill={color} fillOpacity={0.15}
                        stroke={color} strokeWidth={1} />
                      <text x={cx} y={cy + 3} textAnchor="middle"
                        fill={color} fontSize={7} fontWeight={600}>{ref}</text>
                      <title>[[{ref}]] CR={rq.cr.toFixed(1)} AAP={rq.aap.toFixed(1)}</title>
                    </g>
                  );
                })}
              </svg>
            ) : (
              <div className="flex flex-wrap gap-1">
                {entity.cited_refs.map((ref) => (
                  <span key={ref} className="text-xs text-text-primary font-medium">[[{ref}]]</span>
                ))}
              </div>
            )}
            {entity.need_detail_refs && entity.need_detail_refs.length > 0 && (
              <div className="text-xs text-accent-blue mt-1">
                {t.needDeepRead}: [{entity.need_detail_refs.join(', ')}]
              </div>
            )}
          </div>
        );
      })()}

      {/* Quality Profile: CR/AAP dual bars */}
      {entity.quality_profile && (entity.quality_profile.cr_mean || entity.quality_profile.aap_mean) && (
        <div className="space-y-2">
          <div className="text-xs font-medium text-text-muted">{t.sourceQuality}</div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-text-muted w-8">{t.infoDensity}</span>
            <div className="flex-1 h-2 bg-bento-surfaceHover rounded-full overflow-hidden">
              <motion.div
                className="h-full bg-gradient-to-r from-accent-blue to-accent-blue/60 rounded-full"
                initial={{ width: 0 }}
                animate={{ width: `${Math.min((entity.quality_profile.cr_mean || 0) * 20, 100)}%` }}
              />
            </div>
            <span className="text-xs font-medium text-text-primary w-6">
              {(entity.quality_profile.cr_mean || 0).toFixed(1)}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-text-muted w-8">{t.trustworthiness}</span>
            <div className="flex-1 h-2 bg-bento-surfaceHover rounded-full overflow-hidden">
              <motion.div
                className="h-full bg-gradient-to-r from-amber-400 to-amber-400/60 rounded-full"
                initial={{ width: 0 }}
                animate={{ width: `${Math.min((entity.quality_profile.aap_mean || 0) * 20, 100)}%` }}
              />
            </div>
            <span className="text-xs font-medium text-text-primary w-6">
              {(entity.quality_profile.aap_mean || 0).toFixed(1)}
            </span>
          </div>
          <div className="text-xs text-text-muted text-right">
            {entity.quality_profile.page_count || 0} {t.pages}
          </div>
        </div>
      )}
    </div>
  );
};

// Relation Detail

const RelationDetail = ({
  relation,
  findEntity
}: {
  relation: CognitiveRelation;
  findEntity: (id: string) => CognitiveEntity | undefined;
}) => {
  const { t } = useLanguage();
  const sourceEntity = findEntity(relation.source);
  const targetEntity = findEntity(relation.target);
  const hasHistory = relation.search_history && relation.search_history.length > 0;

  const stateInfo = {
    solved: { text: t.solved, color: 'text-accent-green', bg: 'bg-accent-green/10', icon: '✓' },
    investigating: { text: t.investigating, color: 'text-accent-blue', bg: 'bg-accent-blue/10', icon: '🔄' },
    to_solve: { text: t.exploring, color: 'text-amber-500', bg: 'bg-amber-500/10', icon: '◐' },
    exhausted: { text: t.exhausted, color: 'text-red-400', bg: 'bg-red-400/10', icon: '✗' },
    known: { text: t.known, color: 'text-gray-400', bg: 'bg-gray-400/10', icon: '○' },
  }[relation.state] || { text: t.unknown, color: 'text-gray-400', bg: 'bg-gray-400/10', icon: '?' };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-center gap-3 py-3">
        <div className="px-3 py-2 bg-accent-blue/10 rounded-lg text-sm text-accent-blue font-medium">
          {sourceEntity?.name || relation.source}
        </div>
        <div className="flex flex-col items-center">
          <svg width="60" height="20" className="text-text-muted">
            <line x1="0" y1="10" x2="50" y2="10" stroke="currentColor" strokeWidth="2" strokeDasharray={relation.state === 'to_solve' ? '4,4' : undefined} />
            <polygon points="50,5 60,10 50,15" fill="currentColor" />
          </svg>
          <span className="text-[10px] text-text-muted mt-1">{relation.relation_type}</span>
        </div>
        <div className="px-3 py-2 bg-accent-purple/10 rounded-lg text-sm text-accent-purple font-medium">
          {targetEntity?.name || relation.target}
        </div>
      </div>

      <div className="bg-bento-surfaceHover rounded-lg p-3">
        <div className="text-xs font-medium text-text-muted mb-1">{t.explorationGoal}</div>
        <p className="text-sm text-text-primary">{relation.inquiry_goal}</p>
      </div>

      <div className="flex items-center gap-3">
        <span className={`px-3 py-1 rounded-full text-xs font-medium ${stateInfo.bg} ${stateInfo.color}`}>
          {stateInfo.icon} {stateInfo.text}
        </span>
        <span className="text-xs text-text-muted">
          {t.attempts} {relation.attempt_count || 0} / {relation.max_attempts || 3} {t.times}
        </span>
      </div>

      <div className="bg-gradient-to-br from-accent-blue/5 to-accent-purple/5 border border-accent-blue/20 rounded-lg p-4">
        <div className="text-xs font-medium text-accent-blue mb-4 flex items-center gap-2">
          <div className="w-6 h-6 rounded-full bg-accent-blue/20 flex items-center justify-center">
            <SearcherIcon />
          </div>
          {t.searcherPerceptionJourney}
        </div>

        {hasHistory ? (
          <div className="relative ml-3">
            <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-gradient-to-b from-accent-blue to-accent-purple" />

            <div className="space-y-4">
              {relation.search_history!.map((attempt, idx) => (
                <motion.div
                  key={idx}
                  className="relative pl-6"
                  initial={{ opacity: 0, x: -10 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: idx * 0.1 }}
                >
                  <div className={`absolute left-[-4px] top-2 w-[10px] h-[10px] rounded-full border-2
                    ${idx === relation.search_history!.length - 1
                      ? 'bg-accent-blue border-accent-blue'
                      : 'bg-bento-surface border-accent-purple'}`}
                  />

                  <div className="bg-bento-surface rounded-lg p-3 border border-bento-borderLight">
                    <div className="flex items-start gap-2 mb-2">
                      <span className="text-accent-blue text-xs">🔍</span>
                      <div className="text-xs text-text-primary font-medium flex-1">{attempt.query}</div>
                    </div>

                    {attempt.node_name && (
                      <div className="text-[10px] text-text-muted mb-2 flex items-center gap-1">
                        <span className="w-4 h-4 rounded bg-accent-purple/20 flex items-center justify-center text-[8px]">🤖</span>
                        {attempt.node_name}
                      </div>
                    )}

                    {attempt.summary && (
                      <div className="text-[11px] text-text-secondary bg-bento-surfaceHover p-2 rounded mb-2">
                        {attempt.summary}
                      </div>
                    )}

                    {attempt.experience && attempt.experience.length > 0 && (
                      <div className="border-t border-red-400/20 pt-2 mt-2">
                        <div className="text-[10px] text-red-400 mb-1">{t.searchExperience}</div>
                        <div className="space-y-1">
                          {attempt.experience.map((exp, expIdx) => (
                            <div key={expIdx} className="text-[10px] text-text-secondary bg-red-400/5 px-2 py-1 rounded">
                              {exp}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {attempt.feedback && (
                      <div className="border-t border-bento-border pt-2 mt-2">
                        <div className="text-[10px] text-text-muted mb-1">{t.feedbackPerception}</div>

                        {attempt.feedback.availability && (
                          <div className="flex items-center gap-2 mb-1">
                            <span className={`px-2 py-0.5 rounded text-[10px] ${
                              attempt.feedback.availability === 'found' ? 'bg-accent-green/10 text-accent-green' :
                              attempt.feedback.availability === 'partial' ? 'bg-amber-500/10 text-amber-500' :
                              attempt.feedback.availability === 'not_found' ? 'bg-red-400/10 text-red-400' :
                              'bg-gray-400/10 text-gray-400'
                            }`}>
                              {attempt.feedback.availability === 'found' ? t.infoObtained :
                               attempt.feedback.availability === 'partial' ? t.partialObtained :
                               attempt.feedback.availability === 'not_found' ? t.notFound :
                               t.uncertain}
                            </span>
                          </div>
                        )}

                        {attempt.feedback.difficulty_reason && (
                          <div className="text-[10px] text-text-muted mb-1">
                            <span className="text-amber-500">⚠</span> {attempt.feedback.difficulty_reason}
                          </div>
                        )}

                        {attempt.feedback.suggested_action && attempt.feedback.suggested_action !== 'none' && (
                          <div className="text-[10px] text-accent-blue">
                            <span className="font-medium">{t.suggestion}</span>{' '}
                            {attempt.feedback.suggested_action === 'refine_query' ? t.refineQuery :
                             attempt.feedback.suggested_action === 'try_alternative' ? t.tryAlternative :
                             attempt.feedback.suggested_action === 'expand_scope' ? t.expandScope :
                             attempt.feedback.suggested_action === 'accept_partial' ? t.acceptPartial :
                             attempt.feedback.suggested_action}
                            {attempt.feedback.action_detail && (
                              <span className="text-text-muted"> - {attempt.feedback.action_detail}</span>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </motion.div>
              ))}
            </div>
          </div>
        ) : (
          <div className="text-center py-6 text-text-muted">
            <div className="w-12 h-12 rounded-full bg-bento-surfaceHover flex items-center justify-center mx-auto mb-2">
              <SearcherIcon />
            </div>
            <p className="text-xs">{t.waitingForTask}</p>
          </div>
        )}
      </div>

      <div className="bg-gradient-to-r from-accent-blue/5 to-accent-purple/5 rounded-lg p-3 border border-accent-blue/10">
        <div className="text-[11px] text-text-secondary leading-relaxed">
          <span className="text-accent-blue font-medium">{t.multiAgentCollab}</span>{' '}
          {t.collabExplanation}
        </div>
      </div>
    </div>
  );
};

// Brain Node

const BrainNode = ({
  entity,
  onClick,
  isActive,
  nodeRef
}: {
  entity: CognitiveEntity;
  onClick: () => void;
  isActive?: boolean;
  nodeRef?: (el: HTMLButtonElement | null) => void;
}) => {
  const { t } = useLanguage();
  const stateStyle = {
    START: { ring: 'ring-blue-400', bg: 'bg-blue-400/20', pulse: false },
    KNOWN: { ring: 'ring-accent-green', bg: 'bg-accent-green/20', pulse: false },
    PARTIAL: { ring: 'ring-amber-500', bg: 'bg-amber-500/20', pulse: true },
    UNKNOWN: { ring: 'ring-gray-400', bg: 'bg-gray-400/20', pulse: true },
  }[entity.cognitive_state] || { ring: 'ring-gray-400', bg: 'bg-gray-400/20', pulse: false };

  const hasDiscoveredItems = entity.discovered_items && entity.discovered_items.length > 0;
  const itemFindingsCount = entity.item_findings ? Object.keys(entity.item_findings).length : 0;
  const crossItemFindingsCount = entity.cross_item_findings ? Object.keys(entity.cross_item_findings).length : 0;
  const legacyFindingsCount = entity.findings ? Object.keys(entity.findings).length : 0;

  return (
    <motion.button
      ref={nodeRef}
      onClick={onClick}
      className={`
        relative px-3 py-2 rounded-xl border transition-all
        ${isActive ? 'ring-2 ring-accent-blue shadow-lg scale-105' : 'hover:shadow-md hover:scale-102'}
        ${stateStyle.bg} border-white/10
        min-w-[100px] max-w-[140px] text-left
      `}
      whileHover={{ scale: 1.05 }}
      whileTap={{ scale: 0.98 }}
    >
      {stateStyle.pulse && (
        <motion.div
          className={`absolute inset-0 rounded-xl ${stateStyle.bg}`}
          animate={{ opacity: [0.5, 0.2, 0.5] }}
          transition={{ duration: 2, repeat: Infinity }}
        />
      )}

      <div className="relative">
        <div className="text-xs font-medium text-text-primary truncate flex items-center gap-1">
          {entity.node_type === 'placeholder' && !hasDiscoveredItems && (
            <span className="text-amber-500">?</span>
          )}
          {hasDiscoveredItems && <span className="text-accent-green">✓</span>}
          <span className="truncate">
            {hasDiscoveredItems ? entity.discovered_items![0] : entity.name.replace(/^\?+/, '')}
          </span>
        </div>

        {entity.type_constraint && (
          <div className="text-[9px] text-text-muted truncate mt-0.5">
            {entity.type_constraint}
          </div>
        )}

        <div className="flex items-center gap-1 mt-1">
          {itemFindingsCount > 0 && (
            <span className="w-4 h-4 rounded-full bg-accent-blue/30 flex items-center justify-center text-[8px] text-accent-blue" title={t.itemAttributes}>
              {itemFindingsCount}
            </span>
          )}
          {crossItemFindingsCount > 0 && (
            <span className="w-4 h-4 rounded-full bg-purple-500/30 flex items-center justify-center text-[8px] text-purple-400" title={t.crossItemInsights}>
              {crossItemFindingsCount}
            </span>
          )}
          {legacyFindingsCount > 0 && itemFindingsCount === 0 && crossItemFindingsCount === 0 && (
            <span className="w-4 h-4 rounded-full bg-accent-blue/30 flex items-center justify-center text-[8px] text-accent-blue">
              {legacyFindingsCount}
            </span>
          )}
          {entity.temporal_notes && (
            <span className="text-[8px] text-purple-400">⏱</span>
          )}
        </div>
      </div>

      {entity.hop_distance > 0 && (
        <div className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-bento-surface border border-bento-border text-[8px] flex items-center justify-center text-text-muted">
          {entity.hop_distance}
        </div>
      )}
    </motion.button>
  );
};

// Main Component

const LanguageIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <circle cx="12" cy="12" r="10" />
    <path d="M2 12h20" />
    <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
  </svg>
);

const CognitiveGraphInner = ({ data: rawData, isLoading }: CognitiveGraphProps) => {
  const { t, lang, toggleLang } = useLanguage();
  const data = useMemo(() => normalizeGraphData(rawData), [rawData]);
  const [selectedEntity, setSelectedEntity] = useState<CognitiveEntity | null>(null);
  const [selectedRelation, setSelectedRelation] = useState<CognitiveRelation | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);
  const nodeRefs = useRef<Map<string, HTMLElement>>(new Map());
  const [connections, setConnections] = useState<ConnectionInfo[]>([]);

  const calculateConnections = useCallback(() => {
    if (!containerRef.current || !data) return;
    const containerRect = containerRef.current.getBoundingClientRect();
    const newConnections: ConnectionInfo[] = [];

    // Count edges per source/target for y-offset spreading
    const sourceCount: Record<string, number> = {};
    const targetCount: Record<string, number> = {};
    const sourceIdx: Record<string, number> = {};
    const targetIdx: Record<string, number> = {};

    for (const relation of data.relations) {
      sourceCount[relation.source] = (sourceCount[relation.source] || 0) + 1;
      targetCount[relation.target] = (targetCount[relation.target] || 0) + 1;
    }

    for (const relation of data.relations) {
      const sourceEl = nodeRefs.current.get(relation.source);
      const targetEl = nodeRefs.current.get(relation.target);
      if (!sourceEl || !targetEl) continue;

      const sourceRect = sourceEl.getBoundingClientRect();
      const targetRect = targetEl.getBoundingClientRect();

      // Calculate y-offset for multiple edges from same source/target
      const sCount = sourceCount[relation.source] || 1;
      const sIdx = sourceIdx[relation.source] || 0;
      sourceIdx[relation.source] = sIdx + 1;
      const sourceYOffset = sCount > 1 ? (sIdx - (sCount - 1) / 2) * 8 : 0;

      const tCount = targetCount[relation.target] || 1;
      const tIdx = targetIdx[relation.target] || 0;
      targetIdx[relation.target] = tIdx + 1;
      const targetYOffset = tCount > 1 ? (tIdx - (tCount - 1) / 2) * 8 : 0;

      const x1 = sourceRect.right - containerRect.left;
      const y1 = sourceRect.top + sourceRect.height / 2 - containerRect.top + sourceYOffset;
      const x2 = targetRect.left - containerRect.left;
      const y2 = targetRect.top + targetRect.height / 2 - containerRect.top + targetYOffset;
      const mx = (x1 + x2) / 2;
      const my = (y1 + y2) / 2;

      newConnections.push({ id: relation.id, relation, x1, y1, x2, y2, mx, my });
    }
    setConnections(newConnections);
  }, [data]);

  useEffect(() => {
    // Delay to allow DOM to settle after render/animation
    const timer1 = setTimeout(calculateConnections, 50);
    const timer2 = setTimeout(calculateConnections, 300);

    const observer = new ResizeObserver(() => {
      calculateConnections();
    });
    if (containerRef.current) {
      observer.observe(containerRef.current);
    }

    return () => {
      clearTimeout(timer1);
      clearTimeout(timer2);
      observer.disconnect();
    };
  }, [calculateConnections, selectedEntity]);

  const stats = useMemo(() => {
    if (!data) return null;
    return {
      totalEntities: data.entities.filter(e => e.cognitive_state !== 'START').length,
      knownEntities: data.entities.filter(e => e.cognitive_state === 'KNOWN').length,
      partialEntities: data.entities.filter(e => e.cognitive_state === 'PARTIAL').length,
      unknownEntities: data.entities.filter(e => e.cognitive_state === 'UNKNOWN').length,
      totalRelations: data.relations.length,
      solvedRelations: data.relations.filter(r => r.state === 'solved').length,
      investigatingRelations: data.relations.filter(r => r.state === 'investigating').length,
      exploringRelations: data.relations.filter(r => r.state === 'to_solve').length,
      totalSearches: data.relations.reduce((sum, r) => sum + (r.attempt_count || 0), 0),
    };
  }, [data]);

  if (isLoading) {
    return (
      <div className="bg-gradient-to-br from-bento-surface to-bento-bg rounded-xl border border-bento-border p-6">
        <div className="flex items-center justify-center gap-3 text-text-muted">
          <motion.div
            animate={{ rotate: 360 }}
            transition={{ duration: 2, repeat: Infinity, ease: 'linear' }}
          >
            <BrainIcon />
          </motion.div>
          <span className="text-sm">{t.buildingCognition}</span>
        </div>
      </div>
    );
  }

  if (!data || data.entities.length === 0) {
    return (
      <div className="bg-gradient-to-br from-bento-surface to-bento-bg rounded-xl border border-bento-border p-6">
        <div className="text-center text-text-muted">
          <BrainIcon />
          <p className="text-sm mt-2">{t.noCognitiveData}</p>
        </div>
      </div>
    );
  }

  const entitiesByHop = data.entities.reduce((acc, entity) => {
    const hop = entity.hop_distance;
    if (!acc[hop]) acc[hop] = [];
    acc[hop].push(entity);
    return acc;
  }, {} as Record<number, CognitiveEntity[]>);
  const hopLevels = Object.keys(entitiesByHop).map(Number).sort((a, b) => a - b);

  return (
    <>
      <div className="bg-gradient-to-br from-bento-surface via-bento-bg to-bento-surface rounded-xl border border-bento-border overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-bento-border bg-gradient-to-r from-accent-blue/5 to-accent-purple/5">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-accent-blue to-accent-purple flex items-center justify-center text-white">
              <BrainIcon />
            </div>
            <div>
              <h3 className="text-sm font-medium text-text-primary">{t.cognitiveGraph}</h3>
              <p className="text-[10px] text-text-muted">{t.subtitle}</p>
            </div>
          </div>

          <div className="flex items-center gap-4">
            {stats && (
              <div className="flex items-center gap-4 text-[10px]">
                <div className="flex items-center gap-1">
                  <KnowledgeIcon />
                  <span className="text-accent-green">{stats.knownEntities}</span>
                  <span className="text-text-muted">/ {stats.totalEntities}</span>
                </div>
                <div className="flex items-center gap-1">
                  <ExperienceIcon />
                  <span className="text-accent-blue">{stats.solvedRelations}</span>
                  <span className="text-text-muted">/ {stats.totalRelations}</span>
                </div>
                <div className="flex items-center gap-1">
                  <SearcherIcon />
                  <span className="text-accent-purple">{stats.totalSearches}</span>
                  <span className="text-text-muted">{t.perceptions}</span>
                </div>
              </div>
            )}

            <button
              onClick={toggleLang}
              className="flex items-center gap-1.5 px-2 py-1 rounded-lg bg-bento-surfaceHover hover:bg-bento-border transition-colors text-[10px] font-medium text-text-secondary"
              title={lang === 'zh' ? 'Switch to English' : '切换到中文'}
            >
              <LanguageIcon />
              <span>{lang === 'zh' ? '中文' : 'EN'}</span>
            </button>
          </div>
        </div>

        <div className="px-4 py-2 border-b border-bento-borderLight bg-bento-surfaceHover/30 flex items-center gap-4 text-[10px] text-text-muted">
          <span className="font-medium">{t.cognitiveState}</span>
          <div className="flex items-center gap-1">
            <div className="w-2 h-2 rounded-full bg-accent-green" />
            <span>{t.confirmed}</span>
          </div>
          <div className="flex items-center gap-1">
            <div className="w-2 h-2 rounded-full bg-amber-500" />
            <span>{t.partialKnown}</span>
          </div>
          <div className="flex items-center gap-1">
            <div className="w-2 h-2 rounded-full bg-gray-400" />
            <span>{t.toExplore}</span>
          </div>
          <span className="ml-auto text-text-muted">{t.clickForDetails}</span>
        </div>

        {data.structure?.type && (
          <div className="px-4 pt-3">
            <span className="px-2 py-1 text-[10px] bg-accent-purple/10 text-accent-purple rounded-full">
              {data.structure.type === 'serial' ? t.serialStructure :
               data.structure.type === 'parallel' ? t.parallelStructure :
               data.structure.type === 'convergence' ? t.convergenceStructure : t.mixedStructure}
            </span>
          </div>
        )}

        <div className="p-6 overflow-x-auto">
          <div ref={containerRef} className="relative flex items-center gap-20 min-w-max py-2">
            {hopLevels.map((hop) => (
              <div key={hop} className="flex flex-col gap-2 relative z-10">
                <div className="text-[10px] text-text-muted text-center mb-1 font-medium">
                  {hop === 0 ? t.core : `${t.hop} ${hop}`}
                </div>

                {entitiesByHop[hop].map((entity) => (
                  <BrainNode
                    key={entity.id}
                    entity={entity}
                    onClick={() => {
                      setSelectedRelation(null);
                      setSelectedEntity(selectedEntity?.id === entity.id ? null : entity);
                    }}
                    isActive={selectedEntity?.id === entity.id}
                    nodeRef={(el) => {
                      if (el) nodeRefs.current.set(entity.id, el);
                      else nodeRefs.current.delete(entity.id);
                    }}
                  />
                ))}
              </div>
            ))}

            <svg
              className="absolute inset-0 pointer-events-none"
              style={{ zIndex: 1, width: '100%', height: '100%', overflow: 'visible' }}
            >
              <defs>
                <marker id="arrow-solved" markerWidth="6" markerHeight="5" refX="5" refY="2.5" orient="auto">
                  <polygon points="0 0, 6 2.5, 0 5" fill="#30d158" />
                </marker>
                <marker id="arrow-investigating" markerWidth="6" markerHeight="5" refX="5" refY="2.5" orient="auto">
                  <polygon points="0 0, 6 2.5, 0 5" fill="#0071e3" />
                </marker>
                <marker id="arrow-to_solve" markerWidth="6" markerHeight="5" refX="5" refY="2.5" orient="auto">
                  <polygon points="0 0, 6 2.5, 0 5" fill="#f59e0b" />
                </marker>
                <marker id="arrow-exhausted" markerWidth="6" markerHeight="5" refX="5" refY="2.5" orient="auto">
                  <polygon points="0 0, 6 2.5, 0 5" fill="#f87171" />
                </marker>
                <marker id="arrow-known" markerWidth="6" markerHeight="5" refX="5" refY="2.5" orient="auto">
                  <polygon points="0 0, 6 2.5, 0 5" fill="#9ca3af" />
                </marker>
              </defs>

              {connections.map((conn) => {
                const dx = (conn.x2 - conn.x1) * 0.4;
                const path = `M ${conn.x1} ${conn.y1} C ${conn.x1 + dx} ${conn.y1}, ${conn.x2 - dx} ${conn.y2}, ${conn.x2} ${conn.y2}`;
                const state = conn.relation.state;

                const strokeColor = {
                  solved: '#30d158',
                  investigating: '#0071e3',
                  to_solve: '#f59e0b',
                  exhausted: '#f87171',
                  known: '#9ca3af',
                }[state] || '#9ca3af';

                const pathLength = Math.sqrt(
                  Math.pow(conn.x2 - conn.x1, 2) + Math.pow(conn.y2 - conn.y1, 2)
                ) * 1.5;

                return (
                  <g key={conn.id}>
                    {/* White backdrop for contrast */}
                    <path
                      d={path}
                      fill="none"
                      stroke="white"
                      strokeWidth={4}
                      opacity={0.6}
                    />
                    {state !== 'to_solve' ? (
                      <path
                        d={path}
                        fill="none"
                        stroke={strokeColor}
                        strokeWidth={1.5}
                        markerEnd={`url(#arrow-${state})`}
                        opacity={0.85}
                        strokeDasharray={String(pathLength)}
                        strokeDashoffset={String(pathLength)}
                      >
                        <animate
                          attributeName="stroke-dashoffset"
                          from={String(pathLength)}
                          to="0"
                          dur="0.6s"
                          fill="freeze"
                        />
                      </path>
                    ) : (
                      <path
                        d={path}
                        fill="none"
                        stroke={strokeColor}
                        strokeWidth={1.5}
                        strokeDasharray="6,4"
                        markerEnd={`url(#arrow-${state})`}
                        opacity={0.85}
                      />
                    )}
                  </g>
                );
              })}
            </svg>

            {connections.map((conn) => {
              const state = conn.relation.state;
              const dotColor = {
                solved: 'bg-accent-green',
                investigating: 'bg-accent-blue',
                to_solve: 'bg-amber-500',
                exhausted: 'bg-red-400',
                known: 'bg-gray-400',
              }[state] || 'bg-gray-400';

              const textColor = {
                solved: 'text-accent-green',
                investigating: 'text-accent-blue',
                to_solve: 'text-amber-500',
                exhausted: 'text-red-400',
                known: 'text-gray-400',
              }[state] || 'text-gray-400';

              // Position at 65% along the bezier (closer to target)
              const t_param = 0.65;
              const dx = (conn.x2 - conn.x1) * 0.4;
              const cp1x = conn.x1 + dx, cp1y = conn.y1;
              const cp2x = conn.x2 - dx, cp2y = conn.y2;
              const mt = 1 - t_param;
              const labelX = mt*mt*mt*conn.x1 + 3*mt*mt*t_param*cp1x + 3*mt*t_param*t_param*cp2x + t_param*t_param*t_param*conn.x2;
              const labelY = mt*mt*mt*conn.y1 + 3*mt*mt*t_param*cp1y + 3*mt*t_param*t_param*cp2y + t_param*t_param*t_param*conn.y2;

              return (
                <motion.button
                  key={`label-${conn.id}`}
                  className={`
                    absolute z-20 flex items-center gap-1 px-1.5 py-0.5 rounded-md
                    bg-white/80 backdrop-blur-sm shadow-sm border border-bento-borderLight/50
                    hover:bg-white hover:shadow-md hover:border-bento-border
                    transition-all cursor-pointer whitespace-nowrap
                    ${selectedRelation?.id === conn.relation.id ? 'ring-1 ring-accent-blue bg-accent-blue/10 shadow-md' : ''}
                  `}
                  style={{
                    left: labelX,
                    top: labelY,
                    transform: 'translate(-50%, -50%)',
                  }}
                  initial={{ opacity: 0, scale: 0.8 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ delay: 0.4, duration: 0.2 }}
                  onClick={() => {
                    setSelectedEntity(null);
                    setSelectedRelation(selectedRelation?.id === conn.relation.id ? null : conn.relation);
                  }}
                >
                  <div className={`w-1.5 h-1.5 rounded-full ${dotColor} shrink-0`} />
                  <span className={`text-[9px] font-medium ${textColor} truncate max-w-[72px]`}>
                    {conn.relation.relation_type}
                  </span>
                </motion.button>
              );
            })}
          </div>
        </div>

        <div className="px-4 py-3 border-t border-bento-border bg-gradient-to-r from-accent-blue/5 to-accent-purple/5">
          <div className="flex items-center justify-between text-[10px]">
            <div className="flex items-center gap-2 text-text-muted">
              <span className="font-medium text-accent-blue">🧠 {t.planner}</span>
              <span>{t.planCognitionGoal}</span>
              <span className="text-text-muted/50">→</span>
              <span className="font-medium text-accent-purple">🤖 {t.searcher}</span>
              <span>{t.perceiveWorld}</span>
              <span className="text-text-muted/50">→</span>
              <span className="font-medium text-accent-green">✓</span>
              <span>{t.updateCognition}</span>
            </div>
            <span className="text-text-muted">
              {t.multiAgentCycle}
            </span>
          </div>
        </div>
      </div>

      <AnimatePresence>
        {(selectedEntity || selectedRelation) && (
          <DetailModal
            type={selectedEntity ? 'entity' : 'relation'}
            entity={selectedEntity || undefined}
            relation={selectedRelation || undefined}
            entities={data.entities}
            onClose={() => {
              setSelectedEntity(null);
              setSelectedRelation(null);
            }}
          />
        )}
      </AnimatePresence>
    </>
  );
};

const CognitiveGraph = (props: CognitiveGraphProps) => {
  const [lang, setLang] = useState<Language>('zh');
  const t = translations[lang];
  const toggleLang = () => setLang(prev => prev === 'zh' ? 'en' : 'zh');

  return (
    <LanguageContext.Provider value={{ lang, t, toggleLang }}>
      <CognitiveGraphInner {...props} />
    </LanguageContext.Provider>
  );
};

export default CognitiveGraph;
