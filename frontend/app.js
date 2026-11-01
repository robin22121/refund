/* 양도세 환급 무료 진단 — 모바일 웹.
 *
 * 흐름: 소재지 → 본인 정보 → 등기부 등 자동 조회 → 검토 희망 항목 → 전자위임장 → 홈택스 간편인증 → 진단 리포트
 * 백엔드(FastAPI)와 같은 출처에서 서비스된다. 지금은 백엔드가 하이픈 연동을 가짜 데이터로 흉내 낸다.
 */
(() => {
  "use strict";

  // ---------- 설정 ----------
  const API = ""; // 같은 출처
  const STORAGE_KEY = "refund-diagnosis-v1";

  const STEPS = ["address", "applicant", "lookup", "concern", "mandate", "auth"];
  const STEP_LABELS = {
    address: "부동산 주소",
    applicant: "본인 정보",
    lookup: "등기부 자동 조회",
    concern: "궁금한 부분",
    mandate: "전자위임장",
    auth: "홈택스 인증",
    authWait: "홈택스 인증",
  };

  // 고객이 고르는 검토 희망 항목. rules는 백엔드 룰 id와 연결해 리포트에서 먼저 보여 준다.
  const CONCERNS = [
    { id: "exempt", label: "집이 한 채뿐인데 비과세를 못 받았어요", rules: ["CGT_ONE_HOUSE_EXEMPTION"] },
    { id: "lthd", label: "오래 살았는데 공제가 적은 것 같아요", sub: "장기보유특별공제", rules: ["CGT_LTHD_RESIDENCE"] },
    { id: "expense", label: "취득세·중개수수료를 빼지 않은 것 같아요", sub: "필요경비", rules: ["CGT_MISSING_EXPENSE"] },
    { id: "land", label: "농지·땅이 비사업용이라 세금이 늘었어요", sub: "비사업용토지 중과", rules: ["CGT_NON_BUSINESS_LAND"] },
    { id: "converted", label: "옛날 집이라 취득가액을 환산했어요", sub: "환산취득가액", rules: ["CGT_CONVERTED_PRICE"] },
    { id: "all", label: "잘 모르겠어요. 전부 봐 주세요", rules: [] },
  ];

  const PROVIDERS = [
    { id: "kakao", name: "카카오톡", bg: "#fee500", fg: "#191919", short: "TALK" },
    { id: "naver", name: "네이버", bg: "#03c75a", fg: "#fff", short: "N" },
    { id: "pass", name: "PASS", bg: "#e4002b", fg: "#fff", short: "PASS" },
    { id: "toss", name: "토스", bg: "#0064ff", fg: "#fff", short: "toss" },
  ];

  const LOOKUP_TASKS = ["인터넷등기소 등기부등본 발급", "건축물대장·토지대장 조회", "토지이용계획확인원 조회", "소유자 이름 확인"];
  const COLLECT_TASKS = [
    "홈택스 양도소득세 신고내역",
    "홈택스 사업·소득 내역",
    "위택스 취득세·재산세 납부내역",
    "주민등록초본 전입 이력",
    "환급 가능성 분석",
  ];

  // ---------- 상태 ----------
  const initial = () => ({
    screen: "intro",
    addresses: [],
    name: "",
    birth: "",
    phone: "",
    caseId: null,
    assets: [],
    concerns: [],
    concernText: "",
    provider: "kakao",
    report: null,
    requested: false,
  });
  let state = load() || initial();
  let mandateInfo = null;

  function load() {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      return raw ? { ...initial(), ...JSON.parse(raw) } : null;
    } catch { return null; }
  }
  function save() {
    try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch { /* 사생활 보호 모드 등 */ }
  }

  // ---------- 도구 ----------
  const $ = (sel, root = document) => root.querySelector(sel);
  const app = $("#app");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const won = (n) => `${Number(n).toLocaleString("ko-KR")}원`;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function manwon(n) {
    const man = Math.round(n / 10000);
    if (man >= 10000) {
      const eok = Math.floor(man / 10000), rest = man % 10000;
      return `${eok}억${rest ? ` ${rest.toLocaleString("ko-KR")}만` : ""} 원`;
    }
    return `${man.toLocaleString("ko-KR")}만 원`;
  }
  function koDate(iso) {
    if (!iso) return "";
    const [y, m, d] = iso.split("-").map(Number);
    return `${y}년 ${m}월 ${d}일`;
  }
  function dday(iso) {
    const days = Math.ceil((new Date(iso + "T23:59:59") - new Date()) / 86400000);
    return days >= 0 ? `${days.toLocaleString("ko-KR")}일 남음` : "기한 지남";
  }

  async function api(path, opts = {}) {
    const res = await fetch(API + path, {
      method: opts.method || "GET",
      headers: opts.body ? { "Content-Type": "application/json" } : undefined,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (!res.ok) {
      let msg = "잠시 후 다시 시도해 주세요.";
      try { const j = await res.json(); if (typeof j.detail === "string") msg = j.detail; } catch { /* 무시 */ }
      const err = new Error(msg);
      err.status = res.status;
      throw err;
    }
    return res.json();
  }

  let toastTimer;
  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (t.hidden = true), 3200);
  }

  function parseBirth(v) {
    const d = v.replace(/\D/g, "");
    let y, m, day;
    if (d.length === 6) {
      const yy = Number(d.slice(0, 2));
      y = yy > new Date().getFullYear() % 100 ? 1900 + yy : 2000 + yy;
      m = Number(d.slice(2, 4)); day = Number(d.slice(4, 6));
    } else if (d.length === 8) {
      y = Number(d.slice(0, 4)); m = Number(d.slice(4, 6)); day = Number(d.slice(6, 8));
    } else return null;
    const dt = new Date(y, m - 1, day);
    if (dt.getFullYear() !== y || dt.getMonth() !== m - 1 || dt.getDate() !== day) return null;
    return `${y}-${String(m).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
  }
  function formatPhone(v) {
    const d = v.replace(/\D/g, "").slice(0, 11);
    if (d.length < 4) return d;
    if (d.length < 8) return `${d.slice(0, 3)}-${d.slice(3)}`;
    return `${d.slice(0, 3)}-${d.slice(3, d.length - 4)}-${d.slice(-4)}`;
  }

  // 화면 하단 고정 버튼
  const bottom = (inner) => `<div class="bottom"><div class="bottom-inner">${inner}</div></div>`;

  // ---------- 화면 전환 ----------
  function go(screen) {
    state.screen = screen;
    save();
    render();
    window.scrollTo(0, 0);
    app.focus({ preventScroll: true });
  }

  const BACK = {
    address: "intro", applicant: "address", lookup: "applicant", concern: "lookup",
    mandate: "concern", auth: "mandate", authWait: "auth",
  };
  $("#back").addEventListener("click", () => {
    const prev = BACK[state.screen];
    if (prev) go(prev);
  });

  function render() {
    const s = state.screen;
    $("#back").hidden = !BACK[s];
    const idx = STEPS.indexOf(s === "authWait" ? "auth" : s);
    const prog = $("#progress");
    if (idx >= 0) {
      prog.hidden = false;
      $("#progress-fill").style.width = `${((idx + 1) / (STEPS.length + 1)) * 100}%`;
      $("#progress-label").textContent = `${idx + 1} / ${STEPS.length} 단계 · ${STEP_LABELS[s]}`;
    } else prog.hidden = true;
    (SCREENS[s] || SCREENS.intro)();
  }

  // ---------- 화면들 ----------
  const SCREENS = {};

  SCREENS.intro = () => {
    app.innerHTML = `
      <section class="hero">
        <span class="badge">진단 무료</span>
        <h1>내가 낸 양도세,<br>돌려받을 수 있을까요?</h1>
        <p class="lead">부동산 주소만 알려 주시면 서류는 저희가 대신 떼고, 신고 내용을 하나하나 따져 봐 드립니다.</p>
      </section>
      <ul class="benefits">
        <li><span class="ico">₩</span><div><b>진단은 무료입니다</b><br><span class="muted small">환급을 받게 될 때만 세무사 수수료가 생깁니다.</span></div></li>
        <li><span class="ico">✓</span><div><b>서류를 따로 떼지 않아도 됩니다</b><br><span class="muted small">등기부등본·대장·토지이용계획·초본을 자동으로 발급합니다.</span></div></li>
        <li><span class="ico">⚖</span><div><b>양도세 전문가가 만든 기준</b><br><span class="muted small">양도세 담당 공무원 출신이 만든 점검표로 진단하고, 제휴 세무사가 확인합니다.</span></div></li>
      </ul>
      <div class="card soft">
        <b>이렇게 진행돼요 (약 3분)</b>
        <ol class="steps-mini" style="margin-top:10px">
          <li>판 부동산 주소 입력</li>
          <li>궁금한 부분 고르기</li>
          <li>전자위임장 서명</li>
          <li>카카오톡으로 홈택스 인증</li>
        </ol>
      </div>
      <p class="small muted">5년 안에 신고한 양도소득세가 대상입니다. 기한이 지나면 돌려받을 수 없으니 서둘러 확인하세요.</p>
      ${bottom(`<button class="btn" id="start">무료로 진단 시작하기</button>`)}
    `;
    $("#start").onclick = () => {
      if (state.report) { go("report"); return; }
      go("address");
    };
    if (state.report) {
      $("#start").textContent = "지난 진단 결과 보기";
      $(".bottom-inner").insertAdjacentHTML("beforeend", `<button class="btn ghost" id="restart">처음부터 다시 하기</button>`);
      $("#restart").onclick = () => { state = initial(); save(); go("address"); };
    }
  };

  SCREENS.address = () => {
    app.innerHTML = `
      <h1>판 부동산의 주소를<br>알려 주세요</h1>
      <p class="lead">동네 이름이나 도로명 한두 단어만 써도 찾아 드려요.</p>
      <form id="search-form" class="search-row" autocomplete="off">
        <input type="text" id="q" placeholder="예: 마포 성미산로" aria-label="주소 검색어" enterkeyhint="search">
        <button class="btn" type="submit">찾기</button>
      </form>
      <div class="examples" aria-label="체험용 예시 주소">
        <span class="small muted" style="width:100%">체험용 예시 (눌러서 검색)</span>
        ${["마포", "분당", "예산", "유성"].map((w) => `<button type="button" data-q="${w}">${w}</button>`).join("")}
      </div>
      <ul class="results" id="results"></ul>
      <div id="chosen"></div>
      ${bottom(`<button class="btn" id="next" disabled>다음</button>`)}
    `;
    const drawChosen = () => {
      $("#chosen").innerHTML = state.addresses.length ? `
        <h2>선택한 부동산 ${state.addresses.length}곳</h2>
        ${state.addresses.map((a, i) => `
          <div class="card chosen"><div class="addr">${esc(a)}</div>
          <button type="button" class="remove" data-i="${i}">빼기</button></div>`).join("")}
        <p class="small muted">여러 곳을 팔았다면 계속 찾아서 더하세요.</p>` : "";
      $("#next").disabled = state.addresses.length === 0;
      $("#chosen").querySelectorAll(".remove").forEach((b) => (b.onclick = () => {
        state.addresses.splice(Number(b.dataset.i), 1); save(); drawChosen();
      }));
    };
    const search = async (q) => {
      const box = $("#results");
      if (!q.trim()) { box.innerHTML = ""; return; }
      box.innerHTML = `<li class="muted">찾는 중…</li>`;
      try {
        const list = await api(`/address/search?q=${encodeURIComponent(q.trim())}`);
        box.innerHTML = list.length
          ? list.map((r) => `<li><button type="button" data-a="${esc(r.address)}"><span class="kind">${esc(r.kind)}</span><br>${esc(r.address)}</button></li>`).join("")
          : `<li class="card">찾는 주소가 없어요. 동 이름이나 도로명으로 다시 찾아 보세요.<br><span class="small muted">체험판에서는 위 예시 주소만 찾을 수 있어요.</span></li>`;
        box.querySelectorAll("button[data-a]").forEach((b) => (b.onclick = () => {
          if (!state.addresses.includes(b.dataset.a)) state.addresses.push(b.dataset.a);
          save(); box.innerHTML = ""; $("#q").value = ""; drawChosen();
        }));
      } catch (e) { box.innerHTML = `<li class="error-text">${esc(e.message)}</li>`; }
    };
    $("#search-form").onsubmit = (e) => { e.preventDefault(); $("#q").blur(); search($("#q").value); };
    app.querySelectorAll(".examples button").forEach((b) => (b.onclick = () => { $("#q").value = b.dataset.q; search(b.dataset.q); }));
    $("#next").onclick = () => go("applicant");
    drawChosen();
  };

  SCREENS.applicant = () => {
    app.innerHTML = `
      <h1>본인 확인을 위해<br>세 가지만 알려 주세요</h1>
      <p class="lead">등기부의 소유자와 같은 분인지 확인하고, 홈택스 인증을 보낼 때 씁니다.</p>
      <form id="f" novalidate>
        <label class="field" for="name">이름</label>
        <input type="text" id="name" autocomplete="name" value="${esc(state.name)}" placeholder="홍길동">
        <label class="field" for="birth">생년월일 6자리</label>
        <input type="tel" id="birth" inputmode="numeric" maxlength="8" value="${esc(state.birth)}" placeholder="650302">
        <div class="hint">1965년 3월 2일생이면 650302</div>
        <label class="field" for="phone">휴대폰 번호</label>
        <input type="tel" id="phone" autocomplete="tel" value="${esc(state.phone)}" placeholder="010-1234-5678">
        <div class="error-text" id="err" role="alert"></div>
      </form>
      ${bottom(`<button class="btn" id="next">등기부 자동 조회하기</button>`)}
    `;
    $("#phone").oninput = (e) => (e.target.value = formatPhone(e.target.value));
    $("#birth").oninput = (e) => (e.target.value = e.target.value.replace(/\D/g, ""));
    $("#f").onsubmit = (e) => { e.preventDefault(); $("#next").click(); };
    $("#next").onclick = () => {
      const name = $("#name").value.trim();
      const birth = $("#birth").value.trim();
      const phone = $("#phone").value.trim();
      const err = $("#err");
      if (name.length < 2) { err.textContent = "이름을 적어 주세요."; $("#name").focus(); return; }
      if (!parseBirth(birth)) { err.textContent = "생년월일 6자리를 확인해 주세요. 예: 650302"; $("#birth").focus(); return; }
      if (!/^01\d-?\d{3,4}-?\d{4}$/.test(phone)) { err.textContent = "휴대폰 번호를 확인해 주세요."; $("#phone").focus(); return; }
      Object.assign(state, { name, birth, phone, caseId: null, assets: [], report: null });
      go("lookup");
    };
  };

  // 진행 목록을 순서대로 체크하면서, 실제 요청도 함께 기다린다.
  async function runTasks(listEl, tasks, request, stepMs = 700) {
    listEl.innerHTML = tasks.map((t) => `<li><span class="dot" aria-hidden="true"></span><span>${esc(t)}</span></li>`).join("");
    const items = [...listEl.children];
    const result = request();
    for (let i = 0; i < items.length; i++) {
      items[i].className = "running";
      // 마지막 단계는 실제 응답이 올 때까지 기다린다.
      if (i === items.length - 1) await result; else await sleep(stepMs);
      items[i].className = "done";
      items[i].querySelector(".dot").textContent = "✓";
    }
    return result;
  }

  SCREENS.lookup = async () => {
    if (state.caseId && state.assets.length) return drawAssets();
    app.innerHTML = `
      <h1>서류를 떼고 있어요</h1>
      <p class="lead">${state.addresses.length}곳의 등기부등본과 대장을 자동으로 발급하는 중입니다. 잠시만 기다려 주세요.</p>
      <ul class="tasks" id="tasks" aria-live="polite"></ul>
    `;
    try {
      const c = await runTasks($("#tasks"), LOOKUP_TASKS, () => api("/cases", {
        method: "POST",
        body: { name: state.name, birth_date: parseBirth(state.birth), phone: state.phone.replace(/\D/g, ""), addresses: state.addresses, concern: "" },
      }));
      state.caseId = c.id;
      state.assets = c.assets;
      save();
      await sleep(400);
      if (state.screen === "lookup") drawAssets();
    } catch (e) {
      app.innerHTML = `<h1>조회하지 못했어요</h1><p class="error-text">${esc(e.message)}</p>${bottom(`<button class="btn" id="retry">다시 시도</button>`)}`;
      $("#retry").onclick = () => render();
    }
  };

  function assetCard(a) {
    if (!a.property) {
      return `<div class="card asset"><span class="badge danger">찾지 못함</span>
        <h3>${esc(a.address)}</h3><p class="error-text">${esc(a.error || "등기부를 찾지 못했어요.")}</p></div>`;
    }
    const p = a.property;
    const rows = [
      ["종류", p.kind],
      ["취득일", koDate(p.acquired_on)],
      ["취득 원인", p.acquisition_cause],
      p.registered_price ? ["등기 거래가액", manwon(p.registered_price)] : null,
      p.land_category ? ["지목", p.land_category] : null,
      p.zoning ? ["용도지역", p.zoning] : null,
      p.building_approved_on ? ["사용승인일", koDate(p.building_approved_on)] : null,
      p.disposed_on ? ["양도일", koDate(p.disposed_on)] : null,
    ].filter(Boolean);
    return `<div class="card asset">
      <span class="badge ok">조회 완료</span> ${a.owner_name_matches ? `<span class="badge">소유자 확인</span>` : `<span class="badge warn">소유자 다름</span>`}
      <h3>${esc(p.address)}</h3>
      <dl class="facts">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
      <div class="docs">${(a.documents || []).map((d) => `<span class="badge">📄 ${esc(d)}</span>`).join("")}</div>
    </div>`;
  }

  function drawAssets() {
    const found = state.assets.filter((a) => a.property).length;
    app.innerHTML = `
      <h1>${found ? `${found}곳의 서류를<br>모두 떼었어요` : "서류를 찾지 못했어요"}</h1>
      <p class="lead">${found ? "아래 내용이 맞는지 확인해 주세요." : "주소를 다시 확인해 주세요."}</p>
      ${state.assets.map(assetCard).join("")}
      ${bottom(found
        ? `<button class="btn" id="next">네, 맞아요</button><button class="btn ghost" id="fix">주소 다시 고르기</button>`
        : `<button class="btn" id="fix">주소 다시 고르기</button>`)}
    `;
    const next = $("#next");
    if (next) next.onclick = () => go("concern");
    $("#fix").onclick = () => { state.caseId = null; state.assets = []; go("address"); };
  }

  SCREENS.concern = () => {
    app.innerHTML = `
      <h1>세금 낼 때 가장<br>아깝다고 느낀 부분은요?</h1>
      <p class="lead">해당하는 것을 모두 눌러 주세요. 이 부분부터 꼼꼼히 봐 드립니다.</p>
      <div class="choices" role="group" aria-label="검토 희망 항목">
        ${CONCERNS.map((c) => `
          <button type="button" class="choice" data-id="${c.id}" aria-pressed="${state.concerns.includes(c.id)}">
            <span class="check" aria-hidden="true">✓</span>
            <span>${esc(c.label)}${c.sub ? `<small>${esc(c.sub)}</small>` : ""}</span>
          </button>`).join("")}
      </div>
      <label class="field" for="memo">더 하실 말씀 (선택)</label>
      <textarea id="memo" placeholder="예: 2023년에 판 마포 아파트 세금이 너무 많이 나왔어요">${esc(state.concernText)}</textarea>
      ${bottom(`<button class="btn" id="next">다음</button>`)}
    `;
    app.querySelectorAll(".choice").forEach((b) => (b.onclick = () => {
      const id = b.dataset.id;
      if (id === "all") state.concerns = state.concerns.includes("all") ? [] : ["all"];
      else {
        state.concerns = state.concerns.filter((x) => x !== "all");
        state.concerns = state.concerns.includes(id) ? state.concerns.filter((x) => x !== id) : [...state.concerns, id];
      }
      save();
      app.querySelectorAll(".choice").forEach((x) => x.setAttribute("aria-pressed", state.concerns.includes(x.dataset.id)));
    }));
    $("#next").onclick = async () => {
      state.concernText = $("#memo").value.trim();
      if (!state.concerns.length && !state.concernText) { toast("하나 이상 골라 주세요. 모르시면 ‘잘 모르겠어요’를 누르세요."); return; }
      const labels = CONCERNS.filter((c) => state.concerns.includes(c.id)).map((c) => c.label);
      const concern = [...labels, state.concernText].filter(Boolean).join(" / ");
      $("#next").disabled = true;
      try {
        await api(`/cases/${state.caseId}/concern`, { method: "PUT", body: { concern } });
        go("mandate");
      } catch (e) { handleCaseError(e); $("#next").disabled = false; }
    };
  };

  function handleCaseError(e) {
    if (e.status === 404) {
      // 서버가 재시작되어 접수 건이 사라진 경우: 자동 조회부터 다시 한다.
      toast("접수 정보가 만료되어 서류 조회부터 다시 진행합니다.");
      state.caseId = null; state.assets = [];
      go("lookup");
    } else toast(e.message);
  }

  SCREENS.mandate = async () => {
    app.innerHTML = `<h1>전자위임장</h1><p class="muted">불러오는 중…</p>`;
    if (!mandateInfo) {
      try { mandateInfo = await api("/mandate/info"); } catch (e) { toast(e.message); return; }
    }
    if (state.screen !== "mandate") return;
    const { agent, scopes } = mandateInfo;
    const today = new Date();
    app.innerHTML = `
      <h1>세무사에게 맡기는<br>전자위임장이에요</h1>
      <p class="lead">홈택스 자료를 대신 보고, 환급이 가능하면 신청까지 대신 하도록 맡기는 서류입니다.</p>
      <div class="card mandate-doc">
        <dl>
          <dt>위임인</dt><dd>${esc(state.name)} (${esc(parseBirth(state.birth)?.replaceAll("-", ".") || "")})</dd>
          <dt>수임인</dt><dd>${esc(agent.office)}<br>${esc(agent.name)} · 등록번호 ${esc(agent.registration_no)}</dd>
          <dt>대상</dt><dd>${state.assets.filter((a) => a.property).map((a) => esc(a.property.address.split(",")[0])).join("<br>")}</dd>
          <dt>작성일</dt><dd>${today.getFullYear()}년 ${today.getMonth() + 1}월 ${today.getDate()}일</dd>
        </dl>
      </div>
      <h2>맡기는 일</h2>
      <div class="choices">
        <button type="button" class="choice agree-all" id="all" aria-pressed="false"><span class="check" aria-hidden="true">✓</span><span>아래 내용에 모두 동의합니다</span></button>
        ${scopes.map((s, i) => `<button type="button" class="choice scope" data-i="${i}" aria-pressed="false"><span class="check" aria-hidden="true">✓</span><span>${esc(s)}</span></button>`).join("")}
      </div>
      <h2>서명</h2>
      <p class="muted">손가락으로 아래 칸에 이름을 써 주세요.</p>
      <div class="sign-wrap">
        <canvas class="sign" id="sign" aria-label="서명 칸"></canvas>
        <div class="sign-placeholder" id="sign-ph">여기에 서명</div>
      </div>
      <div class="sign-tools"><span class="small muted">서명자: ${esc(state.name)}</span><button type="button" class="linkbtn" id="clear">지우고 다시 쓰기</button></div>
      <p class="small muted">위임은 언제든 철회할 수 있고, 진단만 받고 신청하지 않으셔도 비용이 들지 않습니다.</p>
      ${bottom(`<button class="btn" id="next" disabled>서명하고 다음으로</button>`)}
    `;
    const agreed = new Set();
    const pad = signaturePad($("#sign"), () => { $("#sign-ph").hidden = true; update(); });
    function update() {
      app.querySelectorAll(".scope").forEach((b) => b.setAttribute("aria-pressed", agreed.has(Number(b.dataset.i))));
      $("#all").setAttribute("aria-pressed", agreed.size === scopes.length);
      $("#next").disabled = !(agreed.size === scopes.length && pad.hasInk());
    }
    $("#all").onclick = () => { if (agreed.size === scopes.length) agreed.clear(); else scopes.forEach((_, i) => agreed.add(i)); update(); };
    app.querySelectorAll(".scope").forEach((b) => (b.onclick = () => {
      const i = Number(b.dataset.i);
      agreed.has(i) ? agreed.delete(i) : agreed.add(i);
      update();
    }));
    $("#clear").onclick = () => { pad.clear(); $("#sign-ph").hidden = false; update(); };
    $("#next").onclick = async () => {
      $("#next").disabled = true;
      try {
        await api(`/cases/${state.caseId}/mandate`, {
          method: "POST",
          body: { signer_name: state.name, agreed_scopes: scopes, signature_image: pad.toDataURL() },
        });
        go("auth");
      } catch (e) { handleCaseError(e); $("#next").disabled = false; }
    };
  };

  function signaturePad(canvas, onInk) {
    const ctx = canvas.getContext("2d");
    let drawing = false, ink = false, last = null;
    function resize() {
      const r = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
      canvas.width = r.width * dpr; canvas.height = r.height * dpr;
      ctx.scale(dpr, dpr);
      ctx.lineWidth = 3.2; ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.strokeStyle = "#14171c";
    }
    resize();
    const pos = (e) => { const r = canvas.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; };
    canvas.addEventListener("pointerdown", (e) => { drawing = true; last = pos(e); canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener("pointermove", (e) => {
      if (!drawing) return;
      const p = pos(e);
      ctx.beginPath(); ctx.moveTo(last.x, last.y); ctx.lineTo(p.x, p.y); ctx.stroke();
      last = p;
      if (!ink) { ink = true; onInk(); }
    });
    const end = () => (drawing = false);
    canvas.addEventListener("pointerup", end);
    canvas.addEventListener("pointercancel", end);
    return {
      hasInk: () => ink,
      clear: () => { ctx.clearRect(0, 0, canvas.width, canvas.height); ink = false; },
      toDataURL: () => canvas.toDataURL("image/png"),
    };
  }

  SCREENS.auth = () => {
    app.innerHTML = `
      <h1>홈택스 자료를 불러오려면<br>본인 인증이 필요해요</h1>
      <p class="lead">평소 쓰시는 앱을 고르세요. 휴대폰으로 인증 요청이 갑니다.</p>
      <div class="providers" role="group" aria-label="간편인증 앱">
        ${PROVIDERS.map((p) => `
          <button type="button" class="provider" data-id="${p.id}" aria-pressed="${state.provider === p.id}">
            <span class="logo" style="background:${p.bg};color:${p.fg}">${p.short}</span>${p.name}
          </button>`).join("")}
      </div>
      <div class="card" style="margin-top:20px">
        <dl class="facts"><dt>이름</dt><dd>${esc(state.name)}</dd><dt>휴대폰</dt><dd>${esc(state.phone)}</dd></dl>
      </div>
      <p class="small muted">인증은 홈택스·위택스·정부24 자료 조회에만 쓰이며, 비밀번호나 인증서는 저장하지 않습니다.</p>
      ${bottom(`<button class="btn" id="next">인증 요청 보내기</button>`)}
    `;
    app.querySelectorAll(".provider").forEach((b) => (b.onclick = () => {
      state.provider = b.dataset.id; save();
      app.querySelectorAll(".provider").forEach((x) => x.setAttribute("aria-pressed", x.dataset.id === state.provider));
    }));
    $("#next").onclick = async () => {
      $("#next").disabled = true;
      try {
        await api(`/cases/${state.caseId}/auth/start`, { method: "POST", body: { provider: state.provider } });
        go("authWait");
      } catch (e) {
        if (e.status === 409) { toast(e.message); go("mandate"); } else handleCaseError(e);
        $("#next") && ($("#next").disabled = false);
      }
    };
  };

  SCREENS.authWait = () => {
    const p = PROVIDERS.find((x) => x.id === state.provider) || PROVIDERS[0];
    app.innerHTML = `
      <h1>${esc(p.name)}에서<br>인증을 마쳐 주세요</h1>
      <p class="lead">${esc(state.phone)}로 인증 요청을 보냈어요.</p>
      <div class="phone-mock" aria-hidden="true">
        <div class="notif" style="background:${p.bg};color:${p.fg}"><b>${esc(p.name)} 인증</b>[국세청 홈택스] 본인확인 요청이 도착했습니다.</div>
      </div>
      <ol class="steps-mini">
        <li>휴대폰에 온 ${esc(p.name)} 알림을 누르세요</li>
        <li>‘인증하기’를 누르고 확인을 마치세요</li>
        <li>이 화면으로 돌아와 아래 버튼을 누르세요</li>
      </ol>
      <p class="small muted" style="margin-top:16px">체험판에서는 실제 알림이 가지 않아요. 바로 아래 버튼을 누르시면 됩니다.</p>
      ${bottom(`<button class="btn" id="done">인증을 마쳤어요</button><button class="btn ghost" id="resend">인증 요청 다시 보내기</button>`)}
    `;
    $("#done").onclick = () => go("collecting");
    $("#resend").onclick = async () => {
      try { await api(`/cases/${state.caseId}/auth/start`, { method: "POST", body: { provider: state.provider } }); toast("인증 요청을 다시 보냈어요."); }
      catch (e) { handleCaseError(e); }
    };
  };

  SCREENS.collecting = async () => {
    app.innerHTML = `
      <h1>자료를 모아<br>분석하고 있어요</h1>
      <p class="lead">홈택스·위택스·정부24에서 신고 내역을 불러와 하나씩 따져 보는 중입니다. 화면을 닫지 마세요.</p>
      <ul class="tasks" id="tasks" aria-live="polite"></ul>
    `;
    try {
      const report = await runTasks($("#tasks"), COLLECT_TASKS, () => api(`/cases/${state.caseId}/diagnosis`, { method: "POST" }), 900);
      state.report = report;
      save();
      await sleep(500);
      go("report");
    } catch (e) {
      if (e.status === 409) { toast("인증이 아직 끝나지 않았어요. 다시 시도해 주세요."); go("auth"); }
      else {
        app.innerHTML = `<h1>자료를 불러오지 못했어요</h1><p class="error-text">${esc(e.message)}</p>${bottom(`<button class="btn" id="retry">다시 시도</button>`)}`;
        $("#retry").onclick = () => render();
      }
    }
  };

  const CONF_CLASS = { "높음": "high", "보통": "medium", "세무사 검토 필요": "review" };
  const CONF_BADGE = { "높음": "ok", "보통": "", "세무사 검토 필요": "warn" };
  const CONF_TEXT = { "높음": "환급 가능성 높음", "보통": "환급 가능성 있음", "세무사 검토 필요": "세무사 확인 필요" };

  SCREENS.report = () => {
    const r = state.report;
    if (!r) { go("intro"); return; }
    const wanted = new Set(CONCERNS.filter((c) => state.concerns.includes(c.id)).flatMap((c) => c.rules));
    const findings = [...r.findings].sort((a, b) => Number(wanted.has(b.rule_id)) - Number(wanted.has(a.rule_id)));
    const total = findings.reduce((s, f) => s + (f.estimated_refund || 0), 0);
    const unknown = findings.filter((f) => f.estimated_refund == null).length;
    const nearest = findings.map((f) => f.claim_deadline).filter(Boolean).sort()[0];

    const summary = findings.length ? `
      <div class="summary">
        <div class="label">${esc(r.taxpayer_name)}님이 돌려받을 수 있는 세금</div>
        <div class="amount">${total ? `약 ${manwon(total)}` : "확인 필요"}</div>
        <div class="sub">환급 가능 항목 ${findings.length}건${unknown ? ` · 이 중 ${unknown}건은 세무사가 금액을 계산해 드려요` : ""}</div>
      </div>
      ${nearest ? `<div class="card"><span class="deadline">신청 마감 ${koDate(nearest)}</span> <span class="badge warn">${dday(nearest)}</span><br><span class="small muted">기한이 지나면 돌려받을 수 없어요.</span></div>` : ""}
    ` : `
      <div class="card soft"><h3 style="margin-top:0">지금 자료로는 환급 사유를 찾지 못했어요</h3>
      <p class="muted">신고가 바르게 된 것으로 보입니다. 그래도 걱정되시면 세무사에게 직접 물어보세요.</p></div>`;

    app.innerHTML = `
      <h1>진단 결과가 나왔어요</h1>
      ${summary}
      ${r.concern ? `<div class="card concern-box"><span class="small muted">고객님이 궁금해하신 부분</span><p style="margin:4px 0 0;font-weight:600">${esc(r.concern)}</p></div>` : ""}
      ${findings.length ? `<h2>찾아낸 항목</h2>` : ""}
      ${findings.map((f) => `
        <article class="card finding ${CONF_CLASS[f.confidence] || ""}">
          <div class="head">
            <span class="badge">${esc(f.tax_type)}</span>
            <span class="badge ${CONF_BADGE[f.confidence] ?? ""}">${esc(CONF_TEXT[f.confidence] || f.confidence)}</span>
            ${wanted.has(f.rule_id) ? `<span class="badge mine">궁금해하신 부분</span>` : ""}
          </div>
          <h3>${esc(f.title)}</h3>
          ${f.property_address ? `<div class="where">${esc(f.property_address.split(" (")[0])}</div>` : ""}
          <p>${esc(f.reason)}</p>
          ${f.estimated_refund != null
            ? `<div class="refund">예상 환급액 약 ${manwon(f.estimated_refund)}</div>`
            : `<div class="refund unknown">환급액은 세무사가 서류를 보고 계산해 드려요</div>`}
          ${f.claim_deadline ? `<div class="small"><span class="deadline">신청 마감 ${koDate(f.claim_deadline)}</span> · ${dday(f.claim_deadline)}</div>` : ""}
          ${f.required_documents?.length ? `
            <details><summary>필요한 서류 ${f.required_documents.length}가지</summary>
              <ul>${f.required_documents.map((d) => `<li>${esc(d)}</li>`).join("")}</ul>
              <p class="small muted" style="margin-top:6px">자동으로 뗀 서류 외에 영수증 등은 세무사가 따로 안내해 드려요.</p>
            </details>` : ""}
        </article>`).join("")}
      <p class="disclaimer">이 결과는 신고 자료를 자동으로 분석한 참고용 진단입니다. 실제 환급 여부와 금액은 제휴 세무사가 검토한 뒤 확정됩니다.</p>
      ${bottom(state.requested
        ? `<button class="btn secondary" id="done">신청 완료 · 안내 다시 보기</button>`
        : `<button class="btn" id="apply">세무사에게 환급 신청 맡기기</button>`)}
    `;
    const apply = $("#apply");
    if (apply) apply.onclick = () => { state.requested = true; save(); go("requested"); };
    const done = $("#done");
    if (done) done.onclick = () => go("requested");
  };

  SCREENS.requested = () => {
    const agent = mandateInfo?.agent;
    app.innerHTML = `
      <div class="success-ico" aria-hidden="true">✓</div>
      <h1 class="center">환급 신청을 맡기셨어요</h1>
      <p class="lead center">${agent ? `${esc(agent.office)} ${esc(agent.name)}가` : "제휴 세무사가"} 1~2 영업일 안에<br><b>${esc(state.phone)}</b>로 연락드립니다.</p>
      <div class="card">
        <b>앞으로 이렇게 진행돼요</b>
        <ol class="steps-mini" style="margin-top:10px">
          <li>세무사가 진단 내용과 서류를 확인합니다</li>
          <li>추가 영수증이 필요하면 전화로 안내드립니다</li>
          <li>세무사가 세무서에 경정청구를 대신 냅니다</li>
          <li>환급금이 고객님 계좌로 바로 들어갑니다</li>
        </ol>
      </div>
      <p class="small muted">진단은 무료입니다. 수수료는 환급이 확정되었을 때만 세무사와 약정한 만큼 냅니다.</p>
      ${bottom(`<button class="btn secondary" id="back-report">진단 결과 다시 보기</button>`)}
    `;
    $("#back-report").onclick = () => go("report");
  };

  // 체험판 표시용: 위임장 화면이 아닌 곳에서 완료 화면을 열면 세무사 정보를 미리 불러 둔다.
  api("/mandate/info").then((m) => { mandateInfo = m; }).catch(() => {});

  render();
})();
