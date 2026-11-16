<script setup>
import { onMounted, ref } from 'vue'
import { getJson } from '../api'

const data = ref(null)
const plans = ref([])
const err = ref('')

onMounted(async () => {
  try {
    data.value = await getJson('/api/dashboard')
    plans.value = await getJson('/api/relay-plans')
  } catch (e) {
    err.value = String(e.message || e)
  }
})
</script>

<template>
  <div>
    <h1>总览</h1>
    <p class="lead">看清谁在值班、有多少在途批次、有没有交班卡在半路；接力计划按段推进，失败只停本段。</p>
    <p v-if="err" class="msg">{{ err }}</p>
    <template v-if="data">
      <div class="stats">
        <div class="stat"><div class="k">班次数</div><div class="v">{{ data.班次数 }}</div></div>
        <div class="stat"><div class="k">在途批次</div><div class="v">{{ data.在途批次数 }}</div></div>
        <div class="stat"><div class="k">进行中交班</div><div class="v">{{ data.进行中交班 }}</div></div>
      </div>
      <div class="stats">
        <div class="stat"><div class="k">接力计划</div><div class="v">{{ data.接力计划数 }}</div></div>
        <div class="stat"><div class="k">进行中接力</div><div class="v">{{ data.进行中接力 }}</div></div>
        <div class="stat"><div class="k">失败待关闭段</div><div class="v">{{ data.失败待关闭段 }}</div></div>
      </div>
      <div class="panel">
        <h2>接力计划进度</h2>
        <table>
          <thead>
            <tr><th>计划</th><th>班次序列</th><th>已完成段</th><th>失败段</th><th>总对账</th><th>状态</th><th></th></tr>
          </thead>
          <tbody>
            <tr v-for="p in plans" :key="p.id">
              <td>#{{ p.id }} {{ p.name }}</td>
              <td>{{ p.班次序列.join(' → ') }}</td>
              <td>{{ p.已完成段数 }} / {{ p.段数 }}</td>
              <td>{{ p.失败段数 }}</td>
              <td><span class="tag" :class="{ on: p.总对账 === '全部一致' }">{{ p.总对账 }}</span></td>
              <td>{{ p.status }}</td>
              <td><router-link class="tag on" :to="`/relay/${p.id}`">进度</router-link></td>
            </tr>
            <tr v-if="!plans.length">
              <td colspan="7">尚无接力计划，可到「接力交班」登记</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="panel">
        <h2>班次状态</h2>
        <table>
          <thead><tr><th>班次</th><th>值班人</th><th>状态</th></tr></thead>
          <tbody>
            <tr v-for="s in data.班次" :key="s.id">
              <td>{{ s.name }}</td>
              <td>{{ s.keeper }}</td>
              <td><span class="tag" :class="{ on: s.status === '值班中' }">{{ s.status }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>
