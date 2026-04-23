# 架构设计

## 1. 概述

本项目是一个面向服装车缝产线的桌面排产工具，技术形态为 `PySide6` 桌面应用。系统围绕三类核心业务对象工作：

- 款式工序：来自标准工时表，描述某个款式的具体工序、工时、单价、前后依赖。
- 标准工序库：来自员工技能矩阵和人工补录，是跨款式复用的标准能力定义。
- 员工技能：描述员工是否掌握某个标准工序，以及对应效率。

系统核心目标是将“款式工序”先映射到“标准工序库”，再结合“员工技能”完成排产、工位布局和报告导出。

## 2. 总体分层

代码分为四层：

- `ui/`
  - 桌面界面、交互事件、线程调度、布局拖拽。
- `core/`
  - 业务核心：导入校验、SQLite 仓储、LLM 映射、排产求解、距离计算、报表导出。
- `data/`
  - 领域模型定义，承担跨模块的数据契约。
- `agent/`
  - LangGraph 形式的流程编排层，当前更像对核心流程的脚本化封装，而不是主 UI 执行路径。

## 3. 主要模块职责

### 3.1 启动与配置

- [main.py](/abs/path/C:/Users/htpan/Desktop/langgraph-basic/main.py:1)
  - 负责启动 `QApplication`、加载配置、初始化主窗口。
- [core/config.py](/abs/path/C:/Users/htpan/Desktop/langgraph-basic/core/config.py:1)
  - 从 `config.toml` 和 `.env` 组装配置对象。
  - 设计上使用 dataclass 而非复杂配置框架，保持依赖简单。

### 3.2 领域模型

- [data/models.py](/abs/path/C:/Users/htpan/Desktop/langgraph-basic/data/models.py:1)
  - 定义 `Process`、`SkillProcess`、`Employee`、`MappingRecord`、`AssignmentResult` 等跨层数据结构。
  - 模型偏“扁平 DTO”，服务于 UI、仓储和算法模块共享。

### 3.3 数据导入与校验

- [core/ingestion.py](/abs/path/C:/Users/htpan/Desktop/langgraph-basic/core/ingestion.py:1)
  - 读取工时表和技能矩阵。
  - 对缺列、重复工序、非法效率、非法价格等进行校验。
  - 将 Excel/CSV 转换为领域对象。
  - 额外负责推导工序前驱关系。

设计特点：

- 工时表中的工序主标识是 `(style_no, process_no)`。
- 同时生成：
  - `identity_hash`：标识“逻辑上同一条工序”。
  - `version_hash`：标识工时/价格/顺序变更后的版本。
- 技能矩阵列头即导入态的标准工序定义。

### 3.4 仓储与持久化

- [core/db.py](/abs/path/C:/Users/htpan/Desktop/langgraph-basic/core/db.py:1)
  - 使用本地 SQLite 保存所有业务状态。
  - 启动时自动建表、补字段、迁移旧数据。

核心表：

- `processes`
  - 存储款式工序快照。
- `employees`
  - 存储员工基本信息。
- `skill_processes`
  - 存储标准工序库。
- `employee_skills`
  - 存储员工对标准工序的掌握和效率。
- `mapping_records`
  - 存储某个款式下的工序映射结果。
- `mapping_knowledge`
  - 存储跨款式复用的映射知识库。
- `layout_state`
  - 存储最新一次排产布局结果。

设计特点：

- `mapping_records` 是“某款式某次确认后的当前事实”。
- `mapping_knowledge` 是“跨款式的经验沉淀”。
- 两者分离后，既能保留款式内人工修正，又能复用历史知识。
- `mapping_fts` 用于全文检索已有映射知识。

### 3.5 工序映射

- [core/mapper.py](/abs/path/C:/Users/htpan/Desktop/langgraph-basic/core/mapper.py:1)
  - 把款式工序映射到标准工序库。

映射优先级：

1. 当前款式已确认映射
2. 历史知识库命中
3. 调用 DeepSeek 批量映射
4. 本地相似度回退

设计特点：

- DeepSeek 请求按 batch 执行，并支持并发。
- 为控制 prompt 大小，会先基于本地相似度裁剪候选标准工序。
- 即使 LLM 可用，也保留纯本地 fallback，确保系统在离线/无 key 时仍可工作。
- 自动确认的条件较严格：必须命中已有标准工序，且置信度达到阈值，且不是“建议新工序”。

### 3.6 人工确认

- [ui/dialogs/mapping_dialog.py](/abs/path/C:/Users/htpan/Desktop/langgraph-basic/ui/dialogs/mapping_dialog.py:1)
  - 提供工序映射人工复核界面。
- [ui/main_window.py](/abs/path/C:/Users/htpan/Desktop/langgraph-basic/ui/main_window.py:1)
  - 在“标准工时库”页驱动映射复核、保存结果、补建新标准工序。

设计特点：

- 人工确认不仅修正映射结果，还会触发新标准工序写入 `skill_processes`。
- 新增标准工序后，系统要求用户回到“技能矩阵”页补充员工掌握情况。

### 3.7 排产求解

- [core/balancer.py](/abs/path/C:/Users/htpan/Desktop/langgraph-basic/core/balancer.py:1)
  - 基于 `PuLP` + `CBC` 进行工位分配。

输入：

- 款式工序
- 员工列表
- 工序到标准工序的映射

输出：

- 每个员工对应一个工位
- 每个工位承接的工序集合
- 平衡率、节拍、总有效工时等指标

建模约束：

- 每道工序必须分配给且仅分配给一个工位。
- 每个员工至少分到一条工序。
- 每个工位负荷不超过统一节拍。
- 工序前驱约束通过“前驱工位编号 <= 后继工位编号”表达。

降级策略：

- 问题规模较大时，跳过精确 CBC，直接走贪心分配。
- CBC 未得到可用解时，也会回退到贪心算法。

### 3.8 布局与距离

- [core/distance_calculator.py](/abs/path/C:/Users/htpan/Desktop/langgraph-basic/core/distance_calculator.py:1)
  - 负责默认工序流和曼哈顿距离计算。
- [ui/graphics_view.py](/abs/path/C:/Users/htpan/Desktop/langgraph-basic/ui/graphics_view.py:1)
  - 将工位渲染为可拖拽图元，并在拖动后实时重算距离。

设计特点：

- 业务结果和视图位置共用同一组 `Station` 对象。
- UI 拖拽后直接回写 `station.x/y`，避免额外 ViewModel 同步层。

### 3.9 报告导出

- [core/exporter.py](/abs/path/C:/Users/htpan/Desktop/langgraph-basic/core/exporter.py:1)
  - 导出 Excel 和 PDF。

导出内容：

- 排产结果
- 映射确认摘要
- 导入异常
- 关键指标

### 3.10 后台线程

- [ui/workers.py](/abs/path/C:/Users/htpan/Desktop/langgraph-basic/ui/workers.py:1)
  - 使用 `QThread + QObject` 将长任务放到后台。

设计原则：

- Worker 不直接触碰 UI。
- 所有 UI 变更通过 Qt Signal 回到主线程。

## 4. 关键业务数据流

### 4.1 技能矩阵导入

1. 用户导入技能矩阵 CSV
2. `load_skill_matrix()` 解析为：
   - 员工
   - 标准工序
   - 员工技能
   - 校验问题
3. `Repository.replace_employee_import()` 持久化
4. UI 刷新员工、技能、未覆盖标准工序

### 4.2 标准工时导入与映射

1. 用户导入工时表
2. `load_processes()` 解析工序和前驱关系
3. `Repository.save_style_processes()` 保存款式工序
4. `ProcessMapper.map_processes()` 生成映射候选
5. 人工确认后：
   - 保存 `mapping_records`
   - 更新 `mapping_knowledge`
   - 必要时创建新标准工序

### 4.3 排产

1. 校验款式已映射且人工确认完成
2. 校验所有最终标准工序至少被一名员工掌握
3. 调用 `PulpBalancer.balance()`
4. 布局模块生成默认工位坐标
5. 计算总搬运距离
6. 用户可拖拽调整工位位置并实时重算

## 5. 核心设计决策

### 5.1 采用 SQLite 本地持久化

原因：

- 桌面单机使用，无需额外服务部署。
- 方便保存导入结果、映射历史和布局状态。
- 支持本地迁移与离线运行。

### 5.2 采用“款式映射”与“知识库映射”双层存储

原因：

- 同一描述在不同款式可能有不同决策，需要保留款式上下文。
- 历史人工确认又应尽量复用，减少重复确认工作。

### 5.3 LLM 只做建议，不做最终事实源

原因：

- 映射结果会直接影响排产质量。
- 系统保留人工确认和本地 fallback，避免把生产决策完全交给模型。

### 5.4 排产采用“精确求解 + 贪心回退”

原因：

- 小中规模问题需要较好结果。
- 桌面交互又要求响应时间可接受。
- 因此需要一个总能返回结果的兜底策略。

## 6. 已知约束与风险

- UI 文本存在历史编码问题，部分中文字符串已损坏，影响可维护性但不一定影响核心逻辑。
- `agent/` 目录中的 LangGraph 编排与当前主桌面流并非完全一致，存在演进中接口漂移风险。
- 线程相关定时器/UI 更新边界需要持续注意，Qt 主线程约束较严格。
- 当前排产模型不支持“员工空闲”或“一个工序拆分给多名员工”的复杂场景。

## 7. 建议的后续演进方向

- 统一并修复源码文件编码，避免中文常量继续损坏。
- 为 `core/` 层补自动化测试，重点覆盖：
  - 导入校验
  - 映射优先级
  - 排产约束与回退路径
- 明确 `agent/` 是否仍为正式架构一部分；如保留，应与 `Repository.save_import()` 等接口重新对齐。
- 将 UI 事件逻辑继续下沉，减少 `MainWindow` 中的流程编排密度。
