"""Cognitive graph parsing, evidence updates and restructuring prompts."""

COGNITIVE_GRAPH_PARSE_PROMPT = """你是认知图解析专家。将用户问题解析为概念级的认知图——提取问题中的研究维度和概念关系，而非预测具体实体。

## 当前时间
{current_date}

## 输入问题
{question}

## 解析任务

1. **识别概念节点**:
   - **起点实体(START)**: 你**确切知道其完整内容**的具体实体，作为搜索起点锚点（标记为START，type="start"）
   - **研究概念(UNKNOWN)**: 需要通过搜索才能确定具体内容的维度/概念（type="placeholder", cognitive_state="UNKNOWN"）

   **⚠️ START的严格判定**: START意味着你不需要搜索就已经知道它是什么。问自己："我能列出它的具体内容吗？"
   - ✅ START: "Meta" — 具体公司名，确切知道是什么
   - ✅ START: "欧盟" — 具体组织名
   - ❌ 不是START: "全球市值前十的药企" — 你不知道具体是哪十家，需要搜索 → UNKNOWN
   - ❌ 不是START: "最受欢迎的5个JavaScript框架" — 需要搜索才知道 → UNKNOWN

   **当问题中没有任何已知实体时**: 创建一个通用锚点节点（如问题的研究领域）作为START起点节点，所有待探索概念作为UNKNOWN子节点
   - 示例: "全球市值前十的药企有哪些？各自的研发投入如何？" → 创建"全球制药行业"(START)作为锚点 → "市值前十的药企"(UNKNOWN)作为子节点

   **核心原则**: 只提取问题文本中**显式存在**或**直接隐含**的概念。不要预测搜索会发现什么实体。
   - ✅ 问题说"供应商" → 创建概念节点"数据标注供应商"
   - ✅ 问题说"技术路线" → 创建概念节点"技术路线分类"
   - ❌ 不要预测"供应商可能是 Scale AI, Appen" → 这些是搜索后才知道的

   **隐含前置条件**: 如果直接搜索这个概念，你知道要搜什么吗？如果不知道，就需要先搜清楚它是什么
   - ✅ "X分类体系的各类别特征" → 不知道X有哪些类别 → 先创建"X分类体系"(hop=1)，再创建"各类别特征"(hop=2)
   - ❌ 不要直接创建"X分类体系各类别特征"扁平节点 → 搜索时不知道具体有哪些类别

   **串行依赖概念**: 如果概念B的搜索必须用到概念A的结果，B的名称应包含依赖上下文
   - ✅ "[数据标注供应商]的非洲办公室分布" — 明确依赖关系
   - ❌ "非洲办公室" — 缺少上下文，不知道是谁的办公室

   **共享概念节点**: 多个已知实体指向同一研究概念 → 创建**一个**共享概念节点，不按实体复制
   - ✅ Meta和Google都需要"标注供应商" → 创建一个"多模态模型标注供应商"概念节点，Meta和Google各用一条边连接
   - ❌ 不要创建"Meta的标注供应商"和"Google的标注供应商"两个独立概念节点 → 会导致下游边成倍增长
   - 每条边的inquiry_goal自然包含各自的上下文（"Meta的多模态大模型使用了哪些标注供应商?"），搜索不会混淆

   **概念 vs 分析维度**: 在同一信息源中共现的分析角度应建模为inquiry criteria，而非独立概念节点
   - ✅ "技术优势"和"市场份额"通常出现在同一份公司报告中 → 建模为inquiry_goal中的criteria
   - ❌ 不要为每个分析角度创建独立概念节点 → 会导致 N个实体 × M个维度 的任务爆炸
   - 只有搜索策略确实不同（不同关键词、不同来源类型）的维度才应作为独立概念节点

2. **识别关系/询问目标**:
   - 每条边表示一个需要回答的子问题
   - 标注边的状态：known（已知）或 to_solve（待求解）
   - inquiry_goal必须是**完整的、自包含的搜索问题**
   - ⚠️ **关键规则**: inquiry_goal 必须包含完整上下文，不能有认知断层！
   - 如果inquiry_goal依赖前置概念节点，**必须**使用占位符 `[节点名]` 明确引用
   - 占位符会在运行时被实际发现的值替换
   - ⚠️ **边的target必须是UNKNOWN节点（placeholder）**: START节点只能做边的source（锚点），不能做target。如果你需要调查已知实体的某个属性，target应该是描述该属性的UNKNOWN节点，而非已知实体本身。
     - ✅ "电动汽车电池"(START) → "主流电池材料的安全性对比"(UNKNOWN)
     - ❌ "锂电池安全性"(START) → "磷酸铁锂"(START) — 磷酸铁锂是已知概念，不是待调查的目标

3. **识别约束条件**:
   识别问题中所有限定搜索范围的修饰成分，包括但不限于：时间、地点、类型、数量、领域、归属、状态、程度等。
   每个约束都要明确标注在对应概念节点的 condition_constraints 中。

4. **分析依赖结构**:
   - 串行依赖：A的答案是B的前提（必须先知道A才能搜索B）
   - 并行链路：可同时独立探索（多个概念维度互不依赖）
   - 汇聚节点：多条链路的交集或合并点

   ⚠️ **效率优化关键**：识别可并行的结构！
   - **多条件筛选问题**（如"满足条件A、B、C、D的X"）→ 每个条件**独立搜索**，最后**汇聚筛选**
   - **多维度研究问题**（如"全面分析X的A、B、C方面"）→ 各维度**并行搜索**
   - **多实体比较问题**（如"A和B的X分别是什么"）→ 两条链路**并行搜索**
   - 只有当B的搜索**必须用到**A的结果时，才用串行

5. **检测涉及的语言/地区**:
   - 识别问题涉及的国家/地区
   - 确定建议的搜索语言列表（默认: ["en", "zh"]，最多3种）

6. **识别任务类型（关键！）**:
   先根据**原始用户问题**判断整体问题性质，再决定每条边的 task_type：

   **问题性质判断**（⚠️ 看原始问题整体，不是看单条边）:
   - **有确定答案**: 存在唯一正确答案可从权威来源验证（如投票记录、官方统计、榜单排名）
   - **开放性探索**: 没有标准答案，需要从多渠道综合推断（如"X的供应商有哪些"、"X的发展趋势"）

   **task_type 规则**:
   - **"specified"**:
     - 用户字面指定了来源 → 任何问题性质都可用
       - 示例: "as published by William Reed" → specified_source="William Reed"
     - 领域推导（只能从官方获取）→ **仅限有确定答案的问题**
       - 示例: "美国众议院投票记录" → 有确定答案 + 只能从官方获取 → specified_source="US Congress official sites"
   - **"open"**: 默认选项。开放性问题中，即使你认为某信息"通常来自某渠道"，也**不得**用领域推导标为specified

   ⚠️ **重要**: 开放性问题不允许领域推导specified，只认用户字面指定。不要用预训练知识替用户决定来源。

## 输出格式

**第一步：先输出你的分析思考**（用[思考]标记）：
```
[思考]
1. 问题核心：这个问题本质上在问什么？最终要回答什么？
2. 起点实体：问题中明确提到了哪些具体实体？
3. 研究维度：问题包含哪些需要独立研究的概念/维度？（从问题文本中提取，不是预测）
4. 前置条件检查：对每个概念问自己："如果直接搜索这个概念，我知道要搜什么吗？"如果不知道，就需要先创建前置节点搞清楚它是什么
5. 维度关系：这些维度之间是独立的还是有依赖关系？
6. 结构类型：是串行依赖、并行探索、还是汇聚结构？
7. 潜在遗漏：有没有容易忽略的隐含维度或约束条件？
```

**第二步：基于思考输出JSON认知图**（不要用markdown代码块包裹）：
{{
  "thinking_summary": "一句话总结探索策略",
  "entities": [
    {{"id": "e1", "name": "起点实体名", "type": "start", "cognitive_state": "START", "hop_distance": 0}},
    {{"id": "e2", "name": "研究概念名", "type": "placeholder", "cognitive_state": "UNKNOWN",
     "type_constraint": "类型约束", "condition_constraints": ["条件1"],
     "hop_distance": 1, "discovery_dependency": null}}
  ],
  "relations": [
    {{"id": "r1", "source": "e1", "target": "e2", "type": "关系类型",
     "inquiry_goal": "完整的可搜索问题?", "state": "to_solve",
     "task_type": "open", "specified_source": null}}
  ],
  "structure": {{
    "type": "parallel",
    "parallel_chains": [],
    "convergence_nodes": [],
    "dependency_order": ["e1", "e2"]
  }},
  "languages": ["en", "zh"]
}}

## 结构类型说明

| 类型 | 特征 | 典型问题模式 | 效率 |
|------|------|--------------|------|
| serial | A→B→C 必须按顺序 | "A的B的C是什么?" | 最低 |
| parallel | A和B独立，无汇聚 | "分析X的A、B、C方面" | ⭐常用 |
| convergence | A和B独立搜索→汇聚筛选 | "满足条件A且B且C的X" | ⭐最高 |
| mixed | 串行+并行+汇聚组合 | 复杂多步问题 | 取决于设计 |

⚠️ **优先使用 parallel/convergence**：
- 如果维度之间**互不依赖**，就应该**并行搜索**
- 只有当B的搜索**必须用到**A的结果时，才用串行

## 示例

### 示例1（串行结构：概念间有依赖）

问题："2025年给Meta做数据标注的供应商中，有哪些在非洲设有办公室？"

分析：
- 两个概念维度：Meta的数据标注供应商、供应商的非洲办公室分布
- 第二个概念依赖第一个的结果（必须先知道供应商是谁）→ 串行

输出：
{{
  "thinking_summary": "先搜Meta供应商，再查各供应商的非洲办公室",
  "entities": [
    {{"id": "e1", "name": "Meta", "type": "start", "cognitive_state": "START", "hop_distance": 0}},
    {{"id": "e2", "name": "数据标注供应商", "type": "placeholder", "cognitive_state": "UNKNOWN",
     "type_constraint": "数据标注公司", "condition_constraints": ["2025年", "为Meta服务"], "hop_distance": 1}},
    {{"id": "e3", "name": "[数据标注供应商]的非洲办公室分布", "type": "placeholder", "cognitive_state": "UNKNOWN",
     "condition_constraints": ["在非洲"], "hop_distance": 2, "discovery_dependency": "e2"}}
  ],
  "relations": [
    {{"id": "r1", "source": "e1", "target": "e2", "type": "数据标注服务",
     "inquiry_goal": "Meta在2025年使用哪些数据标注供应商?", "state": "to_solve",
     "task_type": "open", "specified_source": null}},
    {{"id": "r2", "source": "e2", "target": "e3", "type": "办公地点",
     "inquiry_goal": "[数据标注供应商]在非洲哪些国家有办公室?", "state": "to_solve",
     "task_type": "open", "specified_source": null}}
  ],
  "structure": {{
    "type": "serial",
    "parallel_chains": [],
    "convergence_nodes": [],
    "dependency_order": ["e1", "e2", "e3"]
  }},
  "languages": ["en", "zh"]
}}

### 示例2（指定来源任务 task_type=specified）

问题："In 2023, The World's 50 Best restaurants as published by William Reed began to officially list restaurants 51-100 as well. Name any restaurants that are included on both the 2022 and 2023 lists and are situated in Germany."

分析：
- 问题明确指定了来源: "as published by William Reed" → task_type="specified"
- 单一概念维度：同时在两年榜单上的德国餐厅

输出：
{{
  "thinking_summary": "从William Reed官方榜单中筛选同时出现在两年列表的德国餐厅",
  "entities": [
    {{"id": "e1", "name": "World's 50 Best Restaurants", "type": "start", "cognitive_state": "START", "hop_distance": 0}},
    {{"id": "e2", "name": "2022和2023榜单都有的德国餐厅", "type": "placeholder", "cognitive_state": "UNKNOWN",
     "type_constraint": "餐厅", "condition_constraints": ["同时在2022和2023榜单", "位于德国"], "hop_distance": 1}}
  ],
  "relations": [
    {{"id": "r1", "source": "e1", "target": "e2", "type": "榜单收录",
     "inquiry_goal": "World's 50 Best Restaurants 2022和2023榜单中，哪些餐厅同时出现且位于德国?", "state": "to_solve",
     "task_type": "specified", "specified_source": "William Reed"}}
  ],
  "structure": {{
    "type": "serial",
    "parallel_chains": [],
    "convergence_nodes": [],
    "dependency_order": ["e1", "e2"]
  }},
  "languages": ["en", "de"]
}}

### 示例3（⭐ 并行汇聚结构：多条件独立搜索 + 交集筛选）

问题："List all countries that meet ALL conditions: Is an EU member state; Doesn't have a monarchy; Had over 100,000 immigrants in 2022; Voting age is 18+ for EU elections."

分析：
- ⚠️ **关键识别**: 多个**独立条件**需要同时满足 → 使用**并行汇聚**结构
- 每个条件就是一个独立的研究概念维度
- 最后**汇聚筛选**：取所有条件的交集

输出：
{{
  "thinking_summary": "4个独立条件并行搜索，最后汇聚取交集",
  "entities": [
    {{"id": "e0", "name": "欧盟", "type": "start", "cognitive_state": "START", "hop_distance": 0}},
    {{"id": "e1", "name": "EU成员国列表", "type": "placeholder", "cognitive_state": "UNKNOWN",
     "type_constraint": "国家列表", "condition_constraints": ["2024年EU成员国"], "hop_distance": 1}},
    {{"id": "e2", "name": "非君主制国家列表", "type": "placeholder", "cognitive_state": "UNKNOWN",
     "type_constraint": "国家列表", "condition_constraints": ["政体非君主制"], "hop_distance": 1}},
    {{"id": "e3", "name": "2022移民超10万国家", "type": "placeholder", "cognitive_state": "UNKNOWN",
     "type_constraint": "国家列表", "condition_constraints": ["2022年移民数>100,000"], "hop_distance": 1}},
    {{"id": "e4", "name": "投票年龄18+国家", "type": "placeholder", "cognitive_state": "UNKNOWN",
     "type_constraint": "国家列表", "condition_constraints": ["EU选举投票年龄>=18"], "hop_distance": 1}},
    {{"id": "e5", "name": "满足所有条件的国家", "type": "placeholder", "cognitive_state": "UNKNOWN",
     "type_constraint": "国家列表", "condition_constraints": ["e1∩e2∩e3∩e4的交集"],
     "hop_distance": 2, "discovery_dependency": "e1,e2,e3,e4"}}
  ],
  "relations": [
    {{"id": "r1", "source": "e0", "target": "e1", "type": "成员国",
     "inquiry_goal": "2024年欧盟EU有哪些成员国? 列出完整名单", "state": "to_solve",
     "task_type": "open", "specified_source": null}},
    {{"id": "r2", "source": "e0", "target": "e2", "type": "政体筛选",
     "inquiry_goal": "欧洲哪些国家不是君主制? 或者哪些欧洲国家是共和制?", "state": "to_solve",
     "task_type": "open", "specified_source": null}},
    {{"id": "r3", "source": "e0", "target": "e3", "type": "移民数据",
     "inquiry_goal": "2022年哪些欧洲国家的移民人数超过100,000人?", "state": "to_solve",
     "task_type": "specified", "specified_source": "Eurostat"}},
    {{"id": "r4", "source": "e0", "target": "e4", "type": "选举规则",
     "inquiry_goal": "2024年欧盟各国的欧洲议会选举投票年龄是多少?", "state": "to_solve",
     "task_type": "open", "specified_source": null}},
    {{"id": "r5", "source": "e1", "target": "e5", "type": "交集筛选",
     "inquiry_goal": "根据收集到的数据，筛选同时满足所有4个条件的国家", "state": "to_solve",
     "task_type": "open", "specified_source": null}}
  ],
  "structure": {{
    "type": "convergence",
    "parallel_chains": [["e0", "e1"], ["e0", "e2"], ["e0", "e3"], ["e0", "e4"]],
    "convergence_nodes": ["e5"],
    "dependency_order": ["e0", "e1", "e2", "e3", "e4", "e5"]
  }},
  "languages": ["en"]
}}

### 示例4（混合结构：共享概念节点 + 并行 + 汇聚）

问题："2025年，Meta和Google的多模态大模型分别使用了哪些标注供应商？这些供应商中有哪些同时服务了中国的AI公司？"

分析：
- Meta和Google都需要搜索"标注供应商"这个**同一研究概念** → 创建一个共享概念节点
- 两条边（r1, r2）各自包含不同的上下文（Meta vs Google），搜索不会混淆
- "同时服务"表示汇聚/交集 → e4是汇聚节点
- ⚠️ 不要为Meta和Google各创建一个独立的"标注供应商"节点 → 会导致下游边成倍增长

输出：
{{
  "thinking_summary": "Meta和Google共享一个'标注供应商'概念节点，各自用独立的边搜索，再汇聚查找同时服务中国AI公司的",
  "entities": [
    {{"id": "e1", "name": "Meta", "type": "start", "cognitive_state": "START", "hop_distance": 0}},
    {{"id": "e2", "name": "Google", "type": "start", "cognitive_state": "START", "hop_distance": 0}},
    {{"id": "e3", "name": "多模态模型标注供应商", "type": "placeholder", "cognitive_state": "UNKNOWN",
     "type_constraint": "标注供应商", "condition_constraints": ["多模态大模型", "2025年"], "hop_distance": 1}},
    {{"id": "e4", "name": "同时服务中国AI公司的供应商", "type": "placeholder", "cognitive_state": "UNKNOWN",
     "type_constraint": "标注供应商", "condition_constraints": ["同时服务Meta和Google", "服务中国AI公司"],
     "hop_distance": 2, "discovery_dependency": "e3"}}
  ],
  "relations": [
    {{"id": "r1", "source": "e1", "target": "e3", "type": "标注服务",
     "inquiry_goal": "Meta在2025年的多模态大模型使用了哪些数据标注供应商?", "state": "to_solve",
     "task_type": "open", "specified_source": null}},
    {{"id": "r2", "source": "e2", "target": "e3", "type": "标注服务",
     "inquiry_goal": "Google在2025年的多模态大模型使用了哪些数据标注供应商?", "state": "to_solve",
     "task_type": "open", "specified_source": null}},
    {{"id": "r3", "source": "e3", "target": "e4", "type": "客户服务",
     "inquiry_goal": "[多模态模型标注供应商]中，哪些同时服务了中国的AI公司?", "state": "to_solve",
     "task_type": "open", "specified_source": null}}
  ],
  "structure": {{
    "type": "mixed",
    "parallel_chains": [["e1", "e3"], ["e2", "e3"]],
    "convergence_nodes": ["e4"],
    "dependency_order": ["e1", "e2", "e3", "e4"]
  }},
  "languages": ["en", "zh"]
}}

### 示例5（⭐ 开放研究问题：多维度并行探索）

问题："请分析全球新能源汽车市场的竞争格局，重点关注主流技术路线对比、头部企业市场份额、以及各国政策对市场的影响。"

分析：
- 这是**开放研究问题**，没有唯一正确答案
- 问题明确提到了3个需要研究的维度：技术路线对比、市场份额、政策影响
- 各维度之间**互不依赖**，可以并行搜索
- ⚠️ 不要预测搜索会发现什么具体企业或技术路线

输出：
{{
  "thinking_summary": "3个独立研究维度并行搜索：技术路线对比、头部企业市场份额、各国政策影响",
  "entities": [
    {{"id": "e1", "name": "全球新能源汽车市场", "type": "start", "cognitive_state": "START", "hop_distance": 0}},
    {{"id": "e2", "name": "主流技术路线对比", "type": "placeholder", "cognitive_state": "UNKNOWN",
     "type_constraint": "技术路线分析", "condition_constraints": ["新能源汽车", "主流技术路线"], "hop_distance": 1}},
    {{"id": "e3", "name": "头部企业市场份额", "type": "placeholder", "cognitive_state": "UNKNOWN",
     "type_constraint": "市场数据", "condition_constraints": ["全球市场", "头部企业"], "hop_distance": 1}},
    {{"id": "e4", "name": "各国政策对市场的影响", "type": "placeholder", "cognitive_state": "UNKNOWN",
     "type_constraint": "政策分析", "condition_constraints": ["主要国家/地区", "对新能源汽车市场的影响"], "hop_distance": 1}}
  ],
  "relations": [
    {{"id": "r1", "source": "e1", "target": "e2", "type": "技术路线",
     "inquiry_goal": "全球新能源汽车有哪些主流技术路线？各技术路线的优劣势和发展现状如何？", "state": "to_solve",
     "task_type": "open", "specified_source": null}},
    {{"id": "r2", "source": "e1", "target": "e3", "type": "市场格局",
     "inquiry_goal": "全球新能源汽车市场的头部企业有哪些？各自的市场份额和竞争地位如何？", "state": "to_solve",
     "task_type": "open", "specified_source": null}},
    {{"id": "r3", "source": "e1", "target": "e4", "type": "政策影响",
     "inquiry_goal": "各主要国家和地区对新能源汽车有哪些关键政策？这些政策如何影响市场竞争格局？", "state": "to_solve",
     "task_type": "open", "specified_source": null}}
  ],
  "structure": {{
    "type": "parallel",
    "parallel_chains": [["e1", "e2"], ["e1", "e3"], ["e1", "e4"]],
    "convergence_nodes": [],
    "dependency_order": ["e1", "e2", "e3", "e4"]
  }},
  "languages": ["zh", "en"]
}}"""

COGNITIVE_GRAPH_PARSE_PROMPT_EN = """You are a cognitive graph parsing expert. Parse the user's question into a concept-level cognitive graph — extract research dimensions and conceptual relationships from the question, rather than predicting specific entities.

## Current Time
{current_date}

## Input Question
{question}

## Parsing Task

1. **Identify Concept Nodes**:
   - **Start entities (START)**: Specific entities whose **complete identity you already know**, serving as search starting anchors (marked as START, type="start")
   - **Research concepts (UNKNOWN)**: Research dimensions/concepts that require search to determine their specific contents (type="placeholder", cognitive_state="UNKNOWN")

   **⚠️ Strict START Judgment**: START means you don't need to search to already know what it is. Ask yourself: "Can I list its specific contents?"
   - ✅ START: "Meta" — specific company name, you know exactly what it is
   - ✅ START: "European Union" — specific organization name
   - ❌ NOT START: "Top 10 Pharma Companies by Global Market Cap" — you don't know which 10, need to search → UNKNOWN
   - ❌ NOT START: "Most Popular 5 JavaScript Frameworks" — need to search to know → UNKNOWN

   **When the question has NO known entities**: Create a generic anchor node (e.g., the research domain of the question) as a START node, with all concepts to explore as UNKNOWN child nodes
   - Example: "What are the top 10 pharma companies by global market cap? How much does each invest in R&D?" → Create "Global Pharmaceutical Industry" (START) as anchor → "Top 10 Pharma Companies" (UNKNOWN) as child node

   **Core principle**: Only extract concepts that are **explicitly present** or **directly implied** in the question text. Do NOT predict what entities will be discovered through search.
   - ✅ Question says "suppliers" → create concept node "Data Annotation Suppliers"
   - ✅ Question says "technical routes" → create concept node "Technical Route Classification"
   - ❌ Do NOT predict "suppliers might be Scale AI, Appen" → these are discovered after search

   **Implicit prerequisites**: If you search this concept directly, do you know what to search for? If not, you need to first research what it is
   - ✅ "Characteristics of each category in classification system X" → don't know what categories X has → first create "Classification System X" (hop=1), then "Category Characteristics" (hop=2)
   - ❌ Do NOT create "Classification System X Category Characteristics" as a flat node → you don't know what categories to search for

   **Serial dependency concepts**: If concept B's search requires concept A's results, B's name should include dependency context
   - ✅ "African Offices of [Data Annotation Suppliers]" — dependency is clear
   - ❌ "African Offices" — missing context, whose offices?

   **Shared concept nodes**: When multiple known entities point to the same research concept, create **one** shared concept node — do NOT duplicate per entity
   - ✅ Both Meta and Google need "annotation suppliers" → create one "Multimodal Model Annotation Suppliers" concept node, with separate edges from Meta and Google
   - ❌ Do NOT create "Meta's Annotation Suppliers" and "Google's Annotation Suppliers" as two separate concept nodes → causes downstream edge multiplication
   - Each edge's inquiry_goal naturally includes its own context ("What annotation suppliers does Meta use for its multimodal models?"), so searches won't be confused

   **Concept vs analytical dimensions**: Analytical angles that co-occur in the same source should be modeled as inquiry criteria, NOT independent concept nodes
   - ✅ "Technical advantages" and "market share" typically appear in the same company report → model as criteria within inquiry_goal
   - ❌ Do NOT create independent concept nodes for each analytical angle → causes N entities × M dimensions task explosion
   - Only dimensions with genuinely different search strategies (different keywords, different source types) should be separate concept nodes

2. **Identify Relations/Inquiry Goals**:
   - Each edge represents a sub-question that needs to be answered
   - Label the edge state: known or to_solve (to be solved)
   - inquiry_goal must be a **complete, self-contained search question**
   - ⚠️ **Key Rule**: inquiry_goal must contain full context, with no cognitive gaps!
   - If the inquiry_goal depends on a predecessor concept node, you **must** use the placeholder `[node name]` to explicitly reference it
   - Placeholders will be replaced with actually discovered values at runtime
   - ⚠️ **Edge target must be UNKNOWN (placeholder)**: START nodes can only be edge sources (anchors), never targets. If you need to investigate a property of a known entity, the target should be an UNKNOWN node describing that property, not the known entity itself.
     - ✅ "EV Batteries"(START) → "Safety Comparison of Mainstream Battery Materials"(UNKNOWN)
     - ❌ "Battery Safety"(START) → "LiFePO4"(START) — LiFePO4 is a known concept, not an investigation target

3. **Identify Constraints**:
   Identify all qualifying modifiers in the question that limit the search scope, including but not limited to: time, location, type, quantity, domain, ownership, status, degree, etc.
   Each constraint must be clearly annotated in the corresponding concept node's condition_constraints.

4. **Analyze Dependency Structure**:
   - Serial dependency: A's answer is a prerequisite for B (A must be known before B can be searched)
   - Parallel chains: Can be explored independently and simultaneously (multiple concept dimensions with no interdependencies)
   - Convergence nodes: Intersection or merge points of multiple chains

   ⚠️ **Key to Efficiency Optimization**: Identify parallelizable structures!
   - **Multi-condition filtering problems** (e.g., "X that satisfies conditions A, B, C, D") → **Search each condition independently**, then **converge and filter** at the end
   - **Multi-dimension research problems** (e.g., "comprehensively analyze aspects A, B, C of X") → Each dimension **searched in parallel**
   - **Multi-entity comparison problems** (e.g., "What are X of A and B respectively") → Two chains **searched in parallel**
   - Use serial only when searching for B **requires** A's result

5. **Detect Involved Languages/Regions**:
   - Identify the countries/regions the question involves
   - Determine the recommended list of search languages (default: ["en", "zh"], maximum 3)

6. **Identify Task Type (Critical!)**:
   First assess the overall question nature based on the **original user question**, then determine each edge's task_type:

   **Question Nature Assessment** (⚠️ assess the original question as a whole, not individual edges):
   - **Definitive answer**: A unique correct answer exists and can be verified from authoritative sources (e.g., voting records, official statistics, rankings)
   - **Open-ended exploration**: No standard answer, requires synthesizing information from multiple channels (e.g., "Who are X's suppliers?", "What are the trends in X?")

   **task_type Rules**:
   - **"specified"**:
     - User literally names the source → allowed for any question nature
       - Example: "as published by William Reed" → specified_source="William Reed"
     - Domain-derived (can only be obtained officially) → **only for definitive-answer questions**
       - Example: "US House voting records" → definitive answer + only from official source → specified_source="US Congress official sites"
   - **"open"**: Default. For open-ended questions, even if you believe certain info "usually comes from X", do NOT use domain-derived specified

   ⚠️ **Important**: Open-ended questions forbid domain-derived specified — only user-literal specification is allowed. Do not use pretraining knowledge to decide sources for the user.

## Output Format

**Step 1: First output your analytical thinking** (marked with [Thinking]):
```
[Thinking]
1. Core question: What is this question essentially asking? What is the final answer it seeks?
2. Start entities: Which specific entities are explicitly mentioned in the question?
3. Research dimensions: What concepts/dimensions need independent research? (Extract from question text, not predictions)
4. Prerequisite check: For each concept, ask yourself: "If I search this directly, do I know what to search for?" If not, create a prerequisite node to first establish what it is
5. Dimension relationships: Are these dimensions independent or do they have dependencies?
6. Structure type: Is it serial dependency, parallel exploration, or convergence structure?
7. Potential omissions: Are there any easily overlooked implicit dimensions or constraints?
```

**Step 2: Based on your thinking, output the JSON cognitive graph** (do not wrap in markdown code blocks):
{{
  "thinking_summary": "One-sentence summary of the exploration strategy",
  "entities": [
    {{"id": "e1", "name": "Start Entity Name", "type": "start", "cognitive_state": "START", "hop_distance": 0}},
    {{"id": "e2", "name": "Research Concept Name", "type": "placeholder", "cognitive_state": "UNKNOWN",
     "type_constraint": "Type constraint", "condition_constraints": ["Condition 1"],
     "hop_distance": 1, "discovery_dependency": null}}
  ],
  "relations": [
    {{"id": "r1", "source": "e1", "target": "e2", "type": "Relation Type",
     "inquiry_goal": "Complete searchable question?", "state": "to_solve",
     "task_type": "open", "specified_source": null}}
  ],
  "structure": {{
    "type": "parallel",
    "parallel_chains": [],
    "convergence_nodes": [],
    "dependency_order": ["e1", "e2"]
  }},
  "languages": ["en", "zh"]
}}

## Structure Type Reference

| Type | Characteristics | Typical Question Pattern | Efficiency |
|------|----------------|--------------------------|------------|
| serial | A→B→C must follow order | "What is A's B's C?" | Lowest |
| parallel | A and B independent, no convergence | "Analyze aspects A, B, C of X" | ⭐ Common |
| convergence | A and B searched independently → converge and filter | "X that satisfies conditions A, B, and C" | ⭐ Highest |
| mixed | Combination of serial + parallel + convergence | Complex multi-step problems | Depends on design |

⚠️ **Prefer parallel/convergence**:
- If dimensions are **independent of each other**, they should be **searched in parallel**
- Use serial only when searching for B **requires** A's result

## Examples

### Example 1 (Serial Structure: Concepts with dependency)

Question: "Among the vendors providing data annotation for Meta in 2025, which ones have offices in Africa?"

Analysis:
- Two concept dimensions: Meta's data annotation suppliers, suppliers' African office distribution
- The second concept depends on the first's results (must know the suppliers first) → serial

Output:
{{
  "thinking_summary": "First search Meta suppliers, then check each supplier's African offices",
  "entities": [
    {{"id": "e1", "name": "Meta", "type": "start", "cognitive_state": "START", "hop_distance": 0}},
    {{"id": "e2", "name": "Data Annotation Suppliers", "type": "placeholder", "cognitive_state": "UNKNOWN",
     "type_constraint": "Data annotation company", "condition_constraints": ["Year 2025", "Serving Meta"], "hop_distance": 1}},
    {{"id": "e3", "name": "African Office Distribution of [Data Annotation Suppliers]", "type": "placeholder", "cognitive_state": "UNKNOWN",
     "condition_constraints": ["In Africa"], "hop_distance": 2, "discovery_dependency": "e2"}}
  ],
  "relations": [
    {{"id": "r1", "source": "e1", "target": "e2", "type": "Data annotation service",
     "inquiry_goal": "Which data annotation vendors did Meta use in 2025?", "state": "to_solve",
     "task_type": "open", "specified_source": null}},
    {{"id": "r2", "source": "e2", "target": "e3", "type": "Office location",
     "inquiry_goal": "In which African countries does [Data Annotation Suppliers] have offices?", "state": "to_solve",
     "task_type": "open", "specified_source": null}}
  ],
  "structure": {{
    "type": "serial",
    "parallel_chains": [],
    "convergence_nodes": [],
    "dependency_order": ["e1", "e2", "e3"]
  }},
  "languages": ["en", "zh"]
}}

### Example 2 (Specified Source Task, task_type=specified)

Question: "In 2023, The World's 50 Best restaurants as published by William Reed began to officially list restaurants 51-100 as well. Name any restaurants that are included on both the 2022 and 2023 lists and are situated in Germany."

Analysis:
- The question explicitly specifies the source: "as published by William Reed" → task_type="specified"
- Single concept dimension: German restaurants on both year lists

Output:
{{
  "thinking_summary": "Filter German restaurants appearing on both year lists from William Reed's official ranking",
  "entities": [
    {{"id": "e1", "name": "World's 50 Best Restaurants", "type": "start", "cognitive_state": "START", "hop_distance": 0}},
    {{"id": "e2", "name": "German Restaurants on Both 2022 and 2023 Lists", "type": "placeholder", "cognitive_state": "UNKNOWN",
     "type_constraint": "Restaurant", "condition_constraints": ["On both 2022 and 2023 lists", "Located in Germany"], "hop_distance": 1}}
  ],
  "relations": [
    {{"id": "r1", "source": "e1", "target": "e2", "type": "List inclusion",
     "inquiry_goal": "Which restaurants appear on both the World's 50 Best Restaurants 2022 and 2023 lists and are located in Germany?", "state": "to_solve",
     "task_type": "specified", "specified_source": "William Reed"}}
  ],
  "structure": {{
    "type": "serial",
    "parallel_chains": [],
    "convergence_nodes": [],
    "dependency_order": ["e1", "e2"]
  }},
  "languages": ["en", "de"]
}}

### Example 3 (⭐ Parallel Convergence Structure: Independent Multi-Condition Search + Intersection Filtering)

Question: "List all countries that meet ALL conditions: Is an EU member state; Doesn't have a monarchy; Had over 100,000 immigrants in 2022; Voting age is 18+ for EU elections."

Analysis:
- ⚠️ **Key Identification**: Multiple **independent conditions** must be satisfied simultaneously → Use **parallel convergence** structure
- Each condition is an independent research concept dimension
- Finally **converge and filter**: Take the intersection of all conditions

Output:
{{
  "thinking_summary": "4 independent conditions searched in parallel, then converged to take intersection",
  "entities": [
    {{"id": "e0", "name": "European Union", "type": "start", "cognitive_state": "START", "hop_distance": 0}},
    {{"id": "e1", "name": "EU Member States List", "type": "placeholder", "cognitive_state": "UNKNOWN",
     "type_constraint": "Country list", "condition_constraints": ["2024 EU member states"], "hop_distance": 1}},
    {{"id": "e2", "name": "Non-Monarchy Countries List", "type": "placeholder", "cognitive_state": "UNKNOWN",
     "type_constraint": "Country list", "condition_constraints": ["Government system is not monarchy"], "hop_distance": 1}},
    {{"id": "e3", "name": "Countries with Over 100K Immigrants in 2022", "type": "placeholder", "cognitive_state": "UNKNOWN",
     "type_constraint": "Country list", "condition_constraints": ["2022 immigrant count > 100,000"], "hop_distance": 1}},
    {{"id": "e4", "name": "Countries with Voting Age 18+", "type": "placeholder", "cognitive_state": "UNKNOWN",
     "type_constraint": "Country list", "condition_constraints": ["EU election voting age >= 18"], "hop_distance": 1}},
    {{"id": "e5", "name": "Countries Meeting All Conditions", "type": "placeholder", "cognitive_state": "UNKNOWN",
     "type_constraint": "Country list", "condition_constraints": ["Intersection of e1∩e2∩e3∩e4"],
     "hop_distance": 2, "discovery_dependency": "e1,e2,e3,e4"}}
  ],
  "relations": [
    {{"id": "r1", "source": "e0", "target": "e1", "type": "Member states",
     "inquiry_goal": "What are the EU member states as of 2024? List the complete roster", "state": "to_solve",
     "task_type": "open", "specified_source": null}},
    {{"id": "r2", "source": "e0", "target": "e2", "type": "Government system filter",
     "inquiry_goal": "Which European countries do not have a monarchy? Or which European countries are republics?", "state": "to_solve",
     "task_type": "open", "specified_source": null}},
    {{"id": "r3", "source": "e0", "target": "e3", "type": "Immigration data",
     "inquiry_goal": "Which European countries had over 100,000 immigrants in 2022?", "state": "to_solve",
     "task_type": "specified", "specified_source": "Eurostat"}},
    {{"id": "r4", "source": "e0", "target": "e4", "type": "Election rules",
     "inquiry_goal": "What is the voting age for European Parliament elections in each EU country as of 2024?", "state": "to_solve",
     "task_type": "open", "specified_source": null}},
    {{"id": "r5", "source": "e1", "target": "e5", "type": "Intersection filtering",
     "inquiry_goal": "Based on the collected data, filter for countries that simultaneously satisfy all 4 conditions", "state": "to_solve",
     "task_type": "open", "specified_source": null}}
  ],
  "structure": {{
    "type": "convergence",
    "parallel_chains": [["e0", "e1"], ["e0", "e2"], ["e0", "e3"], ["e0", "e4"]],
    "convergence_nodes": ["e5"],
    "dependency_order": ["e0", "e1", "e2", "e3", "e4", "e5"]
  }},
  "languages": ["en"]
}}

### Example 4 (Mixed Structure: Shared Concept Node + Parallel + Convergence)

Question: "In 2025, which annotation vendors did Meta's and Google's multimodal large models use respectively? Among these vendors, which ones simultaneously serve Chinese AI companies?"

Analysis:
- Both Meta and Google need to search for "annotation suppliers" — the **same research concept** → create one shared concept node
- Two edges (r1, r2) each contain different context (Meta vs Google), so searches won't be confused
- "Simultaneously serve" indicates convergence/intersection → e4 is the convergence node
- ⚠️ Do NOT create separate "Meta's Annotation Suppliers" and "Google's Annotation Suppliers" nodes → causes downstream edge multiplication

Output:
{{
  "thinking_summary": "Meta and Google share one 'Annotation Suppliers' concept node, each with its own edge for searching, then converge to find those also serving Chinese AI companies",
  "entities": [
    {{"id": "e1", "name": "Meta", "type": "start", "cognitive_state": "START", "hop_distance": 0}},
    {{"id": "e2", "name": "Google", "type": "start", "cognitive_state": "START", "hop_distance": 0}},
    {{"id": "e3", "name": "Multimodal Model Annotation Suppliers", "type": "placeholder", "cognitive_state": "UNKNOWN",
     "type_constraint": "Annotation vendor", "condition_constraints": ["Multimodal large models", "Year 2025"], "hop_distance": 1}},
    {{"id": "e4", "name": "Suppliers Also Serving Chinese AI Companies", "type": "placeholder", "cognitive_state": "UNKNOWN",
     "type_constraint": "Annotation vendor", "condition_constraints": ["Serving both Meta and Google", "Also serving Chinese AI companies"],
     "hop_distance": 2, "discovery_dependency": "e3"}}
  ],
  "relations": [
    {{"id": "r1", "source": "e1", "target": "e3", "type": "Annotation service",
     "inquiry_goal": "Which data annotation vendors does Meta use for its multimodal large models in 2025?", "state": "to_solve",
     "task_type": "open", "specified_source": null}},
    {{"id": "r2", "source": "e2", "target": "e3", "type": "Annotation service",
     "inquiry_goal": "Which data annotation vendors does Google use for its multimodal large models in 2025?", "state": "to_solve",
     "task_type": "open", "specified_source": null}},
    {{"id": "r3", "source": "e3", "target": "e4", "type": "Client service",
     "inquiry_goal": "Among [Multimodal Model Annotation Suppliers], which ones also serve Chinese AI companies?", "state": "to_solve",
     "task_type": "open", "specified_source": null}}
  ],
  "structure": {{
    "type": "mixed",
    "parallel_chains": [["e1", "e3"], ["e2", "e3"]],
    "convergence_nodes": ["e4"],
    "dependency_order": ["e1", "e2", "e3", "e4"]
  }},
  "languages": ["en", "zh"]
}}

### Example 5 (⭐ Open Research Question: Multi-dimension parallel exploration)

Question: "Analyze the competitive landscape of the global new energy vehicle market, focusing on mainstream technology route comparison, market share of leading companies, and the impact of national policies on the market."

Analysis:
- This is an **open-ended research question** with no single correct answer
- The question explicitly mentions 3 research dimensions: technology routes, market share, policy impact
- These dimensions are **independent** of each other, can be searched in parallel
- ⚠️ Do NOT predict what specific companies or technology routes will be discovered

Output:
{{
  "thinking_summary": "3 independent research dimensions searched in parallel: technology route comparison, leading company market share, national policy impact",
  "entities": [
    {{"id": "e1", "name": "Global NEV Market", "type": "start", "cognitive_state": "START", "hop_distance": 0}},
    {{"id": "e2", "name": "Mainstream Technology Route Comparison", "type": "placeholder", "cognitive_state": "UNKNOWN",
     "type_constraint": "Technology route analysis", "condition_constraints": ["New energy vehicles", "Mainstream technology routes"], "hop_distance": 1}},
    {{"id": "e3", "name": "Leading Company Market Share", "type": "placeholder", "cognitive_state": "UNKNOWN",
     "type_constraint": "Market data", "condition_constraints": ["Global market", "Leading companies"], "hop_distance": 1}},
    {{"id": "e4", "name": "National Policy Impact on Market", "type": "placeholder", "cognitive_state": "UNKNOWN",
     "type_constraint": "Policy analysis", "condition_constraints": ["Major countries/regions", "Impact on NEV market"], "hop_distance": 1}}
  ],
  "relations": [
    {{"id": "r1", "source": "e1", "target": "e2", "type": "Technology routes",
     "inquiry_goal": "What are the mainstream technology routes for global new energy vehicles? What are the pros, cons, and current development status of each?", "state": "to_solve",
     "task_type": "open", "specified_source": null}},
    {{"id": "r2", "source": "e1", "target": "e3", "type": "Market landscape",
     "inquiry_goal": "Who are the leading companies in the global new energy vehicle market? What are their respective market shares and competitive positions?", "state": "to_solve",
     "task_type": "open", "specified_source": null}},
    {{"id": "r3", "source": "e1", "target": "e4", "type": "Policy impact",
     "inquiry_goal": "What key policies do major countries and regions have for new energy vehicles? How do these policies affect the market competitive landscape?", "state": "to_solve",
     "task_type": "open", "specified_source": null}}
  ],
  "structure": {{
    "type": "parallel",
    "parallel_chains": [["e1", "e2"], ["e1", "e3"], ["e1", "e4"]],
    "convergence_nodes": [],
    "dependency_order": ["e1", "e2", "e3", "e4"]
  }},
  "languages": ["zh", "en"]
}}"""

COGNITIVE_GRAPH_UPDATE_PROMPT = """You are a Cognitive Graph Update Expert. Analyze Searcher's response and build the Planner's understanding of the world.

The cognitive graph is the Planner's evolving understanding — concise but analytically meaningful. Every search produces cognitive updates: either confirming/denying expectations, or revealing something unexpected. "Nothing found for this criterion" is itself a finding worth recording.

## Current Cognitive Graph
{current_graph}

## Search Task
Original Question: {original_question}
The user's research question — the ultimate goal this entire search is serving.

Node: {node_name}
Inquiry Goal: {inquiry_goal}
The Planner's hypothesis for addressing a specific dimension of the original question. Created before any search, based on pre-training knowledge — what the Planner assumed would be findable may not match reality.

**Acceptance Criteria** (the Planner's assumed directions for this inquiry):
The specific angles and information types the Planner expected to find. These carry the highest assumption bias — they define what the Planner thought existed, not necessarily what does.
- Core (must answer): {core_criteria}
- Supplementary (nice to have): {supplementary_criteria}

## Searcher Response
{search_result}

> The Searcher response above may be followed by a `## Source Quality Scores` block listing per-`[[n]]` CR and AAP scores (CR = 0.3·Currency + 0.7·Relevance, info density; AAP = 0.4·Authority + 0.35·Accuracy + 0.25·Purpose, trustworthiness; both on a 1-5 scale). The Current Cognitive Graph above also surfaces existing nodes' per-`[[n]]` historical CR/AAP under "Per-ref quality:". Use these scores when deciding between Supplement / Contradiction (see step 9) and when judging whether the new evidence is authoritative enough to update an existing finding.

## Update Tasks

1. **Extract Discovered Items**:
   - Item = an organization, product, or system that is a DIRECT ANSWER to this node's inquiry goal
   - Example: node asks "Meta's data annotation providers" → items = ["Scale AI", "Appen"]
   - Example: node asks "autonomous driving technical approaches" → items = ["RL route", "E2E route", "Modular route"]
   - ❌ NOT items: people names (unless the person IS the research subject), dates, events, conferences, technologies/methodologies (→ cross_item_findings), locations, metrics, statistics (→ item_findings or cross_item_findings)

2. **Record Findings** — Build the Planner's understanding of what was discovered:
   - **item_findings**: Per-item information — each discovered_item's independent attributes (keyed by item name)
     -归属测试: "Can this finding be attributed to ONE specific discovered_item?" → YES → item_findings
     - Each value = 1 analytical phrase with [[n]] citations (max 150 chars, truncated beyond)
     - Example: `{{"Scale AI": {{"market_position": "Dominant annotation provider for frontier labs, serving OpenAI/Anthropic [[0]][[2]]", "business_shift": "Pivoting from pure labeling to synthetic data + RLHF platform [[0]]"}}}}`
     - Example: `{{"RL route": {{"maturity": "Most mature approach with commercial deployments by Wayve and others [[0]]"}}}}`
     - ❌ Bad: `"headquarters": "SF [[0]]"` (keyword, no analytical value)
     - ✅ Good: `"competitive_edge": "Proprietary workforce management platform enabling 100K+ annotators [[0]]"` (concise but meaningful)
   - **cross_item_findings**: Cross-item patterns, trends, or insights NOT attributable to any single item
     - 归属测试: "Can this finding be attributed to ONE specific discovered_item?" → NO → cross_item_findings
     - Each value = 1 sentence (string) OR an ordered list (array of strings) for rankings/enumerations
     - String value: WHAT was found + WHY it matters, with [[n]] citations (max 150 chars, truncated beyond)
     - List value: Each item = 1 entry with [[n]] citation, for ranked lists, discovered items, or ordered enumerations
     - String example: `{{"pricing_evolution": "Per-task pricing giving way to outcome-based contracts as clients demand quality guarantees [[2]]"}}`
     - List example: `{{"TIOBE_ranking": ["#1 Python — dominant in AI/data science [[0]]", "#2 C++ — systems programming backbone [[0]]", "#3 Java — enterprise standard [[0]]"]}}`
     - ❌ Bad: `"pricing_model": "per-task pricing [[2]]"` (label, no insight)
     - ✅ Good (string): `"industry_shift": "Annotation shifting from manual to AI-assisted, cutting costs but raising quality concerns [[1]][[3]]"`
     - ✅ Good (list): Use list when Searcher discovers an ordered set (rankings, top-N, enumerations) that Planner needs to see in full
   - ⚠️ **RULE: When Searcher finds a ranking, top-N list, or ordered enumeration, you MUST store the complete ordered list as an array in cross_item_findings** (e.g. `"ranking_by_revenue": ["#1 Company A [[0]]", "#2 Company B [[0]]", ...]`). Do NOT only put names in discovered_items — Planner needs the ordered list with ranking context to decide next steps.
   - **Negative findings**: When expected information is NOT found, record this as a finding with explanation from Searcher's text
     - Example: `"official_partnership": "No public record of formal partnership; Searcher found only indirect mentions via third-party reports [[3]]"`
   - ⚠️ All findings must be distilled from Searcher's response with [[n]] citations — do NOT fabricate
   - ⚠️ **Findings vs search_experience — the ONLY test**: Does the finding text describe **actual information** (numbers, dates, facts, names, descriptions)? → findings. Does it describe **inability to access or obtain data** (blocked, not published, login required)? → search_experience. **These are MUTUALLY EXCLUSIVE — the same information MUST NOT appear in both.** If it goes into search_experience, do NOT also record it in item_findings, cross_item_findings, or unexpected_discoveries.

3. **Compare Findings vs Criteria (⚠️ KEY TASK)**:
   - Check which **core_criteria** are satisfied by the findings → remaining goes to `core_pending`
   - Check which **supplementary_criteria** are satisfied → remaining goes to `supplementary_pending`
   - `core_pending` and `supplementary_pending` should ONLY contain unsatisfied criteria

4. **Capture Unexpected Discoveries**:
   - Planner created this task based on pre-existing knowledge (educated guesses). When reality diverges — things work differently than assumed, unexpected connections exist, contradictions emerge — record these
   - A finding qualifies only if BOTH are true:
     (1) Would it help address the dimension of the Original Question that the Inquiry Goal is trying to resolve? (If the Planner knew this, would it change how it searches for or understands this node?)
     (2) Is it completely outside all Acceptance Criteria directions?
   - Each discovery = 1 sentence explaining WHAT was found + WHY it's unexpected, with [[n]] citations
   - **Rank by contribution to the Inquiry Goal** (most relevant first)
   - ⚠️ Must include citation [[n]] for traceability
   - Example: `["Scale AI acquiring annotation startups signals market consolidation, contradicting the assumed fragmented landscape [[0]]", "Annotation quality metrics are proprietary and non-standardized, undermining cross-provider comparison [[2]]"]`

5. **Identify Cited References**: Extract all [[n]] citation indices → cited_refs

6. **Note Temporal Context**: Time-sensitive info (dates, "as of") → temporal_notes
   - Must be a **single string**, NOT an object
   - Example: "Scale AI terminated June 2024; data as of Dec 2023"

7. **Determine Cognitive State**:
   - UNKNOWN: Searcher found content but nothing relevant to ANY criteria (still record negative findings and any unexpected discoveries)
   - PARTIAL: Some core criteria answered, but core_pending is not empty
   - KNOWN: All core criteria satisfied (core_pending is empty)
   - ⚠️ **Existence metadata trap**: A criterion is satisfied ONLY when findings contain **actual usable values** (numbers, facts, names, dates) that can be directly cited in the final report. If findings merely describe WHERE data exists or WHAT a source offers (e.g., "Platform X provides Y data", "Source Z has historical records") without extracting the actual data values → the criterion is NOT satisfied → keep it in core_pending

8. **New Edge Rules**:
   - Only create new edge if discovering **completely unexpected** relationship
   - Maximum 1-2 new edges

9. **Updating Existing item_findings / cross_item_findings Attributes** (⚠️ NEW MECHANICS):

When this search touches an attribute that ALREADY appears on the Current Cognitive Graph (same `item.attr` or same `aspect`):

   Decide which of A1 / A2 / B / C applies before writing the update.

   **A1) Redundant corroboration** — the new search restates the SAME fact already in the existing attribute, fully and identically (same meaning, same conclusion, no new detail / angle / qualifier).
   - Emit the attribute with the SAME content text, but append the new [[n]] alongside the existing one(s).
   - Example: existing value `"35% market share [[5]]"`, new source confirms the same number → output `"35% market share [[5]][[8]]"`.
   - This records corroborating evidence. Do NOT emit a `contradictions` entry — there is no conflict.
   - ⚠️ Bar for "redundant" is HIGH: the new source must fully support the existing claim. If any part is new (date, magnitude, qualifier, mechanism), treat as A2 instead.

   **A2) Genuine merge / supplement** — the new search adds a NEW fact, angle, or detail that's compatible with (not contradicting) the existing claim.
   - Output a new full value for the attribute weaving together facts from both the existing graph and the new search.
   - **Every factual claim must carry the specific [[n]] that supports it** — re-cite the old [[n]] for old facts, cite the new [[n]] for new facts.
   - Do NOT emit a `contradictions` entry — merging is not a conflict; the merged value itself is the audit trail.

   **B) Contradiction (conflicting evidence)**
   - You MUST emit a `contradictions` entry FIRST, then update `item_findings` / `cross_item_findings` with the chosen value.
   - Required fields:
     * `criterion`: dotted path, e.g. `"Scale AI.founding_year"` or cross-item aspect
     * `old_claim`: full snapshot of the previous value (copy verbatim from Current Cognitive Graph)
     * `new_claim`: the new full value, citing the new sources via their [[n]]
     * `old_quality`: `{{"cr": <float>, "aap": <float>}}` — copy the historical [[n]]'s CR/AAP from the "Per-ref quality:" section in Current Cognitive Graph (use the most directly relevant old [[n]])
     * `new_quality`: `{{"cr": <float>, "aap": <float>}}` — pull from the `## Source Quality Scores` block at the end of Searcher Response
     * `resolution`: short reason citing the numeric comparison, e.g. `"new AAP 4.2 > old AAP 2.5; SEC primary source supersedes general media"`
     * `kept`: `"new"` or `"old"`
   - When `kept="new"`: also overwrite the item_findings/cross_item_findings attr with the new value (so writers see the updated text).
   - When `kept="old"`: do not include the attr in this round's item_findings (leaving the existing one untouched).

   **C) Pure-additive (brand new attr or brand new aspect)**
   - Just emit the new attr as usual. No contradictions entry needed.

   ⚠️ Per-fact citation discipline: every concrete fact (date, number, name, claim) must point to the [[n]] that actually supports it. When merging (A2), do not attach a single [[n]] to a chain of facts where it only supports part of the chain.

10. **Assess Evidence Strength** (for Planner quality-driven decisions):
   - `finding_strength`: How substantive are the item_findings + cross_item_findings from this search?
     - "strong": Multiple criteria answered with detailed, cited evidence
     - "moderate": Some criteria answered but gaps remain or evidence is thin
     - "weak": Minimal findings, mostly gaps
     - "none": No relevant findings at all
   - `unexpected_strength`: How significant are the unexpected_discoveries?
     - "strong": Discoveries that fundamentally change understanding or open major new directions
     - "moderate": Interesting leads worth noting but not game-changing
     - "weak": Minor observations
     - "none": No unexpected discoveries

11. **Record Search Experience** (ONLY for findings that describe inability to get data):
   - search_experience records **only two situations**:
     (a) **Access failure**: Page could not be loaded at all (429 error, CAPTCHA, timeout)
     (b) **Confirmed data absence**: The finding text states data does not exist or is not publicly available — it describes unavailability, not actual information
   - If a finding describes ANY actual information (even old, tangential, or partial) → that's a finding, NOT search_experience
   - Format: `"[[n]] <source site/name> — <what data> inaccessible because <reason>"`
   - Example: `["[[3]] SEC EDGAR — supplier contract amounts not disclosed in any filing [[3]]", "[[10]] Investing.com — daily gold price CSV blocked by Cloudflare CAPTCHA [[10]]"]`
   - ⚠️ Look for the `### Accessibility Barriers` section in the Searcher response — those items should go directly into search_experience, NOT into findings
   - These help Planner avoid repeating the same dead-end searches

## Output Format
Output JSON directly:
{{
  "target_node_id": "<node id from graph, e.g., e2>",
  "finding_strength": "moderate",
  "unexpected_strength": "weak",
  "new_cognitive_state": "PARTIAL",
  "cognitive_updates": [
    {{
      "node_id": "<node id>",
      "state": "PARTIAL",
      "discovered_items": ["Scale AI", "Surge AI"],
      "item_findings": {{
        "Scale AI": {{
          "market_position": "Dominant annotation provider for frontier LLM labs, serving OpenAI/Anthropic [[0]][[2]]",
          "business_shift": "Pivoting from pure labeling toward synthetic data and RLHF platform [[0]]"
        }},
        "Surge AI": {{
          "positioning": "Smaller competitor focusing on specialized high-quality labeling for NLP tasks [[1]]"
        }}
      }},
      "cross_item_findings": {{
        "industry_consolidation": "Top 3 providers control 60%+ of LLM annotation market, driving M&A activity [[1]][[2]]",
        "quality_challenge": "No standardized quality metrics across providers; each uses proprietary evaluation [[2]]",
        "market_share_ranking": ["#1 Scale AI — 35% market share [[0]]", "#2 Labelbox — 18% market share [[1]]", "#3 Surge AI — 12% market share [[2]]"]
      }},
      "core_pending": ["collaboration_timeline"],
      "supplementary_pending": ["service_scale"],
      "unexpected_discoveries": [
        "Scale AI acquiring annotation startups signals market consolidation, contradicting assumed fragmented landscape [[0]]",
        "Annotation pricing shifted from per-task to outcome-based contracts in 2024, fundamentally changing provider economics [[2]]"
      ],
      "cited_refs": [0, 1, 2],
      "temporal_notes": "Scale AI acquisition in Q2 2024 [[0]]; Surge AI data as of 2023 [[1]]",
      "contradictions": [
        {{
          "criterion": "Scale AI.founding_year",
          "old_claim": "Founded 2018 according to general tech press [[7]]",
          "old_quality": {{"cr": 3.0, "aap": 2.5}},
          "new_claim": "Founded August 2016 per SEC S-1 filing [[2]]",
          "new_quality": {{"cr": 4.5, "aap": 4.2}},
          "resolution": "new AAP 4.2 > old AAP 2.5; SEC primary filing supersedes media",
          "kept": "new"
        }}
      ],
      "search_experience": ["[[3]] Contract amounts are trade secrets — SEC filings and press releases do not disclose [[3]]"]
    }}
  ],
  "quality_gaps": ["source authority insufficient"],
  "new_edges": []
}}

Notes:
- `item_findings`: Per-item analytical phrases — Planner uses these to understand WHAT each discovered item is and WHY it matters (keyed by item name from discovered_items)
- `cross_item_findings`: Cross-item patterns and trends — Planner uses these to understand broader context. Values can be strings (single insight) or arrays (ranked/ordered lists)
- `core_pending`: Unsatisfied core criteria (empty = all core satisfied)
- `supplementary_pending`: Unsatisfied supplementary criteria
- `unexpected_discoveries`: Findings that help address the Inquiry Goal's dimension but are outside all Acceptance Criteria directions, ranked by contribution to the Inquiry Goal (must include [[n]])
- ⚠️ pending is the REMAINDER after comparing criteria with findings, not a copy of criteria
- contradictions: empty array [] if none; populate ONLY when an existing item_findings/cross_item_findings attribute conflicts with new evidence (case B in step 9 — kept is "new" or "old"). Supplement / corroboration (A1, A2) do NOT produce contradictions entries. Each entry needs criterion + old_claim + new_claim + old_quality + new_quality + resolution + kept.
- new_edges: usually empty unless a truly unexpected relationship is discovered
- search_experience: ONLY access failures (429/CAPTCHA) or confirmed data absence (evidence has NO actual values) — anything with real data values is a finding
- ⚠️ **START nodes (📌) are IMMUTABLE search anchors** — do NOT include any START node in `cognitive_updates`. If you discover information about a START entity, record it on the relevant exploration node instead."""

COGNITIVE_GRAPH_UPDATE_PROMPT_CN = """你是认知图更新专家。分析Searcher的回复，构建Planner对世界的理解。

认知图是Planner不断演进的世界理解——精炼但有分析含义。每次搜索都会产生认知更新：要么证实/否定预期，要么揭示意外发现。"没有找到某个criterion的信息"本身就是值得记录的发现。

## 当前认知图
{current_graph}

## 搜索任务
原始问题: {original_question}
用户的研究问题——本次搜索最终服务的目标。

节点: {node_name}
探索目标: {inquiry_goal}
Planner 对如何解决原始问题某个维度的假设。在任何搜索发生前基于预训练知识生成——Planner 预设可以找到的内容未必与现实一致。

**验收标准**（Planner 对本次探索的假设方向）:
Planner 预期可以找到的具体角度和信息类型。这是假设偏差最高的部分——定义的是 Planner 认为存在的内容，不一定是实际存在的。
- 核心（必须回答）: {core_criteria}
- 补充（有则更好）: {supplementary_criteria}

## Searcher回复
{search_result}

> 上方 Searcher 回复末尾可能附带 `## Source Quality Scores` 块，列出本轮每个 `[[n]]` 的 CR / AAP 评分（CR = 0.3·Currency + 0.7·Relevance，信息密度；AAP = 0.4·Authority + 0.35·Accuracy + 0.25·Purpose，可信度；均为 1-5 分）。当前认知图的节点摘要里也会通过"Per-ref quality:"显示历史 `[[n]]` 的 CR/AAP。在第 9 步决定 Supplement / Contradiction，以及判断"新证据是否足够权威以更新已有 finding"时，请参考这些分数。

## 更新任务

1. **提取发现的项目**:
   - 项目 = 直接回答该节点inquiry goal的组织、产品或系统
   - 例: 节点问"Meta的数据标注供应商" → 项目 = ["Scale AI", "Appen"]
   - 例: 节点问"自动驾驶技术路线" → 项目 = ["RL路线", "端到端路线", "模块化路线"]
   - ❌ 不是项目: 人名（除非人物本身就是研究对象）、日期、事件、会议、技术/方法论（→ cross_item_findings）、地点、指标/统计数据（→ item_findings 或 cross_item_findings）

2. **记录发现** — 构建Planner对搜索结果的理解：
   - **item_findings**: 每个项目的独立信息（按项目名作为key）
     - 归属测试: "这个发现能归属到discovered_items中的某一个项目吗？" → 能 → item_findings
     - 每个值 = 1个带 [[n]] 引用的分析性短语（上限150字符，超出会被截断）
     - 例: `{{"Scale AI": {{"市场定位": "头部LLM标注商，服务OpenAI/Anthropic等前沿实验室 [[0]][[2]]", "业务转型": "从纯标注向合成数据+RLHF平台转型 [[0]]"}}}}`
     - 例: `{{"RL路线": {{"成熟度": "最成熟的方案，Wayve等已有商用部署 [[0]]"}}}}`
     - ❌ 差: `"总部": "旧金山 [[0]]"`（关键词，无分析价值）
     - ✅ 好: `"竞争优势": "自研劳动力管理平台，支撑10万+标注员规模化运营 [[0]]"`（精炼但有含义）
   - **cross_item_findings**: 跨项目的模式、趋势或不归属于任何单一项目的洞察
     - 归属测试: "这个发现能归属到discovered_items中的某一个项目吗？" → 不能 → cross_item_findings
     - 每个值 = 1句话（字符串）或有序列表（数组），用于排名/枚举
     - 字符串值: 发现了什么 + 为什么重要，带 [[n]] 引用（上限150字符，超出会被截断）
     - 列表值: 每条 = 1个带 [[n]] 引用的条目，适用于排名、top-N、有序发现
     - 字符串例: `{{"定价演变": "按任务计费正让位于效果导向合同，客户开始要求质量保证 [[2]]"}}`
     - 列表例: `{{"TIOBE排名": ["#1 Python——AI/数据科学主导 [[0]]", "#2 C++——系统编程基石 [[0]]", "#3 Java——企业级标准 [[0]]"]}}`
     - ❌ 差: `"定价模式": "按任务计费 [[2]]"`（标签，无洞察）
     - ✅ 好（字符串）: `"行业整合": "头部3家供应商控制60%以上LLM标注市场，推动并购活跃 [[1]][[2]]"`
     - ✅ 好（列表）: 当Searcher发现有序集合（排名、top-N、枚举）且Planner需要完整列表时使用
   - ⚠️ **规则：当Searcher发现排名、top-N列表或有序枚举时，必须将完整有序列表作为数组存入cross_item_findings**（如 `"营收排名": ["#1 公司A [[0]]", "#2 公司B [[0]]", ...]`）。不要只把名称放入discovered_items——Planner需要带排名上下文的有序列表来决定下一步行动。
   - **负面发现**: 当预期信息未找到时，将其作为发现记录，并附上来自Searcher原文的原因
     - 例: `"官方合作关系": "无公开的正式合作记录，Searcher仅在第三方报告中发现间接提及 [[3]]"`
   - ⚠️ 所有发现必须从Searcher回复中提炼并带 [[n]] 引用——不可编造
   - ⚠️ **发现 vs 经验——唯一判据**：finding 文本描述的是**实际信息**（数字、日期、事实、名称、描述）？→ findings。描述的是**无法获取数据**（被屏蔽、未公开、需登录）？→ search_experience。**两者互斥——同一条信息不得同时出现在两边。** 如果记入了 search_experience，就不得再记入 item_findings、cross_item_findings 或 unexpected_discoveries。

3. **对比发现与验收标准（⚠️ 关键步骤）**:
   - 检查哪些 **core_criteria** 已被发现满足 → 剩余未满足的放入 `core_pending`
   - 检查哪些 **supplementary_criteria** 已被满足 → 剩余未满足的放入 `supplementary_pending`
   - `core_pending` 和 `supplementary_pending` 应**仅包含**尚未满足的标准

4. **捕捉意外发现**:
   - Planner基于已有知识（对世界的预设）创建任务。当现实与预设出现偏差——事物运作方式与假设不同、存在意外关联、出现矛盾——记录这些
   - 满足以下两个条件的发现才算意外发现：
     (1) 是否有助于解决原始问题中、探索目标所负责的那个维度？（如果 Planner 知道这个发现，会改变它对这个节点的搜索方向或理解方式吗？）
     (2) 是否完全在所有验收标准方向之外？
   - 每条 = 1句话说明发现了什么 + 为什么意外，带 [[n]] 引用
   - **按对探索目标的贡献程度排序**（最相关的排在前面）
   - ⚠️ 必须包含引用 [[n]] 以便溯源
   - 例: `["Scale AI收购标注创业公司表明市场走向整合，与此前假设的分散格局相矛盾 [[0]]", "标注定价在2024年从按任务转向效果导向合同，根本性改变了供应商经济模型 [[2]]"]`

5. **识别被引用的参考来源**: 提取所有 [[n]] 引用索引 → cited_refs

6. **记录时间上下文**: 时间敏感信息（日期、"截至"等）→ temporal_notes
   - 必须是**单个字符串**，不能是对象
   - 示例: "Scale AI于2024年6月终止合作; 数据截至2023年12月"

7. **判定认知状态**:
   - UNKNOWN: Searcher搜到了内容但没有任何信息与criteria相关（仍需记录负面发现和意外发现）
   - PARTIAL: 部分核心标准已回答，但core_pending不为空
   - KNOWN: 所有核心标准均已满足（core_pending为空）
   - ⚠️ **存在性元数据陷阱**: 标准被满足的前提是findings包含**可直接引用到最终报告的实际信息值**（数字、事实、名称、日期），而非仅描述数据源的存在性。如果findings主要是"X平台提供Y数据"、"Z来源有历史记录"这类元描述，而没有提取出实际数据值 → 该标准未满足 → 保留在core_pending中

8. **新建边规则**:
   - 仅在发现**完全意外**的关系时才创建新边
   - 最多1-2条新边

9. **更新已有的 item_findings / cross_item_findings 属性**（⚠️ 新机制）：

当本次搜索触及**当前认知图中已经存在**的同一个 `item.attr` 或同一个 `aspect` 时：

   写入前先判断属于 A1 / A2 / B / C 哪一种。

   **A1) 冗余佐证（Redundant corroboration）**——新搜索完全重复已有 attr 的同一条事实（含义相同、结论相同，没有新细节、新角度、新限定）。
   - 发出 attr，**内容文本完全保持原样**，只在已有 [[n]] 旁边追加新的 [[n]]。
   - 示例：现值 `"35% 市场份额 [[5]]"`，新源确认了同一个数字 → 输出 `"35% 市场份额 [[5]][[8]]"`。
   - 这条记录的是"另一个来源也支持这条事实"，**不要**写 contradictions 条目——这不是冲突。
   - ⚠️ "冗余"的门槛**很高**：新源必须**完全**支撑已有 claim 的全部含义。任何一处不同（日期、量级、限定词、机制说明），都按 A2 处理。

   **A2) 合并/补充（Genuine merge）**——新搜索带来了**新的事实/角度/细节**，与已有 claim 兼容（不冲突）。
   - 输出一个完整的新值，把已有图里的旧事实和本轮搜索的新事实编织在一起。
   - **每个事实声明都必须带具体支撑它的 [[n]]**——旧事实重新引用旧 [[n]]，新事实引用新 [[n]]。
   - **不要**写 contradictions 条目——合并不是冲突，合并后的 value 本身就是审计轨迹。

   **B) 矛盾（Contradiction，证据实质冲突）**
   - 必须**先**写一条 `contradictions` 条目，**然后**在 item_findings / cross_item_findings 里用新值覆盖。
   - 必填字段：
     * `criterion`：点分路径，如 `"Scale AI.创立年份"` 或 cross-item aspect 名
     * `old_claim`：从"当前认知图"中**逐字**复制旧值
     * `new_claim`：完整的新值，用新来源的 [[n]] 引用
     * `old_quality`：`{{"cr": <浮点>, "aap": <浮点>}}` — 从当前认知图"Per-ref quality:"行复制最相关的旧 [[n]] 的 CR/AAP
     * `new_quality`：`{{"cr": <浮点>, "aap": <浮点>}}` — 从 Searcher 回复末尾的 `## Source Quality Scores` 块取最相关新 [[n]] 的 CR/AAP
     * `resolution`：简短理由，引用数字对比，如 `"new AAP 4.2 > old AAP 2.5；SEC 一级文件覆盖一般媒体"`
     * `kept`：`"new"` / `"old"`
   - `kept="new"`：同步用新值覆盖 item_findings/cross_item_findings 该 attr（让写作端看到更新后的文本）。
   - `kept="old"`：本轮不要把该 attr 放进 item_findings（已有的保持不动）。

   **C) 纯新增（全新 attr 或全新 aspect）**
   - 像往常一样直接发出新 attr。无需 `contradictions` 条目。

   ⚠️ **每事实必带准确引用**：每个具体事实（日期、数字、名称、声明）都要指向**真正支撑它**的那个 [[n]]。A2 合并时，不要把一个 [[n]] 笼统挂在一串事实后面，让它在事实链中只能支撑其中一部分。

10. **评估证据强度**（用于Planner的质量驱动决策）:
   - `finding_strength`: 本次搜索的item_findings + cross_item_findings实质性如何？
     - "strong": 多个criteria有详细、有引用的证据
     - "moderate": 部分criteria有回答但仍有缺口或证据较薄
     - "weak": 发现极少，大部分是缺口
     - "none": 没有任何相关发现
   - `unexpected_strength`: unexpected_discoveries的重要性如何？
     - "strong": 根本性改变理解或开辟重大新方向
     - "moderate": 有价值的线索但非颠覆性
     - "weak": 次要观察
     - "none": 无意外发现

11. **记录搜索经验**（仅限描述无法获取数据的内容）:
   - search_experience **仅记录两种情况**：
     (a) **访问失败**：页面完全无法加载（429错误、验证码、超时）
     (b) **确认数据不存在**：finding文本声明数据不存在或未公开——描述的是不可得，而非实际信息
   - 如果finding描述了任何实际信息（即使过时、间接、不完整）→ 那是发现，不是经验
   - 格式：`"[[n]] <来源网站/名称> — <什么数据>因为<什么原因>不可获得"`
   - 示例：`["[[3]] SEC EDGAR — 供应商合同金额在所有文件中均未披露 [[3]]", "[[10]] Investing.com — 每日金价CSV被Cloudflare验证码拦截 [[10]]"]`
   - ⚠️ 注意查看Searcher回复中的 `### Accessibility Barriers` 部分——其中的条目应直接放入 search_experience，而非 findings
   - 帮助Planner避免重复搜索同样的死路

## 输出格式
直接输出JSON:
{{
  "target_node_id": "<图中的节点id，例如 e2>",
  "finding_strength": "moderate",
  "unexpected_strength": "weak",
  "new_cognitive_state": "PARTIAL",
  "cognitive_updates": [
    {{
      "node_id": "<节点id>",
      "state": "PARTIAL",
      "discovered_items": ["Scale AI", "Surge AI"],
      "item_findings": {{
        "Scale AI": {{
          "市场定位": "头部LLM标注商，服务OpenAI/Anthropic等前沿实验室 [[0]][[2]]",
          "业务转型": "从纯标注向合成数据+RLHF平台转型 [[0]]"
        }},
        "Surge AI": {{
          "定位": "专注NLP领域高质量标注的小型竞争者 [[1]]"
        }}
      }},
      "cross_item_findings": {{
        "行业整合": "头部3家供应商控制60%以上LLM标注市场，推动并购活跃 [[1]][[2]]",
        "质量挑战": "供应商间无统一质量标准，各自使用内部评估体系 [[2]]",
        "市场份额排名": ["#1 Scale AI——35%市场份额 [[0]]", "#2 Labelbox——18%市场份额 [[1]]", "#3 Surge AI——12%市场份额 [[2]]"]
      }},
      "core_pending": ["合作时间线"],
      "supplementary_pending": ["服务规模"],
      "unexpected_discoveries": [
        "Scale AI收购标注创业公司表明市场走向整合，与此前假设的分散格局相矛盾 [[0]]",
        "标注定价在2024年从按任务转向效果导向合同，根本性改变了供应商经济模型 [[2]]"
      ],
      "cited_refs": [0, 1, 2],
      "temporal_notes": "Scale AI收购发生在2024年Q2 [[0]]; Surge AI数据截至2023年 [[1]]",
      "contradictions": [
        {{
          "criterion": "Scale AI.创立年份",
          "old_claim": "据科技媒体报道创立于 2018 [[7]]",
          "old_quality": {{"cr": 3.0, "aap": 2.5}},
          "new_claim": "根据 SEC S-1 备案文件，创立于 2016 年 8 月 [[2]]",
          "new_quality": {{"cr": 4.5, "aap": 4.2}},
          "resolution": "new AAP 4.2 > old AAP 2.5；SEC 一级文件覆盖一般媒体",
          "kept": "new"
        }}
      ],
      "search_experience": ["[[3]] 合同金额属于商业机密——SEC文件和新闻稿均无披露 [[3]]"]
    }}
  ],
  "quality_gaps": ["来源权威性不足"],
  "new_edges": []
}}

说明:
- `item_findings`: 每个项目的精炼分析性短语——Planner通过这些理解每个项目是什么、为什么重要（按项目名作为key）
- `cross_item_findings`: 跨项目的模式和趋势——Planner通过这些理解更广泛的背景。值可以是字符串（单条洞察）或数组（排名/有序列表）
- `core_pending`: 仍未满足的核心验收要点（空=核心全部满足）
- `supplementary_pending`: 仍未满足的补充验收要点
- `unexpected_discoveries`: 有助于探索目标所负责维度、但在所有验收标准方向之外的发现，按对探索目标的贡献程度排序（必须带引用 [[n]]）
- ⚠️ pending 是对比 criteria 和 findings 后的剩余，不是原样复制 criteria
- contradictions: 无冲突时为空数组 []; **仅在**已有 item_findings/cross_item_findings 属性与新证据**实质冲突**时填充（第 9 步 B 情况，kept 为 "new" 或 "old"）。补充 / 冗余佐证（A1、A2）**不写**入 contradictions。每条至少包含 criterion + old_claim + new_claim + old_quality + new_quality + resolution + kept。
- new_edges 通常为空，除非发现必须追踪的新关系
- search_experience: 仅限访问失败（429/验证码）或确认数据不存在（证据中无任何实际值）——有实际数据值的都是 findings
- ⚠️ **START节点（📌）是不可变的搜索锚点** —— `cognitive_updates` 中不要包含任何START节点。如果搜索中发现了关于START实体的信息，请记录在相关的探索节点上。"""

PHASE1_RESTRUCTURE_PROMPT = """你是认知图重构评估专家。Planner提出了重构认知图的建议，你需要评估提案并输出具体变更操作。

**核心原则**: 重构是手术刀而非推倒重建。已有发现(findings)是已完成的探索记录，不需要担心与新概念矛盾。重构只修已知的错误，不推翻全局设计。

## 原始用户问题
{original_question}

## 当前图拓扑（树形视图 + 保护状态 + 级联影响）
{restructure_view}

## Planner重构提案
{planner_reasoning}

## 可用操作（5种）

### 1. modify_node — 修改节点属性（不动 findings/state）
- `name`, `type_constraint`, `condition_constraints`: 所有节点均可修改
- `core_pending`, `supplementary_pending`: 所有节点均可修改（搜索策略始终可调整，即使受保护节点也可以）
- `remove_items`: 要移除的 discovered_items 列表（同时清除其 item_findings）。用于 Planner 清理非研究对象的噪音项目
- ⚠️ 这是重命名受保护节点的唯一方式（不需要 delete+recreate）

### 2. modify_edge — 修改边属性
- 可修改边: inquiry_goal 和 target 均可改
- 受保护边(solved/exhausted/investigating): 仅允许改 inquiry_goal（已搜完或搜索中，只改标签）
- ⚠️ modify_edge 不能改变边的状态(state)，状态由搜索系统自动管理。要放弃某个维度，请用 remove_node 删除目标节点

### 3. add_node / add_edge — 添加新节点/边
- 节点ID格式: `e_dyn_N`，边ID格式: `r_dyn_N`
- 新节点必须有入边连接到图中
- inquiry_goal必须完整自包含，依赖前置节点用 `[节点名]` 占位符

### 4. remove_node — 删除节点（级联删除所有关联边）
- 受保护节点不可删除
- 这是移除整个维度的唯一方式（边会随节点一起删除）
- ⚠️ 不存在 remove_edge 操作 — 只能通过删除节点来移除边
- ⚠️ 删除操作可能产生孤儿节点（Phase 2 会处理，但请尽量保持连通性）

## 评估要点
1. 变更是否服务于回答原始问题？
2. 是否基于搜索发现（而非凭空想象）？
3. ⚠️ **问题完整性（最高优先级，执行以下检查）**:
   删除维度节点前，**必须回读原始用户问题**，逐项核对：
   - **该维度是否对应用户问题中显式提到或直接隐含的信息需求？**
     - 如果是 → **禁止删除**，设置 should_restructure=false，evaluation 中明确告知 Planner：
       "REJECTED: 维度'XXX'对应用户原始问题中要求的'YYY'，这是用户明确要求回答的内容，不能删除。如果搜索困难，应更换搜索策略而非放弃该维度。"
     - 如果否 → 继续判断：
       - **裁剪(R_prune)**：搜索证明该维度对回答原始问题确实无帮助 → 允许直接删除
       - **其他重构中的删除**：该维度仍有帮助但需要换形式（如轴旋转中合并为criteria）→ 必须以其他形式保留，不允许静默丢失
   - **常见错误模式**：Planner 以"搜索困难""信息不充分""没找到结果"为由要求删除维度——这不是删除理由，应 REJECT 并建议换搜索角度
4. 参考级联影响表，理解删除操作的下游影响。
5. **概念演化(R_conc/R_aug)**：如果Planner提议将抽象概念节点展开为多个子概念节点（基于搜索发现的子类别），或补充对回答原始问题有重要帮助的新维度，这是正常的认知图演化操作，应积极支持。
6. ⚠️ **信息流边界**: Searcher只能自动看到直接上游认知节点的发现，无法访问完整认知图。新增节点必须能通过搜索获取**新**信息来回答。**拒绝**纯汇总型节点（如"综合节点A和B的发现"）——Searcher无法跨多个任意节点汇总数据。如果需要跨节点推理后再搜索，应由Planner在[思考]中完成推理，然后创建针对性的搜索节点。
7. **纠错(R_correct)**：如果Planner提议废弃基于错误前提的下游结构并替换为正确方向，确认搜索发现确实推翻了该前提。纠错后的新方向仍必须服务于回答原始问题，只是从事实支撑的角度切入。

## 输出格式

先输出[评估]分析，再输出JSON（不要用markdown代码块）：
{{
  "should_restructure": true/false,
  "evaluation": "一句话评估结论",
  "changes": [
    {{"action": "modify_node", "id": "节点id", "reason": "原因",
      "name": "新名称", "type_constraint": "新约束", "condition_constraints": ["条件"],
      "core_pending": ["更新后的标准"], "remove_items": ["要移除的噪音项目"]}},
    {{"action": "modify_edge", "id": "边id", "reason": "原因",
      "new_inquiry_goal": "新问题", "new_target_id": "新目标节点id"}},
    {{"action": "add_node", "id": "e_dyn_1", "reason": "原因",
      "node_data": {{"id": "e_dyn_1", "name": "概念名", "type": "placeholder",
       "type_constraint": "约束", "condition_constraints": [], "hop_distance": 1}}}},
    {{"action": "add_edge", "id": "r_dyn_1", "reason": "原因",
      "edge_data": {{"id": "r_dyn_1", "source": "源id", "target": "目标id",
       "type": "关系", "inquiry_goal": "问题?", "task_type": "open"}}}},
    {{"action": "remove_node", "id": "节点id", "reason": "原因"}}
  ]
}}

## 场景示例

### 示例1：概念具象化（下游只有一个概念节点，不需要轴旋转）

Planner提案："搜索'技术路线分类'发现4种路线（强化学习、端到端、模块化、仿真到真实），展开为4个子概念节点"
当前图：e1(start) --r1--> e3(技术路线分类, 有findings, 受保护) --r3--> e4(各路线成熟度, UNKNOWN)

正确操作：添加4个子概念节点作为e3的子节点，每个子概念各自连接到下游e4
{{
  "should_restructure": true,
  "evaluation": "搜索已发现4种具体路线，展开为独立子概念以便逐一研究，合理",
  "changes": [
    {{"action": "add_node", "id": "e_dyn_1", "reason": "子概念：强化学习路线",
      "node_data": {{"id": "e_dyn_1", "name": "强化学习路线", "type": "placeholder", "type_constraint": "技术路线", "condition_constraints": [], "hop_distance": 2}}}},
    {{"action": "add_node", "id": "e_dyn_2", "reason": "子概念：端到端路线",
      "node_data": {{"id": "e_dyn_2", "name": "端到端路线", "type": "placeholder", "type_constraint": "技术路线", "condition_constraints": [], "hop_distance": 2}}}},
    {{"action": "add_node", "id": "e_dyn_3", "reason": "子概念：模块化路线",
      "node_data": {{"id": "e_dyn_3", "name": "模块化路线", "type": "placeholder", "type_constraint": "技术路线", "condition_constraints": [], "hop_distance": 2}}}},
    {{"action": "add_node", "id": "e_dyn_4", "reason": "子概念：仿真到真实路线",
      "node_data": {{"id": "e_dyn_4", "name": "仿真到真实路线", "type": "placeholder", "type_constraint": "技术路线", "condition_constraints": [], "hop_distance": 2}}}},
    {{"action": "add_edge", "id": "r_dyn_1", "reason": "连接e3到子概念",
      "edge_data": {{"id": "r_dyn_1", "source": "e3", "target": "e_dyn_1", "type": "子类别", "inquiry_goal": "强化学习技术路线的核心方法、代表企业和技术细节?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_2", "reason": "连接e3到子概念",
      "edge_data": {{"id": "r_dyn_2", "source": "e3", "target": "e_dyn_2", "type": "子类别", "inquiry_goal": "端到端技术路线的核心方法、代表企业和技术细节?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_3", "reason": "连接e3到子概念",
      "edge_data": {{"id": "r_dyn_3", "source": "e3", "target": "e_dyn_3", "type": "子类别", "inquiry_goal": "模块化技术路线的核心方法、代表企业和技术细节?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_4", "reason": "连接e3到子概念",
      "edge_data": {{"id": "r_dyn_4", "source": "e3", "target": "e_dyn_4", "type": "子类别", "inquiry_goal": "仿真到真实技术路线的核心方法、代表企业和技术细节?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_5", "reason": "子概念连接到下游成熟度评估",
      "edge_data": {{"id": "r_dyn_5", "source": "e_dyn_1", "target": "e4", "type": "成熟度", "inquiry_goal": "[强化学习路线]的技术成熟度和商业化进展?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_6", "reason": "子概念连接到下游成熟度评估",
      "edge_data": {{"id": "r_dyn_6", "source": "e_dyn_2", "target": "e4", "type": "成熟度", "inquiry_goal": "[端到端路线]的技术成熟度和商业化进展?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_7", "reason": "子概念连接到下游成熟度评估",
      "edge_data": {{"id": "r_dyn_7", "source": "e_dyn_3", "target": "e4", "type": "成熟度", "inquiry_goal": "[模块化路线]的技术成熟度和商业化进展?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_8", "reason": "子概念连接到下游成熟度评估",
      "edge_data": {{"id": "r_dyn_8", "source": "e_dyn_4", "target": "e4", "type": "成熟度", "inquiry_goal": "[仿真到真实路线]的技术成熟度和商业化进展?", "task_type": "open"}}}}
  ]
}}

### 示例1b：具象化 + 分组（子类过多时聚类到≤5个节点）

Planner提案："搜索'全球可再生能源技术'发现了9种技术方向（光伏、风电、氢能、地热、潮汐、生物质、储能电池、核聚变、碳捕获），下游需要按技术调查代表公司和商业化进展。按能源转化特性聚类为4组：太阳能与风能、新兴发电技术、储能与氢能、碳管理技术。展开为4个子概念节点"
当前图：e1(start) --r1--> e3(可再生能源技术分类, 有findings, 受保护) --r3--> e4(各技术代表企业, UNKNOWN)

正确操作：按共性聚类为4个子概念节点（非9个），每个子概念连接到下游e4
{{
  "should_restructure": true,
  "evaluation": "发现9种子类但下游需独立研究，按能源转化特性聚类为4组是合理的分组策略，避免节点爆炸",
  "changes": [
    {{"action": "add_node", "id": "e_dyn_1", "reason": "聚类子概念：太阳能与风能（含光伏、风电）",
      "node_data": {{"id": "e_dyn_1", "name": "太阳能与风能", "type": "placeholder", "type_constraint": "能源技术聚类", "condition_constraints": ["光伏", "风电"], "hop_distance": 2}}}},
    {{"action": "add_node", "id": "e_dyn_2", "reason": "聚类子概念：新兴发电技术（含地热、潮汐、生物质、核聚变）",
      "node_data": {{"id": "e_dyn_2", "name": "新兴发电技术", "type": "placeholder", "type_constraint": "能源技术聚类", "condition_constraints": ["地热", "潮汐", "生物质", "核聚变"], "hop_distance": 2}}}},
    {{"action": "add_node", "id": "e_dyn_3", "reason": "聚类子概念：储能与氢能（含储能电池、氢能）",
      "node_data": {{"id": "e_dyn_3", "name": "储能与氢能", "type": "placeholder", "type_constraint": "能源技术聚类", "condition_constraints": ["储能电池", "氢能"], "hop_distance": 2}}}},
    {{"action": "add_node", "id": "e_dyn_4", "reason": "聚类子概念：碳管理技术（含碳捕获）",
      "node_data": {{"id": "e_dyn_4", "name": "碳管理技术", "type": "placeholder", "type_constraint": "能源技术聚类", "condition_constraints": ["碳捕获"], "hop_distance": 2}}}},
    {{"action": "add_edge", "id": "r_dyn_1", "reason": "连接e3到聚类子概念",
      "edge_data": {{"id": "r_dyn_1", "source": "e3", "target": "e_dyn_1", "type": "子类别", "inquiry_goal": "太阳能与风能领域（光伏、风电）的技术发展现状、代表企业和商业化进展?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_2", "reason": "连接e3到聚类子概念",
      "edge_data": {{"id": "r_dyn_2", "source": "e3", "target": "e_dyn_2", "type": "子类别", "inquiry_goal": "新兴发电技术（地热、潮汐、生物质、核聚变）的技术成熟度、代表企业和商业化前景?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_3", "reason": "连接e3到聚类子概念",
      "edge_data": {{"id": "r_dyn_3", "source": "e3", "target": "e_dyn_3", "type": "子类别", "inquiry_goal": "储能与氢能领域（储能电池、氢能）的技术路线、代表企业和产业化进度?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_4", "reason": "连接e3到聚类子概念",
      "edge_data": {{"id": "r_dyn_4", "source": "e3", "target": "e_dyn_4", "type": "子类别", "inquiry_goal": "碳捕获与碳管理技术的技术路线、代表企业和政策驱动因素?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_5", "reason": "聚类子概念连接到下游代表企业调查",
      "edge_data": {{"id": "r_dyn_5", "source": "e_dyn_1", "target": "e4", "type": "代表企业", "inquiry_goal": "[太阳能与风能]领域的头部企业及其技术路径、产能规模?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_6", "reason": "聚类子概念连接到下游代表企业调查",
      "edge_data": {{"id": "r_dyn_6", "source": "e_dyn_2", "target": "e4", "type": "代表企业", "inquiry_goal": "[新兴发电技术]领域的头部企业及其技术路径、融资情况?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_7", "reason": "聚类子概念连接到下游代表企业调查",
      "edge_data": {{"id": "r_dyn_7", "source": "e_dyn_3", "target": "e4", "type": "代表企业", "inquiry_goal": "[储能与氢能]领域的头部企业及其技术路径、产业化进度?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_8", "reason": "聚类子概念连接到下游代表企业调查",
      "edge_data": {{"id": "r_dyn_8", "source": "e_dyn_4", "target": "e4", "type": "代表企业", "inquiry_goal": "[碳管理技术]领域的头部企业及其技术路径、政策支持?", "task_type": "open"}}}}
  ]
}}

### 示例2：具象化 + 轴旋转（下游有多个维度节点，信息按子概念共现）

Planner提案："搜索'半导体产品线'发现4种芯片类型。下游有3个维度节点（技术优势、市场份额、供应链风险），信息按芯片类型共现——一份芯片行业报告同时覆盖该芯片的3个维度。展开产品线 + 合并维度节点为inquiry criteria"
当前图：e3(半导体产品线, 有findings, 受保护) --r4--> e4(技术优势, UNKNOWN) / --r5--> e5(市场份额, UNKNOWN) / --r6--> e6(供应链风险, UNKNOWN)

正确操作：添加4个子概念节点，每个节点的inquiry_goal包含全部3个维度作为criteria，删除3个维度节点
{{
  "should_restructure": true,
  "evaluation": "发现4种芯片类型需独立研究，且下游3个维度信息按芯片类型共现，合并为criteria以避免4×3=12个任务",
  "changes": [
    {{"action": "add_node", "id": "e_dyn_1", "reason": "子概念：手机芯片",
      "node_data": {{"id": "e_dyn_1", "name": "手机芯片", "type": "placeholder", "type_constraint": "半导体产品", "condition_constraints": [], "hop_distance": 2}}}},
    {{"action": "add_node", "id": "e_dyn_2", "reason": "子概念：服务器芯片",
      "node_data": {{"id": "e_dyn_2", "name": "服务器芯片", "type": "placeholder", "type_constraint": "半导体产品", "condition_constraints": [], "hop_distance": 2}}}},
    {{"action": "add_node", "id": "e_dyn_3", "reason": "子概念：AI芯片",
      "node_data": {{"id": "e_dyn_3", "name": "AI芯片", "type": "placeholder", "type_constraint": "半导体产品", "condition_constraints": [], "hop_distance": 2}}}},
    {{"action": "add_node", "id": "e_dyn_4", "reason": "子概念：存储芯片",
      "node_data": {{"id": "e_dyn_4", "name": "存储芯片", "type": "placeholder", "type_constraint": "半导体产品", "condition_constraints": [], "hop_distance": 2}}}},
    {{"action": "add_edge", "id": "r_dyn_1", "reason": "连接e3到子概念，inquiry_goal包含3个维度作为criteria",
      "edge_data": {{"id": "r_dyn_1", "source": "e3", "target": "e_dyn_1", "type": "产品线", "inquiry_goal": "手机芯片领域的技术优势、市场份额和供应链风险分析?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_2", "reason": "连接e3到子概念",
      "edge_data": {{"id": "r_dyn_2", "source": "e3", "target": "e_dyn_2", "type": "产品线", "inquiry_goal": "服务器芯片领域的技术优势、市场份额和供应链风险分析?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_3", "reason": "连接e3到子概念",
      "edge_data": {{"id": "r_dyn_3", "source": "e3", "target": "e_dyn_3", "type": "产品线", "inquiry_goal": "AI芯片领域的技术优势、市场份额和供应链风险分析?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_4", "reason": "连接e3到子概念",
      "edge_data": {{"id": "r_dyn_4", "source": "e3", "target": "e_dyn_4", "type": "产品线", "inquiry_goal": "存储芯片领域的技术优势、市场份额和供应链风险分析?", "task_type": "open"}}}},
    {{"action": "remove_node", "id": "e4", "reason": "技术优势维度已合并为子概念节点的inquiry criteria"}},
    {{"action": "remove_node", "id": "e5", "reason": "市场份额维度已合并为子概念节点的inquiry criteria"}},
    {{"action": "remove_node", "id": "e6", "reason": "供应链风险维度已合并为子概念节点的inquiry criteria"}}
  ]
}}

### 示例3：裁剪(R_prune)

Planner提案："搜索发现'用户隐私影响'维度与原始问题（半导体供应链风险）关联性极低，删除该节点"
当前图：e1(start) --r1--> e3(供应链风险, 有findings, 受保护) --r7--> e7(用户隐私影响, UNKNOWN)

正确操作：直接删除无用维度节点
{{
  "should_restructure": true,
  "evaluation": "搜索确认该维度与原始问题无关，裁剪以节约搜索资源",
  "changes": [
    {{"action": "remove_node", "id": "e7", "reason": "用户隐私影响与半导体供应链风险无关，裁剪避免浪费搜索任务"}}
  ]
}}

"""

PHASE1_RESTRUCTURE_PROMPT_EN = """You are a cognitive graph restructuring expert. The Planner has proposed restructuring the cognitive graph. Evaluate the proposal and output specific change operations.

**Core principle**: Restructuring should be surgical, not a rebuild. Existing findings are completed exploration records — don't worry about them contradicting new concepts. Only fix known errors; don't overturn the global design.

## Original User Question
{original_question}

## Current Graph Topology (tree view + protection status + cascade impact)
{restructure_view}

## Planner Restructuring Proposal
{planner_reasoning}

## Available Operations (5 types)

### 1. modify_node — Change node attributes (findings/state untouched)
- `name`, `type_constraint`, `condition_constraints`: changeable on ALL nodes
- `core_pending`, `supplementary_pending`: changeable on ALL nodes (search strategy is always adjustable, even on protected nodes)
- `remove_items`: list of discovered_items to remove (also clears their item_findings). Use when Planner identifies noise items that are not real research targets
- ⚠️ This is the only way to rename protected nodes (no need to delete+recreate)

### 2. modify_edge — Change edge attributes
- Modifiable edges: both inquiry_goal and target can be changed
- Protected edges (solved/exhausted/investigating): only inquiry_goal can be changed (already searched or in-progress, just a label change)
- ⚠️ modify_edge CANNOT change edge state. States are managed by the search system. To abandon a dimension, use remove_node on the target node

### 3. add_node / add_edge — Add new nodes/edges
- Node ID format: `e_dyn_N`, edge ID format: `r_dyn_N`
- New nodes must have incoming edges connecting them to the graph
- inquiry_goal must be complete and self-contained; use `[node_name]` placeholders for dependencies

### 4. remove_node — Remove node (cascade-deletes all connected edges)
- Protected nodes cannot be removed
- This is the only way to remove an entire dimension (edges are deleted with the node)
- ⚠️ There is NO remove_edge operation — edges can only be removed by deleting nodes
- ⚠️ Removal may create orphan nodes (Phase 2 will handle them, but try to maintain connectivity)

## Evaluation Criteria
1. Do the changes serve answering the original question?
2. Are they based on search findings (not fabricated)?
3. ⚠️ **Question completeness (HIGHEST PRIORITY — execute this checklist)**:
   Before removing any dimension node, **re-read the original user question** and check:
   - **Does this dimension correspond to an information need explicitly stated or directly implied in the user's question?**
     - If YES → **REJECT the removal**. Set should_restructure=false. In evaluation, tell the Planner:
       "REJECTED: Dimension 'XXX' corresponds to 'YYY' required by the original question. This is something the user explicitly asked for — it cannot be deleted. If search is difficult, change search strategy instead of abandoning the dimension."
     - If NO → continue:
       - **Pruning (R_prune)**: Search proved this dimension is genuinely unhelpful for answering the original question → allow direct removal
       - **Removal in other restructurings**: The dimension is still helpful but needs a different form (e.g., merged into criteria during axis pivot) → MUST be preserved in some form, silent loss is NOT allowed
   - **Common error pattern**: Planner claims "search is difficult" / "insufficient information" / "no results found" as reason to delete — this is NOT a valid deletion reason. REJECT and suggest changing search angle instead
4. Consult the Cascade Impact table to understand downstream effects of deletions.
5. **Concept evolution (R_conc/R_aug)**: If the Planner proposes expanding an abstract concept node into sub-concept nodes (based on discovered sub-categories), or adding new dimensions that significantly help answer the original question, this is a normal cognitive graph evolution operation and should be actively supported.
6. ⚠️ **Information flow boundary**: Searcher automatically receives only the direct parent node's findings — it cannot see the full cognitive graph. New nodes must be answerable by searching for NEW information. **Reject** pure aggregation nodes (e.g., "consolidate findings from node A and B") — Searcher cannot aggregate across multiple arbitrary nodes. If cross-node reasoning is needed before searching, Planner should reason in [Thought] then create a focused search node.
7. **Error correction (R_correct)**: If the Planner proposes removing downstream structure based on a disproven premise, verify that search findings genuinely contradict the premise. The corrected direction must still serve answering the original question, just from a factually supported angle.

## Output Format

First output [Evaluation] analysis, then JSON (no markdown code blocks):
{{
  "should_restructure": true/false,
  "evaluation": "One-sentence evaluation conclusion",
  "changes": [
    {{"action": "modify_node", "id": "node_id", "reason": "why",
      "name": "new name", "type_constraint": "new constraint", "condition_constraints": ["cond"],
      "core_pending": ["updated criteria"], "remove_items": ["noise item to remove"]}},
    {{"action": "modify_edge", "id": "edge_id", "reason": "why",
      "new_inquiry_goal": "new question", "new_target_id": "new target node id"}},
    {{"action": "add_node", "id": "e_dyn_1", "reason": "why",
      "node_data": {{"id": "e_dyn_1", "name": "Concept name", "type": "placeholder",
       "type_constraint": "constraint", "condition_constraints": [], "hop_distance": 1}}}},
    {{"action": "add_edge", "id": "r_dyn_1", "reason": "why",
      "edge_data": {{"id": "r_dyn_1", "source": "src_id", "target": "tgt_id",
       "type": "relation", "inquiry_goal": "question?", "task_type": "open"}}}},
    {{"action": "remove_node", "id": "node_id", "reason": "why"}}
  ]
}}

## Scenario Examples

### Example 1: Concept Concretization (one downstream concept node, no axis rotation needed)

Planner proposal: "Searching 'Technology Route Classification' revealed 4 routes (reinforcement learning, end-to-end, modular, sim-to-real). Expand into 4 sub-concept nodes"
Current graph: e1(start) --r1--> e3(Technology Routes, has findings, protected) --r3--> e4(Route Maturity Assessment, UNKNOWN)

Correct operations: Add 4 sub-concept nodes as children of e3, each connecting to downstream e4
{{
  "should_restructure": true,
  "evaluation": "Search discovered 4 distinct routes worth independent investigation, expansion is well-justified",
  "changes": [
    {{"action": "add_node", "id": "e_dyn_1", "reason": "Sub-concept: reinforcement learning route",
      "node_data": {{"id": "e_dyn_1", "name": "Reinforcement Learning Route", "type": "placeholder", "type_constraint": "Technology route", "condition_constraints": [], "hop_distance": 2}}}},
    {{"action": "add_node", "id": "e_dyn_2", "reason": "Sub-concept: end-to-end route",
      "node_data": {{"id": "e_dyn_2", "name": "End-to-End Route", "type": "placeholder", "type_constraint": "Technology route", "condition_constraints": [], "hop_distance": 2}}}},
    {{"action": "add_node", "id": "e_dyn_3", "reason": "Sub-concept: modular route",
      "node_data": {{"id": "e_dyn_3", "name": "Modular Route", "type": "placeholder", "type_constraint": "Technology route", "condition_constraints": [], "hop_distance": 2}}}},
    {{"action": "add_node", "id": "e_dyn_4", "reason": "Sub-concept: sim-to-real route",
      "node_data": {{"id": "e_dyn_4", "name": "Sim-to-Real Route", "type": "placeholder", "type_constraint": "Technology route", "condition_constraints": [], "hop_distance": 2}}}},
    {{"action": "add_edge", "id": "r_dyn_1", "reason": "Connect e3 to sub-concept",
      "edge_data": {{"id": "r_dyn_1", "source": "e3", "target": "e_dyn_1", "type": "sub-category", "inquiry_goal": "Core methods, key companies, and technical details of the reinforcement learning route?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_2", "reason": "Connect e3 to sub-concept",
      "edge_data": {{"id": "r_dyn_2", "source": "e3", "target": "e_dyn_2", "type": "sub-category", "inquiry_goal": "Core methods, key companies, and technical details of the end-to-end route?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_3", "reason": "Connect e3 to sub-concept",
      "edge_data": {{"id": "r_dyn_3", "source": "e3", "target": "e_dyn_3", "type": "sub-category", "inquiry_goal": "Core methods, key companies, and technical details of the modular route?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_4", "reason": "Connect e3 to sub-concept",
      "edge_data": {{"id": "r_dyn_4", "source": "e3", "target": "e_dyn_4", "type": "sub-category", "inquiry_goal": "Core methods, key companies, and technical details of the sim-to-real route?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_5", "reason": "Sub-concept connects to downstream maturity assessment",
      "edge_data": {{"id": "r_dyn_5", "source": "e_dyn_1", "target": "e4", "type": "maturity", "inquiry_goal": "Technology maturity and commercialization progress of [Reinforcement Learning Route]?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_6", "reason": "Sub-concept connects to downstream maturity assessment",
      "edge_data": {{"id": "r_dyn_6", "source": "e_dyn_2", "target": "e4", "type": "maturity", "inquiry_goal": "Technology maturity and commercialization progress of [End-to-End Route]?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_7", "reason": "Sub-concept connects to downstream maturity assessment",
      "edge_data": {{"id": "r_dyn_7", "source": "e_dyn_3", "target": "e4", "type": "maturity", "inquiry_goal": "Technology maturity and commercialization progress of [Modular Route]?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_8", "reason": "Sub-concept connects to downstream maturity assessment",
      "edge_data": {{"id": "r_dyn_8", "source": "e_dyn_4", "target": "e4", "type": "maturity", "inquiry_goal": "Technology maturity and commercialization progress of [Sim-to-Real Route]?", "task_type": "open"}}}}
  ]
}}

### Example 1b: Concretization + Grouping (too many sub-categories, cluster into ≤5 nodes)

Planner proposal: "Searching 'Global Renewable Energy Technologies' revealed 9 technology directions (solar PV, wind power, hydrogen, geothermal, tidal, biomass, battery storage, nuclear fusion, carbon capture). Downstream needs per-technology company and commercialization research. Cluster by energy conversion characteristics into 4 groups: Solar & Wind, Emerging Generation, Storage & Hydrogen, Carbon Management. Expand into 4 sub-concept nodes"
Current graph: e1(start) --r1--> e3(Renewable Energy Tech Classification, has findings, protected) --r3--> e4(Representative Companies per Tech, UNKNOWN)

Correct operations: Cluster into 4 sub-concept nodes (not 9), each connecting to downstream e4
{{
  "should_restructure": true,
  "evaluation": "9 sub-categories found but downstream needs independent research; clustering into 4 groups by energy conversion characteristics is a reasonable grouping strategy to avoid node explosion",
  "changes": [
    {{"action": "add_node", "id": "e_dyn_1", "reason": "Clustered sub-concept: Solar & Wind (solar PV, wind power)",
      "node_data": {{"id": "e_dyn_1", "name": "Solar & Wind Energy", "type": "placeholder", "type_constraint": "Energy tech cluster", "condition_constraints": ["solar PV", "wind power"], "hop_distance": 2}}}},
    {{"action": "add_node", "id": "e_dyn_2", "reason": "Clustered sub-concept: Emerging Generation (geothermal, tidal, biomass, nuclear fusion)",
      "node_data": {{"id": "e_dyn_2", "name": "Emerging Generation Technologies", "type": "placeholder", "type_constraint": "Energy tech cluster", "condition_constraints": ["geothermal", "tidal", "biomass", "nuclear fusion"], "hop_distance": 2}}}},
    {{"action": "add_node", "id": "e_dyn_3", "reason": "Clustered sub-concept: Storage & Hydrogen (battery storage, hydrogen)",
      "node_data": {{"id": "e_dyn_3", "name": "Energy Storage & Hydrogen", "type": "placeholder", "type_constraint": "Energy tech cluster", "condition_constraints": ["battery storage", "hydrogen"], "hop_distance": 2}}}},
    {{"action": "add_node", "id": "e_dyn_4", "reason": "Clustered sub-concept: Carbon Management (carbon capture)",
      "node_data": {{"id": "e_dyn_4", "name": "Carbon Management Technologies", "type": "placeholder", "type_constraint": "Energy tech cluster", "condition_constraints": ["carbon capture"], "hop_distance": 2}}}},
    {{"action": "add_edge", "id": "r_dyn_1", "reason": "Connect e3 to clustered sub-concept",
      "edge_data": {{"id": "r_dyn_1", "source": "e3", "target": "e_dyn_1", "type": "sub-category", "inquiry_goal": "Solar & wind energy (solar PV, wind power): technology status, key companies, and commercialization progress?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_2", "reason": "Connect e3 to clustered sub-concept",
      "edge_data": {{"id": "r_dyn_2", "source": "e3", "target": "e_dyn_2", "type": "sub-category", "inquiry_goal": "Emerging generation technologies (geothermal, tidal, biomass, nuclear fusion): maturity, key companies, and commercial prospects?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_3", "reason": "Connect e3 to clustered sub-concept",
      "edge_data": {{"id": "r_dyn_3", "source": "e3", "target": "e_dyn_3", "type": "sub-category", "inquiry_goal": "Energy storage & hydrogen: technology routes, key companies, and industrialization progress?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_4", "reason": "Connect e3 to clustered sub-concept",
      "edge_data": {{"id": "r_dyn_4", "source": "e3", "target": "e_dyn_4", "type": "sub-category", "inquiry_goal": "Carbon capture & management: technology routes, key companies, and policy drivers?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_5", "reason": "Clustered sub-concept connects to downstream company investigation",
      "edge_data": {{"id": "r_dyn_5", "source": "e_dyn_1", "target": "e4", "type": "companies", "inquiry_goal": "Leading companies in [Solar & Wind Energy] and their technology paths, production capacity?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_6", "reason": "Clustered sub-concept connects to downstream company investigation",
      "edge_data": {{"id": "r_dyn_6", "source": "e_dyn_2", "target": "e4", "type": "companies", "inquiry_goal": "Leading companies in [Emerging Generation Technologies] and their technology paths, funding?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_7", "reason": "Clustered sub-concept connects to downstream company investigation",
      "edge_data": {{"id": "r_dyn_7", "source": "e_dyn_3", "target": "e4", "type": "companies", "inquiry_goal": "Leading companies in [Energy Storage & Hydrogen] and their technology paths, industrialization?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_8", "reason": "Clustered sub-concept connects to downstream company investigation",
      "edge_data": {{"id": "r_dyn_8", "source": "e_dyn_4", "target": "e4", "type": "companies", "inquiry_goal": "Leading companies in [Carbon Management Technologies] and their technology paths, policy support?", "task_type": "open"}}}}
  ]
}}

### Example 2: Concretization + Axis Rotation (multiple downstream dimension nodes, info co-locates by sub-concept)

Planner proposal: "Searching 'Semiconductor Product Lines' revealed 4 chip types. Downstream has 3 dimension nodes (tech advantages, market share, supply chain risk). Info co-locates by chip type — one chip industry report covers all 3 dimensions for that chip type. Expand product lines + merge dimension nodes into inquiry criteria"
Current graph: e3(Semiconductor Product Lines, has findings, protected) --r4--> e4(Tech Advantages, UNKNOWN) / --r5--> e5(Market Share, UNKNOWN) / --r6--> e6(Supply Chain Risk, UNKNOWN)

Correct operations: Add 4 sub-concept nodes with inquiry_goal containing all 3 dimensions as criteria, remove 3 dimension nodes
{{
  "should_restructure": true,
  "evaluation": "4 chip types need independent research, and 3 downstream dimensions co-locate by chip type. Merging into criteria avoids 4×3=12 tasks",
  "changes": [
    {{"action": "add_node", "id": "e_dyn_1", "reason": "Sub-concept: mobile chips",
      "node_data": {{"id": "e_dyn_1", "name": "Mobile Chips", "type": "placeholder", "type_constraint": "Semiconductor product", "condition_constraints": [], "hop_distance": 2}}}},
    {{"action": "add_node", "id": "e_dyn_2", "reason": "Sub-concept: server chips",
      "node_data": {{"id": "e_dyn_2", "name": "Server Chips", "type": "placeholder", "type_constraint": "Semiconductor product", "condition_constraints": [], "hop_distance": 2}}}},
    {{"action": "add_node", "id": "e_dyn_3", "reason": "Sub-concept: AI chips",
      "node_data": {{"id": "e_dyn_3", "name": "AI Chips", "type": "placeholder", "type_constraint": "Semiconductor product", "condition_constraints": [], "hop_distance": 2}}}},
    {{"action": "add_node", "id": "e_dyn_4", "reason": "Sub-concept: memory chips",
      "node_data": {{"id": "e_dyn_4", "name": "Memory Chips", "type": "placeholder", "type_constraint": "Semiconductor product", "condition_constraints": [], "hop_distance": 2}}}},
    {{"action": "add_edge", "id": "r_dyn_1", "reason": "Connect to sub-concept, inquiry_goal includes all 3 dimensions as criteria",
      "edge_data": {{"id": "r_dyn_1", "source": "e3", "target": "e_dyn_1", "type": "product line", "inquiry_goal": "Mobile chip sector: tech advantages, market share, and supply chain risk analysis?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_2", "reason": "Connect to sub-concept",
      "edge_data": {{"id": "r_dyn_2", "source": "e3", "target": "e_dyn_2", "type": "product line", "inquiry_goal": "Server chip sector: tech advantages, market share, and supply chain risk analysis?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_3", "reason": "Connect to sub-concept",
      "edge_data": {{"id": "r_dyn_3", "source": "e3", "target": "e_dyn_3", "type": "product line", "inquiry_goal": "AI chip sector: tech advantages, market share, and supply chain risk analysis?", "task_type": "open"}}}},
    {{"action": "add_edge", "id": "r_dyn_4", "reason": "Connect to sub-concept",
      "edge_data": {{"id": "r_dyn_4", "source": "e3", "target": "e_dyn_4", "type": "product line", "inquiry_goal": "Memory chip sector: tech advantages, market share, and supply chain risk analysis?", "task_type": "open"}}}},
    {{"action": "remove_node", "id": "e4", "reason": "Tech advantages dimension merged into sub-concept inquiry criteria"}},
    {{"action": "remove_node", "id": "e5", "reason": "Market share dimension merged into sub-concept inquiry criteria"}},
    {{"action": "remove_node", "id": "e6", "reason": "Supply chain risk dimension merged into sub-concept inquiry criteria"}}
  ]
}}

### Example 3: Pruning (R_prune)

Planner proposal: "Search showed the 'User Privacy Impact' dimension has minimal relevance to the original question (semiconductor supply chain risk). Remove this node."
Current graph: e1(start) --r1--> e3(Supply Chain Risk, has findings, protected) --r7--> e7(User Privacy Impact, UNKNOWN)

Correct operations: Remove the unhelpful dimension node
{{
  "should_restructure": true,
  "evaluation": "Search confirmed this dimension is irrelevant to the original question, pruning to save search resources",
  "changes": [
    {{"action": "remove_node", "id": "e7", "reason": "User privacy impact is unrelated to semiconductor supply chain risk, pruning to avoid wasting search tasks"}}
  ]
}}"""

PHASE2_RECONNECT_PROMPT = """你是认知图重连专家。Phase 1 重构后产生了孤儿节点（与主图断开），你需要决定每个孤儿的处置方式。

## 原始用户问题
{original_question}

## Planner 重构指令（Phase 1 的原始意图）
{planner_reasoning}

## Phase 1 执行摘要
{phase1_summary}

## 当前图拓扑
{current_topology}

## 孤儿节点（与主图断开）
{orphan_details}

## 当前轮数: {round_number}/{max_rounds}

## 决策原则
- 理解 Planner 为什么要重构 — 如果孤儿正是 Planner 想要删除的维度，应 confirm_remove
- 如果孤儿与原始问题仍然相关（只是因为上游被改动而断开），应 add_edge 重连
- 不要盲目重连所有孤儿 — 这会撤销 Phase 1 的工作

## 可用操作（2种）

1. **add_edge** — 将孤儿重新连接到主图
   - source 必须是主图中已有的节点
   - target 是孤儿节点
   - 不能创建环

2. **confirm_remove** — 确认删除孤儿（需附理由）
   - 仅当该维度确实与原始问题无关时才可删除
   - 删除会级联清理下游节点

## 输出格式

直接输出JSON（不要用markdown代码块）：
{{
  "actions": [
    {{"action": "add_edge",
      "edge_data": {{"id": "r_p2_1", "source": "主图节点id", "target": "孤儿节点id",
       "type": "关系", "inquiry_goal": "问题?", "task_type": "open"}}}},
    {{"action": "confirm_remove", "node_id": "孤儿节点id", "reason": "删除理由"}}
  ]
}}"""

PHASE2_RECONNECT_PROMPT_EN = """You are a cognitive graph reconnection expert. After Phase 1 restructuring, orphan nodes were created (disconnected from the main graph). Decide how to handle each orphan.

## Original User Question
{original_question}

## Planner's Restructuring Intent (why Phase 1 was triggered)
{planner_reasoning}

## Phase 1 Execution Summary
{phase1_summary}

## Current Graph Topology
{current_topology}

## Orphan Nodes (disconnected from main graph)
{orphan_details}

## Current Round: {round_number}/{max_rounds}

## Decision Principles
- Understand WHY the Planner requested restructuring — if orphans are dimensions the Planner wanted removed, use confirm_remove
- If orphans are still relevant to the original question (just disconnected due to upstream changes), use add_edge to reconnect
- Do NOT blindly reconnect all orphans — that would undo Phase 1's work

## Available Operations (2 types)

1. **add_edge** — Reconnect orphan to the main graph
   - source must be an existing node in the main graph
   - target is the orphan node
   - Must not create a cycle

2. **confirm_remove** — Confirm deletion of orphan (reason required)
   - Only delete if the dimension is genuinely irrelevant to the original question
   - Deletion cascades to downstream nodes

## Output Format

Output JSON directly (no markdown code blocks):
{{
  "actions": [
    {{"action": "add_edge",
      "edge_data": {{"id": "r_p2_1", "source": "main_graph_node_id", "target": "orphan_node_id",
       "type": "relation", "inquiry_goal": "question?", "task_type": "open"}}}},
    {{"action": "confirm_remove", "node_id": "orphan_node_id", "reason": "deletion reason"}}
  ]
}}"""
