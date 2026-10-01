<template>
  <section class="page" data-module="safetyvalve">
    <header class="page-head">
      <div>
        <h2>安全阀校验管理</h2>
        <p class="page-desc">
          校验周期按整定压力档位判定：低压（&lt;1.6MPa）12 个月、中压（1.6–10.0MPa）6 个月、高压（≥10.0MPa）3 个月。
          下次校验日由上次合格校验日期与档位自动算出，距到期不足 7 天或已超期的阀门自动前置并说明缘由。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记安全阀</button>
        <button class="btn" type="button" @click="openRules">调整排期规则</button>
        <button class="btn" type="button" @click="exportRows">导出安全阀校验清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value" :class="item.tone">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>安全阀编号</span>
        <input v-model="keyword" placeholder="按安全阀编号检索" />
      </label>
      <label class="filter-item">
        <span>安全阀状态</span>
        <select v-model="status">
          <option value="">全部状态</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td>{{ row['安全阀编号'] ?? '—' }}</td>
          <td>{{ row['所属设备'] ?? '—' }}</td>
          <td>{{ row['公称通径'] ?? '—' }}</td>
          <td>{{ row['整定压力'] ?? '—' }}</td>
          <td>{{ row['压力档位'] || '—' }}</td>
          <td>{{ row['校验周期'] || '—' }}</td>
          <td>{{ row['校验日期'] || '—' }}</td>
          <td>{{ row['下次校验日'] || '—' }}</td>
          <td :class="daysTone(row)">{{ formatDays(row) }}</td>
          <td>{{ row['校验结论'] || '—' }}</td>
          <td><span class="status-tag" :class="statusTone(row)">{{ row['安全阀状态'] }}</span></td>
          <td class="reason-cell">{{ row['排期说明'] || '—' }}</td>
          <td :class="row['年限提示'] ? 'life-warn' : ''">{{ row['年限提示'] || '—' }}</td>
          <td class="row-actions">
            <template v-if="row['安全阀状态'] !== '已报废'">
              <button class="link" type="button" @click="runAction('安排校验', row)">安排校验</button>
              <button class="link" type="button" @click="registerPass(row)">登记合格</button>
              <button class="link" type="button" @click="editPressure(row)">修改整定压力</button>
              <button class="link danger" type="button" @click="runAction('申请报废', row)">申请报废</button>
            </template>
            <span class="muted-text">已报废，不参与排期</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无符合条件的安全阀校验记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条安全阀校验记录</span>
      <span v-if="okMessage" class="ok-text">{{ okMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type StatItem = { label: string; value: number; tone?: string }

const ENDPOINT = '/api/safetyvalve'
const columns = [
  '安全阀编号', '所属设备', '公称通径', '整定压力', '压力档位', '校验周期',
  '校验日期', '下次校验日', '剩余天数', '校验结论', '安全阀状态', '排期说明', '年限提示',
]
const statuses = ['校验合格', '即将到期', '待校验', '已报废']

const rows = ref<Row[]>([])
const total = ref(0)
const keyword = ref('')
const status = ref('')
const errorMessage = ref('')
const okMessage = ref('')
const stats = ref<StatItem[]>([
  { label: '合格安全阀', value: 0 },
  { label: '即将到期', value: 0, tone: 'warn-text' },
  { label: '待校验（超期）', value: 0, tone: 'danger-text' },
  { label: '报废阀', value: 0 },
  { label: '超年限提示', value: 0, tone: 'warn-text' },
])

function notify(message: string, ok = false) {
  errorMessage.value = ok ? '' : message
  okMessage.value = ok ? message : ''
}

function resetFilters() {
  keyword.value = ''
  status.value = ''
  void reload()
}

function exportRows() {
  const query = new URLSearchParams()
  if (keyword.value) query.set('keyword', keyword.value)
  if (status.value) query.set('status', status.value)
  window.open(`${ENDPOINT}/export?${query.toString()}`, '_blank')
}

function formatDays(row: Row) {
  const days = row['剩余天数']
  if (days === '' || days === null || days === undefined) return '—'
  const n = Number(days)
  if (n < 0) return `超期 ${-n} 天`
  return `${n} 天`
}

function daysTone(row: Row) {
  const n = Number(row['剩余天数'])
  if (row['剩余天数'] === '' || row['剩余天数'] === null || Number.isNaN(n)) return ''
  if (n < 0) return 'danger-text'
  if (n < 7) return 'warn-text'
  return ''
}

function statusTone(row: Row) {
  return {
    待校验: 'tag-danger',
    即将到期: 'tag-warn',
    校验合格: 'tag-ok',
    已报废: 'tag-off',
  }[String(row['安全阀状态'] ?? '')] ?? ''
}

function openCreate() {
  const pressure = window.prompt('请输入整定压力（MPa），如 2.5')
  if (pressure === null) return
  const serial = window.prompt('请输入安全阀编号，如 SAFE-0008')
  if (serial === null) return
  const device = window.prompt('请输入所属设备') || ''
  const sizeText = window.prompt('请输入公称通径，如 DN50') || ''
  const madeOn = window.prompt('出厂日期（YYYY-MM-DD，可留空）') || ''
  const checkDate = window.prompt('若已有合格校验，请输入上次校验日期（YYYY-MM-DD，可留空）') || ''
  void saveEntry({
    values: {
      整定压力: pressure,
      安全阀编号: serial,
      所属设备: device,
      公称通径: sizeText,
      出厂日期: madeOn,
      校验日期: checkDate,
    },
  })
}

async function saveEntry(payload: Record<string, unknown>) {
  try {
    const response = await request(ENDPOINT, { method: 'POST', body: JSON.stringify(payload) })
    const data = await response.json().catch(() => ({}))
    if (!response.ok || data.ok === false) {
      throw new Error(data.message || data.detail || '安全阀登记未生效')
    }
    notify(data.message || '安全阀已登记', true)
    await reload()
  } catch (error) {
    notify(error instanceof Error ? error.message : '安全阀登记失败')
  }
}

async function loadStats() {
  try {
    const response = await request(`${ENDPOINT}/stats`)
    if (!response.ok) return
    const data = await response.json()
    stats.value = [
      { label: '合格安全阀', value: Number(data['校验合格'] ?? 0) },
      { label: '即将到期', value: Number(data['即将到期'] ?? 0), tone: 'warn-text' },
      { label: '待校验（超期）', value: Number(data['待校验'] ?? 0), tone: 'danger-text' },
      { label: '报废阀', value: Number(data['已报废'] ?? 0) },
      { label: '超年限提示', value: Number(data['超年限'] ?? 0), tone: 'warn-text' },
    ]
  } catch {
    // 统计加载失败不阻塞列表
  }
}

async function openRules() {
  try {
    const response = await request(`${ENDPOINT}/rules`)
    const rules = await response.json()
    const warnDays = window.prompt('距下次校验日多少天内视为「即将到期」？', String(rules.warn_days))
    if (warnDays === null) return
    const lifeYears = window.prompt('默认使用年限（年）？', String(rules.default_life_years))
    if (lifeYears === null) return
    const text = window.prompt(
      '整定压力档位（每行一条：档位名称,下限MPa(可空),周期月数）',
      rules.tiers.map((t: { name: string; min_mpa: number | null; interval_months: number }) =>
        `${t.name},${t.min_mpa ?? ''},${t.interval_months}`).join('\n'),
    )
    if (text === null) return
    const tiers = text.split('\n').map(line => line.trim()).filter(Boolean).map(line => {
      const [name, minMpa, months] = line.split(',').map(part => part.trim())
      return { name, min_mpa: minMpa === '' ? null : Number(minMpa), interval_months: Number(months) }
    })
    const save = await request(`${ENDPOINT}/rules`, {
      method: 'PUT',
      body: JSON.stringify({ values: { warn_days: Number(warnDays), default_life_years: Number(lifeYears), tiers } }),
    })
    const data = await save.json().catch(() => ({}))
    if (!save.ok || data.ok === false) throw new Error(data.message || '规则保存失败')
    notify(data.message || '判定规则已更新', true)
    await reload()
  } catch (error) {
    notify(error instanceof Error ? error.message : '排期规则读取失败')
  }
}

async function editPressure(row: Row) {
  const input = window.prompt(`修改安全阀 ${row['安全阀编号']} 的整定压力（MPa），保存后自动重算下次校验日`, String(row['整定压力'] ?? ''))
  if (input === null) return
  try {
    const response = await request(`${ENDPOINT}/${row.id}`, {
      method: 'PUT',
      body: JSON.stringify({ values: { 整定压力: input } }),
    })
    const data = await response.json().catch(() => ({}))
    if (!response.ok || data.ok === false) throw new Error(data.message || '整定压力修改失败')
    notify(data.message || '台账已更新', true)
    await reload()
  } catch (error) {
    notify(error instanceof Error ? error.message : '整定压力修改失败')
  }
}

async function registerPass(row: Row) {
  const answer = window.confirm(`为安全阀 ${row['安全阀编号']} 登记本次合格校验。\n校验日期默认今天，整定压力默认沿用台账值，确定后可修改。`)
  if (!answer) return
  const checkDate = window.prompt('校验日期（YYYY-MM-DD）', new Date().toISOString().slice(0, 10))
  if (checkDate === null) return
  const pressure = window.prompt('本次校验时的整定压力（MPa）', String(row['整定压力'] ?? ''))
  if (pressure === null) return
  await submitAction('登记合格', row, { 校验日期: checkDate, 整定压力: pressure })
}

async function runAction(action: string, row: Row, extra: Record<string, string> = {}) {
  let confirmed = true
  if (action === '申请报废') {
    confirmed = window.confirm(`确认将安全阀 ${row['安全阀编号']} 报废？报废后不再参与校验排期。`)
  }
  if (!confirmed) return
  await submitAction(action, row, extra)
}

async function submitAction(action: string, row: Row, extra: Record<string, string>) {
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action, ...extra } }),
    })
    const data = await response.json().catch(() => ({}))
    if (!response.ok || data.ok === false) {
      throw new Error(data.message || '安全阀校验动作未生效，请稍后重试')
    }
    notify(data.message || `安全阀已${action}`, true)
    await reload()
  } catch (error) {
    notify(error instanceof Error ? error.message : '安全阀校验操作失败')
  }
}

async function reload() {
  notify('', true)
  okMessage.value = ''
  const query = new URLSearchParams()
  if (keyword.value) query.set('keyword', keyword.value)
  if (status.value) query.set('status', status.value)
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('安全阀列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    await loadStats()
  } catch (error) {
    notify(error instanceof Error ? error.message : '安全阀校验列表读取失败')
  }
}

onMounted(reload)
</script>

<style scoped>
.reason-cell { min-width: 220px; color: #555; }
.status-tag { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; }
.tag-ok { background: #e6f7e9; color: #1f7a37; }
.tag-warn { background: #fff5dc; color: #9a6500; }
.tag-danger { background: #fde8e8; color: #b42318; }
.tag-off { background: #eee; color: #666; }
.warn-text { color: #9a6500; font-weight: 600; }
.danger-text { color: #b42318; font-weight: 600; }
.life-warn { color: #b45309; }
.muted-text { color: #999; font-size: 12px; }
.link.danger { color: #b42318; }
.ok-text { color: #1f7a37; }
</style>
