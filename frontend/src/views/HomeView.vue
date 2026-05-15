<template>
  <div class="page">
    <div class="page-header">
      <h1>候选人列表</h1>
      <RouterLink to="/prepare" class="btn btn-primary">+ 新建面试</RouterLink>
    </div>

    <div class="search-bar">
      <input
        v-model="keyword"
        placeholder="按姓名搜索..."
        class="input"
        @input="debouncedSearch"
      />
    </div>

    <div v-if="loading" class="center-msg">加载中...</div>
    <div v-else-if="error" class="center-msg error">{{ error }}</div>
    <div v-else-if="candidates.length === 0" class="center-msg muted">暂无候选人记录</div>
    <table v-else class="table">
      <thead>
        <tr>
          <th>姓名</th>
          <th>面试次数</th>
          <th>最近面试</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="c in candidates" :key="c.id">
          <td>{{ c.name || '（未命名）' }}</td>
          <td>{{ c.interview_count ?? '-' }}</td>
          <td>{{ formatDate(c.last_interview_date) }}</td>
          <td>
            <RouterLink :to="`/prepare?candidate_id=${c.id}`" class="btn btn-sm">
              再次面试
            </RouterLink>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="!loading && total > 0" class="footer-info">共 {{ total }} 条记录</div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { candidateApi } from '../api/index.js'

const keyword = ref('')
const candidates = ref([])
const total = ref(0)
const loading = ref(false)
const error = ref('')
let searchTimer = null

async function search() {
  loading.value = true
  error.value = ''
  try {
    const resp = await candidateApi.list({ keyword: keyword.value, limit: 50 })
    candidates.value = resp.data.candidates
    total.value = resp.data.total
  } catch {
    error.value = '加载失败，请检查服务是否运行'
  } finally {
    loading.value = false
  }
}

function debouncedSearch() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(search, 300)
}

function formatDate(d) {
  if (!d) return '-'
  return new Date(d).toLocaleDateString('zh-CN')
}

onMounted(search)
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

.search-bar { margin-bottom: 1.5rem; }

.input {
  width: 100%;
  max-width: 400px;
  padding: 0.5rem 0.75rem;
  background: #1f2937;
  border: 1px solid #374151;
  border-radius: 6px;
  color: #f9fafb;
  font-size: 0.9rem;
  outline: none;
}
.input:focus { border-color: #3b82f6; }

.table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }

.table th, .table td {
  padding: 0.75rem 1rem;
  text-align: left;
  border-bottom: 1px solid #374151;
}

.table th { color: #9ca3af; font-weight: 500; }
.table tr:hover td { background: #1f2937; }

.btn {
  display: inline-flex;
  align-items: center;
  padding: 0.5rem 1rem;
  border-radius: 6px;
  font-size: 0.85rem;
  text-decoration: none;
  cursor: pointer;
  border: none;
  transition: opacity 0.15s;
}
.btn:hover { opacity: 0.85; }
.btn-primary { background: #3b82f6; color: #fff; }
.btn-sm { background: #374151; color: #f9fafb; padding: 0.3rem 0.75rem; }

.center-msg { padding: 3rem; text-align: center; }
.error { color: #f87171; }
.muted { color: #6b7280; }
.footer-info { margin-top: 1rem; color: #6b7280; font-size: 0.85rem; }
</style>