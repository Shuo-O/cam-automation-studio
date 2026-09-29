# 本次评估的复现与证据

日期：2026-09-29。评估对象为当前工作区，包括未提交的 Cimatron 扩展；不是仅评估 Git HEAD。没有修改业务代码，测试仅使用离线 fixture，没有连接真实 CAM。

## 根测试：推荐复现方法

在仓库根目录的 PowerShell 中：

```powershell
$assessmentEnv = Join-Path $env:TEMP 'cam-assessment-20260929-venv'
python -m venv --without-pip $assessmentEnv
& (Join-Path $assessmentEnv 'Scripts/python.exe') -m unittest discover -s tests -q
```

本次：265 tests，65.199 秒，7 failures，0 errors；258 项通过。完整日志见 [assessment-clean-tests-2026-09-29.log](assessment-clean-tests-2026-09-29.log)。

6 个约定不一致：

```text
test_product_actions_cannot_cross_adapter_boundaries
test_marketplace_is_available_on_install_and_never_default_installed
test_connection_statuses_can_include_uninstalled_bridges
test_connection_statuses_keep_all_three_bridges_visible
test_manifest_and_review_validation_are_explicit
test_recorder_and_execution_apis_are_integrated
```

1 个性能失败：

```text
test_all_hard_performance_and_determinism_gates_pass
failed_gates: incremental_validation
p95_ms: 108.162
maximum: 100.0
sample_count: 20
```

未进行专用机器上的性能回归归因。

系统 Python 直接运行时因其他项目的包污染，只发现/运行了 237 项，存在 4 个导入错误及 7 个失败；`-S` 主进程可发现 265 项，但子进程导入仍失败。干净 venv 消除了这类导入问题。

## 产品与前端测试

```powershell
python -m unittest discover -s plugins/ug-cam-copilot/tests -q
python -m unittest discover -s plugins/powermill-cam-copilot/tests -q
node --test cam_automation/web/components/fixtures.test.cjs cam_automation/web/flow/flow.test.cjs tests/flow_barrier/source_mapping_barrier.test.cjs
```

本次结果：NX 44/44，PowerMill 15/15，Node 22/22。Node 包含来源映射测试，因此数量比旧发布说明的 19 项多。本次没有运行浏览器 E2E。

## Cimatron 错误 Flow 分派：最小离线复现

```powershell
@'
from cam_automation.integrations import _map_offline_flow_source
try:
    _map_offline_flow_source(
        b'application.Documents.Open("part.elt")',
        product='cimatron',
        source_name='probe.py',
        target_versions=('Cimatron 2026',),
    )
except Exception as error:
    print(type(error).__name__, str(error))
'@ | python -S -
```

结果：

```text
PowerMillOfflineImportError Non-PowerMill input is not accepted by the PowerMill parser.
```

这是分派函数的最小复现；输入只是探针，不宣称其中方法名是已验证的 Cimatron SDK API。未执行 Python/Journal，也未调用真实宿主。

## 主要代码定位

- `cam_automation/product_catalog.py:72`：Cimatron `flow_import_supported=True`。
- `cam_automation/integrations.py:43`：支持产品来自完整产品目录。
- `cam_automation/integrations.py:1092`：NX/else PowerMill 的 Flow mapper 分派。
- `cam_automation/plugin_manager.py:101`：工作台插件发现只读取 `*/app-plugin.json`。
- `cam_automation/adapters/cimatron_journal.py:189`：Python AST 调用提取。
- `cam_automation/adapters/cimatron_journal.py:215`：逐行 C# 正则提取。
- `plugins/ug-cam-copilot/src/ugcam_ai/selectors.py:47`：对象建议默认未解析。
- `cam_automation/learning.py:633`：结构签名与工作流族挖掘入口。

行号基于评估时工作区，后续修改可能移动。
