# MCP 集成

核对日期：2026-09-07。工作台的 **MCP** 页支持搜索、查看安装前提、选择工具、复制或下载配置。目录数据在 `cam_automation/cad_integrations.json`，每个外部项目记录核对的 commit；本项目不复制其实现代码。

## 本项目 MCP 服务

在项目根目录创建独立环境并安装可选依赖：

```sh
python -m venv .venv
# macOS / Linux
.venv/bin/python -m pip install -e '.[mcp]'
.venv/bin/python -m cam_automation serve --port 8765
```

Windows 将 `.venv/bin/python` 换成 `.venv\Scripts\python.exe`。原有日志分析、HTTP/UI、目录与配置导出仍可直接用 Python 标准库运行；只有 MCP 传输需要 `mcp` extra。

```sh
python -m cam_automation mcp-catalog
python -m cam_automation mcp-config cam-studio step-inspector
# 用已安装 mcp extra 的解释器启动；stdout 专用于 MCP 消息
python -m cam_automation mcp
```

`mcp-config` 仅输出所选项，不写客户端设置或启动进程。`cam-studio` 使用生成配置时的 Python 解释器，所以应使用已经安装 `.[mcp]` 的环境生成配置。安装包包含 NX adapter、目录 JSON 和网页资源，可从项目目录之外启动。

将生成的 JSON 合并到支持 `mcpServers` 的客户端配置；其他客户端需要转换为其原生配置格式。路径含空格时保持整个路径为一个 JSON 字符串。外部 Python 工具若安装在虚拟环境，需把 `command` 换成该环境下的可执行文件绝对路径。

| MCP 工具 | 输入 | 返回 |
| --- | --- | --- |
| `get_capabilities` | 无 | NX/PowerMill 格式与既有人工审阅门禁 |
| `analyze_workflow` | `product`, `source`；可选 `source_format`, `name`, `source_name` | 既有 recipe、ActivityEvent、dry-run 预览和审阅上下文 |
| `list_cad_integrations` | 无 | 工具来源、能力、前置条件、配置和限制 |
| `get_mcp_config` | `server_ids: string[]` | 仅包含所选工具的 `mcpServers` JSON |

这些工具不写文件，不执行 Journal，不附着 CAD 进程。`analyze_workflow` 不开放 `allow_review_steps`；PowerMill 复核步骤继续注释。HTTP 审阅流程保持原样，MCP 调用不会同步到网页当前会话。

新增 HTTP 接口：

- `GET /api/mcp/catalog`
- `GET /api/mcp/config?server=cam-studio&server=step-inspector`，未选择或未知 ID 返回 400。

## 收录与选择依据

| 项目 / ID | 在本项目中的用途 | 安装与验证边界 |
| --- | --- | --- |
| [NXOpen MCP](https://github.com/mingfeng6684/nxopen-mcp) / `nxopen-docs` | 优先辅助核对 NX CAM Builder 和成员签名 | 从自有 NX 安装建立 .NET 文档索引；Python 3.11+，语义检索需要模型。不是 NXOpen Python 签名源 |
| [CAD MCP Server](https://github.com/mattmohandiss/cad-mcp-server) / `step-inspector` | 加工前 STEP 几何检查与版本比较 | Node.js 24+，无需 CAD 许可证；本机已做一次真实 STEP 读取冒烟测试 |
| [FreeCAD MCP](https://github.com/neka-nat/freecad-mcp) / `freecad` | 研究桌面 CAD 对象查询、建模与视图反馈 | 需 FreeCADMCP addon 和已启动的 RPC 服务；未做本机宿主验收 |
| [CadQuery contrib MCP](https://github.com/CadQuery/cadquery-contrib/tree/master/mcp-server) / `cadquery` | 参数化样件、几何查询、STEP/STL 导出 | 需 CadQuery/OCCT 和子包；会执行 Python。子包声明 Apache-2.0，仓库根为 MIT，复用前需核对许可证范围 |
| [OpenSCAD MCP](https://github.com/petrijr/openscad-mcp) / `openscad` | SCAD 验证、网格预览、参数变体 | 需 OpenSCAD CLI；其网格输出不能替代精确 STEP/B-rep |
| [Fusion 360 MCP](https://github.com/JustusBraitinger/Autodesk-Fusion-360-MCP-Server) / `fusion` | 参考 Autodesk 宿主 Add-In 建模桥接 | 需 Fusion、上游依赖、Add-In 与路径替换；未实机验收 |
| [Siemens NX MCP](https://github.com/DreamEnding/NX_MCP) / `nx-bridge` | 参考 NXOpen 的独立进程桥接与执行端 | 当前默认 16 个工具，Python Journal 桥接仍是可选批处理路径；上游 NX 2506 验证不等于本项目现场验收 |

上述排序是结合当前 NX/PowerMill 审阅架构的工程判断：先补 API 和几何证据，再在测试项目中验证有状态 CAD 操作。这里的 Siemens NX 与 JavaScript monorepo 工具 Nx 无关。PowerMill 的官方 COM/.NET 路线继续沿用 [原有调研](research-notes.md)，本轮未发现可据此替代它的已验证 PowerMill MCP 执行器。

Fusion 配置根据 [MCP_Server.py](https://github.com/JustusBraitinger/Autodesk-Fusion-360-MCP-Server/blob/dd2f156fe2e79d1ee2282a26eaa8b7e58661a28a/Server/MCP_Server.py) 补了 `--server_type stdio`，因为直接运行该脚本默认启动 SSE。`fusion` 与 `nx-bridge` 中的 `REPLACE_WITH` 必须替换；NX 桥接还需依照上游 README 完成 NX 侧启动流程，配置 sidecar 不代表已连接 NX。

所有外部条目都是配置模板；本工作台不代理外部 `tools/call`，也不会对外部建模工具实施本项目的 dry-run 门禁。使用具有修改能力的外部工具时，应先在项目副本里验证。没有在目标 NX、PowerMill、FreeCAD 或 Fusion 宿主执行加工动作。

## 验证

```sh
python -m unittest discover -s tests -v
python -m unittest discover -s plugins/ug-cam-copilot/tests -v
```

安装 `.[mcp]` 后，`tests/test_mcp.py` 使用官方 Python SDK 启动真实 stdio 子进程，验证工具发现、两种产品分析、NX JSONL 回读、错误返回、目录和配置导出。未安装 extra 时，仅跳过真实 MCP 传输测试。另有 NX 多会话 JSONL 回归测试，修复了旧代码将会话事件列表误当作事件读取的问题。

外部 STEP 冒烟测试（2026-09-07，macOS arm64，Node 26.0.0，`cad-mcp-server@0.6.1`）：通过官方 MCP Python 客户端调用 `inspect_step`，读取 [CadQuery 的公开测试样件](https://github.com/CadQuery/cadquery/blob/master/tests/testdata/red_cube_blue_cylinder.step)（blob `4ed12421d740787953eeee57e717d7f553473ed6`）。返回 2 个实体、9 个面、15 条边，包围尺寸约 25 × 10 × 10 mm，体积 1785.398163 mm³，`is_valid=true`。这仅验证了安装、协议和一次真实几何查询，未验证全部测量工具或 CAM 安全性。临时依赖、样件及原始结果位于 git 忽略的 `build/cad-mcp-smoke/`；未向仓库加入上游代码或客户模型。
