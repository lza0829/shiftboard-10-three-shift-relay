<script setup>
import { onMounted, ref } from 'vue'
import { getJson, postJson } from '../api'

const plans = ref([])
const shifts = ref([])
const order = ref([1, 2, 3])
const name = ref('')
const msg = ref('')
const ok = ref(false)
const err = ref('')

async function refresh() {
  const [p, s] = await Promise.all([getJson('/api/relay-plans'), getJson('/api/shifts')])
  plans.value = p
  shifts.value = s
}

onMounted(async () => {
  try {
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  }
})

function addShift() {
  const last = order.value[order.value.length - 1]
  const next = shifts.value.find((s) => s.id !== last)
  order.value.push(next?.id ?? 1)
}

function removeShift(i) {
  if (order.value.length <= 3) return
  order.value.splice(i, 1)
}

async function create() {
  msg.value = ''
  ok.value = false
  try {
    const plan = await postJson('/api/relay-plans', {
      name: name.value || null,
      shift_ids: order.value.map(Number),
    })
    ok.value = true
    msg.value = `已登记接力计划 #${plan.id}：${plan.班次序列.join(' → ')}，共 ${plan.段数} 段`
    name.value = ''
    await refresh()
  } catch (e) {
    msg.value = String(e.message || e)
  }
}
</script>

<template>
  <div>
    <h1>接力交班</h1>
    <p class="lead">
      登记一条有序班次接力计划，按段逐次交班：上一段完成才允许发起下一段；任一段失败只关闭本段，
      不回滚此前已完成的过户。
    </p>
    <p v-if="err" class="msg">{{ err }}</p>

    <div class="panel">
      <h2>登记接力计划</h2>
      <div class="row">
        <label>计划名称（可空）
          <input v-model="name" placeholder="自动按班次命名" />
        </label>
        <label v-for="(sid, i) in order" :key="i">
          {{ i === 0 ? '班次 1（交班方）' : `班次 ${i + 1}` }}
          <select v-model.number="order[i]">
            <option v-for="s in shifts" :key="s.id + '-' + i" :value="s.id">{{ s.name }}</option>
          </select>
        </label>
        <button class="btn ghost" type="button" @click="addShift">+ 加一班</button>
        <button
          class="btn ghost"
          type="button"
          :disabled="order.length <= 3"
          @click="removeShift(order.length - 1)"
        >
          − 减一班
        </button>
        <button class="btn" @click="create">登记计划</button>
      </div>
      <p class="lead" style="margin:0">
        至少三班；相邻班次不能相同。共 {{ order.length - 1 }} 段。
      </p>
      <p class="msg" :class="{ ok }">{{ msg }}</p>
    </div>

    <div class="panel">
      <h2>接力计划</h2>
      <table>
        <thead>
          <tr>
            <th>计划</th><th>班次序列</th><th>已完成 / 总段</th><th>失败段</th>
            <th>合计清单</th><th>总对账</th><th>状态</th><th>失败原因</th><th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in plans" :key="p.id">
            <td>#{{ p.id }} {{ p.name }}</td>
            <td>{{ p.班次序列.join(' → ') }}</td>
            <td>{{ p.已完成段数 }} / {{ p.段数 }}</td>
            <td>{{ p.失败段数 }}</td>
            <td>{{ p.合计清单数 }}</td>
            <td>
              <span class="tag" :class="{ on: p.总对账 === '全部一致' }">{{ p.总对账 }}</span>
            </td>
            <td>{{ p.status }}</td>
            <td class="reason">{{ p.reason || '—' }}</td>
            <td><router-link class="tag on" :to="`/relay/${p.id}`">查看进度</router-link></td>
          </tr>
          <tr v-if="!plans.length">
            <td colspan="9">尚无接力计划</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
