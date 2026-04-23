# 服装车缝生产线车位排产智能体

PySide6 桌面端应用，用于标准工时表与员工技能矩阵导入、DeepSeek 工序技能映射、Human-in-the-Loop 确认、PuLP 排产优化、车位拖拽布局和报告导出。

## 环境

项目统一使用 `uv`：

```powershell
uv sync
uv run python main.py
```

如果需要调用 DeepSeek，请复制 `.env.example` 为 `.env` 并填写：

```powershell
DEEPSEEK_API_KEY=your_deepseek_api_key_here
```

未配置 API Key 时，系统会用本地相似度生成低置信度候选，仍然可以演示导入、人工确认、排产和布局。

## 输入模板

工时表必填列：

- 款式编号
- 部件
- 工序号
- 工序描述
- 标准时间
- 标准单价

技能矩阵必须是 UTF-8 CSV，姓名列可为 `姓名`、`员工`、`员工姓名` 或 `名称`。技能效率支持 `0.8` 或 `80%`。

## 核心流程

1. 导入并校验工时表和技能矩阵。
2. 调用 DeepSeek 智能映射工序到技能。
3. 人工确认所有低置信度或新增技能项。
4. 映射 100% 确认后，排产按钮解锁。
5. 运行 PuLP 排产，固定工位数等于员工数，员工不允许空闲。
6. 拖拽工位实时更新曼哈顿搬运距离。
7. 导出 Excel 或 PDF 报告。

## 打包

```powershell
build.bat
```
