# 智能充电桩调度计费系统 · 第二次作业

面向对象的智能充电桩调度计费系统：一套**可运行的前后端一体应用**（`frontend/`），
以及由真实代码组织、严格按学院模板生成的**《概要设计》报告**（`assignment2_output/`）。

## 目录结构

```
第二次作业官方/
├─ README.md                       本说明
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
├─ report/             交付成果
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

# Software-Engineering-HW
