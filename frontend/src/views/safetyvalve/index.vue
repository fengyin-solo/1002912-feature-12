<template>
  <section class="page" data-module="safetyvalve">
    <header class="page-head">
      <div>
        <h2>安全阀校验管理</h2>
        <p class="page-desc">
          按上次校验日期与整定压力档位自动推算下次校验日；超期或 7 天内到期的阀门前置并说明缘由，超年限阀门另行提示。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记安全阀</button>
        <button class="btn" type="button" :disabled="recalculating" @click="recalcAll">
          {{ recalculating ? '重算中…' : '按新规则重算排期' }}
        </button>
        <button class="btn" type="button" @click="exportRows">导出台账与清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card" :class="item.cls">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p v-if="ruleText" class="rule-hint">{{ ruleText }}</p>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>安全阀编号</span>
        <input v-model="keyword" placeholder="按安全阀编号检索" />
      </label>
      <div class="status-tabs">
        <button
          v-for="tab in statusTabs"
          :key="tab.value"
          type="button"
          class="btn"
          :class="{ active: activeStatus === tab.value }"
          @click="selectStatus(tab.value)"
        >
          {{ tab.label }}<em v-if="tab.count !== null">（{{ tab.count }}）</em>
        </button>
      </div>
      <button class="btn primary" type="submit">查询</button>
    </form>

    <table class="data-table valve-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="String(row.id)"
          :class="rowClass(row)"
        >
          <td>{{ row['安全阀编号'] ?? '—' }}</td>
          <td>{{ row['所属设备'] ?? '—' }}</td>
          <td>{{ row['公称通径'] ?? '—' }}</td>
          <td>{{ row['整定压力'] || '未填写' }}</td>
          <td>
            <span class="tier-tag">{{ row['压力档位'] || '未分档' }}</span>
          </td>
          <td>{{ row['校验日期'] || '—' }}</td>
          <td>
            <strong v-if="row['下次校验日']">{{ row['下次校验日'] }}</strong>
            <span v-else class="muted">不参与排期</span>
          </td>
          <td>{{ row['校验结论'] || '—' }}</td>
          <td><span class="status-tag" :data-status="row['安全阀状态']">{{ row['安全阀状态'] }}</span></td>
          <td class="reason-cell">
            <span v-if="row['提醒缘由']">{{ row['提醒缘由'] }}</span>
            <span v-else class="muted">—</span>
          </td>
          <td class="row-actions">
            <template v-if="row['安全阀状态'] !== '已报废'">
              <button class="link" type="button" @click="runAction('安排校验', row)">安排校验</button>
              <button class="link" type="button" @click="openRegister(row)">登记合格</button>
              <button class="link" type="button" @click="openEdit(row)">改整定压力</button>
              <button class="link danger-link" type="button" @click="runAction('申请报废', row)">申请报废</button>
            </template>
            <button class="link" type="button" @click="openRecords(row)">校验记录</button>
            <span v-if="row['安全阀状态'] === '已报废'" class="muted">已不参与排期</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无安全阀数据，可先登记安全阀</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 只安全阀（超期/临期自动前置，已报废沉底）</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-if="okMessage" class="ok-text">{{ okMessage }}</span>
    </footer>

    <!-- 登记合格 -->
    <div v-if="registerTarget" class="modal-mask" @click.self="closeModals">
      <div class="modal">
        <h3>登记合格校验 — {{ registerTarget['安全阀编号'] }}</h3>
        <p class="muted">下次校验日按本次校验日期 + 当前整定压力档位周期自动算出；同阀同日重复登记只认第一次。</p>
        <label><span>校验日期 *</span><input v-model="registerForm['校验日期']" type="date" /></label>
        <label><span>整定压力（MPa，留空用台账值 {{ registerTarget['整定压力'] || '未填' }}）</span>
          <input v-model="registerForm['整定压力']" placeholder="如 2.5" />
        </label>
        <label><span>校验机构</span><input v-model="registerForm['校验机构']" placeholder="如 市特检院" /></label>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="closeModals">取消</button>
          <button class="btn primary" type="button" :disabled="saving" @click="submitRegister">提交合格登记</button>
        </div>
      </div>
    </div>

    <!-- 修改台账（整定压力/投用日期/年限） -->
    <div v-if="editTarget" class="modal-mask" @click.self="closeModals">
      <div class="modal">
        <h3>修改台账 — {{ editTarget['安全阀编号'] }}</h3>
        <p class="muted">整定压力变更后，该阀历次合格记录与下次校验日立即按新档位重算。</p>
        <label><span>整定压力（MPa）</span><input v-model="editForm['整定压力']" placeholder="如 11.0" /></label>
        <label><span>投用日期</span><input v-model="editForm['投用日期']" type="date" /></label>
        <label><span>使用年限（年）</span><input v-model="editForm['使用年限']" placeholder="默认 15" /></label>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="closeModals">取消</button>
          <button class="btn primary" type="button" :disabled="saving" @click="submitEdit">保存并重算</button>
        </div>
      </div>
    </div>

    <!-- 新建台账 -->
    <div v-if="creating" class="modal-mask" @click.self="closeModals">
      <div class="modal">
        <h3>登记安全阀</h3>
        <label><span>安全阀编号 *</span><input v-model="createForm['安全阀编号']" /></label>
        <label><span>所属设备 *</span><input v-model="createForm['所属设备']" /></label>
        <label><span>公称通径 *</span><input v-model="createForm['公称通径']" placeholder="如 DN50" /></label>
        <label><span>整定压力（MPa）</span><input v-model="createForm['整定压力']" placeholder="如 2.5" /></label>
        <label><span>投用日期</span><input v-model="createForm['投用日期']" type="date" /></label>
        <label><span>使用年限（年）</span><input v-model="createForm['使用年限']" placeholder="默认 15" /></label>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="closeModals">取消</button>
          <button class="btn primary" type="button" :disabled="saving" @click="submitCreate">登记</button>
        </div>
      </div>
    </div>

    <!-- 校验记录清单 -->
    <div v-if="recordsTarget" class="modal-mask" @click.self="closeModals">
      <div class="modal wide">
        <h3>合格校验记录 — {{ recordsTarget['安全阀编号'] }}</h3>
        <table class="data-table">
          <thead>
            <tr><th>校验日期</th><th>整定压力</th><th>档位</th><th>下次校验日</th><th>结论</th><th>校验机构</th></tr>
          </thead>
          <tbody>
            <tr v-for="rec in valveRecords" :key="String(rec.id)">
              <td>{{ rec['校验日期'] }}</td>
              <td>{{ rec['整定压力'] }}</td>
              <td>{{ rec['压力档位'] }}</td>
              <td>{{ rec['下次校验日'] }}</td>
              <td>{{ rec['校验结论'] }}</td>
              <td>{{ rec['校验机构'] || '—' }}</td>
            </tr>
            <tr v-if="!valveRecords.length">
              <td colspan="6" class="empty-state">尚无合格校验记录</td>
            </tr>
          </tbody>
        </table>
        <div class="modal-actions">
          <button class="btn primary" type="button" @click="closeModals">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>
type RecordRow = Record<string, string | number | null>

const ENDPOINT = '/api/safetyvalve'
const columns = ['安全阀编号', '所属设备', '公称通径', '整定压力', '压力档位', '校验日期', '下次校验日', '校验结论', '安全阀状态', '提醒缘由']

const rows = ref<Row[]>([])
const total = ref(0)
const summary = ref<Record<string, number>>({})
const ruleText = ref('')
const errorMessage = ref('')
const okMessage = ref('')
const keyword = ref('')
const activeStatus = ref<string>('')
const recalculating = ref(false)
const saving = ref(false)

const registerTarget = ref<Row | null>(null)
const registerForm = ref<Record<string, string>>({})
const editTarget = ref<Row | null>(null)
const editForm = ref<Record<string, string>>({})
const createForm = ref<Record<string, string>>({})
const creating = ref(false)
const recordsTarget = ref<Row | null>(null)
const valveRecords = ref<RecordRow[]>([])

const stats = computed(() => [
  { label: '校验超期', value: summary.value['校验超期'] ?? 0, cls: 'stat-danger' },
  { label: '7 天内到期', value: summary.value['即将到期'] ?? 0, cls: 'stat-warn' },
  { label: '待首检', value: summary.value['待校验'] ?? 0, cls: '' },
  { label: '校验合格', value: summary.value['校验合格'] ?? 0, cls: '' },
  { label: '超使用年限', value: summary.value['超年限'] ?? 0, cls: 'stat-danger' },
  { label: '已报废（不排期）', value: summary.value['已报废'] ?? 0, cls: 'stat-muted' },
])

const statusTabs = computed(() => [
  { label: '全部', value: '', count: summary.value['在役总数'] ?? null },
  { label: '校验超期', value: '校验超期', count: summary.value['校验超期'] ?? null },
  { label: '即将到期', value: '即将到期', count: summary.value['即将到期'] ?? null },
  { label: '待校验', value: '待校验', count: summary.value['待校验'] ?? null },
  { label: '校验合格', value: '校验合格', count: summary.value['校验合格'] ?? null },
  { label: '已报废', value: '已报废', count: summary.value['已报废'] ?? null },
])

function selectStatus(value: string) {
  activeStatus.value = value
  void reload()
}

function rowClass(row: Row): Record<string, boolean> {
  return {
    'row-overdue': row['安全阀状态'] === '校验超期',
    'row-due': row['安全阀状态'] === '即将到期',
    'row-scrapped': row['安全阀状态'] === '已报废',
    'row-expired': Boolean(row['超年限']),
  }
}

function closeModals() {
  registerTarget.value = null
  editTarget.value = null
  creating.value = false
  recordsTarget.value = null
}

function flashOk(message: string) {
  okMessage.value = message
  window.setTimeout(() => {
    okMessage.value = ''
  }, 4000)
}

function openCreate() {
  createForm.value = { 使用年限: '15' }
  creating.value = true
}

function openRegister(row: Row) {
  registerTarget.value = row
  registerForm.value = { '校验日期': '', '整定压力': '', '校验机构': '' }
}

function openEdit(row: Row) {
  editTarget.value = row
  editForm.value = {
    '整定压力': String(row['整定压力'] ?? ''),
    '投用日期': String(row['投用日期'] ?? ''),
    '使用年限': String(row['使用年限'] ?? '15'),
  }
}

async function openRecords(row: Row) {
  recordsTarget.value = row
  valveRecords.value = []
  try {
    const response = await request(`${ENDPOINT}/records?valve_id=${row.id}`)
    if (!response.ok) {
      throw new Error('校验记录读取失败')
    }
    const payload = await response.json()
    valveRecords.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '校验记录读取失败'
    recordsTarget.value = null
  }
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function submitAction(action: string, row: Row, extra: Record<string, unknown> = {}) {
  const response = await request(`${ENDPOINT}/${row.id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ values: { action, ...extra } }),
  })
  const payload = await response.json().catch(() => null)
  if (!response.ok || !payload?.ok) {
    throw new Error(payload?.message || '安全阀动作未生效，请稍后重试')
  }
  return payload.message as string
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const message = await submitAction(action, row)
    flashOk(message)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '安全阀操作失败'
  }
}

async function submitRegister() {
  if (!registerTarget.value || !registerForm.value['校验日期']) {
    errorMessage.value = '请选择校验日期'
    return
  }
  saving.value = true
  errorMessage.value = ''
  try {
    const values: Record<string, string> = { action: '登记合格' }
    for (const [key, val] of Object.entries(registerForm.value)) {
      if (val) values[key] = val
    }
    const response = await request(`${ENDPOINT}/${registerTarget.value.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message || '合格登记失败')
    }
    flashOk(payload.message)
    closeModals()
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '合格登记失败'
  } finally {
    saving.value = false
  }
}

async function submitEdit() {
  if (!editTarget.value) return
  saving.value = true
  errorMessage.value = ''
  try {
    const values: Record<string, string> = {}
    for (const [key, val] of Object.entries(editForm.value)) {
      if (val !== '') values[key] = val
    }
    const response = await request(`${ENDPOINT}/${editTarget.value.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ values }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message || '台账更新失败')
    }
    flashOk(payload.message)
    closeModals()
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '台账更新失败'
  } finally {
    saving.value = false
  }
}

async function submitCreate() {
  saving.value = true
  errorMessage.value = ''
  try {
    const values: Record<string, string> = {}
    for (const [key, val] of Object.entries(createForm.value)) {
      if (val) values[key] = val
    }
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message || '安全阀登记失败')
    }
    flashOk(payload.message)
    closeModals()
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '安全阀登记失败'
  } finally {
    saving.value = false
  }
}

async function recalcAll() {
  recalculating.value = true
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/recalc`, { method: 'POST' })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message || '重算失败')
    }
    flashOk(payload.message)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '按新规则重算失败'
  } finally {
    recalculating.value = false
  }
}

async function reload() {
  errorMessage.value = ''
  const params = new URLSearchParams()
  if (keyword.value) params.set('keyword', keyword.value)
  if (activeStatus.value) params.set('status', activeStatus.value)
  try {
    const response = await request(`${ENDPOINT}?${params.toString()}`)
    if (!response.ok) {
      throw new Error('安全阀列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    summary.value = payload.summary ?? {}
    const rules = payload.rules
    if (rules?.pressureTiers) {
      ruleText.value = `判定规则：${rules.pressureTiers
        .map((t: { minPressureMp: number; name: string; cycleMonths: number }) =>
          `≥${t.minPressureMp}MPa ${t.name}每${t.cycleMonths}个月`)
        .join('；')}；使用年限默认 ${rules.defaultServiceYears} 年；提前 ${rules.warnDays} 天预警`
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '安全阀列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.rule-hint {
  margin: 0 0 12px;
  padding: 8px 12px;
  font-size: 12px;
  color: #475467;
  background: #f6f8fa;
  border: 1px solid var(--border);
  border-radius: 6px;
}

.status-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.status-tabs em {
  font-style: normal;
  color: #667085;
  margin-left: 2px;
}

.status-tabs .btn.active {
  background: var(--brand);
  border-color: var(--brand);
  color: #fff;
}

.status-tabs .btn.active em {
  color: #dbe6ff;
}

.valve-table .reason-cell {
  max-width: 320px;
  color: #475467;
  font-size: 12px;
}

.row-overdue {
  background: #fef3f2;
}

.row-due {
  background: #fffaeb;
}

.row-expired td {
  box-shadow: inset 0 -2px 0 #d92d20;
}

.row-scrapped {
  color: #98a2b3;
  background: #f9fafb;
}

.status-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
  white-space: nowrap;
  background: #e4e7ec;
  color: #475467;
}

.status-tag[data-status='校验合格'] {
  background: #dcfae6;
  color: #067647;
}

.status-tag[data-status='即将到期'] {
  background: #fef0c7;
  color: #b54708;
}

.status-tag[data-status='校验超期'] {
  background: #fee4e2;
  color: #b42318;
}

.status-tag[data-status='待校验'] {
  background: #e0eaff;
  color: #1849a9;
}

.status-tag[data-status='已报废'] {
  background: #f2f4f7;
  color: #98a2b3;
}

.tier-tag {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 12px;
  background: #eef4ff;
  color: #1d4ed8;
}

.muted {
  color: #98a2b3;
}

.danger-link {
  color: #b42318;
}

.stat-danger {
  border-color: #f04438;
}

.stat-danger .stat-value {
  color: #b42318;
}

.stat-warn {
  border-color: #f79009;
}

.stat-warn .stat-value {
  color: #b54708;
}

.stat-muted {
  opacity: 0.7;
}

.ok-text {
  color: #067647;
}

.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(16, 24, 40, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
}

.modal {
  width: 420px;
  max-width: calc(100vw - 32px);
  background: #fff;
  border-radius: 10px;
  padding: 20px 24px;
  box-shadow: 0 20px 48px rgba(16, 24, 40, 0.2);
}

.modal.wide {
  width: 760px;
}

.modal h3 {
  margin: 0 0 8px;
}

.modal label {
  display: block;
  margin: 10px 0;
  font-size: 13px;
  color: #344054;
}

.modal label span {
  display: block;
  margin-bottom: 4px;
}

.modal input {
  width: 100%;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}
</style>
