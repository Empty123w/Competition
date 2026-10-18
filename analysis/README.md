# 空压站多机协同智能调度 Agent — 分析与交付物

本目录存放「空压站多机协同智能调度 Agent」赛题的阶段 1 分析成果。原始数据位于仓库根目录 `data/`。

## 目录结构

```
analysis/
├── README.md                      本文件
├── scripts/
│   └── profile_data.py            通用数据体检工具
├── reports/
│   ├── data_profile.json          机器可读的全量数据画像
│   └── phase1/                    阶段 1 交付物
│       ├── README.md              交付物索引
│       ├── A_数据体检报告.md
│       ├── B_数据字段字典.md
│       ├── C_可用指标清单.md
│       ├── D_不可直接验证指标清单.md
│       └── E_项目总体架构图.md
└── docs/
    └── 项目记忆.md                项目知识（数据陷阱、口径、运维要点）
```

## 数据来源

- 原始数据：仓库根目录 `data/`（8 类数据，2026-03-12 ~ 2026-09-12，1 分钟采样）
- 赛题说明：仓库根目录 `赛题详情.docx`

## 运行数据体检

```bash
# 在本仓库根目录执行
WORKSPACE="$PWD" python3 analysis/scripts/profile_data.py

# 输出: analysis/reports/data_profile.json（按当前工作空间）
```

依赖：

```bash
pip install --break-system-packages pandas numpy openpyxl scipy
```

## 阶段 1 结论摘要

- 7 张序列表各 264,961 行，1 分钟采样，时间戳逐行完全对齐、无缺口。
- 可用真实数据直接验证：负荷预测、能效/压力基准、运行状态识别、故障事件检测（事件级）、累计运行/保养到期提醒、时延类指标。
- 仍缺：喘振边界与性能曲线、多类故障与 RUL 标签、加卸载、预警记录；相关"准确率"类指标需以"机理 + 无监督 + 模拟"补足并显式声明。

详见 `reports/phase1/`。
