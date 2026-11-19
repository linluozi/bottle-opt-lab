# bottle-opt-lab

Bottle 是单文件 WSGI 微框架（8.8k★，MIT）。本仓库只改 `Router` 的**编译时机**：上游在每次 `Router.add()` 动态路由时，都会把该 HTTP method 下**已经加过的全部动态路由**重新拼接、重新 `re.compile` 一遍；本补丁把它推迟到该 method 的**首次 `Router.match()`**，用 set 标记脏 method，一次构建。公开 API、匹配顺序、返回值、异常语义均未改。

Upstream: https://github.com/bottlepy/bottle

- 基线提交：`aba0c1a7ee59ed851db001d100b6a792b54a94a3`（2026-10-23）
- 补丁：`patch.diff`，`bottle.py` 的 `Router` 一处生命周期改动，**13 insertions / 4 deletions = 17 行**
- 许可证：上游 MIT 原文见 `LICENSE`，文末注明来源、提交与改动范围
- 上游已有一条 2016-11-15 开放、至今未修的 issue：**#908 “Router _compile slows down as number of routes increases”**，报告里明确说 80% 启动时间花在 add 路由时的 regex 编译上。本补丁就是针对它的最小修法。

## 结果

同一份 `benchmark/bench.py`、同一批路由、同一环境。基线实现 = `src/bottle.py` 用 `git apply -R` 逐字节还原出的上游文件（反向补丁成功，且还原文件 sha256 与上游一致：`0a491959…9bc968`）；优化实现 = 打完补丁的 `src/bottle.py`。每规模 warmup 3 次、计时 5 次，中位数；原始样本 64 个文件在 `benchmark/raw/`。

**登记（`Router.add()` 全部跑完）**

| 规模 | 路由数 / 动态数 | 基线中位 | 优化后中位 | 加速比 |
| --- | --- | --- | --- | --- |
| 合成 100 | 100 / 100 | 2.679 ms | 1.104 ms | **2.43x** |
| 合成 500 | 500 / 500 | 1,158.359 ms | 6.090 ms | **190.22x** |
| 合成 2,000 | 2,000 / 2,000 | 5,342.605 ms | 163.104 ms | **32.76x** |
| 真实语料 628 | 628 / 392 | 1,189.834 ms | 7.625 ms | **156.05x** |

**端到端就绪（登记 + 该 method 的首次 match，包含一次性编译成本）**

| 规模 | 基线中位 | 优化后中位 | 加速比 |
| --- | --- | --- | --- |
| 合成 100 | 2.692 ms | 1.159 ms | **2.32x** |
| 合成 500 | 1,158.408 ms | 6.343 ms | **182.62x** |
| 合成 2,000 | 5,342.778 ms | 257.074 ms | **20.78x** |
| 真实语料 628 | 1,189.892 ms | 8.060 ms | **147.64x** |

**稳态 dispatch（已就绪后连续 200 次 match，单次 µs）**

| 规模 | 基线中位 | 优化后中位 | 比值 |
| --- | --- | --- | --- |
| 合成 100 | 9.178 µs | 9.224 µs | 0.995x |
| 合成 500 | 40.480 µs | 40.574 µs | 0.998x |
| 合成 2,000 | 156.949 µs | 157.790 µs | 0.995x |
| 真实语料 628 | 26.484 µs | 26.348 µs | 1.005x |

真实语料不是自造随机路径：`benchmark/corpus_real.json` 收录 DigitalOcean 公开 OpenAPI v2 的 **628 条具体路径**（commit `a2f5b2fea7a5113f10251ff342c64eec2ca3dcaa`，`specification/DigitalOcean-public.v2.yaml`），其中 **392 条带 `<name>` 动态段**；`{}` 占位符按 Bottle 语法转成 `<name>`，路径里的 action 冒号转义为 `\:`，每条路径以 GET 注册（只读代理形态）。路径表的 sha256 写在 corpus JSON 里。

## 差分与闸门

差分测试 **407 个互不相同的「add/match 操作历史」**，覆盖：100/500/2000 规模的顺序、`GET/POST/ANY/HEAD`、静态与 `<id>/<int>/<path>/<>` 动态、自定义 filter、转义冒号、非 ASCII、路由覆写、`strict_order=True`、99/100/198/199 条分组边界（含「match 后再 add 再 match」的脏重编译路径）、404/405，以及上面的 628 条真实路径表。样本数按 **sha256(路由历史 + 请求)** 去重后为 **2,382 个独立样本**，不是循环次数；两侧输出用 `json.dumps(..., sort_keys=True)` 序列化后**逐字节比较**。结果：**2,382 / 2,382 一致，0 mismatch**（2.67 s）。

```
$ python3 tests/test_diff.py
{"comparisons": 407, "distinct_samples": 2382, "mismatch_scenarios": [], "mismatches": 0,
 "real_corpus_routes": 628, "scenarios": 407, "seed": 20261119}
$ python3 tests/test_lazy.py
5 tests passed
```

| 闸门 | 结论 | 可复现命令 / 产物 |
| --- | --- | --- |
| G1 上游测试全绿 | ✅ **363 passed in 0.54s**（打补丁后；未打补丁的同一提交 **363 passed in 0.70s**） | `benchmark/upstream_tests.log`；下方 G1 命令 |
| G2 差分一致 | ✅ **2,382 个独立样本，0 mismatch** | `python3 tests/test_diff.py` |
| G3 中位加速 ≥1.15x | ✅ 三个合成规模登记加速比中位 **32.76x**（2.43 / 190.22 / 32.76）；端到端就绪中位 **20.78x** | `python3 benchmark/bench.py --impl {baseline,optimized} ...` |
| G3b 真实语料 ≥1.15x | ✅ **147.64x** 端到端就绪；登记 **156.05x**（628 条真实 OpenAPI 路径） | 同上，读 `real_digitalocean` |
| G4 最大规模不退化 | ✅ 2,000 条：登记 **32.76x**，就绪 **20.78x**；稳态 match **0.995x** | 同上，读 `synthetic_2000` |
| G5 峰值 RSS ≤1.10x | ✅ **0.849x**（基线 33,996 KB → 优化 28,864 KB，3 进程各 3 次的中位；独立复跑 0.862x） | `python3 benchmark/memcheck.py` |

**G1 复现**（不改本仓库；`git` 用于反向还原基线）：

```bash
python3 -m venv /tmp/bv && /tmp/bv/bin/pip -q install pytest
git clone --depth=1 git@github-linluozi:bottlepy/bottle.git /tmp/bottle-up
cd /tmp/bottle-up && git checkout -q aba0c1a7ee59ed851db001d100b6a792b54a94a3
git apply --check /path/to/bottle-opt-lab/patch.diff && git apply /path/to/bottle-opt-lab/patch.diff
/tmp/bv/bin/python -m pytest test -q --no-header -p no:cacheprovider   # 363 passed
```

**G5 复现**：`python3 benchmark/memcheck.py` 会各起 3 个新进程，对 2,000 条路由登记 + 200 次 match 取 `ru_maxrss` 高水位；结果写 `benchmark/memcheck.json`。

**噪声披露**：优化后真实语料登记只有 7.6 ms，5 个样本的 rel_sd 为 **77.0%**（基线 0.33%）；合成 100 条优化后 rel_sd 20.1%（1.1 ms 窗口）。这是 4 vCPU 共享主机上的进程级抖动与小窗口效应，不是加速比来源；即便按 p25/p75 的极端组合计算，真实语料仍 >100x。合成 500/2,000 的优化后 rel_sd 分别为 5.7% / 3.8%，基线均 <0.7%。表中全部取中位，不取最快值，warmup 不计入计时。所有数字来自 `benchmark/baseline.json`、`benchmark/optimized.json` 与 `benchmark/raw/` 的 64 个原始样本。

**四分位（p25–p75）**：登记基线 100 / 500 / 2,000 / 真实 = [2.677, 2.691] / [1,149.194, 1,164.056] / [5,341.140, 5,346.431] / [1,184.223, 1,190.668] ms；优化后 = [1.053, 1.198] / [5.691, 6.310] / [159.760, 163.246] / [7.420, 7.916] ms。就绪与 match 的 p25/p75 见 `benchmark/baseline.json` / `benchmark/optimized.json` 每个规模的 `registration_ms` / `ready_ms` / `match_us` 字段。真实语料 match 的基线 rel_sd 20.7%（26 µs 小窗口），优化后 1.2%，只作「不退化」参考，不作为收益。

**独立复跑**：第二轮完整 `bench.py`（`benchmark/replication.json`）得到登记加速比 2.24 / 186.06 / 32.47 / 146.03x、就绪 2.15 / 173.63 / 19.98 / 140.90x（顺序同表：100 / 500 / 2,000 / 真实）；只读 verifier 的第三轮得到登记 2.55 / 194.63 / 34.11 / 160.75x、就绪 2.45 / 185.75 / 20.85 / 156.28x，G5 RSS 比值 0.862x。三轮的合成就绪中位都 ≥19.9x，真实就绪都 ≥140.9x；`match_us` 三轮比值在 0.963–1.046 之间，按噪声处理，不声称改善。

## 复现（三条命令）

```bash
git clone --depth=1 git@github-linluozi:linluozi/bottle-opt-lab.git && cd bottle-opt-lab
python3 tests/test_diff.py
python3 benchmark/bench.py --impl baseline --out benchmark/baseline.json && python3 benchmark/bench.py --impl optimized --out benchmark/optimized.json
```

运行时只需要 CPython 3.12 + 标准库；`benchmark/bench.py` 与 `tests/test_diff.py` 需要 `git` 可执行文件（用于把 `patch.diff` 反向还原成基线）。不需要安装 Bottle，也不访问网络。完整基线跑约 50 秒（2,000 条基线登记本身约 5.3 秒/次 × 8 次）；优化侧约 1 秒。

## 环境

- CPU：AMD EPYC 7K62 48-Core Processor（4 vCPU）
- 内存：3,812,564 kB
- 内核：Linux 6.8.0-124-generic
- 运行时：CPython 3.12.3（`/usr/bin/python3`），pytest 9.2.0
- 种子：差分测试 `20261119`；基准无 RNG，路由/请求由确定性索引构造
- 跨解释器：本机只有 CPython 3.12.3，无 3.10/3.11，**未做跨版本交叉**；不编造多版本数据

## 本轮创新

**B 档**：上游 issue #908 从 2016-11-15 开放至今未修，报告者只给了「注释掉 `_compile`、最后手工调一次」的 workaround；本仓库把「何时编译」从 `add()` 移到该 method 的首次 `match()`，用一个 `_dirty` 集合保持语义，并在**上游真实 628 条 OpenAPI 路径**上把启动从 1,189.9 ms 降到 8.1 ms（147.6x），同时用 2,382 个独立 `(路由历史, 请求)` 样本逐字节证明匹配结果不变、稳态 dispatch 不变（0.995–1.005x）。与台账里的单点 opt-lab 不同：这里改的是路由表的编译生命周期，不是某个热函数的一行；并且给出了「收益只在启动、稳态不变」的反向证据，没有把启动数字包装成请求吞吐。

## 限制与已知边界

1. 只测了 **启动/登记** 与 2,000 条规模；没有把 628 条真实路径按真实 HTTP method 分布登记（全部按 GET，取只读代理的最坏分组形态）。
2. 稳态 `match()` 没有变快，实测 0.995–1.005x；本仓库不声称请求吞吐提升。
3. `Router.dyna_regexes` 现在**懒填充**：`add()` 之后、首次 `match()` 之前直接读它会看到空/旧值。它是内部属性，上游测试与公开 API 不依赖它；`Router._compile(method)` 仍保留，issue #908 里的手工 workaround 继续可用。若外部代码依赖 `add()` 后立即读 `dyna_regexes`，这是唯一可观察差异，已在此明写。
4. 单条路由的 `re.compile('^(...)$')` 仍在 `add()` 里执行，所以 2,000 条优化侧还剩约 163 ms 的 O(N) 成本（`re` 模块 512 项缓存被冲掉后每条约 80 µs）；继续优化需要把单路由编译也推迟，本轮不做。
5. 若应用在开始服务后还逐条 `add()` 路由，下一次 `match()` 会按 method 全量重编译一次（O(N)）；适合启动期一次性注册的常见用法，不适合运行期高频改路由。
6. 本机无 3.10/3.11，未做跨版本交叉；结论仅限 CPython 3.12.3 / Linux。

## 来源与改动范围

- 上游：`bottlepy/bottle`，commit `aba0c1a7ee59ed851db001d100b6a792b54a94a3`，MIT。
- `src/bottle.py` 是上述文件加上 `patch.diff`；`LICENSE` 保留上游 MIT 原文，文末追加来源与改动范围说明。
- 改动只在 `Router.__init__`（新增 `_dirty`）、`Router.add`（末行改为标脏）、`Router._compile`（清脏）、新增 `Router._get_regexes`、`Router.match`（两处改走 helper）。未改公共 API 签名、返回值语义、上游测试文件。
- 反向补丁验证：`src/bottle.py` 复制到临时目录后 `git apply -R -p1 patch.diff`，与干净上游 clone 的 `bottle.py` **逐字节相同**；`git apply --check` 在干净 clone 上通过。