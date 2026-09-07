/* Фронтенд сайта очереди сдачи лабораторной работы. Vue 3, без сборки. */
(function () {
  "use strict";

  const { createApp, reactive, ref, computed, watch, onMounted, onUnmounted } = Vue;

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
    if (!seconds || seconds <= 0) return "";
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    const pad = function (n) { return n < 10 ? "0" + n : "" + n; };
    if (d > 0) return d + " дн " + h + " ч";
    return (h > 0 ? h + " ч " : "") + pad(m) + ":" + pad(s);
  }

  const WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

  /* ------------------------------------------------------------------ вход */

  const LoginView = {
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
            Войти можно в любое время: посмотреть очередь, своё место и договориться
            об обмене. Пожелания принимаются по расписанию каждого листа — оно
            показано на странице после входа.
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

  /* ------------------------------------------------- один лист голосования */

  const ListCard = {
    props: ["list"],
    emits: ["changed"],
    setup(props, ctx) {
      const picks = reactive({ p1: null, p2: null, p3: null });
      const dirty = ref(false);
      const busy = ref(false);
      const swapTarget = ref(null);
      const showQueue = ref(true);
      const showWho = ref(false);

      function syncFromServer() {
        const pref = props.list.pref;
        picks.p1 = pref ? pref.p1 : null;
        picks.p2 = pref ? pref.p2 : null;
        picks.p3 = pref ? pref.p3 : null;
        dirty.value = false;
      }
      syncFromServer();

      // Обновление с сервера не должно затирать то, что человек уже выбрал.
      watch(function () { return props.list.pref; }, function () {
        if (!dirty.value) syncFromServer();
      });

      const placeList = computed(function () {
        return Array.from({ length: props.list.places }, function (_, i) { return i + 1; });
      });
      const countdown = computed(function () {
        return fmtCountdown(props.list.window.seconds_to_open);
      });
      const incoming = computed(function () {
        return props.list.swaps.filter(function (s) {
          return s.direction === "incoming" && s.status === "pending";
        });
      });
      const outgoing = computed(function () {
        return props.list.swaps.filter(function (s) {
          return s.direction === "outgoing" && s.status === "pending";
        });
      });

      async function save() {
        busy.value = true;
        try {
          const data = await api("/api/user/prefs", {
            method: "POST",
            body: { list_id: props.list.id, p1: picks.p1, p2: picks.p2, p3: picks.p3 },
          });
          notify(data.message, "ok");
          dirty.value = false;
          ctx.emit("changed");
        } catch (e) {
          notify(e.message, "error");
        } finally {
          busy.value = false;
        }
      }

      async function drop() {
        if (!confirm("Удалить свои пожелания в листе «" + props.list.name +
                     "»? Место будет назначено случайно.")) return;
        busy.value = true;
        try {
          const data = await api("/api/user/prefs", {
            method: "DELETE", body: { list_id: props.list.id },
          });
          notify(data.message, "ok");
          picks.p1 = picks.p2 = picks.p3 = null;
          dirty.value = false;
          ctx.emit("changed");
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
            method: "POST",
            body: { list_id: props.list.id, to_user_id: swapTarget.value },
          });
          notify(data.message, "ok");
          swapTarget.value = null;
          ctx.emit("changed");
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
          ctx.emit("changed");
        } catch (e) {
          notify(e.message, "error");
        } finally {
          busy.value = false;
        }
      }

      return { picks, dirty, busy, swapTarget, showQueue, showWho, placeList,
               countdown, incoming, outgoing, save, drop, proposeSwap, respond };
    },
    template: `
      <div class="card">
        <div class="list-head">
          <h2>{{ list.name }}</h2>
          <span class="pill" :class="list.window.state === 'open' ? 'ok' : 'muted'">
            {{ list.window.state === 'open' ? 'приём открыт' : 'приём закрыт' }}
          </span>
          <span class="pill accent" v-if="list.scheduled_today">сегодня</span>
        </div>
        <p class="sub" v-if="list.description">{{ list.description }}</p>
        <p class="small muted">
          Расписание: {{ list.schedule_text }} · мест в очереди: {{ list.places }} ·
          участников: {{ list.members_total }}
        </p>

        <div :class="['banner', list.window.state]">
          {{ list.window.hint }}
          <span v-if="list.window.state === 'closed' && countdown">
            До открытия: <b>{{ countdown }}</b>
          </span>
        </div>

        <div class="row">
          <div>
            <label>Первое желание</label>
            <select v-model.number="picks.p1" :disabled="!list.can_edit"
                    @change="dirty = true">
              <option :value="null">— не выбрано —</option>
              <option v-for="p in placeList" :key="'a'+p" :value="p">Место {{ p }}</option>
            </select>
          </div>
          <div>
            <label>Второе желание</label>
            <select v-model.number="picks.p2" :disabled="!list.can_edit"
                    @change="dirty = true">
              <option :value="null">— не выбрано —</option>
              <option v-for="p in placeList" :key="'b'+p" :value="p">Место {{ p }}</option>
            </select>
          </div>
          <div>
            <label>Третье желание</label>
            <select v-model.number="picks.p3" :disabled="!list.can_edit"
                    @change="dirty = true">
              <option :value="null">— не выбрано —</option>
              <option v-for="p in placeList" :key="'c'+p" :value="p">Место {{ p }}</option>
            </select>
          </div>
        </div>

        <div class="row tight" style="margin-top:14px">
          <button @click="save" :disabled="busy || !list.can_edit">
            {{ list.pref ? 'Сохранить изменения' : 'Зафиксировать пожелания' }}
          </button>
          <button class="danger" @click="drop"
                  :disabled="busy || !list.can_edit || !list.pref">
            Удалить пожелания
          </button>
        </div>

        <p class="small muted" style="margin-top:10px" v-if="list.pref">
          Зафиксировано: <b>{{ list.pref.p1 }}, {{ list.pref.p2 }}, {{ list.pref.p3 }}</b>
          (обновлено {{ list.pref.updated_at }})
        </p>
        <p class="small muted" style="margin-top:10px" v-else>
          Пожелания не зафиксированы. Если не успеть до {{ list.open_to }},
          место будет назначено случайно.
        </p>

        <!-- кто уже проголосовал -->
        <h3 @click="showWho = !showWho" class="clickable">
          Проголосовали: {{ list.voted.length }} из {{ list.members_total }}
          <span class="small">{{ showWho ? '▲ скрыть' : '▼ показать' }}</span>
        </h3>
        <div class="row" v-if="showWho">
          <div>
            <div class="small muted">Отдали пожелания ({{ list.voted.length }})</div>
            <ul class="names">
              <li v-for="p in list.voted" :key="'v'+p.full_name"
                  :class="{me: p.is_me}">{{ p.full_name }}</li>
              <li v-if="!list.voted.length" class="muted">пока никто</li>
            </ul>
          </div>
          <div>
            <div class="small muted">Ещё не голосовали ({{ list.not_voted.length }})</div>
            <ul class="names">
              <li v-for="p in list.not_voted" :key="'n'+p.full_name"
                  :class="{me: p.is_me}">{{ p.full_name }}</li>
              <li v-if="!list.not_voted.length" class="muted">все проголосовали</li>
            </ul>
          </div>
        </div>

        <!-- результат -->
        <template v-if="list.my_result">
          <h3>Моё место</h3>
          <div class="result-line">
            <span class="place-badge">{{ list.my_result.place }}</span>
            <span>
              <span class="pill" :class="list.my_result.status === 'satisfied' ? 'ok' : 'warn'">
                {{ list.my_result.status_text }}
              </span>
              <span class="pill accent" v-if="list.my_result.swapped"
                    style="margin-left:6px">получено обменом</span>
              <div class="small muted" v-if="list.my_result.wishes">
                Ваши пожелания: {{ list.my_result.wishes }}
                <template v-if="list.my_result.rank">
                  · сработало пожелание №{{ list.my_result.rank }}
                </template>
              </div>
              <div class="small muted">Распределение за {{ list.report_day }}</div>
            </span>
          </div>

          <h3>Предложить обмен местами</h3>
          <div class="row">
            <div>
              <label>С кем меняемся</label>
              <select v-model.number="swapTarget">
                <option :value="null">— выберите участника —</option>
                <option v-for="c in list.candidates" :key="c.user_id" :value="c.user_id">
                  Место {{ c.place }} — {{ c.full_name }}
                </option>
              </select>
            </div>
            <div style="flex:0 0 auto">
              <button @click="proposeSwap" :disabled="busy">Предложить обмен</button>
            </div>
          </div>

          <div class="swap-item" v-for="s in incoming" :key="s.id">
            <div class="grow">
              <b>{{ s.from_name }}</b> предлагает поменяться: вы отдаёте место
              <b>{{ s.to_place }}</b>, получаете <b>{{ s.from_place }}</b>.
              <div class="small muted">Заявка №{{ s.id }} от {{ s.created_at }}</div>
            </div>
            <button class="small" @click="respond(s, 'accept')" :disabled="busy">Согласиться</button>
            <button class="small danger" @click="respond(s, 'decline')" :disabled="busy">Отказать</button>
          </div>

          <div class="swap-item" v-for="s in outgoing" :key="s.id">
            <div class="grow">
              Ждём ответа от <b>{{ s.to_name }}</b>
              (ваше место {{ s.from_place }} ↔ его место {{ s.to_place }}).
              <div class="small muted">Заявка №{{ s.id }} от {{ s.created_at }}</div>
            </div>
            <button class="small danger" @click="respond(s, 'cancel')" :disabled="busy">Отозвать</button>
          </div>
        </template>

        <!-- очередь целиком -->
        <template v-if="list.queue.length">
          <h3 @click="showQueue = !showQueue" class="clickable">
            Очередь за {{ list.report_day }}
            <span class="small">{{ showQueue ? '▲ скрыть' : '▼ показать' }}</span>
          </h3>
          <div class="scroll-x" v-if="showQueue">
            <table>
              <thead>
                <tr><th>Место</th><th>Участник</th><th>Как получено место</th></tr>
              </thead>
              <tbody>
                <tr v-for="q in list.queue" :key="q.place" :class="{me: q.is_me}">
                  <td><b>{{ q.place }}</b></td>
                  <td>{{ q.full_name }}<span class="pill accent" v-if="q.is_me"
                      style="margin-left:6px">вы</span></td>
                  <td>
                    <span class="pill" :class="q.status === 'satisfied' ? 'ok' :
                          (q.status === 'missed' ? 'warn' : 'muted')">
                      {{ q.voted ? 'голосовал' : 'не голосовал' }} — {{ q.status_text }}
                    </span>
                    <span class="pill accent" v-if="q.swapped" style="margin-left:6px">обмен</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>
        <p class="small muted" v-else-if="!list.my_result">
          Распределение ещё не сформировано. После расчёта здесь появится вся очередь.
        </p>
      </div>
    `,
  };

  /* ------------------------------------------------------- страница участника */

  const UserView = {
    setup() {
      const state = reactive({ data: null, loading: true });

      const todayLists = computed(function () {
        if (!state.data) return [];
        return state.data.lists.filter(function (l) { return l.scheduled_today; });
      });
      const otherLists = computed(function () {
        if (!state.data) return [];
        return state.data.lists.filter(function (l) { return !l.scheduled_today; });
      });

      async function load() {
        try {
          state.data = await api("/api/user/state");
        } catch (e) {
          notify(e.message, "error");
        } finally {
          state.loading = false;
        }
      }

      let timer = null;
      onMounted(function () {
        load();
        timer = setInterval(load, 20000);   // подтягиваем чужие голоса и заявки
      });
      onUnmounted(function () { if (timer) clearInterval(timer); });

      return { state, todayLists, otherLists, load };
    },
    components: { PasswordCard, ListCard },
    template: `
      <div v-if="state.loading" class="card">Загружаем данные…</div>
      <div v-else-if="state.data">
        <password-card :is-default="state.data.password_is_default"
                       @changed="load"></password-card>

        <div class="card" v-if="!state.data.lists.length">
          <h2>Листов пока нет</h2>
          <p class="sub" style="margin:0">
            Администратор ещё не включил вас ни в один лист голосования.
          </p>
        </div>

        <template v-if="todayLists.length">
          <h2 class="section">Сегодня, {{ state.data.day }}</h2>
          <list-card v-for="l in todayLists" :key="l.id" :list="l"
                     @changed="load"></list-card>
        </template>

        <template v-if="otherLists.length">
          <h2 class="section">Другие мои листы</h2>
          <list-card v-for="l in otherLists" :key="l.id" :list="l"
                     @changed="load"></list-card>
        </template>
      </div>
    `,
  };

  /* ------------------------------------------------------- листы: редактор */

  const emptyForm = function () {
    return { id: null, name: "", description: "", weekdays: "1234567",
             open_from: "20:00", open_to: "21:00", places: 0, active: true,
             member_ids: [] };
  };

  const ListsTab = {
    props: ["data"],
    emits: ["changed"],
    setup(props, ctx) {
      const form = reactive(emptyForm());
      const editing = ref(false);
      const busy = ref(false);

      function startNew() {
        Object.assign(form, emptyForm());
        editing.value = true;
      }

      function startEdit(list) {
        Object.assign(form, {
          id: list.id, name: list.name, description: list.description,
          weekdays: list.weekdays, open_from: list.open_from, open_to: list.open_to,
          places: list.places_setting, active: list.active,
          member_ids: list.member_ids.slice(),
        });
        editing.value = true;
      }

      function toggleDay(index) {
        const digit = String(index + 1);
        form.weekdays = form.weekdays.includes(digit)
          ? form.weekdays.split(digit).join("")
          : (form.weekdays + digit).split("").sort().join("");
      }

      function toggleMember(id) {
        const idx = form.member_ids.indexOf(id);
        if (idx >= 0) form.member_ids.splice(idx, 1);
        else form.member_ids.push(id);
      }

      function allMembers() {
        form.member_ids = props.data.users.map(function (u) { return u.id; });
      }

      function noMembers() { form.member_ids = []; }

      async function save() {
        busy.value = true;
        try {
          const url = form.id ? "/api/admin/lists/" + form.id : "/api/admin/lists";
          const data = await api(url, { method: "POST", body: { ...form } });
          notify(data.message, "ok");
          editing.value = false;
          ctx.emit("changed");
        } catch (e) {
          notify(e.message, "error");
        } finally {
          busy.value = false;
        }
      }

      async function remove(list) {
        if (!confirm("Удалить лист «" + list.name +
                     "» вместе со всеми его пожеланиями и распределениями?")) return;
        busy.value = true;
        try {
          const data = await api("/api/admin/lists/" + list.id + "/delete",
                                 { method: "POST" });
          notify(data.message, "ok");
          ctx.emit("changed");
        } catch (e) {
          notify(e.message, "error");
        } finally {
          busy.value = false;
        }
      }

      return { form, editing, busy, WEEKDAYS, startNew, startEdit, toggleDay,
               toggleMember, allMembers, noMembers, save, remove };
    },
    template: `
      <div class="card">
        <h2>Листы голосования</h2>
        <p class="sub">
          Лист — это отдельное голосование: своё название, описание, состав людей,
          дни недели и время приёма пожеланий. Приём открывается автоматически,
          а за {{ data.reset_lead }} минут до открытия старые пожелания стираются.
        </p>
        <button @click="startNew" :disabled="busy">Создать лист</button>

        <div class="scroll-x" style="margin-top:14px" v-if="data.lists.length">
          <table>
            <thead>
              <tr><th>Название</th><th>Расписание</th><th>Участники</th>
                  <th>Мест</th><th>Состояние</th><th></th></tr>
            </thead>
            <tbody>
              <tr v-for="l in data.lists" :key="l.id">
                <td>
                  <b>{{ l.name }}</b>
                  <div class="small muted" v-if="l.description">{{ l.description }}</div>
                </td>
                <td class="small">{{ l.schedule_text }}</td>
                <td>{{ l.members_total }}</td>
                <td>{{ l.places }}</td>
                <td>
                  <span class="pill" :class="l.window.state === 'open' ? 'ok' : 'muted'">
                    {{ l.window.state === 'open' ? 'приём открыт' : 'приём закрыт' }}
                  </span>
                  <span class="pill warn" v-if="!l.active" style="margin-left:6px">выключен</span>
                </td>
                <td>
                  <button class="small ghost" @click="startEdit(l)" :disabled="busy">Изменить</button>
                  <button class="small danger" @click="remove(l)" :disabled="busy"
                          style="margin-left:6px">Удалить</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="sub" v-else style="margin-top:12px">Пока не создано ни одного листа.</p>
      </div>

      <div class="card" v-if="editing">
        <h2>{{ form.id ? 'Изменение листа' : 'Новый лист' }}</h2>
        <div class="row">
          <div style="flex:2 1 260px">
            <label>Название</label>
            <input v-model="form.name" placeholder="Лабораторная работа №2">
          </div>
          <div style="flex:0 0 140px">
            <label>Начало приёма</label>
            <input type="time" v-model="form.open_from">
          </div>
          <div style="flex:0 0 140px">
            <label>Конец приёма</label>
            <input type="time" v-model="form.open_to">
          </div>
          <div style="flex:0 0 160px">
            <label>Мест (0 — по числу людей)</label>
            <input type="number" min="0" v-model.number="form.places">
          </div>
        </div>

        <label>Описание (видят участники)</label>
        <input v-model="form.description" placeholder="Кратко: что сдаём и где">

        <label>Дни повторения</label>
        <div class="row tight">
          <button v-for="(day, i) in WEEKDAYS" :key="day" type="button"
                  :class="['small', form.weekdays.includes(String(i+1)) ? '' : 'ghost']"
                  @click="toggleDay(i)">{{ day }}</button>
        </div>

        <label style="margin-top:14px">
          <input type="checkbox" v-model="form.active" style="width:auto"> Лист включён
        </label>

        <h3>Участники листа ({{ form.member_ids.length }})</h3>
        <div class="row tight" style="margin-bottom:8px">
          <button class="small ghost" type="button" @click="allMembers">Выбрать всех</button>
          <button class="small ghost" type="button" @click="noMembers">Снять всех</button>
        </div>
        <div class="members">
          <label v-for="u in data.users" :key="u.id" class="member">
            <input type="checkbox" :checked="form.member_ids.includes(u.id)"
                   @change="toggleMember(u.id)" style="width:auto">
            {{ u.full_name }}
          </label>
        </div>

        <div class="row tight" style="margin-top:16px">
          <button @click="save" :disabled="busy">Сохранить лист</button>
          <button class="ghost" @click="editing = false" :disabled="busy">Отмена</button>
        </div>
      </div>
    `,
  };

  /* --------------------------------------------------- раздел администратора */

  const AdminView = {
    props: ["user"],
    setup(props) {
      const tab = ref("lists");
      const day = ref(new Date().toISOString().slice(0, 10));
      const listsData = ref(null);
      const overview = ref(null);
      const report = ref(null);
      const logs = ref(null);
      const busy = ref(false);
      const newPassword = ref(null);
      const manualPw = reactive({});
      const logFrom = ref("");
      const logTo = ref(day.value);
      const currentList = ref(null);
      const reportList = ref(null);
      const testMode = ref(false);

      async function loadLists() {
        try {
          listsData.value = await api("/api/admin/lists?day=" + day.value);
          testMode.value = listsData.value.test_mode;
          if (!currentList.value && listsData.value.lists.length) {
            currentList.value = listsData.value.lists[0].id;
            reportList.value = currentList.value;
          }
        } catch (e) { notify(e.message, "error"); }
      }

      async function loadOverview() {
        try {
          let url = "/api/admin/overview?day=" + day.value;
          if (currentList.value) url += "&list_id=" + currentList.value;
          overview.value = await api(url);
          if (overview.value.list) currentList.value = overview.value.list.id;
        } catch (e) { notify(e.message, "error"); }
      }

      async function loadReport(targetDay) {
        if (targetDay) day.value = targetDay;
        if (!reportList.value) { report.value = null; return; }
        try {
          report.value = await api("/api/admin/report?list_id=" + reportList.value +
                                   "&day=" + day.value);
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
        if (name === "lists" || name === "users") loadLists();
        if (name === "overview") { loadLists(); loadOverview(); }
        if (name === "report") { loadLists(); loadReport(); }
        if (name === "logs") loadLogs();
      }

      async function compute() {
        if (!currentList.value) { notify("Выберите лист.", "error"); return; }
        if (!confirm("Сформировать распределение за " + day.value + "?")) return;
        busy.value = true;
        try {
          const data = await api("/api/admin/compute", {
            method: "POST", body: { list_id: currentList.value, day: day.value },
          });
          notify(data.message + " " + data.summary, "ok");
          reportList.value = data.list_id;
          await loadLists();
          await loadOverview();
          await loadReport(data.day);
          tab.value = "report";
        } catch (e) { notify(e.message, "error"); }
        finally { busy.value = false; }
      }

      async function resetNow(listId) {
        if (!confirm(listId ? "Стереть пожелания этого листа?"
                            : "Стереть пожелания во всех листах?")) return;
        busy.value = true;
        try {
          const data = await api("/api/admin/reset", {
            method: "POST", body: listId ? { list_id: listId } : {},
          });
          notify(data.message, "ok");
          await loadLists();
          await loadOverview();
        } catch (e) { notify(e.message, "error"); }
        finally { busy.value = false; }
      }

      async function saveSettings() {
        busy.value = true;
        try {
          const data = await api("/api/admin/settings", {
            method: "POST", body: { test_mode: testMode.value },
          });
          notify(data.message, "ok");
          await loadLists();
        } catch (e) { notify(e.message, "error"); }
        finally { busy.value = false; }
      }

      const importFile = ref(null);
      const importList = ref(null);
      const importDay = ref("");
      const importResult = ref(null);

      function pickFile(event) {
        importFile.value = event.target.files[0] || null;
        importResult.value = null;
      }

      async function uploadReport() {
        if (!importFile.value) { notify("Выберите файл отчёта.", "error"); return; }
        busy.value = true;
        try {
          const form = new FormData();
          form.append("file", importFile.value);
          if (importList.value) form.append("list_id", importList.value);
          if (importDay.value) form.append("day", importDay.value);
          const response = await fetch("/api/admin/import", { method: "POST", body: form });
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || "Ошибка загрузки");
          notify(data.message, "ok");
          importResult.value = data;
          reportList.value = data.list_id;
          day.value = data.day;
          await loadLists();
          await loadReport(data.day);
        } catch (e) {
          notify(e.message, "error");
        } finally {
          busy.value = false;
        }
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

      onMounted(loadLists);

      return { tab, day, listsData, overview, report, logs, busy, newPassword,
               manualPw, logFrom, logTo, currentList, reportList, testMode,
               importFile, importList, importDay, importResult, pickFile,
               uploadReport, openTab, compute, resetNow, saveSettings,
               changePassword, loadLists, loadOverview, loadReport, loadLogs };
    },
    components: { UserView, ListsTab },
    template: `
      <div class="tabs">
        <button :class="{active: tab === 'lists'}" @click="openTab('lists')">Листы</button>
        <button :class="{active: tab === 'overview'}" @click="openTab('overview')">Обзор дня</button>
        <button :class="{active: tab === 'mine'}" @click="openTab('mine')">Мои пожелания</button>
        <button :class="{active: tab === 'users'}" @click="openTab('users')">Пользователи и пароли</button>
        <button :class="{active: tab === 'report'}" @click="openTab('report')">Отчёт</button>
        <button :class="{active: tab === 'logs'}" @click="openTab('logs')">Журнал</button>
      </div>

      <!-- --------------------------------------------------------- листы -->
      <template v-if="tab === 'lists' && listsData">
        <lists-tab :data="listsData" @changed="loadLists"></lists-tab>

        <div class="card">
          <h2>Общие настройки</h2>
          <div class="row">
            <div>
              <label>
                <input type="checkbox" v-model="testMode" style="width:auto">
                Режим отладки: приём пожеланий открыт круглосуточно во всех листах
              </label>
            </div>
            <div style="flex:0 0 auto">
              <button @click="saveSettings" :disabled="busy">Сохранить</button>
            </div>
            <div style="flex:0 0 auto">
              <button class="ghost" @click="resetNow(null)" :disabled="busy">
                Стереть пожелания во всех листах
              </button>
            </div>
          </div>
        </div>
      </template>

      <!-- ------------------------------------------------------- обзор дня -->
      <template v-if="tab === 'overview' && overview">
        <div class="card">
          <h2>Обзор дня</h2>
          <div class="row">
            <div>
              <label>Лист</label>
              <select v-model.number="currentList" @change="loadOverview">
                <option v-for="l in overview.lists" :key="l.id" :value="l.id">{{ l.name }}</option>
              </select>
            </div>
            <div>
              <label>Дата расчёта</label>
              <input type="date" v-model="day" @change="loadOverview">
            </div>
            <div style="flex:0 0 auto">
              <button @click="compute" :disabled="busy">Сформировать распределение</button>
            </div>
            <div style="flex:0 0 auto">
              <button class="ghost" @click="resetNow(currentList)" :disabled="busy">
                Подготовить лист заново
              </button>
            </div>
          </div>

          <template v-if="overview.list">
            <p class="small muted" style="margin-top:10px">
              {{ overview.list.schedule_text }} · мест: {{ overview.list.places }} ·
              пожелания прислали: <b>{{ overview.submitted }}</b> из
              {{ overview.list.members_total }} ·
              последняя подготовка: {{ overview.list.last_reset || 'ещё не выполнялась' }}
            </p>
            <p class="small" v-if="overview.list.meta">
              <span class="pill ok">отчёт сформирован {{ overview.list.meta.created_at }}</span>
              {{ overview.list.meta.summary }}
            </p>
          </template>
        </div>

        <div class="card" v-if="overview.list">
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

      <!-- ------------------------------------- мои пожелания (админ — участник) -->
      <template v-if="tab === 'mine'">
        <user-view></user-view>
      </template>

      <!-- ------------------------------------------------------ пользователи -->
      <template v-if="tab === 'users' && listsData">
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
                <tr v-for="p in listsData.users" :key="p.id">
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
          <h2>Загрузить готовое распределение из файла</h2>
          <p class="sub">
            Сюда можно вернуть ранее скачанный отчёт (<b>Очередь_…csv</b>) — например,
            если хостинг перезапустился и данные пропали, или если очередь поправили
            в Excel. Лист и дата берутся из самого файла; если листа с таким названием
            нет или дата не указана, выберите их вручную.
          </p>
          <div class="row">
            <div>
              <label>Файл отчёта (CSV)</label>
              <input type="file" accept=".csv,text/csv" @change="pickFile">
            </div>
            <div v-if="listsData">
              <label>Лист (по умолчанию — из файла)</label>
              <select v-model="importList">
                <option :value="null">— определить по названию в файле —</option>
                <option v-for="l in listsData.lists" :key="l.id" :value="l.id">{{ l.name }}</option>
              </select>
            </div>
            <div>
              <label>Дата (по умолчанию — из файла)</label>
              <input type="date" v-model="importDay">
            </div>
            <div style="flex:0 0 auto">
              <button @click="uploadReport" :disabled="busy || !importFile">
                Загрузить отчёт
              </button>
            </div>
          </div>
          <div class="banner open" v-if="importResult" style="margin-top:14px">
            Загружено строк: <b>{{ importResult.imported }}</b> за {{ importResult.day }}.
            <div class="small" v-for="w in importResult.warnings" :key="w">⚠ {{ w }}</div>
          </div>
        </div>

        <div class="card">
          <h2>Отчёт о распределении</h2>
          <div class="row">
            <div v-if="listsData">
              <label>Лист</label>
              <select v-model.number="reportList" @change="loadReport()">
                <option v-for="l in listsData.lists" :key="l.id" :value="l.id">{{ l.name }}</option>
              </select>
            </div>
            <div>
              <label>Дата</label>
              <input type="date" v-model="day">
            </div>
            <div style="flex:0 0 auto">
              <button class="ghost" @click="loadReport()">Показать</button>
            </div>
          </div>

          <template v-if="report">
            <p class="small" style="margin-top:12px">
              <b>{{ report.list.name }}</b> — {{ report.list.schedule_text }}<br>
              <template v-if="report.meta">
                Сформирован: <b>{{ report.meta.created_at }}</b>,
                администратор: {{ report.meta.created_by }}<br>
                {{ report.meta.summary }}
              </template>
            </p>
            <div class="dl-links">
              <a :href="'/download/report/' + report.list.id + '/' + report.day + '.csv'">
                Скачать таблицу (CSV)</a>
              <a :href="'/download/log/' + report.day + '.log'">Скачать журнал (LOG)</a>
              <a :href="'/download/report/' + report.list.id + '/' + report.day + '.zip'">
                Скачать всё архивом (ZIP)</a>
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

            <h3 v-if="report.swaps.length">Заявки на обмен</h3>
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

            <h3>Журнал за этот день</h3>
            <div class="logbox">{{ report.events.map(e => e.ts + ' | ' + e.actor + ' | ' +
              e.action + ' | ' + e.message).join('\\n') }}</div>
          </template>
          <p class="sub" v-else style="margin-top:12px">
            Отчёт за выбранные лист и дату не найден.
          </p>
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
      const session = reactive({ user: null, loaded: false, server_time: "" });

      async function refresh() {
        try {
          const data = await api("/api/session");
          session.user = data.user;
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

      onMounted(refresh);
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
        <login-view v-if="!session.user" @logged-in="onLoggedIn"></login-view>
        <admin-view v-else-if="session.user.is_admin" :user="session.user"></admin-view>
        <user-view v-else></user-view>
      </div>

      <div class="toast-area">
        <div v-for="t in toasts" :key="t.id" :class="['toast', t.kind]">{{ t.text }}</div>
      </div>
    `,
  };

  createApp(App).mount("#app");
})();
