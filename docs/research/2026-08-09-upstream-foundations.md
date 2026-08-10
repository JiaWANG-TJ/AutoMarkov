# AutoMarkov 上游技术基础与复现边界

> 核验日期：2026-08-09（UTC）
>
> 证据范围：官方文档、官方 GitHub、PyPI/Hugging Face 发布物、论文原文与 OpenReview。
>
> 本文只给出依赖与复现决策依据，不代表这些依赖已经安装、资产已经下载或实验已经跑通。

## 1. 结论先行

AutoMarkov 可以建立在现有官方实现之上，但不能把所有组件塞进一个 Python 环境，也不能把“能运行”写成“faithful reproduction”。建议采用以下总路线：

1. `rllib-core` profile 以 **RLlib/Ray 2.56.1 + Gymnasium 1.2.2 + PettingZoo 1.26.1** 为准；Ray 2.56.1 的 `rllib` extra 在官方 `setup.py` 中精确固定 `gymnasium==1.2.2`。Gymnasium 1.3.0 只作为独立上游最新稳定版证据，不能装入该 profile。RLlib 使用默认的新 API 栈和 PyTorch，不另写一套训练框架。
2. 2026-08-09 的基础设施观测曾发现 Qwen3.6-35B-A3B / **vLLM 0.25.1+cu129** attach candidate；该历史观测没有 immutable manifest identity/hash，只用于指导 attach-first discovery，不声明当前 readiness。复用前必须重建并验证带时间、endpoint/relay identity、service snapshot 与 model/config/chat-template hashes 的 runtime manifest；0.26.0 仅是未来 clean-build profile 候选。
3. Tavily 只作为可审计的外部检索器；`Search` 固定 `include_answer=false`，并由 pair-shared immutable budget manifest 对每个比较 cell 显式冻结 `search_depth` 为 `basic` 或 `advanced`；`Crawl` 固定 `allow_external=false`。日期过滤不是历史可获得性证明。
4. CAMEL 可复用其 `ChatAgent`、`ModelFactory` 与 `Workforce` 编排接口，但 AutoMarkov 必须显式注入 worker 和 tool allowlist，不能接受 Workforce 默认附带的搜索与代码执行能力。
5. 驾驶、StarCraft II、城市能源、规划器、模型训练分别置于独立 profile。尤其注意：官方 MetaDrive 的 PyPI 包是 **`metadrive-simulator`**；PyPI 上的 `metadrive` 是无关项目。
6. Agent2World 的公开代码采用“仅研究/评估、禁止分发衍生代码”的定制许可；Text2World 代码和数据当前均未声明可复用许可。两者只能外部检出并在受限环境验证，不能 vendoring。
7. Agent² 和 A-LAMP 暂无可核验的官方公开代码。可按论文描述实现 **paper-spec reproduction**，但不得宣称官方实现或 faithful code reproduction。
8. 用 Tavily 替换 Serper、用 Qwen3.6 替换论文模型、用 CAMEL/RLlib 改写编排与算法，均属于 **controlled adaptation**。报告与表格必须与原论文结果分开。

## 2. 证据等级与术语

本文使用三个互斥标签：

| 标签 | 可声称内容 | 必须满足的条件 |
|---|---|---|
| `faithful` | 对官方发布代码或论文明确实验设置的忠实复用 | 精确版本/commit、原模型与工具、数据切分、超参数、资产版本和评测协议均可核验；仅做兼容性补丁且逐项披露 |
| `paper-spec` | 根据论文正文、附录和补充材料重建 | 没有可复用官方代码，或代码许可不允许集成；实现逐条映射论文规格，并公开论文未说明的缺口 |
| `controlled-adaptation` | 为 AutoMarkov 统一栈作出的受控改写 | 明确列出替换项、保持项、预计影响和独立结果表；不得把改写结果与原文数值直接等同 |

“官方仓库当前默认分支 commit”只用于记录核验快照，会继续漂移；实际构建应锁定 release tag 对应 commit、模型 revision、wheel/sdist hash 或受审查的固定 commit。

## 3. 建议锁定的上游 BOM

以下“当前稳定版”均在 2026-08-09 UTC 重新查询。版本安装结论以精确发布页为准；默认分支 HEAD 不应替代发布锁。

| 组件 | 建议锁 | 版本对应 commit / 完整性证据 | 许可 | AutoMarkov 角色 |
|---|---|---|---|---|
| vLLM | historical discovery hint `0.25.1+cu129`；新建 profile 候选 [`vllm==0.26.0`](https://pypi.org/project/vllm/0.26.0/) | 0.26.0 对应 [`568afb3a13806beb53bb2e6bd518269357b237c0`](https://github.com/vllm-project/vllm/commit/568afb3a13806beb53bb2e6bd518269357b237c0)；attach candidate 必须以新生成的 immutable runtime manifest 与实时 argv/package snapshot 为准 | Apache-2.0 | 独立本地推理服务；attach 不隐式升级 |
| Qwen3.6-35B-A3B | HF revision [`995ad96eacd98c81ed38be0c5b274b04031597b0`](https://huggingface.co/Qwen/Qwen3.6-35B-A3B/tree/995ad96eacd98c81ed38be0c5b274b04031597b0) | Qwen3.6 官方仓库核验快照 [`0886e34d2d6947e631b8338088a1293862243300`](https://github.com/QwenLM/Qwen3.6/commit/0886e34d2d6947e631b8338088a1293862243300) | Apache-2.0 | 作者/批评者/研究代理模型 |
| Tavily Python | [`tavily-python==0.7.27`](https://pypi.org/project/tavily-python/0.7.27/) | 无官方 Git tag；wheel SHA256 `e5cb40cc852d108ced8a313379b7098108642eedfbd97f821296a5e1a483e9b9`，sdist SHA256 `3fbbee7fc7e252479b264835e6f943b4a81395429c1bd419e8024d11bf2c1831`；核验时源码 [`de924695765d5cf28bd1975c1cfca0cd07cd7005`](https://github.com/tavily-ai/tavily-python/commit/de924695765d5cf28bd1975c1cfca0cd07cd7005) | MIT | Search/Extract/Crawl 与用量审计 |
| CAMEL | [`camel-ai==0.2.90`](https://pypi.org/project/camel-ai/0.2.90/) | [`deb286f36702ab15a2cb890c6e223a79e4ce4284`](https://github.com/camel-ai/camel/commit/deb286f36702ab15a2cb890c6e223a79e4ce4284) | Apache-2.0 | 受限多代理编排 |
| Ray/RLlib | [`ray[rllib]==2.56.1`](https://pypi.org/project/ray/2.56.1/) | [`936f0d7d49d9da8ac1a9f04cc8a89faf2cb3c42a`](https://github.com/ray-project/ray/commit/936f0d7d49d9da8ac1a9f04cc8a89faf2cb3c42a) | Apache-2.0 | 可扩展 RL/MARL 训练后端 |
| Safetensors | [`safetensors==0.8.0`](https://pypi.org/project/safetensors/0.8.0/) | manylinux x86_64 wheel SHA256 `fd6f3f93c9a0a7cc2788ee63fb763353d4bd2e89b0751bc78fcf7dda00bea774`；sdist SHA256 `fabaf3e0f18a6618d9b36560682562157f77c2b71fcffc7b432be2baed9d753d` | Apache-2.0 | 无 sealed capability 的 checkpoint exporter 与 sealed evaluator 之间的 weights-only tensor format |
| Gymnasium | `rllib-core` 使用 `gymnasium==1.2.2`；[`gymnasium==1.3.0`](https://pypi.org/project/gymnasium/1.3.0/) 仅作独立最新上游参考 | Ray 2.56.1 官方 [`setup.py`](https://github.com/ray-project/ray/blob/ray-2.56.1/python/setup.py#L325-L331) 精确 pin 1.2.2；1.3.0 对应 [`53bf3e9a884783eb72ad3fc8b15780914c97c3e1`](https://github.com/Farama-Foundation/Gymnasium/commit/53bf3e9a884783eb72ad3fc8b15780914c97c3e1) | MIT | 单智能体环境契约；跨版本通过 profile 隔离 |
| PettingZoo | [`pettingzoo==1.26.1`](https://pypi.org/project/pettingzoo/1.26.1/) | [`1756a4d7494b532651f0024ff7087ef4945432a6`](https://github.com/Farama-Foundation/PettingZoo/commit/1756a4d7494b532651f0024ff7087ef4945432a6) | MIT | AEC/Parallel 多智能体契约 |
| MPE2 | [`mpe2==1.1.0`](https://pypi.org/project/mpe2/1.1.0/) | [`7590d9d52791e321974d4fda6090fb18f34dbf49`](https://github.com/Farama-Foundation/MPE2/commit/7590d9d52791e321974d4fda6090fb18f34dbf49) | MIT | MPE `simple_spread_v3` |
| MiniGrid | [`minigrid==3.1.0`](https://pypi.org/project/minigrid/3.1.0/) | [`90928729376741a41222a257911343b97103b548`](https://github.com/Farama-Foundation/Minigrid/commit/90928729376741a41222a257911343b97103b548) | Apache-2.0（tag 源码 `LICENSE`；PyPI metadata 误标 MIT） | Memory POMDP |
| OpenSpiel | [`open_spiel==2.0.1`](https://pypi.org/project/open-spiel/2.0.1/) | [`112b77704631fc2ce7ad8e4581f6ca09798ce15a`](https://github.com/google-deepmind/open_spiel/commit/112b77704631fc2ce7ad8e4581f6ca09798ce15a) | Apache-2.0 | 博弈建模与算法分析 |
| MetaDrive | [`metadrive-simulator==0.4.3`](https://pypi.org/project/metadrive-simulator/0.4.3/) | [`5bf8ea8909c4643a4099a250e6f5fb89c695d8b4`](https://github.com/metadriverse/metadrive/commit/5bf8ea8909c4643a4099a250e6f5fb89c695d8b4) | Apache-2.0 | 驾驶环境 |
| ScenarioNet | 固定源码 commit | [`d4acdb5f5a844744fc85cb2dc3880d7d4a6eb170`](https://github.com/metadriverse/scenarionet/commit/d4acdb5f5a844744fc85cb2dc3880d7d4a6eb170)；无 PyPI 发布 | Apache-2.0 | 场景描述、转换与数据加载 |
| SMACv2 | 固定源码 commit | [`577ab5a2cff2391f8df582da5731ea9cd6adf3c6`](https://github.com/oxwhirl/smacv2/commit/577ab5a2cff2391f8df582da5731ea9cd6adf3c6)；无 PyPI 发布 | MIT | StarCraft II 协作 MARL |
| CityLearn | [`citylearn==2.5.0`](https://pypi.org/project/citylearn/2.5.0/) | [`29062af6d077409e1c37a3e53a6cac30fd4d02bc`](https://github.com/citylearn-project/CityLearn/commit/29062af6d077409e1c37a3e53a6cac30fd4d02bc) | MIT | 建筑能源环境 |
| Unified Planning | [`unified-planning==1.3.0`](https://pypi.org/project/unified-planning/1.3.0/) | [`42e66926e400ab1367b5b02af504d8c7016b9243`](https://github.com/aiplan4eu/unified-planning/commit/42e66926e400ab1367b5b02af504d8c7016b9243) | Apache-2.0 | PDDL I/O 与规划器统一接口 |
| SwanLab | [`swanlab==0.9.4`](https://pypi.org/project/swanlab/0.9.4/) | [`f86de8a7e74fa6bb39d171cb4f856bb72fe3b786`](https://github.com/SwanHubX/SwanLab/commit/f86de8a7e74fa6bb39d171cb4f856bb72fe3b786) | Apache-2.0 | 离线优先实验追踪 |
| LlamaFactory | [`llamafactory==0.9.5`](https://pypi.org/project/llamafactory/0.9.5/) | [`7af909522a951e3ad9f022ea6f88b6755257eaa5`](https://github.com/hiyouga/LLaMA-Factory/commit/7af909522a951e3ad9f022ea6f88b6755257eaa5) | Apache-2.0 | 独立微调/复现实验 profile |

### 3.1 需要在锁文件中额外记录的事实

- PyPI 当前 `torch` 为 [`2.13.0`](https://pypi.org/project/torch/2.13.0/)，但不能据此跨 profile 统一升级。PyTorch、CUDA、驱动、GPU 架构、vLLM 和 Ray 的兼容矩阵必须在目标机器上联合锁定。
- `unified-planning==1.3.0` 的 sdist SHA256 为 `9f1914377172626e512bd4c1545aebddbfd4752bf37b36f9cd41ff059a7e6a52`，wheel SHA256 为 `3ad9c790d238a12ce1dfd25f564e728d98350a0b4b79d3beb60978f95487fa46`。
- MetaDrive 必须拒绝依赖名 `metadrive`。该 [PyPI 项目](https://pypi.org/project/metadrive/) 是另一个工具控制器集成项目，不是 `metadriverse/metadrive` 模拟器。
- ScenarioNet 与 SMACv2 没有可核验的 PyPI 稳定发布，必须用完整 commit 安装，并保存源归档 hash。

## 4. vLLM 与 Qwen3.6

### 4.1 官方能力与接口

[Qwen3.6-35B-A3B 官方模型卡](https://huggingface.co/Qwen/Qwen3.6-35B-A3B)给出的模型规模为 35B 总参数、3B 激活参数，原生上下文 262,144，并建议 vLLM 不低于 0.19.0。官方 vLLM 示例为：

```bash
vllm serve Qwen/Qwen3.6-35B-A3B \
  --revision 995ad96eacd98c81ed38be0c5b274b04031597b0 \
  --port 8000 \
  --tensor-parallel-size 8 \
  --max-model-len 262144 \
  --reasoning-parser qwen3
```

纯文本工作负载可加 `--language-model-only`；自动工具调用还需 `--enable-auto-tool-choice --tool-call-parser qwen3_coder`。AutoMarkov 初期不应默认开启自动工具调用，应由外层策略层解析、验证并执行结构化工具请求。

vLLM 暴露 OpenAI-compatible API；AutoMarkov 的稳定边界应是 `/v1/chat/completions`，不让领域组件直接导入 vLLM 内部 Python 类。官方 [`vllm serve` CLI 文档](https://docs.vllm.ai/en/latest/cli/serve/)支持 `--revision`、`--tokenizer-revision`、`--code-revision`、`--api-key`、`--host` 与 `--port`。运行 manifest 必须记录非敏感参数，并对 `--api-key` 仅记录鉴权已启用及脱敏 credential ID/fingerprint；bearer token、Authorization header 和 credential locator/value 均不得持久化。

### 4.2 安全与隔离约束

- 服务默认只监听 loopback 或受控容器网络，并启用 `--api-key`；不得无认证暴露到共享网段。
- `--allowed-local-media-path` 保持未设置。官方文档明确将其标为安全风险；不能把整个工作区或数据目录授权给模型服务。
- 不使用未经审查的 `--trust-remote-code`。如模型确需远程代码，必须再锁 `--code-revision` 并审查源码。
- `--trust-request-chat-template` 保持默认关闭；聊天模板来自锁定 tokenizer/model，而不是请求方。
- prompt/response、工具参数和本地路径不得进入普通访问日志。vLLM 默认不记录请求 prompt，但调试级别与外围代理仍可能泄漏内容。
- 模型权重缓存是只读输入；生成结果写入运行专属目录。作者、训练和 sealed evaluation 使用不同凭据与目录挂载。
- 262K 上下文对 KV cache 与显存要求很高；实际 `tensor_parallel_size`、`gpu_memory_utilization` 和 `max_model_len` 需通过目标 GPU canary 决定，不能仅凭模型卡宣布可服务。

### 4.3 忠实度判定

Qwen3.6 是 AutoMarkov 的产品选择，不是 Agent2World、Agent² 或 A-LAMP 原论文的共同基础模型。任何用 Qwen3.6 得到的论文轨实验均标记 `controlled-adaptation`；只有产品内同版本回归可称官方模型的 faithful reuse。

## 5. Tavily Search / Extract / Crawl

### 5.1 接口与计费语义

三类接口都使用 Bearer token，但凭据只能由运行环境注入，不能写入配置快照、SwanLab config、prompt 或事件日志。

| API | 必须显式设置 | 返回与计费要点 | 官方来源 |
|---|---|---|---|
| Search `POST /search` | pair-shared immutable budget manifest 显式冻结 `search_depth` 为 `basic` 或 `advanced`，同时固定 `include_answer=false`、`include_usage=true`、`auto_parameters=false`；按需冻结域名和日期过滤 | `basic` 每请求 1 credit，`advanced` 2 credits；响应含 `results`、`request_id`，请求 usage 后含 credit 信息。任何未登记值或运行时升级都 fail closed | [Search API](https://docs.tavily.com/documentation/api-reference/endpoint/search) |
| Extract `POST /extract` | URL allowlist、`extract_depth`、有限 `timeout`、`include_usage=true` | basic 每 5 个成功 URL 1 credit，advanced 每 5 个成功 URL 2 credits；失败提取不计费。`failed_results` 表示逐 URL 部分失败，不能只检查 HTTP 200 | [Extract API](https://docs.tavily.com/documentation/api-reference/endpoint/extract) |
| Crawl `POST /crawl` | `allow_external=false`、小的 `max_depth`/`max_breadth`/`limit`、有限 `timeout`、`include_usage=true` | 映射和内容提取分别计费；使用自然语言 `instructions` 会提高映射成本。默认 `allow_external=true`，若不覆盖会越出起始站点 | [Crawl API](https://docs.tavily.com/documentation/api-reference/endpoint/crawl) |

Search 的 `start_date`、`end_date`、`days` 或内容中的 `published_date` 只描述搜索引擎识别的发布/更新时间。它们不能证明页面在目标历史 cutoff 前已可访问。AutoMarkov 需额外保存检索时间、原 URL、内容 hash、独立归档/权威来源证据和 cutoff 判定。

### 5.2 错误状态与重试契约

[官方 Search API 错误表](https://docs.tavily.com/documentation/api-reference/endpoint/search)与当前 SDK 源码共同给出以下语义：

| HTTP / 异常 | 含义 | AutoMarkov 行为 |
|---|---|---|
| 400 / `BadRequestError` | 请求参数无效 | 不重试；记录已脱敏参数摘要并阻断该任务 |
| 401 / `InvalidAPIKeyError` | 缺失或无效 API key | 当前 key 标记 `INVALID` 并切换下一 key；全部无效时终止为凭据配置错误 |
| 403 / `ForbiddenError` | 端点或权限被禁止 | 不重试；权限/产品配置错误 |
| 429 / `UsageLimitExceededError` | 请求过快或速率限制 | 当前 key 进入 `COOLDOWN`，遵循 `Retry-After` 与 full jitter；预算允许时路由到下一可用 key |
| 432 / `ForbiddenError` | 套餐 usage limit | 当前 key 标记 `EXHAUSTED` 并切换下一 key；同账户共享额度耗尽时终止为预算门禁 |
| 433 / `ForbiddenError` | pay-as-you-go limit | 当前 key 标记 `EXHAUSTED` 并切换下一 key；同账户共享额度耗尽时终止为费用门禁 |
| 5xx | Tavily 内部错误 | 有界重试，超过次数后降级为明确失败，不伪造空结果 |
| SDK timeout / `TimeoutError` | 网络或服务超时 | 有界重试；保留每次耗时与 attempt |
| `failed_results` | Extract 中部分 URL 失败 | 逐 URL 入账，成功与失败分别落 ledger；只重试允许的失败项 |

SDK 在 [`tavily-python` 当前错误定义](https://github.com/tavily-ai/tavily-python/blob/de924695765d5cf28bd1975c1cfca0cd07cd7005/tavily/errors.py)中还包含 keyless 相关异常。AutoMarkov 使用显式 API key，不依赖 keyless 模式。

上述“哪些错误重试”是基于官方状态语义的 AutoMarkov 策略，不是 Tavily 对幂等性的承诺。每次调用应写入 append-only ledger：`request_id`、endpoint、状态、attempt、耗时、credit、查询/URL 的不可逆摘要、结果 hash 和运行 ID；不得写 token 与不必要的原始敏感内容。

[官方 rate-limit 表](https://docs.tavily.com/documentation/rate-limits)在核验时给出开发环境通常 100 RPM、生产环境通常 1000 RPM，Crawl 为 100 RPM。AutoMarkov 只依据冻结的保守本地预算、三类已允许 endpoint 的响应 metadata 和逐请求 ledger 收紧限流；不能调用未授权 endpoint，也不能把文档数值硬编码为账户配额事实。

### 5.3 忠实度判定

Agent2World 原论文使用 Serper 搜索。AutoMarkov 使用 Tavily 时属于 `controlled-adaptation`；需要保持查询数、搜索轮数、域名拒绝列表、检索快照和总 credit 可比，并单独做 retrieval ablation。

## 6. CAMEL 多代理编排

[`camel-ai==0.2.90`](https://pypi.org/project/camel-ai/0.2.90/)提供 AutoMarkov 可以直接复用的三个关键接口：

- [`ChatAgent`](https://docs.camel-ai.org/reference/camel.agents.chat_agent)：接收 system message、model backend 与 tools，通过 `step()` 执行一轮代理交互。
- [`ModelFactory`](https://docs.camel-ai.org/reference/camel.models.model_factory)：上游可创建 `ModelPlatformType.VLLM` 后端；AutoMarkov authoring worker 禁止使用这条直连路径，改为注入只调用 `LocalLlmRuntime` versioned edge 的窄 model backend。
- [`Workforce`](https://docs.camel-ai.org/key_modules/workforce)：提供 coordinator、task planner、worker、任务分解、分派、异步执行与失败处理，可通过 `process_task_async` 接入外部事件循环。

AutoMarkov 对 CAMEL 的复用边界：

1. 角色、输入输出 schema、预算和工具能力由 AutoMarkov 声明；CAMEL 只执行编排。
2. 不接受 Workforce 默认 worker。官方 Workforce 文档说明默认能力可能包含 `SearchToolkit`、`CodeExecutionToolkit` 和 `ThinkingToolkit`；这会绕过 Tavily ledger 与执行沙箱。
3. 搜索、文件读写、代码执行、训练启动分别使用显式 tool allowlist。没有权限的角色即使在 prompt 中请求也不能获得工具。
4. 共享事实通过带版本的 typed blackboard/event store 传递；自然语言对话仅是证据附件，不能成为唯一状态。
5. 同一任务设置 tool-call 上限、token 上限、wall-clock deadline 和取消传播；异步 worker 不能在父任务终止后继续写结果。
6. CAMEL 的内置 memory、model routing、默认 prompt 和异常重试若被采用，均需锁版本并纳入可复现 manifest。

CAMEL 不是 Agent2World、Agent² 或 A-LAMP 论文的官方执行框架。因此复用 CAMEL 编排这些论文思想属于 `controlled-adaptation`。

## 7. RLlib、Gymnasium、PettingZoo、MPE2、MiniGrid 与 OpenSpiel

### 7.1 统一接口契约

[`Gymnasium Env`](https://gymnasium.farama.org/api/env/)是单智能体契约：

```text
reset(seed, options) -> (observation, info)
step(action) -> (observation, reward, terminated, truncated, info)
```

`terminated` 表示任务 MDP 的终止态，`truncated` 表示外部时限或边界截断。训练 bootstrap 必须区分两者，旧式 `done = terminated or truncated` 只能用于控制 episode 循环，不能直接替代 value target 语义。

[`PettingZoo`](https://pettingzoo.farama.org/)提供：

- [AEC API](https://pettingzoo.farama.org/api/aec/)：智能体依次行动，适合顺序博弈。
- [Parallel API](https://pettingzoo.farama.org/api/parallel/)：一组存活智能体同时给动作并同时推进，适合 MPE2 等同步 MARL。

新代码使用具体环境模块公开的 `env()` 或 `parallel_env()` 构造器，并运行 PettingZoo 官方 AEC/Parallel API tests；PettingZoo 1.26.1 顶层不提供 `pettingzoo.make`。需要按字符串解析环境时，由 AutoMarkov 自己的显式 allowlisted registry 映射到这些官方构造器。适配器需记录 agent 生命周期、possible agents、每个 agent 的 observation/action space 和 episode seed。

### 7.2 RLlib 新 API 栈

[RLlib 新 API 栈迁移指南](https://docs.ray.io/en/latest/rllib/new-api-stack-migration-guide.html)确认核心抽象为 `RLModule`、`Learner`、`SingleAgentEpisode`/`MultiAgentEpisode` 和 `ConnectorV2`，算法配置入口为 `AlgorithmConfig`。新实现应使用 PyTorch；不能为兼容旧示例回退到已弃用的 ModelV2/Policy 路线。

[RLlib multi-agent 文档](https://docs.ray.io/en/latest/rllib/multi-agent-envs.html)支持 `MultiAgentEnv` 的 dict 输入输出，并提供 `PettingZooEnv`、`OpenSpielEnv` 适配器。AutoMarkov 应通过显式 `policy_mapping_fn` 和共享/独立 policy 配置表达参数共享。

RLlib 没有一个可直接等同于论文“MAPPO”的稳定一键开关。AutoMarkov 的 centralized-value recurrent PPO 应写成基于新 API 栈的自定义 CTDE-PPO，并标记“MAPPO-compatible controlled adaptation”；只有逐项匹配原 MAPPO 论文与参考实现后才能称 faithful MAPPO。

### 7.3 环境与用途

| 环境 | 官方接口与确定事实 | AutoMarkov 决策 |
|---|---|---|
| MPE2 Simple Spread | [`simple_spread_v3`](https://mpe2.farama.org/main/environments/simple_spread/)支持 AEC 与 Parallel；默认 `N=3`、`local_ratio=0.5`、`max_cycles=25`、离散动作；默认三智能体时 local obs 长 18、global state 长 54 | 使用 Parallel API；锁完整 kwargs 和 seed；不得继续引用已从 PettingZoo 移出的旧 MPE 实现 |
| MiniGrid Memory | [`MiniGrid-MemoryS17Random-v0`](https://minigrid.farama.org/environments/minigrid/MemoryEnv/)是部分可观测记忆任务；观察为含 `image`、`direction`、`mission` 的 Dict，动作空间 `Discrete(7)`；奖励与耗时相关 | 固定 env ID、size、random_length、max_steps 和 wrappers；实测 3.1.0 中 timeout 的 `truncated` 行为并加契约测试，不能仅按文档文字推断 |
| OpenSpiel | 官方 [`pyspiel` API](https://openspiel.readthedocs.io/en/latest/api_reference.html)以 `load_game`、Game/State、合法动作、chance node 等建模；RLlib 提供 `OpenSpielEnv` | 用于博弈论基准与分析，不替代 PettingZoo 的一般 MARL runtime；chance seed、game parameters 与 wrapper 都进 manifest |

六个计划实验中的 Taxi-v4 作为 sealed gold/evaluator environment；主 generation cell 按冻结 `SYNTHESIS/GENERATE` 合同从公开规则构建 candidate，禁止读取或实例化该 official env。MiniGrid、MPE2 分别走上述官方 reuse 接口。任何作者生成的 wrapper 都必须只做 schema/时间语义转换，不能悄悄改变奖励、可见性、动作可用性或 episode horizon。

### 7.4 profile 隔离

通用 `rllib-core` profile 包含 PyTorch、Ray/RLlib、Gymnasium 1.2.2、PettingZoo、MPE2、MiniGrid、safetensors 和按需 OpenSpiel，但 Gymnasium 1.2.2 仍分发 Taxi-v3 源码，所以该通用 profile 不执行 Taxi cell。Taxi generation/training 使用同一 lock 派生的 `rllib-taxi-synthesis` deny-layer profile：principal 对 `gymnasium/envs/toy_text/taxi.py`、对应 bytecode/resource、wheel/sdist 与 package cache均无读取能力；image build 和每次 preflight 都必须证明 direct open、resource lookup、`find_spec`/import 和 cache discovery fail closed并签发 attestation。`Taxi-v4` 只存在于 Gymnasium 1.3.0 的独立 `sealed-env-taxi-gold` worker，并且只通过 `RemoteEnv` 接受 `sealed-evaluator-rllib` principal；不得接入 authoring 或通用 training、把 Taxi 降为 v3或放松 Ray 的 Gymnasium pin。普通 RLlib checkpoint 只由同一 frozen trainer profile/namespace 内的一次性 export execution 转为 strict-manifest-bound weights-only safetensors，checkpoint/pickle/cloudpickle 永不跨 profile；sealed evaluator 只实例化预注册可信 RLModule 并加载该 tensor map，永不反序列化 checkpoint 或 candidate code。模型推理、驾驶、SC2、规划器和训练微调不进入 sealed worker。

### 7.5 密码学与 canonical JSON 官方实现

签名、证书和 TLS helper 复用 PyCA [`cryptography==49.0.0`](https://pypi.org/project/cryptography/49.0.0/)；该 release 由 `pyca/cryptography` 的 Trusted Publishing workflow 发布。RFC 8785 canonical JSON 复用 Trail of Bits [`rfc8785==0.1.4`](https://pypi.org/project/rfc8785/0.1.4/)，其 pure-Python API 输出 UTF-8 bytes，wheel SHA-256 为 `520d690b448ecf0703691c76e1a34a24ddcd4fc5bc41d589cb7c58ec651bcd48`。TLS transport 使用 Python stdlib `ssl` 并强制 TLS 1.3，不自写 record protocol、Ed25519、X.509 或 JCS encoder。实际 platform wheels、Python/OpenSSL patch、CA bundle 和 image digest仍必须在各 profile lock/SBOM 中冻结；版本文字不能替代 artifact hash 与 conformance vectors。

## 8. MetaDrive 与 ScenarioNet

### 8.1 官方组合

[`MetaDrive`](https://github.com/metadriverse/metadrive)提供 Gymnasium 风格 `MetaDriveEnv`，其 [`ScenarioEnv`](https://metadrive-simulator.readthedocs.io/en/latest/rl_environments.html#real-world-environment)用于真实世界轨迹场景。官方安装包是 `metadrive-simulator==0.4.3`。

[`ScenarioNet`](https://github.com/metadriverse/scenarionet)定义统一场景描述、数据转换与场景数据库，并与 MetaDrive 紧密配合。ScenarioNet 没有 PyPI 版本；官方 README 的环境建议仍以 Python 3.9 和源码 editable install 为主，说明它不适合与 Python 3.11/3.12 的核心 profile 强行合并。

### 8.2 数据与隔离约束

- 建立 `driving` 独立容器/profile，锁 Python、Panda3D、MetaDrive、ScenarioNet、渲染库、系统图形库和 headless 参数。
- 通过进程协议输出规范化 transition/episode，不让核心训练直接依赖 ScenarioNet 私有对象。
- Waymo、nuScenes、nuPlan 等来源数据仍受其原始许可和访问条款约束；ScenarioNet 的 Apache-2.0 代码许可不覆盖底层数据。
- 原始数据与转换后场景放到工作区之外的只读 cache；manifest 保存数据源 release、split、转换器 commit、schema version、文件清单和 checksum。
- 没有明确再分发权时，不将场景数据、地图、缓存或派生切片提交到仓库或公开 artifact。
- renderer、数据下载与随机交通生成分别可控；评测时禁止静默联网下载和自动换数据版本。

MetaDrive + ScenarioNet 是 AutoMarkov 驾驶任务的官方实现复用；若对奖励、观测、交通分布或地图切分作改动，则实验层仍是 `controlled-adaptation`。

## 9. SMACv2

SMACv2 的权威来源是 [oxwhirl/smacv2](https://github.com/oxwhirl/smacv2)和[论文原文](https://arxiv.org/abs/2212.07489)。仓库无 PyPI 发布，当前应锁 commit `577ab5a2cff2391f8df582da5731ea9cd6adf3c6`。

官方入口 `StarCraftCapabilityEnvWrapper` 延续 SMAC 接口，包括 `get_env_info()`、`get_obs()`、`get_state()`、可用动作查询和 `step(actions) -> reward, terminated, info`。这不是 Gymnasium 5-tuple，也不是 PettingZoo Parallel API；适配器必须显式补足 truncation、agent dict、space 和 seed 语义。

计划的 `protoss_5_vs_5` 应来自仓库 `sc2_gen_protoss.yaml`：`n_units=5`、`n_enemies=5`。完整锁定项还包括：

- SMACv2 commit、PySC2 版本、StarCraft II 精确 build；
- SMAC maps 版本和 `32x32_flat.SC2Map` hash；
- capability distribution、unit type、start position、team configuration 与 seed；
- episode limit、reward scale、state/observation feature 开关和动作可用性规则。

StarCraft II 二进制与地图是外部资产，不能由代码仓库许可推导其再分发权。建立 `smacv2` 独立 profile/container，外部只读挂载固定版本资产；无资产时应明确 skip，而不是退回 SMAC1 或伪造环境。

使用官方版本与配置可标记 SMACv2 environment faithful reuse；接入 RLlib、修改 capability distribution 或训练算法属于 `controlled-adaptation`。

## 10. CityLearn

AutoMarkov 应锁 [`citylearn==2.5.0`](https://pypi.org/project/citylearn/2.5.0/)。当前在线文档已出现 2.6.0 beta 内容，因此实现前必须以 2.5.0 tag 的源码和实测接口为准，不能把 beta 文档行为当成稳定版事实。

[`CityLearnEnv`](https://www.citylearn.net/api/citylearn.citylearn.html)遵循 Gymnasium `reset -> (observations, info)` 和 `step -> (observations, reward, terminated, truncated, info)`，但原始 observation/action 是按建筑组织的 list 结构。当前官方源码还提供 `RLlibMultiAgentEnv`，只适用于 `central_agent=False`，把列表转换为 RLlib agent dict；官方建议配合 observation clipping wrapper。

隔离要求：

- 锁 schema/dataset 名称、下载 URL、内容 hash、时间切片、天气与价格数据版本；
- 预取到外部不可变 cache 后离线运行，禁止评测时自动下载或更新；
- 固定 centralized、decentralized-independent 或 decentralized-coordinated 控制模式；
- 奖励函数、normalized observation、action scaling 和 episode slice 都是实验契约，不能隐藏在 wrapper 默认值中；
- 原始数据许可与 CityLearn 代码 MIT 许可分别审查。

官方 `CityLearnEnv` 与稳定版 wrapper 是 faithful implementation reuse；AutoMarkov 的多代理奖励、RLlib policy mapping 和任务生成属于 `controlled-adaptation`。

## 11. Unified Planning 与 PDDL

[`unified-planning==1.3.0`](https://pypi.org/project/unified-planning/1.3.0/)是 PDDL 互操作层，而不是自带所有规划器的单体求解器。

- [`PDDLReader`](https://unified-planning.readthedocs.io/en/stable/api/io/PDDLReader.html)解析 domain/problem；PDDL 标识符不区分大小写，读取器会规范化名称。
- `PDDLWriter` 可把内部 problem 输出为 PDDL；往返并不保证保留原始排版、注释或名称大小写。
- [`OneshotPlanner` / engine selection](https://unified-planning.readthedocs.io/en/stable/engines/02_engine_selection.html)通过插件选引擎；核心包不保证机器上已有兼容 planner。
- [官方 PDDL 示例](https://unified-planning.readthedocs.io/en/latest/notebooks/io/01-pddl-usage-example.html)展示解析、求解与计划验证流程。

规划器 profile 应维护明确 allowlist：engine 名、engine package/binary 版本、许可、可支持的 problem kind、命令行、timeout、CPU/memory limit。规划器通常通过 subprocess 和临时文件执行，因此临时目录必须运行专属、输入只读、输出限额，且不继承 Tavily/模型/评测凭据。

PDDL OOD 是一种表示与评测通道，不是第五种 MDP 数学类别。AutoMarkov 若把 Text2World 或 Agent2World 任务映射到 Gymnasium/RLlib，必须分别报告：PDDL 语法有效性、planner solvability、plan correctness 与在线 RL return；不能用其中一个指标替代其他指标。

使用 Unified Planning 的解析与验证接口是 faithful upstream reuse；规划器选择、PDDL 到 RL 环境的转换与未知符号修复是 `controlled-adaptation`。

## 12. SwanLab

[`swanlab==0.9.4`](https://pypi.org/project/swanlab/0.9.4/)用于指标、配置和 artifact 索引，但 AutoMarkov 必须显式离线优先。官方 [`swanlab.init`](https://docs.swanlab.cn/api/py-init.html)支持 `online`、`local`、`offline`、`disabled` 等 mode，默认是 online；不能依赖操作者机器上的隐式默认值。

建议契约：

```python
swanlab.init(
    mode="offline",
    project="automarkov",
    experiment_name=run_id,
    config=redacted_manifest,
)
```

[SwanLab Offline 文档](https://docs.swanlab.cn/api/cli-swanlab-offline.html)说明离线运行可后续同步。同步是单独、显式、可审查的发布动作，不是实验完成时自动发生。

隔离要求：

- 所有 mode 在代码/启动 manifest 中显式设置；sealed evaluation 强制 `offline` 或 `disabled`。
- config 只传脱敏 manifest；环境变量、API key、本地绝对私密路径、原始 prompt 与受限数据不得自动采集。
- log 根目录位于忽略的运行目录；每个 run 独占目录并保存 SwanLab 版本。
- 自动 Git、硬件、依赖元数据也要按数据治理策略筛选；离线并不等于可公开。
- 后续 `swanlab sync` 前执行 artifact allowlist、许可、隐私和 sealed-boundary 审查。

## 13. LlamaFactory

[`LlamaFactory v0.9.5` 发布说明](https://github.com/hiyouga/LLaMA-Factory/releases/tag/v0.9.5)明确列出 Qwen3.5/3.6 与 Transformers v5 支持。官方入口包括 `llamafactory-cli train`、WebUI、模型导出和 API；SwanLab 可通过 `use_swanlab` / `swanlab_mode` 集成，见[官方集成文档](https://docs.swanlab.cn/guide_cloud/integration/integration-llama-factory.html)。

AutoMarkov 只在独立 `finetune` profile 使用 LlamaFactory：

- 锁基础模型 revision、tokenizer revision、dataset snapshot/hash、template、训练 YAML、LlamaFactory commit、Transformers/PEFT/DeepSpeed 版本；
- 训练 GPU 与 vLLM/RLlib GPU 分离，checkpoint 目录与推理权重目录分离；
- 训练数据先做许可、隐私、去污染和 sealed-eval 隔离审查；
- checkpoint 只在通过离线评测和 manifest 审核后晋级，不覆盖原模型；
- LlamaFactory 0.9.5 内置 SwanLab callback 显式使用其受支持的 `swanlab_mode=local`，并由 profile default-deny egress；若实验要求可后续 `swanlab sync` 的真正 offline log，则禁用内置 callback，改由受控 wrapper 显式调用 `swanlab.init(mode="offline")`，禁止回落到默认 cloud；
- 将 adapter merge、量化和 vLLM canary 作为独立可追踪步骤。

Agent2World 论文的 SFT 使用 LlamaFactory、Llama-3.1-8B、30K 最大序列长度与 5 epochs。沿用这些设置并锁原数据才可能讨论 faithful；改为 Qwen3.6、不同数据或不同训练轮数是 `controlled-adaptation`。

## 14. Agent2World、Text2World、Agent² 与 A-LAMP

### 14.1 总览

| 工作 | 官方代码/发布状态 | 许可结论 | AutoMarkov 允许的复现等级 |
|---|---|---|---|
| Agent2World | 官方仓库 [`DeepExperience/agent2world@1330f3c…`](https://github.com/DeepExperience/agent2world/tree/1330f3cde9509f05d204a255f0f7f43208515dce)，[项目页](https://agent2world.github.io/)，[论文](https://arxiv.org/abs/2512.22336) | 定制 Research/Evaluation Only；非商业、禁止分发/再许可/销售代码及衍生物，详见[固定 commit 的 LICENSE](https://github.com/DeepExperience/agent2world/blob/1330f3cde9509f05d204a255f0f7f43208515dce/LICENSE) | 受限外部运行可做官方代码验证；集成实现只能 paper-spec 或 clean-room controlled adaptation，需法律审查 |
| Text2World | 官方仓库 [`Aaron617/text2world@9440ff…`](https://github.com/Aaron617/text2world/tree/9440ff7732fca4bcc8d9fb59a435886735f4059a)，[论文](https://arxiv.org/abs/2502.13092)；HF canonical 数据 revision [`fc1e7f…`](https://huggingface.co/datasets/EvolventAI/text2world/tree/fc1e7f93b59dd3efa1622401adc7d6cdae3a3c62) | 代码仓库无 LICENSE；HF dataset 未声明许可。默认无复制、修改、再分发授权 | 只读外部验证；不得 vendoring、发布派生数据或声称可复用许可 |
| Agent² | [论文](https://arxiv.org/abs/2509.13368)、[OpenReview](https://openreview.net/forum?id=nwXCmnZ35w)；截至核验日未找到论文作者链接的官方代码 | 论文在 OpenReview 标为 CC BY 4.0；无代码许可可核验 | `paper-spec`；不能声称官方代码 faithful reproduction |
| A-LAMP | [论文](https://arxiv.org/abs/2512.11270)、[OpenReview](https://openreview.net/forum?id=oQdo7H38dC)；截至核验日未找到论文作者链接的官方代码 | 论文为 CC BY-ND 4.0；无代码许可可核验 | `paper-spec`；AutoMarkov 版本为 controlled adaptation |

### 14.2 Agent2World

论文与仓库公开的体系由 Deep Researcher、Model Developer、Unit Tester 和 Simulation Tester 等角色组成，目标是从自然语言描述生成可执行 world model，再经测试与迭代改进。其复现轨覆盖 Text2World/PDDL、Code World Model Benchmark 与 ByteSized32 等。

必须保留的论文设置包括：

- 官方 API 实验使用 GPT-4.1-mini，开源模型轨使用 Llama-3.1-8B；
- 推理 `temperature=0`、`top_p=1`，ReAct 上限 10 步；
- 搜索使用 Serper API 和域名 denylist；
- Text2World/ByteSized32 refinement 次数为 2，CWMB 为 3；
- SFT 使用 LlamaFactory、Llama-3.1-8B、30K max sequence length、5 epochs。

如果 AutoMarkov 改用 Qwen3.6、Tavily、CAMEL、RLlib 或不同 refinement policy，应逐项标为 `controlled-adaptation`，并保留原设置对照。Agent2World 文献/仓库中对 Text2World 任务规模存在 101 与 103 的表述差异；不能挑一个数字写死，必须以实际 benchmark manifest、任务 ID 列表和 hash 为准并解释过滤项。

Agent2World LICENSE 的限制高于普通开源许可。安全做法是：官方代码在仓库外固定 commit 检出、只读挂载、无外发；AutoMarkov 仓库不复制其源码、prompt、测试或衍生代码。任何 clean-room 实现都需先确认是否构成受限 derivative work。

### 14.3 Text2World

Text2World 研究把自然语言任务转换为 PDDL world model，并提供生成与评测脚本。仓库 README 的原始环境以 Python 3.8 和脚本化模型配置为主，与 AutoMarkov Python 3.11/3.12 栈不同。

因为代码与 HF 数据均无明确许可：

- 仅在外部隔离目录按 commit/revision 拉取；不复制进仓库或公开 artifact；
- 不把公开可下载等同于允许训练、改写或再分发；
- 先向权利人确认代码、数据、任务文本和派生模型的许可；
- 在此之前，只能保存任务 ID、不可逆 hash 和自行获得的聚合指标；
- Unified Planning 可作为独立的 PDDL parser/validator，但其输出不能自动洗掉输入数据许可。

用 Unified Planning、Tavily、Qwen3.6 重做 Text2World 流程是 `controlled-adaptation`，不是官方实现的 faithful reuse。

### 14.4 Agent²

Agent² 将过程分为 Generator Agent 与生成的 Target Agent，并包含 MDP 建模、算法优化和通过 Model Context Protocol 连接环境的设计；论文评测覆盖 MuJoCo、MetaDrive、MPE 与 SMAC 等任务。

当前没有可核验的官方代码 commit、包版本、prompt 或代码许可。因此可执行工作只能：

1. 从论文/附录建立逐条 requirement-to-implementation 映射；
2. 把论文未报告的 prompt、随机性、失败恢复、超参数和环境 glue 标为 unresolved；
3. 用 Gymnasium/PettingZoo/RLlib/CAMEL 实现 AutoMarkov 自己的 paper-spec 版本；
4. 单独报告与论文指标口径的差异，不使用“复现官方代码”措辞。

论文 CC BY 4.0 允许在署名下复用论文内容，但不授予尚未发布代码的许可。

### 14.5 A-LAMP

A-LAMP 论文将代理化 RL 流程分成 MDP modeling、coding、training/policy generation，并通过多角色反馈与错误修正迭代。论文报告 GPT-4o A-LAMP、Gemma3-27B Light A-LAMP，并在离散任务中固定 DQN；任务包括 CartPole、MountainCar、无线通信、无人机配送和库存。

由于没有可核验官方代码，AutoMarkov 只能按论文规格重建。Qwen3.6 + CAMEL + RLlib 的版本是明确的 `controlled-adaptation`。A-LAMP 论文的 CC BY-ND 4.0 允许分享未改编论文材料并要求署名，但不应把论文文本/图表改写后当作 AutoMarkov 资产；自行实现的代码仍需独立声明来源与原创边界。

## 15. 运行 profile 与最小权限矩阵

| Profile | 主要组件 | 网络 | 写权限 | 明确禁止 |
|---|---|---|---|---|
| `inference` | `LocalLlmRuntime` adapter、vLLM、Qwen3.6 | 默认仅接受 versioned local runtime edge；首次取权重走受控下载 | 模型 cache 初始化、运行日志 | Tavily key、训练数据、sealed answers、工作区广泛文件读取 |
| `evidence-gateway` | `EvidenceGateway` adapter、Tavily SDK、atomic key lease/usage ledger | 仅 `api.tavily.com:443` 与 versioned authoring edge | evidence cache、lease/usage ledger、retrieval artifacts | LLM、training/sealed assets、把 secret/answer 回传 authoring |
| `authoring` | CAMEL、schema/ledger、六条 seam 的 versioned clients | 只连接 `LocalLlmRuntime`/`EvidenceGateway` 等冻结 protocol edges；无 provider egress | 当前 run 事件与候选 artifact | Tavily SDK/key/API egress、vLLM provider URL/credential、shell/code execution 默认工具、sealed-eval、模型权重写入 |
| `rllib-core` | PyTorch、Ray/RLlib、Gymnasium 1.2.2、PettingZoo、MPE2、MiniGrid、OpenSpiel、safetensors 0.8.0 | 默认断网 | run/checkpoint 专属目录 | Tavily key、受限 benchmark 源、其他 run 写权限、Taxi generation/training |
| `rllib-taxi-synthesis` | `rllib-core` 同锁 deny-layer、generated candidate | 断网 | Taxi run/checkpoint 专属目录 | Gymnasium Taxi 源码/bytecode/resource、wheel/sdist/cache、gold/sealed assets；preflight 必须签发 read/import denial attestation |
| `sealed-evaluator-rllib` | trusted preregistered RLModule/connector、safetensors 0.8.0、core verifier | 仅 mTLS sealed gold edge | episode/evaluation 专属目录 | 普通 RLlib checkpoint、pickle/cloudpickle、candidate Python code/object、authoring/Tavily capability |
| `sealed-env-taxi-gold` | Gymnasium 1.3.0、`Taxi-v4`、`RemoteEnv` worker | 强制断网；仅 mTLS sealed evaluator edge | episode 专属目录 | generation/training principal、Ray、Tavily key、答案输出、自动降级到 `Taxi-v3` |
| `driving` | MetaDrive、ScenarioNet、Panda3D | 预取后断网 | 转换 cache/episode 专属目录 | 原始数据再分发、隐式数据更新 |
| `smacv2` | SMACv2、PySC2、SC2 binary/maps | 断网 | SC2 temp/replay 专属目录 | 未锁 SC2/map、资产入库 |
| `citylearn` | CityLearn、锁定 dataset | 预取后断网 | run 专属目录 | 评测时自动下载/换 schema |
| `planning` | Unified Planning、allowlisted engines | 断网 | 有限临时目录与计划输出 | 任意 planner binary、无限 subprocess、继承敏感凭据 |
| `finetune` | LlamaFactory、PyTorch、SwanLab offline | 数据/模型预取后断网 | checkpoint 与离线日志 | vLLM/RL GPU 共享、sealed 测试数据、自动远端上报 |
| `sealed-eval` | 最小环境与评分器 | 强制断网 | 只写最终受控结果 | authoring memory、Tavily、训练写权限、答案回流 |

trainer-local policy export 是 trainer profile 内的一次性 `ProcessExecution`，不构成额外 profile：它在只读 checkpoint snapshot 上重验 training-record-bound tree commitment，输出 safetensors、manifest、commitment 与 terminal record后销毁。profile 间只传这些 immutable artifacts，不传 checkpoint、pickle/cloudpickle、共享虚拟环境或隐式 Python object。每次运行保存容器 digest 或 lockfile hash、系统库、CUDA/driver、GPU 型号、CPU、seed、时间、输入输出 hash 和调用链 ID。

## 16. 实施门禁与验收证据

### 16.1 上游引入门禁

每个依赖进入任何 lockfile 前必须保存：

- package 名、version、index URL、wheel/sdist SHA256；
- release tag 与 peeled commit，或无 release 时的完整 commit；
- SPDX/定制许可、NOTICE/CITATION、代码与数据许可的区分；
- Python/CUDA/系统资产兼容范围；
- 官方 API 契约与 AutoMarkov adapter contract test；
- 网络、凭据、文件系统、subprocess 和数据外发能力；
- 归属 `faithful`、`paper-spec` 或 `controlled-adaptation` 及理由。

### 16.2 运行时验收

- vLLM：`/health`、`/v1/models`、一次真实 chat completion，且模型 revision 与运行参数可追溯。
- Tavily：Search/Extract/Crawl 的成功、部分失败、401、429/预算错误与 timeout 契约测试；ledger credit 可对账。
- CAMEL：越权 tool call 被结构化拒绝；取消、budget、worker failure 不产生幽灵写入。
- Gymnasium/PettingZoo：官方 env/API checker；terminated/truncated、seed 和 multi-agent lifecycle 回归测试。
- RLlib：一个最小 rollout 与 learner update，checkpoint 恢复后结果结构一致。
- MetaDrive/ScenarioNet：固定场景 hash 的 headless reset/step/回放 canary。
- SMACv2：固定 SC2 build/map 的 `protoss_5_vs_5` reset、可用动作与 episode termination canary。
- CityLearn：固定 schema 的离线 reset/step 和 RLlib multi-agent wrapper canary。
- Unified Planning：已知可解、不可解、超时、语法错误四类 fixture；engine/version 可追溯。
- SwanLab：断网下日志完整，日志中无 token/秘密路径；未经显式动作不会上传。
- LlamaFactory：小数据 dry-run、checkpoint、恢复、导出、vLLM 加载各自留 manifest。

### 16.3 论文轨报告门禁

每个结果表至少分为：

1. `upstream-faithful`：严格使用官方代码/设置且许可允许；
2. `paper-spec`：无官方代码时的论文规格实现；
3. `automarkov-adaptation`：Qwen3.6/Tavily/CAMEL/RLlib 等替换版本；
4. `ablation`：逐项移除检索、反思、测试、训练或角色编排。

不同栏位不得合并平均，也不得用“复现成功”概括只通过 smoke test 的状态。许可不明、资产缺失、只完成接口适配或未做完整评测时，应分别写 `license-blocked`、`asset-blocked`、`adapter-verified` 或 `runtime-only`。

## 17. 当前阻塞与建议下一步

### 必须先解决

1. 向 Text2World 权利人确认代码、HF 数据及派生物许可；在此之前禁止 vendoring 和公开训练。
2. 对 Agent2World 定制许可做法律/项目治理审查；禁止复制其源码和衍生代码进入 AutoMarkov。
3. 选择并锁定 GPU 驱动/CUDA/PyTorch/vLLM 的实际兼容矩阵；PyPI 最新版本不是兼容性证明。
4. 获取合法的 SC2 binary/maps 与驾驶数据访问权，并保存资产版本/hash。
5. 为 Agent²/A-LAMP 建立论文规格缺口清单；在官方代码出现前维持 `paper-spec` 状态。

### 建议首个 tracer-bullet

先在完全无受限资产且具备 Taxi module/resource/cache read-deny attestation 的 `rllib-taxi-synthesis` profile 完成 generated Taxi candidate 的 generic Gymnasium API contract；MiniGrid Memory 与 MPE2 Simple Spread adapter contract test 使用通用 `rllib-core`，随后接 RLlib 新 API 栈最小训练。official Taxi-v4 只在 `sealed-env-taxi-gold` profile 中供 sealed evaluator 使用；普通 checkpoint 必须由 trainer-local 一次性 export execution 在同一 frozen trainer profile/namespace 内转成 manifest-bound weights-only safetensors，跨 profile 只传该 tensor artifact、manifest、commitment 与 terminal record。与此同时只做 vLLM 和 Tavily 的隔离 canary。通过这些证据后，再分别开启 driving、SMACv2、CityLearn、planning 与 finetune profile；不要用一个大环境提前耦合所有依赖。

## 18. 一手来源索引

### 模型、推理与代理

- [vLLM 0.26.0 release](https://github.com/vllm-project/vllm/releases/tag/v0.26.0)
- [vLLM serve CLI](https://docs.vllm.ai/en/latest/cli/serve/)
- [Qwen3.6 官方仓库](https://github.com/QwenLM/Qwen3.6)
- [Qwen3.6-35B-A3B 官方模型卡与固定 revision](https://huggingface.co/Qwen/Qwen3.6-35B-A3B/tree/995ad96eacd98c81ed38be0c5b274b04031597b0)
- [CAMEL 文档](https://docs.camel-ai.org/get_started/introduction)
- [CAMEL Societies](https://docs.camel-ai.org/key_modules/societies)
- [CAMEL Workforce](https://docs.camel-ai.org/key_modules/workforce)

### 检索

- [Tavily Search API](https://docs.tavily.com/documentation/api-reference/endpoint/search)
- [Tavily Extract API](https://docs.tavily.com/documentation/api-reference/endpoint/extract)
- [Tavily Crawl API](https://docs.tavily.com/documentation/api-reference/endpoint/crawl)
- [Tavily rate limits](https://docs.tavily.com/documentation/rate-limits)
- [Tavily Python SDK 固定源码快照](https://github.com/tavily-ai/tavily-python/tree/de924695765d5cf28bd1975c1cfca0cd07cd7005)

### RL、MARL 与环境

- [RLlib new API stack migration](https://docs.ray.io/en/latest/rllib/new-api-stack-migration-guide.html)
- [RLlib multi-agent environments](https://docs.ray.io/en/latest/rllib/multi-agent-envs.html)
- [Gymnasium Env API](https://gymnasium.farama.org/api/env/)
- [PettingZoo AEC API](https://pettingzoo.farama.org/api/aec/)
- [MPE2 Simple Spread](https://mpe2.farama.org/main/environments/simple_spread/)
- [MiniGrid Memory](https://minigrid.farama.org/environments/minigrid/MemoryEnv/)
- [OpenSpiel 官方仓库](https://github.com/google-deepmind/open_spiel)
- [MetaDrive 官方仓库](https://github.com/metadriverse/metadrive)
- [ScenarioNet 官方仓库](https://github.com/metadriverse/scenarionet)
- [SMACv2 官方仓库](https://github.com/oxwhirl/smacv2)
- [SMACv2 论文](https://arxiv.org/abs/2212.07489)
- [CityLearn 官方仓库](https://github.com/citylearn-project/CityLearn)
- [CityLearn API](https://www.citylearn.net/api/citylearn.citylearn.html)

### 规划、追踪与训练

- [Unified Planning 官方仓库](https://github.com/aiplan4eu/unified-planning)
- [Unified Planning engine selection](https://unified-planning.readthedocs.io/en/stable/engines/02_engine_selection.html)
- [SwanLab Python API](https://docs.swanlab.cn/api/py-init.html)
- [SwanLab Offline](https://docs.swanlab.cn/api/cli-swanlab-offline.html)
- [LlamaFactory 0.9.5 release](https://github.com/hiyouga/LLaMA-Factory/releases/tag/v0.9.5)
- [LlamaFactory 文档](https://llamafactory.readthedocs.io/)

### 论文复现轨

- [Agent2World 项目页](https://agent2world.github.io/)
- [Agent2World 论文](https://arxiv.org/abs/2512.22336)
- [Agent2World 固定源码快照](https://github.com/DeepExperience/agent2world/tree/1330f3cde9509f05d204a255f0f7f43208515dce)
- [Text2World 论文](https://arxiv.org/abs/2502.13092)
- [Text2World 固定源码快照](https://github.com/Aaron617/text2world/tree/9440ff7732fca4bcc8d9fb59a435886735f4059a)
- [Agent² 论文](https://arxiv.org/abs/2509.13368)
- [Agent² OpenReview](https://openreview.net/forum?id=nwXCmnZ35w)
- [A-LAMP 论文](https://arxiv.org/abs/2512.11270)
- [A-LAMP OpenReview](https://openreview.net/forum?id=oQdo7H38dC)
