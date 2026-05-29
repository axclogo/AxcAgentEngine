# Engine Development Rules

## 1. Code Quality

谨记：代码是负债，功能才是价值。禁止垃圾、临时、遗留、废弃、重复的代码进入项目。

Remember: code is liability, functionality is value. Do not introduce junk, temporary, legacy, dead, or duplicated code.

代码应在不牺牲阅读性、扩展性、健壮性的前提下尽可能精简。不要为了少写几行代码牺牲结构清晰度，也不要为了抽象而引入没有实际价值的复杂层。

Code should stay as concise as possible without sacrificing readability, extensibility, or robustness. Do not trade clear structure for fewer lines, and do not add abstractions that do not remove real complexity.

代码注释必须使用英中双语。注释只解释必要的设计意图、边界、复杂逻辑或非显而易见的行为，禁止写重复代码字面含义的空注释。

Code comments must be bilingual in English and Chinese. Comments should explain design intent, boundaries, complex logic, or non-obvious behavior only. Do not add empty comments that merely restate the code.

### Python Coding Standard

- 代码紧凑；方法之间保留 1 个空行，类之间保留 1 个空行；每行不超过 120 字符；使用 Tab 缩进。
- Keep code compact. Use one blank line between methods and one blank line between classes. Keep lines at or below 120 characters. Use Tab indentation.

- 注释保持简洁单行，格式为 `"""简洁描述"""`；行内注释放在代码末尾。
- Keep comments short and single-line, using `"""Concise description"""`. Put inline comments at the end of the code line.

- 命名规则：变量和函数使用小写下划线，类名使用驼峰，常量使用全大写，私有成员使用单下划线前缀。
- Naming: variables and functions use lower_snake_case, classes use PascalCase, constants use UPPER_CASE, and private members use a single leading underscore.

- 导入顺序：标准库、第三方库、本地模块；各组之间用一个空行分隔。
- Import order: standard library, third-party libraries, local modules. Separate groups with one blank line.

- 函数不超过 50 行，参数不超过 5 个，异常捕获必须使用具体异常类型。
- Functions must stay within 50 lines, accept no more than 5 parameters, and catch specific exception types.

- 优先使用 f-string；布尔判断直接写 `if items:`，不要写冗余比较。
- Prefer f-strings. Use direct boolean checks such as `if items:` instead of redundant comparisons.

## 2. Failure Policy

禁止为配置错误、运行错误、流程错误等致命错误做任何形式的兜底、降级、静默跳过或继续运行。

Do not add any fallback, degradation, silent skip, or forced continuation for fatal configuration errors, runtime errors, or workflow errors.

配置错就直接报错；资源缺失就直接报错；插件加载失败就直接报错；插件依赖缺失就直接报错；插件 hook 出错就直接报错。

Invalid configuration must fail directly. Missing resources must fail directly. Plugin load failures must fail directly. Missing plugin dependencies must fail directly. Plugin hook failures must fail directly.

引擎不为任何宿主的错误使用买单，不通过兜底、兼容错误写法、自动修正错误输入来掩盖问题；宿主用错就直接崩溃或报错。

The engine must not pay for any host misuse. Do not hide host errors through fallbacks, compatibility with invalid patterns, or automatic correction of invalid input. If the host uses the engine incorrectly, fail or raise directly.

允许存在显式、用户可见、语义清晰的策略，例如用户主动配置的 fallback model、工具 retry policy、检索链内部明确的排序/召回策略。它们不能掩盖配置错误或致命运行错误。

Explicit, user-visible, semantically clear strategies are allowed, such as a user-configured fallback model, a tool retry policy, or a deliberate retrieval ranking/recall strategy. They must not hide configuration errors or fatal runtime failures.

上报错误后，必须先核实是否确实为 bug；确认是 bug 后，修复必须补齐对应测试用例，覆盖复现路径和修复后的正确行为。

After an error is reported, first verify whether it is a real bug. Once confirmed, the fix must include matching tests that cover the reproduction path and the corrected behavior.

## 3. Module Boundaries

引擎每个模块的职责边界必须绝对清晰，禁止把宿主、引擎、插件、Agent YAML 的职责混在一起。

Every engine module must have a clear responsibility boundary. Do not mix host, engine, plugin, and Agent YAML responsibilities.

引擎负责向宿主要运行所需资源，并把资源以明确入口注入运行实例。引擎不应该伪造外部资源，也不应该在资源缺失时偷偷替换成别的实现。

The engine is responsible for requesting required runtime resources from the host and injecting them through explicit instance-level entry points. The engine must not fake external resources or secretly replace missing resources with other implementations.

插件用于增强 Agent 能力。插件可以组织工具、上下文、策略、检索流程或运行时增强，但不能把宿主资源管理、模型配置、持久化基础设施等职责混进插件边界。

Plugins are for enhancing Agent capability. A plugin may organize tools, context, policies, retrieval flow, or runtime enhancement, but it must not absorb host resource management, model configuration, or persistence infrastructure responsibilities.

Agent YAML 只负责声明配置参数。YAML 不承载运行期对象，不表达宿主资源实例，不通过 overrides 或字符串路径伪装资源注入。

Agent YAML is only for declaring configuration parameters. YAML must not carry runtime objects, host resource instances, or fake resource injection through overrides or string paths.

官方预设插件必须保持轻量、边界清晰，禁止植入网络请求、模型 key 管理、外部服务客户端等臃肿模块。第三方宿主插件可以自行决定是否接入这些能力，但不能反向污染官方插件边界。

Official built-in plugins must stay lightweight with clear boundaries. They must not embed bulky modules such as network requests, model key management, or external service clients. Third-party host plugins may choose to integrate those capabilities, but must not push that boundary back into official plugins.
