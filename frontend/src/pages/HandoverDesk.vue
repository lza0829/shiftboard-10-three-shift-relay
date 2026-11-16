<script setup>
import { onMounted, ref } from 'vue'
import { getJson, postJson } from '../api'

const shifts = ref([])
const fromId = ref(1)
const toId = ref(2)
const current = ref(null)
const msg = ref('')
const ok = ref(false)
const failReason = ref('')

async function refresh() {
  shifts.value = await getJson('/api/shifts')
  const logs = await getJson('/api/handovers')
  const running = logs.find((h) => h.status === '进行中')
  current.value = running ? await getJson(`/api/handovers/${running.id}`) : null
}

onMounted(async () => {
  try {
    await refresh()
  } catch (e) {
    msg.value = String(e.message || e)
  }
})

async function start() {
  msg.value = ''
  ok.value = false
  try {
    current.value = await postJson('/api/handovers/start', {
      from_shift_id: Number(fromId.value),
      to_shift_id: Number(toId.value),
    })
    ok.value = true
    msg.value = `已开交班单 #${current.value.id}，清单 ${current.value.batch_count} 批`
    await refresh()
  } catch (e) {
    msg.value = String(e.message || e)
  }
}

async function complete() {
  if (!current.value) return
  msg.value = ''
  ok.value = false
  try {
    current.value = await postJson(`/api/handovers/${current.value.id}/complete`)
    ok.value = current.value.status === '已完成'
    msg.value = current.value.status === '已完成'
      ? `交班单 #${current.value.id} 已完成，批次已过户`
      : `交班单 #${current.value.id} 失败：${current.value.reason || '归属不一致'}（本段已回滚）`
    await refresh()
  } catch (e) {
    msg.value = String(e.message || e)
  }
}

async function fail() {
  if (!current.value || !failReason.value.trim()) {
    msg.value = '请填写失败原因'
    return
  }
  msg.value = ''
  ok.value = false
  try {
    current.value = await postJson(`/api/handovers/${current.value.id}/fail`, {
      reason: failReason.value.trim(),
    })
    ok.value = true
    msg.value = `交班单 #${current.value.id} 已判失败，双方已解冻`
    failReason.value = ''
    await refresh()
  } catch (e) {
    msg.value = String(e.message || e)
  }
}
</script>

<template>
  <div>
    <h1>交班办理</h1>
    <p class="lead">
      同一时刻只允许一单进行中的交班（接力段也计入全局互斥）；清单内在途批次必须整批过户，
      禁止只改单据不改归属。段失败只关闭本段并解冻双方。
    </p>

    <div class="panel">
      <div class="row">
        <label>交班方
          <select v-model.number="fromId">
            <option v-for="s in shifts" :key="s.id" :value="s.id">{{ s.name }} · {{ s.keeper }}</option>
          </select>
        </label>
        <label>接班方
          <select v-model.number="toId">
            <option v-for="s in shifts" :key="'t'+s.id" :value="s.id">{{ s.name }} · {{ s.keeper }}</option>
          </select>
        </label>
        <button class="btn" :disabled="!!current" @click="start">发起单段交班</button>
        <button class="btn ghost" :disabled="!current" @click="complete">完成交班</button>
        <input v-model="failReason" placeholder="人工判失败原因" />
        <button class="btn ghost" :disabled="!current" @click="fail">判失败</button>
      </div>
      <p class="msg" :class="{ ok }">{{ msg }}</p>
      <p class="lead" style="margin:0">多段接力请到「接力交班」登记计划，按段推进。</p>
    </div>

    <div class="panel" v-if="current">
      <h2>
        当前交班单 #{{ current.id }} · {{ current.status }}
        <span v-if="current.relay_plan_id">
          · 接力计划 #{{ current.relay_plan_id }} 段 {{ current.relay_seq }}
        </span>
      </h2>
      <p class="lead" style="margin-bottom:12px">
        {{ current.from_shift_name }} → {{ current.to_shift_name }} · 清单 {{ current.batch_count }} 批
      </p>
      <table>
        <thead><tr><th>业务键</th><th>标题</th><th>归属班次</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="b in current.items || []" :key="b.id">
            <td>{{ b.biz_key }}</td>
            <td>{{ b.title }}</td>
            <td>{{ current.from_shift_name }}</td>
            <td>{{ b.status }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
