<template>
  <div class="page">
    <h1>面试准备</h1>

    <div v-if="!profile && !uploading" class="upload-section">
      <div
        class="drop-zone"
        :class="{ dragging }"
        @dragover.prevent="dragging = true"
        @dragleave="dragging = false"
        @drop.prevent="onDrop"
        @click="fileInputRef.click()"
      >
        <div class="drop-icon">📄</div>
        <div>点击或拖拽上传简历 PDF</div>
        <div class="muted">仅支持 .pdf 格式</div>
      </div>
      <input ref="fileInputRef" type="file" accept=".pdf" style="display:none" @change="onFileChange" />
      <div v-if="uploadError" class="error-msg">{{ uploadError }}</div>
    </div>

    <div v-if="uploading" class="center-msg">解析简历中，请稍候...</div>

    <div v-if="profile" class="content">
      <section class="card">
        <h2>候选人画像</h2>
        <div class="profile-grid">
          <div><span class="label">姓名</span><span>{{ profile.name || '-' }}</span></div>
          <div><span class="label">职位</span><span>{{ profile.target_role || '-' }}</span></div>
          <div><span class="label">工作年限</span><span>{{ profile.years_exp != null ? profile.years_exp + ' 年' : '-' }}</span></div>
          <div><span class="label">学历</span><span>{{ profile.education || '-' }}</span></div>
        </div>
        <div v-if="profile.skills?.length" class="skills">
          <span v-for="s in profile.skills" :key="s" class="tag">{{ s }}</span>
        </div>
      </section>

      <section class="card">
        <div class="section-header">
          <h2>题目清单 ({{ questions.length }})</h2>
          <button class="btn btn-sm" @click="addQuestion">+ 添加题目</button>
        </div>
        <div class="question-list">
          <div v-for="(q, i) in questions" :key="i" class="question-item">
            <div class="q-header">
              <span class="q-dim">{{ q.dimension }}</span>
              <span class="q-diff" :class="q.difficulty">{{ q.difficulty }}</span>
              <button class="icon-btn" @click="removeQuestion(i)">×</button>
            </div>
            <textarea v-model="q.question" class="q-input" rows="2"></textarea>
          </div>
        </div>
        <button class="btn btn-outline" @click="saveQuestions" :disabled="saving">
          {{ saving ? '保存中...' : '保存修改' }}
        </button>
      </section>

      <div class="actions">
        <button class="btn btn-ghost" @click="resetProfile">重新上传</button>
        <button class="btn btn-primary btn-lg" @click="startInterview" :disabled="starting">
          {{ starting ? '启动中...' : '开始面试' }}
        </button>
      </div>
      <div v-if="startError" class="error-msg">{{ startError }}</div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { resumeApi, questionApi, interviewApi } from '../api/index.js'
import { useInterviewStore } from '../stores/interview.js'

const route = useRoute()
const router = useRouter()
const store = useInterviewStore()

const fileInputRef = ref(null)
const dragging = ref(false)
const uploading = ref(false)
const uploadError = ref('')
const saving = ref(false)
const starting = ref(false)
const startError = ref('')
const profile = ref(null)
const candidateId = ref(null)
const questions = ref([])

async function loadProfile(id) {
  try {
    const resp = await resumeApi.getProfile(id)
    profile.value = resp.data.profile
    candidateId.value = id
    const qResp = await questionApi.get(id)
    questions.value = qResp.data.questions
  } catch {
    uploadError.value = '加载候选人信息失败'
  }
}

async function uploadFile(file) {
  if (!file || !file.name.toLowerCase().endsWith('.pdf')) {
    uploadError.value = '请选择 PDF 文件'
    return
  }
  uploading.value = true
  uploadError.value = ''
  try {
    const fd = new FormData()
    fd.append('file', file)
    const resp = await resumeApi.upload(fd, candidateId.value)
    profile.value = resp.data.profile
    candidateId.value = resp.data.candidate_id
    questions.value = resp.data.questions
  } catch (e) {
    uploadError.value = e.response?.data?.detail?.message ?? '上传失败，请重试'
  } finally {
    uploading.value = false
  }
}

function onFileChange(e) {
  const file = e.target.files[0]
  if (file) uploadFile(file)
}

function onDrop(e) {
  dragging.value = false
  const file = e.dataTransfer.files[0]
  if (file) uploadFile(file)
}

function addQuestion() {
  questions.value.push({
    id: questions.value.length + 1,
    dimension: '通用',
    question: '',
    difficulty: 'medium',
    follow_ups: [],
  })
}

function removeQuestion(i) {
  questions.value.splice(i, 1)
}

async function saveQuestions() {
  saving.value = true
  try {
    await questionApi.update({ candidate_id: candidateId.value, questions: questions.value })
  } finally {
    saving.value = false
  }
}

async function startInterview() {
  starting.value = true
  startError.value = ''
  try {
    await interviewApi.start(candidateId.value, 'auto')
    store.candidateName = profile.value?.name ?? ''
    await router.push('/console')
  } catch (e) {
    startError.value = e.response?.data?.detail?.message ?? '启动失败，请重试'
  } finally {
    starting.value = false
  }
}

function resetProfile() {
  profile.value = null
  candidateId.value = null
  questions.value = []
  uploadError.value = ''
}

onMounted(() => {
  const id = route.query.candidate_id
  if (id) loadProfile(String(id))
})
</script>

<style scoped>
.page { padding: 2rem; max-width: 800px; margin: 0 auto; }

h1 { font-size: 1.4rem; margin-bottom: 1.5rem; }
h2 { font-size: 1rem; color: #d1d5db; }

.drop-zone {
  border: 2px dashed #374151;
  border-radius: 12px;
  padding: 4rem 2rem;
  text-align: center;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
  color: #9ca3af;
}
.drop-zone:hover, .drop-zone.dragging {
  border-color: #3b82f6;
  background: #1f2937;
}
.drop-icon { font-size: 3rem; margin-bottom: 1rem; }

.card {
  background: #1f2937;
  border: 1px solid #374151;
  border-radius: 10px;
  padding: 1.25rem;
  margin-bottom: 1rem;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1rem;
}

.profile-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.5rem;
  margin: 0.75rem 0;
  font-size: 0.9rem;
}
.label { color: #9ca3af; margin-right: 0.5rem; }

.skills { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.75rem; }
.tag { background: #374151; padding: 0.2rem 0.6rem; border-radius: 4px; font-size: 0.8rem; }

.question-list { display: flex; flex-direction: column; gap: 0.75rem; margin-bottom: 1rem; }
.question-item {
  background: #111827;
  border: 1px solid #374151;
  border-radius: 8px;
  padding: 0.75rem;
}
.q-header { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.5rem; }
.q-dim { color: #3b82f6; font-size: 0.8rem; }
.q-diff { font-size: 0.75rem; padding: 0.1rem 0.4rem; border-radius: 3px; background: #374151; }
.q-diff.hard { background: #7f1d1d; color: #fca5a5; }
.q-diff.medium { background: #78350f; color: #fcd34d; }
.q-diff.easy { background: #14532d; color: #86efac; }
.icon-btn { margin-left: auto; background: none; border: none; color: #6b7280; cursor: pointer; font-size: 1.1rem; }
.icon-btn:hover { color: #f87171; }
.q-input {
  width: 100%;
  background: #1f2937;
  border: 1px solid #374151;
  border-radius: 4px;
  color: #f9fafb;
  padding: 0.5rem;
  font-size: 0.85rem;
  resize: vertical;
  outline: none;
}
.q-input:focus { border-color: #3b82f6; }

.actions { display: flex; justify-content: space-between; align-items: center; margin-top: 1.5rem; }

.btn {
  padding: 0.5rem 1rem;
  border-radius: 6px;
  font-size: 0.85rem;
  cursor: pointer;
  border: none;
  transition: opacity 0.15s;
}
.btn:hover:not(:disabled) { opacity: 0.85; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-primary { background: #3b82f6; color: #fff; }
.btn-lg { padding: 0.75rem 2rem; font-size: 1rem; }
.btn-ghost { background: transparent; color: #9ca3af; border: 1px solid #374151; }
.btn-outline { background: transparent; border: 1px solid #374151; color: #d1d5db; }
.btn-sm { background: #374151; color: #f9fafb; padding: 0.3rem 0.75rem; }

.error-msg { color: #f87171; margin-top: 0.75rem; font-size: 0.85rem; }
.center-msg { padding: 2rem; text-align: center; color: #9ca3af; }
.muted { color: #6b7280; font-size: 0.8rem; margin-top: 0.25rem; }
</style>