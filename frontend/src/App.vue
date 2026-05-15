<template>
  <div class="app">
    <nav class="navbar">
      <RouterLink to="/" class="brand">面试助手</RouterLink>
      <div class="nav-links">
        <RouterLink to="/">首页</RouterLink>
        <RouterLink to="/prepare">准备</RouterLink>
        <RouterLink to="/console">控制台</RouterLink>
        <RouterLink to="/report">报告</RouterLink>
      </div>
      <div class="ws-status">
        <span :class="['dot', store.wsConnected ? 'ok' : 'off']"></span>
        <span>{{ stageLabel }}</span>
      </div>
    </nav>
    <RouterView />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted } from 'vue'
import { useInterviewStore } from './stores/interview.js'

const store = useInterviewStore()

const stageLabel = computed(() => {
  const labels = {
    idle: '空闲',
    resume_analysis: '简历分析',
    interviewing: '面试中',
    evaluating: '评价中',
    completed: '已完成',
  }
  return labels[store.stage] ?? store.stage
})

onMounted(() => store.connect())
onUnmounted(() => store.disconnect())
</script>

<style scoped>
.app { min-height: 100vh; display: flex; flex-direction: column; }

.navbar {
  display: flex;
  align-items: center;
  gap: 2rem;
  padding: 0 1.5rem;
  height: 52px;
  background: #1f2937;
  border-bottom: 1px solid #374151;
  flex-shrink: 0;
}

.brand {
  font-weight: 700;
  font-size: 1.1rem;
  color: #3b82f6;
  text-decoration: none;
}

.nav-links { display: flex; gap: 1.5rem; }

.nav-links a {
  color: #9ca3af;
  text-decoration: none;
  font-size: 0.9rem;
  transition: color 0.15s;
}

.nav-links a:hover,
.nav-links a.router-link-active { color: #f9fafb; }

.ws-status {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.8rem;
  color: #9ca3af;
}

.dot { width: 8px; height: 8px; border-radius: 50%; }
.dot.ok { background: #10b981; }
.dot.off { background: #6b7280; }
</style>