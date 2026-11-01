/* 세무사 검토실 — 고객이 맡긴 건을 보고 AI 전략 초안을 고쳐 경정청구를 낸다.
 *
 * 흐름: 목록 → AI 초안 검토(항목별 채택·보류·제외, 청구 금액, 청구 이유) → 저장 → 제출(가짜 홈택스·위택스)
 *       → 세무서 결과 기록 → 고객 성공보수 결제 → 정산 탭
 * 접근 키는 X-Advisor-Key 헤더로 보낸다 (체험판 기본값 demo-advisor).
 */
(() => {
  "use strict";

  const KEY_STORE = "advisor-key";
  const $ = (sel, root = document) => root.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const won = (n) => (n == null ? "-" : `${Number(n).toLocaleString("ko-KR")}원`);
  const day = (iso) => (iso ? String(iso).slice(0, 10) : "-");
  const list = $("#list"), detail = $("#detail"), layout = $("#layout");

  let key = "";
  try { key = localStorage.getItem(KEY_STORE) || ""; } catch { /* 저장소를 못 쓰면 매번 입력 */ }
  let tab = "cases", selected = null, current = null, rows = [];

  const STATUS_BADGE = {
    "신청접수": "warn", "세무사검토": "", "청구제출": "", "환급결정": "ok", "불인용": "danger", "정산완료": "gray",
  };
  const badge = (st) => `<span class="badge ${STATUS_BADGE[st] ?? ""}">${esc(st)}</span>`;

  let toastTimer;
  function toast(msg) {
    const t = $("#toast"); t.textContent = msg; t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), 3000);
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      method: opts.method || "GET",
      headers: { "X-Advisor-Key": key, ...(opts.body ? { "Content-Type": "application/json" } : {}) },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (res.status === 401) { logout(); throw new Error("접근 키를 다시 입력해 주세요"); }
    if (!res.ok) {
      let msg = `오류 (${res.status})`;
      try { const j = await res.json(); if (typeof j.detail === "string") msg = j.detail; } catch { /* 무시 */ }
      throw new Error(msg);
    }
    return res.json();
  }

  function logout() {
    key = "";
    try { localStorage.removeItem(KEY_STORE); } catch { /* 무시 */ }
    showLogin();
  }

  function showLogin() {
    layout.classList.add("wide");
    $("#who").textContent = "";
    detail.innerHTML = `
      <form class="card login" id="login">
        <h1>세무사 검토실</h1>
        <p class="muted small">제휴 세무사 접근 키를 입력하세요. 체험판 키는 <code>demo-advisor</code>입니다.</p>
        <label class="f">접근 키<input type="password" id="k" autocomplete="current-password" required></label>
        <p><button class="btn">들어가기</button></p>
      </form>`;
    $("#login").onsubmit = (e) => {
      e.preventDefault();
      key = $("#k").value.trim();
      try { localStorage.setItem(KEY_STORE, key); } catch { /* 무시 */ }
      start();
    };
  }

  document.querySelectorAll(".tab").forEach((b) => (b.onclick = () => {
    tab = b.dataset.tab;
    document.querySelectorAll(".tab").forEach((x) => x.setAttribute("aria-pressed", x === b));
    if (key) start();
  }));

  async function start() {
    if (!key) { showLogin(); return; }
    try {
      if (tab === "billing") { await renderBilling(); return; }
      layout.classList.remove("wide");
      await loadList();
      if (selected) await openCase(selected);
      else detail.innerHTML = `<p class="muted">왼쪽에서 건을 고르세요.</p>`;
    } catch (e) { toast(e.message); }
  }

  // ---------- 목록 ----------
  async function loadList() {
    rows = await api("/advisor/cases");
    const info = await fetch("/mandate/info").then((r) => r.json()).catch(() => null);
    if (info) $("#who").textContent = `${info.agent.office} · ${info.agent.name}`;
    if (!rows.length) {
      list.innerHTML = `<div class="empty">아직 맡겨진 건이 없습니다.<br><span class="small">고객 화면(<a href="/">/</a>)에서 진단 후 "세무사에게 맡기기"를 누르면 여기 나타납니다.</span></div>`;
      return;
    }
    list.innerHTML = rows.map((r) => `
      <button type="button" class="row" data-id="${r.id}" aria-current="${r.id === selected}">
        <div class="top"><b>${esc(r.name)}</b>${badge(r.status)}</div>
        <div class="sub">항목 ${r.findings}건 · 추정 ${won(r.estimated_refund)}</div>
        <div class="sub">마감 ${day(r.nearest_deadline)} · 신청 ${day(r.requested_at)}${r.ai_error ? ` · <span style="color:var(--danger)">AI 실패</span>` : ""}</div>
      </button>`).join("");
    list.querySelectorAll(".row").forEach((b) => (b.onclick = () => openCase(b.dataset.id)));
  }

  // ---------- 상세 ----------
  async function openCase(id) {
    selected = id;
    list.querySelectorAll(".row").forEach((b) => b.setAttribute("aria-current", b.dataset.id === id));
    current = await api(`/advisor/cases/${id}`);
    renderCase();
    detail.focus({ preventScroll: true });
  }

  const editable = () => ["신청접수", "세무사검토"].includes(current.status);

  function renderCase() {
    const c = current, a = c.applicant, s = c.strategy, rv = c.review;
    const props = Object.fromEntries((c.data?.properties || []).map((p) => [p.id, p]));
    const addr = (id) => (props[id]?.address || id || "").split(" (")[0];
    const reviews = rv?.items || [];

    detail.innerHTML = `
      <h1>${esc(a.name)} ${badge(c.status)}</h1>
      <p class="muted small">접수 ${day(c.created_at)} · 신청 ${day(c.requested_at)} · 수수료 약정 ${c.fee_agreed_at ? "동의" : "-"}</p>

      <div class="grid2">
        <div class="card">
          <h3>고객</h3>
          <dl class="kv" style="margin-top:8px">
            <dt>생년월일</dt><dd>${esc(a.birth_date)}</dd>
            <dt>휴대폰</dt><dd>${esc(a.phone)}</dd>
            <dt>검토 희망</dt><dd><b>${esc(a.concern || "없음")}</b></dd>
            <dt>위임장</dt><dd>${c.mandate ? `서명 ${day(c.mandate.signed_at)}` : "없음"}</dd>
          </dl>
        </div>
        <div class="card">
          <h3>자동 발급 서류</h3>
          <ul style="margin:8px 0 0;padding-left:18px">
            ${c.assets.map((x) => `<li>${esc(x.address)}${x.property ? `<br><span class="small muted">${x.documents.map(esc).join(" · ")}${x.owner_name_matches === false ? " · 소유자 불일치" : ""}</span>` : `<br><span class="small" style="color:var(--danger)">${esc(x.error)}</span>`}</li>`).join("")}
          </ul>
        </div>
      </div>

      ${c.data ? `
      <h2>홈택스 양도소득세 신고 내역</h2>
      <div class="card" style="padding:0;overflow-x:auto">
        <table>
          <thead><tr><th>부동산</th><th>양도일</th><th class="num">양도가액</th><th class="num">취득가액</th><th class="num">필요경비</th><th>장특공</th><th>특이</th><th class="num">납부세액</th></tr></thead>
          <tbody>${c.data.capital_gains_returns.map((r) => `
            <tr><td>${esc(addr(r.property_id))}</td><td>${r.transfer_date}</td><td class="num">${won(r.transfer_price)}</td>
            <td class="num">${won(r.acquisition_price)}${r.used_converted_price ? " (환산)" : ""}</td><td class="num">${won(r.necessary_expenses)}</td>
            <td>${Math.round(r.long_term_deduction_rate * 100)}%</td>
            <td>${[r.exemption_applied && "비과세", r.non_business_land_surcharge && "비사업용 중과"].filter(Boolean).join(", ") || "-"}</td>
            <td class="num">${won(r.tax_paid)}</td></tr>`).join("")}</tbody>
        </table>
      </div>
      <div class="grid2">
        <div class="card"><h3>위택스 지방세 납부</h3><ul style="margin:8px 0 0;padding-left:18px">${c.data.local_tax_payments.map((p) => `<li>${esc(p.tax_type)} ${won(p.amount)} · ${p.paid_on} · ${esc(addr(p.property_id))}</li>`).join("") || "<li>없음</li>"}</ul></div>
        <div class="card"><h3>주민등록초본 전입 이력</h3><ul style="margin:8px 0 0;padding-left:18px">${c.data.residences.map((r) => `<li>${r.moved_in} ~ ${r.moved_out || "현재"} · ${esc(r.address)}</li>`).join("")}</ul></div>
      </div>` : ""}

      <h2>AI 전략 초안</h2>
      ${c.strategy_error ? `<div class="alert err">${esc(c.strategy_error)}</div>` : ""}
      ${s ? `
        <div class="card">
          <div class="small muted">작성 ${esc(s.model)} · ${day(s.generated_at)}</div>
          <p style="margin:6px 0 0">${esc(s.advisor_summary)}</p>
          <details style="margin-top:8px"><summary class="small">고객에게 보이는 요약</summary><p class="small">${esc(s.customer_summary)}</p></details>
        </div>
        ${s.items.map((it, i) => itemCard(it, i, reviews.find((r) => r.index === i), addr)).join("")}
      ` : `<p class="muted">초안이 없습니다.</p>`}
      ${editable() ? `<p><button class="btn secondary" id="regen">AI 초안 다시 만들기</button> <span class="small muted">고친 검토 내용은 초기화됩니다.</span></p>` : ""}

      ${s ? `
      <h2>경정청구서 청구 이유</h2>
      <textarea class="tall" id="statement" ${editable() ? "" : "readonly"}>${esc(rv?.claim_statement ?? s.claim_statement)}</textarea>
      <div class="grid2" style="margin-top:12px">
        <label class="f">고객에게 보낼 안내 (고객 진행 화면에 보입니다)<textarea id="custmsg" ${editable() ? "" : "readonly"} placeholder="예: 중개수수료 영수증을 사진으로 보내 주세요">${esc(rv?.customer_message || "")}</textarea></label>
        <label class="f">내부 메모 (고객에게 보이지 않음)<textarea id="memo" ${editable() ? "" : "readonly"}>${esc(rv?.advisor_memo || "")}</textarea></label>
      </div>` : ""}

      ${filingSection(c)}
      ${decisionSection(c)}

      ${editable() && s ? `
      <div class="actions">
        <button class="btn secondary" id="save">검토 저장</button>
        <button class="btn" id="file">채택 항목으로 경정청구 제출</button>
        <span class="small muted" id="total" style="align-self:center"></span>
      </div>` : ""}
    `;
    bind();
  }

  function itemCard(it, i, rv, addr) {
    const dec = rv?.decision || "보류";
    const ed = editable();
    return `
      <article class="card item ${dec}" data-i="${i}">
        <div class="head">
          <span class="badge gray">${it.priority}순위</span>
          <h3>${esc(it.title)}</h3>
          <span class="badge">${esc(it.tax_type)}</span>
          ${it.rule_id ? `<span class="badge gray">${esc(it.rule_id)}</span>` : `<span class="badge warn">AI 추가 쟁점</span>`}
          ${it.claim_deadline ? `<span class="badge ${new Date(it.claim_deadline) - new Date() < 90 * 864e5 ? "danger" : ""}">마감 ${it.claim_deadline}</span>` : ""}
        </div>
        ${it.property_id ? `<div class="small muted">${esc(addr(it.property_id))}</div>` : ""}
        <p>${esc(it.claim_ground)}</p>
        <div class="cols">
          <div><div class="label">근거</div><ul>${it.legal_basis.map((x) => `<li>${esc(x)}</li>`).join("") || "<li>-</li>"}</ul></div>
          <div><div class="label">확보한 자료</div><ul>${it.evidence_on_hand.map((x) => `<li>${esc(x)}</li>`).join("") || "<li>-</li>"}</ul>
               <div class="label" style="margin-top:6px">더 받을 증빙</div><ul>${it.evidence_needed.map((x) => `<li>${esc(x)}</li>`).join("") || "<li>-</li>"}</ul></div>
          <div><div class="label">반론·위험</div><p class="small" style="margin:4px 0">${esc(it.risks)}</p>
               ${it.customer_questions.length ? `<div class="label">고객 질문</div><ul>${it.customer_questions.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}</div>
        </div>
        <div class="controls">
          <div class="seg" role="group" aria-label="판단">
            ${["채택", "보류", "제외"].map((d) => `<button type="button" data-d="${d}" aria-pressed="${d === dec}" ${ed ? "" : "disabled"}>${d}</button>`).join("")}
          </div>
          <label class="f">청구 환급액(원) <span class="small">AI 추정 ${won(it.estimated_refund)}</span>
            <input type="number" class="refund" min="0" step="1000" value="${rv?.refund ?? it.estimated_refund ?? ""}" ${ed ? "" : "readonly"}></label>
          <label class="f" style="flex:1;min-width:200px">메모<input type="text" class="note" value="${esc(rv?.note || "")}" ${ed ? "" : "readonly"}></label>
        </div>
      </article>`;
  }

  function filingSection(c) {
    if (!c.filing) return "";
    const f = c.filing;
    return `
      <h2>제출한 경정청구</h2>
      <div class="card" style="padding:0">
        <table>
          <thead><tr><th>세목</th><th>제출처</th><th>접수번호</th><th class="num">청구 환급액</th></tr></thead>
          <tbody>${f.submissions.map((s) => `<tr><td>${esc(s.tax_type)}</td><td>${esc(s.channel)}</td><td>${esc(s.receipt_no)}</td><td class="num">${won(s.claimed_refund)}</td></tr>`).join("")}</tbody>
        </table>
      </div>
      <p class="small muted">제출 ${day(f.filed_at)} · 결과 통지 기한 ${f.result_due} · 합계 ${won(f.claimed_refund)} · 체험판이라 실제로 제출되지 않았습니다.</p>`;
  }

  function decisionSection(c) {
    if (c.decision) {
      const d = c.decision;
      return `<h2>세무서 결과</h2><div class="card">${d.approved ? `<b>환급 결정 ${won(d.decided_refund)}</b>` : "<b>불인용</b>"} · ${d.decided_on || day(d.recorded_at)}${d.note ? `<br><span class="small">${esc(d.note)}</span>` : ""}
        <br><span class="small muted">${c.status === "정산완료" ? "고객이 성공보수를 결제했습니다." : d.approved ? "고객 성공보수 결제를 기다리는 중입니다." : "성공보수는 없습니다."}</span></div>`;
    }
    if (c.status !== "청구제출") return "";
    return `
      <h2>세무서 결과 기록</h2>
      <div class="card">
        <div class="controls" style="border:0;margin:0;padding:0">
          <div class="seg" role="group" aria-label="결과" id="outcome">
            <button type="button" data-v="1" aria-pressed="true">환급 결정</button><button type="button" data-v="0" aria-pressed="false">불인용</button>
          </div>
          <label class="f">결정 환급액(원)<input type="number" id="decided" min="0" step="1000" value="${c.filing.claimed_refund || ""}"></label>
          <label class="f">통지일<input type="date" id="decided-on"></label>
          <label class="f" style="flex:1;min-width:200px">메모<input type="text" id="dnote"></label>
          <button class="btn" id="record">결과 기록</button>
        </div>
      </div>`;
  }

  function collectReview() {
    return {
      items: [...detail.querySelectorAll(".item")].map((el) => {
        const v = el.querySelector(".refund").value;
        return {
          index: Number(el.dataset.i),
          decision: el.querySelector(".seg [aria-pressed=true]").dataset.d,
          refund: v === "" ? null : Number(v),
          note: el.querySelector(".note").value,
        };
      }),
      claim_statement: $("#statement").value,
      customer_message: $("#custmsg").value,
      advisor_memo: $("#memo").value,
    };
  }

  function updateTotal() {
    const t = $("#total"); if (!t) return;
    const r = collectReview().items;
    const adopted = r.filter((x) => x.decision === "채택");
    const hold = r.filter((x) => x.decision === "보류").length;
    t.textContent = `채택 ${adopted.length}건 · 청구 ${won(adopted.reduce((s, x) => s + (x.refund || 0), 0))}${hold ? ` · 보류 ${hold}건이 남아 있으면 제출할 수 없어요` : ""}`;
  }

  async function act(btn, fn, okMsg) {
    btn.disabled = true;
    try { await fn(); toast(okMsg); await loadList(); renderCase(); }
    catch (e) { toast(e.message); btn.disabled = false; }
  }

  function bind() {
    detail.querySelectorAll(".item .seg button").forEach((b) => (b.onclick = () => {
      const card = b.closest(".item");
      card.querySelectorAll(".seg button").forEach((x) => x.setAttribute("aria-pressed", x === b));
      card.className = `card item ${b.dataset.d}`;
      updateTotal();
    }));
    detail.querySelectorAll(".refund").forEach((i) => (i.oninput = updateTotal));
    updateTotal();

    const save = $("#save");
    if (save) save.onclick = () => act(save, async () => {
      current = await api(`/advisor/cases/${current.id}/review`, { method: "PUT", body: collectReview() });
    }, "검토 내용을 저장했어요");

    const file = $("#file");
    if (file) file.onclick = () => act(file, async () => {
      current = await api(`/advisor/cases/${current.id}/review`, { method: "PUT", body: collectReview() });
      current = await api(`/advisor/cases/${current.id}/file`, { method: "POST" });
    }, "경정청구서를 제출했어요 (체험판)");

    const regen = $("#regen");
    if (regen) regen.onclick = () => act(regen, async () => {
      regen.textContent = "AI가 쓰는 중…";
      current = await api(`/advisor/cases/${current.id}/strategy`, { method: "POST" });
    }, "AI 초안을 새로 만들었어요");

    const outcome = $("#outcome");
    if (outcome) outcome.querySelectorAll("button").forEach((b) => (b.onclick = () => {
      outcome.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b));
      $("#decided").disabled = b.dataset.v === "0";
    }));
    const record = $("#record");
    if (record) record.onclick = () => act(record, async () => {
      const approved = outcome.querySelector("[aria-pressed=true]").dataset.v === "1";
      current = await api(`/advisor/cases/${current.id}/decision`, {
        method: "POST",
        body: { approved, decided_refund: approved ? Number($("#decided").value || 0) : 0, decided_on: $("#decided-on").value || null, note: $("#dnote").value },
      });
    }, "결과를 기록했어요. 고객에게 결제 안내가 갑니다");
  }

  // ---------- 정산 ----------
  async function renderBilling() {
    layout.classList.add("wide");
    const b = await api("/advisor/billing");
    $("#who").textContent = `${b.agent.office} · ${b.agent.name}`;
    const PURPOSE = { success_fee: "성공보수 (고객 → 세무사)", platform_fee: "플랫폼 이용료 (세무사 → 플랫폼)" };
    detail.innerHTML = `
      <h1>정산</h1>
      <p class="muted small">성공보수 요율 ${Math.round(b.success_fee_rate * 100)}% (부가세 별도) · 플랫폼 이용료 경정청구 제출 1건당 ${won(b.platform_fee_per_case)}</p>
      <div class="grid2">
        <div class="card"><div class="muted small">받은 성공보수</div><div class="stat">${won(b.success_fees)}</div></div>
        <div class="card"><div class="muted small">플랫폼에 낼 이용료</div><div class="stat">${won(b.platform_fees)}</div></div>
      </div>
      <h2>결제 기록</h2>
      <div class="card" style="padding:0;overflow-x:auto">
        <table>
          <thead><tr><th>일시</th><th>구분</th><th>낸 사람</th><th>받는 곳</th><th>방법</th><th>영수증</th><th class="num">금액</th></tr></thead>
          <tbody>${b.payments.map((p) => `<tr><td>${esc(String(p.approved_at).slice(0, 16).replace("T", " "))}</td><td>${PURPOSE[p.purpose] || esc(p.purpose)}</td><td>${esc(p.payer)}</td><td>${esc(p.payee)}</td><td>${esc(p.method)}</td><td>${esc(p.receipt_no)}</td><td class="num">${won(p.amount)}</td></tr>`).join("") || `<tr><td colspan="7" class="muted">아직 기록이 없습니다.</td></tr>`}</tbody>
        </table>
      </div>
      <p class="small muted">체험판 결제는 가짜 PG로 바로 승인됩니다.</p>`;
  }

  start();
})();
