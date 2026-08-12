# T05 本地 Qwen3.6 / vLLM 鉴权与身份边界核验

> 核验时刻：2026-08-12（UTC）
> 任务范围：GitHub Issue T05 的 authenticated local Qwen runtime、OpenAI-compatible probe schema、model/tokenizer/chat-template identity 与版本边界。
> 证据范围：仅使用 vLLM 官方文档和固定源码、Qwen 官方 Hugging Face 仓库和官方 GitHub 仓库。本文没有访问用户提供的 relay，没有读取 `.env`，没有启动、停止、升级或调用任何 vLLM 服务。

## 1. 结论

1. vLLM `0.25.1` 的 `--api-key` middleware 只保护路径前缀 `/v1`、`/v2` 和 `/inference`；`OPTIONS` 以及 `/health` 等其他路径明确跳过鉴权。缺失或错误的 Bearer token 对受保护路径返回 HTTP `401`，不是 `403`。[v0.25.1 authentication middleware](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/entrypoints/serve/utils/server_utils.py#L42-L93)
2. 因此 `GET /health == 200` 只能作为 liveness；它不能证明 API key 生效、served model 正确或真实生成可用。T05 至少需要分别保存：unauthenticated health、missing/bad credential 被拒绝、authenticated `/v1/models`、authenticated non-streaming `/v1/chat/completions`，以及独立的 runtime identity attestation。[v0.25.1 security guidance](https://docs.vllm.ai/en/v0.25.1/usage/security/#api-key-authentication-limitations)
3. `/v1/models` 返回的是 served alias、model path、`max_model_len` 和 permission 等服务表面信息；`served_model_name` 本身只是 API alias。它不返回 model revision、tokenizer revision、权重 shard hash、chat-template hash、wheel hash或源码 commit，所以不能单独证明后端是批准的 snapshot。[v0.25.1 model-list implementation](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/entrypoints/openai/models/serving.py#L64-L77) [v0.25.1 model configuration](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/config/model.py#L261-L268)
4. Qwen 官方对 `Qwen/Qwen3.6-35B-A3B` 的 vLLM 路线是 `--reasoning-parser qwen3`；工具调用再加 `--enable-auto-tool-choice --tool-call-parser qwen3_coder`。官方最低建议是 `vllm>=0.19.0`，所以冻结的 `0.25.1+cu129` 满足版本下限，但仍须以现场 attestation 和 canary 证明该 build 的行为。[Qwen3.6 official model card](https://huggingface.co/Qwen/Qwen3.6-35B-A3B#vllm)
5. 2026-08-12 的 `observed_latest` 是 vLLM `0.27.1`，于 2026-08-11 发布；它只是一项上游观测，不得替换、补强或反推本项目冻结 attach build `0.25.1+cu129` 的行为。[vLLM v0.27.1 release](https://github.com/vllm-project/vllm/releases/tag/v0.27.1) [vLLM 0.27.1 PyPI](https://pypi.org/project/vllm/0.27.1/)

## 2. `--api-key` 的精确保护语义

### 2.1 middleware 如何启用

vLLM `0.25.1` 只有在 `args.api_key` 或 `VLLM_API_KEY` 至少提供一个非空 token 时才安装 `AuthenticationMiddleware`；CLI `--api-key` 优先于环境变量。没有配置 token 时，请求即使携带任意 `Authorization` header 也不能证明“已认证”，因为 middleware 根本没有启用。[v0.25.1 app assembly](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/entrypoints/openai/api_server.py#L251-L255)

middleware 的行为是：

- `OPTIONS` 请求跳过鉴权；
- 对 path 执行字符串前缀判断，只有以 `/v1`、`/v2` 或 `/inference` 开头的路径进入 token 检查；
- header scheme 大小写不敏感，但必须是 `Bearer`；
- token 先做 SHA-256，再用 constant-time comparison 与配置 token 比较；
- missing、wrong scheme 或错误 token 均返回 `401`，body 为 `{"error":"Unauthorized"}`。[v0.25.1 authentication middleware](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/entrypoints/serve/utils/server_utils.py#L45-L93)

T05 不能只做“带 credential 成功”正例。若服务没有启用 API key，同一个正例也会成功。最小鉴权证明必须同时满足：

| Probe | 预期 | 能证明什么 |
|---|---:|---|
| `GET /health`，不带 credential | `200` | endpoint liveness；不证明 authentication |
| `GET /v1/models`，不带 credential | `401` | `/v1` middleware 已启用 |
| `GET /v1/models`，错误 credential | `401` | 任意 Bearer token 不能通过 |
| `GET /v1/models`，正确 credential | `200` + schema valid | credential 可访问 model-list route |
| `POST /v1/chat/completions`，正确 credential | `200` + schema-valid generated response | 同一 credential 能完成真实生成 |

上表是依据固定源码得出的 AutoMarkov admission rule；不是“收到一个 200 即 ready”的上游声明。

### 2.2 `/health` 的能力上限

固定版本的 `/health` route 在 generation engine 存在时调用 `engine_client.check_health()`，健康返回 `200`，捕获 `EngineDeadError` 返回 `503`；render-only server 没有 engine client 时也直接返回 `200`。所以 `/health` 甚至不能在所有 server mode 下证明 generation engine 存在。[v0.25.1 health route](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/entrypoints/serve/instrumentator/health.py#L22-L33)

`/health` 位于 guarded prefixes 之外。vLLM 官方安全文档也明确把它、`/version`、`/tokenize`、`/detokenize` 和 `/load` 列为 API key 未保护的 utility endpoints。[v0.25.1 security guidance](https://docs.vllm.ai/en/v0.25.1/usage/security/#unprotected-endpoints-no-api-key-required)

此外，官方文档列出了若干不受 API key 保护的 inference 或 operational routes，包括 `/invocations`、`/generative_scoring`、`/pause` 和 `/update_weights` 等，并建议在反向代理处显式 allowlist 必需 endpoints。因而 `--api-key` 不是完整 service perimeter；AutoMarkov 的 loopback/relay ingress 仍必须只暴露所需 route。[v0.25.1 security implications](https://docs.vllm.ai/en/v0.25.1/usage/security/#security-implications) [v0.25.1 reverse-proxy guidance](https://docs.vllm.ai/en/v0.25.1/usage/security/#deploy-behind-a-reverse-proxy)

## 3. OpenAI-compatible probe schema

### 3.1 `GET /v1/models`

固定版本在 `/v1/models` 返回序列化的 `ModelList`。[route](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/entrypoints/openai/models/api_router.py#L20-L25) 对应 closed minimum 应核验：

- top-level `object == "list"`；
- `data` 是 `ModelCard[]`；
- 每张 card 至少含 `id`、`object == "model"`、`created`、`owned_by`，并可含 `root`、`parent`、`max_model_len` 和 `permission`。[v0.25.1 ModelCard/ModelList schema](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/entrypoints/openai/engine/protocol.py#L71-L100)

`OpenAIModelRegistry` 用配置的 base model name 填 `id`，用 `base_model.model_path` 填 `root`，用 runtime config 填 `max_model_len`。[v0.25.1 model-list implementation](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/entrypoints/openai/models/serving.py#L64-L77) `--served-model-name` 可让多个 alias 指向同一模型，响应 `model` 使用 alias 列表第一个名称；不配置时才回落到 `--model`。[v0.25.1 served-model-name semantics](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/config/model.py#L261-L268)

因此 admission check 应要求精确且唯一的批准 alias，但仍不得把 alias 当作 snapshot identity。

### 3.2 `POST /v1/chat/completions`

固定版本的 request schema 至少以 `messages` 和可选 `model` 为核心；response 的 top-level `object` 固定为 `chat.completion`，并包含 `id`、`created`、`model`、`choices` 和 `usage`。[v0.25.1 ChatCompletionRequest](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/entrypoints/openai/chat_completion/protocol.py#L196-L208) [v0.25.1 ChatCompletionResponse](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/entrypoints/openai/chat_completion/protocol.py#L95-L137)

T05 readiness canary 应使用 non-streaming request，至少核验：

- HTTP `200` 和 JSON media type；
- `object == "chat.completion"`；
- `model` 精确等于批准 served alias；
- `choices` 非空，首项包含 `message` 与 `finish_reason`；
- `message.role` 和非空 final `content` 符合固定 canary contract；
- `usage.prompt_tokens`、`usage.completion_tokens`、`usage.total_tokens` 为非负且关系自洽；
- 保存 request/response 的 redacted bytes hash，不保存 credential，不把 reasoning text写入普通 trace。

这些是基于上游 schema制定的 AutoMarkov验证条件。它们证明请求确实完成，但仍不证明 weight/tokenizer/chat-template 的离线文件身份。

### 3.3 请求前 token budget

completion response 的 `usage.prompt_tokens` 只能在请求消耗资源之后发现超限，不能作为 admission gate。vLLM `0.25.1` 的 `/tokenize` chat request 与 completion 共用 `online_renderer.preprocess_chat`，response 提供 `count`、`max_model_len` 和 token IDs；因此 T05 在读取 completion credential、获取并发槽和发送生成请求前，经冻结的 loopback control edge调用 `/tokenize`，并要求 `count <= max_prompt_tokens` 且 `count + requested max_tokens <= max_model_len`。生成完成后还要精确比较 `usage.prompt_tokens == count`，否则标记 tokenizer drift。[v0.25.1 tokenize protocol](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/entrypoints/serve/tokenize/protocol.py) [v0.25.1 tokenize serving](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/entrypoints/serve/tokenize/serving.py#L57-L126)

`/tokenize` 是 API-key 未保护的 utility route，所以只允许从冻结的 loopback execution edge访问，不得经外部 relay 暴露。token IDs 仅在有界响应内用于核验 count，不进入普通 trace；普通 trace只记录 prompt immutable artifact hash、token 数与冻结 tokenizer/template identity。

## 4. 可以取得的 runtime identity 证据

### 4.1 HTTP 表面证据

| Surface | 固定版本能返回 | 不能单独证明 |
|---|---|---|
| `/health` | engine health 的 `200/503` | API key、model identity、generation correctness |
| `/version` | `{"version": VLLM_VERSION}` | local build/wheel hash、source commit、CUDA/Torch identity |
| `/v1/models` | alias、path、max length、permission | model/tokenizer revision、weights/template hash |
| `/tokenizer_info` | tokenizer config、tokenizer class、effective chat template | wheel/source identity、weights identity；且 route 不受 API key 保护 |
| `/v1/chat/completions` | generated response、served alias、usage | backend files 的 content identity |

`/version` 的实现只返回版本字符串。[v0.25.1 version route](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/entrypoints/serve/instrumentator/basic.py#L53-L56) `/tokenizer_info` 只有显式设置 `--enable-tokenizer-info-endpoint` 才注册；其 response 从实际 tokenizer object 的 `init_kwargs` 构造，并在 server 采用 chat template 时附带 `chat_template`。[v0.25.1 tokenizer-info route](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/entrypoints/serve/tokenize/api_router.py#L94-L109) [v0.25.1 tokenizer-info implementation](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/entrypoints/serve/tokenize/serving.py#L154-L184)

官方安全文档警告 `/tokenizer_info` 会暴露 chat template/tokenizer configuration，且它不在 API-key guarded prefix 下。[v0.25.1 tokenizer-info warning](https://docs.vllm.ai/en/v0.25.1/usage/security/#unprotected-endpoints-no-api-key-required) 所以 T05 不应为普通 runtime client 开启它。attach attestation 应优先在目标 host 通过受控、只读、非敏感的 process/package/file evidence 取得 effective template，再只持久化 hash。

### 4.2 完整 attach manifest 必须补足的 host-side 证据

HTTP probes 之外，immutable runtime manifest 至少应冻结：

- listener identity、process PID/start time/executable、完整 redacted argv；
- vLLM distribution version、installed-file or environment-tree hash、PyTorch/CUDA/driver identity；
- `--model`、`--revision`、`--tokenizer`、`--tokenizer-revision`、`--chat-template`/effective template、`--served-model-name`；
- reasoning/tool parser、thinking defaults、max model length、language-model-only 等行为相关参数；
- local snapshot 中 config、tokenizer、template、index 与所有 26 个 weight shards 的实际 SHA-256；
- 独立 probe evidence artifact 的 status/schema/redacted request-response hashes；该工件反向绑定 manifest 与 host attestation，不能循环写回 pre-probe manifest；
- endpoint/relay identity 与观测时间，但不保存 credential value、Authorization header、credential path 或 secret file content。

这是从上游接口“哪些字段可见/不可见”推导出的 AutoMarkov evidence contract。任何缺项都保持 `WAITING_RUNTIME`，而不是凭版本字符串或 `/v1/models` alias 提升为 ready。

## 5. Qwen3.6 固定模型、tokenizer 与 template

本项目已选定的官方 Hugging Face revision 是：

```text
Qwen/Qwen3.6-35B-A3B
995ad96eacd98c81ed38be0c5b274b04031597b0
```

该 revision 可在官方 repository tree 直接解析。[fixed Qwen revision](https://huggingface.co/Qwen/Qwen3.6-35B-A3B/tree/995ad96eacd98c81ed38be0c5b274b04031597b0) 2026-08-12 对官方固定 bytes 计算得到：

| 文件 | SHA-256 | 作用 |
|---|---|---|
| `config.json` | `93a4693fa9d8392fbfccd4b3c9873f4bfdcb14fdede978b123d07d19675efe99` | model architecture/config |
| `model.safetensors.index.json` | `41b9356101ebf8e7519e150dc811f80c4226e727301fbb032b890f006ed0be83` | 26-shard weight index |
| `tokenizer.json` | `5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42` | tokenizer model |
| `tokenizer_config.json` | `5186f0defcd7f232382c7f0aebcd2252d073bb921ab240e407b7ae8745d2b29b` | tokenizer configuration |
| `chat_template.jinja` | `e84f32a23fdda27689f868aa4a1a5621f41133e51a48d7f3efcbea2839574259` | standalone chat template |
| `generation_config.json` | `e70c136c1b78ddc1fb0905bac8e733a4dc448d4f852a5dd75143fffc70be550e` | upstream generation defaults |
| `preprocessor_config.json` | `27225450ac9c6529872ee1924fcb0962ff5634834f817040f444118116f4e516` | processor configuration |

这些 SHA-256 是本次对官方固定 revision 原始 bytes 的计算结果，不是 Qwen 发布的签名 checksum；runtime 仍要对本地 snapshot 独立重算。官方 repository API 给出每个 safetensors shard 的 LFS object SHA-256 和 size，可作为下载 provenance 与本地逐 shard核验基准。[official fixed-revision tree API](https://huggingface.co/api/models/Qwen/Qwen3.6-35B-A3B/tree/995ad96eacd98c81ed38be0c5b274b04031597b0?recursive=true&expand=true)

2026-08-12 又以 fixed tree API 与独立 paths-info API 逐项交叉核验 26 个 `{path, lfs.oid, size}`，两者完全一致；index 精确引用 `model-00001-of-00026.safetensors` 至 `model-00026-of-00026.safetensors`，总大小 `71,903,776,776` bytes。代码中的 `OFFICIAL_QWEN_WEIGHT_SHARD_HASHES` 固定这 26 个 LFS content SHA-256，manifest 对任何单 shard 变更均 fail closed；不能用任意格式正确的 digest 替代。

固定 `config.json` 的 architecture 是 `Qwen3_5MoeForConditionalGeneration`，model type 是 `qwen3_5_moe`，native max positions 是 `262144`。这是 Qwen3.6 官方 artifact 的真实标识，不能因 architecture 名称包含 `3_5` 而误判为错误模型。[fixed config.json](https://huggingface.co/Qwen/Qwen3.6-35B-A3B/blob/995ad96eacd98c81ed38be0c5b274b04031597b0/config.json)

vLLM 的 `--revision` 和 `--tokenizer-revision` 是不同字段；tokenizer 未显式指定时才默认跟随 model，tokenizer revision 未显式指定时才跟随 model revision。[v0.25.1 model/tokenizer identity fields](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/config/model.py#L110-L187) [v0.25.1 defaulting logic](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/config/model.py#L502-L512) 因此 clean build 应显式锁定二者；attach mode 则必须从实际 argv/config 和本地 bytes 证明二者。

## 6. Qwen3.6 reasoning、thinking 与 tool parser

Qwen 官方 model card给出的 vLLM command 是：

```text
vllm serve Qwen/Qwen3.6-35B-A3B \
  --reasoning-parser qwen3

# tool calling additionally
--enable-auto-tool-choice \
--tool-call-parser qwen3_coder
```

官方同时给出 text-only 节省显存路线 `--language-model-only`，但是否采用仍是 runtime identity 的组成部分，不能由客户端猜测。[Qwen3.6 vLLM commands](https://huggingface.co/Qwen/Qwen3.6-35B-A3B#vllm)

vLLM `0.25.1` 固定源码注册了 `qwen3` reasoning parser，也把 `qwen3_coder` 和 `qwen3_xml` 注册到同一个 `Qwen3EngineToolParser`。[v0.25.1 reasoning registry](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/reasoning/__init__.py#L115-L137) [v0.25.1 tool-parser registry](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/tool_parsers/__init__.py#L157-L164) T05 应采用 Qwen model card明确指定的名称 `qwen3_coder`，而不是仅凭 alias 等价性另选名称。

Qwen3.6 默认 thinking，不支持 `/think`、`/nothink` 软切换。non-thinking request 应使用：

```json
{"chat_template_kwargs":{"enable_thinking":false}}
```

历史 thinking preservation 则使用 `preserve_thinking: true`。[Qwen official thinking controls](https://huggingface.co/Qwen/Qwen3.6-35B-A3B#instruct-or-non-thinking-mode) 固定 chat template本身定义了 `<think>...</think>` 与 XML tool-call rendering；vLLM `0.25.1` 的 Qwen3 parser也读取 `chat_template_kwargs.enable_thinking`，缺省为 `true`。[fixed Qwen chat template](https://huggingface.co/Qwen/Qwen3.6-35B-A3B/blob/995ad96eacd98c81ed38be0c5b274b04031597b0/chat_template.jinja) [v0.25.1 Qwen3 parser](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/parser/qwen3.py#L192-L232)

`enable_in_reasoning=false` 是 vLLM `StructuredOutputsConfig` 的默认值，其官方源码说明是“whether to use structured input for reasoning”；Qwen3.6 model card 并没有把它列为该模型必需的 parser flag。[v0.25.1 StructuredOutputsConfig](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/config/structured_outputs.py#L35-L42) 所以 T05 可以冻结并验证实际值为 `false`，但不能将其误述为 Qwen 官方模型要求。

基本 readiness 只要求一个 deterministic non-streaming completion；若要声称 reasoning/tool-parser behavior也已验证，则必须另外保存 thinking、non-thinking 和 forced tool-call canary。parser 名称存在于 argv或源码只证明配置可选，不证明现场输出解析正确。

## 7. `observed_latest` 与冻结 attach build

| 身份 | 官方证据 | AutoMarkov 含义 |
|---|---|---|
| `vllm 0.27.1` | 2026-08-11 官方 release；commit `6e448d0ea9bf3d88d898b65449ca6dc2aec170ac` | `observed_latest`；只进入 upgrade research，不进入 T05 attach identity |
| `vllm 0.25.1` | 2026-07-14 官方 release；source commit `752a3a504485790a2e8491cacbb35c137339ad34` | 冻结 source lineage |
| `vllm 0.25.1+cu129` | 官方 cu129 wheel index列出的 platform build | 目标 attach package identity；仍需目标 host 实际 wheel/environment attestation |

来源：[v0.27.1 release](https://github.com/vllm-project/vllm/releases/tag/v0.27.1) [v0.25.1 release](https://github.com/vllm-project/vllm/releases/tag/v0.25.1) [official v0.25.1 cu129 wheel metadata](https://wheels.vllm.ai/0.25.1/cu129/vllm/metadata.json)

官方 x86_64 cu129 wheel filename 是 `vllm-0.25.1+cu129-cp38-abi3-manylinux_2_28_x86_64.whl`，官方 artifact bytes SHA-256 为 `9e206f370c934a2d4b6b1f05d3d09708d344e05d80260189ef19f60755709431`。[official v0.25.1 cu129 wheel](https://github.com/vllm-project/vllm/releases/download/v0.25.1/vllm-0.25.1%2Bcu129-cp38-abi3-manylinux_2_28_x86_64.whl)

版本字符串 `0.25.1+cu129`、wheel hash、source commit、installed environment tree、Torch/CUDA identity 和实际 serve argv是不同层次的证据。只有目标 host的实际安装与官方 artifact hash匹配时，才能声明 exact wheel reuse；否则只能记录 observed local build identity，不能套用官方 wheel SHA。

## 8. T05 推荐的 fail-closed gate

```text
DISCOVERED
  -> HEALTHY                 /health == 200
  -> AUTH_ENFORCED           missing/bad credential rejected on /v1
  -> AUTHENTICATED_MODELS    exact approved alias, schema valid
  -> COMPLETION_PROVEN       real non-streaming canary, schema valid
  -> IDENTITY_ATTESTED       process/package/model/tokenizer/template/weights bound
  -> RuntimeReady
```

任一步失败或 identity 缺失都保持 `WAITING_RUNTIME`。`/health`、`/v1/models`、版本字符串、文件存在和 completion 各自只证明自己的窄事实，不能跨层替代。

### 8.1 v3 工件图与当前连接身份

T05 的实现把 pre-probe 静态身份与运行中连接身份分开：runtime manifest v3 绑定本地 model/tokenizer checkpoint path、固定 revision、26 个 shard hash、chat template、non-thinking policy 与 route allowlist identity；host attestation v3 以 typed references 绑定 manifest、process、package 和 model snapshot 四类 immutable artifact。探针成功后另写 runtime probe evidence v3，并以 direct-parent contract 精确绑定 manifest 与 host attestation。`llm_prompt.v3` 使用按 role 判别的封闭 message union，tool response 必须引用此前 assistant 声明且尚未消费的 tool-call ID；包含该 prompt 的 completion request 使用 v4。

route allowlist 的 canonical preimage 是 `{"authenticated_routes":["GET /v1/models","POST /v1/chat/completions"],"domain":"AutoMarkov-vLLM-Route-Policy-v1","local_control_routes":["GET /health","POST /tokenize"],"other_routes":"denied"}`，SHA-256 固定为 `b305ee7c32e0cff9c69911f3dffdb7af5e0351f39d0a4cc64930c683cd63c1dd`。该 hash 同时进入 manifest、process evidence 和 current-connection proof v2；只有特权 resolver 对现场 route perimeter 签发相同 identity 时才允许发送请求。vLLM 的裸 `--api-key` middleware 不能替代此证明。启动参数采用封闭 flag 集，并禁止任何未登记或重复 flag，避免 `--hf-token` 等凭据参数进入 immutable manifest。

每次 health、models、tokenize、canary 或 completion 请求都必须由 `CurrentRuntimeConnectionProvider` 原子返回“已验证的新鲜连接证据 + 同一条尚未发送请求的连接”。runtime 核对 challenge、请求 binding、listener/process identity 和 evidence hash 后才读取 credential，并只能在这条连接上发送一次请求。无法取得该能力时 fail closed；历史 host attestation 或普通 HTTP transport 不能替代当前连接证明。

生产 adapter 使用 `PrivilegedUnixRuntimeConnectionProvider`：它通过 owner/mode/`SO_PEERCRED` 均受限的 Unix `SOCK_SEQPACKET` control edge，请求 host resolver 在目标 network namespace 内建立 loopback TCP 连接；resolver 以 `SCM_RIGHTS` 原子返回该同一 fd 与 Ed25519 签名的短期 proof。客户端验证五秒内的新鲜时间、challenge、manifest/request binding、boot/netns、PID/start-ticks、listener/accepted socket、process/listener identity preimage及实际 fd 四元组后，才允许在该 fd 上发送一次请求并强制关闭。仓库交付 provider client，但不隐式部署或提权 host resolver；resolver socket 或 trusted host key 未配置时必须保持 `WAITING_RUNTIME`，不能回退普通 HTTP。现场 resolver 部署、ACL 和 host key provisioning 属于 operator runtime authority，不是“代码存在即 READY”。

真实 completion 成功路径继续写入 response artifact 与 trace artifact：response 精确绑定 prompt、manifest 和 probe evidence；trace 再绑定 response，并记录本次连接 evidence hash。任一 put/get、parent contract、payload hash 或 readback 失败都不得返回成功结果。

## 9. 一手来源清单

- [vLLM v0.25.1 security documentation](https://docs.vllm.ai/en/v0.25.1/usage/security/)
- [vLLM v0.25.1 authentication middleware](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/entrypoints/serve/utils/server_utils.py#L42-L93)
- [vLLM v0.25.1 API app assembly](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/entrypoints/openai/api_server.py)
- [vLLM v0.25.1 health/version routes](https://github.com/vllm-project/vllm/tree/752a3a504485790a2e8491cacbb35c137339ad34/vllm/entrypoints/serve/instrumentator)
- [vLLM v0.25.1 model list schema and implementation](https://github.com/vllm-project/vllm/tree/752a3a504485790a2e8491cacbb35c137339ad34/vllm/entrypoints/openai/models)
- [vLLM v0.25.1 chat completion schema](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/entrypoints/openai/chat_completion/protocol.py)
- [vLLM v0.25.1 model/tokenizer configuration](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/config/model.py)
- [vLLM v0.25.1 Qwen3 parser and registries](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/parser/qwen3.py)
- [vLLM v0.25.1 official cu129 wheel metadata](https://wheels.vllm.ai/0.25.1/cu129/vllm/metadata.json)
- [vLLM v0.25.1 release](https://github.com/vllm-project/vllm/releases/tag/v0.25.1)
- [vLLM v0.27.1 release](https://github.com/vllm-project/vllm/releases/tag/v0.27.1)
- [Qwen3.6-35B-A3B official model card](https://huggingface.co/Qwen/Qwen3.6-35B-A3B)
- [Qwen3.6-35B-A3B fixed revision](https://huggingface.co/Qwen/Qwen3.6-35B-A3B/tree/995ad96eacd98c81ed38be0c5b274b04031597b0)
- [Qwen3.6-35B-A3B fixed chat template](https://huggingface.co/Qwen/Qwen3.6-35B-A3B/blob/995ad96eacd98c81ed38be0c5b274b04031597b0/chat_template.jinja)
- [Qwen3.6 official repository](https://github.com/QwenLM/Qwen3.6)
