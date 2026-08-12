# T04 上游 BOM 与运行时 profile 隔离核验

> 核验时刻：2026-08-10T22:48:59Z（UTC）
> 范围：GitHub Issue #4 的 upstream provenance、依赖冲突、profile lock/image/SBOM 输入。
> 证据边界：只使用上游官方仓库、官方发布页、官方文档与 PyPI 发布元数据。本文没有安装这些依赖、构建镜像、下载模型/数据或启动实验。

## 1. 决策摘要

1. `rllib-core` 固定 `ray[rllib]==2.56.1`、`gymnasium==1.2.2` 与 `pettingzoo==1.26.1`。Ray 的 `rllib` extra 精确要求 Gymnasium 1.2.2；Gymnasium 最新 1.3.0 只能进入 `sealed-env-taxi-gold`，OpenSpiel 2.0.1 只能进入 `ood-openspiel`，二者均不得进入 `rllib-core`。
2. `env-citylearn`、`env-smacv2` 与 `env-metadrive` 必须是独立进程环境。CityLearn 与 Ray 的 Gymnasium 约束直接冲突；SMACv2 的 `protobuf<3.21` 与现代 vLLM/SwanLab 冲突；ScenarioNet/MetaDrive 要求 Python `<3.12`。OASIS 同样固定 Python `<3.12` 和旧版 CAMEL，但它只作为 deferred upstream provenance，不是 T04 的 active profile。
3. `authoring` 只安装基础 `camel-ai==0.2.90`，禁止 `camel-ai[all]`。其 `all`/`web-tools` extra 要求 `tavily-python<0.6`，与 `retrieval-tavily` 采用的 `tavily-python==0.7.27` 冲突；Tavily SDK 和凭据仅存在于 `retrieval-tavily`。
4. 2026-08-10 刚发布的 vLLM 最新版是 0.27.0。它是上游观测值，不能自动替换 `llm-qwen36-vllm` 已批准、待现场 attestation 的 `0.25.1+cu129` attach runtime。附加运行时必须记录本机 distribution/RECORD 或环境树 hash；PyPI 的标准 0.25.1 wheel hash不能冒充本地 `+cu129` build identity。
5. Agent2World 只能由默认禁用的 `replication-agent2world-restricted` 登记为仓库外受限资产。其许可证仅授权非商业研究评估，并禁止分发、再许可、销售、hosted service 及分发衍生作品；它不能进入可发布 lockfile、镜像 build context、wheel、源码树或发布物。

## 2. 版本、commit、artifact 与许可

“最新”表示核验时官方 PyPI/GitHub 显示的最新稳定发布或最新官方源码快照；“采用锁”才是 AutoMarkov profile 的构建输入。默认分支 HEAD 仅用于没有发布 tag 的源码快照。

| 组件 | 核验到的官方版本或快照 | tag / commit / tree | 选定发行物 SHA256 | 许可 | AutoMarkov 采用锁 |
|---|---|---|---|---|---|
| Ray / RLlib | [`ray==2.56.1`](https://pypi.org/project/ray/2.56.1/) | [`936f0d7d49d9da8ac1a9f04cc8a89faf2cb3c42a`](https://github.com/ray-project/ray/commit/936f0d7d49d9da8ac1a9f04cc8a89faf2cb3c42a) | CPython 3.11 manylinux2014 x86_64 wheel `e7003a47a42ef2ad33ec0b34dc5b6afb03f63fe59465e6f4c8f6d05492d9e4a6` | [Apache-2.0](https://github.com/ray-project/ray/blob/936f0d7d49d9da8ac1a9f04cc8a89faf2cb3c42a/LICENSE) | `rllib-core`: `ray[rllib]==2.56.1` |
| Gymnasium | 最新 [`1.3.0`](https://pypi.org/project/gymnasium/1.3.0/)，Ray 配套 [`1.2.2`](https://pypi.org/project/gymnasium/1.2.2/) | 1.3.0 [`53bf3e9a…`](https://github.com/Farama-Foundation/Gymnasium/commit/53bf3e9a884783eb72ad3fc8b15780914c97c3e1)；1.2.2 [`a923da5d…`](https://github.com/Farama-Foundation/Gymnasium/commit/a923da5d4415a1aa5195d99341069da5e16deed7) | 1.3.0 wheel `6b8c159a8540dcbcb221722d7efda24d78ebbcbc3bd2ea1c2611aa2a34471fc2`；1.2.2 wheel `f04ec362b1fdf73a8b327db5ef89384a3f2ba411e05d3521513414fbbb2199c8` | [MIT](https://github.com/Farama-Foundation/Gymnasium/blob/53bf3e9a884783eb72ad3fc8b15780914c97c3e1/LICENSE) | `rllib-core`: 1.2.2；`sealed-env-taxi-gold`: 1.3.0 |
| PettingZoo | [`1.26.1`](https://pypi.org/project/pettingzoo/1.26.1/) | [`1756a4d7494b532651f0024ff7087ef4945432a6`](https://github.com/Farama-Foundation/PettingZoo/commit/1756a4d7494b532651f0024ff7087ef4945432a6) | wheel `f4715dde696bf159d68bb1d3f764ff5083eb7e0cc32ac31a748e81ae530181dd` | [MIT](https://github.com/Farama-Foundation/PettingZoo/blob/1756a4d7494b532651f0024ff7087ef4945432a6/LICENSE) | `rllib-core`: 1.26.1 |
| OpenSpiel | [`open_spiel==2.0.1`](https://pypi.org/project/open-spiel/2.0.1/) | [`112b77704631fc2ce7ad8e4581f6ca09798ce15a`](https://github.com/google-deepmind/open_spiel/commit/112b77704631fc2ce7ad8e4581f6ca09798ce15a) | CPython 3.11 manylinux 2.27/2.28 x86_64 wheel `27c3c2b878d2d7ab0347ce75c8c2f5d2d7d831c37553b921432ca0d84189992b` | [Apache-2.0](https://github.com/google-deepmind/open_spiel/blob/112b77704631fc2ce7ad8e4581f6ca09798ce15a/LICENSE) | `ood-openspiel`: 2.0.1；明确排除于 `rllib-core` |
| CityLearn | [`2.5.0`](https://pypi.org/project/citylearn/2.5.0/) | [`29062af6d077409e1c37a3e53a6cac30fd4d02bc`](https://github.com/citylearn-project/CityLearn/commit/29062af6d077409e1c37a3e53a6cac30fd4d02bc) | wheel `e6f0ed39d0ce438bfc7ff9caa1b9c3e9d76594d1b6758a926696a7663ce3af5b` | [MIT](https://github.com/citylearn-project/CityLearn/blob/29062af6d077409e1c37a3e53a6cac30fd4d02bc/LICENSE) | `env-citylearn`: 2.5.0 |
| SMACv2 | 无 PyPI 发布；官方源码快照 | commit [`577ab5a2cff2391f8df582da5731ea9cd6adf3c6`](https://github.com/oxwhirl/smacv2/commit/577ab5a2cff2391f8df582da5731ea9cd6adf3c6)，tree `1c9fc98cc647000c6e062558d681963c0de109c5` | lock source 固定 exact Git URL+commit | [MIT](https://github.com/oxwhirl/smacv2/blob/577ab5a2cff2391f8df582da5731ea9cd6adf3c6/LICENSE) | `env-smacv2`: exact Git identity |
| ScenarioNet | 无 PyPI 发布；官方源码快照 | commit [`d4acdb5f5a844744fc85cb2dc3880d7d4a6eb170`](https://github.com/metadriverse/scenarionet/commit/d4acdb5f5a844744fc85cb2dc3880d7d4a6eb170)，tree `efc926d4a64b1a74c31e18bf1f1391333206e536` | lock source 固定 exact Git URL+commit | [Apache-2.0](https://github.com/metadriverse/scenarionet/blob/d4acdb5f5a844744fc85cb2dc3880d7d4a6eb170/LICENSE) | `env-metadrive` converter：exact Git identity |
| MetaDrive | [`metadrive-simulator==0.4.3`](https://pypi.org/project/metadrive-simulator/0.4.3/) | [`5bf8ea8909c4643a4099a250e6f5fb89c695d8b4`](https://github.com/metadriverse/metadrive/commit/5bf8ea8909c4643a4099a250e6f5fb89c695d8b4) | wheel `c6f8a42baac939a0af53a8e1cd9d48ec2bf0051917e7c19083e156a6506493d9` | [Apache-2.0](https://github.com/metadriverse/metadrive/blob/5bf8ea8909c4643a4099a250e6f5fb89c695d8b4/LICENSE.txt) | `env-metadrive`: 0.4.3；禁止错误包名 `metadrive` |
| CAMEL-AI | [`camel-ai==0.2.90`](https://pypi.org/project/camel-ai/0.2.90/) | annotated tag peeled commit [`deb286f36702ab15a2cb890c6e223a79e4ce4284`](https://github.com/camel-ai/camel/commit/deb286f36702ab15a2cb890c6e223a79e4ce4284) | wheel `9998c434779a1a847d9ccddce1c069f22fb9667b19ba06a2452c479882169082` | [Apache-2.0](https://github.com/camel-ai/camel/blob/deb286f36702ab15a2cb890c6e223a79e4ce4284/LICENSE) | `authoring`: base 0.2.90，不装 `all`/`web-tools` |
| OASIS | [`camel-oasis==0.2.5`](https://pypi.org/project/camel-oasis/0.2.5/) | [`e97a1d83761605a24a7dc91fa4d4e9defffa7e23`](https://github.com/camel-ai/oasis/commit/e97a1d83761605a24a7dc91fa4d4e9defffa7e23) | wheel `9ebd6ba8e331495ee56b25cc63982188b94125dde499e5e9c00398a1d47e606d` | [Apache-2.0](https://github.com/camel-ai/oasis/blob/e97a1d83761605a24a7dc91fa4d4e9defffa7e23/LICENSE) | provenance/deferred evidence；不进入 T04 active profile catalog |
| vLLM | 最新 [`0.27.0`](https://pypi.org/project/vllm/0.27.0/)；批准的 attach identity 是 `0.25.1+cu129` | 0.27.0 [`4bdc8a788d2e2ce9165d552b3d4d8b72604626bf`](https://github.com/vllm-project/vllm/commit/4bdc8a788d2e2ce9165d552b3d4d8b72604626bf)；0.25.1 [`752a3a504485790a2e8491cacbb35c137339ad34`](https://github.com/vllm-project/vllm/commit/752a3a504485790a2e8491cacbb35c137339ad34) | 0.27.0 x86_64 wheel `02d8265e71bab1cf50f93026211c9a75562a7f0a72b3a32ec5e0e8bcdd62ec75`；PyPI 0.25.1 x86_64 wheel `16fc7a28df1576eb6f7ca0455026551b8f9adb674c19c66059359ef3e964bd1e` | [Apache-2.0](https://github.com/vllm-project/vllm/blob/4bdc8a788d2e2ce9165d552b3d4d8b72604626bf/LICENSE) | `llm-qwen36-vllm`: 精确现场证明 0.25.1+cu129；0.27.0 仅登记为 upgrade candidate |
| LlamaFactory | [`llamafactory==0.9.5`](https://pypi.org/project/llamafactory/0.9.5/) | [`7af909522a951e3ad9f022ea6f88b6755257eaa5`](https://github.com/hiyouga/LLaMA-Factory/commit/7af909522a951e3ad9f022ea6f88b6755257eaa5) | wheel `10776e9b259798bf65f6c5343f6298f0302e92e9cd47472abe29eef69e286c6a` | [Apache-2.0](https://github.com/hiyouga/LLaMA-Factory/blob/7af909522a951e3ad9f022ea6f88b6755257eaa5/LICENSE) | provenance/deferred evidence；不进入 T04 active profile catalog |
| SwanLab | [`swanlab==0.9.4`](https://pypi.org/project/swanlab/0.9.4/) | [`f86de8a7e74fa6bb39d171cb4f856bb72fe3b786`](https://github.com/SwanHubX/SwanLab/commit/f86de8a7e74fa6bb39d171cb4f856bb72fe3b786) | wheel `f6fdc42f6ae7fd639f852a3f515804b6e48597f241df9c51fec55deeb4112b72` | [Apache-2.0](https://github.com/SwanHubX/SwanLab/blob/f86de8a7e74fa6bb39d171cb4f856bb72fe3b786/LICENSE) | provenance/deferred evidence；不进入 T04 active profile catalog |
| Tavily Python | [`tavily-python==0.7.27`](https://pypi.org/project/tavily-python/0.7.27/)；官方仓库无 release/tag | 独立源码快照 commit [`de924695765d5cf28bd1975c1cfca0cd07cd7005`](https://github.com/tavily-ai/tavily-python/commit/de924695765d5cf28bd1975c1cfca0cd07cd7005)，tree `2fb33ec2d81cb2f388ffa99036b3609618098919` | wheel `e5cb40cc852d108ced8a313379b7098108642eedfbd97f821296a5e1a483e9b9` | [MIT](https://github.com/tavily-ai/tavily-python/blob/de924695765d5cf28bd1975c1cfca0cd07cd7005/LICENSE) | `retrieval-tavily`: wheel 0.7.27；源码快照与 wheel identity 分开记录 |
| Agent2World | 无 package/release；受限官方快照 | commit [`1330f3cde9509f05d204a255f0f7f43208515dce`](https://github.com/DeepExperience/agent2world/commit/1330f3cde9509f05d204a255f0f7f43208515dce)，tree `e8ec115a835a8774ff3a2085e2251316f50041cc`，license blob `f4cd1452843c91b52a65573cd06d66192bc24aa7` | 不生成可分发 artifact | [Research / Evaluation Only](https://github.com/DeepExperience/agent2world/blob/1330f3cde9509f05d204a255f0f7f43208515dce/LICENSE) | `replication-agent2world-restricted`，默认禁用且不可发布 |

PyPI hash 只标识上表指定文件。不同 Python ABI、操作系统、CPU/GPU 架构或 CUDA build 必须选择对应文件并记录自己的 hash；不能复用 x86_64/CPython 3.11 的值。Git source 的重建身份是 lock 中的 exact URL+40-hex commit；GitHub 自动生成 archive 的压缩字节不作为长期身份。

### 2.1 固定许可文件 SHA256

下列 SHA256 由上表固定 commit 的官方许可文件原始字节计算，供 SBOM/license policy 复验；Gymnasium 1.2.2/1.3.0 与 vLLM 0.25.1/0.27.0 在所列 commit 上分别具有相同许可文件 hash。

MiniGrid 3.1.0 的 PyPI `METADATA` 错标 `MIT License` classifier，但 selected wheel 内 `dist-info/licenses/LICENSE` 与 fixed commit `90928729376741a41222a257911343b97103b548` 的 `LICENSE` 都是 Apache-2.0，原始字节 SHA256 均为 `6c2915ffe9ac7ad36b26a36d03c2297ccc42a3dd914c902b28bfd5ff08c21b7c`。profile license inventory 必须采用该固定源码/随 wheel 许可文件证据，禁止以后由 classifier 覆盖。

| 组件 | license file SHA256 |
|---|---|
| Ray | `cc68f9a408c8edf33c900f645846a7d8388a23e4b92a4a9fce7499c372b2acc0` |
| Gymnasium | `7dacaa9772e856aee6943b32ef663d3634d91d72ec7bbc74d136943673f91e18` |
| PettingZoo | `57569ca4221c4cbf9a035d1280d142550b7021722a70ffd79c318ae382689cc4` |
| OpenSpiel | `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` |
| CityLearn | `5a136b692e5288cfc83099df5f21d4dc6ebbb20303ceaf7116f231158c333ea3` |
| SMACv2 | `6debad0d199caa25baac65c7f963d507370dc360daba2ba043a36e08a7afc145` |
| ScenarioNet | `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4` |
| MetaDrive | `45f65910a340942a8bdcd995c3703fc0f7cba6e5ae195d488ba1ab65c60dec2b` |
| CAMEL-AI | `950deb34b1341a0ac95236fae92fe247c318c3a83a62c9ebacbe1882530ab1f6` |
| OASIS | `950deb34b1341a0ac95236fae92fe247c318c3a83a62c9ebacbe1882530ab1f6` |
| vLLM | `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4` |
| LlamaFactory | `50e6751797c50dedd75ef1b8a0d9e42f5f8472e9fbce91f34718e9f97b0c780a` |
| SwanLab | `298f13ad08641bf02c1ee83bace23b542b505a0ceeac225bfc57317249e284f8` |
| Tavily Python | `5487dae77c2e475439bd62828b6c5e4896e79f3f7bcc1dbec10efc59fc8bb77f` |
| Agent2World | `96608c951b4ebd3eba46a943d91d7fd08e445ec2bd15963d8e9ff49a6981b2a2` |

## 3. 已确认的依赖冲突

### 3.1 `rllib-core` 与 CityLearn

Ray 2.56.1 的官方 [`rllib` extra](https://github.com/ray-project/ray/blob/936f0d7d49d9da8ac1a9f04cc8a89faf2cb3c42a/python/setup.py#L325-L331)固定 `gymnasium==1.2.2`。CityLearn 2.5.0 的[发布元数据](https://pypi.org/project/citylearn/2.5.0/)要求：

- `gymnasium<=0.28.1`；
- `numpy<2.0.0`；
- `scikit-learn<=1.2.2`；
- `openstudio<=3.3.0`，并带 `doe-xstock`、`nrel-pysam` 等领域依赖。

两个 Gymnasium 集合没有交集，因此不存在一个正确的联合 lock。`env-citylearn` worker 固定自身 Gymnasium 0.28.1 与完整 transitive lock，通过 `RemoteEnv` 向 `rllib-core` 暴露 canonical spaces/reset/step frame；不得把 CityLearn Python 对象、pickle 或 site-packages 交给 trainer。

### 3.2 SMACv2 与现代服务/追踪栈

SMACv2 固定 commit 的 [`setup.py`](https://github.com/oxwhirl/smacv2/blob/577ab5a2cff2391f8df582da5731ea9cd6adf3c6/setup.py)要求 `pysc2>=3.0.0`、`protobuf<3.21` 和 `s2clientprotocol>=4.10.1.75800.0`。vLLM 0.27.0 的[发布元数据](https://pypi.org/project/vllm/0.27.0/)要求 `protobuf>=5.29.6` 并排除若干 6.x 版本；SwanLab 0.9.4 在 Linux 上要求 `protobuf>=6.31.1,<7` 且排除 6.31.0。SMACv2 与这两组约束均无交集。

`env-smacv2` 只运行环境 worker，不安装 vLLM、SwanLab 或 RLlib。StarCraft II binary、maps、PySC2 和 `s2clientprotocol` 必须分别记录版本、合法来源与 SHA256；代码的 MIT 许可不覆盖 StarCraft II 资产许可。

### 3.3 ScenarioNet / MetaDrive

ScenarioNet 当前 commit 的 [`setup.py`](https://github.com/metadriverse/scenarionet/blob/d4acdb5f5a844744fc85cb2dc3880d7d4a6eb170/setup.py)存在一个需要 fail-closed 处理的元数据差异：`python_requires` 写 `>=3.8`，模块安装时的 assert 实际要求 Python 3.6–3.11。它还要求 `metadrive-simulator>=0.4.1.2` 和 `geopandas<1.0`；其 `train` extra 固定过时的 `ray[rllib]==1.0.0`、`aiohttp==3.6.0` 与 `wandb==0.12.1`。

MetaDrive 0.4.3 的[发布元数据](https://pypi.org/project/metadrive-simulator/0.4.3/)要求 Python `<3.12`、`panda3d==1.10.13`、`panda3d-gltf==0.13`，并包含图形/原生依赖。`env-metadrive` 因此使用 Python 3.11、MetaDrive 0.4.3；ScenarioNet 固定 commit 的 converter 因 legacy Ray 约束再隔离且只输出 hashed scenario artifacts，禁止安装 ScenarioNet `train` extra。RLlib 训练仍在远端 `rllib-core`。

### 3.4 CAMEL、OASIS 与 Tavily

CAMEL 0.2.90 的[发布元数据](https://pypi.org/project/camel-ai/0.2.90/)要求 Python `>=3.10,<3.15`、`pydantic>=2.10.6,<=2.12.0` 和 `websockets>=13,<15.1`。其 `all`/`web-tools` extra 固定 `tavily-python>=0.5.0,<0.6`，与当前 Tavily 0.7.27 不兼容，并会引入大量未授权 provider、搜索、浏览器和代码执行能力。

OASIS 0.2.5 的固定 [`pyproject.toml`](https://github.com/camel-ai/oasis/blob/e97a1d83761605a24a7dc91fa4d4e9defffa7e23/pyproject.toml)要求 Python `>=3.10,<3.12`、`camel-ai==0.2.78`，并精确固定 pandas、igraph、Neo4j、sentence-transformers 等依赖。它不能与 `authoring` 的 CAMEL 0.2.90 共用环境。

因此，T04 active profiles 采用以下边界：

- `authoring` 只用基础 CAMEL 0.2.90，并通过 `LocalLlmRuntime`/`EvidenceGateway` 窄客户端工作；安装包中存在 provider client 不等于获得 provider credential 或 egress capability；
- `retrieval-tavily` 独占 Tavily 0.7.27、Tavily credential 与 `api.tavily.com:443` egress；
- OASIS 0.2.5 + CAMEL 0.2.78 + Python 3.11 的事实只保留为 deferred provenance。T04 不创建 OASIS profile；若未来批准引入，必须重新进入设计与 ticket 流程。

### 3.5 vLLM、LlamaFactory 与 SwanLab

vLLM 0.27.0 的[发布元数据](https://pypi.org/project/vllm/0.27.0/)固定 `torch==2.13.0`、要求 `transformers>=5.5.3`，并带大量 CUDA/native 依赖；这与已有 `0.25.1+cu129` 服务不是同一 runtime identity。版本、CUDA 后缀、wheel/build hash、Torch/CUDA/driver、模型 revision、tokenizer/chat-template hash和 serve argv必须共同参与 profile identity。

LlamaFactory 0.9.5 的[发布元数据](https://pypi.org/project/llamafactory/0.9.5/)要求 Python `>=3.11`、`torch>=2.4.0`、`transformers>=4.55.0,<=5.6.0`（排除 4.52.0 与 4.57.0）、`peft>=0.18.0,<=0.18.1` 和 `trl>=0.18.0,<=0.24.0`。它只作为 Agent2World SFT 延期项的 provenance；T04 不创建 finetune profile。未来若获批，完整 transitive lock、基础模型、tokenizer、dataset、训练 YAML 与 GPU/CUDA identity 缺一不可。

SwanLab 0.9.4 的[官方离线模式](https://docs.swanlab.cn/api/py-init.html)允许 `swanlab.init(mode="offline")`。该事实只保留为延期训练的 provenance，SwanLab 不进入 T04 的 17 个 active profiles。未来训练 profile 必须显式锁 SwanLab 与 protobuf、默认断网并写 run 专属日志；同步是独立发布动作。

## 4. 与已接受规格一致的 17 个 profile

| Profile | Python / 核心锁 | 只允许的职责 | 必须隔离或禁止 |
|---|---|---|---|
| `core` | Pydantic 2.12.0、cryptography 49.0.0、rfc8785 0.1.4 | artifact/state machine、signature/JCS 与 domain protocols | 无 Ray/vLLM/env package |
| `authoring` | Python 3.11；CAMEL 0.2.90 base | agent orchestration、schema client、六条公共 seam client | 无 Tavily SDK/key、无 provider egress、无 `camel-ai[all]`、无 simulator/Ray |
| `llm-qwen36-vllm` | 现场必须证明 `vllm==0.25.1+cu129` 与 Qwen3.6 revision | 本地 OpenAI-compatible inference | 无 Tavily key、训练数据、sealed asset；禁止静默升级 0.27.0 |
| `retrieval-tavily` | Python 3.11；Tavily 0.7.27 | Search/Extract/Crawl、key lease、usage ledger、evidence cache | 无 LLM/Ray/simulator/sealed asset；仅 Tavily API egress |
| `runner-control` | Python 3.11；cryptography 49.0.0、rfc8785 0.1.4、TLS 1.3 | mTLS profile graph、fixed-commit control、attestation/replay index | 无 LLM/Ray/env package |
| `rllib-core` | Python 3.11；Ray 2.56.1、Gymnasium 1.2.2、PettingZoo 1.26.1及规格冻结的 MPE2/MiniGrid/safetensors/PyTorch | RLlib new-stack sampling/training/evaluation、trainer-local checkpoint/export | 无 Gymnasium 1.3.0/OpenSpiel/CityLearn/SMACv2/ScenarioNet/MetaDrive/Tavily/vLLM |
| `rllib-taxi-synthesis` | 与 `rllib-core` 相同 lock/image 的 deny-layer | generated Taxi candidate 的训练 | 无 Gymnasium Taxi 源码/bytecode/resource/wheel/cache read/import capability |
| `sealed-env-taxi-gold` | Python 3.11；Gymnasium 1.3.0 | sealed Taxi-v4 environment worker | 无 Ray、authoring、generation/training principal、答案回流 |
| `sealed-evaluator-rllib` | 与 `rllib-core` wire-compatible pins、core verifier、safetensors 0.8.0 | 可信 RLModule + weights-only sealed evaluation | 无 checkpoint/pickle/cloudpickle/candidate code/authoring/Tavily |
| `env-minigrid` | 默认复用 `rllib-core` 精确 Farama lock | MiniGrid environment worker | 未经 contract 证据不得松 pin |
| `env-mpe2` | 默认复用 `rllib-core` 精确 Farama lock | MPE2 environment worker | 未经 contract 证据不得松 pin |
| `env-smacv2` | SMACv2 固定 commit、protobuf `<3.21`、锁定 PySC2/SC2 assets | StarCraft II environment worker | 无 vLLM/SwanLab/Ray；SC2 binary/maps 不入仓库/镜像 |
| `env-metadrive` | Python 3.11；MetaDrive 0.4.3；ScenarioNet converter 固定 commit且再隔离 | headless driving/scenario environment worker | 无 ScenarioNet `train` extra、无 Ray、无原始数据再分发 |
| `env-citylearn` | Python 3.11；CityLearn 2.5.0、Gymnasium 0.28.1、NumPy 1.x | CityLearn environment worker | 无 Ray/vLLM/Tavily；dataset/schema 禁止隐式升级 |
| `ood-openspiel` | OpenSpiel 2.0.1 / pyspiel | 有限博弈 analysis | 明确排除于 `rllib-core` |
| `ood-pddl` | Unified Planning 1.3.0 + allowlisted planner engines | PDDL I/O 与规划分析 | 无任意 planner binary/无限 subprocess |
| `replication-agent2world-restricted` | Python 3.10；Agent2World 外部 checkout 固定 commit | 默认禁用的非商业研究评估 | ignored、不可发布、SFT deferred、无 hosted service |

跨 profile 的持久化交接只用 schema-versioned、content-hashed immutable artifacts；在线环境交互只用 manifest 冻结的 `RemoteEnv` codec/protocol。任何 profile 都不得接收另一个 profile 的 Python 对象、editable checkout、virtualenv、pickle/cloudpickle 或普通 RLlib checkpoint。

## 5. Agent2World 许可门禁

固定 commit 的[许可证正文](https://github.com/DeepExperience/agent2world/blob/1330f3cde9509f05d204a255f0f7f43208515dce/LICENSE)只授权查看、下载和运行代码以进行非商业研究评估及论文复现，并明确禁止：

- 商业使用；
- 分发、再许可或销售源码及衍生作品；
- 作为 hosted service 提供；
- 删除或修改许可声明。

T04 manifest/CI 必须把它表达为拒绝规则，而非普通 SPDX dependency：

1. `restricted_sources` 仅登记 repository、commit、tree、license blob、用途和批准状态；不包含源码 locator、archive 或 vendored hash。
2. 任何可发布 profile 的 package/lock/SBOM/build context 中出现 `agent2world` distribution、module、源码路径或 commit archive，立即失败。
3. ignored external checkout 只允许在另行批准的非商业研究评估中只读挂载；其输出、prompt、测试和衍生代码不得进入 AutoMarkov 发布树。
4. 未来 SFT 仍需独立核验代码、数据、基础模型和输出权重许可，并取得必要书面许可；LlamaFactory 接口存在不构成执行授权。

## 6. T04 manifest 与 CI 可验证字段

每个可构建 profile 至少冻结：

- `profile_schema_version`、`profile_name`、`python_abi`、OS/architecture、glibc、CUDA/driver/hardware contract；
- 顶层 package 的规范名、精确 version、index URL、artifact filename、SHA256、release tag 与 peeled commit；
- source-only dependency 的 repository、full commit、Git tree 与 lock 中 exact Git source；
- 完整 transitive lock hash、lock 生成器/版本、package hashes 与允许的 index；
- closed image state：`recipe_frozen` 以先拒绝全部路径、再按 manifest allowlist 的 `.dockerignore` 关闭真实 build context，并绑定完整 build-context files/hash；`built` 绑定实际 OCI digest、platform/libc/OpenSSL/CA 与 build/import-smoke attestation ID/hash，现场服务和受限资源分别使用 `attached_unverified`、`restricted_disabled`；SBOM format/version/hash 与完整 transitive license inventory/hash；
- capabilities、egress allowlist、credential IDs、read/write mounts、protocol edges；
- import smoke、native library smoke、asset readiness 与 `RemoteEnv` handshake 的 contract/hash。

T04 的 recipe target 固定为 CPython profile patch version、`linux/amd64` 与 Debian bookworm glibc 2.36。uv lock 是 universal inventory；实际 artifact 先从 virtual root 计算 marker+requested-extra closure，再由官方 `packaging==26.3` tags 选择唯一 compatible wheel，只有无 compatible wheel 时回退 identity-matched sdist。inactive platform branch 保留 license/SBOM inventory，但使用 `NOASSERTION` 且不携带 artifact hash。active pip upstream checksum 与 selected wheel/sdist 精确相等；ScenarioNet/SMACv2 以 uv lock 的 exact Git URL+commit 为重建身份，自动生成的 GitHub archive bytes 不承担长期身份语义。

当前 Linux/amd64 target closure 恰有 5 个 selected sdist 与 2 个 Git source：`google-search-results==2.4.2`、`tinynumpy==1.2.1`、`progressbar==2.5`、`mpyq==0.2.5`、`s2protocol==5.0.16.97563.0`，以及 exact-commit ScenarioNet/SMACv2。七者没有声明可独立冻结的 PEP 517 build backend，[uv 0.11.16 的 legacy fallback](https://github.com/astral-sh/uv/blob/0.11.16/crates/uv-build-frontend/src/lib.rs#L49-L56)会请求浮动 `setuptools>=40.8.0`；因此 `--frozen` 单阶段同步不足以冻结构建工具链。四个相关 profile 采用 [uv 官方 no-build-isolation 双阶段方案](https://github.com/astral-sh/uv/blob/0.11.16/docs/concepts/projects/config.md#L350-L386)：将 [`setuptools==84.0.0`](https://pypi.org/pypi/setuptools/84.0.0/json) 纳入 direct lock、SBOM、license 与 profile identity，保留 exact build constraint，第一阶段排除该 profile 的完整 source set 并安装 locked backend，断言版本后第二阶段构建完整闭包。SBOM 使用 `BUILD_DEPENDENCY_OF` 关系把 backend 连接到每个 source package；verifier 对 source set、配置和 recipe 任一漂移 fail closed。

基础镜像使用 Docker Official Images 的 `slim-bookworm` linux/amd64 manifest digest：Python 3.10.18 为 `sha256:b4d66d07136c546f1765eae2bfcce9a64fa95f37c717c02bedd06d0476d1dbbd`，3.11.13 为 `sha256:cec9aa7aa96eea4fa036e9b82be1e6b325f2e3707f462d885868df51ec0a4b47`，3.12.11 为 `sha256:c00fc7b44d844b6da22861ec24af43968a5200eac4ec607b4725d585165d6b49`；OCI config history 均声明 bookworm 且 `PYTHON_VERSION` 与 profile 精确一致。包含 active Git source 的两个 recipe 另外固定 Debian snapshot `20250910T000000Z`、`git=1:2.39.5-0+deb12u2` 与 `ca-certificates=20230311+deb12u1`，禁止依赖宿主 Git 或浮动 apt index。

定向 CI 应执行以下 fail-closed 检查：

1. 所有 target-closure package 必须 `==` 精确版本且具备当前平台唯一 selected artifact hash；inactive universal-lock branch 必须为 `NOASSERTION` 且无 hash。VCS URL 必须使用 40 字节 commit并与 lock source 精确相等。
2. `rllib-core` 解析结果必须精确包含 Gymnasium 1.2.2，并排除 Gymnasium 1.3.0 与 OpenSpiel；OpenSpiel 只允许出现在 `ood-openspiel`。`env-citylearn` 不得包含 Ray；`env-smacv2` 不得包含 protobuf 3.21+；`env-metadrive` 不得包含 ScenarioNet `train` extra；`authoring` 不得包含 Tavily 或 CAMEL extras；OASIS、LlamaFactory 与 SwanLab 不得出现在任何 T04 active profile。
3. profile import smoke 只导入自身 allowlist，核心包 import smoke 必须证明 CityLearn、SMACv2、ScenarioNet、MetaDrive、OASIS、vLLM 和 Tavily 均不可见。
4. SBOM/license policy 必须拒绝 Agent2World 及未知/无许可组件；profiles 发布树除明确登记的 restricted declaration 与本地 cache/`.venv` 外均扫描路径和内容，未登记的 Agent2World source、payload 或 archive 在任意 profile 下都 fail closed。profile `.dockerignore` 的 deny-by-default build-context allowlist 保证被忽略的 cache/`.venv` 也不能进入镜像。SC2 和驾驶数据作为独立 asset manifest 检查，不能从代码许可推导数据许可。
5. attach runtime 只有在 `/health`、带凭据的 `/v1/models`、真实 completion、模型/tokenizer/chat-template identity 和环境树 hash 全部通过后才可标记 ready；“版本字符串匹配”不足以通过。

## 7. 仍需运行时解决的事实

- 0.27.0 在核验当天刚发布，尚未经过 AutoMarkov/Qwen3.6、安全鉴权与 GPU canary；它只进入 upgrade backlog，不进入当前 active profile。
- `0.25.1+cu129` 是本地 build identity。只有在目标 GPU 服务上读取非敏感 package/argv/model metadata并计算环境树 hash 后，才能生成最终 `runtime_profile_id`。
- SC2 binary/maps 与驾驶数据需要合法获取、版本/hash 和数据许可；源码 BOM不能替代资产 provenance。
- 2026-08-11 已对 15 个 buildable profile 完成 `uv sync --frozen --no-dev --no-install-project` 和各自 strict import/forbidden-import smoke，均通过；`rllib-taxi-synthesis` 另通过独立临时 cache 的 deny-layer harden/verify。native final review 后又在四个全新临时 environment 中按 closed source set 执行第一阶段 omit、核对 `setuptools==84.0.0`、执行第二阶段完整 locked sync，并重跑对应 import smoke，四项均通过；临时 environment 随后删除。该证据证明本机 profile lock/import isolation，不等于 OCI image 已构建。`llm-qwen36-vllm` 仍是 `attached_unverified` metadata-only，真实服务 canary 属于 T05；Agent2World profile 保持 `restricted_disabled`。
