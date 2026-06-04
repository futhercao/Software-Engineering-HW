# 智能充电桩调度计费系统 · 第二次作业

面向对象的智能充电桩调度计费系统：一套**可运行的前后端一体应用**（`frontend/`），
以及由真实代码组织、严格按学院模板生成的**《概要设计》报告**（`assignment2_output/`）。

## 目录结构

```
第二次作业官方/
├─ README.md                       本说明
├─ build_report.py                 《概要设计》报告生成器（python-docx）
├─ report_diagrams.py              全部 UML / 界面图源（PlantUML / salt）
├─ 2025年第二次作业模版.docx        报告模版（生成时读取，继承其样式、封面与目录域）
│
├─ frontend/                       可运行的前后端一体系统
│   ├─ server.py                   纯标准库 HTTP 服务（默认 127.0.0.1:5173）
│   ├─ index.html / login.html     前端页面（登录/注册页 + 单页主界面）
│   ├─ app.js / styles.css         前端逻辑与样式
│   ├─ test_system.py              单元测试（15 个用例）
│   ├─ core/                       面向对象核心，按层组织：
│   │     controllers · services · domain · repositories
│   │     fault_strategies · support · system（应用门面）
│   ├─ assets/                     静态资源（station.svg）
│   └─ data/state.json             运行状态快照（JSON 持久化，运行时自动生成/更新）
│
├─ assignment2_output/             交付成果
│   ├─ 智能充电桩调度计费系统_概要设计.docx
│   └─ assets_report/              报告所用 UML/界面 PNG 及 .puml 源（每次生成自动重渲染）
│
└─ docs/                           需求与参考资料
    ├─ 智能充电桩调度计费系统详细需求.pdf
    ├─ 第二次作业要求.pdf
    ├─ 2025年第二次作业模版.pdf
    ├─ 软件设计课件.pdf
    └─ 面向对象设计课件.pdf
```

## 运行系统

```bash
cd frontend
python server.py          # 默认端口 5173；python server.py 8080 可改端口
```

浏览器打开 **http://127.0.0.1:5173/** ，进入登录页：

- **用户登录**：车辆 ID `京A-88888` / 密码 `123456`（开箱演示账户）
- **管理员**：工号 `admin` / 密码 `admin123456`
- **注册新用户**：填写车辆 ID、用户名、电池容量、密码即可自助开户

> 系统只设「用户客户端」「管理员客户端」两类角色（服务器端为同一服务），与需求一致。
> 「查看充电桩状态」每 5 秒定时刷新。

### 运行单元测试

```bash
cd frontend
python -m unittest test_system -v     # 15 个用例
```

## 重新生成《概要设计》报告

报告内容（架构、界面、动态/静态结构、调度计费、工作量）全部依据 `frontend/core` 的真实代码组织；
所有 UML 图由 PlantUML 现场渲染。

**依赖**

- Python 包：`python-docx`、`Pillow`
- `java`（渲染 PlantUML）
- `plantuml.jar` 放在系统临时目录 `%TEMP%\plantuml.jar`

**命令**（在项目根目录执行）

```bash
python build_report.py              # 渲染全部 UML 图 + 生成 docx
python build_report.py --no-render  # 跳过渲染，仅用现有 PNG 重建 docx
```

产物输出到 `assignment2_output/`。

## 报告与模板的关系（严格继承模板格式）

`build_report.py` 以 `2025年第二次作业模版.docx` 为基底打开，保留其封面与目录域，
仅替换正文，因此**字体与段落格式严格继承模板**：

| 元素 | 套用样式 | 字体 / 格式（来自模板） |
| --- | --- | --- |
| 正文 | `正文1` | 仿宋 · 小四(12pt)、1.5 倍行距、首行缩进 |
| 章 / 节 / 小节标题 | `Heading 1/2/3` | 微软雅黑、16/14pt 加粗、模板自动编号与段间距 |
| 图 / 表题注 | `Caption` | 黑体 · 五号(10pt) |
| 封面字段 | 原样保留模板 run | 模板封面字体 |

正文段落不写任何直接字体覆盖，完全由样式决定；表格内文为便于排版采用仿宋·五号并加边框底纹。
修改报告内容请改 `build_report.py`（文字/表格）或 `report_diagrams.py`（图），再重新生成，
以保证「框架结构与最终代码一致」且格式与模板对齐。
# Software-Engineering-HW
