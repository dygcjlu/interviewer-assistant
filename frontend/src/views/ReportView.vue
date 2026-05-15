<template>
  <div class="page">
    <div class="page-header">
      <h1>评价报告</h1>
      <RouterLink to="/" class="btn">返回首页</RouterLink>
    </div>

    <div v-if="loading" class="center-msg">生成报告中，请稍候...</div>
    <div v-else-if="error" class="center-msg error">{{ error }}</div>

    <div v-else-if="report" class="report">
      <div class="overview card">
        <div class="score-circle">
          <span class="score-num">{{ report.overall_score?.toFixed(1) }}</span>
          <span class="score-unit">/ 10</span>
        </div>
        <div class="overview-detail">
          <div class="rec-badge" :class="report.recommendation">
            {{ recLabels[report.recommendation] ?? report.recommendation }}
          </div>
          <p class="summary">{{ report.summary }}</p>
        </div>
      </div>

      <section class="card">
        <h2>维度评分</h2>
        <div class="dimension-list">
          <div v-for="d in report.dimensions" :key="d.dimension" class="dimension-item">
            <div class="dim-header">
              <span class="dim-name">{{ d.dimension }}</span>
              <span class="dim-score">{{ d.score?.toFixed(1) }}</span>
            </div>
            <div class="score-bar-track">
              <div class="score-bar-fill" :style="{ width: (d.score / 10 * 100) + '%' }"></div>
            </div>
            <p v-if="d.comment" class="dim-comment">{{ d.comment }}</p>
          </div>
        </div>
      </section>

      <div class="two-col">
        <section class="card">
          <h2>优势</h2>
          <ul>
            <li v-for="(s, i) in report.strengths" :key="i">{{ s }}</li>
          </ul>
        </section>
        <section class="card">
          <h2>待提升</h2>
          <ul class="weakness">
            <li v-for="(w, i) in report.weaknesses" :key="i">{{ w }}</li>
          </ul>
        </section>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { interviewApi } from '../api/index.js'

const loading = ref(true)
const error = ref('')
const report = ref(null)

const recLabels = {
  hire: '推荐录用',
  maybe: '可以考虑',
  no_hire: '不推荐',
}

onMounted(async () => {
  try {
    const resp = await interviewApi.getEval()
    report.value = resp.data.report
  } catch (e) {
    error.value = e.response?.data?.detail?.message ?? '获取报告失败，请确保面试已结束并完成评价'
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.page { padding: 2rem; max-width: 900px; margin: 0 auto; }

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1.5rem;
}

h1 { font-size: 1.4rem; }
h2 { font-size: 1rem; color: #d1d5db; margin-bottom: 1rem; }

.card {
  background: #1f2937;
  border: 1px solid #374151;
  border-radius: 10px;
  padding: 1.25rem;
  margin-bottom: 1rem;
}

.overview { display: flex; align-items: flex-start; gap: 2rem; }

.score-circle {
  flex-shrink: 0;
  width: 100px;
  height: 100px;
  border-radius: 50%;
  background: #1e3a5f;
  border: 3px solid #3b82f6;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
}
.score-num { font-size: 1.8rem; font-weight: 700; color: #93c5fd; line-height: 1; }
.score-unit { font-size: 0.75rem; color: #9ca3af; }

.rec-badge {
  display: inline-block;
  padding: 0.3rem 0.8rem;
  border-radius: 4px;
  font-size: 0.85rem;
  font-weight: 600;
  margin-bottom: 0.75rem;
}
.rec-badge.hire { background: #064e3b; color: #6ee7b7; }
.rec-badge.maybe { background: #78350f; color: #fcd34d; }
.rec-badge.no_hire { background: #7f1d1d; color: #fca5a5; }

.summary { color: #d1d5db; line-height: 1.65; font-size: 0.9rem; }

.dimension-list { display: flex; flex-direction: column; gap: 1rem; }
.dim-header { display: flex; justify-content: space-between; margin-bottom: 0.4rem; font-size: 0.9rem; }
.dim-name { color: #e5e7eb; }
.dim-score { color: #3b82f6; font-weight: 600; }
.score-bar-track { height: 6px; background: #374151; border-radius: 3px; }
.score-bar-fill { height: 100%; background: #3b82f6; border-radius: 3px; transition: width 0.4s; }
.dim-comment { color: #9ca3af; font-size: 0.8rem; margin-top: 0.4rem; }

.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }

ul { padding-left: 1.25rem; color: #d1d5db; font-size: 0.9rem; line-height: 1.85; }
.weakness li { color: #fca5a5; }

.btn {
  padding: 0.5rem 1rem;
  background: #374151;
  color: #d1d5db;
  border: none;
  border-radius: 6px;
  text-decoration: none;
  font-size: 0.85rem;
  cursor: pointer;
}
.btn:hover { opacity: 0.85; }

.center-msg { padding: 3rem; text-align: center; color: #9ca3af; }
.error { color: #f87171; }
</style>