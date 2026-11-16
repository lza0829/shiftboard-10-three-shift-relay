<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { getJson, postJson } from '../api'

const route = useRoute()
const plan = ref(null)
const activeDetail = ref(null)
const msg = ref('')
const ok = ref(false)
const failReason = ref('')
const busy = ref(false)

const planId = computed(() => Number(route.params.id))
const activeSeg = computed(() => (plan.value?.段 || []).find((s) => s.status === '进行中') || null)
const canAbort = computed(() =>
  ['待启动', '进行中', '段失败'].includes(plan.value?.status) && !activeSeg.value
)

async function refresh() {
  plan.value = await getJson(`/api/relay-plans/${planId.value}`)
  const active = (plan.value.段 || []).find((s) => s.status === '进行中')
  activeDetail.value = active ? await getJson(`/api/handovers/${active.交班单}`) : null
}

onMounted(async () => {
  try {
    await refresh()
  } catch (e) {
    msg.value = String(e.message || e)
  }
})

function blockReason() {
  const p = plan.value
  if (!p || p.可发起下一段) return ''
  if (p.未关闭失败段?.length) return `段${p.未关闭失败段.join('、')}失败未关闭，禁止发起下一段`
  if (p.status === '已完成') return '全部段已完成'
  if (p.status === '已中止') return '计划已中止，不能续段'
  if (p.status === '段失败') return '存在失败段，关闭后请另登记计划继续'
  return '上一段未完成或已有其他进行中交班'
}

async function run(fn, successMsg) {
  busy.value = true
  msg.value = ''
  ok.value = false
  try {
    const text = await fn()
    ok.value = true
    msg.value = text || successMsg
    await refresh()
  } catch (e) {
    msg.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

function startNext() {
  run(async () => {
    const out = await postJson(`/api/relay-plans/${planId.value}/segments/start`)
    const seg = out.刚发起段
    return `已发起段${seg.relay_seq}（交班单 #${seg.id}），清单 ${seg.清单数} 批`
  })
}

function completeSeg(seg) {
  run(async () => {
    const h = await postJson(`/api/handovers/${seg.交班单}/complete`)
    if (h.status === '已完成') {
      return `段${seg.段}已完成，批次整批过户到「${seg.to_shift_name}」`
    }
    throw new Error(`段${seg.段}失败：${h.reason || '对账不一致'}（本段已回滚，前段不变）`)
  })
}

function failSeg(seg) {
  if (!failReason.value.trim()) {
    msg.value = '请填写本段失败原因'
    return
  }
  run(async () => {
    await postJson(`/api/handovers/${seg.交班单}/fail`, { reason: failReason.value.trim() })
    failReason.value = ''
    return `段${seg.段}已标记失败；前段已完成过户保持不变`
  })
}

function closeSeg(seg) {
  run(async () => {
    await postJson(`/api/handovers/${seg.交班单}/close`)
    return `段${seg.段}失败已关闭`
  })
}

function abortPlan() {
  run(async () => {
    await postJson(`/api/relay-plans/${planId.value}/abort`)
    return '接力计划已中止'
  })
}
</script>

<template>
  <div>
    <h1>接力进度</h1>
    <router-link class="tag" to="/relay">← 返回接力计划</router-link>
    <p v-if="msg" class="msg" :class="{ ok }">{{ msg }}</p>

    <template v-if="plan">
      <div class="stats">
        <div class="stat"><div class="k">计划状态</div><div class="v">{{ plan.status }}</div></div>
        <div class="stat"><div class="k">已完成段 / 总段</div><div class="v">{{ plan.已完成段数 }} / {{ plan.段数 }}</div></div>
        <div class="stat"><div class="k">总对账</div><div class="v">{{ plan.总对账 }}</div></div>
      </div>

      <div class="panel">
        <h2>计划 #{{ plan.id }} · {{ plan.name }}</h2>
        <p class="lead" style="margin-bottom:12px">
          班次序列：{{ plan.班次序列.join(' → ') }} · 合计清单 {{ plan.合计清单数 }} 批 ·
          当前段 {{ plan.current_seq || '未开始' }}
        </p>
        <p v-if="plan.reason" class="msg">失败 / 中止原因：{{ plan.reason }}</p>
        <div class="row">
          <button class="btn" :disabled="!plan.可发起下一段 || busy" @click="startNext">
            发起段{{ plan.下一段序号 ?? '—' }}
          </button>
          <button class="btn ghost" :disabled="busy || !canAbort" @click="abortPlan">中止计划</button>
          <span class="tag" v-if="blockReason()">{{ blockReason() }}</span>
        </div>
      </div>

      <div class="panel">
        <h2>分段明细</h2>
        <table>
          <thead>
            <tr>
              <th>段</th><th>交班单</th><th>交班 → 接班</th><th>状态</th>
              <th>清单数</th><th>对账结果</th><th>失败原因</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="seg in plan.段" :key="seg.段">
              <td>{{ seg.段 }}</td>
              <td>#{{ seg.交班单 }}</td>
              <td>{{ seg.from_shift_name }} → {{ seg.to_shift_name }}</td>
              <td>
                <span class="tag" :class="{ on: seg.status === '已完成' }">{{ seg.status }}</span>
              </td>
              <td>{{ seg.清单数 }}</td>
              <td>
                <span class="tag" :class="{ on: seg.对账结果 === '一致' }">{{ seg.对账结果 }}</span>
              </td>
              <td class="reason">{{ seg.失败原因 || '—' }}</td>
              <td>
                <template v-if="seg.可完成">
                  <button class="btn" :disabled="busy" @click="completeSeg(seg)">完成本段</button>
                  <input v-model="failReason" placeholder="失败原因" style="margin-left:8px" />
                  <button class="btn ghost" :disabled="busy" @click="failSeg(seg)">判失败</button>
                </template>
                <button v-else-if="seg.可关闭" class="btn ghost" :disabled="busy" @click="closeSeg(seg)">
                  关闭失败段
                </button>
                <span v-else>—</span>
              </td>
            </tr>
            <tr v-if="!plan.段.length">
              <td colspan="8">尚未发起任何段</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="panel" v-if="activeSeg && activeDetail">
        <h2>当前段 {{ activeSeg.段 }} 清单（交班单 #{{ activeDetail.id }}）</h2>
        <p class="lead" style="margin-bottom:12px">
          {{ activeSeg.from_shift_name }} → {{ activeSeg.to_shift_name }} ·
          {{ activeSeg.清单数 }} 批 · {{ activeSeg.status }}
        </p>
        <table>
          <thead><tr><th>业务键</th><th>标题</th><th>归属班次</th><th>状态</th></tr></thead>
          <tbody>
            <tr v-for="b in activeDetail.items || []" :key="b.id">
              <td>{{ b.biz_key }}</td>
              <td>{{ b.title }}</td>
              <td>{{ b.owner_shift_name || activeSeg.from_shift_name }}</td>
              <td>{{ b.status }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>
