// ---- 会话与角色（登录信息存于 sessionStorage，未登录跳转登录页） ----
const session = {
  role: sessionStorage.getItem("role") || "",
  carId: sessionStorage.getItem("carId") || "",
  userName: sessionStorage.getItem("userName") || "",
  username: sessionStorage.getItem("username") || "",
};

const ROLE_TABS = {
  user: ["user", "mybills"],
  admin: ["admin", "dispatch"],
};
const ROLE_NAMES = { user: "用户", admin: "管理员" };

const titles = {
  user: ["用户客户端", "提交充电申请、查看队列与控制充电"],
  mybills: ["我的账单", "查看本车的账单汇总与充电详单"],
  admin: ["管理员客户端", "启动/关闭充电桩、监控队列、处理单桩故障与报表"],
  dispatch: ["调度模拟", "观察等候区叫号、最短完成时长与故障重排"],
};

let state = null;
let reportRows = [];

function $(id) {
  return document.getElementById(id);
}

function money(value) {
  return `￥${Number(value || 0).toFixed(2)}`;
}

function numberText(value, digits = 1) {
  return Number(value || 0).toFixed(digits);
}

function modeName(mode) {
  return mode === "FAST" ? "快充" : "慢充";
}

function showToast(message) {
  const toast = $("toast");
  toast.textContent = message;
  toast.classList.add("show");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("show"), 2400);
}

function encodeQuery(params) {
  return Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
    .join("&");
}

async function apiGet(path, params = {}) {
  const query = encodeQuery(params);
  const response = await fetch(`${path}${query ? `?${query}` : ""}`);
  const payload = await response.json();
  applyApiPayload(payload);
  return payload.data;
}

async function apiPost(path, body = {}) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  applyApiPayload(payload);
  return payload.data;
}

function applyApiPayload(payload) {
  if (payload.state) {
    state = payload.state;
    render();
  }
  if (!payload.ok) {
    showToast(payload.error || "操作失败");
    throw new Error(payload.error || "request failed");
  }
}

// 所有用户端查询均绑定登录车辆
function currentCarId() {
  return session.carId;
}

function requestById(id) {
  return state?.requests.find((request) => request.id === id) || null;
}

function activeRequestForCar(carId) {
  return state?.requests.find((request) => request.carId === carId && !["ENDED", "CANCELLED"].includes(request.state)) || null;
}

// 账单/详单仅限登录车辆自身（对应 Request_Bill(carId,date) / Request_DetailedList(billId)）
function filteredBills() {
  const carId = currentCarId();
  const date = $("billDate") ? $("billDate").value : "";
  return (state?.bills || []).filter((bill) => bill.carId === carId && (!date || bill.date === date));
}

function filteredDetails() {
  const carId = currentCarId();
  const billId = $("detailBillId") ? $("detailBillId").value.trim() : "";
  return (state?.details || []).filter((detail) => detail.carId === carId && (!billId || detail.billId === billId));
}

// ---- 用户：充电申请与控制（绑定登录车辆） ----
async function submitRequest() {
  await apiPost("/api/charging/request", {
    carId: currentCarId(),
    amount: Number($("requestAmount").value),
    mode: $("requestMode").value,
  });
  showToast("充电申请已提交");
}

async function modifyAmount() {
  await apiPost("/api/charging/modify-amount", {
    carId: currentCarId(),
    amount: Number($("requestAmount").value),
  });
  showToast("请求电量已修改");
}

async function modifyMode() {
  await apiPost("/api/charging/modify-mode", {
    carId: currentCarId(),
    mode: $("requestMode").value,
  });
  showToast("充电模式已修改");
}

async function cancelRequest() {
  await apiPost("/api/charging/cancel", { carId: currentCarId() });
  showToast("充电请求已取消");
}

async function queryState() {
  const result = await apiGet("/api/charging/car-state", { carId: currentCarId() });
  showToast(`${result.queueNum || "-"} 当前状态：${result.carState}，前车 ${result.beforeCount} 辆`);
}

async function startCharging() {
  const request = activeRequestForCar(currentCarId());
  await apiPost("/api/charging/start", {
    carId: currentCarId(),
    pileId: request?.pileId || "",
  });
  showToast("充电已开始");
}

async function endCharging() {
  await apiPost("/api/charging/end", { carId: currentCarId(), reason: "用户结束" });
  showToast("已结束充电并生成详单");
}

async function tick(minutes = 15) {
  await apiPost("/api/time/tick", { minutes });
  showToast(`系统时间已推进 ${minutes} 分钟`);
}

// ---- 管理员：计费、充电桩、故障、报表、调度 ----
function openRuleModal() {
  if (!state) return;
  $("rulePeak").value = state.rules.peak;
  $("ruleNormal").value = state.rules.normal;
  $("ruleValley").value = state.rules.valley;
  $("ruleService").value = state.rules.service;
  $("ruleModal").classList.remove("hidden");
}

function closeRuleModal() {
  $("ruleModal").classList.add("hidden");
}

async function saveRule() {
  const values = {
    peak: Number($("rulePeak").value),
    normal: Number($("ruleNormal").value),
    valley: Number($("ruleValley").value),
    service: Number($("ruleService").value),
  };
  if (Object.values(values).some((v) => !Number.isFinite(v) || v < 0)) {
    showToast("计费参数需为非负数字");
    return;
  }
  await apiPost("/api/rules", values);
  closeRuleModal();
  showToast("计费规则已更新");
}

async function setPilePower(pileId, powered) {
  await apiPost("/api/piles/power", { pileId, powered });
  showToast(`${pileId} ${powered ? "已启动" : "已关闭"}`);
}

async function markFault() {
  await apiPost("/api/fault", {
    pileId: $("faultPile").value,
    strategy: $("faultStrategy").value,
  });
  showToast("故障调度已执行");
}

async function recoverPile() {
  await apiPost("/api/recover", { pileId: $("faultPile").value });
  showToast("故障桩已恢复");
}

// ---- 用户：查看本车账单 / 详单 ----
async function queryBill() {
  await apiGet("/api/bills", {
    carId: currentCarId(),
    date: $("billDate") ? $("billDate").value : "",
  });
  showToast("账单已刷新");
}

async function queryDetail() {
  await apiGet("/api/details", {
    carId: currentCarId(),
    billId: $("detailBillId") ? $("detailBillId").value.trim() : "",
  });
  showToast("详单已刷新");
}

async function refreshReport() {
  const result = await apiGet("/api/reports", { period: $("reportPeriod").value });
  reportRows = result.reports || [];
  renderReports();
  showToast("报表已刷新");
}

async function addDemoVehicles() {
  await apiPost("/api/demo");
  showToast("测试车辆已加入");
}

async function dispatchWaiting() {
  await apiPost("/api/dispatch");
  showToast("已执行最短完成时长调度");
}

async function resetSystem() {
  await apiPost("/api/reset");
  reportRows = [];
  showToast("系统已重置");
}

function logout() {
  sessionStorage.clear();
  location.href = "./login.html";
}

function render() {
  if (!state) return;
  $("simTime").textContent = state.simTimeText.slice(-5);
  if ($("billDate") && !$("billDate").value) $("billDate").value = state.simTimeText.slice(0, 10);
  renderUser();
  renderPiles();
  renderQueues();
  renderBills();
  renderReports();
  renderDispatch();
  renderMiniPiles();
  renderLogs();
}

function renderUser() {
  const carId = session.carId;
  const account = (state.accounts && state.accounts[carId]) || null;
  const request = activeRequestForCar(carId);
  $("boundCar").textContent = carId || "-";
  $("boundUser").textContent = account ? account.userName : session.userName || "";
  $("accountState").textContent = account ? (account.active ? "已验证" : "待验证") : "未注册";
  $("requestTicket").textContent = request ? request.ticket : "未排队";
  $("requestTicket").className = `status-pill ${request ? "" : "muted"}`;
  $("carStateText").textContent = request ? request.stateText : "未入站";
  $("beforeCount").textContent = request ? request.beforeCount : 0;
  $("waitEstimate").textContent = request ? `${request.waitMinutes} 分钟` : "0 分钟";
  $("currentPile").textContent = request?.pileId || request?.session?.pileId || "-";

  if (request?.state === "CHARGING" && request.live) {
    $("chargedAmount").textContent = `${numberText(request.live.charged)} 度`;
    $("chargeDuration").textContent = `${request.live.elapsed} 分钟`;
    $("liveFee").textContent = money(request.live.fee.totalFee);
  } else {
    $("chargedAmount").textContent = "0.0 度";
    $("chargeDuration").textContent = "0 分钟";
    $("liveFee").textContent = "￥0.00";
  }
}

function renderPiles() {
  $("pileGrid").innerHTML = state.piles
    .map((pile) => {
      const active = requestById(pile.activeRequestId);
      const stateText = pile.state === "WORKING" ? "运行" : pile.state === "FAULT" ? "故障" : "关闭";
      const pillClass = pile.state === "FAULT" ? "warn" : pile.state === "OFF" ? "muted" : "";
      return `
        <article class="pile-card">
          <header>
            <h4>${pile.id} ${modeName(pile.mode)}</h4>
            <span class="status-pill ${pillClass}">${stateText}</span>
          </header>
          <dl>
            <dt>功率</dt><dd>${numberText(pile.power, 0)} 度/小时</dd>
            <dt>当前车辆</dt><dd>${active ? active.carId : "-"}</dd>
            <dt>队列</dt><dd>${pile.queue.length} 辆</dd>
            <dt>累计次数</dt><dd>${pile.totalChargeNum}</dd>
            <dt>累计时长</dt><dd>${pile.totalChargeTime} 分钟</dd>
            <dt>累计电量</dt><dd>${numberText(pile.totalCapacity)} 度</dd>
          </dl>
          <div class="pile-actions">
            <button data-pile="${pile.id}" data-action="on" type="button">启动</button>
            <button data-pile="${pile.id}" data-action="off" type="button">关闭</button>
          </div>
        </article>
      `;
    })
    .join("");

  $("faultPile").innerHTML = state.piles.map((pile) => `<option value="${pile.id}">${pile.id} ${modeName(pile.mode)}</option>`).join("");
  const fault = state.piles.find((pile) => pile.state === "FAULT");
  $("faultState").textContent = fault ? `${fault.id} 故障` : "无故障";
}

function renderQueues() {
  const rows = state.queueRows || [];
  $("queueSummary").textContent = `${rows.length} 条队列记录`;
  $("waitingCount").textContent = state.waitingCount;
  $("queueRows").innerHTML = rows.length
    ? rows
        .map(
          (row) => `
        <tr>
          <td>${row.queue}</td>
          <td>${row.carId} / ${row.ticket}<br><small>${row.state}</small></td>
          <td>${numberText(row.capacity)} 度</td>
          <td>${numberText(row.amount)} 度</td>
          <td>${row.waitTime} 分钟</td>
        </tr>`,
        )
        .join("")
    : `<tr><td class="empty" colspan="5">暂无队列车辆</td></tr>`;
}

function renderBills() {
  const bills = filteredBills();
  const total = bills.reduce(
    (sum, bill) => ({
      chargeAmount: sum.chargeAmount + bill.chargeAmount,
      chargeDuration: sum.chargeDuration + bill.chargeDuration,
      chargeFee: sum.chargeFee + bill.chargeFee,
      serviceFee: sum.serviceFee + bill.serviceFee,
      totalFee: sum.totalFee + bill.totalFee,
    }),
    { chargeAmount: 0, chargeDuration: 0, chargeFee: 0, serviceFee: 0, totalFee: 0 },
  );
  if ($("billBoundCar")) $("billBoundCar").textContent = currentCarId() || "-";
  $("billSummary").innerHTML = [
    ["充电电量", `${numberText(total.chargeAmount)} 度`],
    ["充电时长", `${total.chargeDuration} 分钟`],
    ["充电费用", money(total.chargeFee)],
    ["服务费用", money(total.serviceFee)],
    ["总费用", money(total.totalFee)],
  ]
    .map(([label, value]) => `<div><span>${label}</span><strong>${value}</strong></div>`)
    .join("");

  const details = filteredDetails();
  $("detailCount").textContent = `${details.length} 条`;
  $("detailRows").innerHTML = details.length
    ? details
        .map(
          (detail) => `
        <tr>
          <td>${detail.detailId}</td>
          <td>${detail.billId}</td>
          <td>${detail.carId}</td>
          <td>${detail.pileId}</td>
          <td>${numberText(detail.chargeAmount)} 度</td>
          <td>${detail.chargeDuration} 分钟</td>
          <td>${detail.startTime}</td>
          <td>${detail.endTime}</td>
          <td>${money(detail.chargeFee)}</td>
          <td>${money(detail.serviceFee)}</td>
          <td>${money(detail.totalFee)}</td>
          <td>${detail.generatedAt}</td>
        </tr>`,
        )
        .join("")
    : `<tr><td class="empty" colspan="12">暂无详单</td></tr>`;
}

function renderReports() {
  // 默认「日」报表始终用最新 state.reports 实时渲染；周/月为按需查询，用上次刷新结果。
  const period = $("reportPeriod") ? $("reportPeriod").value : "day";
  const rows = period === "day" ? state.reports || [] : reportRows;
  $("reportRows").innerHTML = rows.length
    ? rows
        .map(
          (row) => `
        <tr>
          <td>${row.period}</td>
          <td>${row.pileId}</td>
          <td>${row.totalChargeNum}</td>
          <td>${row.totalChargeTime} 分钟</td>
          <td>${numberText(row.totalCapacity)} 度</td>
          <td>${money(row.totalChargeFee)}</td>
          <td>${money(row.totalServiceFee)}</td>
          <td>${money(row.totalFee)}</td>
        </tr>`,
        )
        .join("")
    : `<tr><td class="empty" colspan="8">暂无报表数据</td></tr>`;
}

function renderDispatch() {
  const lanes = [
    ["等候区", state.requests.filter((request) => request.state === "WAITING_AREA")],
    ...state.piles.map((pile) => {
      const active = requestById(pile.activeRequestId);
      const queued = pile.queue.map(requestById).filter(Boolean);
      return [pile.id, active ? [active, ...queued] : queued];
    }),
  ];
  $("dispatchLane").innerHTML = lanes
    .map(
      ([label, items]) => `
      <div class="lane-row">
        <div class="lane-label">${label}</div>
        <div class="vehicle-list">
          ${
            items.length
              ? items
                  .map(
                    (request) =>
                      `<span class="vehicle-chip ${request.mode === "FAST" ? "fast" : "trickle"}">${request.ticket}<small>${numberText(request.amount)}度</small></span>`,
                  )
                  .join("")
              : `<span class="empty">空</span>`
          }
        </div>
      </div>`,
    )
    .join("");
}

function renderMiniPiles() {
  $("miniPiles").innerHTML = state.piles
    .map((pile) => `<div class="mini-pile ${pile.state.toLowerCase()}">${pile.id}</div>`)
    .join("");
}

function renderLogs() {
  $("eventLog").innerHTML = state.logs.length ? state.logs.map((item) => `<li>${item}</li>`).join("") : `<li class="empty">暂无事件</li>`;
}

// ---- 角色门禁：只显示并激活本角色允许的标签页 ----
function activateTab(tab) {
  document.querySelectorAll(".tab").forEach((button) => button.classList.toggle("is-active", button.dataset.tab === tab));
  document.querySelectorAll(".view").forEach((view) => view.classList.remove("is-active"));
  $(`view-${tab}`).classList.add("is-active");
  const [title, subtitle] = titles[tab];
  $("viewTitle").textContent = title;
  $("viewSubtitle").textContent = subtitle;
}

function applyRole() {
  const allowed = ROLE_TABS[session.role] || ["user"];
  document.querySelectorAll(".tab").forEach((button) => {
    button.classList.toggle("hidden", !allowed.includes(button.dataset.tab));
  });
  const tag = session.role === "user" ? session.carId : session.username;
  $("sessionLabel").textContent = `${ROLE_NAMES[session.role] || "未登录"}${tag ? "·" + tag : ""}`;
  activateTab(allowed[0]);
}

// 异步操作期间禁用被点按钮，避免重复提交（错误已由 applyApiPayload 以 toast 反馈）
function bindClick(id, handler) {
  const el = $(id);
  if (!el) return;
  el.addEventListener("click", async () => {
    if (el.disabled) return;
    el.disabled = true;
    try {
      await handler();
    } catch (err) {
      /* 已通过 toast 提示，吞掉以避免未处理的 promise 拒绝 */
    } finally {
      el.disabled = false;
    }
  });
}

function bindEvents() {
  document.querySelectorAll(".tab").forEach((button) => {
    button.addEventListener("click", () => activateTab(button.dataset.tab));
  });

  $("logoutBtn").addEventListener("click", logout);
  bindClick("tickBtn", () => tick(Number($("tickMinutes").value) || 15));
  bindClick("submitRequestBtn", submitRequest);
  bindClick("modifyAmountBtn", modifyAmount);
  bindClick("modifyModeBtn", modifyMode);
  bindClick("cancelRequestBtn", cancelRequest);
  bindClick("queryStateBtn", queryState);
  bindClick("startChargeBtn", startCharging);
  bindClick("endChargeBtn", endCharging);
  bindClick("refreshAdminBtn", () => apiGet("/api/state"));
  $("setRuleBtn").addEventListener("click", openRuleModal);
  $("ruleCancelBtn").addEventListener("click", closeRuleModal);
  bindClick("ruleSaveBtn", saveRule);
  $("ruleModal").addEventListener("click", (event) => {
    if (event.target === $("ruleModal")) closeRuleModal();
  });
  bindClick("faultBtn", markFault);
  bindClick("recoverBtn", recoverPile);
  bindClick("queryBillBtn", queryBill);
  bindClick("queryDetailBtn", queryDetail);
  bindClick("refreshReportBtn", refreshReport);
  $("reportPeriod").addEventListener("change", () => refreshReport().catch(() => {}));
  bindClick("addDemoBtn", addDemoVehicles);
  bindClick("dispatchBtn", dispatchWaiting);
  bindClick("resetBtn", resetSystem);
  bindClick("clearLogBtn", () => apiPost("/api/logs/clear"));
  $("pileGrid").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-pile]");
    if (!button || button.disabled) return;
    button.disabled = true;
    setPilePower(button.dataset.pile, button.dataset.action === "on")
      .catch(() => {})
      .finally(() => {
        button.disabled = false;
      });
  });
}

// ---- 启动：未登录跳转登录页，否则按角色装配并拉取状态 ----
if (!session.role) {
  location.href = "./login.html";
} else {
  bindEvents();
  applyRole();
  apiGet("/api/state").catch(() => showToast("无法连接后端服务"));
  // 定时刷新：满足需求「查看充电桩状态要求定时刷新」，并保持队列/账单/详单等视图实时
  window.setInterval(() => apiGet("/api/state").catch(() => {}), 5000);
}
