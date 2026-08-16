# GPU 运行准备（当前禁止执行）

本目录当前只有 CPU 生成的断点队列和待机脚本。`install_*.sh`、`run_*.sh` 同时要求环境变量 `GRAPHIC_REASONING_GPU_READY=1` 和调用方指定运行目录中的 `GPU_READY` 文件；任一缺失都会在下载、安装或模型加载前退出 64。`run_starvector_candidate.sh` 还要求一个安装后根据实际官方仓库版本检查过的可执行 runner，避免在尚未安装源码时臆造 Python API。

主窗口明确通知工作站空闲后，才允许在工作站独立运行目录创建 `GPU_READY`。模型输出必须逐格保存原始 SVG、模型与参数、输出哈希；随后依次运行 `candidate_ingest.py`、`verify_candidate.py`、`refine_geometry.py`、`verify_topology.py` 和 `acceptance_gate.py`。失败或中断只更新该 checkpoint，不能跳过门禁或清空失败状态。模型原始 SVG 永远只是候选，不能直接写入正式 `structured.json`。

首轮顺序固定为：直线+曲线、纯直线、圆、闭合多边形、网格各一格；只有代表格通过后才继续同题剩余面板。工作站忙碌期间不得创建 `GPU_READY`。
