# Release checklist · 3.1.0rc1

本清单用于候选包的本地验收。发布标签、上传包和用户验收单独记录。

## 行为验收

| 交付项 | 可复现的检查 |
| --- | --- |
| 会话隔离 | `tests/engine/test_live_input.py` 用实际模型请求检查 A/B 会话并发、交替恢复和匿名调用。压缩插件用例覆盖挂载索引的归属，边界存储用例覆盖旧键与含分隔符的键。 |
| 插话及回执 | 官方 ReAct、POR 自动模式和示例第三方循环共用 FIFO、幂等和所有者检查。覆盖队列上限、落盘失败、终止钩子、在途并行工具、取消、运行 ID 冲突与父子运行。 |
| 中断恢复 | 子进程分别在接收确认后、历史写入后主动退出，宿主从磁盘快照恢复。写工具已产生副作用时取消，检查未知调用被拦截、已落盘步骤可继续以及外部写入计数。 |
| 恢复边界 | 本次新增用例验证已完成运行重复恢复、会话已有后续消息、补充输入，以及并行工具结果部分缺失。旧 wheel 的测试数字不含这批改动。 |
| POR 实时进度 | 阻塞模型证明宿主可在步骤完成前收到开始事件。并行快步骤在慢步骤结束前交付结果。补充消息仅触发剩余工作重规划，关闭流回收在途任务。 |
| 循环插件 | 示例 01–07 显式注册并选择官方 POR 自动模式。示例 08 通过 YAML 切换第三方循环。策略接口、状态版本和旧快照迁移有独立契约测试。 |
| 本地命令与工作区文件 | 验证取消和超时后子进程释放资源、大日志按制品读取、精确编辑保留字节、过期版本拒写，以及只读代码搜索的工作区边界。 |
| 第三方模块 | 实际 stdio 子进程与本地 SSE、Streamable HTTP 服务验证官方 MCP SDK。基础安装与扩展安装均默认使用内存工作流，Burr 由宿主显式选择。基础安装能运行本地知识检索并恢复旧快照。 |
| 文档与依赖 | 中英文 README、插件规范和示例 09 记录投递、回执、取消、持久化与迁移。工具参数校验子集已列明。MCP 自写传输、未使用的 FAISS extra 和双语占位说明已清理。 |

## 从安装包复验

构建需要 Python 3.11、3.12 或 3.13。以下命令在仓库根目录执行，以 3.13 为例，将 `v` 分别换成 `3.11`、`3.12`、`3.13`。模型测试使用离线夹具，MCP 集成测试需要绑定本机回环端口。工作目录与 wheel 分离可以发现源码树掩盖的打包问题。

```sh
v=3.13
work=$(mktemp -d)
uv build --out-dir "$work/dist"
wheel=$(find "$work/dist" -name '*.whl')
uv venv --python "$v" "$work/base"
uv pip install --python "$work/base/bin/python" "$wheel"
uv venv --python "$v" "$work/verify"
uv pip install --python "$work/verify/bin/python" "${wheel}[all,dev]"
uv pip check --python "$work/base/bin/python"
uv pip check --python "$work/verify/bin/python"
"$work/verify/bin/twine" check --strict "$work/dist"/*.whl "$work/dist"/*.tar.gz

mkdir "$work/check"
cp -R tests examples docs "$work/check"
cp pyproject.toml Makefile README*.md CHANGELOG.md ENGINE_DEVELOPMENT_RULES.md "$work/check"
installed=$("$work/verify/bin/python" -Ic 'import axc_agent_engine as a; print(a.__path__[0])')
ln -s "$installed" "$work/check/axc_agent_engine"
(
  cd "$work/check"
  export BURR_TELEMETRY_ENABLED=false
  "$work/verify/bin/python" -m pytest -q --cov=axc_agent_engine --cov-fail-under=95
  make test-core-cov PYTHON="$work/verify/bin/python"
  "$work/base/bin/python" -I examples/09_live_input/run.py
  "$work/base/bin/python" -I examples/09_live_input/run.py --cancel
)
```

## 发布检查

- [x] `pyproject.toml`、`_version.py`、API 元数据和 CHANGELOG 使用候选版本号。
- [x] 全套测试覆盖率门槛 95%，核心门槛 98%。源码通过 Ruff 检查与格式校验、mypy、compileall 和本地 pre-commit hooks。
- [x] wheel 和 sdist 通过 `twine check --strict`。两者包含 LICENSE、NOTICE 和 py.typed，产物内容检查覆盖缓存、环境目录及日志。
- [x] 复核 SECURITY.md 与工具能力和审批门控。使用独立的 `[all]` 生产环境导出确切依赖版本，用 `pip-audit -r requirements.txt --no-deps --disable-pip` 审计。输入只包含第三方包。
- [x] 用 `detect-secrets scan --all-files` 扫描 Git 工作树内的发行文件，包含尚未跟踪的新文件，排除 Git 忽略的缓存。逐项核对命中样例与测试数据。
- [ ] 用户验收候选改动。
- [ ] 维护者批准后打标签并发布。

下方记录对应当时构建的 wheel。当前工作树已更新恢复行为，新增用例尚未执行。包元数据检查使用 Twine，未向 PyPI 上传预览。分布式收件箱、完整事件溯源及其他工作流框架留待有具体宿主需求时评估。

## 本地验收记录 · 2026-11-24

以下结果来自 macOS arm64，在源码目录外安装本次构建的 wheel 后运行。测试使用离线模型，MCP 测试启动实际本地服务。

| Python | 全量测试 | 全仓行覆盖率 |
| --- | ---: | ---: |
| 3.11.16 | 1691 通过 | 96.65% |
| 3.12.14 | 1691 通过 | 96.61% |
| 3.13.15 | 1691 通过 | 96.63% |

- Python 3.13 的核心集合 820 项通过，覆盖率 98.32%。三组全量测试均无跳过，各有一条 Starlette TestClient 使用 httpx 的上游弃用提示。
- 三个 Python 版本的基础安装与扩展安装均完成示例 09 的继续执行和取消分支。基础安装另行验证本地知识检索、旧快照恢复和缺少可选依赖时的错误说明。
- 本次解析得到的 36 个第三方生产依赖未报告已知漏洞。密钥扫描检查了 427 个文件，7 个候选经核对均为示例占位符或测试数据。

本轮独立复核覆盖了可能重复写入的并发恢复、租户记忆在子步骤和进程恢复中的归属、相同会话 ID 重用、官方 POR 的真实 HTTP 流以及 MCP 发现时取消。另选 7 个竞态用例，在 Python 3.11 和 3.13 各连续运行 5 轮，70 次执行全部通过。
