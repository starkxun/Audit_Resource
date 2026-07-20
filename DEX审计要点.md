# DEX 审计要点（通用 · 可带走）

这份文档**不绑定任何项目**，是《赏金审计开局 checklist》的 **DEX 换血包**：checklist 的元流程（读范围 → 信任边界 → 找兜底 → 假设排序 → 四问闸门 → 报告）不变，只把**阶段 2 的兜底目录**、**阶段 0 的高频排除项**、**阶段 3 的类型学**换成 DEX 味的。

> 用法：打 DEX 项目时，checklist 阶段 2 那句"桥请用跨桥目录替换本节"→ 改成"DEX 请用本文《§2 兜底目录》替换本节"。元动作"**先找兜底再决定挖不挖**"一字不变。

---

## 为什么单列一份

DEX 和借贷/桥的**兜底形态不同**，但会撞死人的机制是同一套逻辑：

- 借贷靠 **预言机计价 + 余额差 + 偿付性**，把 calldata 花招压成"最多漏 X%"。
- 桥靠 **守恒 + 门限签名 + 去重**。
- **DEX 靠 `K 不变量 + reserve/balance 对账 + 用户滑点`**，把任意路由/构造压成"最多漏 rounding dust"，dust 又被滑点兜住。

> **核心命题**（与 checklist 一致）：当 DEX 在 swap 关键路径上用 `x*y>=k` 后置校验时，它把所有 calldata 层逻辑漏洞降级成"最多漏舍入 dust"。攻击面被压缩到只剩**兜底机制本身**（舍入方向、K 在特殊 token/特殊分支下失效）与**没有兜底罩着的面**（回调鉴权、tick 记账、首存者份额、只读重入）。

---

## §0 阶段 0 换血：DEX 高频排除项（雷区）

这是 DEX 的 "temporary-freeze 级"杀手 —— 任何一条命中，对应整类攻击面**直接作废**。**读范围时逐条比对**，比 in-scope 更重要。

| 排除项 | 作废掉什么 | 备注 |
| --- | --- | --- |
| **sandwich / MEV / front-run-only** | 所有"抢跑套利、夹子"类 | 极高频。发现只能靠 front-run 触发的 → 死 |
| **impermanent loss / LP 无常损失** | 所有"价格波动导致 LP 亏"的 | 这是设计特性，不是 bug |
| **user-defined slippage / deadline** | "没设/设太松滑点被割"类 | 属用户配置错误，不归协议 |
| **price manipulation on low-liquidity pools** | "薄池砸盘操纵报价"类 | 常判 out；但**若该池被当预言机喂给别的协议**，可能 in |
| **flashloan-enabled**（明列可/排除两种都有） | 决定整条闪电贷假设生死 | ⚠️ **开局第一件事确认**，别做到最后才发现被排除 |
| requires external protocol failure | "如果某 router / 某 oracle 挂了" | 通用 |
| already known / previously reported | 先搜 public reports（DEX 报告极多） | 通用 |

### 阶段 0 产出（DEX 版一句话）

> **"我要找的是：`不可信的 swapper / LP / flash 借款人 / 回调方`，能通过 `swap / add / remove / flash / callback` 这类入口，让 `其他 LP / 协议金库 / 交易者` 损失资金 —— 且不依赖抢跑。"**

任何不匹配它、或落在上表任一排除项里的线索，**直接跳过**。

---

## §1 阶段 1 换血：DEX 入口 → 谁能调 → 是否可信

grep 出外部入口，按 checklist 三栏分。DEX 的特殊性和桥一样：**caller 可信 ≠ 数据可信** —— 回调里 caller 是 pool/router，但 callback data 由发起 swap 的人撰写。

| 入口 | 谁能调 | 结论 / 挖点 |
| --- | --- | --- |
| `swap` | 任何人 | ⚠️ **重点** K 校验、reserve 更新时机、to 地址、fee 计算 |
| `mint` / `addLiquidity` | 任何人 | ⚠️ **重点** 份额计算、**首存者膨胀**、`totalSupply==0` 分支 |
| `burn` / `removeLiquidity` | 任何人（自己 LP） | ⚠️ 份额→资产舍入方向、pro-rata 计算 |
| `flash` / flashLoan | 任何人 | ⚠️ **重点** 回调后 K/余额校验、fee 下限 |
| `*Callback`（swap/flash/mint 回调） | pool 调，**data 不可信** | 🎯 **主攻** caller 校验是否只认真 pool？`msg.sender==pool` 用什么算的？可被伪造 pool 绕过？ |
| `skim` / `sync` | 任何人 | ⚠️ reserve 与 balanceOf desync、donation |
| `getReserves` / `slot0`（view） | 任何人读 | 🎯 **只读重入**：在回调中间态被读去喂下游报价 |
| `collect` / fee 提取 | LP / 协议 | 校验 fee 记账 |
| admin: `setFeeTo` / `setOwner` / pause | owner / governance | 🚫 可信，别挖（除非能证明可被非治理方触发） |

> ⚠️ **回调鉴权是 DEX 的头号孔**：`uniswapV3SwapCallback` 里若用 `CallbackValidation` 按 `factory + poolKey` 反算 pool 地址，检查该反算是否可被伪造 token/fee 参数绕过 → 假 pool 骗真回调。

**产出**：一张表，只在"不可信"行继续。

---

## §2 阶段 2 换血：⭐ DEX 兜底目录（本文最值钱的一节）

挖任何字段前先问：**这条路径上罩着哪几层后置校验？** DEX 兜底目录：

| 兜底 | 长什么样 | 它罩住了什么 |
| --- | --- | --- |
| **K 不变量后置校验** | swap 后 `require(balance0Adj * balance1Adj >= reserve0 * reserve1 * 1e6²)`（含 fee） | 所有骗过参数的路由/构造花招 → 压到"最多漏舍入 dust"。**余额差校验的 DEX 对应物** |
| **reserve vs balanceOf 对账** | 用真实 `balanceOf` 差值算 amountIn，非声明值 | fee-on-transfer / rebasing / 假 token 的记账偏移 |
| **用户滑点上限** | `require(amountOut >= minAmountOut)` + `deadline` | 把价格滑动压到用户容忍范围 |
| **舍入方向不变量** | 取出向下取整、存入向上取整，**恒向池子有利** | 精度型反复抽取 |
| **重入锁** | `lock` 修饰器 | 经典重入 |
| **TWAP / 抗操纵预言机** | 时间加权、多区块累加器 | 单区块闪电贷操纵现价 |
| **tick / liquidity 记账不变量**（集中流动性） | `liquidityNet` 累加、`feeGrowthInside` 单调 | tick 穿越时的流动性/费用记账 |

### 判定规则（与 checklist 一致）

1. **先找兜底，再决定挖不挖。** 一个面被 K-不变量罩着 → **别在这面找 calldata 花招**。
2. 要么**去打兜底本身**，要么**换一个没兜底的面**。
3. ⚠️ **如果兜底恰好是被排除那一类**（如"薄池预言机操纵" out-of-scope）→ 这个面对你是死结，早撤。

### DEX 快速算术杀法（不用查链就杀掉整类）

> **K-不变量能否被抽干，先算这个**：设 swap 后池子断言 `x'·y' ≥ x·y`。任何满足该式的交易，攻击者拿走的价值 ≤ **舍入误差累积**（每笔 ≤ 1 wei 量级）+ **用户自设滑点**。
> **结论：只要 K 校验用真实 balanceOf 且 fee 正确计入、舍入恒向池子，calldata 级抽取型攻击碰不到 LP。**
> **唯一活路**：① 舍入方向反了（向用户有利）；② K 校验被特殊 token 绕过（fee-on-transfer 未用余额差、rebasing、ERC777 回调重入）；③ `totalSupply==0` / 空池分支绕过 K；④ 集中流动性下 K 不适用、改看 tick 记账。**四条活路正是 §3 的主攻方向。**

---

## §3 阶段 3 换血：DEX 逻辑类型学

checklist 那张通用类型学照用（Decode/Execute 分歧、Fail-open on zero、不对称检查、单向校验、记账 vs 真实余额、空 catch、可信角色边界）。补这几条 DEX 专属，**按证伪成本从低到高**：

| 类型 | 模式 | 去哪找 | 证伪成本 |
| --- | --- | --- | --- |
| **舍入方向** | 取出/存入取整方向反了，或先乘后除 vs 先除后乘 | 所有 `mulDiv` / `* / ` 顺序 | 💰 纯推理 |
| **首存者 / 份额膨胀** | `totalSupply==0` 时 `shares=amount`，可被 donation 撬动 | mint / vault deposit 的空池分支 | 💰 读代码 |
| **donation / reserve desync** | 直接转账抬高 balanceOf，撬动依赖 balance 的分支 | 用 `balanceOf(this)` 而非 reserve 的地方 | 💰 读代码 |
| **回调鉴权绕过** | `msg.sender==pool` 用可伪造参数反算 | `*Callback`、CallbackValidation | 💰💰 读调用链 |
| **只读重入** | 回调中间态 `getReserves/slot0` 被下游读为价格 | view 报价函数 + 有回调的 token | 💰💰 读调用链 |
| **fee-on-transfer / rebasing** | 声明 amount ≠ 真实到账，K 校验被绕 | 未用余额差的 swap/add 路径 | 💰💰 读代码 + 确认 token |
| **tick / feeGrowth 记账** | tick 穿越时 liquidityNet 符号、feeGrowthInside 溢出（有意 unchecked wrap） | 集中流动性 tick 库 | 💰💰💰 读+算 |
| **精度/单位混用** | token decimals 差异、Q64.96 定点转换 | 价格/sqrtPrice 转换处 | 💰💰💰 读+算 |

排序规则同 checklist：**按证伪成本排，不按"成立多爽"**。先用纯推理杀掉舍入/份额膨胀这类，再动手读回调/tick。

---

## §4 阶段 4 闸门（DEX 版四问，不变）

任何一问答不出来就别写 PoC：

1. **攻击者是谁？** 必须不可信（swapper/LP/flash 借款人/回调方）。是 owner/治理 → 停。
2. **失效模式是什么？** 必须是错误数值/状态（多拿了资产、份额算错）。是 revert/卡住 → 看该项目是否排除可用性。
3. **钱从谁兜里出？** 必须能论证是**其他 LP / 协议金库 / 其他交易者**。是攻击者自己的本金/滑点/无常损失 → 停。
4. **属于哪一类 in-scope？** 且**不依赖 front-run/MEV**（若已排除）→ 说不出来就停。

---

## §5 报告 + 工具箱（复用 checklist，DEX 补丁）

阶段 5 写报告、附 A 工具箱**全部复用**。DEX 特有补丁：

| 手法 | 说明 |
| --- | --- |
| **fork 真实池子做 PoC** | `anvil --fork-url`，fork 到有真实流动性的区块；别在空池 mock 上证明（triager 会质疑真实性） |
| **扫历史求真实分布** | 池子真实流动性深度、fee tier、token 是否 fee-on-transfer —— **扫链上事件，别信文档** |
| **舍入类必给数值 PoC** | 精度型漏洞纯推理会被打回"theoretical"，必须 fork 上跑出攻击者净赚 > gas |
| **确认 token 行为** | fee-on-transfer / rebasing / ERC777 hook —— 从**链上字节码/真实交易**确认，不是从接口假设 |

---

## 附：三类项目兜底目录速查（换血对照）

| | 借贷 | 桥 | **DEX** |
| --- | --- | --- | --- |
| 兜底 1 | 预言机计价 | 门限签名（信任根） | **K 不变量 `x·y≥k`** |
| 兜底 2 | 余额差后置校验 | 消息去重（全哈希承诺） | **reserve/balanceOf 对账** |
| 兜底 3 | 偿付性 `HR≥1` | 守恒（进出配平） | **用户滑点 + 舍入方向** |
| 头号活路 | 预言机高估清算价值 | 漏进 preimage 的字段 | **舍入反向 / K 被特殊 token 绕过 / 空池分支 / tick 记账** |
| 常见死结 | 预言机 in out-of-scope | validator 合谋 | **sandwich/MEV/IL/滑点 in out-of-scope** |

> **元动作永远不变**：读范围杀死方向 → 画信任边界只挖不可信 → 找兜底决定挖哪面 → 按证伪成本排序先杀便宜的 → 四问闸门 → 绕损失路径写报告。**换项目只换这三行兜底目录和排除项清单。**
