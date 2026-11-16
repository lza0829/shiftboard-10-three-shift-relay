<script setup>
import { onMounted, ref } from 'vue'
import { getJson } from '../api'

const rows = ref([])
const err = ref('')

function detailNote(r) {
  const d = r.detail || {}
  if (!d.失败来源) return '—'
  const suffix = d.已回滚本段 ? '（本段已回滚）' : d.本段未过户 ? '（本段未过户）' : ''
  return `${d.失败来源}${suffix}`
}

onMounted(async () => {
  try {
    rows.value = await getJson('/api/reconcile')
  } catch (e) {
    err.value = String(e.message || e)
  }
})
</script>

<template>
  <div>
    <h1>交班对账</h1>
    <p class="lead">
      交班前后：交班方在途应变 0，接班方在途应接住清单批次数；数字与交班单同源。
      接力时每段各有一份对账报告，段失败的原因落在这里且刷新后仍在。
    </p>
    <p v-if="err" class="msg">{{ err }}</p>
    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>报告</th><th>交班单</th><th>接力计划</th><th>段</th><th>交班前清单</th>
            <th>交后交班方</th><th>交后接班方</th><th>一致</th><th>说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id">
            <td>#{{ r.id }}</td>
            <td>#{{ r.handover_id }}</td>
            <td>{{ r.relay_plan_id ? `#${r.relay_plan_id}` : '单段' }}</td>
            <td>{{ r.relay_seq ?? '—' }}</td>
            <td>{{ r.before_count }}</td>
            <td>{{ r.after_from_count }}</td>
            <td>{{ r.after_to_count }}</td>
            <td>
              <span class="tag" :class="{ on: r.一致 }">{{ r.一致 ? '一致' : '不一致' }}</span>
            </td>
            <td class="reason">
              {{ detailNote(r) }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
