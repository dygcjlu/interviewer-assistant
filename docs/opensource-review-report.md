# 面试助手项目 · 开源化评审报告

> 评审日期：2026-07-06
> 评审依据：`docs/功能完善.md` 需求
> 评审方式：单 agent 顺序深入阅读（架构文档 → 核心代码 → 实测覆盖率 → 比对既往 review 记录），未采用文档建议的"多 agent 并行分工"（工作区规则禁止并发子任务调用），但充分复用了仓库内已有的 F1–F6 功能 review 成果（`docs/review-findings.md`、`docs/review-findings-f4-f6.md`）以避免重复劳动、把精力集中在"开源化"这个增量视角上。

---

## 0. 评审方法与范围说明

本次评审做了以下工作，均基于仓库当前真实代码，非泛泛而谈：

1. 通读 `CLAUDE.md`、`docs/arc/` 全部 8 篇架构文档，建立整体认知。
2. 通读 `src/agents/prompts.py`（三大核心 Agent 的完整 system prompt）、`src/agents/base.py`、`src/agents/main_agent.py` 关键片段、`src/agents/interview_controller.py`、`src/storage/memory_module.py` 关键片段、`src/utils/metrics.py`、`src/web/routes.py` 片段。
3. 对照仓库已有的 F1–F6 功能 review（2026-06-08，共发现 69 项问题，含 2 个 Critical）和后续 `fix-review-findings` 变更（`.superpowers/sdd/progress.md`，7 个任务已全部完成），**逐条抽查关键修复是否落地**（见附录 A），确认多数 Critical/High 问题已修复。
4. 实测运行测试套件并统计真实覆盖率（而非引用 2026-06-04 的旧基线）：
   - `pytest tests/unit --cov=src`：51%，1 个失败用例
   - `pytest tests/unit tests/integration --cov=src`：60%，同一失败用例
5. 检查开源关键文件：`README.md`、`CONTRIBUTING.md`、`SECURITY.md`、`LICENSE`、`.env.example`、`.github/workflows/ci.yml`、`requirements*.txt`、`docs/todo/*.md`（6 个待办功能点）。
6. 统计 `src/` 下最大文件行数，排查文件行数是否超出 `.cursor/rules/ecc-coding-style.mdc` 的 800 行上限。

**需要明确指出的局限**（避免臆测）：

- 未逐行阅读 `src/web/ui.py`（1184 行/848 语句，NiceGUI 前端）、`src/web/routes.py`（809 行）、`src/audio/*`（WASAPI/百度/讯飞/火山 STT 客户端，均为 Windows 专属或第三方协议实现）的全部实现细节，仅做了结构性分析（文件规模、测试覆盖率、silent-except 扫描）。若这些模块内部有更细节的逻辑问题，需要针对性二次 review。
- `docs/review-findings.md` / `docs/review-findings-f4-f6.md` 中记录的 Medium/Low 级问题（约 50 余项）**未逐条重新验证**是否已修复，本报告只重点抽查了影响最大的 Critical/High 项（见附录 A），其余仍应视为"待确认修复状态"。
- 未运行 `tests/e2e`（需要真实 LLM key）和 Windows 专属音频集成路径，其真实可用性未做运行时验证。

---

## 1. 总体评价

**成熟度：6.5 / 10**

> 一句话总结：**工程纪律和架构设计已经达到"能拿出手"的水准（Provider 抽象、PDF 解析策略模式、WAL 崩溃恢复、系统性 review 流程），但"开源包装"——演示物料、跨平台故事、自动化质量门禁、UI 层测试——还停留在"内部可用"阶段，是当前拉低"开源项目质感"的主要短板，而非核心技术能力不足。**

这与很多"刷简历"项目相反：本项目**技术深度足够，营销/包装不足**。评审建议的重心也据此倾斜：优先补"呈现层"（README/Demo/CI 门禁/覆盖率），其次补"结构层"（UI 与核心 Agent 测试、大文件拆分），最后再考虑"新功能"类的加分项。

---

## 2. 分维度发现

### 2.1 架构设计

| # | 严重度 | 发现 | 位置 |
|---|---|---|---|
| A-1 | P2 | **三套并行的 prompt 组装机制**（MainAgent 自管理三层 / PromptBuilder 七层 / EvalAgent 自建 messages）虽然文档中做了说明和取舍解释，但客观上增加了新人理解成本和维护面。三处都要各自处理"USER.md 注入""候选人信息注入"等相似逻辑，容易出现如 F4-2/F3-3 那样的"题目清单 vs 面试简报"文案漂移（已修复，但结构性风险仍在）。 | `docs/arc/prompt-assembly.md`、`src/agents/main_agent.py`、`src/agents/eval_agent.py` |
| A-2 | P2 | `main.py` 的 `lifespan()` 是一个手动 20 步顺序组装的大函数（`src/main.py`，141 行，测试覆盖 **0%**），本质上是手写的 DI 容器。当前规模尚可接受，但没有任何测试覆盖意味着"启动流程本身是否正确"完全靠人工冒烟测试，回归风险高。 | `src/main.py` |
| A-3 | P1 | `MemoryModule` 是一个 1019 行的单文件，承担候选人 CRUD、面试生命周期、WAL 恢复、评价报告持久化、两级 index 维护等 5+ 类职责，超出 `.cursor/rules/ecc-coding-style.mdc` 规定的 800 行上限（"高内聚低耦合""200-400 行典型，800 行为上限"）。虽然内部方法划分清晰，但作为单一模块承担过多"上帝类"职责，后续新增存储需求（如结构化题目清单持久化）会继续膨胀此文件。 | `src/storage/memory_module.py`（1019 行） |
| A-4 | P2 | `src/web/ui.py`（1184 行）是唯一的 NiceGUI 页面文件，混合了布局、状态刷新、轮询重试（如刚新增的 `_retry_questions_later`）、WebSocket 客户端逻辑。同样超出 800 行上限，且是全仓库**测试覆盖率最低的文件（0%，848 条可执行语句）**。 | `src/web/ui.py` |
| A-5 | ✅ 正面 | Provider 抽象（`src/llm/providers.py` 的 `ProviderProfile`）、PDF 解析 Strategy 模式（`src/tools/pdf_parsers/`）、`BaseAgent._run_with_tools()` 的 ReAct 循环、WAL 崩溃恢复设计，均体现了扎实的软件设计能力，是本项目在架构维度上**最值得在面试中重点讲述的部分**。 | `docs/arc/llm-providers.md`、`src/agents/base.py` |
| A-6 | P2 | 未发现明显的循环依赖或状态机边界问题——`InterviewController` 的状态机（`_start_interview_impl` 已改为仅 `IDLE` 可进入，见附录 A）设计正确。**并发候选人会话**场景未被设计覆盖：`InterviewController` 全程只维护单个 `self._session`（见 `src/agents/interview_controller.py`），这是"单面试官单会话"产品定位下的**有意简化（YAGNI）**，而非缺陷，但若要支持"多标签页同时准备两个候选人"需要架构改造，值得在 README/文档中显式声明为已知边界。 | `src/agents/interview_controller.py` |

### 2.2 提示词工程（重点）

`src/agents/prompts.py`（124 行）质量总体**高于典型个人项目水准**：三段 system prompt 都包含明确角色定位、输出格式约束（JSON schema / 纯文本+字数限制）、ASR 噪声鲁棒性说明（`EVAL_AGENT_SYSTEM_PROMPT` 第 135-140 行专门处理"识别错误""停顿词"，这是很多同类工具会忽略的细节）、防幻觉约束（"无则省略，勿编造"）。

| # | 严重度 | 发现 |
|---|---|---|
| P-1 | P2 | **无 few-shot 示例**。三段 prompt 都是纯指令式（zero-shot），没有提供"好的追问 vs 差的追问"或"简报输出示例"。对于"面试简报生成""评价报告生成"这类结构复杂、格式要求高的任务，1-2 个高质量 few-shot 示例通常能显著提升稳定性，尤其是 `RESUME_AGENT_SYSTEM_PROMPT` 要求的 Markdown 简报格式较复杂（项目考察/技能考察两级结构）。 |
| P-2 | P2 | **多语言/多岗位扩展性弱**：三段 prompt 均硬编码中文表达和"技术面试"场景（如"你是一位专业的技术面试助手"），若要支持非技术岗位（销售、产品）或英文面试，需要改代码而非改配置。作为开源项目，若想吸引更广泛用户，可以考虑把"岗位类型""语言"抽象为 prompt 模板变量。 |
| P-3 | P1 | **EvalAgent 未使用 PromptBuilder**（`docs/arc/prompt-assembly.md` 已明确说明是"评价场景特殊性"的有意设计），但这意味着 USER.md 注入、候选人信息注入等逻辑在 `EvalAgent._build_base_messages()` 中重复实现了一份，未来 `PromptBuilder` 的改动（如 Layer 5 格式调整）不会自动同步到 EvalAgent，需要人工双向维护。 |
| P-4 | ✅ 已验证修复 | F3-3/F4-2 记录的"题目清单"与"面试简报"术语不一致问题，已在当前 `prompts.py` 中统一为"面试简报"（原文档记录的是 2026-06-08 的代码状态）。 |
| P-5 | P1 | `CONTEXT_TOKEN_BUDGET` 压缩策略（`src/framework/context.py`）的 head/tail 截断（保留头 2 轮尾 3 轮，中间轮次直接丢弃且不进摘要）**仅影响 InterviewAgent 实时追问上下文**，EvalAgent 使用完整 `session.rounds`，不受影响——这是 F3-5 review 已澄清的结论，本次抽查 `context.py` 确认压缩逻辑未变，结论仍然成立。风险点在于：若面试很长（>8 轮触发压缩，压缩后又继续新增到 >8 轮），**多次压缩会导致中间信息持续被丢弃**，追问建议逐渐"失忆"于面试中段的内容，只记得开场和最近几轮。 | `src/framework/context.py` |
| P-6 | P2 | token 预算估算用 `len(text) / 3` 或 `len(text)` 字符数代理（`context.py`、`eval_agent.py`），对中英混杂内容（技术面试常见）估算偏差较大，已有 `_enforce_token_budget` 硬限兜底，不影响正确性，但会导致触发压缩/分块的时机不够精确。项目已引入 `tiktoken`（见 `requirements.txt`），可以直接用真实 tokenizer 计数替代字符数估算，这是一个"低成本、可量化改进、适合写进简历"的优化点。 |

**优化前后对比示例**（InterviewAgent 追问 prompt，补充 few-shot）：

```text
# 优化前（当前 prompts.py 片段）
## 输出要求
- 直接输出一句追问话术或切换引导语，不要任何前置解释
...

# 优化建议：追加 1-2 组 few-shot
## 示例
候选人回答："我们当时用了分库分表来解决这个问题。"
✅ 好的追问："分库分表的路由规则是按什么维度设计的？扩容时怎么处理数据迁移？"
❌ 差的追问："能详细说说分库分表吗？"（过于宽泛，未体现"技术深挖"原则）
```

### 2.3 功能完整性与产品体验（按模块）

#### 简历管理

- 上传/解析/去重链路完整（`POST /api/resume/upload` → 聊天触发解析 → `dispatch_to_agent`）。去重逻辑之前依赖文件名匹配（F1-5），当前实现仍是 `get_candidate_by_name(safe_stem)`（`src/web/routes.py`），**未看到改为"解析后按真实姓名去重"**——若候选人重新上传时文件名不同（如 `resume_v2.pdf`），仍会静默产生重复档案。建议核实并修复（P1）。
- 无候选人资料编辑 API（无 PUT/PATCH），更新只能"重新上传覆盖"，对单用户工具是合理的 YAGNI 取舍，但若开源后有多人反馈"想手动改个技能标签"，会成为高频请求。

#### 面试准备

- `docs/todo/03-structured-interview-mode.md`（结构化问题清单 + 覆盖度追踪）**已经开始实现**：`src/models/question.py`、`routes.py` 中的 `GET /api/interview/questions`、`_check_question_coverage()` 均已存在，`ui.py` 有对应的 `_render_questions` / `_retry_questions_later`（工作区中甚至有一处**尚未提交的本地改动**，见附录 B）。但 `docs/todo/03-structured-interview-mode.md` 的验收条件仍全部是 `- [ ]` 未勾选状态，**文档与代码进度不同步**，建议开源前同步更新 todo 状态或直接归档到已完成。
- `src/models/question.py` 测试覆盖率 **0%**（9 条语句全未覆盖），是一个新功能但完全没有测试的典型例子。

#### 实时转写与追问建议

- 三种 STT 引擎（百度/讯飞/火山）+ Mock 的可插拔设计是合理的（`STT_ENGINE` 配置切换），但 `baidu_stt.py`（167 语句）、`xunfei_stt.py`（200 语句）、`wasapi.py`（120 语句）测试覆盖率均为 **0%**，`volc_stt.py` 70%。这些模块因为依赖真实网络/硬件，0% 覆盖本身可以理解，但意味着**协议层的边界处理（重连、鉴权失败、断流）完全没有自动化验证**，只能靠人工在真实面试中触发。
- **发现一个当前会失败的测试**：`tests/unit/test_volc_stt.py::TestVolcRealtimeSTTCredentialCheck::test_connect_silent_when_no_credentials` 在本地环境实测**失败**（`TypeError: object MagicMock can't be used in 'await' expression'`）。根因是该测试假设"测试环境下凭据默认为空字符串"，但本机 `.env` 配置了真实的火山 ASR 凭据，导致 `connect()` 走到了真正发起 WebSocket 连接的分支，而 mock 未配置为 `AsyncMock`。**这是一个测试隔离问题（P1）**：单元测试不应依赖本地 `.env` 的 ambient 配置，应在测试内显式清空/覆盖相关环境变量或直接 mock `Settings`，否则同一套测试在不同开发者机器/CI 上可能表现不一致（CI 大概率因为没配置 `VOLC_*` secret 而"侥幸通过"，掩盖了测试设计缺陷）。

#### 追问建议（同上，另补充）

- F3-4 记录的"`generate_suggestion` 实际是非流式调用，`suggestion_delta` 只发一次完整文本"问题，本次未重新抽查 `interview_agent.py` 具体实现是否已改为真流式，**列为待确认项**（详见附录 A 未验证清单）。若未修复，"流式推送"这个在架构文档中反复强调的特性在实际体验上是失真的，值得作为 P1 尽快确认。

#### 面试评价

- F4-5（Critical：`save_eval_report` 在 `finish_interview` 之前执行导致 `interviews/index.md` 的 `key_findings` 永远为空）**已确认修复**：`memory_module.py` 第 929-957 行现在会在未找到已有 interview 条目时主动 `insert` 一条完整记录（含 `overall_score`/`recommendation`/`key_findings`），不再依赖执行顺序。这是本次抽查中最关键的一处修复确认，直接影响"候选人历史记忆"功能是否可用。
- `key_findings` 提取逻辑仍是"`strengths`/`weaknesses` 各取前 2 条拼接"（F6-2 记录的问题未变），不包含维度评分，历史摘要信息密度偏低。
- PDF 导出（`docs/todo/02-report-export-pdf.md`）已实现（`src/utils/pdf_export.py`，`reportlab` 依赖已加入 `requirements.txt`），但**该文件测试覆盖率为 0%**（75 语句全未覆盖，含单元+集成测试）。中文渲染是 PDF 导出最容易出问题的地方（字体嵌入、换行），没有测试意味着这块极易在字体环境变化时静默出现乱码而无人发现。

#### `src/agents/`（Agent 层整体）

- `BaseAgent._run_with_tools()`（`src/agents/base.py`）设计干净：统一的 ReAct 循环、`on_tool_result` 钩子支持"提前中止 + 硬 fallback"，是全仓库代码质量最高的文件之一。
- `MainAgent` 测试覆盖率仅 **26%（单元）/ 41%（单元+集成）**，是核心业务 Agent 中最薄弱的一环——它是唯一对话入口、承载工具调用循环、Memory Nudge 后台任务、`_trim_history` 边界处理等最容易出 bug 的逻辑，但恰恰是测试最少的地方。`docs/feature-review-plan.md` 的 C1 表格早在 2026-06-04 就标注 main_agent 覆盖率 12%（当时更低），说明这是一个**长期被忽视的测试盲区**，而不是新问题。

#### `src/framework/`（框架层整体）

- `ContextManager`（`context.py`，86% 覆盖）、`PromptBuilder`（74% 覆盖）、`ToolRegistry`（95% 覆盖）、`SkillLoader`（96% 覆盖）整体测试质量不错。`PromptBuilder` 的 Layer 2（技巧索引）/ Layer 3（工具说明）分支覆盖率偏低（65-77 行、121-140 行未覆盖），建议补充 ResumeAgent 使用 skill_names/tool_names 场景的测试。

#### 开源吸引力角度的新功能建议

参考文档要求"哪些新功能能体现技术深度"，结合已发现的架构亮点，给出针对性建议（而非泛泛列举）：

1. **Agent 工具调用可视化**：`ui.py` 已经在 SSE 流中透传 `tool_call` 事件（`docs/arc/api.md` `POST /api/chat` 响应格式），但前端目前如何渲染未深入确认。若尚未做可视化展示，补一个"面试官可以在聊天框里看到 MainAgent 何时调用了 dispatch_to_agent / manage_user_memory"的轻量 UI（如折叠的工具调用卡片），成本低、演示效果好，直接对应文档要求的"Agent 工具调用可视化"。
2. **Prompt 版本管理**：`prompts.py` 目前是硬编码常量。可以低成本地加一层："每次 system prompt 变更时，`ConversationLogger` 已经记录了 system 行的历史"（见 `docs/arc/context-memory.md` §六），只需再加一个小工具脚本从 `conversations/*.jsonl` 提取 system prompt 演化历史，即可包装成"Prompt 版本回溯"能力，成本远低于从零设计 prompt 版本系统。
3. **可插拔 LLM/ASR Provider** 已经做到了（`ProviderProfile` + `STT_ENGINE` 三选一），**这是现成的加分项，只是没有在 README 里讲清楚它的设计思路**——建议在 README 或单独博客中突出讲述，而不是重新做功能。
4. **评测/Benchmark 模块**：`docs/superpowers/reports/2026-06-09-benchmark-llm-latency-verify.md` 和 `tests/test_benchmark_llm.py` 显示项目已经做过 LLM 延迟基准测试的探索，但未产品化为可复用的 benchmark 工具。可以考虑将其整理为 `scripts/benchmark_llm.py`，对外暴露"评测不同 Provider/模型延迟与质量"的能力，这正是文档要求的"评测/Benchmark 模块"雏形。

### 2.4 代码质量与工程规范

| # | 严重度 | 发现 | 位置 |
|---|---|---|---|
| C-1 | P0 | **仓库承诺的编码规范（`.cursor/rules/ecc-coding-style.mdc`）没有任何自动化工具落地**：`requirements-dev.txt` 只有 `pytest`/`pytest-asyncio`/`asgi-lifespan`，**没有 `ruff`/`black`/`isort`/`mypy`**，仓库也没有 `pyproject.toml`、`.flake8`、`ruff.toml` 等配置文件。规则文档写"Format with black, sort imports with isort, lint with ruff"，但这些工具从未被引入项目依赖，CI 里也没有 lint 步骤。对于一个"展示工程规范"的开源项目，这是**认知与现实脱节最明显的一处**，优先级应为 P0。 | `requirements-dev.txt`、`.github/workflows/ci.yml` |
| C-2 | P1 | `pytest-cov` 同样不在 `requirements-dev.txt` 中（本次评审是临时用 `pip`/已装环境跑出覆盖率数据的），意味着"80% 覆盖率"目标目前**没有任何强制机制**，只能靠人工偶尔跑一次 `--cov` 检查，覆盖率数据容易过时（`feature-review-plan.md` 里的基线还停留在 2026-06-04）。 | 全仓库 |
| C-3 | P1 | 文件规模：`ui.py`（1184 行）、`memory_module.py`（1019 行）、`routes.py`（809 行）均超过 800 行上限，属于规则文档明确列出的"坏味道"（"文件是否聚焦 <800 行"）。 | 见 §2.1 A-3/A-4 |
| C-4 | P2 | 存在若干 `except Exception: ... pass`（静默吞异常）模式，集中在 `ui.py`（前端轮询容错，可接受）、`memory_module.py`、`wasapi.py`。多数是"尽力而为、不影响主流程"的合理设计（如 UI 轮询失败重试），但**没有统一约定**（有的记录 `logger.exception`，有的完全静默），建议至少在静默分支加一行 `logger.debug`，方便排障时能看到"发生过什么但被吞掉了"。 | `src/web/ui.py`、`src/storage/memory_module.py`、`src/audio/wasapi.py` |
| C-5 | P1 | 安全性：API Key 通过 `.env` + `pydantic-settings` 管理，未硬编码到代码库（抽查 `config.py`/`llm/config.py` 未发现明文 key），`.gitignore` 已排除 `candidates/`、`recordings/`、`USER.md`、`*.db` 等敏感目录，`SECURITY.md` 说明清晰。**唯一的结构性风险**（README 已自行标注为 S-17）：候选人简历、录音、转写、评价报告均为**明文本地存储，无加密**，这是"面试数据"这类高度敏感个人信息场景下值得强调的已知限制，README 已经诚实披露，值得肯定，但作为开源项目对外发布前，建议再补充一条"不建议在多用户共享机器上运行"的提示。 | `README.md`、`SECURITY.md`、`.gitignore` |
| C-6 | P2 | PDF 上传解析路径（`parse_resume_pdf`）、文件读写工具（`file_read`/`file_write`）均限定在 `resumes/`/`candidates/` 目录内（见 `docs/arc/overview.md` 目录结构与 prompts.py 的"文件命名规则"约束），**未在本次评审中逐行验证路径穿越防护的具体实现**（如是否用 `Path.resolve()` + 前缀校验防止 `../../` 逃逸），建议作为安全维度的专项二次检查（列入附录 B 待确认清单）。 | `src/tools/file_read.py`、`src/tools/file_write.py`、`src/tools/parse_resume_pdf.py` |

### 2.5 测试与质量保障

**真实覆盖率数据（本次实测，2026-07-06，非引用旧基线）：**

| 范围 | 覆盖率 | 说明 |
|---|---|---|
| `tests/unit` | 51%（431 passed / 1 failed） | |
| `tests/unit` + `tests/integration` | 60%（484 passed / 1 failed） | 集成测试对 `routes.py` 覆盖率提升明显（19% → 74%） |
| 目标（`.cursor/rules/ecc-testing.mdc`） | 80%+ | **未达标**，距目标仍有 20 个百分点差距 |

**关键覆盖盲区（按风险排序）：**

1. `src/web/ui.py`：**0%**（848 语句）。前端交互层完全无自动化测试，只能靠人工点击或将来补充的 Playwright/浏览器测试覆盖。
2. `src/main.py`：**0%**（141 语句）。启动流程（`lifespan`）无测试，20 步依赖组装若有遗漏（如漏挂某个 `tool_ctx` 字段）不会被自动发现，只会在实际启动时报错。
3. `src/agents/main_agent.py`：26%/41%。核心对话入口测试严重不足（详见 §2.3）。
4. `src/web/websocket.py`：13%/70%（集成测试补足明显，单测仍薄弱）。
5. `src/models/question.py`、`src/utils/pdf_export.py`：0%（均为近期新增功能，"新功能配套测试"未跟上）。
6. Windows 专属 STT 客户端（`baidu_stt.py`/`xunfei_stt.py`/`wasapi.py`）：0%，属于平台/网络依赖导致的合理盲区，但意味着"生产音频链路"的正确性完全依赖人工验证。

**其他发现：**

- CI（`.github/workflows/ci.yml`）目前只运行 `pytest tests/unit tests/integration -v`，**没有覆盖率门禁、没有 lint 步骤、没有 `tests/e2e`**（E2E 依赖真实 LLM，不适合放 CI，这点合理）。
- `tests/unit/test_volc_stt.py` 一个用例在本地环境下失败（见 §2.3 详述），暴露测试对 ambient 环境（本地 `.env`）的隐式依赖，是真实存在、当前可复现的问题，非假设。
- 单元测试总数（431+ 个测试文件运行结果）、集成测试覆盖已经相当可观（43 个测试文件），相比很多个人项目"测试聊胜于无"的情况，本项目的测试基础设施本身是扎实的——**问题不是"没测试"，而是"测试分布不均"**（音频/存储层测试充分，Web 层和主 Agent 薄弱）。

### 2.6 文档与开源友好度

- `README.md` 整体质量较高：定位清晰、架构图（mermaid）、快速开始、配置说明、**数据隐私专节**（罕见但对面试数据场景非常必要）、文档索引齐全。**但演示物料缺失**：README 第 11-13 行明确写着"GIF 演示（...）待录制"，`docs/todo/01-readme-demo.md` 的验收条件全部未勾选，仓库内无 `assets/`、无截图、无 GIF。对于"5 分钟内让陌生人看懂项目"的目标，**这是当前最大的单点缺口**——没有可视化演示，再好的架构文档也难以让非技术背景的 HR/招聘方快速建立印象。
- `docs/arc/` 8 篇架构文档质量在个人开源项目中属于**上游水准**（时序图、状态机图、表格化的字段说明、明确的更新时机记录），这是本项目的核心加分项之一，建议在 README 中更醒目地引导读者浏览（当前已有链接，可以考虑加一段"如果你只有 5 分钟，先看这张图"的引导）。
- `CONTRIBUTING.md`、`SECURITY.md`、`LICENSE`（MIT）均已具备，覆盖了开源项目的基本合规要求。**缺少** `CHANGELOG.md` 和 Issue/PR 模板（`.github/ISSUE_TEMPLATE/`、`.github/PULL_REQUEST_TEMPLATE.md`），对于希望吸引外部贡献者的项目，这两项是低成本高信号的补充。
- **文档与代码进度不同步**的情况在 `docs/todo/03-structured-interview-mode.md` 已经出现（功能已实现大半，todo 仍全部未勾选）——建议开源发布前统一同步一次 `docs/todo/` 状态，或直接将已完成项归档，避免给读者"这个项目还有很多半成品"的错误印象（实际上是文档滞后而非功能缺失）。

### 2.7 可移植性与部署

- 生产音频强依赖 Windows WASAPI（`src/audio/wasapi.py`），README/`docs/arc/overview.md` 已经清晰声明"当前仅支持 Windows"，非 Windows 用户可用 `MOCK_AUDIO=true` 完整体验其余流程。**这个降级路径是诚实且完整的**，比很多"声称跨平台实际上并没有测试过"的项目更值得信任。
- **没有 Docker 化方案**：仓库内无 `Dockerfile`/`docker-compose.yml`。即使音频采集无法容器化（Windows 音频设备直通到容器本身就复杂），至少可以为"非 Windows 用户 + MOCK_AUDIO 体验模式"或"仅体验 Web/LLM 部分功能"提供一个开箱即用的 Docker 镜像，降低"clone 下来自己配 Python 3.12 环境"的门槛。这是一个**性价比很高**的可移植性改进：不需要解决音频跨平台问题，只需要把"降级模式"打包好。
- CI 目前只有 `windows-latest` 一个 runner（`ci.yml` 第 11 行）。虽然生产音频只能在 Windows 验证，但**核心业务逻辑（Agent/Framework/Storage 层）理论上是平台无关的**，可以考虑加一个 `ubuntu-latest` matrix（`MOCK_AUDIO=true`）来验证"非 Windows 开发者贡献代码"这条路径没有被破坏——目前完全没有验证，是潜在的贡献者门槛。

---

## 3. 优化建议清单（按优先级排序）

> 排序原则：**先解决"开源项目质感"和"可信度"层面的低成本高收益项，再处理结构性重构，最后是锦上添花的新功能**。每条包含：解决的问题 / 实施思路 / 工作量 / 个人能力展示价值。

### P0（本周可做，建议最先处理）

**1. 引入 ruff + black + isort + pytest-cov，接入 CI**
- **问题**：规则声称的编码规范无自动化落地（C-1/C-2）。
- **思路**：`requirements-dev.txt` 新增 `ruff`、`black`、`isort`、`pytest-cov`；根目录加 `pyproject.toml` 配置三者规则（沿用仓库已有的 88 列/双引号等风格，先跑一遍 `ruff check --fix` + `black .` 摸底改动量）；CI 增加 `ruff check` 和 `pytest --cov=src --cov-fail-under=60`（先设一个略低于当前 60% 的门槛防止倒退，后续逐步提高到 80%）。
- **工作量**：S（半天，含首次全量格式化 + 修复 lint 报错）
- **展示价值**：证明你懂得"规范不能只停留在文档里"，是工程成熟度最直观的信号，面试中可以直接讲"如何把一个隐式规范变成 CI 强制门禁"。

**2. 录制 Demo GIF + 补充 README 截图**
- **问题**：README 明确缺演示物料（§2.6），是"5 分钟看懂项目"的最大缺口。
- **思路**：用 `MOCK_AUDIO=true` 模式（无需真实音频设备），录制一次完整流程（上传简历 → 生成简报 → 开始面试 → 追问建议弹出 → 生成评价报告），用 ScreenToGif/LICEcap 压缩到 10MB 内，放入新建的 `docs/assets/` 目录，嵌入 README。
- **工作量**：S（1-2 小时，含录制和压缩调试）
- **展示价值**：直接影响招聘方/评审者的第一印象，投入产出比在所有建议中最高。

**3. 修复 `test_volc_stt.py` 的环境依赖问题**
- **问题**：测试隐式依赖本地 `.env` 内容，导致不同机器上结果不一致（§2.3）。
- **思路**：在该测试内用 `monkeypatch.setenv` 显式清空 `VOLC_APP_ID`/`VOLC_ACCESS_TOKEN` 等相关变量，或直接 mock `get_settings()` 返回值，确保测试与运行环境隔离。
- **工作量**：S（<1 小时）
- **展示价值**：体现"测试应该是确定性的、与环境无关的"这一测试工程基本功。

**4. 同步 `docs/todo/` 状态与实际代码进度**
- **问题**：结构化面试模式等功能已实现大半，todo 仍显示未开始（§2.6）。
- **思路**：逐项核对 `docs/todo/*.md` 的验收条件，勾选已完成项，未完成的补充当前进度说明；可考虑归档已完成的 todo 到 `docs/todo/done/`。
- **工作量**：S（1-2 小时）
- **展示价值**：体现文档维护的严谨性，避免给评审者"项目半成品堆积"的错误印象。

### P1（中期重构项，1-2 周内）

**5. 补齐 MainAgent 与 Web 层测试**
- **问题**：核心对话入口（`main_agent.py`）覆盖率仅 41%，`routes.py`/`ui.py`/`main.py` 是主要覆盖盲区（§2.5）。
- **思路**：优先为 `main_agent.py` 的工具调用循环、`_trim_history` 边界、Memory Nudge 触发条件补单元测试（`tests/unit/test_main_agent.py` 目前似乎缺失或不充分，可参考已有的 `test_agents.py` 补充）；`routes.py` 已有集成测试覆盖到 74%，重点补剩余的错误分支（404/409 场景）；`ui.py` 可以先从"关键渲染函数"（如 `_render_questions`）抽取为可单测的纯函数开始，而非追求整页 E2E。
- **工作量**：M（3-5 天）
- **展示价值**：体现"识别测试 ROI 最高的模块并优先投入"的工程判断力，而非机械堆测试数量。

**6. 拆分 `memory_module.py` 与 `ui.py`**
- **问题**：两个文件均超 800 行上限，承担过多职责（§2.1 A-3/A-4，§2.4 C-3）。
- **思路**：`memory_module.py` 可按职责拆为 `candidate_store.py`（候选人 CRUD）、`interview_store.py`（面试生命周期+WAL）、`eval_store.py`（评价报告），`MemoryModule` 保留为门面（Facade）类做统一入口，对外接口不变，降低回归风险；`ui.py` 可按 Tab/面板拆分为多个渲染函数所在的子模块（如 `ui_chat.py`、`ui_interview.py`、`ui_candidates.py`），`ui.py` 只保留页面骨架和路由。
- **工作量**：M（3-4 天，需要保证拆分过程中现有测试全部通过，建议先补测试再拆分）
- **展示价值**：一次真实的"大文件重构为高内聚模块"的实践，是重构类问题在面试中最常被追问细节的场景。

**7. Token 预算改用 `tiktoken` 精确计数**
- **问题**：`context.py`/`eval_agent.py` 用字符数估算 token，中英混杂场景偏差大（P-6）。
- **思路**：项目已依赖 `tiktoken`，`llm/client.py` 大概率已有计数封装（`docs/review-findings.md` F3-4 提到"精确 count_tokens"），将 `context.py` 的 `len(text)/3` 和 `eval_agent.py` 的 `len(full_text)` 替换为统一的 `count_tokens()` 调用。
- **工作量**：S-M（1-2 天，含验证压缩/分块触发阈值是否需要相应调整）
- **展示价值**：小而具体的"用真实数据替代经验估算"改进，适合作为面试中的具体案例。

**8. 补充 PDF 导出的中文渲染测试**
- **问题**：`pdf_export.py` 0% 覆盖，中文字体是 PDF 导出最容易静默出错的环节（§2.3）。
- **思路**：用 `pymupdf`（已是依赖）读取生成的 PDF 提取文本，断言中文内容正确出现且无乱码字符，作为一个"生成 → 回读校验"的集成测试。
- **工作量**：S（半天）
- **展示价值**：体现对"看似能跑但实际可能乱码"这类隐蔽 bug 的测试意识。

### P2（长期锦上添花项）

**9. Docker 化"降级体验模式"**
- 思路：仅打包 `MOCK_AUDIO=true` 模式的镜像，非 Windows 用户 `docker run` 即可体验完整 Web 流程，不承诺容器内音频采集。
- 工作量：M（2-3 天，含验证跨平台文件权限/路径问题）
- 展示价值：体现"在无法完全解决平台限制时，如何设计合理的部分可移植方案"的取舍能力。

**10. Agent 工具调用可视化 + Prompt 版本回溯（§2.3 已展开）**
- 工作量：均为 S-M
- 展示价值：直接对应文档要求的"体现技术深度"的差异化功能，且都是在现有基础设施（SSE tool_call 事件、ConversationLogger）上的低成本包装。

**11. CI 增加 `ubuntu-latest` matrix（MOCK_AUDIO 模式）**
- 工作量：S
- 展示价值：体现"降低外部贡献者门槛"的开源维护者意识。

**12. CHANGELOG.md + Issue/PR 模板**
- 工作量：S
- 展示价值：低成本的开源社区信号。

---

## 4. 实施路线图

### 阶段一：本周可做的低成本高收益项
1. 引入 ruff/black/isort/pytest-cov 并接入 CI（P0-1）
2. 录制 Demo GIF，补 README 截图（P0-2）
3. 修复 `test_volc_stt.py` 环境依赖问题（P0-3）
4. 同步 `docs/todo/` 状态（P0-4）
5. 补充 CHANGELOG.md + Issue/PR 模板（P2-12，顺手做）

### 阶段二：中期重构项（1-3 周）
1. 补齐 MainAgent / routes / ui 关键路径测试，覆盖率提升到 70%+（P1-5）
2. Token 预算改用 `tiktoken` 精确计数（P1-7）
3. PDF 导出中文渲染测试（P1-8）
4. 拆分 `memory_module.py`（先拆，风险较低，接口不变）（P1-6 前半）

### 阶段三：长期锦上添花项（视精力投入）
1. 拆分 `ui.py`（P1-6 后半，改动面较大，建议放在覆盖率提升之后）
2. Docker 化降级体验模式（P2-9）
3. Agent 工具调用可视化 + Prompt 版本回溯（P2-10）
4. CI 增加 ubuntu matrix（P2-11）
5. 覆盖率目标最终推进到 80%+，去掉 CI 覆盖率门槛的"临时低标准"

---

## 附录 A：既往 F1–F6 Review 关键问题修复状态抽查

| 编号 | 原问题（2026-06-08 记录） | 本次抽查结论 |
|---|---|---|
| F2-1 | `start_interview` 只防 INTERVIEWING，不防 EVALUATING | ✅ 已修复：`interview_controller.py` 现要求 `stage == IDLE` 才允许开始 |
| F4-5 | `save_eval_report` 早于 `finish_interview`，导致 `key_findings` 永远为空（Critical） | ✅ 已修复：`memory_module.py` 现在会在未找到条目时主动 insert 完整记录 |
| F6-1 | 依赖 F4-5，history_summary 失效（Critical） | ✅ 随 F4-5 修复而修复 |
| F5-4 | `_trim_history` 可能切断 tool_call pair | ✅ 已修复：新增"跳过截断后开头的孤儿 tool 消息"逻辑 |
| F5-2 | manage_user_memory 缺少"不应保存候选人信息"的约束 | ✅ 已修复：`_NUDGE_SYSTEM` 已加入"忽略候选人具体表现"的显式约束 |
| F3-3/F4-2 | prompt 中"题目清单"与实际"面试简报"术语不符 | ✅ 已修复：`prompts.py` 统一为"面试简报" |
| F1-5 | 候选人去重依赖文件名而非真实姓名；`/candidates` total 不准确 | 部分修复：`total` 已修复为独立 `count_candidates()`；**去重仍依赖文件名**，未确认修复（本报告 §2.3 列为 P1） |
| F3-4 | `generate_suggestion` 实际非流式调用 | **未验证**，需要二次确认 `interview_agent.py` 当前实现 |
| F4-1 | EvalAgent 失败时 `close_session()` 永不执行 | **未验证**，需要二次确认 `routes.py` 的 `/interview/eval` 处理顺序 |
| 其余 Medium/Low 项（约 50 条） | — | 未逐条重新验证，建议下一轮针对性抽查 |

## 附录 B：本次评审发现的额外事实

- 工作区当前存在一处**未提交的本地改动**（`src/web/ui.py`，+20 行）：为结构化面试问题清单加载失败时增加了指数退避重试（`_retry_questions_later`）。这不是本次评审引入的改动，是评审开始前已存在的工作中状态，特此记录以免与评审建议混淆。
- `src/tools/file_read.py`/`file_write.py` 的路径穿越防护未逐行验证（§2.4 C-6），建议作为安全维度的独立二次审查项。
- `interview_agent.py` 的流式实现状态（F3-4）和 `routes.py` 的 EvalAgent 失败处理顺序（F4-1）需要二次确认，本报告未作为"已修复"或"未修复"下结论，避免臆测。
