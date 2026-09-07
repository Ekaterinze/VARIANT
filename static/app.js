/* Фронтенд сайта очереди сдачи лабораторной работы. Vue 3, без сборки. */
(function () {
  "use strict";

  const { createApp, reactive, ref, computed, onMounted, onUnmounted } = Vue;

  /* ------------------------------------------------------------ уведомления */

  const toasts = reactive([]);
  let toastId = 0;

  function notify(text, kind) {
    const item = { id: ++toastId, text: text, kind: kind || "ok" };
    toasts.push(item);
    setTimeout(function () {
      const idx = toasts.findIndex(function (t) { return t.id === item.id; });
      if (idx >= 0) toasts.splice(idx, 1);
    }, 6000);
  }

  /* -------------------------------------------------------------------- API */

  async function api(url, options) {
    const opts = Object.assign({ headers: {} }, options || {});
    if (opts.body !== undefined && typeof opts.body !== "string") {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    const response = await fetch(url, opts);
    let data = {};
    try { data = await response.json(); } catch (e) { data = {}; }
    if (!response.ok) {
      const error = new Error(data.error || "Ошибка запроса (" + response.status + ")");
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function fmtCountdown(seconds) {
    if (seconds === null || seconds === undefined || seconds <= 0) return "";
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    const pad = function (n) { return n < 10 ? "0" + n : "" + n; };
    return (h > 0 ? h + " ч " : "") + pad(m) + ":" + pad(s);
  }

  /* ------------------------------------------------------------------ вход */

  const LoginView = {
    props: ["window"],
    emits: ["logged-in"],
    setup(props, ctx) {
      const fio = ref("");
      const password = ref("");
      const busy = ref(false);

      async function submit() {
        busy.value = true;
        try {
          const data = await api("/api/login", {
            method: "POST",
            body: { fio: fio.value, password: password.value },
          });
          notify("Добро пожаловать, " + data.user.full_name + "!", "ok");
          ctx.emit("logged-in", data.user);
        } catch (e) {
          notify(e.message, "error");
        } finally {
          busy.value = false;
        }
      }

      return { fio, password, busy, submit };
    },
    template: `
      <div class="login-wrap">
        <div class="card">
          <h2>Вход на сайт</h2>
          <p class="sub">Очередь сдачи лабораторной работы</p>

          <div :class="['banner', window.state]" v-if="window">
            {{ window.hint }}
            <span v-if="window.state === 'closed' && window.countdown">
              До открытия приёма: <b>{{ window.countdown }}</b>
            </span>
          </div>

          <form @submit.prevent="submit">
            <label for="fio">Фамилия и имя</label>
            <input id="fio" v-model="fio" placeholder="Иванова Анна" autocomplete="username">
            <label for="pw">Пароль (7 символов)</label>
            <input id="pw" v-model="password" type="password" maxlength="7"
                   autocomplete="current-password" placeholder="•••••••">
            <div style="margin-top:16px">
              <button type="submit" :disabled="busy">
                {{ busy ? 'Проверяем…' : 'Войти' }}
              </button>
            </div>
          </form>
          <p class="sub small" style="margin-top:16px">
            Войти можно в любое время: посмотреть своё место и договориться об обмене.
            Пожелания принимаются только с {{ window.open_from }} до {{ window.open_to }}
            и стираются каждый день в {{ window.reset_at }}.
          </p>
        </div>
      </div>
    `,
  };

  /* -------------------------------------------------------- смена пароля */

  const PasswordCard = {
    props: ["isDefault"],
    emits: ["changed"],
    setup(props, ctx) {
      const form = reactive({ old_password: "", new_password: "", new_password2: "" });
      const busy = ref(false);
      const open = ref(false);

      async function submit() {
        busy.value = true;
        try {
          const data = await api("/api/user/password", { method: "POST", body: { ...form } });
          notify(data.message, "ok");
          form.old_password = form.new_password = form.new_password2 = "";
          open.value = false;
          ctx.emit("changed");
        } catch (e) {
          notify(e.message, "error");
        } finally {
          busy.value = false;
        }
      }

      return { form, busy, open, submit };
    },
    template: `
      <div class="card">
        <h2>Мой пароль</h2>
        <p class="sub" v-if="isDefault">
          Сейчас у вас стандартный пароль, который знают все. Смените его на свой:
          ровно 7 символов, английские буквы и цифры.
        </p>
        <p class="sub" v-else>
          Пароль можно поменять в любой момент: введите старый и дважды новый
          (7 символов, английские буквы и цифры).
        </p>

        <div class="banner closed" v-if="isDefault && !open">
          Пароль по умолчанию — смените его, иначе войти сможет кто угодно.
        </div>

        <button class="ghost" v-if="!open" @click="open = true">Сменить пароль</button>

        <form v-else @submit.prevent="submit">
          <div class="row">
            <div>
              <label>Старый пароль</label>
              <input type="password" v-model="form.old_password" maxlength="7"
                     autocomplete="current-password">
            </div>
            <div>
              <label>Новый пароль</label>
              <input type="password" v-model="form.new_password" maxlength="7"
                     autocomplete="new-password">
            </div>
            <div>
              <label>Новый пароль ещё раз</label>
              <input type="password" v-model="form.new_password2" maxlength="7"
                     autocomplete="new-password">
            </div>
          </div>
          <div class="row tight" style="margin-top:14px">
            <button type="submit" :disabled="busy">Сохранить новый пароль</button>
            <button type="button" class="ghost" @click="open = false" :disabled="busy">
              Отмена
            </button>
          </div>
        </form>
      </div>
    `,
  };

  /* ------------------------------------------------------- страница участника */

  const UserView = {
    props: ["user", "window"],
    setup(props) {
      const state = reactive({ data: null, loading: true });
      const picks = reactive({ p1: null, p2: null, p3: null });
      const swapTarget = ref(null);
      const busy = ref(false);

      const placeList = computed(function () {
        const total = state.data ? state.data.places : 0;
        return Array.from({ length: total }, function (_, i) { return i + 1; });
      });

      const incoming = computed(function () {
        if (!state.data) return [];
        return state.data.swaps.filter(function (s) {
          return s.direction === "incoming" && s.status === "pending";
        });
      });
      const outgoing = computed(function () {
        if (!state.data) return [];
        return state.data.swaps.filter(function (s) {
          return s.direction === "outgoing" && s.status === "pending";
        });
      });
      const history = computed(function () {
        if (!state.data) return [];
        return state.data.swaps.filter(function (s) { return s.status !== "pending"; });
      });

      async function load() {
        try {
          const data = await api("/api/user/state");
          state.data = data;
          if (data.pref) {
            picks.p1 = data.pref.p1;
            picks.p2 = data.pref.p2;
            picks.p3 = data.pref.p3;
          }
        } catch (e) {
          notify(e.message, "error");
        } finally {
          state.loading = false;
        }
      }

      async function savePrefs() {
        busy.value = true;
        try {
          const data = await api("/api/user/prefs", { method: "POST", body: { ...picks } });
          notify(data.message, "ok");
          await load();
        } catch (e) {
          notify(e.message, "error");
        } finally {
          busy.value = false;
        }
      }

      async function dropPrefs() {
        if (!confirm("Удалить свои пожелания? Место будет назначено случайно.")) return;
        busy.value = true;
        try {
          const data = await api("/api/user/prefs", { method: "DELETE" });
          notify(data.message, "ok");
          picks.p1 = picks.p2 = picks.p3 = null;
          await load();
        } catch (e) {
          notify(e.message, "error");
        } finally {
          busy.value = false;
        }
      }

      async function proposeSwap() {
        if (!swapTarget.value) { notify("Выберите человека для обмена.", "error"); return; }
        busy.value = true;
        try {
          const data = await api("/api/user/swaps", {
            method: "POST", body: { to_user_id: swapTarget.value },
          });
          notify(data.message, "ok");
          swapTarget.value = null;
          await load();
        } catch (e) {
          notify(e.message, "error");
        } finally {
          busy.value = false;
        }
      }

      async function respond(swap, action) {
        busy.value = true;
        try {
          const data = await api("/api/user/swaps/" + swap.id, {
            method: "POST", body: { action: action },
          });
          notify(data.message, "ok");
          await load();
        } catch (e) {
          notify(e.message, "error");
        } finally {
          busy.value = false;
        }
      }

      let timer = null;
      onMounted(function () {
        load();
        timer = setInterval(load, 20000);   // подтягиваем новые заявки на обмен
      });
      onUnmounted(function () { if (timer) clearInterval(timer); });

      return { state, picks, placeList, swapTarget, busy, incoming, outgoing, history,
               savePrefs, dropPrefs, proposeSwap, respond, load };
    },
    components: { PasswordCard },
    template: `
      <div v-if="state.loading" class="card">Загружаем данные…</div>
      <div v-else-if="state.data">
        <div :class="['banner', window.state]">
          {{ window.hint }}
          <span v-if="window.state === 'closed' && window.countdown">
            До открытия: <b>{{ window.countdown }}</b>
          </span>
        </div>

        <password-card :is-default="state.data.password_is_default"
                       @changed="load"></password-card>

        <div class="card">
          <h2>Мои пожелания на {{ state.data.day }}</h2>
          <p class="sub">
            Выберите три разных места очереди (1 — сдавать первым,
            {{ state.data.places }} — последним). Пока приём открыт, выбор можно менять.
          </p>

          <div class="row">
            <div>
              <label>Первое желание</label>
              <select v-model.number="picks.p1" :disabled="!state.data.can_edit">
                <option :value="null">— не выбрано —</option>
                <option v-for="p in placeList" :key="'a'+p" :value="p">Место {{ p }}</option>
              </select>
            </div>
            <div>
              <label>Второе желание</label>
              <select v-model.number="picks.p2" :disabled="!state.data.can_edit">
                <option :value="null">— не выбрано —</option>
                <option v-for="p in placeList" :key="'b'+p" :value="p">Место {{ p }}</option>
              </select>
            </div>
            <div>
              <label>Третье желание</label>
              <select v-model.number="picks.p3" :disabled="!state.data.can_edit">
                <option :value="null">— не выбрано —</option>
                <option v-for="p in placeList" :key="'c'+p" :value="p">Место {{ p }}</option>
              </select>
            </div>
          </div>

          <div class="row tight" style="margin-top:16px">
            <button @click="savePrefs" :disabled="busy || !state.data.can_edit">
              {{ state.data.pref ? 'Сохранить изменения' : 'Зафиксировать пожелания' }}
            </button>
            <button class="danger" @click="dropPrefs"
                    :disabled="busy || !state.data.can_edit || !state.data.pref">
              Удалить пожелания
            </button>
          </div>

          <p class="small muted" style="margin-top:12px" v-if="state.data.pref">
            Зафиксировано: <b>{{ state.data.pref.p1 }}, {{ state.data.pref.p2 }},
            {{ state.data.pref.p3 }}</b> (обновлено {{ state.data.pref.updated_at }})
          </p>
          <p class="small muted" style="margin-top:12px" v-else>
            Пожелания пока не зафиксированы. Если не успеть до
            {{ window.open_to }}, место будет назначено случайно.
          </p>
          <p class="small muted" v-if="!state.data.can_edit">
            Сейчас приём пожеланий закрыт, поля недоступны для изменения.
            <template v-if="window.countdown">
              Откроется через <b>{{ window.countdown }}</b>.
            </template>
          </p>
        </div>

        <div class="card" v-if="state.data.my_result">
          <h2>Моё место в очереди</h2>
          <p class="sub">Распределение за {{ state.data.report_day }}</p>
          <div class="result-line">
            <span class="place-badge">{{ state.data.my_result.place }}</span>
            <span>
              <span class="pill" :class="state.data.my_result.status === 'satisfied' ? 'ok' : 'warn'">
                {{ state.data.my_result.status_text }}
              </span>
              <span class="pill accent" v-if="state.data.my_result.swapped"
                    style="margin-left:6px">получено обменом</span>
              <div class="small muted" v-if="state.data.my_result.wishes">
                Ваши пожелания были: {{ state.data.my_result.wishes }}
                <template v-if="state.data.my_result.rank">
                  · сработало пожелание №{{ state.data.my_result.rank }}
                </template>
              </div>
            </span>
          </div>

          <h3>Предложить обмен местами</h3>
          <div class="row">
            <div>
              <label>С кем меняемся</label>
              <select v-model.number="swapTarget">
                <option :value="null">— выберите участника —</option>
                <option v-for="c in state.data.candidates" :key="c.user_id" :value="c.user_id">
                  Место {{ c.place }} — {{ c.full_name }}
                </option>
              </select>
            </div>
            <div style="flex:0 0 auto">
              <button @click="proposeSwap" :disabled="busy">Предложить обмен</button>
            </div>
          </div>

          <h3 v-if="incoming.length">Вам предлагают обмен</h3>
          <div class="swap-item" v-for="s in incoming" :key="s.id">
            <div class="grow">
              <b>{{ s.from_name }}</b> предлагает поменяться:
              вы отдаёте место <b>{{ s.to_place }}</b>, получаете <b>{{ s.from_place }}</b>.
              <div class="small muted">Заявка №{{ s.id }} от {{ s.created_at }}</div>
            </div>
            <button class="small" @click="respond(s, 'accept')" :disabled="busy">Согласиться</button>
            <button class="small danger" @click="respond(s, 'decline')" :disabled="busy">Отказать</button>
          </div>

          <h3 v-if="outgoing.length">Ваши предложения</h3>
          <div class="swap-item" v-for="s in outgoing" :key="s.id">
            <div class="grow">
              Ждём ответа от <b>{{ s.to_name }}</b>
              (ваше место {{ s.from_place }} ↔ его место {{ s.to_place }}).
              <div class="small muted">Заявка №{{ s.id }} от {{ s.created_at }}</div>
            </div>
            <button class="small danger" @click="respond(s, 'cancel')" :disabled="busy">Отозвать</button>
          </div>

          <h3 v-if="history.length">История обменов</h3>
          <div class="scroll-x" v-if="history.length">
            <table>
              <thead>
                <tr><th>№</th><th>Кто</th><th>Кому</th><th>Места</th><th>Результат</th></tr>
              </thead>
              <tbody>
                <tr v-for="s in history" :key="s.id">
                  <td>{{ s.id }}</td>
                  <td>{{ s.from_name }}</td>
                  <td>{{ s.to_name }}</td>
                  <td>{{ s.from_place }} ↔ {{ s.to_place }}</td>
                  <td><span class="pill" :class="s.status === 'accepted' ? 'ok' : 'muted'">
                    {{ s.status_text }}</span></td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div class="card" v-else>
          <h2>Моё место в очереди</h2>
          <p class="sub" style="margin:0">
            Распределение ещё не сформировано. Как только администратор его посчитает,
            здесь появится ваше место и станет доступен обмен местами.
          </p>
        </div>
      </div>
    `,
  };

  /* --------------------------------------------------- раздел администратора */

  const AdminView = {
    props: ["user", "window"],
    setup(props) {
      const tab = ref("overview");
      const day = ref(new Date().toISOString().slice(0, 10));
      const overview = ref(null);
      const report = ref(null);
      const logs = ref(null);
      const busy = ref(false);
      const newPassword = ref(null);
      const manualPw = reactive({});
      const logFrom = ref("");
      const logTo = ref(day.value);
      const settings = reactive({ places_count: "", test_mode: false });

      async function loadOverview() {
        try {
          overview.value = await api("/api/admin/overview?day=" + day.value);
          settings.places_count = overview.value.places;
          settings.test_mode = overview.value.test_mode;
        } catch (e) { notify(e.message, "error"); }
      }

      async function loadReport(target) {
        if (target) day.value = target;
        try {
          report.value = await api("/api/admin/report?day=" + day.value);
        } catch (e) {
          report.value = null;
          notify(e.message, "error");
        }
      }

      async function loadLogs() {
        try {
          let url = "/api/admin/logs?to=" + logTo.value;
          if (logFrom.value) url += "&from=" + logFrom.value;
          logs.value = await api(url);
          logFrom.value = logs.value.from;
        } catch (e) { notify(e.message, "error"); }
      }

      function openTab(name) {
        tab.value = name;
        if (name === "overview" || name === "users") loadOverview();
        if (name === "report") loadReport();
        if (name === "logs") loadLogs();
      }

      async function compute() {
        if (!confirm("Сформировать распределение за " + day.value + "?")) return;
        busy.value = true;
        try {
          const data = await api("/api/admin/compute", {
            method: "POST", body: { day: day.value },
          });
          notify(data.message + " " + data.summary, "ok");
          await loadOverview();
          await loadReport(data.day);
          tab.value = "report";
        } catch (e) { notify(e.message, "error"); }
        finally { busy.value = false; }
      }

      async function resetNow() {
        if (!confirm("Стереть все пожелания и подготовить сайт к новому расчёту?")) return;
        busy.value = true;
        try {
          const data = await api("/api/admin/reset", { method: "POST" });
          notify(data.message, "ok");
          await loadOverview();
        } catch (e) { notify(e.message, "error"); }
        finally { busy.value = false; }
      }

      async function saveSettings() {
        busy.value = true;
        try {
          const data = await api("/api/admin/settings", {
            method: "POST",
            body: { places_count: settings.places_count, test_mode: settings.test_mode },
          });
          notify(data.message, "ok");
          await loadOverview();
        } catch (e) { notify(e.message, "error"); }
        finally { busy.value = false; }
      }

      async function changePassword(person, mode) {
        busy.value = true;
        try {
          const body = { user_id: person.id, mode: mode };
          if (mode === "manual") body.password = manualPw[person.id] || "";
          const data = await api("/api/admin/password", { method: "POST", body: body });
          newPassword.value = { full_name: data.full_name, password: data.password };
          manualPw[person.id] = "";
          notify(data.message, "ok");
        } catch (e) { notify(e.message, "error"); }
        finally { busy.value = false; }
      }

      onMounted(loadOverview);

      return { tab, day, overview, report, logs, busy, newPassword, manualPw,
               settings, logFrom, logTo, openTab, compute, resetNow, saveSettings,
               changePassword, loadOverview, loadReport, loadLogs };
    },
    components: { UserView },
    template: `
      <div class="tabs">
        <button :class="{active: tab === 'overview'}" @click="openTab('overview')">Обзор дня</button>
        <button :class="{active: tab === 'mine'}" @click="openTab('mine')">Мои пожелания</button>
        <button :class="{active: tab === 'users'}" @click="openTab('users')">Пользователи и пароли</button>
        <button :class="{active: tab === 'report'}" @click="openTab('report')">Отчёт</button>
        <button :class="{active: tab === 'logs'}" @click="openTab('logs')">Журнал</button>
      </div>

      <!-- ------------------------------------- мои пожелания (админ тоже участник) -->
      <template v-if="tab === 'mine'">
        <user-view :user="user" :window="window"></user-view>
      </template>

      <!-- ------------------------------------------------------- обзор дня -->
      <template v-if="tab === 'overview' && overview">
        <div class="card">
          <h2>Состояние на {{ overview.day }}</h2>
          <p class="sub">{{ overview.window.hint }}</p>
          <div class="row">
            <div>
              <label>Дата расчёта</label>
              <input type="date" v-model="day" @change="loadOverview">
            </div>
            <div style="flex:0 0 auto">
              <button @click="compute" :disabled="busy">Сформировать распределение</button>
            </div>
            <div style="flex:0 0 auto">
              <button class="ghost" @click="resetNow" :disabled="busy">
                Подготовить к новому дню
              </button>
            </div>
          </div>
          <p class="small muted" style="margin-top:10px">
            Пожелания прислали: <b>{{ overview.submitted }}</b> из {{ overview.people.length }}.
            Мест: {{ overview.places }}.
            Последняя автоподготовка: {{ overview.last_reset || 'ещё не выполнялась' }}
            (ежедневно в {{ overview.window.reset_at }}).
          </p>
          <p class="small" v-if="overview.meta">
            <span class="pill ok">отчёт сформирован {{ overview.meta.created_at }}</span>
            {{ overview.meta.summary }}
          </p>
        </div>

        <div class="card">
          <h2>Настройки</h2>
          <div class="row">
            <div>
              <label>Количество мест в очереди</label>
              <input type="number" v-model="settings.places_count" min="1">
            </div>
            <div>
              <label>
                <input type="checkbox" v-model="settings.test_mode" style="width:auto">
                Режим отладки: сайт открыт круглосуточно
              </label>
            </div>
            <div style="flex:0 0 auto">
              <button @click="saveSettings" :disabled="busy">Сохранить</button>
            </div>
          </div>
        </div>

        <div class="card">
          <h2>Кто прислал пожелания</h2>
          <div class="scroll-x">
            <table>
              <thead><tr><th>Участник</th><th>Пожелания</th><th>Обновлено</th></tr></thead>
              <tbody>
                <tr v-for="p in overview.people" :key="p.id">
                  <td>{{ p.full_name }}
                    <span class="pill accent" v-if="p.is_admin">админ</span></td>
                  <td>
                    <span class="pill ok" v-if="p.wishes">{{ p.wishes }}</span>
                    <span class="pill muted" v-else>нет пожеланий — место случайно</span>
                  </td>
                  <td class="small muted">{{ p.updated_at || '—' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </template>

      <!-- ------------------------------------------------------ пользователи -->
      <template v-if="tab === 'users' && overview">
        <div class="card">
          <h2>Пользователи и пароли</h2>
          <p class="sub">
            Пароль — ровно 7 символов: английские буквы и цифры. Можно сгенерировать
            случайный или задать вручную.
          </p>
          <div class="pw-box" v-if="newPassword">
            Новый пароль для <b>{{ newPassword.full_name }}</b>:
            <code>{{ newPassword.password }}</code>
            <div class="small">Сохраните его — повторно пароль показать нельзя.</div>
          </div>
          <div class="scroll-x" style="margin-top:12px">
            <table>
              <thead><tr><th>Участник</th><th style="width:260px">Задать вручную</th>
                <th style="width:230px">Действие</th></tr></thead>
              <tbody>
                <tr v-for="p in overview.people" :key="p.id">
                  <td>{{ p.full_name }}
                    <span class="pill accent" v-if="p.is_admin">админ</span></td>
                  <td><input v-model="manualPw[p.id]" maxlength="7" placeholder="например ab12cd7"></td>
                  <td>
                    <button class="small" @click="changePassword(p, 'manual')"
                            :disabled="busy">Задать</button>
                    <button class="small ghost" @click="changePassword(p, 'generate')"
                            :disabled="busy" style="margin-left:6px">Сгенерировать</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </template>

      <!-- ------------------------------------------------------------ отчёт -->
      <template v-if="tab === 'report'">
        <div class="card">
          <h2>Отчёт о распределении</h2>
          <div class="row">
            <div>
              <label>Дата</label>
              <input type="date" v-model="day">
            </div>
            <div style="flex:0 0 auto">
              <button class="ghost" @click="loadReport()">Показать</button>
            </div>
            <div v-if="overview && overview.report_days.length">
              <label>Готовые отчёты</label>
              <select @change="loadReport($event.target.value)">
                <option value="">— выбрать дату —</option>
                <option v-for="d in overview.report_days" :key="d" :value="d">{{ d }}</option>
              </select>
            </div>
          </div>

          <template v-if="report">
            <p class="small" style="margin-top:12px" v-if="report.meta">
              Сформирован: <b>{{ report.meta.created_at }}</b>,
              администратор: {{ report.meta.created_by }}<br>
              {{ report.meta.summary }}
            </p>
            <div class="dl-links">
              <a :href="'/download/report/' + report.day + '.csv'">Скачать таблицу (CSV)</a>
              <a :href="'/download/log/' + report.day + '.log'">Скачать журнал (LOG)</a>
              <a :href="'/download/report/' + report.day + '.zip'">Скачать всё архивом (ZIP)</a>
            </div>

            <div class="scroll-x" style="margin-top:16px">
              <table>
                <thead>
                  <tr><th>Место</th><th>Фамилия</th><th>Имя</th><th>Отчество</th>
                      <th>Пожелания</th><th>Результат</th></tr>
                </thead>
                <tbody>
                  <tr v-for="r in report.rows" :key="r.place">
                    <td><b>{{ r.place }}</b></td>
                    <td>{{ r.surname }}</td>
                    <td>{{ r.name }}</td>
                    <td>{{ r.patronymic }}</td>
                    <td>{{ r.wishes || '—' }}</td>
                    <td>
                      <span class="pill" :class="r.status === 'satisfied' ? 'ok' :
                            (r.status === 'missed' ? 'warn' : 'muted')">
                        {{ r.status_text }}</span>
                      <span class="pill accent" v-if="r.swapped" style="margin-left:6px">обмен</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <h3 v-if="report.swaps.length">Заявки на обмен за этот день</h3>
            <div class="scroll-x" v-if="report.swaps.length">
              <table>
                <thead><tr><th>№</th><th>От кого</th><th>Кому</th><th>Места</th>
                  <th>Статус</th><th>Создана</th></tr></thead>
                <tbody>
                  <tr v-for="s in report.swaps" :key="s.id">
                    <td>{{ s.id }}</td><td>{{ s.from_name }}</td><td>{{ s.to_name }}</td>
                    <td>{{ s.from_place }} ↔ {{ s.to_place }}</td>
                    <td><span class="pill" :class="s.status === 'accepted' ? 'ok' : 'muted'">
                      {{ s.status_text }}</span></td>
                    <td class="small muted">{{ s.created_at }}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <h3>Журнал расчёта</h3>
            <div class="logbox">{{ report.events.map(e => e.ts + ' | ' + e.actor + ' | ' +
              e.action + ' | ' + e.message).join('\\n') }}</div>
          </template>
          <p class="sub" v-else style="margin-top:12px">Отчёт за выбранную дату не найден.</p>
        </div>
      </template>

      <!-- ----------------------------------------------------------- журнал -->
      <template v-if="tab === 'logs'">
        <div class="card">
          <h2>Журнал событий</h2>
          <div class="row">
            <div><label>С даты</label><input type="date" v-model="logFrom"></div>
            <div><label>По дату</label><input type="date" v-model="logTo"></div>
            <div style="flex:0 0 auto"><button class="ghost" @click="loadLogs">Показать</button></div>
            <div style="flex:0 0 auto" v-if="logs">
              <a :href="'/download/log/' + logs.to + '.log'">Скачать журнал за {{ logs.to }}</a>
            </div>
          </div>
          <div class="logbox" style="margin-top:14px" v-if="logs">{{ logs.events.map(e =>
            e.ts + ' | ' + e.level + ' | ' + e.actor + ' | ' + e.action + ' | ' +
            e.message).join('\\n') || 'Событий за период нет.' }}</div>
        </div>
      </template>
    `,
  };

  /* ------------------------------------------------------------------ корень */

  const App = {
    setup() {
      const session = reactive({ user: null, window: { state: "closed", hint: "", countdown: "" },
                                 loaded: false, server_time: "" });

      async function refresh() {
        try {
          const data = await api("/api/session");
          session.user = data.user;
          session.window = Object.assign({ countdown: "" }, data.window);
          session.window.countdown = fmtCountdown(data.window.seconds_to_open);
          session.server_time = data.server_time;
        } catch (e) {
          notify(e.message, "error");
        } finally {
          session.loaded = true;
        }
      }

      function onLoggedIn(user) {
        session.user = user;
        refresh();
      }

      async function logout() {
        await api("/api/logout", { method: "POST" });
        session.user = null;
        notify("Вы вышли с сайта.", "ok");
        refresh();
      }

      let tick = null;
      onMounted(function () {
        refresh();
        tick = setInterval(function () {
          if (session.window.seconds_to_open > 0) {
            session.window.seconds_to_open -= 1;
            session.window.countdown = fmtCountdown(session.window.seconds_to_open);
            if (session.window.seconds_to_open <= 0) refresh();
          }
        }, 1000);
      });
      onUnmounted(function () { if (tick) clearInterval(tick); });

      return { session, toasts, onLoggedIn, logout };
    },
    components: { LoginView, UserView, AdminView },
    template: `
      <header class="top">
        <div class="inner">
          <h1>Очередь сдачи лабораторной работы</h1>
          <span class="who" v-if="session.user">
            {{ session.user.full_name }}
            <template v-if="session.user.is_admin"> · администратор</template>
          </span>
          <button class="ghost small" v-if="session.user" @click="logout">Выйти</button>
        </div>
      </header>

      <div class="wrap" v-if="session.loaded">
        <login-view v-if="!session.user" :window="session.window" @logged-in="onLoggedIn"></login-view>
        <admin-view v-else-if="session.user.is_admin" :user="session.user"
                    :window="session.window"></admin-view>
        <user-view v-else :user="session.user" :window="session.window"></user-view>
      </div>

      <div class="toast-area">
        <div v-for="t in toasts" :key="t.id" :class="['toast', t.kind]">{{ t.text }}</div>
      </div>
    `,
  };

  createApp(App).mount("#app");
})();
